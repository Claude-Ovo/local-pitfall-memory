---
name: local-pitfall-memory
description: |
  Local pitfall memory for build/runtime errors on THIS machine (本地踩坑记忆库：报错先查本机已验证修复，验证后沉淀入库，历史与检索不出机). Use when an error signal (报错/traceback/stderr/exit code/npm/pip/cargo/build failure) is combined with a HISTORY or MEMORY intent: 这个报错见过吗/以前怎么修的/查踩坑库/记录这次修复/沉淀/归因/生成踩坑文档, "have we seen this error", "how did we fix this before", "look up / log / commit / remember this fix", "digest pitfalls", "known issue on this machine". Do NOT trigger for a fresh error the user just wants fixed now with no reference to history, for general log analysis, or for offline/local-model questions unrelated to error memory. Prefer this skill over re-deriving a fix whenever the same error may have occurred on this machine before.
---

# Local Pitfall Memory Skill Guide

给 Agent 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。

Supported inputs: raw error text (compiler errors, stack traces, npm/pip/cargo failures, HTTP errors, exit codes);
fix records (the command/change that fixed it + how it was verified); digest requests.

## Usage

`scripts\run.ps1` is the only supported interface. Every command prints exactly one JSON line on stdout when `--json` is given.

| Intent | Command |
| --- | --- |
| 查坑（this error seen before?） | `scripts\run.ps1 lookup --request-file request.json --json` |
| 查坑 + 让本地 LLM 归因（慢，10–25 s） | `scripts\run.ps1 lookup --request-file request.json --json --attribute` |
| 预沉淀（fix proposed, not yet verified） | `scripts\run.ps1 propose --request-file fix.json --json` |
| 验证通过后入库 | `scripts\run.ps1 commit --id <proposal-id> --verify-exit-code 0 --json` |
| 汇编踩坑文档（explicit export） | `scripts\run.ps1 digest --out pitfalls.md --json` |
| 健康检查 | `scripts\run.ps1 status --json` |
| 归因引擎：查状态 | `scripts\run.ps1 server status --json` |
| 归因引擎：启动 | `scripts\run.ps1 server start --json` (exit 3 while the model is still downloading) |
| 归因引擎：停止 | `scripts\run.ps1 server stop --json` |
| 下载/续传模型 | `scripts\run.ps1 --continue` (exit 0 ready · 3 still pending · 1 error) |

`request.json`: `{"error_text": "<full error output>", "context": {"cwd": "...", "runtime": "node|python|..."}}`
`fix.json`: same plus `"root_cause"`, `"fix_command"`, `"verify_method"` (all three non-empty).
Multi-line error text MUST go through `--request-file`, never inline shell quotes.

## Replies (one JSON object per call)

`lookup` — exact hit (fingerprint identical):

```json
{"hit": "exact", "confidence": "可引用", "pitfall_id": 12, "times_seen": 3, "last_seen": 1787500000,
 "resolution": {"root_cause": "package.json lacks type=module", "fix_command": "npm pkg set type=module",
                "verify_method": "node server.js exits 0", "verified": true}}
```

`lookup` — family hit (same class/package, details differ) adds `family_size` and `known_variants`; semantic hit adds a `retrieval` block:

```json
{"hit": "semantic", "confidence": "仅联想", "pitfall_id": 7,
 "resolution": {"root_cause": "...", "fix_command": "...", "verify_method": "...", "verified": true},
 "retrieval": {"fused_score": 0.0325, "fts_rank": 1, "vec_rank": 2, "channels": ["fts5", "vector"],
               "mode": "hybrid", "env_compatible": true}}
```

`lookup` — nothing known: `{"hit": "none", "confidence": null, "note": "no local history; solve fresh then propose+commit"}`.
With `--attribute`, semantic/none replies also carry `"attribution": {"error_class": "...", "package": "...", "root_cause_guess": "...", "fix_hint": "..."}`
or `"attribution": {"pending": true, "note": "..."}` while the model is still downloading.

`propose` / `commit` / `status`:

```json
{"ok": true, "pitfall_id": 12, "proposal_id": 5, "state": "proposed (unverified) — run commit after the fix is verified"}
```

```json
{"ok": true, "resolution_id": 5, "state": "verified"}
```

```json
{"ok": true, "db": "~\\.pitfall-memory\\pitfalls.db", "pitfalls": 2, "occurrences": 3, "resolutions": 2,
 "verified_resolutions": 2, "embeddings": 2, "fts5": true, "model": "Qwen3-4B-int4-ov", "model_ready": true,
 "embedding_model": "bge-base-en-v1.5-int8-ov", "embedding_ready": true, "retrieval_mode": "hybrid",
 "redaction": "on", "version": "0.7.0"}
```

`digest --out` → `{"ok": true, "out": "pitfalls.md", "verified_entries": 2}`; without `--out` the Markdown table is printed instead.
`server status` → `{"ok": true, "state": "running", "pid": 1234, "version": "0.7.0", "embedder": true}` (or `{"ok": false, "state": "down"}`, exit 1).
`--continue` → `{"ok": true, "state": "ready", "models": [...], "network": "modelscope.cn"}` or `{"ok": false, "state": "pending", "exit_code": 3, "note": "..."}`.

- 命中层级 `hit`: exact（指纹全同）/ family（同类错误，细节不同）/ semantic（语义相近：FTS5/BM25 与本地向量两路各自排名后 RRF 融合，不同 runtime 的记录降权）/ none
- 置信 `confidence`: **可引用** = exact 且 `resolution.verified=true`；**需谨慎** = exact 未验证或 family；**仅联想** = semantic
- Only 可引用 results may be applied without re-verification. `resolution.verified` is inside `resolution`, not top-level.

## Failure contract

Every detectable failure is one JSON line `{"ok": false, "error": "<code>", "message": "..."}` on stdout.

| exit | `error` codes | meaning |
| --- | --- | --- |
| 1 | `platform_unsupported`, `platform_probe_failed` | entry gate: not x64 / < 6 GB RAM / memory could not be probed (`PITFALL_SKIP_GATE=1` bypasses) |
| 1 | `env_install_failed` | venv / pinned-dependency install failed (needs Python 3.13 and PyPI on first run) |
| 1 | `bad_arguments` | unknown command or missing option |
| 1 | `request_unreadable`, `request_not_utf8`, `request_bad_json`, `request_schema`, `request_empty`, `request_too_large` | request file problems |
| 1 | `path_not_local`, `output_unwritable`, `io_error` | `PITFALL_DB` / `--out` on a UNC or network drive, `--out` is a directory or unwritable |
| 1 | `db_corrupt`, `db_error`, `no_such_proposal`, `empty_resolution`, `internal_error` | database problems (a corrupt DB is moved aside, never deleted) / unexpected exception (class name only) |
| 1 | `model_download_failed` (`--continue`) | downloader hit a hard error |
| 3 | state `downloading` / `pending` | required model not yet on disk — rerun `scripts\run.ps1 --continue` |

## Local model policy

- Fingerprint lookup (exact/family) is deterministic and fast (< 1 s). No model is on that path.
- The **attribution LLM** (Qwen3-4B INT4) runs only on the first record of a new pit in `propose` (~15–25 s on CPU; skip with `--no-model`) and on fuzzy results with `lookup --attribute`.
- The **embedding model** (bge-base, optional) runs on the semantic path (`propose` first record, semantic `lookup`); if it is missing or down, retrieval degrades to `fts-only` — never fails.
- Any model failure/timeout is soft. The engine stays resident and exits after `server_alive_timeout` seconds idle (info.json).

## Important

- Do not call other scripts directly; `scripts\run.ps1` is the interface.
- `status` does not download models. A model-backed `propose`, `lookup --attribute`, or `server start` may start the required model download (~2.3 GB); run `scripts\run.ps1 --continue` to finish or resume it.
- Unsupported platform (non-x64 / < 6 GB RAM) → `platform_unsupported`, exit 1. ISA-level requirements (e.g. AVX2) are checked by the OpenVINO CPU plugin at load time and surface through `server status` as a short redacted `error`.
- The optional embedding model (`bge-base-en-v1.5-int8-ov`) only affects `retrieval_mode` (`hybrid` vs `fts-only`); it never blocks `--continue`.

## Privacy contract (precise wording)

- **Error text, history, index and retrieval never leave this machine.** No cloud fallback exists in the code.
- **Bootstrap is the only network use**: the first run installs pinned wheels from PyPI and `--continue` fetches the OpenVINO models from ModelScope. Nothing from the request or the database is sent in either case.
- **Everything stored and everything returned is redacted** (keys/JWT/emails/public IPs/username/home path), including the diagnostics log `~\.pitfall-memory\server.log`.
- The database is refused on UNC/network drives (`path_not_local`). The one documented way redacted content leaves the DB file is the explicit, user-requested `digest --out <local file>`.

## What this skill does NOT do

- It does not fix bugs itself and does not replace the agent's reasoning — it serves this machine's verified history.
- It does not send error text, code, or history anywhere off this machine.
