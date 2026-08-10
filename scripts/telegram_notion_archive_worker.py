#!/usr/bin/env python3
"""Retry-safe Notion archiver for Telegram audio intake."""

from __future__ import annotations

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
import time
from pathlib import Path
from typing import Any


DEFAULT_ENV_FILE = "/root/.notion/notion.env"
DEFAULT_REGISTRY_FILE = "/var/lib/zoom-audio-pipeline/telegram-notion-intake.json"
DEFAULT_EVENTS_FILE = "/var/log/zoom-audio-pipeline/events.jsonl"
DEFAULT_LOCK_FILE = "/var/lib/zoom-audio-pipeline/telegram-notion-archive.lock"
BACKOFF_SECONDS = [60, 300, 900, 3600, 21600]


def load_webhook_module():
    candidates = [
        Path(__file__).with_name("telegram_roadmap_webhook.py"),
        Path("/usr/local/bin/telegram-roadmap-webhook"),
    ]
    for path in candidates:
        if path.exists():
            spec = importlib.util.spec_from_file_location("telegram_roadmap_webhook_for_archive", path)
            if spec is None:
                loader = SourceFileLoader("telegram_roadmap_webhook_for_archive", str(path))
                spec = importlib.util.spec_from_loader("telegram_roadmap_webhook_for_archive", loader)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
    raise RuntimeError("telegram_roadmap_webhook module was not found")


WEBHOOK = load_webhook_module()


def load_env(path: Path) -> dict[str, str]:
    return WEBHOOK.load_env(path)


def load_json(path: Path, default: Any) -> Any:
    return WEBHOOK.load_json(path, default)


def save_json(path: Path, value: Any) -> None:
    WEBHOOK.save_json(path, value)


def append_event(path: Path, event: dict[str, Any]) -> None:
    WEBHOOK.append_event(path, event)


def utc_now() -> str:
    return WEBHOOK.utc_now()


def parse_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        parsed = time.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        return time.mktime(parsed)
    except Exception:
        return 0.0


def next_retry_delay(attempts: int) -> int:
    if attempts <= 0:
        return BACKOFF_SECONDS[0]
    return BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]


def retry_at_from_now(attempts: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + next_retry_delay(attempts)))


def acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w", encoding="utf-8")
    try:
        import fcntl  # type: ignore

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        return handle
    except BlockingIOError:
        handle.close()
        return None
    return handle


def entry_due(entry: dict[str, Any], now: float) -> bool:
    status = str(entry.get("notion_upload_status", ""))
    if status == "pending":
        return True
    if status != "failed_retry_wait":
        return False
    return parse_ts(str(entry.get("next_retry_at") or "")) <= now


def select_entries(files: dict[str, Any], intake_id: str, now: float) -> list[tuple[str, dict[str, Any]]]:
    if intake_id:
        entry = files.get(intake_id)
        return [(intake_id, entry)] if isinstance(entry, dict) and entry_due(entry, now) else []
    selected: list[tuple[str, dict[str, Any]]] = []
    for key, value in files.items():
        if isinstance(value, dict) and entry_due(value, now):
            selected.append((key, value))
    return selected


def archive_entry(config: dict[str, str], entry: dict[str, Any]) -> dict[str, Any]:
    updated = {**entry, "notion_upload_status": "uploading", "updated_at": utc_now()}
    return WEBHOOK.upload_registry_entry_to_notion(config, updated)


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive Telegram intake files to Notion with retry state.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--lock-file", default=DEFAULT_LOCK_FILE)
    parser.add_argument("--intake-id", default="")
    parser.add_argument("--max-items", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=12)
    args = parser.parse_args()

    env = {**load_env(Path(args.env_file)), **os.environ}
    lock_handle = acquire_lock(Path(args.lock_file))
    if lock_handle is None:
        print("processed=0 locked=true")
        return 0
    registry_path = Path(args.registry_file)
    events_path = Path(args.events_file)
    registry = load_json(registry_path, {"files": {}})
    files = registry.setdefault("files", {})
    if not isinstance(files, dict):
        raise SystemExit("registry files must be an object")

    config = {
        "notion_api_key": env.get("NOTION_API_KEY", ""),
        "notion_target": env.get("NOTION_TARGET", ""),
    }
    processed = 0
    for key, entry in select_entries(files, args.intake_id, time.time()):
        if processed >= args.max_items:
            break
        attempts = int(entry.get("notion_upload_attempts") or 0) + 1
        entry["notion_upload_status"] = "uploading"
        entry["notion_upload_attempts"] = attempts
        entry["updated_at"] = utc_now()
        files[key] = entry
        save_json(registry_path, registry)
        append_event(events_path, {
            "stage": "notion_archive_upload_started",
            "intake_id": key,
            "file_name": entry.get("file_name"),
            "attempt": attempts,
        })
        try:
            updated = archive_entry(config, entry)
        except Exception as error:
            entry["notion_upload_status"] = "failed_final" if attempts >= args.max_attempts else "failed_retry_wait"
            entry["last_error"] = repr(error)[:500]
            entry["next_retry_at"] = "" if entry["notion_upload_status"] == "failed_final" else retry_at_from_now(attempts)
            entry["updated_at"] = utc_now()
            files[key] = entry
            save_json(registry_path, registry)
            append_event(events_path, {
                "stage": "notion_archive_upload_failed",
                "intake_id": key,
                "file_name": entry.get("file_name"),
                "attempt": attempts,
                "status": entry["notion_upload_status"],
                "error": entry["last_error"],
            })
            processed += 1
            continue
        updated["notion_upload_attempts"] = attempts
        files[key] = updated
        save_json(registry_path, registry)
        append_event(events_path, {
            "stage": "notion_archive_upload_done",
            "intake_id": key,
            "file_name": updated.get("file_name"),
            "notion_page_id": updated.get("notion_page_id"),
        })
        processed += 1
    lock_handle.close()
    print(f"processed={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
