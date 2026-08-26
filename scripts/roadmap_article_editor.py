#!/usr/bin/env python3
"""Versioned, block-scoped editing for final roadmap articles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ALLOWED_ACTIONS = {"keep", "replace", "delete"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LEGACY_BLOCK_LINE_RE = re.compile(r"^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```)")
HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-pro"
ARTICLE_EDIT_LOCK_STALE_SECONDS = 30 * 60


def source_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def section_spans(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    headings: list[tuple[int, int, str, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if match and len(match.group(1)) <= 2:
            headings.append((index, len(match.group(1)), match.group(2).strip(), line.strip()))

    if not headings:
        text = markdown.strip()
        return ([{
            "start_line": 0,
            "end_line": len(lines),
            "heading": "Вступление",
            "heading_level": 0,
            "heading_markdown": "",
            "text": text,
        }] if text else [])

    spans: list[dict[str, Any]] = []
    first_start = headings[0][0]
    if any(line.strip() for line in lines[:first_start]):
        spans.append({
            "start_line": 0,
            "end_line": first_start,
            "heading": "Вступление",
            "heading_level": 0,
            "heading_markdown": "",
            "text": "\n".join(lines[:first_start]).strip(),
        })
    for position, (start, level, heading, heading_markdown) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        spans.append({
            "start_line": start,
            "end_line": end,
            "heading": heading,
            "heading_level": level,
            "heading_markdown": heading_markdown,
            "text": "\n".join(lines[start:end]).strip(),
        })
    return spans


def build_manifest(markdown: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    digest = source_hash(markdown)
    previous_version = int((previous or {}).get("article_version") or 0)
    previous_hash = str((previous or {}).get("source_sha256") or "")
    same_schema = int((previous or {}).get("schema_version") or 0) == 2
    version = previous_version if previous_version and previous_hash == digest and same_schema else previous_version + 1
    blocks = []
    for number, span in enumerate(section_spans(markdown), start=1):
        blocks.append({
            "id": f"b_{number:03d}",
            "number": number,
            "type": "section",
            **span,
        })
    return {
        "schema_version": 2,
        "article_version": max(1, version),
        "source_sha256": digest,
        "blocks": blocks,
    }


def validate_replacement(value: Any, block: dict[str, Any]) -> str:
    if not isinstance(value, str):
        raise ValueError("replacement must be a string")
    replacement = value.strip()
    if not replacement:
        raise ValueError("replacement must not be empty")
    if len(replacement) > 50_000:
        raise ValueError("replacement is too long")
    if HTML_TAG_RE.search(replacement):
        raise ValueError("replacement cannot contain HTML")
    if block.get("type") == "paragraph":
        if "\n\n" in replacement or "\r\n\r\n" in replacement:
            raise ValueError("legacy replacement must remain one paragraph")
        if any(
            LEGACY_BLOCK_LINE_RE.match(line.strip())
            for line in replacement.splitlines()
            if line.strip()
        ):
            raise ValueError("legacy replacement cannot add Markdown blocks")
        return " ".join(line.strip() for line in replacement.splitlines() if line.strip())
    lines = replacement.splitlines()
    heading_level = int(block.get("heading_level") or 0)
    heading_markdown = str(block.get("heading_markdown") or "")
    if heading_level:
        if not lines or lines[0].strip() != heading_markdown:
            raise ValueError("replacement must preserve the block heading")
        for line in lines[1:]:
            match = HEADING_RE.match(line.strip())
            if match and len(match.group(1)) <= heading_level:
                raise ValueError("replacement cannot add a sibling block")
    elif any(
        (match := HEADING_RE.match(line.strip())) and len(match.group(1)) <= 2
        for line in lines
    ):
        raise ValueError("intro replacement cannot add a sibling block")
    return replacement


def validate_patch(
    manifest: dict[str, Any],
    selected_block_ids: list[str],
    patch_value: dict[str, Any],
) -> list[dict[str, str]]:
    if not isinstance(patch_value, dict):
        raise ValueError("patch must be an object")
    if patch_value.get("article_version") != manifest.get("article_version"):
        raise ValueError("article version does not match")
    blocks = manifest.get("blocks")
    operations = patch_value.get("operations")
    if not isinstance(blocks, list) or not isinstance(operations, list):
        raise ValueError("patch operations must be an array")
    blocks_by_id = {
        str(block.get("id")): block
        for block in blocks
        if isinstance(block, dict) and block.get("id")
    }
    known_ids = set(blocks_by_id)
    selected = [str(value) for value in selected_block_ids]
    if not selected or len(selected) != len(set(selected)) or not set(selected).issubset(known_ids):
        raise ValueError("selected block set is invalid")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in operations:
        if not isinstance(raw, dict) or set(raw) != {"block_id", "action", "replacement"}:
            raise ValueError("operation has invalid fields")
        block_id = str(raw.get("block_id") or "")
        action = str(raw.get("action") or "")
        if block_id in seen or block_id not in selected:
            raise ValueError("operation is outside the selected block set")
        if action not in ALLOWED_ACTIONS:
            raise ValueError("operation action is invalid")
        replacement = raw.get("replacement")
        if action == "replace":
            replacement = validate_replacement(replacement, blocks_by_id[block_id])
        elif replacement not in ("", None):
            raise ValueError("replacement must be empty for keep/delete")
        else:
            replacement = ""
        normalized.append({"block_id": block_id, "action": action, "replacement": str(replacement)})
        seen.add(block_id)
    if seen != set(selected):
        raise ValueError("operations must match the selected block set exactly")
    return normalized


def apply_validated_patch(
    markdown: str,
    manifest: dict[str, Any],
    selected_block_ids: list[str],
    patch_value: dict[str, Any],
) -> str:
    if manifest.get("source_sha256") != source_hash(markdown):
        raise ValueError("article source does not match manifest")
    operations = {
        item["block_id"]: item
        for item in validate_patch(manifest, selected_block_ids, patch_value)
    }
    lines = markdown.splitlines()
    blocks_by_id = {
        str(block["id"]): block
        for block in manifest["blocks"]
        if isinstance(block, dict)
    }
    for block_id in sorted(selected_block_ids, key=lambda value: int(blocks_by_id[value]["start_line"]), reverse=True):
        block = blocks_by_id[block_id]
        operation = operations[block_id]
        if operation["action"] == "keep":
            continue
        replacement_lines = (
            []
            if operation["action"] == "delete"
            else operation["replacement"].splitlines()
        )
        if replacement_lines and int(block["end_line"]) < len(lines):
            replacement_lines.append("")
        lines[int(block["start_line"]):int(block["end_line"])] = replacement_lines
    result = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", result)


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "article_version": {"type": "integer"},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                        "replacement": {"type": "string"},
                    },
                    "required": ["block_id", "action", "replacement"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["article_version", "operations"],
        "additionalProperties": False,
    }


def build_gemini_payload(
    markdown: str,
    manifest: dict[str, Any],
    selected_block_ids: list[str],
    instruction: str,
    model: str,
) -> dict[str, Any]:
    blocks = {str(item["id"]): item for item in manifest["blocks"]}
    selected = [
        {
            "block_id": block_id,
            "number": blocks[block_id]["number"],
            "type": blocks[block_id].get("type", "section"),
            "heading": blocks[block_id].get("heading", ""),
            "original_markdown": blocks[block_id]["text"],
        }
        for block_id in selected_block_ids
        if block_id in blocks
    ]
    prompt = f"""Ты локальный редактор финальной статьи для ученика.

Полная статья ниже является контекстом и эталоном стиля. Сохраняй обращение к ученику, спокойный поддерживающий тон, естественную русскую лексику, длину и ритм предложений, терминологию и степень формальности.

Изменять разрешено только перечисленные выбранные блоки. Для каждого выбранного block_id верни ровно одну операцию: keep, replace или delete. Не меняй порядок. Не добавляй факты, сроки, уровни, числа, обещания или договорённости, если голосовая инструкция прямо этого не требует. Делай минимально необходимое изменение. Для блока type=section верни в replacement полный Markdown раздела вместе с исходным заголовком, сохранив его дословно. Внутри section можно сохранять и менять абзацы, списки, таблицы и вложенные подзаголовки; не добавляй соседние разделы уровня # или ##. Для переходного блока type=paragraph replacement должен оставаться одним абзацем. Не используй HTML. Для keep/delete replacement должен быть пустой строкой.

Версия статьи: {manifest['article_version']}

ВЫБРАННЫЕ БЛОКИ:
{json.dumps(selected, ensure_ascii=False, indent=2)}

ГОЛОСОВАЯ ИНСТРУКЦИЯ:
{instruction.strip()}

ПОЛНАЯ СТАТЬЯ, ТОЛЬКО КОНТЕКСТ СТИЛЯ:
{markdown.strip()}
"""
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 8_000,
        "reasoning": {"effort": "minimal", "exclude": True},
        "provider": {"require_parameters": True},
        "plugins": [{"id": "response-healing"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "roadmap_article_patch",
                "strict": True,
                "schema": response_schema(),
            },
        },
    }


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def request_gemini_patch(payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://codex.local",
            "X-Title": "Roadmap Article Editor",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"OpenRouter article edit failed with HTTP {error.code}: {body}") from error
    try:
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Gemini did not return valid structured JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("Gemini patch must be an object")
    return parsed


def process_edit_job(
    job_path: Path,
    *,
    env: dict[str, str],
    model_request: Callable[[dict[str, Any], str], dict[str, Any]] = request_gemini_patch,
    run_command: Callable[..., Any] = subprocess.run,
    renderer: str = "/usr/local/bin/roadmap-markdown-to-html",
    notifier: str = "/usr/local/bin/telegram-roadmap-notify",
) -> str:
    job = load_json(job_path, {})
    if not isinstance(job, dict):
        raise ValueError("article edit job is invalid")
    if job.get("status") == "done":
        return "already_done"
    run_dir = Path(str(job.get("run_dir") or ""))
    status_path = run_dir / "status.json"
    lock_dir = run_dir / ".article-edit.lock"
    try:
        lock_dir.mkdir()
    except FileExistsError as error:
        lock_age = time.time() - lock_dir.stat().st_mtime
        if lock_age <= ARTICLE_EDIT_LOCK_STALE_SECONDS:
            raise RuntimeError("another article edit is already running") from error
        lock_dir.rmdir()
        lock_dir.mkdir()

    try:
        job["status"] = "started"
        job["started_at"] = utc_now()
        job.pop("last_error", None)
        write_json_atomic(job_path, job)

        article_path = run_dir / "roadmap-article.md"
        manifest_path = run_dir / "roadmap-article-blocks.json"
        markdown = article_path.read_text(encoding="utf-8")
        manifest = load_json(manifest_path, {})
        if not isinstance(manifest, dict):
            raise ValueError("article manifest is invalid")
        selected = job.get("selected_block_ids", [])
        if not isinstance(selected, list):
            raise ValueError("article edit selection is invalid")
        if job.get("article_version") != manifest.get("article_version"):
            raise ValueError("article edit job uses a stale article version")
        instruction = str(job.get("instruction") or "").strip()
        if not instruction:
            raise ValueError("article edit instruction is empty")
        api_key = env.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        model = env.get("ARTICLE_EDIT_MODEL") or env.get("GEMINI_REWRITE_MODEL") or DEFAULT_MODEL
        payload = build_gemini_payload(markdown, manifest, [str(value) for value in selected], instruction, model)

        patch_value: dict[str, Any] | None = None
        validation_error = ""
        for attempt in range(1, 3):
            try:
                candidate = model_request(payload, api_key)
                validate_patch(manifest, [str(value) for value in selected], candidate)
            except (ValueError, RuntimeError) as error:
                validation_error = str(error)
                if attempt == 1:
                    payload = dict(payload)
                    payload["messages"] = [
                        *payload["messages"],
                        {
                            "role": "user",
                            "content": (
                                "Предыдущий ответ не прошёл JSON/patch-проверку. "
                                "Верни заново полный набор операций строго по заданной схеме."
                            ),
                        },
                    ]
                    continue
                break
            patch_value = candidate
            break
        if patch_value is None:
            raise RuntimeError(f"Gemini patch validation failed after two attempts: {validation_error}")

        job_dir = job_path.parent / job_path.stem
        stage_dir = job_dir / "staged"
        stage_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(job_dir / "gemini-patch.json", patch_value)
        updated = apply_validated_patch(
            markdown,
            manifest,
            [str(value) for value in selected],
            patch_value,
        )
        staged_markdown = stage_dir / "roadmap-article.md"
        staged_html = stage_dir / "roadmap-article.html"
        staged_pdf = stage_dir / "roadmap-article.pdf"
        staged_markdown.write_text(updated, encoding="utf-8")
        run_command([renderer, str(staged_markdown), "-o", str(staged_html)], check=True)
        if not staged_html.exists() or staged_html.stat().st_size < 200:
            raise RuntimeError("article edit HTML render is invalid")
        run_command([
            "wkhtmltopdf",
            "--encoding", "utf-8",
            "--enable-local-file-access",
            "--margin-top", "12mm",
            "--margin-right", "10mm",
            "--margin-bottom", "12mm",
            "--margin-left", "10mm",
            str(staged_html),
            str(staged_pdf),
        ], check=True)
        if not staged_pdf.exists() or staged_pdf.stat().st_size < 200:
            raise RuntimeError("article edit PDF render is invalid")

        old_version = int(manifest["article_version"])
        version_dir = run_dir / "article-versions" / f"v{old_version}"
        version_dir.mkdir(parents=True, exist_ok=True)
        for current in (
            article_path,
            run_dir / "roadmap-article.html",
            run_dir / "roadmap-article.pdf",
            manifest_path,
        ):
            if current.exists():
                shutil.copy2(current, version_dir / current.name)

        staged_markdown.replace(article_path)
        staged_html.replace(run_dir / "roadmap-article.html")
        staged_pdf.replace(run_dir / "roadmap-article.pdf")
        next_manifest = build_manifest(updated, previous=manifest)
        write_json_atomic(manifest_path, next_manifest)

        status = load_json(status_path, {})
        if not isinstance(status, dict):
            status = {}
        status.update({
            "article_edit_status": "delivery_started",
            "article_edit_rendered_at": utc_now(),
            "article_edit_job": str(job_path),
            "article_version": next_manifest["article_version"],
            "article_edit_model": model,
        })
        status.pop("article_edit_done_at", None)
        status.pop("article_edit_last_error", None)
        write_json_atomic(status_path, status)

        notify_command = [
            notifier,
            "--env-file", env.get("PIPELINE_ENV_FILE", "/etc/zoom-audio-pipeline/pipeline.env"),
            "--chat-id", str(job.get("chat_id") or ""),
            "--stage", "article_ready",
            "--audio", str(job.get("audio") or run_dir.name),
            "--run-dir", str(run_dir),
            "--registry-file", str(job.get("registry_file") or "/var/lib/zoom-audio-pipeline/telegram-run-registry.json"),
        ]
        run_command(notify_command, check=True)
        status = load_json(status_path, {})
        if not isinstance(status, dict):
            status = {}
        status["article_edit_status"] = "done"
        status["article_edit_done_at"] = utc_now()
        status.pop("article_edit_last_error", None)
        write_json_atomic(status_path, status)
        job["status"] = "done"
        job["done_at"] = utc_now()
        job["result_article_version"] = next_manifest["article_version"]
        write_json_atomic(job_path, job)
        return "done"
    except Exception as error:
        status = load_json(status_path, {})
        if not isinstance(status, dict):
            status = {}
        status["article_edit_status"] = "failed"
        status["article_edit_failed_at"] = utc_now()
        status["article_edit_last_error"] = str(error)[:1000]
        status.pop("article_edit_done_at", None)
        write_json_atomic(status_path, status)
        job["status"] = "failed"
        job["failed_at"] = utc_now()
        job["last_error"] = str(error)[:1000]
        write_json_atomic(job_path, job)
        raise
    finally:
        lock_dir.rmdir()


def notify_edit_failure(
    job_path: Path,
    env: dict[str, str],
    *,
    notifier: str = "/usr/local/bin/telegram-roadmap-notify",
    run_command: Callable[..., Any] = subprocess.run,
) -> None:
    job = load_json(job_path, {})
    if not isinstance(job, dict) or not job.get("chat_id"):
        return
    run_command([
        notifier,
        "--env-file", env.get("PIPELINE_ENV_FILE", "/etc/zoom-audio-pipeline/pipeline.env"),
        "--chat-id", str(job["chat_id"]),
        "--text", (
            "Не смог применить правки к выбранным блокам автоматически. "
            "Текущая версия статьи сохранена. Открой её, выбери блоки и попробуй ещё раз."
        ),
    ], check=False)


def prepare_article_page(
    source: Path,
    output: Path,
    run_key: str,
    api_url: str,
    renderer: str,
) -> dict[str, Any]:
    markdown = source.read_text(encoding="utf-8")
    manifest_path = source.parent / "roadmap-article-blocks.json"
    previous = load_json(manifest_path, {})
    manifest = build_manifest(markdown, previous=previous if isinstance(previous, dict) else None)
    write_json_atomic(manifest_path, manifest)
    editor_config = {
        "run_key": run_key,
        "article_version": manifest["article_version"],
        "api_url": api_url,
        "blocks": manifest["blocks"],
    }
    config_path = source.parent / "roadmap-article-editor.json"
    write_json_atomic(config_path, editor_config)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    subprocess.run(
        [renderer, str(source), "-o", str(temporary_output), "--editor-config", str(config_path)],
        check=True,
    )
    if not temporary_output.exists() or temporary_output.stat().st_size < 200:
        temporary_output.unlink(missing_ok=True)
        raise RuntimeError("editor renderer did not create a valid HTML file")
    temporary_output.replace(output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or edit a versioned roadmap article.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--run-key", required=True)
    prepare.add_argument("--api-url", default="/roadmap-telegram/article-selection")
    prepare.add_argument("--renderer", default="roadmap-markdown-to-html")
    apply_job = subparsers.add_parser("apply-job")
    apply_job.add_argument("--job", required=True)
    apply_job.add_argument("--env-file", default="/etc/zoom-audio-pipeline/pipeline.env")
    apply_job.add_argument("--renderer", default="/usr/local/bin/roadmap-markdown-to-html")
    apply_job.add_argument("--notifier", default="/usr/local/bin/telegram-roadmap-notify")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare_article_page(
            Path(args.source),
            Path(args.output),
            args.run_key,
            args.api_url,
            args.renderer,
        )
        return 0
    if args.command == "apply-job":
        env = {**load_env_file(Path(args.env_file)), **os.environ}
        env.setdefault("PIPELINE_ENV_FILE", args.env_file)
        try:
            process_edit_job(
                Path(args.job),
                env=env,
                renderer=args.renderer,
                notifier=args.notifier,
            )
        except Exception as error:
            print(f"article_edit_failed: {error}", flush=True)
            try:
                notify_edit_failure(Path(args.job), env, notifier=args.notifier)
            except Exception as notify_error:
                print(f"article_edit_failure_notify_failed: {notify_error}", flush=True)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
