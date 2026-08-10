#!/usr/bin/env python3
"""Telegram webhook for roadmap pipeline approval buttons and corrections."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


DEFAULT_ENV_FILE = "/root/.telegram/roadmap-bot.env"
DEFAULT_REGISTRY_FILE = "/var/lib/zoom-audio-pipeline/telegram-run-registry.json"
DEFAULT_EVENTS_FILE = "/var/log/zoom-audio-pipeline/events.jsonl"
DEFAULT_VOICE_PYTHON = "/root/codex-audio/nastya-a2/.venv/bin/python"
DEFAULT_VOICE_TRANSCRIBER = "/usr/local/bin/transcribe-telegram-voice"
DEFAULT_VOICE_TRANSCRIBE_TIMEOUT = 900
DEFAULT_NOTION_ENV_FILE = "/root/.notion/notion.env"
DEFAULT_TELEGRAM_INTAKE_DIR = "/var/lib/zoom-audio-pipeline/telegram-intake"
DEFAULT_TELEGRAM_NOTION_INTAKE_STATE = "/var/lib/zoom-audio-pipeline/telegram-notion-intake.json"
DEFAULT_INBOX_DIR = "/var/lib/zoom-audio-pipeline/inbox"
DEFAULT_NOTION_ARCHIVE_WORKER = "/usr/local/bin/telegram-notion-archive-worker"
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
DEFAULT_TELEGRAM_CLOUD_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_LOCAL_BOT_API_ROOT = "/var/lib/telegram-bot-api"
NOTION_UPLOAD_VERSION = "2026-03-11"


class TelegramFileTooLargeError(RuntimeError):
    def __init__(self, file_size: int, max_bytes: int):
        self.file_size = file_size
        self.max_bytes = max_bytes
        super().__init__(f"Telegram file is too large for bot download: {file_size} bytes")


def format_mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def telegram_file_too_large_message(file_size: int, max_bytes: int) -> str:
    return (
        "Файл вижу, но он больше лимита облачного Telegram Bot API: "
        f"размер {format_mb(file_size)}, лимит {format_mb(max_bytes)}.\n\n"
        "Для таких файлов нужен Local Bot API Server или Notion."
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


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


def telegram_api_base_url(value: str | None = None) -> str:
    return (value or DEFAULT_TELEGRAM_API_BASE_URL).rstrip("/")


def is_cloud_telegram_api(api_base_url: str) -> bool:
    return telegram_api_base_url(api_base_url) == DEFAULT_TELEGRAM_API_BASE_URL


def telegram_method_url(token: str, method: str, api_base_url: str | None = None) -> str:
    return f"{telegram_api_base_url(api_base_url)}/bot{token}/{method}"


def telegram_file_url(token: str, file_path: str, api_base_url: str | None = None) -> str:
    return f"{telegram_api_base_url(api_base_url)}/file/bot{token}/{file_path.lstrip('/')}"


def copy_local_bot_api_file(file_path: str, destination: Path, allowed_root: Path) -> bool:
    source = Path(file_path)
    if not source.is_absolute():
        return False
    resolved_source = source.resolve(strict=True)
    resolved_root = allowed_root.resolve(strict=True)
    if resolved_source != resolved_root and resolved_root not in resolved_source.parents:
        raise RuntimeError("Local Bot API returned a file outside the allowed root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".partial")
    tmp.unlink(missing_ok=True)
    with resolved_source.open("rb") as src, tmp.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
    tmp.replace(destination)
    return True


def telegram_request(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    api_base_url: str | None = None,
) -> dict[str, Any]:
    url = telegram_method_url(token, method, api_base_url)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def safe_telegram_request(
    token: str,
    method: str,
    payload: dict[str, Any],
    *,
    api_base_url: str | None = None,
) -> None:
    try:
        telegram_request(token, method, payload, api_base_url=api_base_url)
    except Exception as error:
        print(f"telegram_request_error method={method}: {error!r}", flush=True)


def start_pipeline_async() -> None:
    try:
        subprocess.Popen(
            ["systemctl", "start", "notion-pipeline-poll.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:
        print(f"pipeline_start_error: {error!r}", flush=True)


def start_notion_archive_worker_async(config: dict[str, str], intake_id: str = "") -> None:
    script = config.get("notion_archive_worker", DEFAULT_NOTION_ARCHIVE_WORKER)
    if not script:
        return
    command = [
        script,
        "--registry-file",
        config.get("telegram_notion_intake_state", DEFAULT_TELEGRAM_NOTION_INTAKE_STATE),
        "--env-file",
        config.get("notion_env_file", DEFAULT_NOTION_ENV_FILE),
    ]
    if intake_id:
        command.extend(["--intake-id", intake_id])
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as error:
        print(f"notion_archive_worker_start_error: {error!r}", flush=True)


def download_telegram_file(
    token: str,
    file_id: str,
    destination: Path,
    *,
    max_bytes: int | None = None,
    api_base_url: str | None = None,
    local_bot_api_root: Path | None = None,
) -> None:
    base_url = telegram_api_base_url(api_base_url)
    info = telegram_request(token, "getFile", {"file_id": file_id}, api_base_url=base_url)
    result = info.get("result", {})
    file_path = result.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram getFile did not return file_path")
    file_size = result.get("file_size")
    if max_bytes and isinstance(file_size, int) and file_size > max_bytes:
        raise TelegramFileTooLargeError(file_size, max_bytes)

    if not is_cloud_telegram_api(base_url) and local_bot_api_root:
        if copy_local_bot_api_file(str(file_path), destination, local_bot_api_root):
            return

    url = telegram_file_url(token, str(file_path), base_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=60) as response:
        bytes_written = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if max_bytes and bytes_written > max_bytes:
                    destination.unlink(missing_ok=True)
                    raise TelegramFileTooLargeError(bytes_written, max_bytes)
                handle.write(chunk)


def page_id_from_target(target: str) -> str:
    match = re.search(r"([0-9a-fA-F]{32})", target)
    if not match:
        raise RuntimeError("Could not find a 32-character Notion page id in target")
    raw = match.group(1).lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def notion_json_request(api_key: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + api_key,
        "Notion-Version": NOTION_UPLOAD_VERSION,
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        "https://api.notion.com/v1" + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"Notion API error {error.code}: {body[:500]}") from error


def notion_multipart_file_request(api_key: str, file_upload_id: str, file_path: Path, content_type: str) -> dict[str, Any]:
    boundary = "----CodexRoadmapNotionBoundary" + hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        f"https://api.notion.com/v1/file_uploads/{file_upload_id}/send",
        data=bytes(body),
        headers={
            "Authorization": "Bearer " + api_key,
            "Notion-Version": NOTION_UPLOAD_VERSION,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"Notion file upload error {error.code}: {body[:500]}") from error


def notion_upload_content_type(file_path: Path, declared_mime_type: str = "") -> str:
    content_type = declared_mime_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    normalized = content_type.lower().strip()
    suffix = file_path.suffix.lower()
    if normalized in {"audio/m4a", "audio/x-m4a"} or suffix == ".m4a":
        return "audio/mp4"
    if normalized == "audio/mp3":
        return "audio/mpeg"
    return content_type


def notion_child_page_payload(root_page_id: str, title: str) -> dict[str, Any]:
    return {
        "parent": {"type": "page_id", "page_id": root_page_id},
        "properties": {
            "title": {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": title},
                    }
                ]
            }
        },
    }


def notion_audio_block_payload(file_upload_id: str) -> dict[str, Any]:
    return {
        "children": [
            {
                "object": "block",
                "type": "audio",
                "audio": {
                    "caption": [],
                    "type": "file_upload",
                    "file_upload": {"id": file_upload_id},
                },
            }
        ]
    }


def notion_archive_children_payload(file_upload_id: str, intake_id: str) -> dict[str, Any]:
    payload = notion_audio_block_payload(file_upload_id)
    payload["children"].insert(
        0,
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": f"intake_id: {intake_id}\nsource: telegram"},
                    }
                ]
            },
        },
    )
    return payload


def safe_filename(name: str, fallback: str = "telegram-audio.m4a") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
    return cleaned or fallback


def unique_intake_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique intake path for {filename}")


def copy_to_inbox(source: Path, inbox_dir: Path, filename: str) -> Path:
    target = unique_intake_path(inbox_dir, filename)
    target.write_bytes(source.read_bytes())
    return target


def intake_sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_name(audio_path.name + ".telegram-intake.json")


def write_intake_sidecar(inbox_path: Path, entry: dict[str, Any]) -> Path:
    sidecar = intake_sidecar_path(inbox_path)
    payload = {
        key: entry.get(key)
        for key in (
            "intake_id",
            "source",
            "file_name",
            "local_path",
            "inbox_path",
            "telegram_chat_id",
            "telegram_file_unique_id",
        )
        if entry.get(key) is not None
    }
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def telegram_intake_id(audio: dict[str, Any], local_path: Path | None = None) -> str:
    file_unique_id = str(audio.get("file_unique_id") or "").strip()
    if file_unique_id:
        return "telegram:" + file_unique_id
    if local_path and local_path.exists():
        digest = hashlib.sha256(local_path.read_bytes()).hexdigest()
        return "sha256:" + digest
    file_id = str(audio.get("file_id") or "").strip()
    if file_id:
        return "telegram-file:" + hashlib.sha256(file_id.encode("utf-8")).hexdigest()
    raise RuntimeError("Cannot build intake id for Telegram audio")


def registry_entry_blocks_pipeline(entry: dict[str, Any]) -> bool:
    return str(entry.get("pipeline_status", "")) in {"pipeline_started", "pipeline_done", "pipeline_failed"}


def extract_audio_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("voice"):
        voice = message["voice"]
        return {
            "kind": "voice",
            "file_id": voice.get("file_id", ""),
            "file_unique_id": voice.get("file_unique_id", ""),
            "file_name": "telegram-voice.oga",
            "mime_type": "audio/ogg",
            "file_size": voice.get("file_size"),
        }
    if message.get("audio"):
        audio = message["audio"]
        return {
            "kind": "audio",
            "file_id": audio.get("file_id", ""),
            "file_unique_id": audio.get("file_unique_id", ""),
            "file_name": audio.get("file_name") or "telegram-audio.mp3",
            "mime_type": audio.get("mime_type") or "audio/mpeg",
            "file_size": audio.get("file_size"),
        }
    document = message.get("document")
    if document and str(document.get("mime_type", "")).startswith("audio/"):
        return {
            "kind": "document",
            "file_id": document.get("file_id", ""),
            "file_unique_id": document.get("file_unique_id", ""),
            "file_name": document.get("file_name") or "telegram-audio",
            "mime_type": document.get("mime_type") or "application/octet-stream",
            "file_size": document.get("file_size"),
        }
    return None


def accept_audio_message_for_pipeline(config: dict[str, str], token: str, message: dict[str, Any]) -> dict[str, Any]:
    audio = extract_audio_message(message)
    if not audio or not audio.get("file_id"):
        raise RuntimeError("No Telegram audio file found")
    api_base_url = telegram_api_base_url(config.get("telegram_api_base_url"))
    max_bytes = None
    if is_cloud_telegram_api(api_base_url):
        max_bytes = int(config.get("telegram_cloud_max_download_bytes", str(DEFAULT_TELEGRAM_CLOUD_MAX_DOWNLOAD_BYTES)))
    file_size = audio.get("file_size")
    if max_bytes and isinstance(file_size, int) and file_size > max_bytes:
        raise TelegramFileTooLargeError(file_size, max_bytes)

    state_path = Path(config.get("telegram_notion_intake_state", DEFAULT_TELEGRAM_NOTION_INTAKE_STATE))
    state = load_json(state_path, {"files": {}})
    files = state.setdefault("files", {})
    preliminary_intake_id = telegram_intake_id(audio)
    if preliminary_intake_id in files:
        return {**files[preliminary_intake_id], "status": "duplicate"}

    filename = safe_filename(str(audio.get("file_name") or "telegram-audio.m4a"))
    local_path = unique_intake_path(Path(config.get("telegram_intake_dir", DEFAULT_TELEGRAM_INTAKE_DIR)), filename)
    download_telegram_file(
        token,
        str(audio["file_id"]),
        local_path,
        max_bytes=max_bytes,
        api_base_url=api_base_url,
        local_bot_api_root=Path(config.get("local_bot_api_root", DEFAULT_LOCAL_BOT_API_ROOT)),
    )
    intake_id = telegram_intake_id(audio, local_path)
    if intake_id in files:
        local_path.unlink(missing_ok=True)
        return {**files[intake_id], "status": "duplicate"}

    inbox_path = copy_to_inbox(local_path, Path(config.get("inbox_dir", DEFAULT_INBOX_DIR)), local_path.name)
    now = utc_now()
    result = {
        "status": "accepted",
        "source": "telegram",
        "intake_id": intake_id,
        "file_name": local_path.name,
        "local_path": str(local_path),
        "inbox_path": str(inbox_path),
        "mime_type": str(audio.get("mime_type") or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"),
        "telegram_chat_id": str(message.get("chat", {}).get("id") or ""),
        "telegram_file_unique_id": str(audio.get("file_unique_id") or ""),
        "pipeline_status": "pipeline_started",
        "pipeline_started_at": now,
        "notion_upload_status": "pending",
        "notion_upload_attempts": 0,
        "created_at": now,
        "updated_at": now,
    }
    write_intake_sidecar(inbox_path, result)
    files[intake_id] = result
    save_json(state_path, state)
    return result


def upload_registry_entry_to_notion(config: dict[str, str], entry: dict[str, Any]) -> dict[str, Any]:
    local_path = Path(str(entry.get("local_path") or ""))
    if not local_path.exists():
        raise RuntimeError(f"Local file for Notion archive is missing: {local_path}")
    intake_id = str(entry.get("intake_id") or "")
    if not intake_id:
        raise RuntimeError("intake_id is required for Notion archive")

    notion_api_key = config.get("notion_api_key") or os.environ.get("NOTION_API_KEY", "")
    notion_target = config.get("notion_target") or os.environ.get("NOTION_TARGET", "")
    if not notion_api_key or not notion_target:
        raise RuntimeError("NOTION_API_KEY and NOTION_TARGET are required for Telegram intake")

    content_type = notion_upload_content_type(local_path, str(entry.get("mime_type") or ""))
    upload = notion_json_request(
        notion_api_key,
        "POST",
        "/file_uploads",
        {"mode": "single_part", "filename": local_path.name, "content_type": content_type},
    )
    file_upload_id = upload.get("id")
    if not file_upload_id:
        raise RuntimeError("Notion did not return file_upload id")
    sent = notion_multipart_file_request(notion_api_key, str(file_upload_id), local_path, content_type)
    if sent.get("status") != "uploaded":
        raise RuntimeError(f"Notion upload did not finish: {sent.get('status')}")

    root_page_id = page_id_from_target(notion_target)
    page = notion_json_request(notion_api_key, "POST", "/pages", notion_child_page_payload(root_page_id, local_path.name))
    page_id = page.get("id")
    if not page_id:
        raise RuntimeError("Notion did not return created page id")
    notion_json_request(notion_api_key, "PATCH", f"/blocks/{page_id}/children", notion_archive_children_payload(str(file_upload_id), intake_id))

    result = {
        **entry,
        "status": "accepted",
        "file_name": local_path.name,
        "local_path": str(local_path),
        "notion_page_id": str(page_id),
        "notion_file_upload_id": str(file_upload_id),
        "notion_upload_status": "uploaded",
        "notion_uploaded_at": utc_now(),
        "updated_at": utc_now(),
    }
    return result


def upload_audio_message_to_notion(config: dict[str, str], token: str, message: dict[str, Any]) -> dict[str, Any]:
    result = accept_audio_message_for_pipeline(config, token, message)
    if result.get("status") == "duplicate":
        return result
    uploaded = upload_registry_entry_to_notion(config, result)
    state_path = Path(config.get("telegram_notion_intake_state", DEFAULT_TELEGRAM_NOTION_INTAKE_STATE))
    state = load_json(state_path, {"files": {}})
    state.setdefault("files", {})[str(uploaded["intake_id"])] = uploaded
    save_json(state_path, state)
    return uploaded


def transcribe_voice(config: dict[str, str], audio_path: Path) -> str:
    python = config.get("voice_python", DEFAULT_VOICE_PYTHON)
    script = config.get("voice_transcriber", DEFAULT_VOICE_TRANSCRIBER)
    timeout = int(config.get("voice_transcribe_timeout", str(DEFAULT_VOICE_TRANSCRIBE_TIMEOUT)))
    result = subprocess.run(
        [python, script, str(audio_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def is_approval_text(text: str) -> bool:
    normalized = text.strip().lower().replace("ё", "е")
    normalized = re.sub(r"[^\w\s]+", " ", normalized, flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in {
        "согласен",
        "совсем согласен",
        "согласна",
        "совсем согласна",
        "ок",
        "окей",
        "все ок",
        "все верно",
        "утверждаю",
        "подтверждаю",
        "делай статью",
        "генерируй",
        "запускай",
    }


def append_teacher_note(run_dir: Path, source: str, text: str) -> Path:
    notes_path = run_dir / "teacher-notes.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    with notes_path.open("a", encoding="utf-8") as handle:
        if notes_path.stat().st_size == 0:
            handle.write("# Правки преподавателя\n\n")
        handle.write(f"## {utc_now()} ({source})\n\n{text}\n\n")
    return notes_path


def approval_teacher_note() -> str:
    return (
        "Совсем согласен. Подтверждаю все факты, которые уже есть в проверке и "
        "транскрипте созвона: цену, оплату, расписание, дни недели, время занятий, "
        "формат, платформу, сроки и результаты, если они там указаны. "
        "Дополнительные PDF-опции P1-P14, которых не было в созвоне, не включать, "
        "если я не назвал их отдельно."
    )


def mark_approved_and_start(
    run_dir: Path,
    audio: str,
    chat_id: int | str | None,
    key: str,
    status_path: Path,
    events_path: Path | None = None,
) -> str:
    status = load_json(status_path, {})
    article_status = str(status.get("article_status", ""))
    if article_status == "done":
        return "Уже принято: статья-roadmap готова. Сейчас пришлю финальные файлы, если они есть на сервере."
    if article_status == "started":
        return "Уже принято в работу. Статья-roadmap сейчас генерируется, пришлю HTML и PDF, когда всё будет готово."

    notes_path = append_teacher_note(run_dir, "approval", approval_teacher_note())
    status["teacher_verification_decision"] = "approved_for_article"
    status["teacher_verification_decision_at"] = utc_now()
    status["teacher_notes"] = str(notes_path)
    status["teacher_notes_updated_at"] = utc_now()
    status["telegram_callback_key"] = key
    if chat_id:
        status["telegram_chat_id"] = str(chat_id)
    save_json(status_path, status)
    append_event(events_path or Path(os.environ.get("ROADMAP_EVENTS_FILE", DEFAULT_EVENTS_FILE)), {
        "stage": "verification_approve",
        "audio": audio,
        "run_dir": str(run_dir),
        "decision": "approved_for_article",
    })
    start_pipeline_async()
    return "Принято в работу. Генерирую статью-roadmap, пришлю HTML и PDF, когда всё будет готово."


def correction_text_from_message(config: dict[str, str], token: str, run_dir: Path, message: dict[str, Any]) -> tuple[str, str]:
    text = str(message.get("text", "")).strip()
    if text:
        return text, "text"

    file_id = ""
    extension = ".oga"
    if message.get("voice"):
        file_id = message["voice"].get("file_id", "")
        extension = ".oga"
    elif message.get("audio"):
        file_id = message["audio"].get("file_id", "")
        extension = Path(message["audio"].get("file_name") or "audio.mp3").suffix or ".mp3"
    elif message.get("document") and str(message["document"].get("mime_type", "")).startswith("audio/"):
        file_id = message["document"].get("file_id", "")
        extension = Path(message["document"].get("file_name") or "audio.oga").suffix or ".oga"

    if not file_id:
        return "", ""

    voice_path = run_dir / "telegram-voice-notes" / f"{safe_stamp()}-{file_id[:10]}{extension}"
    api_base_url = telegram_api_base_url(config.get("telegram_api_base_url"))
    max_bytes = None
    if is_cloud_telegram_api(api_base_url):
        max_bytes = int(config.get("telegram_cloud_max_download_bytes", str(DEFAULT_TELEGRAM_CLOUD_MAX_DOWNLOAD_BYTES)))
    download_telegram_file(
        token,
        file_id,
        voice_path,
        max_bytes=max_bytes,
        api_base_url=api_base_url,
        local_bot_api_root=Path(config.get("local_bot_api_root", DEFAULT_LOCAL_BOT_API_ROOT)),
    )
    try:
        transcript = transcribe_voice(config, voice_path)
    except Exception as error:
        transcript = f"[Голосовая правка сохранена, но не расшифрована автоматически: {voice_path}. Ошибка: {error!r}]"
    if not transcript:
        transcript = f"[Голосовая правка сохранена: {voice_path}. Текст не распознан.]"
    return transcript, "voice"


def make_handler(config: dict[str, str]):
    token = config["token"]
    secret = config["secret"]
    registry_file = Path(config["registry_file"])
    events_file = Path(config["events_file"])
    api_base_url = telegram_api_base_url(config.get("telegram_api_base_url"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "RoadmapTelegramWebhook/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            print("%s - %s" % (self.address_string(), fmt % args), flush=True)

        def send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/roadmap-telegram/health":
                self.send_json(200, {"ok": True})
                return
            self.send_json(404, {"ok": False})

        def do_POST(self) -> None:
            if self.path != "/roadmap-telegram/webhook":
                self.send_json(404, {"ok": False})
                return
            if self.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
                self.send_json(403, {"ok": False})
                return
            length = int(self.headers.get("Content-Length", "0"))
            update = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            self.handle_update(update)
            self.send_json(200, {"ok": True})

        def handle_update(self, update: dict[str, Any]) -> None:
            if update.get("message"):
                self.handle_message(update["message"])
                return
            callback = update.get("callback_query")
            if callback:
                self.handle_callback(callback)

        def handle_callback(self, callback: dict[str, Any]) -> None:
            data = str(callback.get("data", ""))
            parts = data.split(":")
            if len(parts) != 3 or parts[0] != "roadmap":
                return
            action, key = parts[1], parts[2]
            registry = load_json(registry_file, {"runs": {}})
            item = registry.get("runs", {}).get(key)
            callback_id = callback.get("id")
            message = callback.get("message", {})
            chat_id = message.get("chat", {}).get("id") or callback.get("from", {}).get("id")
            if not item:
                if callback_id:
                    safe_telegram_request(
                        token,
                        "answerCallbackQuery",
                        {"callback_query_id": callback_id, "text": "Run не найден"},
                        api_base_url=api_base_url,
                    )
                if chat_id:
                    safe_telegram_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": "Не нашёл этот запуск. Открой свежую проверку и нажми «Согласен» там.",
                        "disable_web_page_preview": True,
                    }, api_base_url=api_base_url)
                return

            run_dir = Path(item["run_dir"])
            audio = item.get("audio", run_dir.name)
            status_path = run_dir / "status.json"
            decision_map = {
                "approve": ("approved_for_article", "Принято в работу."),
            }
            decision, reply = decision_map.get(action, ("unknown", "Неизвестное действие"))
            if action != "approve":
                if callback_id:
                    safe_telegram_request(
                        token,
                        "answerCallbackQuery",
                        {"callback_query_id": callback_id, "text": reply},
                        api_base_url=api_base_url,
                    )
                return
            reply = mark_approved_and_start(run_dir, audio, chat_id, key, status_path, events_file)

            registry.get("pending_reviews", {}).pop(str(chat_id), None)
            save_json(registry_file, registry)

            if callback_id:
                safe_telegram_request(
                    token,
                    "answerCallbackQuery",
                    {"callback_query_id": callback_id, "text": reply},
                    api_base_url=api_base_url,
                )
            if chat_id:
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "\n".join([reply, f"Файл: {audio}"]),
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)

        def handle_message(self, message: dict[str, Any]) -> None:
            chat_id = message.get("chat", {}).get("id")
            if not chat_id:
                return

            registry = load_json(registry_file, {"runs": {}})
            pending = registry.get("pending_reviews", {}).get(str(chat_id))
            if not pending:
                audio = extract_audio_message(message)
                if not audio:
                    return
                max_bytes = None
                if is_cloud_telegram_api(api_base_url):
                    max_bytes = int(config.get(
                        "telegram_cloud_max_download_bytes",
                        str(DEFAULT_TELEGRAM_CLOUD_MAX_DOWNLOAD_BYTES),
                    ))
                file_size = audio.get("file_size")
                if max_bytes and isinstance(file_size, int) and file_size > max_bytes:
                    append_event(events_file, {
                        "stage": "telegram_intake_rejected",
                        "chat_id": str(chat_id),
                        "reason": "file_too_large",
                        "file_size": file_size,
                        "max_bytes": max_bytes,
                    })
                    safe_telegram_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": telegram_file_too_large_message(file_size, max_bytes),
                        "disable_web_page_preview": True,
                    }, api_base_url=api_base_url)
                    return
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "Аудио получил. Запускаю pipeline, а файл отдельно архивирую в Notion.",
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)
                try:
                    result = accept_audio_message_for_pipeline(config, token, message)
                except TelegramFileTooLargeError as error:
                    append_event(events_file, {
                        "stage": "telegram_intake_rejected",
                        "chat_id": str(chat_id),
                        "reason": "file_too_large",
                        "file_size": error.file_size,
                        "max_bytes": error.max_bytes,
                    })
                    safe_telegram_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": telegram_file_too_large_message(error.file_size, error.max_bytes),
                        "disable_web_page_preview": True,
                    }, api_base_url=api_base_url)
                    return
                except Exception as error:
                    append_event(events_file, {
                        "stage": "telegram_intake_failed",
                        "chat_id": str(chat_id),
                        "error": repr(error),
                    })
                    safe_telegram_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": (
                            "Не смог принять аудио в pipeline. "
                            "Файл не запущен, нужно проверить серверную ошибку."
                        ),
                        "disable_web_page_preview": True,
                    }, api_base_url=api_base_url)
                    return
                append_event(events_file, {
                    "stage": "telegram_intake_accepted",
                    "chat_id": str(chat_id),
                    "intake_id": result.get("intake_id"),
                    "file_name": result.get("file_name"),
                    "local_path": result.get("local_path"),
                    "inbox_path": result.get("inbox_path"),
                    "status": result.get("status"),
                })
                if result.get("status") == "duplicate":
                    text = "Этот файл уже был принят раньше. Второй pipeline не запускаю."
                else:
                    start_pipeline_async()
                    start_notion_archive_worker_async(config, str(result.get("intake_id") or ""))
                    text = "Файл принят. Pipeline запущен, Notion-архивация идёт отдельно."
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "\n".join([text, f"Файл: {result.get('file_name', 'audio')}"]),
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)
                return

            run_dir = Path(pending["run_dir"])
            audio = pending.get("audio", run_dir.name)
            if message.get("voice") or message.get("audio") or (
                message.get("document") and str(message["document"].get("mime_type", "")).startswith("audio/")
            ):
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "\n".join([
                        "Голосовое получил. Сейчас расшифрую правку и возьму статью-roadmap в работу.",
                        f"Файл: {audio}",
                    ]),
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)
            text, source = correction_text_from_message(config, token, run_dir, message)
            if not text:
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "Я жду текстовую или голосовую правку по текущему анализу.",
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)
                return
            if text.startswith("/") and source == "text":
                return
            key = pending.get("run_key", "")
            status_path = run_dir / "status.json"

            if is_approval_text(text):
                reply = mark_approved_and_start(run_dir, audio, chat_id, key, status_path, events_file)
                registry.get("pending_reviews", {}).pop(str(chat_id), None)
                save_json(registry_file, registry)
                safe_telegram_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": "\n".join([reply, f"Файл: {audio}"]),
                    "disable_web_page_preview": True,
                }, api_base_url=api_base_url)
                return

            notes_path = append_teacher_note(run_dir, source, text)

            status = load_json(status_path, {})
            status["teacher_verification_decision"] = "approved_for_article"
            status["teacher_revision_notes_received"] = True
            status["teacher_notes"] = str(notes_path)
            status["teacher_notes_updated_at"] = utc_now()
            status["teacher_verification_decision_at"] = utc_now()
            status["telegram_chat_id"] = str(chat_id)
            save_json(status_path, status)

            registry.get("pending_reviews", {}).pop(str(chat_id), None)
            save_json(registry_file, registry)
            append_event(events_file, {
                "stage": "verification_revision_notes_received",
                "audio": audio,
                "run_dir": str(run_dir),
                "teacher_notes": str(notes_path),
                "source": source,
            })
            start_pipeline_async()
            safe_telegram_request(token, "sendMessage", {
                "chat_id": chat_id,
                "text": "\n".join([
                    "Правки сохранил. Запускаю статью-roadmap с учётом этих правок, пришлю HTML и PDF, когда всё будет готово.",
                    f"Файл: {audio}",
                    "",
                    text[:2500],
                ]),
                "disable_web_page_preview": True,
            }, api_base_url=api_base_url)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Telegram roadmap webhook server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--notion-env-file", default=DEFAULT_NOTION_ENV_FILE)
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--telegram-intake-dir", default=DEFAULT_TELEGRAM_INTAKE_DIR)
    parser.add_argument("--telegram-notion-intake-state", default=DEFAULT_TELEGRAM_NOTION_INTAKE_STATE)
    parser.add_argument("--inbox-dir", default=DEFAULT_INBOX_DIR)
    parser.add_argument("--notion-archive-worker", default=DEFAULT_NOTION_ARCHIVE_WORKER)
    parser.add_argument("--telegram-api-base-url", default=DEFAULT_TELEGRAM_API_BASE_URL)
    parser.add_argument("--telegram-cloud-max-download-mb", type=int, default=20)
    parser.add_argument("--local-bot-api-root", default=DEFAULT_LOCAL_BOT_API_ROOT)
    args = parser.parse_args()

    env = {**load_env(Path(args.notion_env_file)), **load_env(Path(args.env_file)), **os.environ}
    token = env.get("TELEGRAM_BOT_TOKEN")
    secret = env.get("TELEGRAM_WEBHOOK_SECRET")
    if not token or not secret:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET are required")

    handler = make_handler({
        "token": token,
        "secret": secret,
        "registry_file": args.registry_file,
        "events_file": args.events_file,
        "voice_python": env.get("TELEGRAM_VOICE_TRANSCRIBE_PYTHON", DEFAULT_VOICE_PYTHON),
        "voice_transcriber": env.get("TELEGRAM_VOICE_TRANSCRIBER", DEFAULT_VOICE_TRANSCRIBER),
        "voice_transcribe_timeout": env.get("TELEGRAM_VOICE_TRANSCRIBE_TIMEOUT", str(DEFAULT_VOICE_TRANSCRIBE_TIMEOUT)),
        "notion_api_key": env.get("NOTION_API_KEY", ""),
        "notion_target": env.get("NOTION_TARGET", ""),
        "notion_env_file": args.notion_env_file,
        "telegram_intake_dir": args.telegram_intake_dir,
        "telegram_notion_intake_state": args.telegram_notion_intake_state,
        "inbox_dir": args.inbox_dir,
        "notion_archive_worker": args.notion_archive_worker,
        "telegram_api_base_url": env.get("TELEGRAM_API_BASE_URL", args.telegram_api_base_url),
        "telegram_cloud_max_download_bytes": str(
            int(env.get("TELEGRAM_CLOUD_MAX_DOWNLOAD_MB", str(args.telegram_cloud_max_download_mb))) * 1024 * 1024
        ),
        "local_bot_api_root": env.get("LOCAL_BOT_API_ROOT", args.local_bot_api_root),
    })
    server = HTTPServer((args.host, args.port), handler)
    print(f"telegram roadmap webhook listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
