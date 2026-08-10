# Project Playbooks

These playbooks are lightweight development practices for the roadmap audio
pipeline project. They are not product specs. Use them only when the matching
workflow is active.

## Current Playbooks

- `builder-prompting.md` - plan and prompt medium/risky implementation tasks.
- `stage-close-gate.md` - close a stage only after checking scope, evidence, tests, and risks.
- `builder-gate-matrix.md` - choose required gates based on touched pipeline area.
- `automated-testing-strategy.md` - organize local, VPS, Telegram, Notion, and Gemini checks.
- `security-gate.md` - baseline security checks and escalation triggers.

## Transferable Practices

Reusable versions of these practices live in
`docs/transferable-practices/`. Use that folder when starting another small
agent or automation pipeline.

## Rule

Do not load every playbook for every change. Pick the smallest set that matches
the touched area and keep the report compact.
