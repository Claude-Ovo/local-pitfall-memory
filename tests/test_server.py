"""Server/engine tests with the fake model (no OpenVINO needed). Run: python tests/test_server.py"""
import os, subprocess, sys, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["PITFALL_FAKE_MODEL"] = "1"
import engine  # noqa: E402


class TestServerProtocol(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.shutdown(); time.sleep(0.5)          # in case a real one is up
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "server.py")],
                                    env=dict(os.environ, PITFALL_FAKE_MODEL="1"))
        deadline = time.time() + 15
        while time.time() < deadline:
            st = engine.status()
            if st and st.get("state") == "running":
                return
            time.sleep(0.3)
        raise RuntimeError("fake server did not come up")

    @classmethod
    def tearDownClass(cls):
        engine.shutdown(); time.sleep(0.5)
        if cls.proc.poll() is None:
            cls.proc.kill()

    def test_status(self):
        st = engine.status()
        self.assertTrue(st["ok"]); self.assertEqual(st["state"], "running"); self.assertTrue(st["fake"])
        self.assertIn("pid", st); self.assertIn("uptime_s", st)

    def test_attribute(self):
        r = engine.attribute("Error [ERR_REQUIRE_ESM]: require() of ES Module x not supported", {"runtime": "node"})
        self.assertIsNotNone(r)
        self.assertEqual(r["error_class"], "ERR_REQUIRE_ESM")
        for k in ("package", "root_cause_guess", "fix_hint", "_latency_s"):
            self.assertIn(k, r)

    def test_unknown_op_and_kind(self):
        self.assertFalse(engine._send({"op": "nope"})["ok"])
        self.assertFalse(engine._send({"op": "request", "kind": "translate"})["ok"])

    def test_one_request_per_connection_is_fine_repeatedly(self):
        for _ in range(5):
            self.assertEqual(engine.status()["state"], "running")


class TestEngineSoftFailure(unittest.TestCase):
    def test_returns_none_when_no_server_and_no_spawn(self):
        engine.shutdown(); time.sleep(0.5)
        # spawn_wait=0 → do not wait for a server → soft None, never raises
        self.assertIsNone(engine.attribute("x", {}, timeout=1, spawn_wait=0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
