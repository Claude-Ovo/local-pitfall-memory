#!/usr/bin/env python3
"""local-pitfall-memory client (short-lived).
Commands: status | lookup | propose | commit | digest | server
Storage: SQLite (FTS5) at %USERPROFILE%/.pitfall-memory/pitfalls.db (override: PITFALL_DB).

Data discipline (codex reviews #1/#2):
  * everything from the request (error_text, context.runtime, cwd, resolution fields) is REDACTED FIRST,
    then normalized/fingerprinted/stored — no raw text reaches any table
  * every value returned to the Host passes redact() again, recursively (server replies included)
  * exit codes: 0 ok · 1 error (structured JSON on stdout) · 3 required model download pending (--continue)
Model policy (MODELSCOPE-SKILL-PLAN.md §6): NEVER on the exact/family lookup path — only first-record propose,
`lookup --attribute` on fuzzy results, and the optional embedding channel; always soft-fail.
"""
import argparse, hashlib, io, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact, redact_card  # noqa: E402
import engine  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VERSION = "0.6.0"
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"
DB_PATH = Path(os.environ.get("PITFALL_DB", Path.home() / ".pitfall-memory" / "pitfalls.db"))
MODEL_TIMEOUT_S = float(os.environ.get("PITFALL_MODEL_TIMEOUT", "45"))
FAKE = os.environ.get("PITFALL_FAKE_MODEL") == "1"
MAX_ERROR_CHARS = 64_000
MAX_FIELD_CHARS = 2_000
RRF_K = 60                      # reciprocal-rank-fusion constant
SEMANTIC_MAX_RANK = 5           # a candidate counts if it is within the top-N of at least one channel
BACKFILL_PER_LOOKUP = 20        # lazy embedding backfill budget per semantic lookup (~0.1 s each)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pitfalls(
  id INTEGER PRIMARY KEY,
  exact_fp TEXT UNIQUE, family_fp TEXT,
  runtime TEXT, error_class TEXT, package TEXT,
  norm_tail TEXT, created_at INTEGER,
  attribution TEXT
);
CREATE TABLE IF NOT EXISTS occurrences(
  id INTEGER PRIMARY KEY, pitfall_id INTEGER REFERENCES pitfalls(id),
  cwd TEXT, raw_head TEXT, seen_at INTEGER
);
CREATE TABLE IF NOT EXISTS resolutions(
  id INTEGER PRIMARY KEY, pitfall_id INTEGER REFERENCES pitfalls(id),
  root_cause TEXT, fix_command TEXT, verify_method TEXT,
  verified INTEGER DEFAULT 0, verified_at INTEGER, created_at INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS pitfall_fts USING fts5(
  semantic_text, content='', tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS embeddings(pitfall_id INTEGER PRIMARY KEY REFERENCES pitfalls(id), dim INTEGER, vec BLOB);
"""

class UserError(Exception):
    def __init__(self, code, msg, exit_code=1):
        super().__init__(msg); self.code = code; self.exit_code = exit_code

def deep_redact(obj):
    """Recursively redact every string in a reply before it reaches the Host."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: deep_redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_redact(v) for v in obj]
    return obj

def out(obj):
    print(json.dumps(deep_redact(obj), ensure_ascii=False))

# ---- deterministic normalization (what to strip / what to KEEP) ---------------
STRIP_PATTERNS = [
    (re.compile(r"\x1b\[[0-9;]*[A-Za-z]"), ""),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?\b"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{16,64}\b", re.I), "<HASH>"),
    (re.compile(r"0x[0-9a-fA-F]{4,16}"), "<ADDR>"),
    (re.compile(r"(['\"])(?:[A-Za-z]:[\\/]|\\\\|/)[^'\"]*\1"), "<PATH>"),
    (re.compile(r"\\\\[^\s'\"():]+"), "<PATH>"),
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"():]+"), "<PATH>"),
    (re.compile(r"(?<![\w:./-])/(?:[\w.-]+/)+[\w.-]*"), "<PATH>"),
    (re.compile(r"\bpid[ =:]?\d+\b", re.I), "<PID>"),
    (re.compile(r":\d+:\d+\b"), ":<L>:<C>"),
    (re.compile(r"\bline \d+\b", re.I), "line <L>"),
    (re.compile(r"\b(?:localhost|127\.0\.0\.1):(\d{4,5})\b"), "localhost:<PORT>"),
]
ERROR_CLASS_RE = re.compile(
    r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning)|ERR_[A-Z_]+|E[A-Z]{2,}[0-9]*|TS\d{4}|E\d{4}|panic|SIGSEGV|ENOENT|EADDRINUSE)\b")
PKG_RE = re.compile(r"(?:node_modules[\\/](@?[\w.-]+(?:[\\/][\w.-]+)?)|site-packages[\\/]([\w.-]+)|from ['\"]([^'\"]+)['\"]|package ([\w.-]+))")
FRAME_RE = re.compile(r"\s*(at |File \"|--> |#\d+ )")

def normalize(text: str) -> str:
    for pat, rep in STRIP_PATTERNS:
        text = pat.sub(rep, text)
    return text.strip()

def norm_runtime(runtime) -> str:
    """runtime is user input too: redact, then canonicalize (trim/lower, short, safe charset)."""
    r = redact(str(runtime or ""))[:32].strip().lower()
    return re.sub(r"[^a-z0-9._+-]", "", r)

def extract(text: str, runtime: str = ""):
    """text must already be redacted."""
    runtime = norm_runtime(runtime)
    norm = normalize(text)
    lines = [l for l in norm.splitlines() if l.strip()]
    frames = [l.strip()[:200] for l in lines if FRAME_RE.match(l)][:3]
    msg_lines = [l for l in lines if not FRAME_RE.match(l)]
    m = ERROR_CLASS_RE.search(norm)
    error_class = m.group(1) if m else ""
    tail = next((l for l in reversed(msg_lines) if error_class and error_class in l), "")
    if not tail:
        tail = msg_lines[-1] if msg_lines else (lines[-1] if lines else "")
    tail = tail.strip()[:300]
    pm = PKG_RE.search(text)
    package = next((g for g in (pm.groups() if pm else ()) if g), "")
    exact_src = "|".join([runtime, error_class, tail] + frames + [package])
    family_src = "|".join([error_class, package, re.sub(r"['\"][^'\"]*['\"]", "<S>", tail)])
    return {"runtime": runtime, "error_class": error_class, "package": package, "norm_tail": tail,
            "exact_fp": hashlib.sha256(exact_src.encode()).hexdigest()[:24],
            "family_fp": hashlib.sha256(family_src.encode()).hexdigest()[:24],
            "semantic_text": " ".join([error_class, package, tail] + frames)[:1000]}

# ---- request loading / validation ------------------------------------------
def _str_field(req, k, required, maxlen=MAX_FIELD_CHARS):
    v = req.get(k)
    if v is None or v == "":
        if required:
            raise UserError("request_schema", f"{k} is required and must be a non-empty string")
        return ""
    if not isinstance(v, str):
        raise UserError("request_schema", f"{k} must be a string")
    if required and not v.strip():
        raise UserError("request_schema", f"{k} must not be blank")
    if len(v) > maxlen:
        raise UserError("request_too_large", f"{k} exceeds {maxlen} characters")
    return redact(v).strip()

def load_request(path: str, need_resolution: bool = False) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise UserError("request_unreadable", f"cannot read request file: {exc.strerror}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise UserError("request_not_utf8", "request file must be UTF-8 encoded JSON")
    try:
        req = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UserError("request_bad_json", f"request file is not valid JSON: {exc.msg} at line {exc.lineno}")
    if not isinstance(req, dict):
        raise UserError("request_schema", "request must be a JSON object")
    et = req.get("error_text")
    if not isinstance(et, str):
        raise UserError("request_schema", "error_text must be a string")
    if not et.strip():
        raise UserError("request_empty", "error_text is empty")
    if len(et) > MAX_ERROR_CHARS:
        raise UserError("request_too_large", f"error_text exceeds {MAX_ERROR_CHARS} characters; send the tail of the log")
    ctx = req.get("context") or {}
    if not isinstance(ctx, dict):
        raise UserError("request_schema", "context must be an object")
    rt = ctx.get("runtime", "")
    if rt is not None and not isinstance(rt, str):
        raise UserError("request_schema", "context.runtime must be a string")
    clean = {"error_text": redact(et),
             "context": {"cwd": redact(str(ctx.get("cwd") or ""))[:1000], "runtime": norm_runtime(rt)}}
    for k in ("root_cause", "fix_command", "verify_method"):
        clean[k] = _str_field(req, k, required=need_resolution)
    return clean

# ---- db --------------------------------------------------------------------
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.executescript(SCHEMA)
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
        bad = DB_PATH.with_name(DB_PATH.name + f".corrupt-{int(time.time())}")
        try:
            os.replace(DB_PATH, bad)
        except OSError:
            raise UserError("db_corrupt", f"database unreadable ({exc}) and could not be moved aside")
        raise UserError("db_corrupt", f"database was corrupt and has been moved to {bad}; a fresh DB will be created on next call")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pitfalls)")}
    if "attribution" not in cols:
        conn.execute("ALTER TABLE pitfalls ADD COLUMN attribution TEXT")
    _migrate(conn)
    return conn

def _migrate(conn):
    """One-time rescrubs for rows written by older versions (idempotent, keyed in meta)."""
    if not conn.execute("SELECT v FROM meta WHERE k='redaction_migrated'").fetchone():
        for pid, tail in conn.execute("SELECT id, norm_tail FROM pitfalls").fetchall():
            r = redact(tail or "")
            if r != tail:
                conn.execute("UPDATE pitfalls SET norm_tail=? WHERE id=?", (r, pid))
        conn.execute("INSERT INTO pitfall_fts(pitfall_fts) VALUES('delete-all')")
        for pid, ec, pkg, tail in conn.execute("SELECT id,error_class,package,norm_tail FROM pitfalls").fetchall():
            conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(?,?)",
                         (pid, " ".join(x for x in (ec, pkg, tail) if x)[:1000]))
        conn.execute("INSERT INTO meta(k,v) VALUES('redaction_migrated', ?)", (VERSION,))
    if not conn.execute("SELECT v FROM meta WHERE k='runtime_migrated'").fetchone():
        for pid, rt in conn.execute("SELECT id, runtime FROM pitfalls").fetchall():
            n = norm_runtime(rt)
            if n != (rt or ""):
                conn.execute("UPDATE pitfalls SET runtime=? WHERE id=?", (n, pid))
        conn.execute("INSERT INTO meta(k,v) VALUES('runtime_migrated', ?)", (VERSION,))
    conn.commit()

# ---- models ------------------------------------------------------------------
def _spec(role):
    return next((m for m in INFO["models"] if m.get("role") == role), None)

def _complete(m):
    if not m:
        return False
    d = MODELS_DIR / m["dir_name"]
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

def model_ready() -> bool:
    return _complete(_spec("attribution"))

def _pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h); return True
    except Exception:
        return False

def _kickoff_download():
    """Start download_model.py detached, single-flight: the downloader's own lock file (holds PID) is the guard."""
    m = _spec("attribution")
    lock = MODELS_DIR / (m["dir_name"] + ".lock")
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except Exception:
            pid = 0
        if pid and _pid_alive(pid):
            return "already_running"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([sys.executable, str(Path(__file__).with_name("download_model.py"))], **kw)
    return "started"

def _attribute(error_text, ctx):
    if not FAKE and not model_ready():
        _kickoff_download()
        return {"pending": True, "note": "model downloading; rerun scripts\\run.ps1 --continue to finish, then retry"}
    r = engine.attribute(error_text, ctx, timeout=MODEL_TIMEOUT_S)
    return redact_card(r) if r else None

def _embed(text):
    if not FAKE and not model_ready():
        return None
    return engine.embed(text, spawn_wait=float(os.environ.get("PITFALL_EMBED_SPAWN_WAIT", "60")))

# ---- retrieval ---------------------------------------------------------------
def _store_embedding(conn, pid, vec):
    import struct
    if vec:
        conn.execute("INSERT OR REPLACE INTO embeddings(pitfall_id,dim,vec) VALUES(?,?,?)",
                     (pid, len(vec), struct.pack(f"{len(vec)}f", *vec)))

def _backfill_embeddings(conn, budget=BACKFILL_PER_LOOKUP):
    """Lazy backfill for rows recorded without a vector (pre-v0.5 or embedder was down)."""
    rows = conn.execute("SELECT p.id, p.error_class, p.package, p.norm_tail FROM pitfalls p "
                        "LEFT JOIN embeddings e ON e.pitfall_id=p.id WHERE e.pitfall_id IS NULL LIMIT ?", (budget,)).fetchall()
    n = 0
    for pid, ec, pkg, tail in rows:
        vec = _embed(" ".join(x for x in (ec, pkg, tail) if x)[:1000])
        if not vec:
            break
        _store_embedding(conn, pid, vec); n += 1
    if n:
        conn.commit()
    return n

def _vector_rank(conn, qvec, limit=10):
    import struct
    if not qvec:
        return []
    scored = []
    for pid, dim, blob in conn.execute("SELECT pitfall_id, dim, vec FROM embeddings"):
        if dim != len(qvec):
            continue
        v = struct.unpack(f"{dim}f", blob)
        scored.append((sum(a * b for a, b in zip(qvec, v)), pid))
    scored.sort(reverse=True)
    return [pid for _, pid in scored[:limit]]

def _rrf(*ranked_lists, k=RRF_K):
    score = {}
    for lst in ranked_lists:
        for rank, pid in enumerate(lst, 1):
            score[pid] = score.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda x: -x[1])

def _env_compatible(conn, pid, runtime):
    if not runtime:
        return True
    r = conn.execute("SELECT runtime FROM pitfalls WHERE id=?", (pid,)).fetchone()
    return (not r or not r[0] or r[0] == runtime)

def _resolution_for(conn, pid):
    r = conn.execute("SELECT root_cause,fix_command,verify_method,verified FROM resolutions "
                     "WHERE pitfall_id=? ORDER BY verified DESC, created_at DESC LIMIT 1", (pid,)).fetchone()
    return None if not r else redact_card({"root_cause": r[0], "fix_command": r[1], "verify_method": r[2], "verified": bool(r[3])})

def _hits(conn, pid):
    return conn.execute("SELECT COUNT(*),MAX(seen_at) FROM occurrences WHERE pitfall_id=?", (pid,)).fetchone()

# ---- commands ----------------------------------------------------------------
def cmd_status(args):
    conn = db()
    n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("pitfalls", "occurrences", "resolutions", "embeddings")}
    verified = conn.execute("SELECT COUNT(*) FROM resolutions WHERE verified=1").fetchone()[0]
    fts_ok = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pitfall_fts'").fetchone())
    emb = _spec("embedding")
    out({"ok": True, "db": str(DB_PATH), "pitfalls": n["pitfalls"], "occurrences": n["occurrences"],
         "resolutions": n["resolutions"], "verified_resolutions": verified, "embeddings": n["embeddings"], "fts5": fts_ok,
         "model": _spec("attribution")["dir_name"], "model_ready": model_ready(),
         "embedding_model": emb["dir_name"] if emb else None, "embedding_ready": _complete(emb),
         "retrieval_mode": "hybrid" if (_complete(emb) or FAKE) else "fts-only",
         "redaction": "on", "version": VERSION})
    return 0

def cmd_lookup(args):
    req = load_request(args.request_file); ctx = req["context"]
    feat = extract(req["error_text"], ctx["runtime"])
    conn = db()
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    if row:
        pid = row[0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        out({"hit": "exact", "confidence": "可引用" if (res and res["verified"]) else "需谨慎", "pitfall_id": pid,
             "times_seen": hits[0], "last_seen": hits[1], "resolution": res}); return 0
    total = conn.execute("SELECT COUNT(*) FROM pitfalls WHERE family_fp=?", (feat["family_fp"],)).fetchone()[0]
    if total:
        rows = conn.execute("SELECT id, norm_tail FROM pitfalls WHERE family_fp=? ORDER BY created_at DESC LIMIT 3",
                            (feat["family_fp"],)).fetchall()
        pid = rows[0][0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        out({"hit": "family", "confidence": "需谨慎", "pitfall_id": pid, "family_size": total,
             "times_seen": hits[0], "last_seen": hits[1],
             "family_hint": "same error class/package, different details — verify before applying",
             "known_variants": [r[1] for r in rows], "resolution": res}); return 0

    # semantic: FTS5/BM25 ⊕ vector (optional), RRF-fused; a candidate qualifies if it is top-N in ANY channel.
    tokens = re.findall(r"[A-Za-z0-9_]{3,}", feat["semantic_text"])[:20]
    q = " OR ".join(f'"{t}"' for t in tokens)
    fts_rank = [r[0] for r in conn.execute(
        "SELECT rowid FROM pitfall_fts WHERE pitfall_fts MATCH ? ORDER BY bm25(pitfall_fts) LIMIT 10", (q,)).fetchall()] if q else []
    qvec = _embed(feat["semantic_text"])
    if qvec:
        _backfill_embeddings(conn)
    vec_rank = _vector_rank(conn, qvec)
    fused = _rrf(fts_rank, vec_rank)
    def qualifies(pid):
        return (pid in fts_rank[:SEMANTIC_MAX_RANK]) or (pid in vec_rank[:SEMANTIC_MAX_RANK])
    good = [(pid, s) for pid, s in fused if qualifies(pid)]
    compat = [x for x in good if _env_compatible(conn, x[0], feat["runtime"])]
    chosen = compat[0] if compat else (good[0] if good else None)     # demote cross-runtime, never hide
    if chosen:
        pid, score = chosen
        resp = {"hit": "semantic", "confidence": "仅联想", "pitfall_id": pid, "resolution": _resolution_for(conn, pid),
                "retrieval": {"fused_score": round(score, 4),
                              "fts_rank": (fts_rank.index(pid) + 1) if pid in fts_rank else None,
                              "vec_rank": (vec_rank.index(pid) + 1) if pid in vec_rank else None,
                              "channels": [c for c, ok in (("fts5", pid in fts_rank), ("vector", pid in vec_rank)) if ok],
                              "mode": "hybrid" if qvec else "fts-only",
                              "env_compatible": _env_compatible(conn, pid, feat["runtime"])}}
    else:
        resp = {"hit": "none", "confidence": None, "note": "no local history; solve fresh then propose+commit"}
    if args.attribute:
        resp["attribution"] = _attribute(req["error_text"], ctx)
    out(resp); return 0

def cmd_propose(args):
    req = load_request(args.request_file, need_resolution=True); ctx = req["context"]
    feat = extract(req["error_text"], ctx["runtime"]); now = int(time.time())
    conn = db()
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    attribution = None
    if row:
        pid = row[0]
    else:
        if not args.no_model:
            attribution = _attribute(req["error_text"], ctx)
        stored_attr = json.dumps(attribution, ensure_ascii=False) if attribution and not attribution.get("pending") else None
        try:
            cur = conn.execute("INSERT INTO pitfalls(exact_fp,family_fp,runtime,error_class,package,norm_tail,created_at,attribution) "
                               "VALUES(?,?,?,?,?,?,?,?)", (feat["exact_fp"], feat["family_fp"], feat["runtime"], feat["error_class"],
                                                           feat["package"], feat["norm_tail"], now, stored_attr))
            pid = cur.lastrowid
            conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(?,?)", (pid, feat["semantic_text"]))
            if not args.no_model:
                _store_embedding(conn, pid, _embed(feat["semantic_text"]))
        except sqlite3.IntegrityError:
            pid = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()[0]
    conn.execute("INSERT INTO occurrences(pitfall_id,cwd,raw_head,seen_at) VALUES(?,?,?,?)",
                 (pid, ctx["cwd"], req["error_text"][:500], now))
    cur = conn.execute("INSERT INTO resolutions(pitfall_id,root_cause,fix_command,verify_method,verified,created_at) VALUES(?,?,?,?,0,?)",
                       (pid, req["root_cause"], req["fix_command"], req["verify_method"], now))
    conn.commit()
    resp = {"ok": True, "pitfall_id": pid, "proposal_id": cur.lastrowid,
            "state": "proposed (unverified) — run commit after the fix is verified"}
    if attribution:
        resp["attribution"] = attribution
    out(resp); return 0

def cmd_commit(args):
    conn = db()
    if int(args.verify_exit_code) != 0:
        out({"ok": False, "note": "verification failed; proposal stays unverified"}); return 1
    r = conn.execute("SELECT root_cause,fix_command,verify_method FROM resolutions WHERE id=?", (args.id,)).fetchone()
    if not r:
        out({"ok": False, "error": "no_such_proposal", "resolution_id": args.id}); return 1
    if not all((x or "").strip() for x in r):
        out({"ok": False, "error": "empty_resolution", "message": "a resolution needs non-empty root_cause, fix_command and verify_method before it can be verified"}); return 1
    conn.execute("UPDATE resolutions SET verified=1, verified_at=? WHERE id=?", (int(time.time()), args.id)); conn.commit()
    out({"ok": True, "resolution_id": args.id, "state": "verified"}); return 0

def cmd_digest(args):
    conn = db()
    rows = conn.execute("SELECT p.error_class,p.package,p.norm_tail,r.root_cause,r.fix_command,"
                        "(SELECT COUNT(*) FROM occurrences o WHERE o.pitfall_id=p.id) "
                        "FROM pitfalls p JOIN resolutions r ON r.pitfall_id=p.id AND r.verified=1 ORDER BY 6 DESC").fetchall()
    lines = ["# Pitfalls we hit (verified fixes only)", "", "| Error | Package | Root cause | Fix | Times |", "|---|---|---|---|---|"]
    cell = lambda s: redact((s or "")).replace("|", "\\|").replace("\n", " ")[:120]
    for ec, pkg, tail, cause, fix, cnt in rows:
        lines.append(f"| {cell(ec) or cell(tail)} | {cell(pkg)} | {cell(cause)} | `{cell(fix)}` | {cnt} |")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        out({"ok": True, "out": args.out, "verified_entries": len(rows)})
    else:
        print(text)
    return 0

def cmd_server(args):
    if args.action == "status":
        st = engine.status(); out(st or {"ok": False, "state": "down"}); return 0 if st else 1
    if args.action == "start":
        if not FAKE and not model_ready():
            _kickoff_download(); out({"ok": False, "state": "downloading", "note": "rerun scripts\\run.ps1 --continue"}); return 3
        st = engine.ensure_server(); out(st or {"ok": False, "state": "failed to start"}); return 0 if st else 1
    if args.action == "stop":
        r = engine.shutdown(); out(r or {"ok": True, "state": "already down"}); return 0
    return 1

def main():
    p = argparse.ArgumentParser(prog="local-pitfall-memory")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("lookup"); s.add_argument("--request-file", required=True); s.add_argument("--json", action="store_true")
    s.add_argument("--attribute", action="store_true", help="on semantic/none results also ask the local model (slow, ~10-25s CPU)")
    s = sub.add_parser("propose"); s.add_argument("--request-file", required=True); s.add_argument("--json", action="store_true")
    s.add_argument("--no-model", action="store_true", help="skip model attribution and embedding on first record")
    s = sub.add_parser("commit"); s.add_argument("--id", required=True); s.add_argument("--verify-exit-code", required=True, type=int)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("digest"); s.add_argument("--out"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("server"); s.add_argument("action", choices=["status", "start", "stop"]); s.add_argument("--json", action="store_true")
    args = p.parse_args()
    try:
        return {"status": cmd_status, "lookup": cmd_lookup, "propose": cmd_propose, "commit": cmd_commit,
                "digest": cmd_digest, "server": cmd_server}[args.cmd](args)
    except UserError as exc:
        out({"ok": False, "error": exc.code, "message": str(exc)}); return exc.exit_code
    except sqlite3.DatabaseError as exc:
        out({"ok": False, "error": "db_error", "message": str(exc)}); return 1

if __name__ == "__main__":
    sys.exit(main())
