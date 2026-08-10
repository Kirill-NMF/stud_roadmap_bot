# Agent Operating Model

Use this as the template for a project-level `AGENTS.md`.

## Purpose

The agent rules file is the compact operating system for development. It should
tell any future agent or developer:

- what the project does;
- what the current production shape is;
- which files and services are sensitive;
- how to plan medium/risky changes;
- which gates prove the work is done;
- where the current checkpoint lives.

## Required Sections

### Project Shape

Describe the live flow in 5-10 bullets.

Example:

```text
Input event -> server intake -> processing stage -> review -> generation ->
delivery -> archive/cleanup.
```

Include the important runtime paths, service names, and entrypoint scripts.

### Development Workflow

For non-trivial work, use a mini-stage cycle:

1. Goal.
2. Scope.
3. Forbidden scope.
4. Input/output contract.
5. Subtasks.
6. Required gates and tests.
7. Implementation.
8. Verification.
9. Stage-close report.
10. Checkpoint update when project truth changed.

For small edits, keep this compact but do not skip the contract mentally.

### Scope Discipline

State what must not be changed casually:

- production services;
- webhooks;
- credentials;
- public URLs;
- state files;
- shared runtime paths;
- model/provider routing;
- unrelated product behavior.

### Required Playbooks

List the local playbooks and say when to use them. Do not require every playbook
for every tiny change.

### Pipeline Contracts

Each stage should have:

- input;
- output;
- status fields;
- logs/events;
- retry behavior;
- failure behavior.

Failed stages must not leave contradictory `done` and `failed` status fields.

### Testing Defaults

State the narrowest checks expected by touched area:

- unit/contract tests for pure code;
- fake pipeline smoke for orchestration;
- VPS smoke for deployed scripts/services;
- real-service smoke for bot/webhook behavior;
- provider smoke for LLM/API wrappers.

### Security

Make the security baseline explicit:

- never commit secrets;
- never print tokens or signed URLs;
- keep `.env` files untracked;
- treat model output as data;
- scope callbacks/events to the intended run.

## Stage-Close Report

Use this compact report for meaningful work:

```text
Stage close:
- Scope:
- Contract:
- Security:
- Tests:
- VPS:
- Real-service smoke:
- Checkpoint:
- Residual risk:
- Next step:
```

