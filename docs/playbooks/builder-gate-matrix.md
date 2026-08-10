# Builder Gate Matrix

Use this matrix to choose gates by touched area. Do not run every gate for every
task, but do not skip required gates for risky areas.

For risky areas, write the gate list before implementation and get owner
approval. Telegram, Notion, VPS/systemd/Caddy, secrets/env, cleanup, webhooks,
and provider routing are always risky.

| Touched area | Required focus | Gates |
| --- | --- | --- |
| Notion intake/webhook | download, duplicate events, signed URLs, state files | security, idempotency, VPS smoke |
| Audio transcription | memory, large files, state claim, transcript artifacts | pipeline contract, idempotency, VPS smoke |
| Codex verification | prompt file, non-interactive CLI, output file, status fields | file-in/file-out smoke, status check |
| Telegram verification bot | message length, buttons, callback handling, voice/text notes, pending-review routing | unit tests, Telegram smoke, security/idempotency |
| Telegram intake boundary | `audio`/audio `document` can start pipeline; `voice` can only be a correction when a pending review exists | unit tests for pending/no-pending, Telegram smoke |
| Article generation | teacher notes, verified facts, PDF options, article status | article contract test, focused fixture |
| Gemini rewrite | model, prompt chain, history, validation, OpenRouter env | Gemini chain smoke, artifact check |
| HTML/PDF delivery | renderer, public path, Telegram sendDocument | render smoke, Telegram smoke |
| VPS/systemd/Caddy | units, timers, logs, public URL, runtime dirs | VPS smoke, security gate |
| Secrets/runtime env | tokens, sessions, API keys, logs | security gate, no-print check |
| Docs/playbooks/checkpoint | current truth, no stale commands, no secrets | stage-close docs check |

## Non-Negotiable Boundary Checks

- Telegram `voice` must never start a new roadmap pipeline. It is only a
  teacher correction for an existing pending review.
- Telegram `audio` or audio `document` may start a new pipeline only when no
  pending review is being handled for that chat.
- Duplicate Telegram updates, repeated callback clicks, repeated Notion events,
  and timer overlap must not create duplicate pipeline runs.
- Cleanup must never delete files that are still needed for pipeline completion,
  retry, or Notion archive recovery.

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
