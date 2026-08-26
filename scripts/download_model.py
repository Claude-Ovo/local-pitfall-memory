#!/usr/bin/env python3
"""Download the OpenVINO IR model(s) listed in info.json from ModelScope — resumable, atomic, single-flight.

Layout:  <models_dir>/<dir_name>            final (only ever complete)
         <models_dir>/<dir_name>.partial    in-progress copy (same volume → atomic os.replace)
         <models_dir>/<dir_name>.lock       exclusive lock (holds PID); removed in finally
         models_dir = %PITFALL_MODELS_DIR% or ~/.openvino/models
Exit codes (entry-script contract): 0 ready · 3 REQUIRED model incomplete (rerun with --continue) · 1 error
Optional models (info.json "required": false) never affect the exit code; their state is reported by `status`.

Host contract (codex #3 P0): stdout is exactly ONE JSON line, every string redacted, e.g.
  {"ok": true, "state": "ready", "models": [...], "network": "modelscope.cn"}
  {"ok": false, "state": "pending", "exit_code": 3, "note": "rerun scripts\\run.ps1 --continue", ...}
PITFALL_OFFLINE=1 → never import modelscope / touch the network; missing models report "pending" (exit 3).
"""
import io, json, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path(os.environ.get("PITFALL_MODELS_DIR") or (Path.home() / ".openvino" / "models"))
OFFLINE = os.environ.get("PITFALL_OFFLINE") == "1"
NETWORK_HOST = "modelscope.cn"

def model_ok(m, d=None):
    d = d or (MODELS_DIR / m["dir_name"])
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

def _pid_alive(pid):
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h); return True
    except Exception:
        return False

class Lock:
    """Atomic exclusive lock file holding the owner PID; stale locks (dead PID) are reclaimed."""
    def __init__(self, path: Path):
        self.path = path; self.fd = None
    def __enter__(self):
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode()); return self
            except FileExistsError:
                try:
                    pid = int(self.path.read_text().strip() or 0)
                except Exception:
                    pid = 0
                if pid and _pid_alive(pid):
                    raise RuntimeError(f"another downloader (pid {pid}) is running")
                self.path.unlink(missing_ok=True)      # stale
        raise RuntimeError("could not acquire download lock")
    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)

def _fetch(m, report):
    """Returns 0 ready, 3 incomplete, 1 error. Never touches an existing complete final dir until replacement."""
    final = MODELS_DIR / m["dir_name"]
    partial = MODELS_DIR / (m["dir_name"] + ".partial")
    if OFFLINE:
        report["note"] = "PITFALL_OFFLINE=1: network disabled, model not downloaded"; return 3
    try:
        from modelscope import snapshot_download
    except ImportError:
        report["note"] = "modelscope not installed; run scripts\\install-env.ps1 first"; return 1
    report["source"] = m["model_id"]
    try:
        src = Path(snapshot_download(m["model_id"]))   # resumes its own cache on rerun
    except Exception as exc:
        report["note"] = f"download interrupted: {exc.__class__.__name__}: {str(exc)[:160]}"; return 3
    if partial.exists():
        shutil.rmtree(partial)
    shutil.copytree(src, partial)
    if not model_ok(m, partial):
        report["note"] = "required files missing after download"; return 3
    old = MODELS_DIR / (m["dir_name"] + ".old")
    if old.exists():
        shutil.rmtree(old)
    if final.exists():
        os.replace(final, old)
    try:
        os.replace(partial, final)
    except OSError:
        if old.exists() and not final.exists():
            os.replace(old, final)                      # roll back: keep the previous good model
        raise
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    report["size_gb"] = round(sum(p.stat().st_size for p in final.iterdir() if p.is_file()) / 1e9, 2)
    return 0

def _deep_redact(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: _deep_redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_redact(v) for v in obj]
    return obj

def run():
    """Returns (exit_code, report_dict). No printing here (testable)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    worst = 0
    models = []
    for m in INFO["models"]:
        required = m.get("required", True)
        rep = {"model": m["dir_name"], "required": required, "state": "ready"}
        if model_ok(m):
            models.append(rep); continue
        try:
            with Lock(MODELS_DIR / (m["dir_name"] + ".lock")):
                rc = _fetch(m, rep)
        except RuntimeError as exc:
            rep["note"] = f"busy: {exc}"; rc = 3
        except Exception as exc:
            rep["note"] = f"{exc.__class__.__name__}: {str(exc)[:160]}"; rc = 1
        rep["state"] = {0: "ready", 3: "pending", 1: "error"}[rc]
        models.append(rep)
        if required:
            if rc == 1:
                worst = 1; break
            worst = max(worst, rc)
        # optional models never affect the exit code
    state = {0: "ready", 3: "pending", 1: "error"}[worst]
    report = {"ok": worst == 0, "state": state, "exit_code": worst, "models": models,
              "models_dir": str(MODELS_DIR), "network": "none" if OFFLINE else NETWORK_HOST}
    if worst == 3:
        report["note"] = "required model incomplete; rerun scripts\\run.ps1 --continue"
    elif worst == 1:
        report["error"] = "model_download_failed"
    return worst, _deep_redact(report)

def main():
    rc, report = run()
    print(json.dumps(report, ensure_ascii=False))
    return rc

if __name__ == "__main__":
    sys.exit(main())
