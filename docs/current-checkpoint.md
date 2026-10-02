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
- `telegram-bot-api-local.service`

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
- `/usr/local/bin/roadmap-article-editor`
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

Latest clean VPS result: `122/122 OK` on commit `4f7a1f0`.

Latest deployed final-article editing (2026-08-26):

- Commits through `4f7a1f0` are deployed on the VPS.
- The public final-article reader assigns fixed block IDs and numbers for the
  current article version. The title/introduction and every `##` section are
  separate selectable blocks; nested headings, lists, and tables stay inside
  their parent block. The complete Roadmap table is therefore selectable as
  one block.
- Block numbers and checkboxes exist only in the Telegram editor page opened by
  `Посмотреть красиво`. Student HTML/PDF contain no editor controls, numbers,
  block attributes, or editor-only CSS.
- Telegram WebApp selection is bound to the signed teacher identity, run owner,
  current article version, and known paragraph IDs. Repeated Telegram updates
  and completed edit jobs are idempotent.
- Gemini Pro receives the complete article as style context but may return
  operations only for the selected blocks. A replacement must contain the full
  selected section and preserve its heading. The server validates the strict
  JSON patch and merges it deterministically; unselected blocks and article
  order remain unchanged.
- Legacy already-open editor pages with paragraph IDs remain accepted during
  the schema transition; newly prepared pages use block IDs.
- A successful edit creates a new article version and sends fresh HTML/PDF.
  Invalid model output is retried once and cannot replace the current article.
- Production gates passed: full `122/122` suite, `roadmap-pipeline-doctor`,
  webhook health on `127.0.0.1:8792`, runtime/source byte comparison, unsigned
  request rejection, release secret scan, and isolated block/editor/student
  render smoke.
- No real Gemini call or signed Telegram WebApp interaction was used during the
  deployment gate.
- Production backup before this deployment:
  `/var/backups/zoom-audio-pipeline/4f7a1f0-pre-20260826-212645`.

Latest deployed Telegram repeat-intake behavior (2026-08-26):

- Commit `366736b` is deployed on the VPS.
- A deliberate resend or forward of the same Telegram audio/document is a new
  intake because its identity is now `chat_id + message_id`, not
  `file_unique_id`.
- A technical replay of the same Telegram message remains idempotent and does
  not create a second pipeline run.
- Existing legacy `telegram:<file_unique_id>` registry entries remain valid but
  do not block a newly sent or forwarded message.
- Repeated files receive unique local/inbox paths and distinct process keys, so
  downstream transcription creates a separate run.
- `file_unique_id`, `message_id`, and the message key remain available as intake
  metadata for diagnostics and Notion archiving.
- VPS gates passed: full `89/89` suite, focused 8-case Telegram fake smoke,
  runtime/source hash match, public webhook health, and
  `roadmap-pipeline-doctor`.
- Production backup before this deployment:
  `/var/backups/zoom-audio-pipeline/366736b-pre`.

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

Deployed Codex latency policy (2026-08-26):

- Historical VPS logs show normal Codex CLI article runs completing in
  `36-63` seconds. The two runs containing `Reconnecting... 2/5` took
  approximately `649` and `663` seconds, independent of model and prompt size.
- The selected policy is two Codex CLI attempts with an `80` second timeout per
  attempt and the existing `3` second retry delay. After the second failure,
  generation falls back once to the existing OpenRouter article wrapper.
- Commit `7e67f31` is deployed on the VPS. The installed wrapper matches the
  repository source, the full VPS suite passes `90/90`, and
  `roadmap-pipeline-doctor` is green. No real model call was made for this gate.
- Production backup before this deployment:
  `/var/backups/zoom-audio-pipeline/7e67f31-pre`.

Deployed hedged Codex policy (2026-08-26):

- Keep the primary `gpt-5.6-terra` high-reasoning request alive instead of
  terminating it at 80 seconds.
- If no valid article is ready after 65 seconds, start a second isolated Codex
  request. If neither request has won by 145 seconds, start the OpenRouter
  fallback in an isolated temporary run directory.
- The first valid output is committed atomically. All losing process groups are
  terminated, and only the winner can continue to Gemini.
- Commit `22d003a` is deployed on the VPS. The primary has a 900-second safety
  cap, the delayed Codex hedge has an 80-second cap, and the OpenRouter fallback
  has a 900-second cap.
- The installed wrapper and hedge runner match their repository sources. The
  full clean VPS suite passes `99/99`, three repeated race-focused runs pass,
  and `roadmap-pipeline-doctor` is green with the hedge runner included. No real
  model call was made for these deployment gates.
- Production backup before this deployment:
  `/var/backups/zoom-audio-pipeline/22d003a-pre`.

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

Latest deployed repeatable final-article editing (2026-08-27):

- Commit `f6c4fd1` is deployed on the VPS.
- `active_articles` keeps the current final article active after a correction
  job consumes the one-shot `pending_article_edits` checkbox selection.
- Every delivered article version updates the active `run_key` and
  `article_version`, clears the previous selection, and provides a fresh editor
  page for the next voice or text correction.
- Voice or text without a fresh selection now asks the teacher to select
  blocks in the current article. A second correction while an edit job is
  running is rejected visibly and cannot create a parallel Gemini job.
- A successfully accepted new audio/document closes the previous article
  session and clears its selection. Failed or duplicate intake leaves the
  current article session untouched. Closed and stale editor pages cannot save
  selections.
- Existing article pages created before this state field was introduced are
  recovered once from the same teacher's latest valid run. A closed-state
  tombstone prevents an older run from being recovered after new intake.
- Clean Windows archive, clean Linux pre-deploy, and VPS post-deploy suites all
  pass `129/129`; `12` platform-dependent tests are skipped on Windows only.
  Webhook health returns `200`, an unsigned article-selection request returns
  `400`, installed runtime hashes match repository sources, and the service log
  has no post-restart warnings.
- No real Telegram message or Gemini call was used for the deployment gate.
- Production rollback point:
  `/var/backups/zoom-audio-pipeline/f6c4fd1-pre-20260826-2312`.

Latest deployed visual verification structure (2026-08-28):

- Commit `38d9811` is deployed on the VPS.
- New verification documents contain six teacher-review blocks with stable
  bracket numbers: route chain, vertical roadmap, lesson-to-result chain,
  expected-results checklist, skill-to-benefit table, and pre-start checklist.
- The verification reader preserves those blocks verbatim under
  `Предварительная схема будущей статьи`. Legacy verification documents without
  the new section still render through the previous compatible path.
- The six blocks use only facts and careful interpretations from the call.
  Optional PDF-bank ideas remain excluded unless they were discussed in the
  call or explicitly approved by code.
- The GPT article prompt treats the reviewed structure and numbered teacher
  corrections as binding input, but removes service numbers `[1]`-`[6]` from
  the student-facing article.
- This supersedes the earlier wide `Roadmap` table rule. Roadmap stages are now
  vertical and compact, with `Цель`, `Практика`, and `На выходе`; detailed
  lesson-system sections are not shortened.
- Clean Windows and VPS suites pass `133/133`; `12` POSIX-only tests are skipped
  on Windows. Runtime/source hashes match, all three related services are
  active, webhook health returns `200`, and an unsigned article-selection
  request returns `400`.
- No real Telegram message, model call, or Notion mutation was used for this
  deployment gate because intake/callback behavior did not change.
- Production rollback point:
  `/var/backups/zoom-audio-pipeline/38d9811-pre-20260828`.

Latest deployed resilient Gemini article edits (2026-10-02):

- Commit `07265f2` is deployed on the VPS.
- Gemini now returns only the editable body of a selected section. The server
  deterministically restores the original immutable heading, while remaining
  backward-compatible with responses that repeat or alter that first heading.
- HTML and sibling sections remain rejected. Failed validation attempts record
  only attempt number, stable error code, and block ID; voice instructions and
  provider responses are not copied into diagnostics.
- The full Linux suite passes `136/136`; the focused article-edit, selection,
  security, and fake-delivery suite passes `25/25`. Installed and repository
  editor hashes match, and the three related services remain active.
- No real Telegram message, Gemini call, or Notion mutation was used for the
  deployment gate.
- Production rollback point:
  `/usr/local/bin/roadmap-article-editor.rollback-07265f2`.

Latest deployed repeat-edit cache fix (2026-10-02):

- Commit `58c2e6a` is deployed on the VPS.
- Every delivered article revision now has a versioned Telegram WebApp URL
  (`article.html?v=N`). Telegram therefore opens the current editor instead of
  reusing a cached page whose `article_version` has already become stale.
- The server still rejects genuinely stale pages, unknown blocks, wrong owners,
  and selections submitted while an edit is running. No selection API,
  authentication, article content, or Gemini behavior changed.
- The clean Linux suite passes `137/137`; the post-deploy Telegram/security
  suite passes `9/9`. Runtime and repository notifier hashes match, and the
  webhook plus both pipeline services are active.
- The current article was redelivered through the normal `article_ready` path
  with a versioned button. No Gemini call or Notion mutation was used.
- Production rollback point:
  `/usr/local/bin/telegram-roadmap-notify.rollback-e0747be`.

Latest deployed study-buddy rewrite and pre-generation decisions (2026-10-02):

- Commit `356202c` is deployed on the VPS.
- Gemini pass 2 now rewrites in the voice of an adult, supportive English
  study buddy. It explicitly excludes irony, baby talk, childish imagery, and
  overfamiliarity while preserving structure and facts.
- Verification documents now contain six numbered pre-generation decisions:
  roadmap stages, student content, group versus individual format, homework,
  pre-start checklist, and level/results.
- Every decision is marked either `Из созвона` or `Предложение`. A general
  `Согласен` confirms only facts already discussed during the call; proposals
  still require explicit teacher approval before article generation.
- The article prompt uses only confirmed variants for those six decisions. It
  no longer invents a default 1/3/6-month roadmap or an unconfirmed pre-start
  checklist.
- The focused Linux contract suite passes `24/24`; the clean Linux repository
  suite passes `140/140`. Installed runtime files match repository sources.
- A real isolated Gemini three-pass smoke preserved the student, destination,
  timeline, lesson frequency, and A1/A2 levels. Pass 2 and the final article
  contained none of the rejected Winnie-the-Pooh/childish imagery.
- Telegram and Notion webhook services plus Local Bot API are active; the
  Notion poll timer is active. No real Telegram message, Notion mutation, or
  new audio intake was used for this deployment.
- Production rollback point:
  `/var/backups/zoom-audio-pipeline/356202c-pre-20261002`.

## Next Useful Hardening

- Add focused tests for `process-new-audio` in-progress/idempotency behavior.
- Add a documented VPS smoke script instead of ad hoc SSH checks.
- Add a real Telegram smoke checklist for approval and article delivery.
- Add a Gemini artifact check for `pass1`, `pass2`, `pass3`, and `final-history`.
