#!/usr/bin/env python3
"""Merge existing single-purpose env files into /etc/zoom-audio-pipeline/pipeline.env."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_TARGET = Path("/etc/zoom-audio-pipeline/pipeline.env")
DEFAULT_SOURCES = [
    Path("/root/.telegram/roadmap-bot.env"),
    Path("/root/.notion/notion.env"),
    Path("/root/.openrouter/openrouter.env"),
    Path("/opt/shorttalk/.runtime/telegram-e2e.env"),
]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def replace_or_append(lines: list[str], updates: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        key = ""
        if stripped and not stripped.startswith("#") and "=" in stripped:
            body = stripped[len("export "):] if stripped.startswith("export ") else stripped
            key = body.split("=", 1)[0].strip()
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(raw_line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8081")
    args = parser.parse_args()

    target: Path = args.target
    source_paths = args.source or DEFAULT_SOURCES
    current = load_env(target)
    sources: dict[str, str] = {}
    for source in source_paths:
        sources.update(load_env(source))

    updates = {
        "TELEGRAM_BOT_TOKEN": sources.get("TELEGRAM_BOT_TOKEN", current.get("TELEGRAM_BOT_TOKEN", "")),
        "TELEGRAM_WEBHOOK_SECRET": sources.get("TELEGRAM_WEBHOOK_SECRET", current.get("TELEGRAM_WEBHOOK_SECRET", "")),
        "TELEGRAM_CHAT_ID": sources.get("TELEGRAM_CHAT_ID", current.get("TELEGRAM_CHAT_ID", "")),
        "NOTION_API_KEY": sources.get("NOTION_API_KEY", current.get("NOTION_API_KEY", "")),
        "NOTION_TARGET": sources.get("NOTION_TARGET", current.get("NOTION_TARGET", "")),
        "OPENROUTER_API_KEY": sources.get("OPENROUTER_API_KEY", current.get("OPENROUTER_API_KEY", "")),
        "ROADMAP_PUBLIC_BASE_URL": current.get("ROADMAP_PUBLIC_BASE_URL") or "https://dev.short-talk.space/roadmap-reader",
        "ROADMAP_PUBLIC_ROOT": current.get("ROADMAP_PUBLIC_ROOT") or "/var/www/roadmap-reader",
        "TELEGRAM_API_BASE_URL": args.api_base_url,
        "TELEGRAM_CLOUD_MAX_DOWNLOAD_MB": current.get("TELEGRAM_CLOUD_MAX_DOWNLOAD_MB") or "20",
        "LOCAL_BOT_API_ROOT": "/var/lib/telegram-bot-api",
        "TELEGRAM_LOCAL_API_ID": sources.get("SHORTTALK_REAL_TG_API_ID", current.get("TELEGRAM_LOCAL_API_ID", "")),
        "TELEGRAM_LOCAL_API_HASH": sources.get("SHORTTALK_REAL_TG_API_HASH", current.get("TELEGRAM_LOCAL_API_HASH", "")),
        "TELEGRAM_LOCAL_API_PORT": "8081",
    }
    missing = [key for key, value in updates.items() if not value]
    if missing:
        print("missing required keys: " + ", ".join(missing))
        return 2

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".bak." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        shutil.copy2(target, backup)
    lines = target.read_text(errors="ignore").splitlines() if target.exists() else []
    target.write_text("\n".join(replace_or_append(lines, updates)).rstrip() + "\n")
    target.chmod(0o600)
    print(f"configured_keys={len(updates)}")
    print("missing=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
