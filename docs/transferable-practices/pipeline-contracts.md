# Pipeline Contracts

Use this playbook for any automation that processes files, messages, webhooks,
or model outputs through several stages.

## Contract-First Rule

Before implementation, define each stage as:

```text
Stage name:
Input:
Output:
Status:
Events/logs:
Retry:
Failure:
Idempotency:
```

This keeps the pipeline debuggable when a stage starts but the final result does
not arrive.

## Standard Stage Shape

### Input

Examples:

- file path in an inbox;
- webhook payload;
- database row id;
- transcript file;
- approved review note;
- model draft.

### Output

Examples:

- normalized file;
- transcript;
- verification document;
- final article;
- HTML/PDF;
- outbound notification.

### Status

Use explicit status fields, for example:

```json
{
  "stage_status": "started",
  "stage_started_at": "2026-01-01T00:00:00Z",
  "stage_log": "/path/to/log"
}
```

On success:

```json
{
  "stage_status": "done",
  "stage_done_at": "2026-01-01T00:03:00Z",
  "stage_output": "/path/to/output"
}
```

On failure:

```json
{
  "stage_status": "failed",
  "stage_failed_at": "2026-01-01T00:03:00Z",
  "stage_error": "short machine-readable reason",
  "stage_log": "/path/to/log"
}
```

Do not keep stale success fields when the same stage is retried and fails.

## File-In/File-Out Wrapper

For model or CLI stages, prefer a deterministic wrapper:

```text
input files + prompt/config -> command -> output file + log + status update
```

Avoid relying on an interactive chat window for production behavior. If an
interactive tool is used during development, productize it behind a stable API
or command wrapper before handoff.

## Artifact Naming Contract

Name user-facing artifacts from the original meaningful input, not from smoke
test markers, run directories, or temporary slugs.

Example:

```text
Original audio: Misha A1.m4a
Verification:   Misha A1 - verification.html
Article HTML:   Misha A1 - roadmap.html
Article PDF:    Misha A1 - roadmap.pdf
```

Keep smoke-test names only inside smoke-test fixtures and logs.

## Event Log Contract

Write compact machine-readable events for:

- intake accepted;
- duplicate skipped;
- stage started;
- stage done;
- stage failed;
- notification sent;
- cleanup candidate found;
- cleanup completed.

Events should include stage, run id, status, and safe paths. They must not
include secrets, signed URLs, raw tokens, or full private payloads.

