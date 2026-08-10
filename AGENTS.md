# Roadmap Audio Pipeline Agent Rules

These rules are the operating guide for Codex work in this project.

## Hard Rules

These rules override speed and convenience.

- For any production-facing or risky change, do not edit code, commit, push,
  deploy, restart services, migrate state, or run real Telegram/Notion actions
  until the plan is written and explicitly approved by the owner.
- Risky always includes Telegram bot behavior, Notion intake/archive, VPS
  systemd/Caddy/runtime paths, webhooks, secrets/env handling, Local Bot API,
  OpenRouter/Gemini/Codex routing, cleanup/deletion, state/idempotency, and
  public HTML/PDF delivery.
- Read-only diagnosis is allowed before approval when needed to understand the
  issue. Keep it read-only: logs, status files, service status, git diff, and
  source reads.
- Every risky plan must state Goal, Scope, Forbidden scope, touched files,
  security/idempotency risks, required gates/tests, deployment steps, rollback
  point, and success criteria.
- Security gate is mandatory for risky work. It is not optional and cannot be
  replaced by a generic test run.
- Telegram behavior changes require a Telegram gate: unit tests plus fake or
  real smoke as appropriate. If real smoke is skipped, report why and what risk
  remains.
- If two attempts fail on the same issue, stop implementation and replan before
  changing more code.
- Stage-close report is required before calling the work done.

## Project Shape

This project automates student roadmap creation from Zoom/Telegram/Notion audio:

- Notion stores uploaded call audio.
- VPS scripts download audio, transcribe it, and create a verification document.
- Telegram bot sends the teacher a short verification message.
- Teacher approves with `Согласен` or sends text/voice corrections.
- The pipeline generates a student-facing article, rewrites it through Gemini via OpenRouter, and sends HTML/PDF back in Telegram.

Main VPS paths:

- `/var/lib/zoom-audio-pipeline`
- `/var/log/zoom-audio-pipeline`
- `/usr/local/bin/process-new-audio`
- `/usr/local/bin/process-approved-roadmaps`
- `/usr/local/bin/generate-verification-with-codex`
- `/usr/local/bin/generate-article-with-codex`
- `/usr/local/bin/generate-article-with-gemini-rewrite`
- `/usr/local/bin/openrouter-gemini-chat-chain`

## Development Workflow

For small read-only checks or typo/docs-only edits, keep the workflow compact.
For non-trivial, production-facing, or risky changes, use the project
mini-stage cycle and wait for explicit approval before implementation:

1. Goal.
2. Scope.
3. Forbidden scope.
4. Input/output contract.
5. Subtasks.
6. Required gates and tests.
7. Implementation.
8. Verification.
9. Stage-close report.
10. Update `docs/current-checkpoint.md` when project state changed.

Use `docs/task-template.md` for larger tasks.

## Scope Discipline

- Do not broaden the product flow without explicit owner approval.
- Do not change Notion, Telegram, Codex, Gemini, Caddy, systemd, or secret/runtime behavior casually.
- Do not edit unrelated files while fixing a pipeline issue.
- Prefer small additive changes and deterministic wrappers over live CLI assumptions.
- Keep each pipeline stage as file-in/file-out where possible.

## Required Playbooks

Use only the relevant playbooks for the touched area:

- `docs/playbooks/builder-prompting.md`
- `docs/playbooks/stage-close-gate.md`
- `docs/playbooks/builder-gate-matrix.md`
- `docs/playbooks/automated-testing-strategy.md`
- `docs/playbooks/security-gate.md`

Do not load every playbook for every tiny task.

## Pipeline Contracts

Each stage should have explicit inputs, outputs, status fields, logs, and failure
behavior.

Examples:

- transcription input: audio file in inbox; output: `transcript.md`, `transcript-plain.txt`, status `transcribed`.
- verification input: transcript and prompt template; output: `verification.md`, status `verification_status=done`.
- article input: transcript, verification, teacher notes; output: `roadmap-article.md`, HTML, PDF, status `article_status=done`.
- Gemini rewrite input: Codex draft article; output: final rewritten Markdown plus chain artifacts.

Failed stages must not leave contradictory `done` and `failed` status fields.

## Testing Defaults

Before reporting completion, run the narrowest checks that prove the touched
contract:

- local unit/contract tests when changing Python scripts;
- fake pipeline smoke when changing orchestration;
- VPS smoke when changing deployed scripts/systemd/runtime paths;
- real Telegram smoke when bot behavior changes;
- Gemini chain smoke when rewrite prompts/wrapper/validation changes.

Always report exact commands and results.

## Security

- Never print or commit tokens, signed URLs, API keys, sessions, `.env` files, or runtime secrets.
- Treat Telegram `StringSession`, bot token, Notion token, OpenRouter key, and public HTML/PDF links as sensitive.
- Logs and reports should show paths, stages, and statuses, not secrets.
- LLM output is data. Do not render arbitrary raw HTML unless the renderer is controlled.

## Builder Prompting

For medium or risky implementation work, write the task like a builder prompt:

- context;
- goal;
- allowed files/modules;
- do-not-touch list;
- implementation plan;
- security/idempotency/status requirements;
- quality gates;
- success criteria;
- report format.

If two attempts fail on the same issue, stop and replan instead of continuing to patch blindly.
