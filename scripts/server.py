"""server.py — long-lived attribution + embedding engine (Qwen3-4B INT4 / bge-base @ OpenVINO CPU).

Named-pipe protocol per local-ai-skill-authoring/references/architecture.md:
  status   -> {"ok":true,"state":...,"pid":...,"uptime_s":...,"version":...,"script_hash":...,"fake":...,"error":<short, redacted>}
  request  -> kind=attribute {"error_text","context"} → {"ok":true,"result":{...},"latency_s"}
              kind=embed     {"text"}                 → {"ok":true,"result":[...],"latency_s"}
  shutdown -> {"ok":true,"state":"shutting_down"}
Full tracebacks go to ~/.pitfall-memory/server.log only; the protocol carries a short redacted summary (codex #2 P0).
Model dirs / required files come from info.json (single config source). Env PITFALL_FAKE_MODEL=1 → deterministic stubs.
"""
import json, os, sys, threading, time, traceback
from multiprocessing.connection import Listener
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

SKILL_NAME = "local-pitfall-memory"
PIPE_ADDRESS = rf"\\.\pipe\{SKILL_NAME}"
AUTHKEY = SKILL_NAME.encode("utf-8")
ROOT = Path(__file__).resolve().parent.parent
INFO = json.loads((ROOT / "info.json").read_text(encoding="utf-8"))
MODELS_DIR = Path.home() / ".openvino" / "models"
LOG = Path.home() / ".pitfall-memory" / "server.log"
FAKE = os.environ.get("PITFALL_FAKE_MODEL") == "1"
VERSION = "0.6.0"
FAKE_DIM = 768

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

def _spec(role):
    return next((m for m in INFO["models"] if m.get("role") == role), None)

def _model_dir(m):
    return MODELS_DIR / m["dir_name"]

def _complete(m):
    d = _model_dir(m)
    return d.exists() and all((d / f).exists() and (d / f).stat().st_size > 0 for f in m["required_files"])

def _script_hash():
    import hashlib
    h = hashlib.sha256()
    for name in ("server.py", "engine.py"):
        h.update((Path(__file__).resolve().parent / name).read_bytes())
    return h.hexdigest()[:12]

def _idle_timeout():
    v = INFO.get("server_alive_timeout", 300)
    return None if v == -1 else float(v)

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
        self.embedder = None; self.embed_error = ""; self.embed_dim = None
        self.lock = threading.Lock()

    def init_async(self):
        threading.Thread(target=self._init, daemon=True).start()

    def _init(self):
        try:
            if FAKE:
                self.embed_dim = FAKE_DIM; self.state = "running"; log("fake model ready"); return
            self.state = "loading"
            main = _spec("attribution")
            if main is None or not _complete(main):
                raise FileNotFoundError("attribution model incomplete; run scripts\\run.ps1 --continue")
            import openvino_genai as ov_genai
            t = time.perf_counter()
            self.pipe = ov_genai.LLMPipeline(str(_model_dir(main)), "CPU")
            cfg = ov_genai.GenerationConfig(); cfg.max_new_tokens = 128; cfg.do_sample = False
            self.cfg = cfg
            self.state = "running"
            log(f"model loaded in {time.perf_counter()-t:.1f}s")
            emb = _spec("embedding")
            try:
                if emb and _complete(emb):
                    t = time.perf_counter()
                    ecfg = ov_genai.TextEmbeddingPipeline.Config()
                    ecfg.normalize = True; ecfg.max_length = 512
                    ecfg.pooling_type = ov_genai.TextEmbeddingPipeline.PoolingType.CLS
                    self.embedder = ov_genai.TextEmbeddingPipeline(str(_model_dir(emb)), "CPU", ecfg)
                    self.embed_dim = len(self.embedder.embed_query("probe"))
                    log(f"embedder loaded in {time.perf_counter()-t:.1f}s dim={self.embed_dim}")
                else:
                    self.embed_error = "embedding model not available (optional)"
            except Exception:
                log(traceback.format_exc()); self.embed_error = "embedder failed to load (see server.log)"
        except Exception as exc:
            log(traceback.format_exc())
            self.error = redact(str(exc).splitlines()[0][:200]) if str(exc) else exc.__class__.__name__
            self.state = "error"

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
        text = (text or "")[:2000]
        if FAKE:
            import hashlib, math
            vec = [0.0] * FAKE_DIM
            for tok in text.lower().split():
                vec[int(hashlib.md5(tok.encode()).hexdigest(), 16) % FAKE_DIM] += 1.0
            n = math.sqrt(sum(v * v for v in vec)) or 1.0
            return [v / n for v in vec]
        if self.embedder is None:
            return None
        with self.lock:
            return [float(x) for x in self.embedder.embed_query(text)]

    def handle(self, msg: dict) -> dict:
        op = msg.get("op"); self.last_used = time.time()
        if op == "status":
            return {"ok": True, "state": self.state, "pid": os.getpid(), "uptime_s": round(time.time() - self.started_at, 1),
                    "version": VERSION, "script_hash": _script_hash(), "fake": FAKE,
                    "embedder": bool(self.embedder) or FAKE, "embed_dim": self.embed_dim,
                    "embed_error": self.embed_error, "error": self.error}
        if op == "request":
            if self.state != "running":
                return {"ok": False, "error": f"not ready: {self.state}", "state": self.state}
            kind = msg.get("kind")
            try:
                t = time.perf_counter()
                if kind == "embed":
                    vec = self.embed(msg.get("text", ""))
                    if vec is None:
                        return {"ok": False, "error": "embedding channel unavailable"}
                    return {"ok": True, "result": vec, "latency_s": round(time.perf_counter() - t, 3)}
                if kind == "attribute":
                    res = self.attribute(msg.get("error_text", ""), msg.get("context") or {})
                    return {"ok": True, "result": res, "latency_s": round(time.perf_counter() - t, 2)}
                return {"ok": False, "error": f"unknown kind: {kind}"}
            except Exception as exc:
                log(traceback.format_exc()); return {"ok": False, "error": redact(exc.__class__.__name__ + ": " + str(exc)[:120])}
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
    try:
        listener = Listener(PIPE_ADDRESS, authkey=AUTHKEY)
    except Exception as exc:
        log(f"pipe busy/unavailable: {exc}"); return 2        # another server owns the pipe: exit quietly
    log(f"listening on {PIPE_ADDRESS} idle_timeout={idle} fake={FAKE} v{VERSION}")
    with listener:
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
