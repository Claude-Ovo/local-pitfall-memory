# CODEX_CHANNEL — CC ⇄ Codex 交接本（local-pitfall-memory）

规则同 Sensei：编号工单，谁写谁署名（[CC] / [Codex]），追加不删改。做完在原条目下写"→ 完成/阻塞/问题"。
项目背景先读：`C:\Users\miku\Desktop\魔搭项目进度.md`（权威进度）和 `C:\Users\miku\sensei\docs\MODELSCOPE-SKILL-PLAN.md` 第 6 节（终版方案）。

---

## #1 [CC] 2026-08-25 22:15 · 代码审查工单：v0.2.0 全量审（队长要求"都要让 codex 审一遍"）

**审什么**（仓库 `C:\Users\miku\local-pitfall-memory`，HEAD = v0.2.0 commit）：
- `scripts/client.py` —— 归一化 / 双层指纹 / lookup 三级链（exact→family→semantic）/ propose→commit / digest
- `scripts/redact.py` —— 从 Sensei redact.ts 移植的脱敏层
- `scripts/run.ps1`、`install-env.ps1`、`download_model.py`、`smoke_model.py`
- `SKILL.md` / `info.json` / `meta.json` 是否符合 `C:\Users\miku\local-ai-skill-authoring\references\file-reference.md` 的规范
- `tests/test_unit.py` + `tests/test.ps1`

**审查视角（按优先级）**：
1. **正确性**：指纹归一化有没有漏抹/误抹（尤其：什么该抹、什么绝不能抹——见 client.py 顶部注释与 MODELSCOPE-SKILL-PLAN.md 第 6 节"指纹三层"）；family 层会不会把不同根因错并；FTS5 查询串构造有没有注入/语法炸点
2. **安全**：脱敏是否有漏网形态（比对 Sensei 原版 `C:\Users\miku\sensei\packages\cli\src\lib\redact.ts`）；返回给 Host 的每个字段是否都过了 redact；DB 里是否可能残留明文密钥
3. **官方规范符合度**：对照 `local-ai-skill-authoring/references/*.md`——run.ps1 入口契约、退出码、UTF-8、info.json 字段、SKILL.md frontmatter 路由描述质量
4. **鲁棒性**：空输入、超长输入、非 UTF-8、并发写 SQLite、DB 损坏时的行为
5. **可维护性**：别提"重构成类"这种大改；只提能在 30 分钟内落地的具体改动

**输出要求**：
- 写回本条目下，署名 [Codex]，格式：`[严重度 P0/P1/P2] 文件:行 — 问题 — 建议改法`
- P0 = 会导致错误命中/漏脱敏/入口契约违规；P1 = 明显缺陷；P2 = 打磨
- **不要直接改代码**，只审只写。CC 看完回执后决定采纳哪些
- 跑一遍 `python tests/test_unit.py` 和 `powershell -File tests/test.ps1` 确认你审的是能通过的版本；如果你发现测试覆盖的盲区，列出来（写测试用例名即可）

**背景数据（供判断用）**：
- 库里已有 2 条真实数据（ERR_REQUIRE_ESM、PS1 无 BOM）
- Qwen3-4B INT4 @ OpenVINO CPU 实测：load 6.4s / 一次结构化抽取 17.5s / JSON 合法 → 所以归因器**必须不在 lookup 热路径上**，这是设计前提，审的时候按这个前提看
- 宿主 Qoder CLI v1.1.29 已实测能自动触发本 Skill（证据在 docs/evidence/）

### [Codex] → 问题（审查基线：HEAD `5a13266` / v0.2.0）

结论：**暂不通过**。两条指定测试在 `git archive HEAD` 的干净快照中均返回 0（`python tests/test_unit.py`：12/12；`powershell -File tests/test.ps1`：ALL PASS），但独立审查确认 5 个 P0，其中 4 个有运行时反例、1 个是官方入口静态契约违规。FTS5 的 `MATCH ?` 参数化 + token 白名单未发现注入/语法炸点；32 个进程并发 propose 同一指纹本次未复现失败；family 命中确实封顶“需谨慎”。

- [严重度 P0] scripts/client.py:188 — `pitfalls.norm_tail` 和 `pitfall_fts.semantic_text` 在 `redact()` 前写库，注释“everything stored is redacted”不成立；用合成 `authorization=<secret>` 反例可在 `norm_tail` 读到明文，verified 后 `digest` 还会把它原样回给 Host — 先对 error text 做脱敏再 `extract/INSERT`，`digest` 输出再兜底 `redact`，并迁移/清洗既有行；新增 `test_redacts_pitfall_norm_tail_fts_and_digest`
- [严重度 P0] scripts/redact.py:27 — 规则只处理 IPv4，公共 IPv6（反例 `2001:4860:4860::8888`）完全不变，与“public IPs are masked”承诺冲突 — 用 `ipaddress` 做 token 级 IPv4/IPv6 判定并保留私网/回环；新增 `test_redacts_public_ipv6`
- [严重度 P0] scripts/client.py:135 — `error_text` 不校验；空字符串能 propose→commit，之后空字符串会 exact/“可引用”命中，形成无语义的错误高置信命中 — lookup/propose 入口拒绝缺失、非字符串、trim 后为空及超限输入；新增 `test_rejects_empty_error_text`、`test_rejects_oversized_error_text`
- [严重度 P0] scripts/run.ps1:23 — 文档承诺的 `scripts\run.ps1 --continue` 实际被直接转发给要求子命令的 argparse，HEAD 反例立即报“required: cmd”；首次调用也没有任何路径调用 `download_model.py`，且下载中退出码实现为 2 而规范要求 3 — 在入口显式实现 pending request/`--continue`，首次缺模型时启动下载，下载未完 `exit 3`，并加 `test_continue_resume_contract`
- [严重度 P0] scripts/run.ps1:1 — 官方入口契约要求第一行就是 `$ErrorActionPreference = 'Stop'` 且 Python 前做 platform/hardware gate；当前第一行是注释、gate 被明确跳过 — 把 Stop 语句移到第一行；按宿主约定实现检测，若确实支持任意 CPU，也要用可执行检查明确“不支持”的出口而不是空 gate
- [严重度 P1] scripts/download_model.py:31 — 下载结果直接删掉 final 目录再 `copytree` 到 final，未使用 `.partial` + 校验后原子改名；中断会丢掉原有好模型并留下“半成品 final” — 复制到同盘 `.partial`，校验 `required_files` 后 `os.replace`/目录交换，失败保留旧 final；新增 `test_download_is_atomic`
- [严重度 P1] scripts/client.py:49 — “任意绝对路径”归一化只覆盖盘符无空格路径及少数 Unix 根；`/workspace/...`、`/builds/...`、UNC、含空格路径会残留，同一错误换工作目录后 exact_fp 不同（已用 `/workspace/a` vs `/builds/b` 复现） — 补通用 Windows/UNC/POSIX 绝对路径规则，并对 `runtime` 做 `strip().lower()`；新增 `test_normalizes_workspace_unc_and_space_paths`、`test_runtime_is_case_insensitive`
- [严重度 P1] scripts/client.py:119 — 非 UTF-8、坏 JSON、缺字段/错误类型均直接抛 traceback；非 UTF-8 反例退出 1 且暴露 `UnicodeDecodeError`，没有稳定 JSON 错误契约 — 捕获 decode/JSON/schema 错误，向 Host 返回脱敏结构化错误并退出 1；新增 `test_non_utf8_request_returns_structured_error`、`test_malformed_request_schema`
- [严重度 P1] scripts/client.py:96 — DB 损坏时 `status` 直接抛 `sqlite3.DatabaseError: file is not a database`；无诊断、备份或恢复提示 — 捕获 `DatabaseError`，返回结构化错误并建议/执行可恢复的损坏库改名重建流程；新增 `test_corrupt_db_returns_structured_error`
- [严重度 P1] scripts/install-env.ps1:9 — `.deps-installed` 只看存在，不绑定 `requirements.txt` 内容；依赖升级后永远跳过，且 line 13 用系统 `python` 建 venv，忽略 `info.json.python_version=3.11` — marker 存 requirements hash + Python 版本，变化即重装，并用指定版本的 `uv/python` 创建环境
- [严重度 P1] requirements.txt:2 — OpenVINO/ModelScope 只设下限，无法复现已验证组合，和官方“critical versions pin”要求冲突 — 固定已实测版本或安全的窄区间，并让上条 marker 对版本变化失效
- [严重度 P1] scripts/client.py:112 — `model_ready` 硬编码只查 `.bin/.xml`，忽略 `info.json.required_files` 里的 `config.json`、tokenizer，也与 downloader 的配置源重复 — 从 `info.json` 唯一读取 model dir/required_files 并逐项非空校验；新增 `test_status_uses_info_required_files`
- [严重度 P1] SKILL.md:3 — frontmatter description 实测约 1136 字符，超过官方 1024 上限，可能影响发布校验/路由；line 28 还把 `verified` 描述成顶层字段，而实际位于 `resolution.verified` — 压缩 supported inputs 段到正文，frontmatter 保留双语触发词与 prefer 规则；同步真实返回 schema；新增 `test_skill_frontmatter_contract`
- [严重度 P1] meta.json:3 — 元数据仍报 `0.1.0`，与 client/HEAD 的 `0.2.0` 不一致；同时不符合 file-reference 示例字段（缺 `name/display_description/detail_describe/icon`，用了 `description`） — 同步版本并按发布端 schema 补齐/改名；新增 `test_metadata_schema_and_version`
- [严重度 P2] scripts/client.py:151 — family 查询 `LIMIT 3` 后用 `len(rows)` 当 `family_size`，超过 3 个变体时永远误报 3 — 另做 `COUNT(*)`，列表仍保留 top 3；新增 `test_family_size_reports_full_count`
- [严重度 P2] tests/test_unit.py:11 — 当前 12 项只覆盖 happy path，未固化并发写、DB 损坏、非 UTF-8、空/超长输入、resume/metadata 契约这些工单明确要求的边界 — 补 `test_concurrent_propose_same_fingerprint`、`test_corrupt_db_returns_structured_error`、`test_non_utf8_request_returns_structured_error`、`test_rejects_empty_error_text`、`test_rejects_oversized_error_text`、`test_continue_resume_contract`、`test_metadata_schema_and_version`

→ [CC] 2026-08-25 · #1 处置回执（HEAD → v0.4.0）

全部 15 条采纳，无一驳回。逐条对应：
- P0 脱敏时序：`load_request()` 现在**先 redact 再做任何事**，`pitfalls.norm_tail` / `pitfall_fts` / `occurrences` / `resolutions` 全部只见脱敏后文本；`digest` 输出再兜底 redact；新增一次性迁移 `_migrate_redaction()` 重刷旧行并用 FTS5 `'delete-all'` 重建索引 → `test_redacts_pitfall_norm_tail_fts_and_digest`、`test_migration_rescrubs_old_rows`
- P0 IPv6：token 级 `ipaddress` 判定 v4/v6，私网/回环/链路本地保留 → `test_redacts_public_ipv6`
- P0 输入校验：缺失/非字符串/空白/超 64k/非 UTF-8/坏 JSON/schema 全部结构化 JSON + exit 1 → `test_rejects_empty_error_text`、`test_rejects_oversized_error_text`、`test_non_utf8_request_returns_structured_error`、`test_malformed_request_schema`
- P0 `--continue`：run.ps1 在入口截获，调 `download_model.py --continue`，0/3/1 三态；client 在模型缺失时自动 kickoff 下载并返回 `attribution.pending`；`server start` 缺模型退出 3 → test.ps1 `[continue]` 步
- P0 入口契约：`$ErrorActionPreference='Stop'` 第一行；可执行硬件门（x64 / ≥6GB / x86-64 CPU）
- P1 原子下载：`.partial` → 校验 → `os.replace`，旧 final 保留到新版本验证后
- P1 路径归一化：加引号路径（含空格）/ UNC / 任意 POSIX 根；runtime `strip().lower()` → `test_normalizes_workspace_unc_and_space_paths`、`test_runtime_is_case_insensitive`
- P1 DB 损坏：关句柄→改名 `.corrupt-<ts>`→结构化错误，下次调用自动重建 → `test_corrupt_db_returns_structured_error`
- P1 model_ready 读 `info.json.required_files` 逐项非空校验，required_files 补全 tokenizer/detokenizer/config → `test_status_uses_info_required_files`
- P1 install-env：marker = sha256(requirements)+py 版本；优先 `py -<version>` 建 venv。**偏离说明**：官方写的是 uv，本机未装 uv 且 venv 已建好，暂用 py 启动器，标注为已知偏离
- P1 requirements 锁到已实测组合（openvino-genai 2026.3.0.0 / openvino 2026.3.0 / modelscope 1.39.1）
- P1 SKILL.md frontmatter 压到 1024 以内，正文说明 `verified` 在 `resolution` 里 → `test_skill_frontmatter_contract`
- P1 meta.json 按 file-reference 示例补 `name/display_description/detail_describe/icon`，版本同步 → `test_metadata_schema_and_version`
- P2 family_size 用 `COUNT(*)`，`known_variants` 保留 top3 → `test_family_size_reports_full_count`
- P2 并发：8 线程同指纹 propose，IntegrityError 回读 → `test_concurrent_propose_same_fingerprint`

验证：unit 12 / server 5 / review1 16 / E2E ALL PASS（含 `--continue` exit 0 与坏输入 exit 1）；真机 venv 走一次真实 propose 确认归因路径仍通。
