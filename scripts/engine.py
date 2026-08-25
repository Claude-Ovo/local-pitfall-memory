"""engine.py — client-side bridge to server.py (spawn-on-demand, one request per connection).

The only thing client.py calls is `attribute(error_text, context, timeout)`:
  - returns the structured attribution dict, or None on ANY failure (server down, timeout, model missing).
  - None must always be a soft path: the deterministic fingerprints already work without the model.
Standalone lifecycle: if the pipe is not answering, spawn `server.py` with the venv python and wait
up to `spawn_wait` seconds for state == running. Never blocks the caller beyond its timeout.
"""
import os, subprocess, sys, time
from multiprocessing.connection import Client
from pathlib import Path

SKILL_NAME = "local-pitfall-memory"
PIPE_ADDRESS = rf"\\.\pipe\{SKILL_NAME}"
AUTHKEY = SKILL_NAME.encode("utf-8")
HERE = Path(__file__).resolve().parent

def _send(payload: dict, timeout: float = 5.0) -> dict:
    """One request per connection. Raises on failure."""
    conn = Client(PIPE_ADDRESS, authkey=AUTHKEY)
    try:
        conn.send(payload)
        if not conn.poll(timeout):
            raise TimeoutError(f"server did not answer within {timeout}s")
        return conn.recv()
    finally:
        conn.close()

def status(timeout: float = 2.0):
    try:
        return _send({"op": "status"}, timeout)
    except Exception:
        return None

def _spawn():
    python = sys.executable
    env = dict(os.environ)
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([python, str(HERE / "server.py")], **kwargs)

def ensure_server(spawn_wait: float = 60.0):
    """Return status dict when running, else None. Spawns the server if the pipe is dead."""
    st = status()
    if st is None:
        _spawn()
    deadline = time.time() + spawn_wait
    while time.time() < deadline:
        st = status()
        if st and st.get("state") == "running":
            return st
        if st and st.get("state") == "error":
            return None
        time.sleep(0.5)
    return None

def attribute(error_text: str, context: dict, timeout: float = 45.0, spawn_wait: float = 60.0):
    """Structured attribution from the local model, or None (soft failure)."""
    try:
        if ensure_server(spawn_wait) is None:
            return None
        resp = _send({"op": "request", "kind": "attribute",
                      "error_text": error_text, "context": context or {}}, timeout)
        if resp.get("ok"):
            r = dict(resp["result"]); r["_latency_s"] = resp.get("latency_s"); return r
        return None
    except Exception:
        return None

def embed(text: str, timeout: float = 10.0, spawn_wait: float = 60.0):
    """Unit vector for `text` from the resident embedder, or None (soft failure)."""
    try:
        if ensure_server(spawn_wait) is None:
            return None
        resp = _send({"op": "request", "kind": "embed", "text": text}, timeout)
        return resp["result"] if resp.get("ok") else None
    except Exception:
        return None

def shutdown():
    try:
        return _send({"op": "shutdown"}, 5.0)
    except Exception:
        return None
