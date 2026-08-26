# 录屏分镜（30–50 秒主片 + 2–3 分钟完整版）

> 2026-08-27 02:5x 写。台子已搭好：`C:\Users\miku\pitfall-demo` 是一个 CJS 项目、`index.js` 用了 ESM `import`，`node index.js` 必报 `SyntaxError: Cannot use import statement outside a module`；这个报错已作为**第 3 个已验证的坑**在真库里（`~\.pitfall-memory\pitfalls.db`），重查命中 exact / 可引用。前两个坑是 8/23–8/25 真踩的。

## 录前 30 秒检查

```powershell
cd C:\Users\miku\pitfall-demo
npm pkg get type          # 必须是 "commonjs"；不是就 npm pkg set type=commonjs
node index.js             # 必须报错（这就是第一镜）
Get-Content .qoder\skills\local-pitfall-memory\SKILL.md -TotalCount 2   # name: local-pitfall-memory
```

- 终端：Windows Terminal，字号放大两档（Ctrl + = 两次），窗口 1920×1080 或全屏，深色主题
- OBS 录"窗口捕获"这个终端；麦不用开，后期 TTS 或不配音
- 录之前别开 Qoder，第一镜是干净的 PowerShell 提示符

## 主片四拍（目标 30–50 s，每拍 8–12 s）

| 拍 | 你敲什么 | 镜头停在哪 | 备注 |
|---|---|---|---|
| 1 | `node index.js` | 红色 `SyntaxError: Cannot use import statement outside a module` | 停 2 秒让人读到 |
| 2 | `qodercli`，进去后粘贴报错，跟一句：`这个报错以前在这台机器上见过吗？查一下踩坑库` | Qoder 调 `local-pitfall-memory` 的那一行 + 返回的 `"hit": "exact", "confidence": "可引用"` 与修复卡 | **整片的钉子**：`可引用` 三个字和 `fix_command` 要在画面里 |
| 3 | 照修复卡敲 `npm pkg set type=module`，再 `node index.js` | 打印 `demo app` | 干净退出，停 1 秒 |
| 4 | 回 Qoder：`把踩坑库汇编成文档` 或直接 `.qoder\skills\local-pitfall-memory\scripts\run.ps1 digest` | 三行踩坑表（ESM import / ERR_REQUIRE_ESM / PS1 BOM） | 收在表格上，停 2 秒 |

片头卡 2 秒："同一个坑，第二次：从重新排查到本地命中 0.4 秒"；片尾卡 2 秒：Skill 名 + 魔搭链接。字幕后期加，别配音也行。

## 完整版（2–3 分钟，不剪，给评委外链）

在主片前面多录一段"第一次踩坑"：
1. 先 `.qoder\skills\local-pitfall-memory\scripts\run.ps1 status --json`（看得见 `model_ready: true`、`retrieval_mode: hybrid`、`version: 0.7.0`）
2. 造一个库里没有的错：`node -e "require('./nope.js')"` → `MODULE_NOT_FOUND`
3. Qoder 里问"见过吗" → `"hit": "none"`（诚实：没见过就说没见过）
4. 修（`echo module.exports=1 > nope.js`），跟 Qoder 说"修好了，记进踩坑库：根因是文件不存在，修复是创建文件，验证是命令退出 0" → `propose` 返回 `attribution`（本地 Qwen3-4B 归因，约 20 s，**这段别剪**，就是要让人看见是本地 CPU 在算）→ `commit`
5. 再问一次同样的错 → `可引用`
6. 然后接主片四拍

## 录完给我的东西

- 主片原始 mp4（放 `D:\录像\`，名字随意，告诉我）
- 完整版原始 mp4
- 顺手四张截图（可以从视频截）：触发行、可引用卡、`digest` 表、`status --json`

我来：剪辑（剪映草稿走老流程）、字幕、片头片尾卡、README 顶部 GIF、研习社文章配图、小红书封面。

## 录之后复位

```powershell
npm pkg set type=commonjs     # 让 node index.js 重新报错，方便重录
```
