#!/usr/bin/env python3
"""Download the OpenVINO IR model listed in info.json from ModelScope, with resume + validation.
Usage: python download_model.py [--continue]
Exit codes: 0 ok, 2 model incomplete (rerun with --continue), 1 other error.
"""
import io, json, os, shutil, sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"

def model_ok(m):
    d = MODELS_DIR / m["dir_name"]
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

def main():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for m in INFO["models"]:
        dst = MODELS_DIR / m["dir_name"]
        if model_ok(m):
            print(f"[ok] {m['dir_name']} already complete at {dst}")
            continue
        print(f"[download] {m['model_id']} -> {dst}")
        try:
            from modelscope import snapshot_download
        except ImportError:
            print("modelscope not installed; run install-env.ps1 first"); return 1
        # snapshot_download resumes partial files by itself (cache dir), so --continue is just "run again"
        src = Path(snapshot_download(m["model_id"]))
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        if not model_ok(m):
            print(f"[incomplete] required files missing in {dst}; rerun with --continue"); return 2
        total = sum(p.stat().st_size for p in dst.iterdir() if p.is_file())
        print(f"[ok] {m['dir_name']} ready, {total/1e9:.2f} GB")
    return 0

if __name__ == "__main__":
    sys.exit(main())
