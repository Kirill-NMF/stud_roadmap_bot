#!/usr/bin/env python3
"""Low-frequency real Telegram E2E smoke for the roadmap bot.

This reuses the proven Telethon pattern from the ShortTalk project, but keeps
roadmap runtime files separate. It may reuse the same Telegram test account
credentials if explicitly configured, so it uses the same global lock name to
avoid concurrent clients for one StringSession.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

try:
    from telethon import TelegramClient, events
    from telethon.errors import FloodWaitError
    from telethon.sessions import StringSession
except ImportError as exc:
    raise SystemExit("Telethon is not installed in this Python environment") from exc


DEFAULT_ENV_FILE = "/root/.telegram/roadmap-e2e.env"
FALLBACK_ENV_FILE = "/opt/shorttalk/.runtime/telegram-e2e.env"
DEFAULT_NOTIFY_SCRIPT = "/usr/local/bin/telegram-roadmap-notify"
DEFAULT_RUNS_DIR = "/var/lib/zoom-audio-pipeline/runs"


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real Telegram E2E smoke for stud_roadmap_bot.")
    parser.add_argument("--env-file", default=os.environ.get("ROADMAP_REAL_TG_ENV_FILE", DEFAULT_ENV_FILE))
    parser.add_argument("--fallback-env-file", default=FALLBACK_ENV_FILE)
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--action", choices=["revise", "approve", "approve_only"], default="revise")
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--max-delay", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--article-timeout", type=float, default=900.0)
    args = parser.parse_args()

    env = {
        **read_env_file(Path(args.fallback_env_file)),
        **read_env_file(Path(args.env_file)),
        **os.environ,
    }
    enabled = env.get("ROADMAP_REAL_TG_ENABLED", env.get("SHORTTALK_REAL_TG_ENABLED", "false")).lower()
    if enabled != "true":
        print("skip - ROADMAP_REAL_TG_ENABLED is not true")
        return 0

    run_dir = args.run_dir or env.get("ROADMAP_REAL_TG_RUN_DIR", "") or latest_run_dir()
    if not run_dir:
        raise RuntimeError("No run dir found for smoke test")

    config = SmokeConfig(
        api_id=required_int(env, "ROADMAP_REAL_TG_API_ID", "SHORTTALK_REAL_TG_API_ID"),
        api_hash=required(env, "ROADMAP_REAL_TG_API_HASH", "SHORTTALK_REAL_TG_API_HASH"),
        session=required(env, "ROADMAP_REAL_TG_STRING_SESSION", "SHORTTALK_REAL_TG_STRING_SESSION"),
        bot_username=env.get("ROADMAP_REAL_TG_BOT_USERNAME", "@stud_roadmap_bot"),
        notify_script=env.get("ROADMAP_NOTIFY_SCRIPT", DEFAULT_NOTIFY_SCRIPT),
        run_dir=run_dir,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        timeout=args.timeout,
        article_timeout=args.article_timeout,
        proxy_url=env.get("ROADMAP_REAL_TG_PROXY_URL", env.get("SHORTTALK_REAL_TG_PROXY_URL", "")),
        action=args.action,
    )

    with single_run_lock():
        asyncio.run(run_smoke(config))
    return 0


class SmokeConfig:
    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        bot_username: str,
        notify_script: str,
        run_dir: str,
        min_delay: float,
        max_delay: float,
        timeout: float,
        article_timeout: float,
        proxy_url: str,
        action: str,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session = session
        self.bot_username = bot_username
        self.notify_script = notify_script
        self.run_dir = run_dir
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.article_timeout = article_timeout
        self.proxy_url = proxy_url
        self.action = action


async def run_smoke(config: SmokeConfig) -> None:
    client = TelegramClient(
        StringSession(config.session),
        config.api_id,
        config.api_hash,
        flood_sleep_threshold=300,
        proxy=parse_proxy(config.proxy_url),
    )
    await client.connect()

    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telethon session is not authorized")

        me = await client.get_me()
        bot = await client.get_entity(config.bot_username)
        marker = "roadmap-e2e-" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))

        await human_delay(config)
        await client.send_message(bot, "/start")
        await human_delay(config)

        subprocess.run(
            [
                config.notify_script,
                "--chat-id",
                str(me.id),
                "--stage",
                "verification_ready",
                "--audio",
                f"{marker}.m4a",
                "--run-dir",
                config.run_dir,
            ],
            check=True,
        )
        print("ok - sent bot notification with inline buttons to test account")

        notification = await wait_bot_message(client, bot, "Проверка перед статьёй готова.", config.timeout)
        if not notification.buttons:
            raise AssertionError("Expected inline buttons on verification notification")
        assert_button_labels(notification, {"Открыть", "Согласен"})
        await human_delay(config)
        if config.action in {"approve", "approve_only"}:
            await click_button(notification, "Согласен")
            print("ok - clicked 'approve' button")
            await wait_bot_message(client, bot, "Принято в работу", config.timeout)
            assert_approval_saved(Path(config.run_dir))
            print("ok - approval status and teacher note saved")
            if config.action == "approve_only":
                print("ok - roadmap Telegram approve-only smoke passed")
                return
            await wait_for_article_created(Path(config.run_dir), config.article_timeout)
            assert_article_created(Path(config.run_dir))
            print("ok - article/html generated after Telegram approval")
            print("ok - roadmap Telegram approve smoke passed")
            return

        await human_delay(config)
        note = f"Telethon smoke revision note: {marker}"
        await client.send_message(bot, note)
        await wait_bot_message(client, bot, "Правки сохранил.", config.timeout)
        assert_note_saved(Path(config.run_dir), marker)
        print("ok - revision note saved and acknowledged")
        print("ok - roadmap Telegram real smoke passed")
    except FloodWaitError as exc:
        raise RuntimeError(f"Telegram FloodWaitError: wait {exc.seconds} seconds before retrying") from exc
    finally:
        await client.disconnect()


async def wait_bot_message(client: TelegramClient, bot, expected_fragment: str, timeout: float):
    future = client.loop.create_future()

    @client.on(events.NewMessage(from_users=bot))
    async def handler(event):
        body = event.raw_text or ""
        if expected_fragment in body and not future.done():
            future.set_result(event.message)

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    finally:
        client.remove_event_handler(handler, events.NewMessage)


async def human_delay(config: SmokeConfig) -> None:
    if config.max_delay < config.min_delay:
        raise ValueError("--max-delay must be greater than or equal to --min-delay")
    await asyncio.sleep(random.uniform(config.min_delay, config.max_delay))


def button_labels(message) -> list[str]:
    labels: list[str] = []
    for row in message.buttons or []:
        for button in row:
            labels.append(str(getattr(button, "text", "")))
    return labels


def assert_button_labels(message, expected: set[str]) -> None:
    labels = set(button_labels(message))
    missing = expected - labels
    if missing:
        raise AssertionError(f"Missing expected buttons: {sorted(missing)}; got {sorted(labels)}")
    forbidden = {"Нужны правки", "Подтвердить", "Подтвердить нужные правки"} & labels
    if forbidden:
        raise AssertionError(f"Unexpected old buttons: {sorted(forbidden)}")


async def click_button(message, label: str) -> None:
    for row_index, row in enumerate(message.buttons or []):
        for button_index, button in enumerate(row):
            if str(getattr(button, "text", "")) == label:
                await message.click(row_index, button_index)
                return
    raise AssertionError(f"Button not found: {label}; got {button_labels(message)}")


def assert_note_saved(run_dir: Path, marker: str) -> None:
    notes = run_dir / "teacher-notes.md"
    status = run_dir / "status.json"
    if marker not in notes.read_text(encoding="utf-8"):
        raise AssertionError(f"Marker was not saved in {notes}")
    data = json.loads(status.read_text(encoding="utf-8"))
    if data.get("teacher_verification_decision") != "approved_for_article":
        raise AssertionError("status.json did not record approved_for_article")
    if data.get("teacher_revision_notes_received") is not True:
        raise AssertionError("status.json did not record teacher_revision_notes_received=True")


def assert_approval_saved(run_dir: Path) -> None:
    notes = run_dir / "teacher-notes.md"
    status = run_dir / "status.json"
    note_text = notes.read_text(encoding="utf-8")
    data = json.loads(status.read_text(encoding="utf-8"))
    if data.get("teacher_verification_decision") != "approved_for_article":
        raise AssertionError("status.json did not record approved_for_article")
    if "цену, оплату, расписание" not in note_text:
        raise AssertionError("approval note did not confirm transcript/verification facts")
    if "PDF-опции P1-P14" not in note_text:
        raise AssertionError("approval note did not protect P1-P14 from auto-inclusion")


def assert_article_created(run_dir: Path) -> None:
    article = run_dir / "roadmap-article.md"
    html = run_dir / "roadmap-article.html"
    status = run_dir / "status.json"
    if not article.exists() or article.stat().st_size == 0:
        raise AssertionError(f"Article was not created: {article}")
    if not html.exists() or html.stat().st_size == 0:
        raise AssertionError(f"HTML was not created: {html}")
    data = json.loads(status.read_text(encoding="utf-8"))
    if data.get("article_status") != "done":
        raise AssertionError("status.json did not record article_status=done")


async def wait_for_article_created(run_dir: Path, timeout: float) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = run_dir / "status.json"
        article = run_dir / "roadmap-article.md"
        html = run_dir / "roadmap-article.html"
        if status.exists() and article.exists() and html.exists():
            try:
                data = json.loads(status.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if data.get("article_status") == "done" and article.stat().st_size > 0 and html.stat().st_size > 0:
                return
        await asyncio.sleep(3)
    raise TimeoutError(f"Timed out waiting for article/html in {run_dir}")


def latest_run_dir() -> str:
    runs_dir = Path(DEFAULT_RUNS_DIR)
    candidates = [p for p in runs_dir.iterdir() if p.is_dir() and (p / "verification.md").exists()]
    if not candidates:
        return ""
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def parse_proxy(proxy_url: str):
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"socks5", "socks4", "http"}:
        raise ValueError("proxy URL must use socks5://, socks4://, or http://")

    try:
        import socks
    except ImportError as exc:
        raise RuntimeError("Proxy support requires PySocks") from exc

    proxy_type = {
        "socks5": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "http": socks.HTTP,
    }[parsed.scheme]
    return (proxy_type, parsed.hostname, parsed.port, True, parsed.username, parsed.password)


@contextmanager
def single_run_lock():
    if os.name == "nt":
        yield
        return

    import fcntl

    lock_path = Path(tempfile.gettempdir()) / "telegram-real-account-e2e.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another real Telegram E2E smoke is already running") from exc
        yield


def required(env: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = env.get(key, "").strip()
        if value:
            return value
    raise RuntimeError(f"One of these env vars is required: {', '.join(keys)}")


def required_int(env: dict[str, str], *keys: str) -> int:
    return int(required(env, *keys))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error - {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
