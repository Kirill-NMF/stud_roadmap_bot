#!/usr/bin/env python3
"""Pull audio-like files from a Notion page tree.

Reads NOTION_API_KEY and NOTION_TARGET from /root/.notion/notion.env by default.
Downloads audio/file/video blocks and files-property attachments from the target
page and child pages. Keeps a local state file to avoid re-downloading the same
Notion attachment unless --force is passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


NOTION_VERSION = "2022-06-28"
ATTACHMENT_BLOCK_TYPES = {"audio", "file", "video"}
DEFAULT_ENV_FILE = "/root/.notion/notion.env"
DEFAULT_OUTPUT_DIR = "/var/lib/zoom-audio-pipeline/inbox"
DEFAULT_STATE_FILE = "/var/lib/zoom-audio-pipeline/notion-pull-state.json"
DEFAULT_INTAKE_REGISTRY_FILE = "/var/lib/zoom-audio-pipeline/telegram-notion-intake.json"


@dataclass
class Attachment:
    source_id: str
    source_kind: str
    page_id: str
    page_title: str
    block_type: str
    file_type: str
    name: str
    url: str
    expiry_time: str | None


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def page_id_from_target(target: str) -> str:
    match = re.search(r"([0-9a-fA-F]{32})", target)
    if not match:
        raise SystemExit("Could not find a 32-character Notion page/database id in target")
    raw = match.group(1).lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def notion_get(path_or_url: str, api_key: str) -> dict[str, Any]:
    url = path_or_url
    if not url.startswith("https://"):
        url = "https://api.notion.com/v1" + path_or_url
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + api_key,
            "Notion-Version": NOTION_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise SystemExit(f"Notion API error {error.code}: {body}") from error


def plain_text(rich_text: list[dict[str, Any]] | None) -> str:
    return "".join(item.get("plain_text", "") for item in (rich_text or []))


def title_from_page(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            title = plain_text(prop.get("title"))
            if title.strip():
                return title.strip()
    return "notion-audio"


def load_intake_skip_page_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    files = data.get("files", {}) if isinstance(data, dict) else {}
    if not isinstance(files, dict):
        return set()
    page_ids: set[str] = set()
    for item in files.values():
        if isinstance(item, dict) and item.get("source") == "telegram":
            page_id = str(item.get("notion_page_id") or "")
            if page_id:
                page_ids.add(page_id)
    return page_ids


def block_plain_text(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    payload = block.get(block_type, {}) if block_type else {}
    if not isinstance(payload, dict):
        return ""
    return plain_text(payload.get("rich_text")) or plain_text(payload.get("caption"))


def has_telegram_intake_marker(page_id: str, api_key: str) -> bool:
    data = notion_get(f"/blocks/{page_id}/children?page_size=10", api_key)
    for block in data.get("results", []):
        text = block_plain_text(block).lower()
        if "intake_id:" in text and "source: telegram" in text:
            return True
    return False


def safe_filename(name: str, fallback_suffix: str = ".m4a") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip().strip(".")
    if not cleaned:
        cleaned = "notion-audio"
    if not Path(cleaned).suffix:
        cleaned += fallback_suffix
    return cleaned


def attachment_from_file_obj(
    *,
    source_id: str,
    source_kind: str,
    page_id: str,
    page_title: str,
    block_type: str,
    name: str | None,
    file_obj: dict[str, Any],
) -> Attachment | None:
    file_type = file_obj.get("type")
    if file_type not in {"file", "external"}:
        return None
    payload = file_obj.get(file_type, {})
    url = payload.get("url")
    if not url:
        return None
    expiry_time = payload.get("expiry_time")
    return Attachment(
        source_id=source_id,
        source_kind=source_kind,
        page_id=page_id,
        page_title=page_title,
        block_type=block_type,
        file_type=file_type,
        name=name or page_title,
        url=url,
        expiry_time=expiry_time,
    )


def collect_page_attachments(
    page_id: str,
    api_key: str,
    max_depth: int,
    *,
    skip_page_ids: set[str] | None = None,
) -> list[Attachment]:
    attachments: list[Attachment] = []
    seen_pages: set[str] = set()
    seen_blocks: set[str] = set()
    skip_page_ids = skip_page_ids or set()

    def collect_page(current_page_id: str, depth: int) -> None:
        if current_page_id in seen_pages:
            return
        seen_pages.add(current_page_id)
        if current_page_id in skip_page_ids or has_telegram_intake_marker(current_page_id, api_key):
            return
        page = notion_get(f"/pages/{current_page_id}", api_key)
        page_title = title_from_page(page)

        for prop_name, prop in page.get("properties", {}).items():
            if prop.get("type") != "files":
                continue
            for index, item in enumerate(prop.get("files", [])):
                attachment = attachment_from_file_obj(
                    source_id=f"{current_page_id}:{prop_name}:{index}",
                    source_kind="property",
                    page_id=current_page_id,
                    page_title=page_title,
                    block_type="property.files",
                    name=item.get("name") or page_title,
                    file_obj=item,
                )
                if attachment:
                    attachments.append(attachment)

        collect_children(current_page_id, current_page_id, page_title, depth)

    def collect_children(block_id: str, owner_page_id: str, owner_title: str, depth: int) -> None:
        cursor = None
        while True:
            path = f"/blocks/{block_id}/children?page_size=100"
            if cursor:
                path += "&start_cursor=" + urllib.parse.quote(cursor)
            data = notion_get(path, api_key)
            for block in data.get("results", []):
                current_block_id = block.get("id")
                if not current_block_id or current_block_id in seen_blocks:
                    continue
                seen_blocks.add(current_block_id)
                block_type = block.get("type")
                payload = block.get(block_type, {}) if block_type else {}

                if block_type in ATTACHMENT_BLOCK_TYPES:
                    file_type = payload.get("type")
                    file_obj = {
                        "type": file_type,
                        file_type: payload.get(file_type, {}),
                    }
                    attachment = attachment_from_file_obj(
                        source_id=current_block_id,
                        source_kind="block",
                        page_id=owner_page_id,
                        page_title=owner_title,
                        block_type=block_type,
                        name=payload.get("name") or owner_title,
                        file_obj=file_obj,
                    )
                    if attachment:
                        attachments.append(attachment)

                if block_type == "child_page" and depth < max_depth:
                    collect_page(current_block_id, depth + 1)
                elif block.get("has_children") and depth < max_depth:
                    collect_children(current_block_id, owner_page_id, owner_title, depth + 1)

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    collect_page(page_id, 0)
    return attachments


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"downloaded": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def unique_path(output_dir: Path, filename: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = output_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        candidate = output_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise SystemExit(f"Could not find a unique filename for {filename}")


def download_url(url: str, output_path: Path) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "zoom-audio-pipeline/1.0"})
    with urllib.request.urlopen(request, timeout=240) as response:
        output_path.write_bytes(response.read())
    return output_path.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull audio-like files from a Notion page tree.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--target", help="Notion page URL/id. Defaults to NOTION_TARGET.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--intake-registry-file", default=DEFAULT_INTAKE_REGISTRY_FILE)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--force", action="store_true", help="Download even if source_id is already in state.")
    parser.add_argument("--list", action="store_true", help="Only list discovered attachments.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--include-urls", action="store_true", help="Include temporary Notion file URLs in JSON output.")
    args = parser.parse_args()

    load_env_file(args.env_file)
    api_key = os.environ.get("NOTION_API_KEY")
    target = args.target or os.environ.get("NOTION_TARGET")
    if not api_key:
        raise SystemExit("NOTION_API_KEY is not set")
    if not target:
        raise SystemExit("NOTION_TARGET is not set and --target was not provided")

    root_page_id = page_id_from_target(target)
    skip_page_ids = load_intake_skip_page_ids(Path(args.intake_registry_file))
    attachments = collect_page_attachments(root_page_id, api_key, args.max_depth, skip_page_ids=skip_page_ids)
    state_path = Path(args.state_file)
    output_dir = Path(args.output_dir)
    state = load_state(state_path)
    downloaded_state = state.setdefault("downloaded", {})
    results: list[dict[str, Any]] = []

    for attachment in attachments:
        already_downloaded = attachment.source_id in downloaded_state
        item = asdict(attachment)
        if not args.include_urls:
            item["url"] = "[redacted]"
        item["already_downloaded"] = already_downloaded
        if args.list:
            results.append(item)
            continue
        if already_downloaded and not args.force:
            item["status"] = "skipped"
            item["path"] = downloaded_state[attachment.source_id].get("path")
            results.append(item)
            continue

        filename = safe_filename(attachment.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = unique_path(output_dir, filename) if args.force else output_dir / filename
        if output_path.exists() and not args.force:
            output_path = unique_path(output_dir, filename)
        bytes_written = download_url(attachment.url, output_path)
        downloaded_state[attachment.source_id] = {
            "path": str(output_path),
            "bytes": bytes_written,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "page_id": attachment.page_id,
            "page_title": attachment.page_title,
            "block_type": attachment.block_type,
            "name": attachment.name,
        }
        item.update({"status": "downloaded", "path": str(output_path), "bytes": bytes_written})
        results.append(item)

    if not args.list:
        save_state(state_path, state)

    if args.json:
        print(json.dumps({"root_page_id": root_page_id, "results": results}, ensure_ascii=False, indent=2))
    else:
        print(f"root_page_id={root_page_id}")
        print(f"attachments={len(attachments)}")
        for item in results:
            status = item.get("status", "found")
            path = item.get("path", "")
            print(f"{status}: {item['page_title']} [{item['block_type']}] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
