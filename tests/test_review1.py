"""Tests added for codex review #1 findings (P0/P1/P2). Run: python tests/test_review1.py"""
import json, os, re, sqlite3, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import client                       # noqa: E402
from redact import redact           # noqa: E402


class CLIBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = str(Path(self.tmp.name) / "t.db")
        self.env = dict(os.environ, PITFALL_DB=self.dbp, PITFALL_FAKE_MODEL="1")
    def tearDown(self):
        self.tmp.cleanup()
    def run_cli(self, *args, expect=0):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "client.py"), *args, "--json"],
                           capture_output=True, text=True, env=self.env, encoding="utf-8")
        self.assertEqual(p.returncode, expect, f"stdout={p.stdout!r} stderr={p.stderr[-400:]!r}")
        return json.loads(p.stdout.strip().splitlines()[-1])
    def write(self, name, obj_or_bytes):
        path = Path(self.tmp.name) / name
        if isinstance(obj_or_bytes, bytes):
            path.write_bytes(obj_or_bytes)
        else:
            path.write_text(json.dumps(obj_or_bytes), encoding="utf-8")
        return str(path)


class TestP0Redaction(CLIBase):
    def test_redacts_pitfall_norm_tail_fts_and_digest(self):
        secret = "authorization=Bearer abcdefghijklmnopqrstuvwxyz0123"
        err = f"RequestError: 401 Unauthorized ({secret})\n    at call (C:\\a\\b.js:1:1)"
        fix = self.write("f.json", {"error_text": err, "context": {"runtime": "node"},
                                    "root_cause": f"bad header {secret}", "fix_command": "rotate key",
                                    "verify_method": "curl 200"})
        r = self.run_cli("propose", "--request-file", fix)
        self.run_cli("commit", "--id", str(r["proposal_id"]), "--verify-exit-code", "0")
        db = sqlite3.connect(self.dbp)
        try:
            for table, col in (("pitfalls", "norm_tail"), ("occurrences", "raw_head"), ("resolutions", "root_cause"),
                               ("pitfall_fts", "semantic_text")):
                for (v,) in db.execute(f"SELECT {col} FROM {table}").fetchall():
                    self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", v or "", f"{table}.{col} leaked")
        finally:
            db.close()
        out_md = str(Path(self.tmp.name) / "d.md")
        self.run_cli("digest", "--out", out_md)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", Path(out_md).read_text(encoding="utf-8"))

    def test_redacts_public_ipv6(self):
        s = redact("peer 2001:4860:4860::8888 and fe80::1 and ::1 and 8.8.8.8 and 10.0.0.1")
        self.assertNotIn("2001:4860:4860::8888", s); self.assertNotIn("8.8.8.8", s)
        self.assertIn("fe80::1", s); self.assertIn("::1", s); self.assertIn("10.0.0.1", s)

    def test_migration_rescrubs_old_rows(self):
        """A DB written by v0.3 with raw norm_tail gets redacted on first open."""
        db = sqlite3.connect(self.dbp)
        db.executescript(client.SCHEMA.replace("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);", ""))
        db.execute("INSERT INTO pitfalls(exact_fp,family_fp,runtime,error_class,package,norm_tail,created_at) VALUES('x','y','node','E','', 'token=SUPERSECRET123456', 1)")
        db.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(1,'E token=SUPERSECRET123456')")
        db.commit(); db.close()
        self.run_cli("status")
        db = sqlite3.connect(self.dbp)
        try:
            self.assertNotIn("SUPERSECRET123456", db.execute("SELECT norm_tail FROM pitfalls").fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pitfall_fts WHERE pitfall_fts MATCH '\"SUPERSECRET\"'").fetchone()[0], 0)
        finally:
            db.close()


class TestP0Validation(CLIBase):
    def test_rejects_empty_error_text(self):
        r = self.run_cli("lookup", "--request-file", self.write("r.json", {"error_text": "   "}), expect=1)
        self.assertEqual(r["error"], "request_empty")
        r = self.run_cli("propose", "--request-file", self.write("p.json", {"error_text": ""}), expect=1)
        self.assertEqual(r["error"], "request_empty")

    def test_rejects_oversized_error_text(self):
        r = self.run_cli("lookup", "--request-file", self.write("r.json", {"error_text": "x" * 70_000}), expect=1)
        self.assertEqual(r["error"], "request_too_large")

    def test_non_utf8_request_returns_structured_error(self):
        r = self.run_cli("lookup", "--request-file", self.write("bad.json", b'{"error_text": "\xff\xfe oops"}'), expect=1)
        self.assertEqual(r["error"], "request_not_utf8")

    def test_malformed_request_schema(self):
        r = self.run_cli("lookup", "--request-file", self.write("a.json", b"{not json"), expect=1)
        self.assertEqual(r["error"], "request_bad_json")
        r = self.run_cli("lookup", "--request-file", self.write("b.json", {"error_text": 42}), expect=1)
        self.assertEqual(r["error"], "request_schema")
        r = self.run_cli("lookup", "--request-file", self.write("c.json", {"error_text": "E", "context": "nope"}), expect=1)
        self.assertEqual(r["error"], "request_schema")


class TestP1Robustness(CLIBase):
    def test_corrupt_db_returns_structured_error(self):
        Path(self.dbp).write_bytes(b"this is not a sqlite database at all" * 50)
        r = self.run_cli("status", expect=1)
        self.assertEqual(r["error"], "db_corrupt")
        self.assertTrue(any(p.name.startswith("t.db.corrupt-") for p in Path(self.tmp.name).iterdir()))
        self.assertTrue(self.run_cli("status")["ok"])          # fresh DB works on next call

    def test_normalizes_workspace_unc_and_space_paths(self):
        a = client.extract("Error: ENOENT open '/workspace/a/src/x.js'\n    at f (/workspace/a/src/x.js:1:1)", "node")
        b = client.extract("Error: ENOENT open '/builds/b/src/x.js'\n    at f (/builds/b/src/x.js:9:9)", "node")
        self.assertEqual(a["exact_fp"], b["exact_fp"])
        c = client.extract(r"Error: ENOENT open '\\srv\share\proj\x.js'", "node")
        d = client.extract(r"Error: ENOENT open 'C:\Program Files\My App\x.js'", "node")
        self.assertEqual(c["norm_tail"], d["norm_tail"])

    def test_runtime_is_case_insensitive(self):
        a = client.extract("TypeError: boom", "Node"); b = client.extract("TypeError: boom", "node ")
        self.assertEqual(a["exact_fp"], b["exact_fp"])

    def test_status_uses_info_required_files(self):
        st = self.run_cli("status")
        self.assertIn("model", st); self.assertIn("model_ready", st)
        self.assertEqual(st["model"], client.INFO["models"][0]["dir_name"])

    def test_concurrent_propose_same_fingerprint(self):
        fix = self.write("f.json", {"error_text": "TypeError: race\n    at f (C:\\a\\b.js:1:1)", "context": {"runtime": "node"},
                                    "root_cause": "r", "fix_command": "c", "verify_method": "v"})
        results, errs = [], []
        def go():
            try:
                results.append(self.run_cli("propose", "--request-file", fix))
            except AssertionError as e:
                errs.append(str(e))
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertFalse(errs, errs[:1])
        self.assertEqual(len({r["pitfall_id"] for r in results}), 1)
        db = sqlite3.connect(self.dbp)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM pitfalls").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0], 8)
        finally:
            db.close()


class TestP2AndContracts(CLIBase):
    def test_family_size_reports_full_count(self):
        for i in range(5):
            self.run_cli("propose", "--request-file", self.write(f"f{i}.json", {
                "error_text": f"TypeError: Cannot read properties of undefined (reading 'k{i}')\n    at f (C:\\a\\b.js:1:1)",
                "context": {"runtime": "node"}, "root_cause": "r", "fix_command": "c", "verify_method": "v"}))
        r = self.run_cli("lookup", "--request-file", self.write("q.json", {
            "error_text": "TypeError: Cannot read properties of undefined (reading 'zzz')\n    at f (C:\\a\\b.js:1:1)",
            "context": {"runtime": "node"}}))
        self.assertEqual(r["hit"], "family"); self.assertEqual(r["family_size"], 5)
        self.assertLessEqual(len(r["known_variants"]), 3)

    def test_skill_frontmatter_contract(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---", text, re.S); self.assertTrue(m)
        fm = m.group(1)
        self.assertIn("name: local-pitfall-memory", fm)
        desc = fm.split("description:", 1)[1]
        self.assertLessEqual(len(desc.strip()), 1024, f"description is {len(desc.strip())} chars")
        for kw in ("本地", "offline", "Prefer this skill"):
            self.assertIn(kw, desc)
        self.assertIn("--continue", text); self.assertIn("resolution.verified", text)

    def test_metadata_schema_and_version(self):
        meta = json.loads((ROOT / "meta.json").read_text(encoding="utf-8"))
        for k in ("name", "display_name", "display_description", "detail_describe", "icon", "use_cases", "author", "version"):
            self.assertIn(k, meta)
        self.assertEqual(meta["name"], "local-pitfall-memory")
        self.assertEqual(meta["version"], client.VERSION)
        info = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
        self.assertTrue(info["models"][0]["required_files"])
        self.assertTrue(any(f.endswith((".xml", ".bin")) for f in info["models"][0]["required_files"]))

    def test_requirements_are_pinned(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pins = [l for l in req.splitlines() if l and not l.startswith("#")]
        self.assertTrue(pins and all("==" in l for l in pins), pins)


if __name__ == "__main__":
    unittest.main(verbosity=2)
