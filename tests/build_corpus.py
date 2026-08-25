"""Mine real error blocks from Sensei session logs (~/.sensei/sessions/*.jsonl) into tests/corpus/real_errors.jsonl.
Each corpus line: {"id", "source", "runtime", "error_text"} — error_text is REDACTED at extraction time.
Usage: python tests/build_corpus.py [max_per_session=12]
"""
import io, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from redact import redact          # noqa: E402
import client                      # noqa: E402

# (client already reconfigures stdout to UTF-8 on import — wrapping again closes the buffer)
SESS = Path.home() / ".sensei" / "sessions"
OUT = ROOT / "tests" / "corpus" / "real_errors.jsonl"
START_RE = re.compile(r"(Traceback \(most recent call last\)|\b[A-Z][A-Za-z]*(?:Error|Exception)\b|ERR_[A-Z_]+|npm ERR!|error TS\d{4}|\bENOENT\b|\bEADDRINUSE\b|panicked at|fatal:|SyntaxError)")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")

def blocks_from(text):
    lines = [l.rstrip() for l in ANSI.sub("", text).splitlines()]
    i, out = 0, []
    while i < len(lines):
        head = lines[i].lstrip()
        noisy = head.startswith(("$", "[sensei]", "|", ">", "+ CategoryInfo", "executing", "throw new"))
        if START_RE.search(lines[i]) and not noisy:
            j = i + 1
            while j < len(lines) and j - i < 25 and lines[j].strip() and not lines[j].lstrip().startswith(("$", "PS ", ">")):
                j += 1
            blk = "\n".join(l for l in lines[i:j] if l.strip())
            if len(blk) >= 25:
                out.append(blk)
            i = j
        else:
            i += 1
    return out

def main():
    max_per = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    OUT.parent.mkdir(parents=True, exist_ok=True)
    seen, corpus = set(), []
    for f in sorted(SESS.glob("*.jsonl")):
        if f.name.startswith("replay-"):
            continue
        n = 0
        text_buf, runtime = [], ""
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                c = json.loads(line)
            except Exception:
                continue
            if c.get("kind") == "in":
                cmd = str(c.get("text", ""))
                if re.search(r"\b(node|npm|npx|pnpm|tsc)\b", cmd): runtime = "node"
                elif re.search(r"\b(python|pip|py)\b", cmd): runtime = "python"
            if c.get("kind") == "out":
                text_buf.append(str(c.get("text", "")))
        for blk in blocks_from("\n".join(text_buf)):
            red = redact(blk)
            fp = client.extract(red, runtime)["exact_fp"]
            if fp in seen:
                continue
            seen.add(fp); n += 1
            corpus.append({"id": f"{f.stem}-{n:02d}", "source": f.name, "runtime": runtime, "error_text": red[:4000]})
            if n >= max_per:
                break
    with OUT.open("w", encoding="utf-8") as fh:
        for c in corpus:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"{len(corpus)} unique error blocks -> {OUT}")
    for c in corpus[:40]:
        first = c["error_text"].splitlines()[0][:90]
        print(f"  {c['id']:28} {c['runtime']:7} {first}")

if __name__ == "__main__":
    main()
