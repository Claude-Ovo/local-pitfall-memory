#!/usr/bin/env python3
"""Download the OpenVINO IR model(s) listed in info.json from ModelScope — resumable and atomic.

Layout:  ~/.openvino/models/<dir_name>            final (only ever complete)
         ~/.openvino/models/<dir_name>.partial    in-progress copy (same volume → atomic os.replace)
Exit codes (entry-script contract): 0 ready · 3 incomplete/pending (rerun with --continue) · 1 error
"""
import io, json, os, shutil, sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"

def model_ok(m, d=None):
    d = d or (MODELS_DIR / m["dir_name"])
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

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
    # atomic swap: keep the old final until the new one is verified
    old = MODELS_DIR / (m["dir_name"] + ".old")
    if old.exists():
        shutil.rmtree(old)
    if final.exists():
        os.replace(final, old)
    os.replace(partial, final)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    total = sum(p.stat().st_size for p in final.iterdir() if p.is_file())
    print(f"[ok] {m['dir_name']} ready, {total/1e9:.2f} GB")
    return 0

def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    worst = 0
    for m in INFO["models"]:
        if model_ok(m):
            print(f"[ok] {m['dir_name']} already complete"); continue
        rc = _fetch(m)
        worst = max(worst, rc) if rc != 1 else 1
        if rc == 1:
            return 1
    return worst

if __name__ == "__main__":
    sys.exit(main())
