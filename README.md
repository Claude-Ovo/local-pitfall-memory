# local-pitfall-memory · 本地踩坑记忆库

> 给 Qoder 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。
>
> A local, verified-fix-only error memory for productivity agents (Qoder / WorkBuddy / TRAE Work).
> Fingerprint lookup in < 1 s, attribution by **Qwen3-4B INT4 on OpenVINO (CPU)**, hybrid retrieval with a local **bge** embedder — error text and history never leave the machine.

## Why

An agent's brain lives in the cloud and forgets between sessions. The error you fixed last Tuesday comes back on Friday in another project, and the agent re-derives it from scratch. This skill is the agent's **local hippocampus for errors**: it remembers *what happened on this machine*, and only trusts fixes that were **executed and verified**.

- **Not smarter — just remembers.** The cloud model stays the brain; this is its memory of *your* machine.
- **Verified only.** `propose → verify → commit`: a fix reaches the high-confidence tier only after the verification command exits 0.
- **History never leaves the machine.** Error text, index, embeddings and retrieval are local; every stored/returned string — and the diagnostics log — is redacted (keys, JWTs, emails, public IPs, username). The only network use is bootstrap: pinned wheels from PyPI on first run, models from ModelScope via `--continue`. See *Limitations* for the exact boundary.

## How it answers

```
lookup(error) → exact   fingerprint match           → 可引用 (only if verified)
             → family  same class/package, details differ → 需谨慎
             → semantic FTS5/BM25 ⊕ local vector, RRF-fused, cross-runtime demoted → 仅联想
             → none
```

The local LLM is **never on the lookup path**. It runs once when a new pit is first recorded (structured attribution), or when you explicitly ask for it on fuzzy results.

## Quick start (Windows, Qoder CLI)

Prerequisites: 64-bit Windows on x86-64, ≥ 6 GB RAM, **Python 3.13** (`py -3.13` or `python` on PATH), and network access to PyPI + ModelScope for the first run only.

```powershell
# 1. install as a project-level skill — from git…
git clone https://github.com/Claude-Ovo/local-pitfall-memory .qoder/skills/local-pitfall-memory
#    …or from the Skills Center zip (unzip so that .qoder/skills/local-pitfall-memory/SKILL.md exists)

# 2. first call builds the venv (PyPI, a few minutes); status only reports model readiness
powershell -ExecutionPolicy Bypass -File .qoder\skills\local-pitfall-memory\scripts\run.ps1 status --json
powershell -ExecutionPolicy Bypass -File .qoder\skills\local-pitfall-memory\scripts\run.ps1 --continue   # models from ModelScope (~2.4 GB, resumable)

# 3. use it from Qoder — it triggers on its own when you paste an error and ask whether it was seen before, or explicitly:
/local-pitfall-memory status
```

`-ExecutionPolicy Bypass` is only needed when the host's policy blocks unsigned scripts; Qoder/WorkBuddy/TRAE Work call `scripts\run.ps1` directly. See `SKILL.md` for the full command table, reply schema and failure contract.

## Architecture

```
Qoder / TRAE Work (agent brain)
   │ Skill call
   ▼
scripts/run.ps1  ──►  client.py (short-lived)
                       ├─ redact → normalize → fingerprints (exact / family)
                       ├─ SQLite: pitfalls / occurrences / resolutions / embeddings + FTS5
                       └─ named pipe ──► server.py (resident, exits when idle)
                                          ├─ Qwen3-4B-int4-ov  @ OpenVINO CPU   (attribution)
                                          └─ bge-base-en-v1.5-int8-ov          (embeddings, 768-d)
```

Measured on a desktop CPU (no GPU/NPU): model load 6 s · attribution 15–17 s · embedding 0.11 s · hybrid lookup 0.4 s · exact/family lookup < 0.1 s.

Trust boundary: everything inside the dashed box is local and redacted; the only arrows that cross it are **bootstrap downloads** (PyPI wheels on first run, ModelScope models on `--continue`) and the **explicit `digest --out` export** the user asks for.

```
┌ this machine ───────────────────────────────────────────────────────────────┐
│  Host (Qoder) ──run.ps1──► client.py ──► SQLite/FTS5 ──pipe──► server.py    │
│                  ▲ redacted JSON only          ▲ redacted rows   ▲ redacted log │
└──────────────────┼─────────────────────────────┼─────────────────┼────────────┘
   first run only: PyPI wheels  ·  --continue only: ModelScope models  ·  digest --out: user-requested local file
```

## Tests

```powershell
tests\test.ps1        # unit (12) · server protocol (5) · review regressions (16+16+12) · retrieval (5) · E2E through run.ps1
```

All suites run without OpenVINO (`PITFALL_FAKE_MODEL=1`) and without the network (the E2E `--continue` step uses an empty isolated models dir with `PITFALL_OFFLINE=1`). The real-model checks are `scripts\smoke_model.py` and `tests\bench_model.py` (results in `docs/bench-*.md`).

## Limitations

- **Platform**: 64-bit Windows on x86-64 with ≥ 6 GB RAM; the entry gate is fail-closed (`platform_probe_failed` if memory cannot be probed; `PITFALL_SKIP_GATE=1` bypasses). macOS/Linux are not supported (named pipes, PowerShell entry).
- **Python 3.13** must be installed; the venv is recreated if a different minor version is found.
- **First run needs the network**: pinned wheels from PyPI, then ~2.4 GB of models from ModelScope. After that the skill is fully offline; `PITFALL_OFFLINE=1` forbids any network use (missing models then report `pending`).
- **Resources**: the resident server holds ~5 GB RSS while loaded; attribution takes 15–25 s on a desktop CPU and is single-threaded (a second request waits). It exits after `server_alive_timeout` seconds idle.
- **Degradation**: without the optional embedder, retrieval is `fts-only`; without the LLM, `propose` records the pit with `attribution.pending`.
- **Redaction is rule-based** (keys, JWTs, emails, public IPs, username, home path, credential-in-URL). It is not a general DLP; unusual secret formats can pass through. Everything stored, returned and logged goes through it.
- **Data lives in `~\.pitfall-memory\pitfalls.db`** (override `PITFALL_DB`, local drives only). Back it up like any file; a corrupt DB is moved aside, never deleted. There is no sync between machines by design.
- **Explicit export**: `digest --out <file>` is the one documented way redacted content leaves the DB file, and only to a local path.

## Provenance

Cut from [Sensei](https://github.com/Claude-Ovo/sensei) — the redaction rules, the pitfalls-table format and the first entries in the database are real: the first two pits in the author's own DB were hit while building this.

Built for the ModelScope × Intel *Production AI Skills* contest (3rd edition). Slug: `local-pitfall-memory`.
