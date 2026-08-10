# Transferable Practices

This folder contains reusable engineering practices extracted from this project.
Use it as a starter playbook for another small automation agent or pipeline.

The goal is not to copy this exact roadmap bot. The goal is to copy the way of
building:

- clear project-level agent rules;
- contract-first pipeline stages;
- idempotent event handling;
- scoped quality gates;
- layered tests;
- secret-safe operations;
- staged deployment for non-developers;
- a last-mile productization pass before handoff.

## Recommended Copy Set

For a new similar project, copy these files first:

- `small-agent-project-blueprint.md`
- `agent-operating-model.md`
- `pipeline-contracts.md`
- `gates-and-testing.md`
- `idempotency-security.md`
- `deployment-and-handoff.md`
- `last-mile-productization.md`

Then adapt project names, runtime paths, services, providers, and real smoke
tests.

## How To Use

1. Start the new project with `AGENTS.md` based on
   `agent-operating-model.md`.
2. Define the pipeline stages using `pipeline-contracts.md`.
3. Choose gates from `gates-and-testing.md` before implementation.
4. Add idempotency and secret rules from `idempotency-security.md`.
5. Before handoff, run the productization phase from
   `last-mile-productization.md`.
6. Only then package the project for a non-developer using
   `deployment-and-handoff.md`.

## What To Keep Project-Specific

Do not blindly copy:

- API provider names;
- Telegram bot names;
- Notion page structure;
- VPS paths;
- domains and ports;
- exact smoke-test accounts;
- model choices and prompt text.

Those belong in the target project's own README, `.env.example`, doctor script,
and deployment guide.

