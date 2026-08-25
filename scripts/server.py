"""server.py — long-lived attribution engine (Qwen3-4B INT4 @ OpenVINO).

Named-pipe protocol per local-ai-skill-authoring/references/architecture.md:
  status   -> {"ok":true,"state":...,"pid":...,"uptime_s":...}
  request  -> {"op":"request","kind":"attribute","error_text":...,"context":{...}}
              {"ok":true,"result":{"error_class","package","root_cause_guess","fix_hint"},"latency_s":..}
  shutdown -> {"ok":true,"state":"shutting_down"}

Design rule (MODELSCOPE-SKILL-PLAN.md §6): the model is NEVER on the lookup hot path.
It is only consulted for first-record structuring and fuzzy queries, by the client, with a timeout.

Standalone lifecycle (no server-dog): client spawns this process; it exits by itself after
`server_alive_timeout` seconds idle (info.json, default 300, -1 = never).
Env PITFALL_FAKE_MODEL=1 -> deterministic stub instead of OpenVINO (tests / CI).
"""
import json, os, sys, threading, time, traceback
from multiprocessing.connection import Listener
from pathlib import Path

SKILL_NAME = "local-pitfall-memory"
PIPE_ADDRESS = rf"\\.\pipe\{SKILL_NAME}"
AUTHKEY = SKILL_NAME.encode("utf-8")
ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = Path.home() / ".openvino" / "models" / "Qwen3-4B-int4-ov"
EMBED_DIR = Path.home() / ".openvino" / "models" / "bge-base-en-v1.5-int8-ov"
EMBED_DIM = 768
LOG = Path.home() / ".pitfall-memory" / "server.log"
FAKE = os.environ.get("PITFALL_FAKE_MODEL") == "1"

for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8")

def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass

def _idle_timeout():
    try:
        v = json.loads((ROOT / "info.json").read_text(encoding="utf-8")).get("server_alive_timeout", 300)
        return None if v == -1 else float(v)
    except Exception:
        return 300.0

PROMPT = (
    "You are an error triage engine for developer tools. Given a terminal/build/runtime error, "
    "reply ONLY with one compact JSON object with exactly these keys: "
    '{"error_class": string, "package": string, "root_cause_guess": string, "fix_hint": string}. '
    "Keep every value under 120 characters. No prose, no markdown. /no_think\n\n"
)

class Server:
    def __init__(self):
        self.state = "starting"; self.error = ""; self.started_at = time.time()
        self.last_used = time.time(); self.pipe = None; self.cfg = None
        self.embedder = None; self.embed_error = ""
        self.lock = threading.Lock()

    def init_async(self):
        threading.Thread(target=self._init, daemon=True).start()

    def _init(self):
        try:
            if FAKE:
                self.state = "running"; log("fake model ready"); return
            self.state = "loading"
            need = ["openvino_model.bin", "openvino_model.xml", "openvino_tokenizer.bin"]
            if not all((MODEL_DIR / f).exists() for f in need):
                raise FileNotFoundError(f"model missing under {MODEL_DIR}; run scripts/download_model.py")
            import openvino_genai as ov_genai
            t = time.perf_counter()
            self.pipe = ov_genai.LLMPipeline(str(MODEL_DIR), "CPU")
            cfg = ov_genai.GenerationConfig(); cfg.max_new_tokens = 128; cfg.do_sample = False
            self.cfg = cfg
            self.state = "running"
            log(f"model loaded in {time.perf_counter()-t:.1f}s")
            # embedding channel is optional: failure here never blocks the attribution engine
            try:
                if (EMBED_DIR / "openvino_model.xml").exists():
                    t = time.perf_counter()
                    ecfg = ov_genai.TextEmbeddingPipeline.Config()
                    ecfg.normalize = True; ecfg.max_length = 512
                    ecfg.pooling_type = ov_genai.TextEmbeddingPipeline.PoolingType.CLS
                    self.embedder = ov_genai.TextEmbeddingPipeline(str(EMBED_DIR), "CPU", ecfg)
                    log(f"embedder loaded in {time.perf_counter()-t:.1f}s")
                else:
                    self.embed_error = f"embedding model missing under {EMBED_DIR}"
            except Exception:
                self.embed_error = traceback.format_exc(); log(self.embed_error)
        except Exception:
            self.error = traceback.format_exc(); self.state = "error"; log(self.error)

    # ---- inference ---------------------------------------------------------
    def attribute(self, error_text: str, context: dict) -> dict:
        text = (error_text or "")[:4000]
        if FAKE:
            import re
            m = re.search(r"\b([A-Z][A-Za-z]*(?:Error|Exception)|ERR_[A-Z_]+)\b", text)
            return {"error_class": m.group(1) if m else "", "package": "",
                    "root_cause_guess": "fake-model stub", "fix_hint": "n/a"}
        runtime = context.get("runtime", "")
        prompt = PROMPT + (f"Runtime: {runtime}\n" if runtime else "") + text
        with self.lock:
            raw = str(self.pipe.generate(prompt, self.cfg))
        return _first_json(raw)

    def embed(self, text: str):
        """L2-normalized embedding of `text`, or None when the channel is unavailable."""
        text = (text or "")[:2000]
        if FAKE:
            # deterministic pseudo-embedding: hashed character trigrams → unit vector (tests only)
            import hashlib, math
            vec = [0.0] * EMBED_DIM
            toks = text.lower().split()
            for tok in toks:
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % EMBED_DIM] += 1.0
            n = math.sqrt(sum(v * v for v in vec)) or 1.0
            return [v / n for v in vec]
        if self.embedder is None:
            return None
        with self.lock:
            return [float(x) for x in self.embedder.embed_query(text)]

    # ---- protocol ----------------------------------------------------------
    def handle(self, msg: dict) -> dict:
        op = msg.get("op"); self.last_used = time.time()
        if op == "status":
            return {"ok": True, "state": self.state, "pid": os.getpid(),
                    "uptime_s": round(time.time() - self.started_at, 1), "error": self.error, "fake": FAKE,
                    "embedder": bool(self.embedder) or FAKE, "embed_error": self.embed_error[:200]}
        if op == "request":
            if self.state != "running":
                return {"ok": False, "error": f"not ready: {self.state}", "state": self.state}
            if msg.get("kind") == "embed":
                try:
                    t = time.perf_counter(); vec = self.embed(msg.get("text", ""))
                    if vec is None:
                        return {"ok": False, "error": "embedding channel unavailable"}
                    return {"ok": True, "result": vec, "latency_s": round(time.perf_counter() - t, 3)}
                except Exception as exc:
                    log(traceback.format_exc()); return {"ok": False, "error": str(exc)}
            if msg.get("kind") != "attribute":
                return {"ok": False, "error": f"unknown kind: {msg.get('kind')}"}
            try:
                t = time.perf_counter()
                res = self.attribute(msg.get("error_text", ""), msg.get("context") or {})
                return {"ok": True, "result": res, "latency_s": round(time.perf_counter() - t, 2)}
            except Exception as exc:
                log(traceback.format_exc()); return {"ok": False, "error": str(exc)}
        if op == "shutdown":
            return {"ok": True, "state": "shutting_down"}
        return {"ok": False, "error": f"unknown op: {op}"}

def _first_json(raw: str) -> dict:
    keys = ("error_class", "package", "root_cause_guess", "fix_hint")
    for start in [i for i, ch in enumerate(raw) if ch == "{"]:
        end = raw.find("}", start)
        while end != -1:
            try:
                j = json.loads(raw[start:end + 1])
                return {k: str(j.get(k, ""))[:200] for k in keys}
            except Exception:
                end = raw.find("}", end + 1)
    return {k: "" for k in keys} | {"root_cause_guess": "unparseable model output"}

def main() -> int:
    srv = Server(); srv.init_async()
    idle = _idle_timeout()
    log(f"listening on {PIPE_ADDRESS} idle_timeout={idle} fake={FAKE}")
    with Listener(PIPE_ADDRESS, authkey=AUTHKEY) as listener:
        # watchdog: exit when idle too long (standalone replacement for server-dog keepalive)
        def watchdog():
            while True:
                time.sleep(5)
                if idle is not None and time.time() - srv.last_used > idle:
                    log("idle timeout -> exit"); os._exit(0)
        threading.Thread(target=watchdog, daemon=True).start()
        while True:
            with listener.accept() as conn:
                try:
                    msg = conn.recv()
                except Exception:
                    continue
                resp = srv.handle(msg if isinstance(msg, dict) else {})
                try:
                    conn.send(resp)
                except Exception:
                    pass
                if isinstance(msg, dict) and msg.get("op") == "shutdown":
                    log("shutdown requested"); return 0

if __name__ == "__main__":
    sys.exit(main())
