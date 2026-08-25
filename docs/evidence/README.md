# Qoder integration evidence (2026-08-24, Qoder CLI v1.1.29, project-level install `.qoder/skills/local-pitfall-memory`)

1. Discovery — `qodercli -p "List the skills available..."` lists `local-pitfall-memory` alongside built-ins.
2. Natural-language trigger — pasted a real ERR_REQUIRE_ESM stack trace from a different project/path; Qoder invoked the Skill tool on its own, ran `scripts\run.ps1 lookup`, and answered with the exact-match verified fix (可引用). See `2026-08-24-qoder-natural-trigger.txt`.
3. Explicit trigger — `/local-pitfall-memory status` → "Skill local-pitfall-memory activated" + health report. See `2026-08-24-qoder-explicit-trigger.txt`.
4. Session log proof — `~/.qoder/logs/sessions/C--Users-miku-pitfall-demo/*.jsonl` contains `tool_name: "Skill"` permission.resolved and `tool.shell.started` with `run.ps1`.

Screenshots/screen recording of the same four steps in an interactive terminal: TODO 8/28 (主片素材).

5. (2026-08-25, v0.5.0) Natural-language trigger re-run after the codex-reviewed rewrite: Qoder invoked the Skill on its own and reported exact / 可引用 / times_seen 1 with the verified resolution. See `2026-08-25-qoder-natural-trigger-v0.5.0.txt`.
