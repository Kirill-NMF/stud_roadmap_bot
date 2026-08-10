# Builder Gate Matrix

Use this matrix to choose gates by touched area. Do not run every gate for every
task.

| Touched area | Required focus | Gates |
| --- | --- | --- |
| Notion intake/webhook | download, duplicate events, signed URLs, state files | security, idempotency, VPS smoke |
| Audio transcription | memory, large files, state claim, transcript artifacts | pipeline contract, idempotency, VPS smoke |
| Codex verification | prompt file, non-interactive CLI, output file, status fields | file-in/file-out smoke, status check |
| Telegram verification bot | message length, buttons, callback handling, voice/text notes | unit tests, real Telegram smoke |
| Article generation | teacher notes, verified facts, PDF options, article status | article contract test, focused fixture |
| Gemini rewrite | model, prompt chain, history, validation, OpenRouter env | Gemini chain smoke, artifact check |
| HTML/PDF delivery | renderer, public path, Telegram sendDocument | render smoke, Telegram smoke |
| VPS/systemd/Caddy | units, timers, logs, public URL, runtime dirs | VPS smoke, security gate |
| Secrets/runtime env | tokens, sessions, API keys, logs | security gate, no-print check |
| Docs/playbooks/checkpoint | current truth, no stale commands, no secrets | stage-close docs check |

## Final Report Gate Matrix

For non-trivial work, include:

```text
Gate / Check              Status            Evidence / Why
Scope                     passed
Pipeline Contract         passed
Idempotency               passed
Security                  passed
Automated Tests           passed
VPS Smoke                 not applicable
Telegram Real Smoke       not applicable
Gemini Chain Smoke        not applicable
Checkpoint                updated / unchanged
```

