#!/usr/bin/env python3
"""local-pitfall-memory client (short-lived).
Commands: status | lookup | propose | commit | digest | server
Storage: SQLite (FTS5) at %USERPROFILE%/.pitfall-memory/pitfalls.db (override: PITFALL_DB).

Data discipline (codex review #1, P0):
  * error text is REDACTED FIRST, then normalized/fingerprinted/stored — no raw text reaches any table
  * every string returned to the Host passes redact() again (belt and braces)
  * exit codes: 0 ok · 1 error (structured JSON on stdout) · 3 model download pending (--continue)
Model policy (MODELSCOPE-SKILL-PLAN.md §6): NEVER on the lookup hot path — only first-record propose
and `lookup --attribute` on fuzzy results, always soft-fail.
"""
import argparse, hashlib, io, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact, redact_card  # noqa: E402
import engine  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VERSION = "0.4.0"
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"
DB_PATH = Path(os.environ.get("PITFALL_DB", Path.home() / ".pitfall-memory" / "pitfalls.db"))
MODEL_TIMEOUT_S = float(os.environ.get("PITFALL_MODEL_TIMEOUT", "45"))
MAX_ERROR_CHARS = 64_000

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
"""

class UserError(Exception):
    """Structured, redacted error returned to the Host with exit code 1."""
    def __init__(self, code, msg, exit_code=1):
        super().__init__(msg); self.code = code; self.exit_code = exit_code

# ---- deterministic normalization (what to strip / what to KEEP) ---------------
STRIP_PATTERNS = [
    (re.compile(r"\x1b\[[0-9;]*[A-Za-z]"), ""),                                # ANSI
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?\b"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{16,64}\b", re.I), "<HASH>"),
    (re.compile(r"0x[0-9a-fA-F]{4,16}"), "<ADDR>"),
    # absolute paths: quoted (may contain spaces), Windows drive, UNC, POSIX (any root)
    (re.compile(r"(['\"])(?:[A-Za-z]:[\\/]|\\\\|/)[^'\"]*\1"), "<PATH>"),
    (re.compile(r"\\\\[^\s'\"():]+"), "<PATH>"),                                # UNC \\server\share\x
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"():]+"), "<PATH>"),                      # C:\x or C:/x
    (re.compile(r"(?<![\w:./-])/(?:[\w.-]+/)+[\w.-]*"), "<PATH>"),               # /workspace/a/b (>=2 segments)
    (re.compile(r"\bpid[ =:]?\d+\b", re.I), "<PID>"),
    (re.compile(r":\d+:\d+\b"), ":<L>:<C>"),                                    # line:col
    (re.compile(r"\bline \d+\b", re.I), "line <L>"),
    (re.compile(r"\b(?:localhost|127\.0\.0\.1):(\d{4,5})\b"), "localhost:<PORT>"),
]
# KEEP (never stripped): HTTP status, errno, compiler codes (TS2345/E0308), versions, exit codes, tensor shapes.

ERROR_CLASS_RE = re.compile(
    r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning)|ERR_[A-Z_]+|E[A-Z]{2,}[0-9]*|TS\d{4}|E\d{4}|panic|SIGSEGV|ENOENT|EADDRINUSE)\b")
PKG_RE = re.compile(r"(?:node_modules[\\/](@?[\w.-]+(?:[\\/][\w.-]+)?)|site-packages[\\/]([\w.-]+)|from ['\"]([^'\"]+)['\"]|package ([\w.-]+))")
FRAME_RE = re.compile(r"\s*(at |File \"|--> |#\d+ )")

def normalize(text: str) -> str:
    for pat, rep in STRIP_PATTERNS:
        text = pat.sub(rep, text)
    return text.strip()

def extract(text: str, runtime: str = ""):
    """text must already be redacted. runtime is case-insensitive."""
    runtime = (runtime or "").strip().lower()
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
def load_request(path: str) -> dict:
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
    ctx = req.get("context", {})
    if ctx is None:
        ctx = {}
    if not isinstance(ctx, dict):
        raise UserError("request_schema", "context must be an object")
    for k in ("root_cause", "fix_command", "verify_method"):
        if k in req and req[k] is not None and not isinstance(req[k], str):
            raise UserError("request_schema", f"{k} must be a string")
    # redact FIRST — nothing downstream ever sees the raw text
    req["error_text"] = redact(et)
    req["context"] = {"cwd": redact(str(ctx.get("cwd", ""))), "runtime": str(ctx.get("runtime", ""))}
    for k in ("root_cause", "fix_command", "verify_method"):
        req[k] = redact(req.get(k) or "")
    return req

# ---- db --------------------------------------------------------------------
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.executescript(SCHEMA)
    except sqlite3.DatabaseError as exc:
        # corrupt file: move it aside (never delete), start fresh, tell the Host
        if conn is not None:
            conn.close()                      # Windows: an open handle blocks the rename
        bad = DB_PATH.with_name(DB_PATH.name + f".corrupt-{int(time.time())}")
        try:
            os.replace(DB_PATH, bad)
        except OSError:
            raise UserError("db_corrupt", f"database unreadable ({exc}) and could not be moved aside")
        raise UserError("db_corrupt", f"database was corrupt and has been moved to {redact(str(bad))}; a fresh DB will be created on next call")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pitfalls)")}
    if "attribution" not in cols:
        conn.execute("ALTER TABLE pitfalls ADD COLUMN attribution TEXT")
    _migrate_redaction(conn)
    return conn

def _migrate_redaction(conn):
    """One-time: rows written by <=0.3.0 stored un-redacted norm_tail/semantic_text. Re-redact in place."""
    if conn.execute("SELECT v FROM meta WHERE k='redaction_migrated'").fetchone():
        return
    for pid, tail in conn.execute("SELECT id, norm_tail FROM pitfalls").fetchall():
        r = redact(tail or "")
        if r != tail:
            conn.execute("UPDATE pitfalls SET norm_tail=? WHERE id=?", (r, pid))
    # contentless FTS5 table: rows can't be deleted individually → 'delete-all' then rebuild from redacted rows
    conn.execute("INSERT INTO pitfall_fts(pitfall_fts) VALUES('delete-all')")
    for pid, ec, pkg, tail in conn.execute("SELECT id,error_class,package,norm_tail FROM pitfalls").fetchall():
        conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(?,?)",
                     (pid, " ".join(x for x in (ec, pkg, tail) if x)[:1000]))
    conn.execute("INSERT INTO meta(k,v) VALUES('redaction_migrated', ?)", (VERSION,))
    conn.commit()

def out(obj, as_json=True):
    print(json.dumps(obj, ensure_ascii=False, indent=None if as_json else 2))

# ---- model readiness / download kickoff (entry contract: exit 3 while pending) ----
def _model_spec():
    return INFO["models"][0]

def model_ready() -> bool:
    m = _model_spec(); d = MODELS_DIR / m["dir_name"]
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

def _kickoff_download():
    """Start download_model.py detached (idempotent: it exits fast if already complete/running)."""
    lock = MODELS_DIR / (_model_spec()["dir_name"] + ".downloading")
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        return
    MODELS_DIR.mkdir(parents=True, exist_ok=True); lock.touch()
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([sys.executable, str(Path(__file__).with_name("download_model.py"))], **kw)

def _attribute(error_text, ctx):
    """Structured guess from the local model. Soft: None on failure; kicks off download if model missing."""
    if os.environ.get("PITFALL_FAKE_MODEL") != "1" and not model_ready():
        _kickoff_download()
        return {"pending": True, "note": "model downloading; rerun scripts\\run.ps1 --continue to finish, then retry"}
    r = engine.attribute(error_text, ctx, timeout=MODEL_TIMEOUT_S)
    return redact_card(r) if r else None

# ---- commands ----------------------------------------------------------------
def cmd_status(args):
    conn = db()
    n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("pitfalls", "occurrences", "resolutions")}
    verified = conn.execute("SELECT COUNT(*) FROM resolutions WHERE verified=1").fetchone()[0]
    fts_ok = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pitfall_fts'").fetchone())
    out({"ok": True, "db": redact(str(DB_PATH)), "pitfalls": n["pitfalls"], "occurrences": n["occurrences"],
         "resolutions": n["resolutions"], "verified_resolutions": verified, "fts5": fts_ok,
         "model": _model_spec()["dir_name"], "model_ready": model_ready(), "redaction": "on", "version": VERSION})
    return 0

def _resolution_for(conn, pid):
    r = conn.execute("SELECT root_cause,fix_command,verify_method,verified FROM resolutions "
                     "WHERE pitfall_id=? ORDER BY verified DESC, created_at DESC LIMIT 1", (pid,)).fetchone()
    return None if not r else redact_card({"root_cause": r[0], "fix_command": r[1], "verify_method": r[2], "verified": bool(r[3])})

def _hits(conn, pid):
    return conn.execute("SELECT COUNT(*),MAX(seen_at) FROM occurrences WHERE pitfall_id=?", (pid,)).fetchone()

def cmd_lookup(args):
    req = load_request(args.request_file); ctx = req["context"]
    feat = extract(req["error_text"], ctx["runtime"])
    conn = db()
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    if row:                                      # 1) exact → 可引用 only with a verified resolution
        pid = row[0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        out({"hit": "exact", "confidence": "可引用" if (res and res["verified"]) else "需谨慎", "pitfall_id": pid,
             "times_seen": hits[0], "last_seen": hits[1], "resolution": res}); return 0
    total = conn.execute("SELECT COUNT(*) FROM pitfalls WHERE family_fp=?", (feat["family_fp"],)).fetchone()[0]
    if total:                                    # 2) family → never 可引用
        rows = conn.execute("SELECT id, norm_tail FROM pitfalls WHERE family_fp=? ORDER BY created_at DESC LIMIT 3",
                            (feat["family_fp"],)).fetchall()
        pid = rows[0][0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        out({"hit": "family", "confidence": "需谨慎", "pitfall_id": pid, "family_size": total,
             "times_seen": hits[0], "last_seen": hits[1],
             "family_hint": "same error class/package, different details — verify before applying",
             "known_variants": [redact(r[1]) for r in rows], "resolution": res}); return 0
    tokens = re.findall(r"[A-Za-z0-9_]{3,}", feat["semantic_text"])[:20]   # 3) semantic → 仅联想
    q = " OR ".join(f'"{t}"' for t in tokens)
    resp = None
    if q:
        rows = conn.execute("SELECT rowid FROM pitfall_fts WHERE pitfall_fts MATCH ? ORDER BY bm25(pitfall_fts) LIMIT 3",
                            (q,)).fetchall()
        if rows:
            pid = rows[0][0]
            resp = {"hit": "semantic", "confidence": "仅联想", "pitfall_id": pid, "resolution": _resolution_for(conn, pid)}
    if resp is None:
        resp = {"hit": "none", "confidence": None, "note": "no local history; solve fresh then propose+commit"}
    if args.attribute:                            # fuzzy path only — model allowed here
        resp["attribution"] = _attribute(req["error_text"], ctx)
    out(resp); return 0

def cmd_propose(args):
    req = load_request(args.request_file); ctx = req["context"]
    feat = extract(req["error_text"], ctx["runtime"]); now = int(time.time())
    conn = db()
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    attribution = None
    if row:
        pid = row[0]
    else:
        if not args.no_model:                     # first record of this pit → one model call
            attribution = _attribute(req["error_text"], ctx)
        stored_attr = json.dumps(attribution, ensure_ascii=False) if attribution and not attribution.get("pending") else None
        try:
            cur = conn.execute("INSERT INTO pitfalls(exact_fp,family_fp,runtime,error_class,package,norm_tail,created_at,attribution) "
                               "VALUES(?,?,?,?,?,?,?,?)", (feat["exact_fp"], feat["family_fp"], feat["runtime"], feat["error_class"],
                                                           feat["package"], feat["norm_tail"], now, stored_attr))
            pid = cur.lastrowid
            conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(?,?)", (pid, feat["semantic_text"]))
        except sqlite3.IntegrityError:            # concurrent propose of the same fingerprint
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
    n = conn.execute("UPDATE resolutions SET verified=1, verified_at=? WHERE id=?", (int(time.time()), args.id)).rowcount
    conn.commit()
    out({"ok": bool(n), "resolution_id": args.id, "state": "verified" if n else "no such proposal"}); return 0 if n else 1

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
        if os.environ.get("PITFALL_FAKE_MODEL") != "1" and not model_ready():
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
    s.add_argument("--attribute", action="store_true", help="on semantic/none results also ask the local model (slow, ~15-25s CPU)")
    s = sub.add_parser("propose"); s.add_argument("--request-file", required=True); s.add_argument("--json", action="store_true")
    s.add_argument("--no-model", action="store_true", help="skip the one-time model attribution on first record")
    s = sub.add_parser("commit"); s.add_argument("--id", required=True); s.add_argument("--verify-exit-code", required=True, type=int)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("digest"); s.add_argument("--out"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("server"); s.add_argument("action", choices=["status", "start", "stop"]); s.add_argument("--json", action="store_true")
    args = p.parse_args()
    try:
        return {"status": cmd_status, "lookup": cmd_lookup, "propose": cmd_propose, "commit": cmd_commit,
                "digest": cmd_digest, "server": cmd_server}[args.cmd](args)
    except UserError as exc:
        out({"ok": False, "error": exc.code, "message": redact(str(exc))}); return exc.exit_code
    except sqlite3.DatabaseError as exc:
        out({"ok": False, "error": "db_error", "message": redact(str(exc))}); return 1

if __name__ == "__main__":
    sys.exit(main())
