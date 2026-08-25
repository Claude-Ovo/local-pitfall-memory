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
