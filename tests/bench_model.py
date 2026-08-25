"""Benchmark the resident attribution engine on the real-error corpus (the 30-log check from MODELSCOPE-SKILL-PLAN §6).
Reports: JSON validity rate, error_class agreement with the deterministic extractor, cold/warm latency p50/p95, peak RSS.
Usage (venv python, real model):  python tests/bench_model.py [n=30] [--out docs/bench-YYYYMMDD.md]
"""
import io, json, os, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import client, engine    # noqa: E402  (client reconfigures stdout to UTF-8; don't wrap again)

CORPUS_FILES = [ROOT / "tests" / "corpus" / "real_errors.jsonl", ROOT / "tests" / "corpus" / "curated_errors.jsonl"]

def peak_rss_mb(pid):
    try:
        import ctypes, ctypes.wintypes as wt
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        ctypes.windll.kernel32.CloseHandle(h)
        return round(pmc.PeakWorkingSetSize / 1024 / 1024)
    except Exception:
        return None

def pct(xs, p):
    xs = sorted(xs); k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return xs[k]

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    items = []
    for f in CORPUS_FILES:
        if f.exists():
            items += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    items = items[:n]
    print(f"corpus: {sum(1 for i in items if i['source'] != 'curated')} mined + {sum(1 for i in items if i['source'] == 'curated')} curated = {len(items)}")
    engine.shutdown(); time.sleep(1)
    t0 = time.perf_counter(); st = engine.ensure_server(spawn_wait=120); cold = time.perf_counter() - t0
    if not st:
        print("server failed to start"); return 1
    print(f"server up in {cold:.1f}s (pid {st['pid']}, fake={st.get('fake')}, embedder={st.get('embedder')})")
    rows, lat, json_ok, agree = [], [], 0, 0
    for it in items:
        feat = client.extract(it["error_text"], it["runtime"])
        t = time.perf_counter()
        r = engine.attribute(it["error_text"], {"runtime": it["runtime"]}, timeout=120)
        dt = time.perf_counter() - t; lat.append(dt)
        ok = bool(r) and not r.get("root_cause_guess", "").startswith("unparseable")
        json_ok += ok
        ec_model = (r or {}).get("error_class", "")
        a = bool(feat["error_class"]) and ec_model and (feat["error_class"].lower() in ec_model.lower() or ec_model.lower() in feat["error_class"].lower())
        agree += bool(a)
        rows.append((it["id"], f"{dt:.1f}", "yes" if ok else "no", feat["error_class"] or "-", ec_model or "-", (r or {}).get("root_cause_guess", "")[:60]))
        print(f"  {it['id']:28} {dt:5.1f}s json={'ok' if ok else 'BAD'} det={feat['error_class'] or '-':22} model={ec_model or '-'}")
    pid = st["pid"]; rss = peak_rss_mb(pid)
    summary = {"n": len(items), "cold_start_s": round(cold, 1), "json_valid_rate": round(json_ok / len(items), 3),
               "error_class_agreement": round(agree / len(items), 3),
               "latency_p50_s": round(pct(lat, 50), 1), "latency_p95_s": round(pct(lat, 95), 1),
               "latency_first_s": round(lat[0], 1), "peak_rss_mb": rss, "model": client.INFO["models"][0]["dir_name"]}
    print(json.dumps(summary, ensure_ascii=False))
    if out:
        md = ["# Attribution engine benchmark", "", f"`{summary['model']}` @ OpenVINO CPU, {len(items)} real error blocks mined from Sensei sessions", "",
              "| metric | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summary.items()] + \
             ["", "| id | s | json | det class | model class | root cause guess |", "|---|---|---|---|---|---|"] + \
             ["| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |" for r in rows]
        Path(out).write_text("\n".join(md) + "\n", encoding="utf-8"); print("wrote", out)
    engine.shutdown()
    return 0

if __name__ == "__main__":
    sys.exit(main())
