#!/usr/bin/env python3
"""local-pitfall-memory client.
Commands: status | lookup | propose | commit | digest
Storage: SQLite (FTS5) at %USERPROFILE%/.pitfall-memory/pitfalls.db (override: PITFALL_DB).
Day-1 scope: deterministic normalization + exact/family fingerprints + FTS5 (BM25) retrieval.
Day-2 (server.py): Qwen3-4B@OpenVINO structured extraction for first-record / fuzzy queries.
"""
import argparse, hashlib, io, json, os, re, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact, redact_card  # noqa: E402
import engine  # noqa: E402  (local model bridge; every call is soft-fail → None)

# Model policy (MODELSCOPE-SKILL-PLAN.md §6): NEVER on the lookup hot path.
# Only consulted (a) on first record in `propose`, (b) on fuzzy lookups (semantic/none) when --attribute is given.
MODEL_TIMEOUT_S = float(os.environ.get("PITFALL_MODEL_TIMEOUT", "45"))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

VERSION = "0.3.0"
DB_PATH = Path(os.environ.get("PITFALL_DB", Path.home() / ".pitfall-memory" / "pitfalls.db"))
MODEL_DIR = Path.home() / ".openvino" / "models" / "Qwen3-4B-int4-ov"

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
"""

# ---- deterministic normalization -------------------------------------------
STRIP_PATTERNS = [
    (re.compile(r"\x1b\[[0-9;]*m"), ""),                                   # ANSI
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?\b"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b[0-9a-f]{16,64}\b", re.I), "<HASH>"),
    (re.compile(r"0x[0-9a-fA-F]{4,16}"), "<ADDR>"),
    (re.compile(r"[A-Za-z]:[\\/][^\s'\"():]+"), "<PATH>"),                 # any drive path
    (re.compile(r"(?<![\w:])/(?:Users|home|tmp|var|opt|mnt)/[^\s'\"():]+"), "<PATH>"),
    (re.compile(r"\bpid[ =:]?\d+\b", re.I), "<PID>"),
    (re.compile(r":\d+:\d+\b"), ":<L>:<C>"),                               # line:col
    (re.compile(r"\bline \d+\b", re.I), "line <L>"),
]
# NOT stripped: HTTP status codes, errno, compiler codes (TS2345, E0308), versions, exit codes.

ERROR_CLASS_RE = re.compile(
    r"\b([A-Z][A-Za-z]*(?:Error|Exception|Warning)|ERR_[A-Z_]+|E[A-Z]{2,}[0-9]*|TS\d{4}|E\d{4}|panic|SIGSEGV|ENOENT|EADDRINUSE)\b")
PKG_RE = re.compile(r"(?:node_modules[\\/](@?[\w.-]+(?:[\\/][\w.-]+)?)|site-packages[\\/]([\w.-]+)|from ['\"]([^'\"]+)['\"]|package ([\w.-]+))")

def normalize(text: str) -> str:
    for pat, rep in STRIP_PATTERNS:
        text = pat.sub(rep, text)
    return text.strip()

FRAME_RE = re.compile(r"\s*(at |File \"|--> |#\d+ )")

def extract(text: str, runtime: str = ""):
    norm = normalize(text)
    lines = [l for l in norm.splitlines() if l.strip()]
    frames = [l.strip()[:200] for l in lines if FRAME_RE.match(l)][:3]
    msg_lines = [l for l in lines if not FRAME_RE.match(l)]
    # "tail" = the message line, never a stack frame. Prefer the line that names the error class
    # (Node prints it after a source excerpt: "loader:1958 / throw err; / ^ / Error [X]: ...").
    m = ERROR_CLASS_RE.search(norm)
    error_class = m.group(1) if m else ""
    tail = ""
    if error_class:
        tail = next((l for l in reversed(msg_lines) if error_class in l), "")
    if not tail:
        tail = msg_lines[-1] if msg_lines else (lines[-1] if lines else "")
    tail = tail.strip()[:300]
    pm = PKG_RE.search(text)
    package = next((g for g in (pm.groups() if pm else ()) if g), "")
    exact_src = "|".join([runtime, error_class, tail] + frames + [package])
    family_src = "|".join([error_class, package, re.sub(r"['\"][^'\"]*['\"]", "<S>", tail)])
    return {
        "runtime": runtime, "error_class": error_class, "package": package,
        "norm_tail": tail,
        "exact_fp": hashlib.sha256(exact_src.encode()).hexdigest()[:24],
        "family_fp": hashlib.sha256(family_src.encode()).hexdigest()[:24],
        "semantic_text": " ".join([error_class, package, tail] + frames)[:1000],
    }

# ---- db ----------------------------------------------------------------------
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    # migration for DBs created by v0.1/v0.2 (no attribution column)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pitfalls)")}
    if "attribution" not in cols:
        conn.execute("ALTER TABLE pitfalls ADD COLUMN attribution TEXT")
    return conn

def _attribute(error_text, ctx):
    """Ask the local model for a structured guess. Soft: returns None on any failure/timeout."""
    r = engine.attribute(error_text, ctx, timeout=MODEL_TIMEOUT_S)
    return redact_card(r) if r else None

def out(obj, as_json):
    print(json.dumps(obj, ensure_ascii=False, indent=None if as_json else 2))

# ---- commands ----------------------------------------------------------------
def cmd_status(args):
    conn = db()
    n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
         for t in ("pitfalls", "occurrences", "resolutions")}
    verified = conn.execute("SELECT COUNT(*) FROM resolutions WHERE verified=1").fetchone()[0]
    fts_ok = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='pitfall_fts'").fetchone())
    model_ready = (MODEL_DIR / "openvino_model.bin").exists() and (MODEL_DIR / "openvino_model.xml").exists()
    out({"ok": True, "db": redact(str(DB_PATH)), "pitfalls": n["pitfalls"],
         "occurrences": n["occurrences"], "resolutions": n["resolutions"],
         "verified_resolutions": verified, "fts5": fts_ok,
         "model_ready": model_ready, "redaction": "on", "version": VERSION}, args.json)
    return 0

def _load_request(args):
    with open(args.request_file, encoding="utf-8") as f:
        return json.load(f)

def _resolution_for(conn, pid):
    r = conn.execute(
        "SELECT root_cause,fix_command,verify_method,verified FROM resolutions "
        "WHERE pitfall_id=? ORDER BY verified DESC, created_at DESC LIMIT 1", (pid,)).fetchone()
    if not r:
        return None
    return redact_card({"root_cause": r[0], "fix_command": r[1], "verify_method": r[2], "verified": bool(r[3])})

def _hits(conn, pid):
    return conn.execute("SELECT COUNT(*),MAX(seen_at) FROM occurrences WHERE pitfall_id=?", (pid,)).fetchone()

def cmd_lookup(args):
    req = _load_request(args)
    ctx = req.get("context", {})
    feat = extract(req["error_text"], ctx.get("runtime", ""))
    conn = db()

    # 1) exact: same fingerprint → 可引用 only if a verified resolution exists
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    if row:
        pid = row[0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        conf = "可引用" if (res and res["verified"]) else "需谨慎"
        out({"hit": "exact", "confidence": conf, "pitfall_id": pid,
             "times_seen": hits[0], "last_seen": hits[1], "resolution": res}, args.json)
        return 0

    # 2) family: same error class + package + message template, but details differ.
    #    Never 可引用 — the Host must re-verify before applying.
    rows = conn.execute(
        "SELECT id, norm_tail FROM pitfalls WHERE family_fp=? ORDER BY created_at DESC LIMIT 3",
        (feat["family_fp"],)).fetchall()
    if rows:
        pid = rows[0][0]; res = _resolution_for(conn, pid); hits = _hits(conn, pid)
        out({"hit": "family", "confidence": "需谨慎", "pitfall_id": pid,
             "family_size": len(rows), "times_seen": hits[0], "last_seen": hits[1],
             "family_hint": "same error class/package, different details — verify before applying",
             "known_variant": redact(rows[0][1]), "resolution": res}, args.json)
        return 0

    # 3) semantic: FTS5 BM25 over normalized text → 仅联想
    tokens = re.findall(r"[A-Za-z0-9_]{3,}", feat["semantic_text"])[:20]
    q = " OR ".join(f'"{t}"' for t in tokens)
    if q:
        rows = conn.execute(
            "SELECT rowid FROM pitfall_fts WHERE pitfall_fts MATCH ? ORDER BY bm25(pitfall_fts) LIMIT 3",
            (q,)).fetchall()
        if rows:
            pid = rows[0][0]
            resp = {"hit": "semantic", "confidence": "仅联想", "pitfall_id": pid,
                    "resolution": _resolution_for(conn, pid)}
            if getattr(args, "attribute", False):
                resp["attribution"] = _attribute(req["error_text"], ctx)   # fuzzy path only
            out(resp, args.json)
            return 0
    resp = {"hit": "none", "confidence": None, "note": "no local history; solve fresh then propose+commit"}
    if getattr(args, "attribute", False):
        resp["attribution"] = _attribute(req["error_text"], ctx)           # fuzzy path only
    out(resp, args.json)
    return 0

def cmd_propose(args):
    req = _load_request(args)
    ctx = req.get("context", {})
    feat = extract(req["error_text"], ctx.get("runtime", ""))
    now = int(time.time())
    conn = db()
    row = conn.execute("SELECT id FROM pitfalls WHERE exact_fp=?", (feat["exact_fp"],)).fetchone()
    attribution = None
    if row:
        pid = row[0]
    else:
        # first record of this pit → one model call to structure it (skippable with --no-model)
        if not getattr(args, "no_model", False):
            attribution = _attribute(req["error_text"], ctx)
        cur = conn.execute(
            "INSERT INTO pitfalls(exact_fp,family_fp,runtime,error_class,package,norm_tail,created_at,attribution) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (feat["exact_fp"], feat["family_fp"], feat["runtime"], feat["error_class"],
             feat["package"], feat["norm_tail"], now, json.dumps(attribution, ensure_ascii=False) if attribution else None))
        pid = cur.lastrowid
        conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(?,?)", (pid, feat["semantic_text"]))
    # everything stored is redacted at write time — the DB never holds secrets, emails or public IPs
    conn.execute("INSERT INTO occurrences(pitfall_id,cwd,raw_head,seen_at) VALUES(?,?,?,?)",
                 (pid, redact(ctx.get("cwd", "")), redact(req["error_text"][:500]), now))
    cur = conn.execute(
        "INSERT INTO resolutions(pitfall_id,root_cause,fix_command,verify_method,verified,created_at) "
        "VALUES(?,?,?,?,0,?)",
        (pid, redact(req.get("root_cause", "")), redact(req.get("fix_command", "")),
         redact(req.get("verify_method", "")), now))
    conn.commit()
    resp = {"ok": True, "pitfall_id": pid, "proposal_id": cur.lastrowid,
            "state": "proposed (unverified) — run commit after the fix is verified"}
    if attribution:
        resp["attribution"] = attribution
    out(resp, args.json)
    return 0

def cmd_commit(args):
    conn = db()
    if int(args.verify_exit_code) != 0:
        out({"ok": False, "note": "verification failed; proposal stays unverified"}, args.json)
        return 1
    n = conn.execute("UPDATE resolutions SET verified=1, verified_at=? WHERE id=?",
                     (int(time.time()), args.id)).rowcount
    conn.commit()
    out({"ok": bool(n), "resolution_id": args.id, "state": "verified"}, args.json)
    return 0 if n else 1

def cmd_digest(args):
    conn = db()
    rows = conn.execute(
        "SELECT p.error_class,p.package,p.norm_tail,r.root_cause,r.fix_command,"
        "(SELECT COUNT(*) FROM occurrences o WHERE o.pitfall_id=p.id) "
        "FROM pitfalls p JOIN resolutions r ON r.pitfall_id=p.id AND r.verified=1 "
        "ORDER BY 6 DESC").fetchall()
    lines = ["# Pitfalls we hit (verified fixes only)", "",
             "| Error | Package | Root cause | Fix | Times |", "|---|---|---|---|---|"]
    for ec, pkg, tail, cause, fix, cnt in rows:
        cell = lambda s: (s or "").replace("|", "\\|").replace("\n", " ")[:120]
        lines.append(f"| {cell(ec) or cell(tail)} | {cell(pkg)} | {cell(cause)} | `{cell(fix)}` | {cnt} |")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        out({"ok": True, "out": args.out, "verified_entries": len(rows)}, True)
    else:
        print(text)
    return 0

def main():
    p = argparse.ArgumentParser(prog="local-pitfall-memory")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status",):
        s = sub.add_parser(name); s.add_argument("--json", action="store_true")
    s = sub.add_parser("lookup")
    s.add_argument("--request-file", required=True)
    s.add_argument("--json", action="store_true")
    s.add_argument("--attribute", action="store_true",
                   help="on semantic/none results, also ask the local model for a structured guess (slow, ~15-20s CPU)")
    s = sub.add_parser("propose")
    s.add_argument("--request-file", required=True)
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-model", action="store_true", help="skip the one-time model attribution on first record")
    s = sub.add_parser("server")
    s.add_argument("action", choices=["status", "start", "stop"])
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("commit")
    s.add_argument("--id", required=True)
    s.add_argument("--verify-exit-code", required=True)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("digest")
    s.add_argument("--out")
    s.add_argument("--json", action="store_true")
    args = p.parse_args()
    return {"status": cmd_status, "lookup": cmd_lookup, "propose": cmd_propose,
            "commit": cmd_commit, "digest": cmd_digest, "server": cmd_server}[args.cmd](args)

def cmd_server(args):
    if args.action == "status":
        st = engine.status(); out(st or {"ok": False, "state": "down"}, args.json); return 0 if st else 1
    if args.action == "start":
        st = engine.ensure_server(); out(st or {"ok": False, "state": "failed to start"}, args.json); return 0 if st else 1
    if args.action == "stop":
        r = engine.shutdown(); out(r or {"ok": True, "state": "already down"}, args.json); return 0
    return 1

if __name__ == "__main__":
    sys.exit(main())
