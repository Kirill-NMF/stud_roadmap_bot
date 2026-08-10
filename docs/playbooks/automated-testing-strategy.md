# Automated Testing Strategy

Use this playbook when adding or closing checks for the roadmap audio pipeline.

## Test Layers

- Unit/contract tests: pure Python functions, message formatting, status updates, prompt rules.
- Fake pipeline smoke: temporary run dirs with fake transcript/verification/article artifacts.
- VPS smoke: service status, timers, script executability, logs, current run status.
- Real Telegram smoke: low-frequency checks with the bot and a dedicated test account/session.
- Real Notion intake smoke: manually add one file to the shared Notion page and watch intake.
- Gemini chain smoke: run the OpenRouter/Gemini rewrite wrapper on a known draft and inspect artifacts.

## Rules

- Do not print secrets, signed URLs, Telegram sessions, or API keys.
- Keep real Telegram and real Notion checks opt-in; do not run them on every small change.
- Real Telegram smoke is required when bot messages, buttons, callbacks, or voice/text correction handling changes.
- Real Notion smoke is required when download, webhook, polling, or duplicate handling changes.
- Gemini smoke is required when prompts, model, OpenRouter wrapper, or validators change.
- Prefer deterministic unit tests before real-service checks.
- Store command outputs in reports, not full logs with sensitive values.

## Current Local Command

```powershell
& 'C:\Users\bests\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\roadmap_pipeline_tests.py
```

## Current VPS Checks

```bash
systemctl is-active notion-pipeline-poll.timer notion-webhook-receiver.service telegram-roadmap-webhook.service
tail -80 /var/log/zoom-audio-pipeline/runner.log
tail -40 /var/log/zoom-audio-pipeline/events.jsonl
```

## Done

A changed flow is done when the narrow automated layer passes and any required
real-service smoke is either passed or explicitly skipped with a reason.

