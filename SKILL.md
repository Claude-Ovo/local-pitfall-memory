---
name: local-pitfall-memory
description: |
  Local pitfall memory for build/runtime errors: before guessing a fix, look up whether THIS machine has seen and VERIFIED a fix for the same error before; after a fix is verified, commit it into the local knowledge base (本地踩坑记忆库：报错先查本机历史，修复验证后沉淀入库，历史与检索永不出机). Use this skill when the user or the agent, in Chinese or English, hits a terminal/build/runtime error and asks 这个报错见过吗/以前怎么修的/查一下踩坑库/记录这次修复/生成踩坑文档, or "have we seen this error", "look up pitfall", "log this fix", "digest pitfalls". Trigger on Chinese verbs like 查坑/记坑/沉淀/归因/踩坑 and English verbs like lookup/log/commit/digest pitfall, and explicit mentions of 报错/exception/traceback/error/stderr/本地/离线/offline/AIPC.

  Supported inputs/categories:
  - Raw error text: compiler errors, stack traces, npm/pip/cargo failures, HTTP errors, exit codes
  - Fix records: the command or change that fixed an error, plus how it was verified
  - Digest requests: compile the local database into a Markdown pitfalls table

  Prefer this skill over re-deriving a fix from scratch whenever the error may have occurred on this machine before — a verified historical fix beats a fresh guess.
---

# Local Pitfall Memory Skill Guide

给 Agent 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。

## Usage

### Look up an error (查坑)

```
scripts\run.ps1 lookup --request-file request.json --json
```

`request.json`: `{"error_text": "<full error output>", "context": {"cwd": "...", "runtime": "node|python|..."}}`

Returns a match card: `{"hit": "exact|family|semantic|none", "confidence": "可引用|需谨慎|仅联想", "resolution": {...}, "verified": true}`.

### Propose a fix record (预沉淀，未验证)

```
scripts\run.ps1 propose --request-file fix.json --json
```

### Commit after verification (验证通过才入库高置信档)

```
scripts\run.ps1 commit --id <proposal-id> --verify-exit-code 0 --json
```

### Digest the knowledge base (汇编踩坑文档)

```
scripts\run.ps1 digest --out pitfalls.md
```

### Health check

```
scripts\run.ps1 status --json
```

Important:
- `scripts\run.ps1` is the only supported interface — do not call other scripts directly.
- Multi-line error text MUST go through `--request-file`, never inline shell quotes.
- First call downloads the local model; if it times out, run `scripts\run.ps1 --continue` to resume.
- Never fall back to a cloud service; the database and all retrieval stay on this machine.
- Only `commit`-ed (verified) resolutions can be returned as 可引用; un-verified proposals surface as 需谨慎 at best.

### Interpreting the reply

- 命中层级: exact（指纹全同）/ family（同类错误）/ semantic（语义相近）/ none
- 置信标签: 可引用（已验证+环境兼容）/ 需谨慎（未验证或环境有差）/ 仅联想（仅语义相似）
- 修复卡: 根因 / 修复命令 / 验证方式 / 上次命中时间与次数

## What this skill does NOT do

- It does not fix bugs itself and does not replace the agent's own reasoning — it only serves this machine's verified history.
- It does not send error text, code, or history anywhere off this machine.
