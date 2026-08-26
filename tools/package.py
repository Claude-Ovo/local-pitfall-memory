"""Build the ModelScope Skills-Center zip for local-pitfall-memory.
Rules (Skills Center): zip <= 5 MB, exactly one SKILL.md at the zip root, frontmatter complete, slug immutable.
Usage: python tools/package.py [--out dist/local-pitfall-memory-<version>.zip]
"""
import json, re, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 5 * 1024 * 1024
INCLUDE_TOP = ["SKILL.md", "info.json", "meta.json", "requirements.txt", "README.md", "LICENSE"]
# The Skills Center artifact is runtime-only. Tests and historical integration
# evidence stay in the repository; shipping them added stale output and local
# fixture paths without being needed by scripts/run.ps1.
INCLUDE_DIRS = ["scripts"]
EXCLUDE_PARTS = {".git", "__pycache__", ".pytest_cache", "collab", "corpus/tmp"}
EXCLUDE_SUFFIX = {".pyc", ".log", ".db", ".err"}

def files():
    for name in INCLUDE_TOP:
        p = ROOT / name
        assert p.exists(), f"required top-level file missing: {name}"     # a missing LICENSE is a packaging failure, not a skip
        yield p
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir() or p.suffix in EXCLUDE_SUFFIX or EXCLUDE_PARTS & set(p.relative_to(ROOT).parts):
                continue
            yield p

def check_skill_md():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    fm = m.group(1)
    assert "name: local-pitfall-memory" in fm, "frontmatter name must be the slug"
    desc = fm.split("description:", 1)[1].strip()
    assert len(desc) <= 1024, f"description is {len(desc)} chars (> 1024)"

def main():
    version = json.loads((ROOT / "meta.json").read_text(encoding="utf-8"))["version"]
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "dist" / f"local-pitfall-memory-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    check_skill_md()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files():
            z.write(p, p.relative_to(ROOT).as_posix()); n += 1
    size = out.stat().st_size
    with zipfile.ZipFile(out) as z:
        roots = [i for i in z.namelist() if i.lower() == "skill.md"]
        assert roots == ["SKILL.md"], f"expected exactly one root SKILL.md, got {roots}"
    print(f"{out}  files={n}  size={size/1024:.0f} KB  limit=5120 KB  {'OK' if size <= LIMIT else 'TOO BIG'}")
    return 0 if size <= LIMIT else 1

if __name__ == "__main__":
    sys.exit(main())
