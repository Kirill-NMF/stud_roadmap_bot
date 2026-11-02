#!/usr/bin/env python3
"""Generate verification with Codex first and OpenRouter as a fallback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


DEFAULT_PRIMARY = "/usr/local/bin/generate-verification-with-codex"
DEFAULT_FALLBACK = "/usr/local/bin/generate-verification-with-openrouter"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_status(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_status(path: Path, status: dict[str, object]) -> None:
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def provider_chain() -> list[tuple[str, str]]:
    configured = [
        ("codex", os.environ.get("VERIFICATION_PRIMARY_SCRIPT", DEFAULT_PRIMARY)),
        ("openrouter", os.environ.get("VERIFICATION_FALLBACK_SCRIPT", DEFAULT_FALLBACK)),
    ]
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for name, script in configured:
        script = script.strip()
        if script and script not in seen:
            seen.add(script)
            result.append((name, script))
    return result


def run_chain(
    run_dir: Path,
    providers: list[tuple[str, str]],
    *,
    run_command: Callable[..., object] = subprocess.run,
) -> int:
    status_path = run_dir / "status.json"
    log_path = run_dir / "verification-provider-fallback.log"
    failures: list[str] = []

    with log_path.open("a", encoding="utf-8") as log:
        for provider, script in providers:
            log.write(f"{utc_now()} provider_started provider={provider}\n")
            log.flush()
            try:
                run_command([script, str(run_dir)], check=True)
                output = run_dir / "verification.md"
                if not output.exists() or output.stat().st_size == 0:
                    raise RuntimeError("provider returned success without verification.md")
            except Exception as error:
                failures.append(provider)
                log.write(
                    f"{utc_now()} provider_failed provider={provider} "
                    f"error_type={type(error).__name__}\n"
                )
                log.flush()
                continue

            status = load_status(status_path)
            status["verification_provider"] = provider
            status["verification_provider_completed_at"] = utc_now()
            status.pop("verification_providers_failed", None)
            save_status(status_path, status)
            log.write(f"{utc_now()} provider_succeeded provider={provider}\n")
            return 0

    status = load_status(status_path)
    status["verification_status"] = "failed"
    status["verification_failed_at"] = utc_now()
    status["verification_providers_failed"] = failures
    save_status(status_path, status)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: generate-verification-with-fallback RUN_DIR", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1])
    if not run_dir.is_dir():
        print(f"run dir does not exist: {run_dir}", file=sys.stderr)
        return 2
    return run_chain(run_dir, provider_chain())


if __name__ == "__main__":
    raise SystemExit(main())
