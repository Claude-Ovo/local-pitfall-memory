"""engine.py — client-side bridge to server.py (spawn-on-demand, one request per connection).

Guarantees (codex review #2):
  * every call is SOFT: returns None on any failure, never raises to the caller
  * every call is BOUNDED by a single monotonic deadline that covers spawn + pipe connect + reply
  * cold start is SINGLE-FLIGHT across processes (atomic lock file with owner PID)
  * a server whose version/mode does not match this client is shut down and restarted
"""
import hashlib, json, os, subprocess, sys, threading, time
from multiprocessing.connection import Client
from pathlib import Path

SKILL_NAME = "local-pitfall-memory"
PIPE_ADDRESS = rf"\\.\pipe\{SKILL_NAME}"
AUTHKEY = SKILL_NAME.encode("utf-8")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAKE = os.environ.get("PITFALL_FAKE_MODEL") == "1"
LOCK = Path.home() / ".pitfall-memory" / "server.spawn.lock"
_child = None                       # keep the Popen handle so the child is observable/collectable

def script_hash() -> str:
    h = hashlib.sha256()
    for name in ("server.py", "engine.py"):
        h.update((HERE / name).read_bytes())
    return h.hexdigest()[:12]

def _pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h); return True
    except Exception:
        return False

# ---- bounded pipe I/O ---------------------------------------------------------
def _send(payload: dict, timeout: float = 5.0) -> dict:
    """One request per connection, whole round-trip (connect+auth+send+recv) bounded by `timeout`."""
    box = {}
    def work():
        try:
            conn = Client(PIPE_ADDRESS, authkey=AUTHKEY)
            try:
                conn.send(payload)
                box["resp"] = conn.recv()
            finally:
                conn.close()
        except BaseException as exc:            # noqa: BLE001 — carried back to the caller
            box["exc"] = exc
    t = threading.Thread(target=work, daemon=True); t.start(); t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"server did not answer within {timeout}s")
    if "exc" in box:
        raise box["exc"]
    return box["resp"]

def status(timeout: float = 2.0):
    try:
        return _send({"op": "status"}, timeout)
    except Exception:
        return None

# ---- lifecycle ----------------------------------------------------------------
def _spawn():
    global _child
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": dict(os.environ)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    _child = subprocess.Popen([sys.executable, str(HERE / "server.py")], **kwargs)
    return _child.pid

def _try_lock():
    """Atomic single-flight lock; returns True if this process owns the spawn. Stale (dead PID) locks reclaimed."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd); return True
        except FileExistsError:
            try:
                pid = int(LOCK.read_text().strip() or 0)
            except Exception:
                pid = 0
            if pid and _pid_alive(pid) and time.time() - LOCK.stat().st_mtime < 180:
                return False
            LOCK.unlink(missing_ok=True)
    return False

def _unlock():
    try:
        if int(LOCK.read_text().strip() or 0) == os.getpid():
            LOCK.unlink(missing_ok=True)
    except Exception:
        pass

def _compatible(st: dict) -> bool:
    return st.get("fake", False) == FAKE and st.get("script_hash") == script_hash()

def ensure_server(spawn_wait: float = 60.0):
    """Return a running, compatible server's status, else None. spawn_wait=0 → never spawn."""
    deadline = time.monotonic() + max(spawn_wait, 0)
    st = status()
    if st and st.get("state") == "running" and _compatible(st):
        return st
    if st and not _compatible(st):
        shutdown()                                   # stale version / wrong mode: replace it
        time.sleep(0.5); st = None
    if spawn_wait <= 0:
        return None
    if st is None:
        if _try_lock():
            try:
                _spawn()
            finally:
                pass                                   # lock released once the server answers (below) or goes stale
    try:
        while time.monotonic() < deadline:
            st = status()
            if st and st.get("state") == "running" and _compatible(st):
                return st
            if st and st.get("state") == "error":
                return None
            time.sleep(0.5)
        return None
    finally:
        _unlock()

def attribute(error_text: str, context: dict, timeout: float = 45.0, spawn_wait: float = 60.0):
    """Structured attribution from the local model, or None (soft failure). Total time ≤ spawn_wait + timeout."""
    try:
        if ensure_server(spawn_wait) is None:
            return None
        resp = _send({"op": "request", "kind": "attribute", "error_text": error_text, "context": context or {}}, timeout)
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
