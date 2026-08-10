# Last-Mile Productization

This playbook captures the final polishing stage from the roadmap bot project.
Use it when a prototype works with developer help but must become a reliable
small product.

## Goal

The pipeline should run autonomously, reproducibly, and without manual help from
an interactive development chat.

Example target flow:

```text
Telegram audio -> VPS pipeline -> verification -> approve/revise -> article ->
rewrite -> HTML/PDF -> Telegram
```

Replace these names with the target project's real services.

## Key Decision

Do not start with Docker or packaging.

First make the production-style pipeline autonomous and testable in its current
runtime mode. Otherwise the packaging step may preserve hidden dependencies and
make debugging harder.

## Productization Steps

### 1. Production Contract Audit

Fix the exact contract of every stage:

- input files/events;
- output files/artifacts;
- status fields;
- events;
- retry behavior;
- failure behavior.

Focus especially on intake, archive, processing, review, generation, delivery,
and cleanup.

### 2. Interactive Tool Removal Plan

Identify where the live pipeline still depends on an interactive developer tool.

Replace only those locations with autonomous wrappers:

```text
generate-verification
generate-article
rewrite-article
```

Inside the wrapper, choose provider/model through env:

```text
PROVIDER=openrouter
MODEL=...
```

Do not rewrite unrelated pipeline stages while doing this.

### 3. Naming Contract Fix

Centralize names:

- `display_name`;
- `safe_slug`;
- `artifact_title`;
- `student_title` or equivalent user-facing title.

User-facing files should be named from the original meaningful input, not from
test names, temporary ids, or run directory names.

### 4. Full E2E Before Packaging

Before GitHub handoff or Docker, one real input must pass:

```text
intake -> verification -> approval/revision -> generation -> rewrite ->
final HTML/PDF or equivalent -> delivery
```

This is the gate that catches "accepted for work, but no final result arrived."

### 5. Failure Tests

Add focused checks for:

- archive/upload failed;
- pipeline failed;
- notification failed;
- duplicate input;
- duplicate webhook;
- duplicate approval;
- cleanup before durable archive;
- provider output missing or invalid.

### 6. Repository Extraction

Only after the E2E gate is green, prepare the repository:

- scripts or `src/`;
- tests;
- runtime units/config templates;
- docs;
- `.env.example`;
- README;
- troubleshooting;
- CI.

### 7. Deployment Strategy

For a non-developer, the primary path can be `install.sh + systemd` when the
target is a normal VPS. Docker Compose is useful as a second layer, but it can
add complexity around reverse proxy, volumes, timers, browser/PDF dependencies,
and webhook ports.

### 8. Doctor And Dry-Run

Ship:

```bash
./doctor.sh
./install.sh --dry-run
```

`doctor` proves the installation is healthy. `--dry-run` shows planned changes
without modifying the server.

### 9. Release Gate

The product is ready for handoff only when:

- fresh install path is documented;
- env is filled with placeholders replaced;
- doctor passes;
- sample input E2E passes;
- final artifact reaches the user;
- docs match actual commands;
- no secrets are present in tracked files.

## Output Of This Stage

The result of this stage should be boring in the best way:

- one clear way to run the system;
- one clear way to check health;
- one clear way to debug;
- one clear way to install;
- no dependency on a developer sitting in the chat.

