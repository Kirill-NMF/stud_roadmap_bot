# Idempotency And Security

This playbook protects small agents from the most common production failures:
duplicate events, half-finished state, leaked credentials, and unsafe cleanup.

## Idempotency Rules

Every external event can happen twice.

Design for:

- webhook retries;
- duplicate file uploads;
- repeated button clicks;
- repeated worker runs;
- process crashes after partial writes;
- archive completion after pipeline completion;
- pipeline completion after archive failure.

## State Registry Pattern

Use a registry or state file for event-driven pipelines.

Track at least:

- stable input id;
- original display name;
- safe slug;
- run directory;
- pipeline status;
- archive status;
- notification status;
- retry count;
- next retry time;
- last error summary.

The registry should answer one question quickly: "Is this input already claimed,
done, failed, or safe to retry?"

## Lock Pattern

Use locks around workers that mutate shared state.

Examples:

- one pipeline runner at a time;
- one archive worker at a time;
- one real-service smoke at a time;
- one cleanup worker at a time.

If a lock is busy, exit cleanly and log `locked=true`.

## Cleanup Rules

Cleanup must be conservative.

Delete local files only when all required durable outcomes are true:

- pipeline is done or final failure is explicitly accepted;
- archive is uploaded or deliberately disabled;
- final delivery is done or deliberately skipped;
- file is older than the retention window;
- paths are inside approved runtime directories.

Never recursively delete a computed path unless the resolved absolute path is
inside the intended runtime root.

## Security Baseline

Never commit or print:

- API keys;
- bot tokens;
- session strings;
- signed URLs;
- `.env` files;
- raw webhook secrets;
- private payload dumps.

Use placeholders in docs:

```text
TELEGRAM_BOT_TOKEN=PASTE_TELEGRAM_BOT_TOKEN_HERE
```

Do not include realistic-looking fake secrets.

## Public Artifact Safety

If the agent publishes HTML/PDF/files:

- generate public paths from safe slugs;
- do not embed signed source URLs;
- do not expose tokens in links;
- render model output through a controlled template;
- keep raw model output as data, not executable HTML.

## Security Review Triggers

Stop and replan before broad changes to:

- credential storage;
- webhook authentication;
- public URL routing;
- provider routing;
- file deletion;
- Caddy/nginx/systemd;
- external paid APIs;
- real production resources.

