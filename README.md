# local-pitfall-memory · 本地踩坑记忆库

> 给 Qoder 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。
>
> A local, verified-fix-only error memory for productivity agents (Qoder / WorkBuddy / TRAE Work).
> Fingerprint lookup in < 1 s, attribution by **Qwen3-4B INT4 on OpenVINO (CPU)**, hybrid retrieval with a local **bge** embedder — nothing leaves the machine.

## Why

An agent's brain lives in the cloud and forgets between sessions. The error you fixed last Tuesday comes back on Friday in another project, and the agent re-derives it from scratch. This skill is the agent's **local hippocampus for errors**: it remembers *what happened on this machine*, and only trusts fixes that were **executed and verified**.

- **Not smarter — just remembers.** The cloud model stays the brain; this is its memory of *your* machine.
- **Verified only.** `propose → verify → commit`: a fix reaches the high-confidence tier only after the verification command exits 0.
- **Nothing leaves the machine.** History, index, embeddings and retrieval are local; every stored/returned string is redacted (keys, JWTs, emails, public IPs, username).

## How it answers

```
lookup(error) → exact   fingerprint match           → 可引用 (only if verified)
             → family  same class/package, details differ → 需谨慎
             → semantic FTS5/BM25 ⊕ local vector, RRF-fused, cross-runtime demoted → 仅联想
             → none
```

The local LLM is **never on the lookup path**. It runs once when a new pit is first recorded (structured attribution), or when you explicitly ask for it on fuzzy results.

## Quick start (Windows, Qoder CLI)

```powershell
# 1. install as a project-level skill
git clone <this repo> .qoder/skills/local-pitfall-memory

# 2. first call builds the venv and downloads the models (~2.4 GB, resumable)
.qoder\skills\local-pitfall-memory\scripts\run.ps1 status --json
.qoder\skills\local-pitfall-memory\scripts\run.ps1 --continue    # if the download timed out

# 3. use it from Qoder — it triggers on its own when you paste an error, or explicitly:
/local-pitfall-memory status
```

See `SKILL.md` for the full command table, reply schema and failure contract.

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

## Tests

```powershell
tests\test.ps1        # unit (12) · server protocol (5) · review regressions (16) · retrieval (5) · E2E through run.ps1
```

All suites run without OpenVINO (`PITFALL_FAKE_MODEL=1`). The real-model checks are `scripts\smoke_model.py` and `tests\bench_model.py` (results in `docs/bench-*.md`).

## Provenance

Cut from [Sensei](https://github.com/Claude-Ovo/sensei) — the redaction rules, the pitfalls-table format and the first entries in the database are real: the first two pits in the author's own DB were hit while building this.

Built for the ModelScope × Intel *Production AI Skills* contest (3rd edition). Slug: `local-pitfall-memory`.
