"""Unit tests (stdlib unittest, no deps). Run: python tests/test_unit.py"""
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import client                                                  # noqa: E402
from redact import redact                                      # noqa: E402


class TestRedact(unittest.TestCase):
    def test_api_keys(self):
        self.assertIn("<REDACTED_KEY>", redact("key sk-abcdefghijklmnopqrstuvwxyz123456 here"))
        self.assertIn("<REDACTED_KEY>", redact("AKIAABCDEFGHIJKLMNOP"))
        self.assertIn("<REDACTED_KEY>", redact("ghp_" + "a" * 30))

    def test_kv_secret(self):
        s = redact("DATABASE_PASSWORD=hunter2hunter2 and api_key: 'abcdef123456'")
        self.assertNotIn("hunter2hunter2", s)
        self.assertNotIn("abcdef123456", s)

    def test_email_and_public_ip(self):
        s = redact("mail zyc@example.com from 8.8.8.8 and 192.168.1.5")
        self.assertIn("<EMAIL>", s); self.assertIn("<IP>", s)
        self.assertIn("192.168.1.5", s, "private IPs must be kept")

    def test_url_credentials(self):
        self.assertIn("<REDACTED_CRED>@", redact("postgres://admin:s3cret@db.local:5432/x"))

    def test_home_and_user(self):
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        user = os.environ.get("USERNAME") or os.environ.get("USER")
        if home:
            self.assertNotIn(home, redact(f"path {home}\\proj\\a.js"))
        if user and len(user) >= 3:
            self.assertIn("<USER>", redact(f"C:\\Users\\{user}\\x"))

    def test_keeps_error_codes(self):
        s = redact("HTTP 503 errno 111 TS2345 exit code 1")
        for tok in ("503", "111", "TS2345", "exit code 1"):
            self.assertIn(tok, s)


class TestNormalize(unittest.TestCase):
    def test_strips_volatile(self):
        raw = ("\x1b[31mError\x1b[0m at C:\\Users\\x\\a.js:12:5 pid 4242 "
               "2026-08-24T10:00:00Z 0xDEADBEEF 550e8400-e29b-41d4-a716-446655440000")
        n = client.normalize(raw)
        for bad in ("\x1b", "C:\\Users", ":12:5", "4242", "2026-08-24", "0xDEAD", "550e8400"):
            self.assertNotIn(bad, n)

    def test_keeps_semantic_numbers(self):
        n = client.normalize("HTTP 404 errno 2 E0308 exit code 137 port 5432 version 20.11.1")
        for keep in ("404", "errno 2", "E0308", "137", "5432", "20.11.1"):
            self.assertIn(keep, n)


class TestFingerprints(unittest.TestCase):
    A = ("Error [ERR_REQUIRE_ESM]: require() of ES Module C:\\Users\\a\\p\\x.js from C:\\Users\\a\\p\\i.js not supported.\n"
         "    at Object.<anonymous> (C:\\Users\\a\\p\\i.js:3:15)")
    B = ("Error [ERR_REQUIRE_ESM]: require() of ES Module D:\\work\\q\\dist\\m.js from D:\\work\\q\\b.js not supported.\n"
         "    at Object.<anonymous> (D:\\work\\q\\b.js:11:22)")
    C = ("Error [ERR_MODULE_NOT_FOUND]: Cannot find module 'D:\\work\\q\\node_modules\\foo' imported from D:\\work\\q\\b.js")

    def test_cross_project_same_pit_is_exact(self):
        a, b = client.extract(self.A, "node"), client.extract(self.B, "node")
        self.assertEqual(a["exact_fp"], b["exact_fp"])

    def test_different_error_class_not_family(self):
        a, c = client.extract(self.A, "node"), client.extract(self.C, "node")
        self.assertNotEqual(a["exact_fp"], c["exact_fp"])
        self.assertNotEqual(a["family_fp"], c["family_fp"])

    def test_family_when_only_quoted_detail_differs(self):
        x = client.extract("TypeError: Cannot read properties of undefined (reading 'foo')\n    at run (C:\\a\\b.js:1:1)", "node")
        y = client.extract("TypeError: Cannot read properties of undefined (reading 'bar')\n    at run (C:\\a\\b.js:1:1)", "node")
        self.assertNotEqual(x["exact_fp"], y["exact_fp"])
        self.assertEqual(x["family_fp"], y["family_fp"])


class TestLookupChain(unittest.TestCase):
    """End-to-end through the CLI with an isolated DB: none → propose → family → commit → exact."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, PITFALL_DB=str(Path(self.tmp.name) / "t.db"), PITFALL_FAKE_MODEL="1")
        self.py = sys.executable
        self.cli = str(ROOT / "scripts" / "client.py")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        p = subprocess.run([self.py, self.cli, *args, "--json"], capture_output=True, text=True,
                           env=self.env, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def write(self, name, obj):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return str(path)

    def test_chain(self):
        e1 = "TypeError: Cannot read properties of undefined (reading 'foo')\n    at run (C:\\a\\b.js:1:1)"
        e2 = "TypeError: Cannot read properties of undefined (reading 'bar')\n    at run (C:\\a\\b.js:9:9)"
        ctx = {"cwd": "C:/a", "runtime": "node"}
        req1 = self.write("r1.json", {"error_text": e1, "context": ctx})
        self.assertEqual(self.run_cli("lookup", "--request-file", req1)["hit"], "none")
        fix = self.write("f1.json", {"error_text": e1, "context": ctx, "root_cause": "obj undefined",
                                     "fix_command": "add null check; token=abcdef123456", "verify_method": "run"})
        r = self.run_cli("propose", "--request-file", fix)
        # family path: different quoted detail → family, never 可引用
        req2 = self.write("r2.json", {"error_text": e2, "context": ctx})
        fam = self.run_cli("lookup", "--request-file", req2)
        self.assertEqual(fam["hit"], "family"); self.assertEqual(fam["confidence"], "需谨慎")
        self.assertIn("family_hint", fam)
        # redaction at write time: the secret never reaches the DB
        db = sqlite3.connect(self.env["PITFALL_DB"])
        try:
            stored = db.execute("SELECT fix_command FROM resolutions").fetchone()[0]
        finally:
            db.close()   # Windows: an open handle blocks TemporaryDirectory cleanup
        self.assertNotIn("abcdef123456", stored); self.assertIn("<REDACTED>", stored)
        # commit → exact/可引用
        self.run_cli("commit", "--id", str(r["proposal_id"]), "--verify-exit-code", "0")
        ex = self.run_cli("lookup", "--request-file", req1)
        self.assertEqual((ex["hit"], ex["confidence"]), ("exact", "可引用"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
