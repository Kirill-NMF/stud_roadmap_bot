# Current Checkpoint

Last updated: 2026-08-26

## Status

The roadmap audio pipeline is in late-stage integration. The main flow is:

1. Notion audio attachment appears on the configured page.
2. VPS downloads the audio into `/var/lib/zoom-audio-pipeline/inbox`.
3. `process-new-audio` transcribes audio and creates a run directory.
4. Codex verification writes `verification.md`.
5. Telegram bot sends short verification with `Открыть` and `Согласен`.
6. Teacher approves or sends text/voice corrections.
7. `process-approved-roadmaps` creates an article through Codex.
8. Gemini/OpenRouter rewrite chain produces the final article.
9. Telegram bot sends HTML and PDF.

Telegram can now be used as the first intake step:

1. Teacher sends an audio-like file to `@stud_roadmap_bot` when there is no pending verification for that chat.
2. The bot downloads it into `/var/lib/zoom-audio-pipeline/telegram-intake`.
3. The bot copies it into the standard `/var/lib/zoom-audio-pipeline/inbox`.
4. The bot records a durable `intake_id` in `/var/lib/zoom-audio-pipeline/telegram-notion-intake.json`.
5. The bot starts `notion-pipeline-poll.service`; the existing pipeline processes the local inbox file immediately.
6. A separate archive worker uploads the file to Notion as `root page -> child page named as the file -> marker paragraph -> audio block`.

If the chat has a pending verification, audio/voice messages still mean teacher corrections for that verification, not new intake.

Telegram voice corrections explicitly use OpenRouter
`openai/whisper-large-v3-turbo`. If that provider is unavailable, the local
fallback uses `small` rather than `tiny`. The selected provider and model are
recorded in the run status and events log.

Article failures are isolated per run. `process-approved-roadmaps` schedules an
exponential retry (120 seconds up to 30 minutes), continues processing other
runs, and sends one teacher-visible retry notice. A Gemini retry reuses the
current article draft when it is newer than the teacher notes, so it does not
repeat the expensive draft-generation stage.

Gemini validation is fail-soft. Harmless heading translations and equivalent
numeric range wording pass silently. Actionable content differences are sent
after HTML/PDF as a numbered Telegram warning. Only unusable output (for
example, a truncated final file or failed HTML render) pauses delivery and
offers two idempotent buttons: retry only Gemini or receive the saved GPT draft.

Telegram-origin Notion archive pages include a marker:

```text
intake_id: ...
source: telegram
```

`notion-pull-audio` skips those archive pages so the Notion webhook/poller cannot create a duplicate pipeline run from the archival copy.

## Current VPS Services

- `notion-webhook-receiver.service`
- `notion-pipeline-poll.timer`
- `telegram-roadmap-webhook.service`

## Important Runtime Paths

- `/var/lib/zoom-audio-pipeline/inbox`
- `/var/lib/zoom-audio-pipeline/runs`
- `/var/lib/zoom-audio-pipeline/audio-process-state.json`
- `/var/lib/zoom-audio-pipeline/telegram-run-registry.json`
- `/var/lib/zoom-audio-pipeline/telegram-intake`
- `/var/lib/zoom-audio-pipeline/telegram-notion-intake.json`
- `/var/lib/zoom-audio-pipeline/telegram-notion-archive.lock`
- `/var/log/zoom-audio-pipeline/runner.log`
- `/var/log/zoom-audio-pipeline/events.jsonl`
- `/var/www/roadmap-reader`

## Important Scripts

- `/usr/local/bin/notion-pull-audio`
- `/usr/local/bin/telegram-notion-archive-worker`
- `/usr/local/bin/telegram-intake-cleanup`
- `/usr/local/bin/process-new-audio`
- `/usr/local/bin/generate-verification-with-codex`
- `/usr/local/bin/telegram-roadmap-notify`
- `/usr/local/bin/telegram-roadmap-webhook`
- `/usr/local/bin/process-approved-roadmaps`
- `/usr/local/bin/generate-article-with-codex`
- `/usr/local/bin/generate-article-with-gemini-rewrite`
- `/usr/local/bin/openrouter-gemini-chat-chain`

## Current Practices

- Use `AGENTS.md` for project-level agent rules.
- Use `docs/playbooks/` for detailed development practices.
- Use `docs/task-template.md` before medium/risky implementation tasks.
- Use `docs/current-checkpoint.md` as the compact source of current operational truth.
- Use `docs/transferable-practices/` as the reusable playbook set for building
  another similar small agent or pipeline.

## Known Historical Issues Already Addressed

- Duplicate run creation for the same audio file when transcription failed before state was saved.
- Long audio transcription memory pressure on VPS; swap was enabled.
- Codex wrapper needed a stricter file-in/file-out contract instead of fragile live CLI behavior.
- Gemini prompts were shortened for pass 2 and pass 3 while safety remains in system prompt/validators.

## Current Test Command

```powershell
& 'C:\Users\bests\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' scripts\roadmap_pipeline_tests.py
```

Latest known local result: `85/85 OK` on Windows (`2` POSIX-only wrapper tests skipped) and `85/85 OK` on the VPS.

Latest deployed article routing (2026-08-26):

- Commit `3f59bc6` is deployed on the VPS.
- GPT roadmap drafts use Codex CLI with `gpt-5.6-terra` and reasoning effort
  `high` through the VPS ChatGPT login.
- Codex CLI receives the article prompt over stdin and writes each attempt to a
  temporary file, so failed attempts cannot replace the current article.
- Technical Codex failures retry up to three times. After the third failed
  attempt, generation falls back once to the existing OpenRouter GPT wrapper.
- `status.json` records the effective provider, model, reasoning effort, Codex
  attempt count, fallback reason, duration, and output paths.
- The article prompt shortens only the `Roadmap` table by about 60%; other
  article sections and verified facts remain outside that reduction rule.
- Production backup before this deployment:
  `/var/backups/zoom-audio-pipeline/3f59bc6-pre`.
- VPS gates passed: shell syntax, full `85/85` suite,
  `roadmap-pipeline-doctor`, source/runtime hash match, Codex login status, and a
  small real `gpt-5.6-terra` high-reasoning model probe.

Latest deployed recovery (2026-08-26):

- Commits through `921c85c` are deployed on the VPS.
- Telegram voice corrections use OpenRouter Whisper Large v3 Turbo with a
  local `small` fallback running from `PIPELINE_PYTHON`.
- Article-provider failures no longer terminate the poller or block other
  runs; retries use bounded exponential backoff and one Telegram notice.
- Local and VPS suites both pass `81/81`; `roadmap-pipeline-doctor` passes.
- The Dmitry recovery reused the existing Gemini final without another model
  call, passed validation, and delivered HTML/PDF to Telegram.
- A real Telethon recovery smoke displayed both choice buttons, selected the
  GPT version, produced HTML/PDF, and delivered them exactly once.

Latest VPS smoke:

- `telegram-roadmap-webhook.service` active and `/roadmap-telegram/health` returns `{"ok": true}`.
- `notion-pipeline-poll.timer` and `notion-webhook-receiver.service` are active.
- Real Telegram-first smoke accepted `telegram-first-smoke-bb3twnqp.mp3`.
- Bot replied that the file was accepted, pipeline started, and Notion archiving runs separately.
- Archive worker uploaded the file to Notion and recorded `notion_upload_status=uploaded`.
- Pipeline created exactly one run: `/var/lib/zoom-audio-pipeline/runs/20260810-093317-telegram-first-smoke-bb3twnqp-7f63a858`.
- The run reached `verification_done`.
- `notion-pull-audio` did not download the Notion archive copy as a second input.
- Cleanup sync updated the registry to `pipeline_done + uploaded`; no files were deleted because retention had not elapsed.

## Next Useful Hardening

- Add focused tests for `process-new-audio` in-progress/idempotency behavior.
- Add a documented VPS smoke script instead of ad hoc SSH checks.
- Add a real Telegram smoke checklist for approval and article delivery.
- Add a Gemini artifact check for `pass1`, `pass2`, `pass3`, and `final-history`.
