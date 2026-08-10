# Builder Prompting

Use this playbook before medium or risky implementation tasks.

## Why

This project crosses Notion, Telegram, VPS scripts, Codex CLI, Gemini/OpenRouter,
HTML/PDF rendering, and runtime secrets. Vague prompts create long debugging
tails. Good builder prompts reduce scope drift, repeated fixes, and hidden
regressions.

## Mini-Stage Cycle

For non-trivial work, define:

1. Goal.
2. Scope.
3. Forbidden scope.
4. Input/output contract.
5. Subtasks.
6. Gates/tests.
7. Success criteria.
8. Report format.

Keep this compact for small changes.

## Task Prompt Template

```text
CONTEXT:
Why this change is needed and what currently happens.

GOAL:
The user-visible or pipeline-visible outcome.

SCOPE:
Files, scripts, services, and runtime paths that may be touched.

DO NOT TOUCH:
Explicitly forbidden areas.

CONTRACT:
Input files/events/status fields.
Output files/events/status fields.
Failure behavior.

SUBTASKS:
1. ...
2. ...
3. ...

SECURITY:
Secrets, public links, logs, user data, and external calls to protect.

IDEMPOTENCY:
What happens if the same event/file/action runs twice or concurrently.

STATUS:
Required status fields and no contradictory done/failed state.

QUALITY GATES:
Exact tests, smoke checks, or manual checks required.

SUCCESS CRITERIA:
What must be true before the task is complete.

REPORT:
Summarize changed files, commands run, results, residual risks, and next step.
```

## Planning Rules

- Split work into small batches. Prefer 2-4 focused files per step.
- Work contract-first: define file-in/file-out or event-in/event-out before implementation.
- Add anchor tests before implementation when a failure mode is clear.
- Do not mark risk as zero. Use low/medium/high and explain why.
- Use one canonical pattern for the task; do not mix competing approaches.
- Paste raw command output when debugging.
- If two attempts fail on the same issue, stop and replan with two alternatives and a recommendation.

## Good Pipeline Contract Example

```text
Codex verification

INPUT:
- run_dir/transcript.md or transcript-plain.txt
- prompt template
- run_dir/status.json

OUTPUT:
- run_dir/verification.md
- run_dir/codex-verification.log
- status verification_status=done

FAILURE:
- verification_status=failed
- verification_log points to the log
- no stale verification_done_at or verification_bytes fields
```

