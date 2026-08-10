# Small Agent Project Blueprint

Use this blueprint for a small automation agent that connects messaging, files,
external APIs, model calls, and final delivery.

## Project Skeleton

```text
AGENTS.md
README.md
.env.example
.gitignore
.gitattributes
docs/
  current-checkpoint.md
  handoff-deploy.md
  playbooks/
  transferable-practices/
scripts/ or src/
tests/
deploy/
  systemd/
  reverse-proxy/
.github/workflows/
```

## Build Phases

### Phase 1: Prototype Flow

Make the smallest useful path work once.

Do not over-package yet.

### Phase 2: Contracts And State

Define every stage as input/output/status/log/failure.

Add durable state for claimed inputs and completed outputs.

### Phase 3: Human Review Loop

If the agent needs approval or corrections, define:

- what the reviewer sees;
- what buttons/actions exist;
- how voice/text notes are stored;
- how repeated actions behave;
- what confirmation message appears.

### Phase 4: Autonomous Model Layer

Move prompt/model calls behind stable wrappers.

The production system should call:

```text
generate-thing
rewrite-thing
validate-thing
```

It should not depend on a developer's interactive session.

### Phase 5: Observability

Add:

- status files;
- event logs;
- service health endpoints;
- doctor command;
- concise operational docs.

### Phase 6: E2E Hardening

Run the full path with a realistic sample.

Add failure tests for all places where the system previously got stuck.

### Phase 7: Handoff

Create:

- `.env.example`;
- install guide;
- dry-run installer;
- deployment conflict matrix;
- troubleshooting;
- CI checks;
- optional Docker/Compose.

## Design Defaults

- Prefer one canonical path over several competing paths.
- Keep external APIs behind adapters.
- Keep model prompts in files.
- Keep generated artifacts out of source control.
- Keep runtime state in one documented directory.
- Keep logs useful but secret-free.
- Keep cleanup conservative.

## Definition Of Done

For a handoff-ready small agent:

```text
fresh install documented
doctor OK
unit/contract tests pass
one full E2E passes
failure modes are documented
secrets are not tracked
current checkpoint is accurate
non-developer next steps are clear
```

