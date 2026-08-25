# 给 Qoder 装一块本地海马体：同一个报错，为什么每次都从头猜？

> 魔搭研习社 · Intel AI PC 专题 · 作品：`local-pitfall-memory`（本地踩坑记忆库）
> 草稿 v0.1（2026-08-25），基准数字与截图待 8/28 冻结后填入。**定位铁律：全文不出现"教学/教程"；不卖智商，卖记性；隐私口径只承诺"历史库、索引、检索不出机"。**

## 0. 一句话

给 Qoder 装一块长在本机、只记住已验证修复的踩坑记忆：历史库与检索不出机，同一个坑不再从头猜。

## 1. 场景：AI 不笨，它只是失忆

上周二你在项目 A 里撞上 `ERR_REQUIRE_ESM`，Qoder 帮你查了十几分钟，最后一句 `npm pkg set type=module` 解决。
周五你在项目 B 里换了盘符、换了行号，同一个错又来了。Qoder 的大脑在云上，每个会话都是新的——它不知道**这台机器**上周刚修过这个坑，于是从头再猜一遍。

这不是模型不够聪明的问题，是它没有"这台机器的记性"。而且这段记性没法上云：报错里带着路径、包名、有时还有密钥。

**这个 Skill 做的事只有一件：让 Qoder 记住这台机器上验证过的修复，下次同一个坑 0.4 秒命中。**

（截图 1：Qoder 里粘一段异项目的报错，只说"这错见过吗"，它自己调 Skill、返回 exact/可引用 + 修复命令 + "要不要我执行"）

## 2. 它怎么工作

### 2.1 三级命中 + 三档置信

```
lookup(报错) → exact   指纹全同                       → 可引用（仅当修复已验证）
            → family  同错误类/同包，细节不同           → 需谨慎
            → semantic FTS5/BM25 ⊕ 本地向量，RRF 融合，跨 runtime 降权 → 仅联想
            → none
```

- **exact 指纹**：runtime + 错误类 + 归一化后的消息行 + 1–3 个稳定栈帧 + 包名。归一化抹掉路径/行号/时间戳/哈希/PID/内存地址，**保留** HTTP 状态码、errno、编译器错误码、依赖版本、退出码——这些是根因的一部分，抹了就分不清坑。
- **family 指纹**：错误类 + 包 + 消息模板（引号里的常量抹成 `<S>`）。`TypeError: reading 'foo'` 和 `reading 'bar'` 是一家人，但不是同一个坑，所以最高只给"需谨慎"。
- **semantic**：FTS5（trigram + BM25）和本地 bge 向量各排一次名，倒数秩融合（RRF, k=60）；记录在别的 runtime 下的命中降权不隐藏。

### 2.2 只记验证过的修复：propose → verify → commit

修复建议先 `propose`（未验证），验证命令退出 0 后 `commit`，才进高置信档。**幻觉进不了"可引用"**——这是和"集体记忆平台"最根本的区别：我们不存"有人说过的修法"，只存"在这台机器上跑通过的修法"。

（截图 2：propose 后 lookup 显示 需谨慎；commit 后同一查询变 可引用）

### 2.3 本地模型在哪、不在哪

- **不在查询热路径上。** exact/family/semantic 全是确定性 + 本地索引，< 1 秒。
- 只在两处出场：新坑**首次入库**时做一次结构化归因（错误类/包/根因猜测/修复提示，存进库）；模糊结果上用户显式 `--attribute`。
- 归因器：`OpenVINO/Qwen3-4B-int4-ov`，`openvino_genai.LLMPipeline` CPU 常驻；嵌入器：`OpenVINO/bge-base-en-v1.5-int8-ov`，`TextEmbeddingPipeline`（CLS 池化 + 归一化，768 维）。两者都由一个常驻 `server.py` 托管，named-pipe 协议，空闲自退出。

### 2.4 隐私：历史库、索引、检索不出机

入库前先脱敏（API key / JWT / 私钥 / Bearer / 邮箱 / 公网 IPv4+IPv6 / 用户名 / home 路径），再归一化、再指纹、再存；返回给 Host 的修复卡再脱一次。**Qoder 的大脑在云上，我们不替它承诺"全程不出机"；我们承诺的是这块记性不出机。**

## 3. 工具使用：官方参考包 + OpenVINO

按 `openvino-dev-samples/local-ai-skill-authoring` 的契约实现：`scripts/run.ps1` 固定入口（第一行 `Stop`、可执行硬件门、`--continue` 续传、exit 3 = 下载未完）、`install-env.ps1`（marker 绑 requirements hash）、`client.py` 短命 + `server.py` 常驻、`info.json` 声明模型与 `required_files`、原子下载（`.partial → os.replace`）。

**实测（桌面 CPU，无 GPU/NPU）**：模型加载 6 s · 单次归因 15–17 s · 嵌入 0.11 s · 混合查询 0.4 s · exact/family < 0.1 s
**基准（26 条真实/精选报错）**：JSON 合法率 __ · 错误类一致率 __ · p50 __ s · p95 __ s · 峰值 RSS __ MB（待填：`docs/bench-2026-08-25.md`）
**为什么是 4B 不是 8B**：归因只要稳定吐 4 个字段的 JSON，不需要它独立修 bug；4B-INT4 首轮即合法且正确，CPU 上 8B 只会把延迟翻倍。

## 4. 在 Qoder 里跑通（四件证据）

1. `/skills` 列表里能看到 `local-pitfall-memory`
2. 自然语言触发：粘报错，只说"见过吗"，Qoder 自主调用 Skill 工具
3. 显式触发：`/local-pitfall-memory status`
4. 会话日志：`~/.qoder/logs/sessions/.../*.jsonl` 里 `tool_name: "Skill"` + `run.ps1` 调用

（截图 3–6 待 8/28 真终端重录）

## 5. 一个反例：它不乱认亲

同一个 `TypeError` 换了引号里的字段名 → family/需谨慎，不冒充 exact；换成 `ERR_MODULE_NOT_FOUND` → 只给"仅联想"。（截图 7）

## 6. 复现

```powershell
git clone <repo> .qoder/skills/local-pitfall-memory
.qoder\skills\local-pitfall-memory\scripts\run.ps1 status --json      # 首次建 venv + 下模型（约 2.4 GB，可 --continue 续传）
.qoder\skills\local-pitfall-memory\tests\test.ps1                      # unit 12 / server 5 / review 16 / retrieval 5 / E2E，全程不碰 OpenVINO
```

Skill 链接：（发布后填）· 仓库：（填）· 演示视频：（填）

## 7. Hybrid AI 的一点思考

云上的大模型负责"想"，本机的小模型负责"记"和"整理"。这块记性只有放在本机才成立——它记的是这台机器的路径、这个人的踩坑史、有时还有不该上云的字符串。AI PC 的价值不是把大模型搬回本地，是把**该留在本地的那部分智能**留在本地。

---
#英特尔 #openvino #魔搭 #agentic #skills
