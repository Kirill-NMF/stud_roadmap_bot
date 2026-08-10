#!/usr/bin/env python3
"""Generate roadmap articles for runs approved through Telegram."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_RUNS_DIR = "/var/lib/zoom-audio-pipeline/runs"
DEFAULT_EVENTS_FILE = "/var/log/zoom-audio-pipeline/events.jsonl"
DEFAULT_ARTICLE_SCRIPT = "/usr/local/bin/generate-article-with-gemini-rewrite"
DEFAULT_NOTIFY_SCRIPT = "/usr/local/bin/telegram-roadmap-notify"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def notify(script_path: str, args: list[str]) -> None:
    if not script_path:
        return
    try:
        subprocess.run([script_path, *args], check=True)
    except Exception as error:
        print(f"telegram_notify_error: {error!r}", file=sys.stderr)


def save_json(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def notify_article_if_needed(
    status_path: Path,
    status: dict[str, Any],
    notify_script: str,
    audio_name: str,
    run_dir: Path,
) -> None:
    if status.get("article_notified_at"):
        return
    notify_args = ["--stage", "article_ready", "--audio", str(audio_name), "--run-dir", str(run_dir)]
    if status.get("telegram_chat_id"):
        notify_args = ["--chat-id", str(status["telegram_chat_id"]), *notify_args]
    notify(notify_script, notify_args)
    status["article_notified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_json(status_path, status)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process Telegram-approved roadmap runs.")
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--article-script", default=DEFAULT_ARTICLE_SCRIPT)
    parser.add_argument("--notify-script", default=DEFAULT_NOTIFY_SCRIPT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    events_path = Path(args.events_file)
    run_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.exists() else []
    print(f"run_dirs={len(run_dirs)}")

    for run_dir in run_dirs:
        status_path = run_dir / "status.json"
        status = load_json(status_path)
        audio_name = status.get("audio_name") or run_dir.name
        decision = status.get("teacher_verification_decision")
        article_status = status.get("article_status")
        if decision != "approved_for_article":
            continue
        if not status.get("audio_path") and not args.force:
            continue
        if article_status == "done" and not args.force:
            notify_article_if_needed(status_path, status, args.notify_script, audio_name, run_dir)
            continue
        if not (run_dir / "verification.md").exists():
            print(f"article_waiting_for_verification: {audio_name} -> {run_dir}")
            continue

        append_event(events_path, {"stage": "article_started", "audio": audio_name, "run_dir": str(run_dir)})
        print(f"article: {audio_name} -> {run_dir / 'roadmap-article.md'}")
        try:
            subprocess.run([args.article_script, str(run_dir)], check=True)
        except Exception as error:
            append_event(events_path, {"stage": "article_error", "audio": audio_name, "run_dir": str(run_dir), "error": repr(error)})
            raise

        status = load_json(status_path)
        append_event(events_path, {
            "stage": "article_done",
            "audio": audio_name,
            "run_dir": str(run_dir),
            "article": status.get("article", str(run_dir / "roadmap-article.md")),
            "html": status.get("html", str(run_dir / "roadmap-article.html")),
        })
        notify_article_if_needed(status_path, status, args.notify_script, audio_name, run_dir)
        print(f"article_done: {audio_name} -> {status.get('article')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
