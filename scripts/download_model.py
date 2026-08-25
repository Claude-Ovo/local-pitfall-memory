#!/usr/bin/env python3
"""Download the OpenVINO IR model(s) listed in info.json from ModelScope — resumable, atomic, single-flight.

Layout:  ~/.openvino/models/<dir_name>            final (only ever complete)
         ~/.openvino/models/<dir_name>.partial    in-progress copy (same volume → atomic os.replace)
         ~/.openvino/models/<dir_name>.lock       exclusive lock (holds PID); removed in finally
Exit codes (entry-script contract): 0 ready · 3 REQUIRED model incomplete (rerun with --continue) · 1 error
Optional models (info.json "required": false) never affect the exit code; their state is reported by `status`.
"""
import io, json, os, shutil, sys, time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"

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

def _fetch(m):
    """Returns 0 ready, 3 incomplete, 1 error. Never touches an existing complete final dir until replacement."""
    final = MODELS_DIR / m["dir_name"]
    partial = MODELS_DIR / (m["dir_name"] + ".partial")
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("modelscope not installed; run scripts/install-env.ps1 first"); return 1
    print(f"[download] {m['model_id']} -> {final}")
    try:
        src = Path(snapshot_download(m["model_id"]))   # resumes its own cache on rerun
    except Exception as exc:
        print(f"[incomplete] download interrupted: {exc}; rerun scripts\\run.ps1 --continue"); return 3
    if partial.exists():
        shutil.rmtree(partial)
    shutil.copytree(src, partial)
    if not model_ok(m, partial):
        print(f"[incomplete] required files missing in {partial}; rerun scripts\\run.ps1 --continue"); return 3
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
    total = sum(p.stat().st_size for p in final.iterdir() if p.is_file())
    print(f"[ok] {m['dir_name']} ready, {total/1e9:.2f} GB")
    return 0

def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    worst = 0
    for m in INFO["models"]:
        required = m.get("required", True)
        if model_ok(m):
            print(f"[ok] {m['dir_name']} already complete"); continue
        try:
            with Lock(MODELS_DIR / (m["dir_name"] + ".lock")):
                rc = _fetch(m)
        except RuntimeError as exc:
            print(f"[busy] {exc}"); rc = 3
        except Exception as exc:
            print(f"[error] {m['dir_name']}: {exc}"); rc = 1
        if required:
            if rc == 1:
                return 1
            worst = max(worst, rc)
        elif rc != 0:
            print(f"[optional] {m['dir_name']} not available (rc={rc}); skill runs without it")
    return worst

if __name__ == "__main__":
    sys.exit(main())
