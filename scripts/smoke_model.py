"""Smoke-load the OpenVINO IR model and time one short structured generation on CPU."""
import io, json, sys, time
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MODEL_DIR = Path.home() / ".openvino" / "models" / "Qwen3-4B-int4-ov"
import openvino_genai as ov_genai  # noqa: E402

t0 = time.perf_counter()
pipe = ov_genai.LLMPipeline(str(MODEL_DIR), "CPU")
t_load = time.perf_counter() - t0

prompt = (
    "You are an error triage engine. Given a terminal error, reply ONLY with compact JSON "
    '{"error_class": str, "package": str, "root_cause_guess": str}. No prose. /no_think\n\n'
    "Error [ERR_REQUIRE_ESM]: require() of ES Module /p/dist/main.js from /p/bootstrap.js not supported.\n"
    "    at Object.<anonymous> (/p/bootstrap.js:11:22)"
)
cfg = ov_genai.GenerationConfig()
cfg.max_new_tokens = 96
cfg.do_sample = False

t1 = time.perf_counter()
out = pipe.generate(prompt, cfg)
t_gen = time.perf_counter() - t1
text = str(out).strip()
# Qwen3 may wrap thinking; keep the first JSON object we can parse
j = None
for start in [i for i, ch in enumerate(text) if ch == "{"]:
    try:
        j = json.loads(text[start:text.rindex("}") + 1]); break
    except Exception:
        continue
print(json.dumps({"load_s": round(t_load, 2), "gen_s": round(t_gen, 2),
                  "raw_len": len(text), "json_ok": j is not None, "parsed": j}, ensure_ascii=False))
print("RAW:", text[:400])
