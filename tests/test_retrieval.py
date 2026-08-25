"""Hybrid retrieval (FTS5 ⊕ vector, RRF, env-compat) with the fake embedder. Run: python tests/test_retrieval.py"""
import json, os, sqlite3, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["PITFALL_FAKE_MODEL"] = "1"
import client, engine   # noqa: E402


class TestRRF(unittest.TestCase):
    def test_rrf_fuses_two_channels(self):
        fused = client._rrf([1, 2, 3], [3, 1, 4])
        ids = [pid for pid, _ in fused]
        self.assertEqual(ids[0], 1)                    # rank1 + rank2
        self.assertIn(3, ids[:2])                      # rank3 + rank1
        self.assertEqual(ids[-1], 4)                   # only one channel, rank3
    def test_rrf_single_channel_ok(self):
        self.assertEqual([p for p, _ in client._rrf([7, 8], [])], [7, 8])


class TestHybridCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.shutdown(); time.sleep(0.5)
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "server.py")],
                                    env=dict(os.environ, PITFALL_FAKE_MODEL="1"))
        for _ in range(50):
            st = engine.status()
            if st and st.get("state") == "running":
                break
            time.sleep(0.3)
    @classmethod
    def tearDownClass(cls):
        engine.shutdown(); time.sleep(0.5)
        if cls.proc.poll() is None:
            cls.proc.kill()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = str(Path(self.tmp.name) / "t.db")
        self.env = dict(os.environ, PITFALL_DB=self.dbp, PITFALL_FAKE_MODEL="1")
    def tearDown(self):
        self.tmp.cleanup()
    def cli(self, *args):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "client.py"), *args, "--json"],
                           capture_output=True, text=True, env=self.env, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr[-300:])
        return json.loads(p.stdout.strip().splitlines()[-1])
    def w(self, name, obj):
        p = Path(self.tmp.name) / name; p.write_text(json.dumps(obj), encoding="utf-8"); return str(p)

    def test_embedding_stored_and_vector_channel_used(self):
        self.cli("propose", "--request-file", self.w("a.json", {
            "error_text": "ECONNREFUSED connect 127.0.0.1:5432 postgres refused\n    at net (C:\\a\\db.js:1:1)",
            "context": {"runtime": "node"}, "root_cause": "db down", "fix_command": "docker start pg", "verify_method": "psql"}))
        db = sqlite3.connect(self.dbp)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 1)
        finally:
            db.close()
        r = self.cli("lookup", "--request-file", self.w("q.json", {
            "error_text": "Error: connect ECONNREFUSED postgres 127.0.0.1:5432", "context": {"runtime": "node"}}))
        self.assertEqual(r["hit"], "semantic")
        self.assertIn("vector", r["retrieval"]["channels"])
        self.assertIsNotNone(r["retrieval"]["vec_rank"])

    def test_env_incompatible_is_demoted_not_hidden(self):
        # same words, recorded under python
        self.cli("propose", "--request-file", self.w("a.json", {
            "error_text": "PermissionError: [Errno 13] Permission denied: 'out.log'",
            "context": {"runtime": "python"}, "root_cause": "r", "fix_command": "c", "verify_method": "v"}))
        r = self.cli("lookup", "--request-file", self.w("q.json", {
            "error_text": "EACCES: permission denied, open 'out.log'", "context": {"runtime": "node"}}))
        if r["hit"] == "semantic":
            self.assertFalse(r["retrieval"]["env_compatible"])
        else:
            self.assertEqual(r["hit"], "none")

    def test_no_model_skips_embedding(self):
        self.cli("propose", "--no-model", "--request-file", self.w("a.json", {
            "error_text": "TypeError: x is not a function", "context": {"runtime": "node"},
            "root_cause": "r", "fix_command": "c", "verify_method": "v"}))
        db = sqlite3.connect(self.dbp)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
