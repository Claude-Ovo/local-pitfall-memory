---
name: local-pitfall-memory
description: |
  Local pitfall memory for build/runtime errors: before guessing a fix, look up whether THIS machine has a VERIFIED fix for the same error; after a fix is verified, commit it to the local knowledge base (本地踩坑记忆库：报错先查本机已验证修复，验证后沉淀入库，历史与检索不出机). Use when the user or agent, in Chinese or English, hits a terminal/build/runtime error and asks 这个报错见过吗/以前怎么修的/查踩坑库/记录这次修复/生成踩坑文档, or "have we seen this error", "look up pitfall", "log this fix", "digest pitfalls". Trigger on 查坑/记坑/沉淀/归因/踩坑 and lookup/log/commit/digest pitfall, plus 报错/exception/traceback/stderr/本地/离线/offline/AIPC. Prefer this skill over re-deriving a fix from scratch whenever the error may have occurred on this machine before.
---

# Local Pitfall Memory Skill Guide

给 Agent 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。

Supported inputs: raw error text (compiler errors, stack traces, npm/pip/cargo failures, HTTP errors, exit codes);
fix records (the command/change that fixed it + how it was verified); digest requests.

## Usage

`scripts\run.ps1` is the only supported interface.

| Intent | Command |
| --- | --- |
| 查坑（this error seen before?） | `scripts\run.ps1 lookup --request-file request.json --json` |
| 查坑 + 让本地模型归因（慢） | `scripts\run.ps1 lookup --request-file request.json --json --attribute` |
| 预沉淀（fix proposed, not yet verified） | `scripts\run.ps1 propose --request-file fix.json --json` |
| 验证通过后入库 | `scripts\run.ps1 commit --id <proposal-id> --verify-exit-code 0 --json` |
| 汇编踩坑文档 | `scripts\run.ps1 digest --out pitfalls.md` |
| 健康检查 | `scripts\run.ps1 status --json` |
| 归因引擎 | `scripts\run.ps1 server status|start|stop --json` |
| 续传模型下载 | `scripts\run.ps1 --continue` |

`request.json`: `{"error_text": "<full error output>", "context": {"cwd": "...", "runtime": "node|python|..."}}`
`fix.json`: same plus `"root_cause"`, `"fix_command"`, `"verify_method"`.
Multi-line error text MUST go through `--request-file`, never inline shell quotes.

## Interpreting the reply

```
{"hit": "exact|family|semantic|none", "confidence": "可引用|需谨慎|仅联想|null",
 "pitfall_id": 12, "times_seen": 3, "last_seen": 1787500000,
 "resolution": {"root_cause": "...", "fix_command": "...", "verify_method": "...", "verified": true},
 "family_size": 2, "known_variants": ["..."],          // family hits only
 "retrieval": {"fused_score": 0.03, "fts_rank": 1, "vec_rank": 2, "channels": ["fts5","vector"], "env_compatible": true},  // semantic hits only
 "attribution": {"error_class": "...", "package": "...", "root_cause_guess": "...", "fix_hint": "..."}  // only with --attribute / first propose
}
```

- 命中层级 `hit`: exact（指纹全同）/ family（同类错误，细节不同）/ semantic（语义相近：FTS5/BM25 与本地向量两路各自排名后 RRF 融合，不同 runtime 的记录降权）/ none
- 置信 `confidence`: **可引用** = exact 且 `resolution.verified=true`；**需谨慎** = exact 未验证或 family；**仅联想** = semantic
- Only 可引用 results may be applied without re-verification. `resolution.verified` is inside `resolution`, not top-level.
- `attribution.pending=true` means the local model is still downloading — run `scripts\run.ps1 --continue`.

## Failure handling

- Errors are structured JSON on stdout with exit code 1: `{"ok": false, "error": "<code>", "message": "..."}`
  codes: `request_not_utf8`, `request_bad_json`, `request_schema`, `request_empty`, `request_too_large`, `db_corrupt`, `db_error`
- A corrupt database is moved aside (never deleted) and a fresh one is created on the next call.
- Exit code 3 = model download pending; rerun `scripts\run.ps1 --continue`.

## Local model policy

- Fingerprint lookup is deterministic and fast (< 1 s). The model is **never** on that path.
- The model runs only on the first record of a new pit in `propose` (~15–25 s on CPU; skip with `--no-model`) and on fuzzy results with `lookup --attribute`. Any model failure/timeout is soft.
- The engine stays resident and exits after `server_alive_timeout` seconds idle (info.json).

## Important

- Do not call other scripts directly; `scripts\run.ps1` is the interface.
- First call downloads the model (~2.3 GB); if it times out, run `scripts\run.ps1 --continue`.
- Unsupported platform (non-x64 / < 6 GB RAM) prints an error and exits with code 1. ISA-level requirements (e.g. AVX2) are checked by the OpenVINO CPU plugin at load time and surface as a structured `server` error, not by the entry script.
- The optional embedding model (`bge-base-en-v1.5-int8-ov`) only affects `retrieval_mode` (`hybrid` vs `fts-only`); it never blocks `--continue`.
- Never fall back to a cloud service; the database, index and retrieval stay on this machine.
- Everything stored and everything returned is redacted (keys/JWT/emails/public IPs/username).

## What this skill does NOT do

- It does not fix bugs itself and does not replace the agent's reasoning — it serves this machine's verified history.
- It does not send error text, code, or history anywhere off this machine.
