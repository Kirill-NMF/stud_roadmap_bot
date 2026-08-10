#!/usr/bin/env python3
"""Clean Telegram intake files only after pipeline and Notion archive are done."""

from __future__ import annotations

import argparse
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import time
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY_FILE = "/var/lib/zoom-audio-pipeline/telegram-notion-intake.json"
DEFAULT_AUDIO_STATE_FILE = "/var/lib/zoom-audio-pipeline/audio-process-state.json"
DEFAULT_EVENTS_FILE = "/var/log/zoom-audio-pipeline/events.jsonl"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None:
        loader = SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def parse_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def file_age_days(path: Path, now: float) -> float:
    if not path.exists():
        return 10**9
    return (now - path.stat().st_mtime) / 86400


def update_pipeline_status_from_audio_state(
    entry: dict[str, Any],
    audio_state: dict[str, Any],
    process_audio_module,
) -> dict[str, Any]:
    inbox_path = Path(str(entry.get("inbox_path") or ""))
    if not inbox_path.exists():
        return entry
    try:
        key = process_audio_module.file_key(inbox_path)
    except Exception:
        return entry
    processed = audio_state.get("processed", {}) if isinstance(audio_state, dict) else {}
    item = processed.get(key) if isinstance(processed, dict) else None
    if not isinstance(item, dict):
        return entry
    status = str(item.get("status") or "")
    run_dir = Path(str(item.get("run_dir") or ""))
    if status == "transcribed" and (run_dir / "verification.md").exists():
        entry["pipeline_status"] = "pipeline_done"
        entry["pipeline_run_dir"] = str(run_dir)
        entry["pipeline_done_at"] = entry.get("pipeline_done_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    elif status == "error":
        entry["pipeline_status"] = "pipeline_failed"
        entry["pipeline_run_dir"] = str(run_dir) if str(run_dir) else entry.get("pipeline_run_dir", "")
    return entry


def cleanup_entry(entry: dict[str, Any], *, now: float, min_age_days: float, dry_run: bool) -> list[str]:
    if entry.get("pipeline_status") != "pipeline_done":
        return []
    if entry.get("notion_upload_status") != "uploaded":
        return []
    deleted: list[str] = []
    for key in ("local_path", "inbox_path"):
        path = Path(str(entry.get(key) or ""))
        if not path.exists() or file_age_days(path, now) < min_age_days:
            continue
        deleted.append(str(path))
        if not dry_run:
            path.unlink(missing_ok=True)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean Telegram intake files after safe archive.")
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--audio-state-file", default=DEFAULT_AUDIO_STATE_FILE)
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--process-audio-script", default="/usr/local/bin/process-new-audio")
    parser.add_argument("--min-age-days", type=float, default=7.0)
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Default is dry-run.")
    args = parser.parse_args()

    process_script = Path(args.process_audio_script)
    if not process_script.exists():
        local = Path(__file__).with_name("process_new_audio.py")
        process_script = local if local.exists() else process_script
    process_audio = load_module("process_new_audio_for_cleanup", process_script)

    registry_path = Path(args.registry_file)
    registry = load_json(registry_path, {"files": {}})
    audio_state = load_json(Path(args.audio_state_file), {"processed": {}})
    files = registry.setdefault("files", {})
    if not isinstance(files, dict):
        raise SystemExit("registry files must be an object")
    now = time.time()
    dry_run = not args.apply
    deleted_count = 0
    for intake_id, entry in list(files.items()):
        if not isinstance(entry, dict):
            continue
        entry = update_pipeline_status_from_audio_state(entry, audio_state, process_audio)
        deleted = cleanup_entry(entry, now=now, min_age_days=args.min_age_days, dry_run=dry_run)
        if deleted:
            entry["cleanup_status"] = "dry_run" if dry_run else "deleted"
            entry["cleanup_paths"] = deleted
            entry["cleanup_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            append_event(Path(args.events_file), {
                "stage": "telegram_intake_cleanup",
                "intake_id": intake_id,
                "file_name": entry.get("file_name"),
                "dry_run": dry_run,
                "paths": deleted,
            })
            deleted_count += len(deleted)
        files[intake_id] = entry
    save_json(registry_path, registry)
    print(f"dry_run={dry_run} deleted_candidates={deleted_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
