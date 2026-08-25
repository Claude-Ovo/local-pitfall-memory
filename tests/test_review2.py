"""Tests for codex review #2 findings. Run: python tests/test_review2.py  (fake model; no OpenVINO)"""
import json, os, sqlite3, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["PITFALL_FAKE_MODEL"] = "1"
import client, engine   # noqa: E402


class CLIBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = str(Path(self.tmp.name) / "t.db")
        self.env = dict(os.environ, PITFALL_DB=self.dbp, PITFALL_FAKE_MODEL="1")
    def tearDown(self):
        self.tmp.cleanup()
    def cli(self, *args, expect=0, env=None):
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "client.py"), *args, "--json"],
                           capture_output=True, text=True, env=env or self.env, encoding="utf-8")
        self.assertEqual(p.returncode, expect, f"stdout={p.stdout!r} stderr={p.stderr[-400:]!r}")
        return json.loads(p.stdout.strip().splitlines()[-1])
    def w(self, name, obj):
        p = Path(self.tmp.name) / name; p.write_text(json.dumps(obj), encoding="utf-8"); return str(p)
    def fix(self, name, text, runtime="node", **kw):
        d = {"error_text": text, "context": {"runtime": runtime}, "root_cause": "r", "fix_command": "c", "verify_method": "v"}
        d.update(kw); return self.w(name, d)


class TestP0(CLIBase):
    def test_fts_only_semantic_hit_without_embedder(self):
        """--no-model rows have no vector; a later lookup with the embedder down must still hit via FTS."""
        self.cli("propose", "--no-model", "--request-file", self.fix("a.json",
                 "ECONNREFUSED connect 127.0.0.1:5432 postgres refused\n    at net (C:\\a\\db.js:1:1)"))
        engine.shutdown(); time.sleep(0.3)
        env = dict(self.env, PITFALL_EMBED_SPAWN_WAIT="0")                 # embedder unreachable → FTS-only
        r = self.cli("lookup", "--request-file", self.w("q.json", {
            "error_text": "Error: connect ECONNREFUSED postgres 127.0.0.1:5432", "context": {"runtime": "node"}}), env=env)
        self.assertEqual(r["hit"], "semantic"); self.assertEqual(r["retrieval"]["mode"], "fts-only")
        self.assertEqual(r["retrieval"]["channels"], ["fts5"])

    def test_pre_v050_row_remains_semantically_retrievable(self):
        """Rows without embeddings get lazily backfilled when the embedder is up."""
        self.cli("propose", "--no-model", "--request-file", self.fix("a.json", "KeyError: 'user' in payload handler"))
        r = self.cli("lookup", "--request-file", self.w("q.json", {"error_text": "KeyError: 'user' payload handler crash",
                                                                   "context": {"runtime": "node"}}))
        self.assertEqual(r["hit"], "semantic")
        db = sqlite3.connect(self.dbp)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0], 1)   # backfilled
        finally:
            db.close()

    def test_propose_requires_nonempty_resolution_fields(self):
        r = self.cli("propose", "--request-file", self.w("p.json", {"error_text": "TypeError: x"}), expect=1)
        self.assertEqual(r["error"], "request_schema")
        r = self.cli("propose", "--request-file", self.w("p2.json", {"error_text": "TypeError: x", "root_cause": " ",
                                                                     "fix_command": "c", "verify_method": "v"}), expect=1)
        self.assertEqual(r["error"], "request_schema")

    def test_commit_rejects_empty_resolution(self):
        self.cli("propose", "--request-file", self.fix("a.json", "TypeError: y"))
        db = sqlite3.connect(self.dbp)
        try:
            db.execute("UPDATE resolutions SET fix_command=''"); db.commit()
        finally:
            db.close()
        r = self.cli("commit", "--id", "1", "--verify-exit-code", "0", expect=1)
        self.assertEqual(r["error"], "empty_resolution")

    def test_redacts_runtime_before_fingerprint_and_storage(self):
        self.cli("propose", "--request-file", self.fix("a.json", "TypeError: z", runtime="authorization=SUPERSECRET123456 node"))
        db = sqlite3.connect(self.dbp)
        try:
            rt = db.execute("SELECT runtime FROM pitfalls").fetchone()[0]
        finally:
            db.close()
        self.assertNotIn("SUPERSECRET123456", rt); self.assertLessEqual(len(rt), 32)
        self.assertEqual(client.norm_runtime("Node "), "node")

    def test_redacts_server_status_error(self):
        home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        st = {"ok": True, "state": "error", "error": f"FileNotFoundError: {home}\\x missing token=abcdef123456"}
        red = client.deep_redact(st)
        self.assertNotIn("abcdef123456", red["error"])
        if home:
            self.assertNotIn(home, red["error"])

    def test_entry_gate_has_no_cim_dependency(self):
        text = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("$ErrorActionPreference = 'Stop'"))
        gate = text.split("# --- 2.")[0]
        self.assertNotIn("Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1GB, 1)\nif", gate)
        self.assertIn("PROCESSOR_ARCHITE", gate); self.assertIn("ComputerInfo", gate)
        self.assertNotIn("AVX2", gate.split("ISA-level")[0])


class TestP1(CLIBase):
    def test_send_deadline_includes_pipe_connect(self):
        engine.shutdown(); time.sleep(0.3)
        t = time.monotonic()
        with self.assertRaises(Exception):
            engine._send({"op": "status"}, timeout=1.0)
        self.assertLess(time.monotonic() - t, 3.0)

    def test_spawn_wait_zero_does_not_spawn(self):
        engine.shutdown(); time.sleep(0.3)
        self.assertIsNone(engine.ensure_server(spawn_wait=0))
        time.sleep(0.5)
        self.assertIsNone(engine.status())

    def test_two_clients_cold_start_spawn_once(self):
        engine.shutdown(); time.sleep(0.3)
        procs = []
        for _ in range(3):
            procs.append(subprocess.Popen([sys.executable, str(ROOT / "scripts" / "client.py"), "server", "start", "--json"],
                                          env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        outs = [json.loads(p.communicate(timeout=90)[0].strip().splitlines()[-1]) for p in procs]
        pids = {o.get("pid") for o in outs if o.get("state") == "running"}
        self.assertEqual(len(pids), 1, outs)
        engine.shutdown()

    def test_restarts_stale_server_version(self):
        engine.shutdown(); time.sleep(0.3)
        st = engine.ensure_server(); self.assertIsNotNone(st)
        old = engine.script_hash
        try:
            engine.script_hash = lambda: "deadbeef0000"          # pretend our scripts changed
            st2 = engine.ensure_server(spawn_wait=30)
            self.assertIsNone(st2)                              # new server (real hash) never matches the fake hash…
        finally:
            engine.script_hash = old
        st3 = engine.ensure_server(spawn_wait=30)                # …but with the real hash a fresh server comes up
        self.assertIsNotNone(st3); self.assertNotEqual(st3["pid"], st["pid"])
        engine.shutdown()

    def test_optional_embed_failure_does_not_block_continue(self):
        import download_model as dm
        specs = [{"dir_name": "req", "required": True, "required_files": ["a"]},
                 {"dir_name": "opt", "required": False, "required_files": ["a"]}]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "req").mkdir(); (Path(d) / "req" / "a").write_text("x")
            old_dir, old_info = dm.MODELS_DIR, dm.INFO
            dm.MODELS_DIR = Path(d); dm.INFO = {"models": specs}
            dm._fetch = lambda m: 3                              # optional download "fails"
            try:
                self.assertEqual(dm.main(), 0)
            finally:
                dm.MODELS_DIR, dm.INFO = old_dir, old_info

    def test_env_incompatible_candidate_is_demoted_not_hidden(self):
        self.cli("propose", "--request-file", self.fix("a.json", "PermissionError: [Errno 13] Permission denied: 'out.log'", runtime="python"))
        r = self.cli("lookup", "--request-file", self.w("q.json", {"error_text": "PermissionError: [Errno 13] Permission denied: 'out.log' again",
                                                                   "context": {"runtime": "node"}}))
        self.assertEqual(r["hit"], "semantic"); self.assertFalse(r["retrieval"]["env_compatible"])

    def test_install_env_asserts_python_version(self):
        text = (ROOT / "scripts" / "install-env.ps1").read_text(encoding="utf-8")
        self.assertIn("sys.version_info", text)
        self.assertIn("exit 1", text.split("if ($actual -ne", 1)[1].split("}", 1)[0])


class TestP2(CLIBase):
    def test_retrieval_channels_describe_selected_candidate(self):
        self.cli("propose", "--no-model", "--request-file", self.fix("a.json", "ERR_SOCKET_TIMEOUT fetch registry timed out"))
        engine.shutdown(); time.sleep(0.3)
        env = dict(self.env, PITFALL_EMBED_SPAWN_WAIT="0")
        r = self.cli("lookup", "--request-file", self.w("q.json", {"error_text": "ERR_SOCKET_TIMEOUT registry fetch timed out",
                                                                   "context": {"runtime": "node"}}), env=env)
        self.assertEqual(r["retrieval"]["channels"], ["fts5"]); self.assertIsNone(r["retrieval"]["vec_rank"])

    def test_server_uses_info_model_specs(self):
        import server
        self.assertIsNotNone(server._spec("attribution")); self.assertEqual(server._spec("attribution")["dir_name"], client._spec("attribution")["dir_name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
