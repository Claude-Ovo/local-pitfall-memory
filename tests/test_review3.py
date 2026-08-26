"""Tests for codex review #3 (judge-view audit) findings. Run: python tests/test_review3.py  (fake model; no OpenVINO, no network)"""
import json, os, sqlite3, subprocess, sys, tempfile, time, unittest, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["PITFALL_FAKE_MODEL"] = "1"
import client  # noqa: E402

SECRET = "token=SUPERSECRET123456"


class CLIBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dbp = str(Path(self.tmp.name) / "t.db")
        self.env = dict(os.environ, PITFALL_DB=self.dbp, PITFALL_FAKE_MODEL="1")
    def tearDown(self):
        self.tmp.cleanup()
    def cli(self, *args, expect=0, env=None, json_flag=True):
        cmd = [sys.executable, str(ROOT / "scripts" / "client.py"), *args] + (["--json"] if json_flag else [])
        p = subprocess.run(cmd, capture_output=True, text=True, env=env or self.env, encoding="utf-8")
        self.assertEqual(p.returncode, expect, f"stdout={p.stdout!r} stderr={p.stderr[-400:]!r}")
        return p
    def cli_json(self, *args, **kw):
        p = self.cli(*args, **kw)
        return json.loads(p.stdout.strip().splitlines()[-1])
    def w(self, name, obj):
        p = Path(self.tmp.name) / name; p.write_text(json.dumps(obj), encoding="utf-8"); return str(p)


class TestP0Privacy(CLIBase):
    def test_unc_db_path_is_rejected_structured(self):
        env = dict(self.env, PITFALL_DB=r"\\server\share\pitfalls.db")
        r = self.cli_json("status", expect=1, env=env)
        self.assertEqual(r["error"], "path_not_local")

    def test_digest_out_rejects_unc_and_directories(self):
        self.cli("propose", "--no-model", "--request-file", self.w("f.json", {
            "error_text": "TypeError: x", "root_cause": "r", "fix_command": "c", "verify_method": "v"}))
        self.cli("commit", "--id", "1", "--verify-exit-code", "0")
        r = self.cli_json("digest", "--out", r"\\server\share\pitfalls.md", expect=1)
        self.assertEqual(r["error"], "path_not_local")
        r = self.cli_json("digest", "--out", self.tmp.name, expect=1)      # a directory, not a file
        self.assertEqual(r["error"], "output_unwritable")

    def test_old_db_rows_are_scrubbed_in_every_string_column(self):
        """Simulate a pre-0.7 DB whose rows were written without redaction; opening it must scrub all six columns."""
        conn = sqlite3.connect(self.dbp); conn.executescript(client.SCHEMA)
        conn.execute("INSERT INTO pitfalls(id,exact_fp,family_fp,runtime,error_class,package,norm_tail,created_at,attribution) "
                     "VALUES(1,'a','b','node','TypeError','pkg',?,1,?)",
                     (f"boom {SECRET}", json.dumps({"fix_hint": f"use {SECRET}"})))
        conn.execute("INSERT INTO occurrences(pitfall_id,cwd,raw_head,seen_at) VALUES(1,?,?,1)",
                     (f"C:\\Users\\{os.environ.get('USERNAME','someone')}\\proj", f"head {SECRET}"))
        conn.execute("INSERT INTO resolutions(pitfall_id,root_cause,fix_command,verify_method,verified,created_at) VALUES(1,?,?,?,1,1)",
                     (f"cause {SECRET}", f"cmd {SECRET}", f"verify {SECRET}"))
        conn.execute("INSERT INTO pitfall_fts(rowid,semantic_text) VALUES(1,?)", (f"TypeError pkg boom {SECRET}",))
        conn.commit(); conn.close()
        self.cli("status")                                                  # any command opens + migrates
        conn = sqlite3.connect(self.dbp)
        try:
            for table, cols in client.SCRUB_COLUMNS.items():
                for row in conn.execute(f"SELECT {','.join(cols)} FROM {table}"):
                    for v in row:
                        self.assertNotIn("SUPERSECRET123456", v or "", f"{table} still leaks")
            fts = conn.execute("SELECT semantic_text FROM pitfall_fts WHERE pitfall_fts MATCH '\"SUPERSECRET\"'").fetchall()
            self.assertEqual(fts, [])
            self.assertTrue(conn.execute("SELECT v FROM meta WHERE k='full_scrub_migrated'").fetchone())
        finally:
            conn.close()

    def test_server_log_is_redacted(self):
        import importlib
        logp = Path(self.tmp.name) / "server.log"
        os.environ["PITFALL_SERVER_LOG"] = str(logp)
        try:
            import server; importlib.reload(server)
            server.log(f"Traceback ... {SECRET} at C:\\Users\\{os.environ.get('USERNAME','someone')}\\x.py")
        finally:
            os.environ.pop("PITFALL_SERVER_LOG", None)
        text = logp.read_text(encoding="utf-8")
        self.assertNotIn("SUPERSECRET123456", text)
        user = os.environ.get("USERNAME", "")
        if len(user) >= 3:
            self.assertNotIn(f"\\{user}\\", text)

    def test_downloader_offline_is_zero_network_and_json(self):
        """Empty models dir + PITFALL_OFFLINE=1 → exit 3, one redacted JSON line, no modelscope import."""
        env = dict(self.env, PITFALL_MODELS_DIR=str(Path(self.tmp.name) / "models"), PITFALL_OFFLINE="1",
                   PITFALL_DB=self.dbp)
        p = subprocess.run([sys.executable, str(ROOT / "scripts" / "download_model.py"), "--continue"],
                           capture_output=True, text=True, env=env, encoding="utf-8")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        lines = [l for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, "downloader must print exactly one line")
        r = json.loads(lines[0])
        self.assertEqual(r["state"], "pending"); self.assertEqual(r["network"], "none")
        self.assertNotIn(os.environ.get("USERPROFILE", "\x00"), lines[0])

    def test_downloader_report_is_redacted(self):
        import download_model as dm
        rc, rep = dm.run.__wrapped__() if hasattr(dm.run, "__wrapped__") else (None, None)
        rep = dm._deep_redact({"note": f"{SECRET} in ~\\x", "models": [{"path": f"C:\\Users\\{os.environ.get('USERNAME','someone')}\\m"}]})
        self.assertNotIn("SUPERSECRET123456", json.dumps(rep))


class TestP1Contract(CLIBase):
    def test_bad_arguments_are_structured_exit_1(self):
        r = self.cli_json("lookup", expect=1, json_flag=False)              # missing --request-file
        self.assertEqual(r["error"], "bad_arguments")
        r = self.cli_json("frobnicate", expect=1, json_flag=False)
        self.assertEqual(r["error"], "bad_arguments")

    def test_entry_script_emits_json_envelopes(self):
        text = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8")
        self.assertIn("function Fail", text)
        for code in ("platform_unsupported", "platform_probe_failed", "env_install_failed"):
            self.assertIn(code, text)
        self.assertNotIn("Write-Output 'This skill requires", text)         # no bare-text failures left
        gate = text.split("# --- 2.")[0]
        self.assertIn("PITFALL_SKIP_GATE", gate)
        self.assertNotIn("continue: the model loader will surface", gate)   # fail-open comment gone

    def test_install_env_verifies_python_before_marker_fast_path(self):
        text = (ROOT / "scripts" / "install-env.ps1").read_text(encoding="utf-8")
        fast = text.split("exit 0", 1)[0]
        self.assertIn("Actual-Version", fast)
        self.assertNotIn("Write-Output '[install-env]", text)                # progress goes to Write-Host, not stdout

    def test_license_exists_and_is_packaged(self):
        self.assertTrue((ROOT / "LICENSE").exists())
        out = Path(self.tmp.name) / "skill.zip"
        p = subprocess.run([sys.executable, str(ROOT / "tools" / "package.py"), "--out", str(out)],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            self.assertIn("LICENSE", names); self.assertIn("SKILL.md", names)
            self.assertFalse([n for n in names if n.startswith("tests/") or n.startswith("docs/")])
            blob = b"".join(z.read(n) for n in names if n.endswith((".py", ".ps1", ".md", ".json", ".txt")))
        user = os.environ.get("USERNAME", "")
        if len(user) >= 3:
            self.assertNotIn(f"Users\\{user}".encode(), blob); self.assertNotIn(f"Users/{user}".encode(), blob)

    def test_skill_md_examples_are_valid_json_and_triggers_are_conjunctive(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        import re
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        self.assertGreaterEqual(len(blocks), 4)
        for b in blocks:
            json.loads(b)                                                    # no // comments, valid JSON
        fm = text.split("---")[1]
        self.assertIn("Do NOT trigger", fm)
        self.assertNotIn("server status|start|stop", text)

    def test_status_reports_version_and_models_dir_override(self):
        env = dict(self.env, PITFALL_MODELS_DIR=str(Path(self.tmp.name) / "nomodels"))
        r = self.cli_json("status", env=env)
        self.assertEqual(r["version"], "0.7.0")
        self.assertFalse(r["model_ready"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
