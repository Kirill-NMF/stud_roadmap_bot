#!/usr/bin/env python3
"""Send Telegram notifications for the Zoom audio roadmap pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ENV_FILE = "/root/.telegram/roadmap-bot.env"
DEFAULT_REGISTRY_FILE = "/var/lib/zoom-audio-pipeline/telegram-run-registry.json"
DEFAULT_PUBLIC_ROOT = "/var/www/roadmap-reader"
DEFAULT_PUBLIC_BASE_URL = "https://dev.short-talk.space/roadmap-reader"
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 4096
VERIFICATION_BRIEF_FILE = "verification-brief.md"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def telegram_api_base_url(value: str | None = None) -> str:
    return (value or DEFAULT_TELEGRAM_API_BASE_URL).rstrip("/")


def telegram_method_url(token: str, method: str, api_base_url: str | None = None) -> str:
    return f"{telegram_api_base_url(api_base_url)}/bot{token}/{method}"


def telegram_request(
    token: str,
    method: str,
    payload: dict[str, object] | None = None,
    *,
    api_base_url: str | None = None,
) -> dict[str, object]:
    url = telegram_method_url(token, method, api_base_url)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def telegram_multipart_request(
    token: str,
    method: str,
    fields: dict[str, object],
    file_field: str,
    file_path: Path,
    display_filename: str | None = None,
    api_base_url: str | None = None,
) -> dict[str, object]:
    boundary = "----CodexRoadmapBoundary" + hashlib.sha1(str(file_path).encode("utf-8")).hexdigest()
    body = bytearray()

    def add_part(name: str, value: bytes, filename: str | None = None, content_type: str | None = None) -> None:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        body.extend((disposition + "\r\n").encode("utf-8"))
        if content_type:
            body.extend(f"Content-Type: {content_type}\r\n".encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(value)
        body.extend(b"\r\n")

    for key, value in fields.items():
        if isinstance(value, (dict, list)):
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        else:
            encoded = str(value).encode("utf-8")
        add_part(key, encoded)

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    add_part(file_field, file_path.read_bytes(), filename=display_filename or file_path.name, content_type=content_type)
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = urllib.request.Request(
        telegram_method_url(token, method, api_base_url),
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def register_run(registry_path: Path, run_dir: str, audio: str, chat_id: str) -> str:
    key = hashlib.sha1(run_dir.encode("utf-8")).hexdigest()[:12]
    registry = load_json(registry_path, {"runs": {}})
    assert isinstance(registry, dict)
    runs = registry.setdefault("runs", {})
    assert isinstance(runs, dict)
    runs[key] = {
        "run_dir": run_dir,
        "audio": audio,
        "chat_id": str(chat_id),
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    save_json(registry_path, registry)
    return key


def truncate_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 80)].rstrip() + "\n\n[...]\nПолная версия сохранена на сервере."


def join_readable(items: list[str]) -> str:
    return "\n\n".join(item.strip() for item in items if item.strip())


def split_messages(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < 1200:
            cut = remaining.rfind("\n", 0, limit)
        if cut < 1200:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def extract_section(markdown: str, heading: str, limit: int) -> str:
    marker = f"\n## {heading}"
    start = markdown.find(marker)
    if start < 0 and markdown.startswith(f"## {heading}"):
        start = 0
    if start < 0:
        return ""
    next_start = markdown.find("\n## ", start + len(marker))
    block = markdown[start: next_start if next_start >= 0 else len(markdown)].strip()
    return truncate_text(block, limit)


def section_body(markdown: str, heading: str) -> str:
    block = extract_section(markdown, heading, 20000)
    return re.sub(r"^##\s+.+?\n", "", block, count=1).strip()


def first_existing_body(markdown: str, headings: list[str]) -> str:
    for heading in headings:
        body = section_body(markdown, heading)
        if body:
            return body
    return ""


def first_numbered_items(text: str, limit: int) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+", stripped):
            items.append(re.sub(r"^\d+\.\s+", "", stripped).strip())
        if len(items) >= limit:
            break
    return items


def compact_bullets(text: str, limit: int) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped)
        if len(items) >= limit:
            break
    return items


def numbered_bullets(text: str, prefix: str, limit: int) -> list[str]:
    items: list[str] = []
    for index, line in enumerate(compact_bullets(text, limit), start=1):
        items.append(f"{prefix}.{index}. {line[2:].strip()}")
    return items


def numbered_questions(text: str, prefix: str, limit: int) -> list[str]:
    return [f"{prefix}.{index}. {item}" for index, item in enumerate(first_numbered_items(text, limit), start=1)]


def extract_roadmap_table(markdown: str) -> str:
    body = first_existing_body(markdown, [
        "6. Roadmap для статьи",
        "6. Предлагаемый roadmap",
        "2. Обсуждённые сроки и результаты",
    ])
    rows = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    return "\n".join(rows[:6]).strip()


def number_table_rows(table: str, prefix: str) -> str:
    rows = [row for row in table.splitlines() if row.strip()]
    if len(rows) < 3:
        return table
    numbered = []
    for index, row in enumerate(rows):
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if index == 0:
            numbered.append("| ID | " + " | ".join(cells) + " |")
        elif index == 1:
            numbered.append("| --- | " + " | ".join("---" for _ in cells) + " |")
        else:
            numbered.append(f"| {prefix}.{index - 1} | " + " | ".join(cells) + " |")
    return "\n".join(numbered)


def table_to_numbered_cards(table: str, prefix: str) -> str:
    rows = [row for row in table.splitlines() if row.strip().startswith("|")]
    if len(rows) < 3:
        return table
    cards: list[str] = []
    headers = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
    for index, row in enumerate(rows[2:], start=1):
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if not cells:
            continue
        title = cells[0]
        card_lines = [f"{prefix}.{index}. {title}"]
        for header, value in zip(headers[1:], cells[1:]):
            if value:
                card_lines.append(f"{header}: {value}")
        cards.append("\n".join(card_lines))
    return join_readable(cards)


ENHANCEMENT_DESCRIPTIONS = {
    "P1": "Progress.me как единая платформа: материалы, грамматика, лексика, аудирование, говорение, озвучка.",
    "P2": "Голосовые сообщения в Telegram между уроками с обратной связью преподавателя.",
    "P3": "Карточки Anki или карточки в приложении: определения, синонимы, примеры, картинки.",
    "P4": "Уроки по контенту ученика: YouTube Shorts, TikTok, reels, мемы или видео по интересам.",
    "P5": "Настроить англоязычную ленту YouTube/TikTok под интересы ученика.",
    "P6": "Language Reactor или Context Reverso для самостоятельной работы с видео и словами.",
    "P7": "Сценарии путешествий: отель, аэропорт, кафе, дорога, знакомство, бытовые вопросы.",
    "P8": "Сложные ситуации в поездке: потеря багажа, болезнь, проблема с номером, непонятная просьба.",
    "P9": "Видеоигровой модуль: общение с зарубежными игроками и координация в игре.",
    "P10": "Профессиональный модуль: документы, встречи, переговоры, презентации.",
    "P11": "IT/interview/offers-модуль, если есть цель выйти на международную работу.",
    "P12": "Группа позже как опция для социальной практики с разными людьми.",
    "P13": "Гибкий режим под сменный график, вахту, поездки или нерегулярную работу.",
    "P14": "Интенсив под ближайший дедлайн: поездка, встреча, интервью, презентация.",
}

ENHANCEMENT_PRESENT_TERMS = {
    "P1": ["progress.me", "платформ", "интерактив"],
    "P2": ["голосов", "войс", "telegram", "телеграм"],
    "P3": ["карточ", "anki", "определени", "синоним", "пример"],
    "P4": ["youtube", "shorts", "tiktok", "reels", "рилс", "мем", "видео", "контент"],
    "P5": ["англоязычн", "лента", "youtube", "tiktok"],
    "P6": ["language reactor", "context reverso", "субтитр", "перевод по клику"],
    "P7": ["путеше", "отель", "аэропорт", "кафе", "дорог", "знаком"],
    "P8": ["потер", "багаж", "болез", "номер", "проблем"],
    "P9": ["игр", "game", "gaming", "стрим", "зарубежн"],
    "P10": ["проект", "документ", "встреч", "переговор", "презентац", "созвон"],
    "P11": ["it", "айти", "interview", "интерв", "оффер", "foreign company", "международ"],
    "P12": ["групп", "социальн"],
    "P13": ["гибк", "сменн", "вахт", "нерегуляр", "график"],
    "P14": ["интенсив", "дедлайн", "срочн", "через неделю", "две недели", "встреч"],
}


def enhancement_is_present(markdown: str, code: str) -> bool:
    text = markdown.lower()
    return any(term in text for term in ENHANCEMENT_PRESENT_TERMS.get(code, []))


def build_enhancement_suggestions(markdown: str) -> list[str]:
    existing: list[str] = []
    optional: list[str] = []
    for code in sorted(ENHANCEMENT_DESCRIPTIONS, key=lambda value: int(value[1:])):
        if enhancement_is_present(markdown, code):
            existing.append(f"{code}. **Уже было в созвоне:** {ENHANCEMENT_DESCRIPTIONS[code]}")
        else:
            optional.append(f"{code}. {ENHANCEMENT_DESCRIPTIONS[code]}")

    result: list[str] = []
    if existing:
        result.extend([
            "8.1. Уже есть в созвоне или первичном анализе. Эти элементы можно включать в статью базово, если ты нажмёшь «Согласен»:",
            "",
            *existing,
        ])
    if optional:
        if result:
            result.append("")
        result.extend([
            "8.2. Можно дополнительно усилить roadmap. Эти элементы не включаются автоматически и добавляются только если ты явно подтвердишь код:",
            "",
            *optional,
        ])
    return result


def build_verification_brief(markdown: str, audio: str) -> str:
    student = section_body(markdown, "1. Краткая картина ученика")
    timeline = first_existing_body(markdown, [
        "2. Обсуждённые сроки и результаты",
        "6. Roadmap для статьи",
        "6. Предлагаемый roadmap",
    ])
    teacher_phrases = first_existing_body(markdown, [
        "3. Формулировки преподавателя, которые стоит сохранить",
        "5. Моя интерпретация для будущей статьи",
    ])
    trust = first_existing_body(markdown, [
        "4. Сильные стороны ученика и основания доверия",
        "3. Сильные стороны ученика",
    ])
    system = first_existing_body(markdown, [
        "5. Система обучения, которую важно показать ученику",
        "9. Предлагаемые акценты для продающего смысла",
    ])
    risks = first_existing_body(markdown, [
        "7. Риски для формулировок и что лучше не включать",
        "4. Боли, ограничения и риски",
    ])
    questions = first_numbered_items(first_existing_body(markdown, [
        "8. Факты, которые реально нужно уточнить",
        "8. Вопросы к преподавателю перед финальной статьёй",
    ]), 7)
    roadmap = extract_roadmap_table(markdown)
    numbered_roadmap = table_to_numbered_cards(roadmap, "2") if roadmap else timeline
    proposals = build_enhancement_suggestions(markdown)

    parts = [
        "# Проверка перед статьёй",
        "",
        f"Файл: {audio}",
        "",
        "## 1. Картина ученика",
        "",
        join_readable(numbered_bullets(student, "1", 8)) or student,
        "",
        "## 2. Сроки и результаты из созвона",
        "",
        numbered_roadmap,
        "",
        "## 3. Формулировки преподавателя, которые стоит сохранить",
        "",
        join_readable(numbered_bullets(teacher_phrases, "3", 6)) or teacher_phrases or "3.1. Явные формулировки преподавателя не выделены.",
        "",
        "## 4. Почему ученик может поверить в результат",
        "",
        join_readable(numbered_bullets(trust, "4", 5)) or "4.1. Основания доверия не выделены.",
        "",
        "## 5. Какую систему важно показать",
        "",
        join_readable(numbered_bullets(system, "5", 5)) or "5.1. Система обучения не выделена.",
        "",
        "## 6. Что реально нужно уточнить",
        "",
    ]

    if questions:
        parts.append(join_readable([f"6.{index}. {item}" for index, item in enumerate(questions, start=1)]))
    else:
        parts.append("6.1. Критичных неясностей не найдено.")

    parts.extend([
        "",
        "## 7. Осторожные места для текста ученику",
        "",
        join_readable(numbered_bullets(risks, "7", 6)) or "7.1. Осторожные места не выделены.",
        "",
        "## 8. Предложения из PDF-базы",
        "",
        "Часть PDF-опций может уже звучать в созвоне. Такие элементы помечены отдельно жирной меткой «Уже было в созвоне» и считаются фактами созвона. Остальные — только предложения на твоё подтверждение: напиши, например, «P1 да, P4 нет, P7 добавить».",
        "",
        join_readable(proposals),
        "",
        "## Что сделать дальше",
        "",
        "Если факты, сроки и формулировки верны, нажми «Согласен». Если нужно что-то поправить или добавить PDF-опции, пришли правку голосом или текстом по номерам и/или кодам P1-P14.",
    ])

    return "\n".join(part for part in parts if part is not None).strip() + "\n"


def build_verification_chat_message(markdown: str, audio: str) -> str:
    student = section_body(markdown, "1. Краткая картина ученика")
    values: dict[str, str] = {}
    for line in student.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- ") or ":" not in stripped:
            continue
        key, value = stripped[2:].split(":", 1)
        values[key.strip().lower()] = value.strip()

    name = values.get("имя", "нужно уточнить")
    level = values.get("уровень", "нужно уточнить")
    dates = values.get("сроки и даты") or values.get("сроки", "нужно уточнить")

    return "\n".join([
        "Проверка перед статьёй готова.",
        "",
        f"Файл: {audio}",
        f"Имя: {truncate_text(name, 120)}",
        f"Уровень: {truncate_text(level, 180)}",
        f"Дата/сроки: {truncate_text(dates, 180)}",
        "",
        "Открой полную проверку. Потом нажми «Согласен» или пришли правки голосом/текстом.",
    ])


def write_verification_brief(run_dir: Path, audio: str) -> Path:
    source = run_dir / "verification.md"
    target = run_dir / VERIFICATION_BRIEF_FILE
    if source.exists():
        target.write_text(build_verification_brief(source.read_text(encoding="utf-8"), audio), encoding="utf-8")
    return target


def build_transcript_message(run_dir: Path, audio: str) -> str:
    return "\n".join([
        f"Транскрипт готов: {audio}",
        "",
        "Запускаю первичный анализ созвона.",
        f"Файл: {run_dir / 'transcript.md'}",
    ])


def build_verification_message(run_dir: Path, audio: str) -> str:
    source = run_dir / "verification.md"
    write_verification_brief(run_dir, audio)
    if not source.exists():
        return "\n".join([
            f"Первичный анализ готов: {audio}",
            f"Файл: {run_dir / 'verification.md'}",
        ])

    return build_verification_chat_message(source.read_text(encoding="utf-8"), audio)


def build_article_message(run_dir: Path, audio: str) -> str:
    article = run_dir / "roadmap-article.md"
    html = run_dir / "roadmap-article.html"
    if article.exists():
        return "\n".join([
            f"Статья-roadmap готова: {audio}",
            "",
            "Открой красивую версию по кнопке ниже.",
        ])
    return "\n".join([
        f"Статья-roadmap готова: {audio}",
        f"Markdown: {article}",
        f"HTML: {html}",
    ])


def ensure_article_pdf(run_dir: Path) -> Path:
    article = run_dir / "roadmap-article.md"
    html = run_dir / "roadmap-article.html"
    pdf = run_dir / "roadmap-article.pdf"

    if not html.exists() and article.exists():
        subprocess.run(["roadmap-markdown-to-html", str(article), "-o", str(html)], check=True)
    if html.exists():
        subprocess.run([
            "wkhtmltopdf",
            "--encoding",
            "utf-8",
            "--enable-local-file-access",
            "--margin-top",
            "12mm",
            "--margin-right",
            "10mm",
            "--margin-bottom",
            "12mm",
            "--margin-left",
            "10mm",
            str(html),
            str(pdf),
        ], check=True)
    return pdf


def safe_document_stem(audio: str) -> str:
    stem = Path(audio).stem.strip()
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem or "roadmap"


def article_document_filename(audio: str, extension: str) -> str:
    return f"{safe_document_stem(audio)} roadmap{extension}"


def publish_markdown(source: Path, kind: str, public_root: Path, public_base_url: str) -> str:
    if not source.exists():
        return ""
    key = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:16]
    out_dir = public_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{kind}.html"
    subprocess.run(["roadmap-markdown-to-html", str(source), "-o", str(out_path)], check=True)
    return f"{public_base_url.rstrip('/')}/{key}/{kind}.html"


def send_text_messages(
    token: str,
    chat_id: str,
    text: str,
    *,
    reply_markup: dict[str, object] | None = None,
    parse_mode: str = "",
    api_base_url: str | None = None,
) -> dict[str, object]:
    messages = split_messages(text)
    if not messages:
        raise ValueError("empty Telegram text")
    last_result: dict[str, object] = {}
    for index, chunk in enumerate(messages):
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup and index == len(messages) - 1:
            payload["reply_markup"] = reply_markup
        last_result = telegram_request(token, "sendMessage", payload, api_base_url=api_base_url)
    return last_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a Telegram message through the roadmap bot.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--parse-mode", default="")
    parser.add_argument(
        "--stage",
        choices=["custom", "transcript_ready", "verification_ready", "article_ready"],
        default="custom",
    )
    parser.add_argument("--audio", default="")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--registry-file", default=DEFAULT_REGISTRY_FILE)
    parser.add_argument("--public-root", default="")
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--get-me", action="store_true")
    parser.add_argument("--get-updates", action="store_true")
    parser.add_argument("--set-webhook", default="")
    parser.add_argument("--delete-webhook", action="store_true")
    args = parser.parse_args()

    env = {**load_env(Path(args.env_file)), **os.environ}
    token = env.get("TELEGRAM_BOT_TOKEN")
    api_base_url = telegram_api_base_url(env.get("TELEGRAM_API_BASE_URL"))
    chat_id = args.chat_id or env.get("TELEGRAM_CHAT_ID", "")
    if not token:
        print("TELEGRAM_BOT_TOKEN is not configured", file=sys.stderr)
        return 2

    if args.get_me:
        result = telegram_request(token, "getMe", api_base_url=api_base_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.get_updates:
        result = telegram_request(token, "getUpdates", api_base_url=api_base_url)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.set_webhook:
        secret = env.get("TELEGRAM_WEBHOOK_SECRET", "")
        payload: dict[str, object] = {"url": args.set_webhook, "allowed_updates": ["message", "callback_query"]}
        if secret:
            payload["secret_token"] = secret
        result = telegram_request(token, "setWebhook", payload, api_base_url=api_base_url)
        print(json.dumps({"ok": result.get("ok", False), "description": result.get("description")}, ensure_ascii=False))
        return 0

    if args.delete_webhook:
        result = telegram_request(token, "deleteWebhook", {"drop_pending_updates": False}, api_base_url=api_base_url)
        print(json.dumps({"ok": result.get("ok", False), "description": result.get("description")}, ensure_ascii=False))
        return 0

    if not chat_id:
        print("TELEGRAM_CHAT_ID is not configured", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir) if args.run_dir else Path()
    text = args.text
    reply_markup = None
    public_url = ""
    public_root = Path(args.public_root or env.get("ROADMAP_PUBLIC_ROOT", DEFAULT_PUBLIC_ROOT))
    public_base_url = args.public_base_url or env.get("ROADMAP_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL)

    if args.stage == "transcript_ready":
        if not args.audio or not args.run_dir:
            print("--audio and --run-dir are required for transcript_ready", file=sys.stderr)
            return 2
        text = build_transcript_message(run_dir, args.audio)

    elif args.stage == "verification_ready":
        if not args.audio or not args.run_dir:
            print("--audio and --run-dir are required for verification_ready", file=sys.stderr)
            return 2
        key = register_run(Path(args.registry_file), args.run_dir, args.audio, str(chat_id))
        registry = load_json(Path(args.registry_file), {"runs": {}})
        assert isinstance(registry, dict)
        pending = registry.setdefault("pending_reviews", {})
        assert isinstance(pending, dict)
        pending[str(chat_id)] = {
            "run_key": key,
            "run_dir": args.run_dir,
            "audio": args.audio,
            "requested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        save_json(Path(args.registry_file), registry)
        brief_path = write_verification_brief(run_dir, args.audio)
        public_url = publish_markdown(brief_path, "verification", public_root, public_base_url)
        text = build_verification_message(run_dir, args.audio)
        keyboard: list[list[dict[str, object]]] = []
        if public_url:
            keyboard.append([{"text": "Открыть", "web_app": {"url": public_url}}])
        keyboard.append([{"text": "Согласен", "callback_data": f"roadmap:approve:{key}"}])
        reply_markup = {"inline_keyboard": keyboard}

    elif args.stage == "article_ready":
        if not args.audio or not args.run_dir:
            print("--audio and --run-dir are required for article_ready", file=sys.stderr)
            return 2
        public_url = publish_markdown(run_dir / "roadmap-article.md", "article", public_root, public_base_url)
        text = build_article_message(run_dir, args.audio)
        article_pdf = ensure_article_pdf(run_dir)
        if public_url:
            reply_markup = {"inline_keyboard": [[{"text": "Открыть красиво", "web_app": {"url": public_url}}]]}

    if not text:
        print("--text is required", file=sys.stderr)
        return 2

    result = send_text_messages(
        token,
        str(chat_id),
        text,
        reply_markup=reply_markup,
        parse_mode=args.parse_mode,
        api_base_url=api_base_url,
    )
    if args.stage == "article_ready":
        html = run_dir / "roadmap-article.html"
        if html.exists():
            telegram_multipart_request(token, "sendDocument", {
                "chat_id": str(chat_id),
                "caption": f"HTML-версия статьи-roadmap: {args.audio}",
            }, "document", html, display_filename=article_document_filename(args.audio, ".html"), api_base_url=api_base_url)
        pdf = run_dir / "roadmap-article.pdf"
        if pdf.exists():
            telegram_multipart_request(token, "sendDocument", {
                "chat_id": str(chat_id),
                "caption": f"PDF-версия статьи-roadmap: {args.audio}",
            }, "document", pdf, display_filename=article_document_filename(args.audio, ".pdf"), api_base_url=api_base_url)
    print(json.dumps({"ok": result.get("ok", False), "message_id": result.get("result", {}).get("message_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
