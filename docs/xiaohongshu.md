> 已发布 2026-08-28 22:04（图文，审核中）。标题用第 3 个；配图=封面+03+02+05；视频 LPM-Promo-v1 未用，留作第二条。

# 小红书帖（她的号发；@OpenVINO中文社区 @魔搭ModelScope社区）

标题候选（选一个）：
1. AI 不笨，它只是失忆：这个报错你上周明明修过
2. 同一个报错，AI 为什么每次都从头猜？我给 Qoder 装了块本地海马体
3. 同一个报错，第二次：本地命中 0.4 秒   ← 0.4s 是真机实测，选这个（17 分钟那个数字没有可复现记录，不用）

正文（≤ 300 字）：

上周二修过的报错，周五换个项目又来了，AI 又从头猜十分钟。
不是它不聪明，是它没有"这台机器的记性"——大脑在云上，每次都是新会话。

所以我做了个 Qoder 的 Skill：local-pitfall-memory。
报错先查本机历史：同一个坑 0.4 秒命中，直接给上次验证过的修法。
只记"跑通过的修复"，没验证的进不了高置信档，幻觉进不来。
历史库、索引、检索全在本机，入库先脱敏。
归因用 Qwen3-4B INT4 跑在 OpenVINO 上，纯 CPU，不在查询路径上，不拖慢。

魔搭 × Intel Production AI Skills 大赛作品。
文章👉 https://www.modelscope.cn/learn/436076  Skill👉 https://www.modelscope.cn/skills/CecilyOvo/local-pitfall-memory

#英特尔 #openvino #魔搭 #agentic #skills

配图（都在 docs/）：封面 `xiaohongshu-cover.png`（3:4）；①`screenshots/03-exact-hit.png` Qoder 自动触发 + 可引用；②`screenshots/02-skill-activated.png`；③`screenshots/05-digest.png`；视频用 `Videos\魔搭\LPM-Promo-v1.mp4`（37.9s，小红书直接传视频优先，图文备用）
