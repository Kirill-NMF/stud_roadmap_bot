#!/usr/bin/env python3
"""Focused tests for the roadmap Telegram approval and article flow."""

from __future__ import annotations

import importlib.util
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {relative_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WEBHOOK = load_module("telegram_roadmap_webhook", "scripts/telegram_roadmap_webhook.py")
NOTIFY = load_module("telegram_roadmap_notify", "scripts/telegram_roadmap_notify.py")
ARTICLE_EDITOR = load_module("roadmap_article_editor", "scripts/roadmap_article_editor.py")
ARTICLE_RENDERER = load_module(
    "roadmap_markdown_to_html",
    "skills/english-roadmap-rewrite/scripts/roadmap_markdown_to_html.py",
)
APPROVED = load_module("process_approved_roadmaps", "scripts/process_approved_roadmaps.py")
PROCESS_AUDIO = load_module("process_new_audio", "scripts/process_new_audio.py")
NOTION_PULL = load_module("notion_pull_audio", "scripts/notion_pull_audio.py")
ARCHIVE_WORKER = load_module("telegram_notion_archive_worker", "scripts/telegram_notion_archive_worker.py")
CLEANUP = load_module("telegram_intake_cleanup", "scripts/telegram_intake_cleanup.py")
OPENROUTER_GENERATOR = load_module("openrouter_roadmap_generate", "scripts/openrouter_roadmap_generate.py")
ENV_MIGRATOR = load_module("configure_pipeline_env_from_legacy", "scripts/configure_pipeline_env_from_legacy.py")
VOICE_TRANSCRIBER = load_module("transcribe_telegram_voice", "scripts/transcribe_telegram_voice.py")


VERIFICATION_MD = """# Проверка

## 1. Краткая картина ученика

- Имя: Даниил
- Уровень: A0
- Сроки и даты: старт 12 августа, 1/3/6 месяцев
- Цель: разговорный английский для работы

## 2. Обсуждённые сроки и результаты

| Период | Результат | Что делаем |
| --- | --- | --- |
| 1 месяц | первые ситуации | база |
| 3 месяца | увереннее говорить | практика |
| 6 месяцев | B1 | система |

## 3. Формулировки преподавателя, которые стоит сохранить

- У тебя всё получится, потому что есть понятная система.

## 4. Сильные стороны ученика и основания доверия

- Есть мотивация и понятная цель.

## 5. Система обучения, которую важно показать ученику

- Разговорная практика и материалы.

## 7. Риски для формулировок и что лучше не включать

- Не перегружать деталями.
"""


SYMBOLIC_VERIFICATION_MD = VERIFICATION_MD + """

## 6. Предварительная наглядная структура статьи

### [1] Весь маршрут в одной цепочке

**A0 → первые диалоги → уверенная речь → B1.**

### [2] Roadmap: 1 → 3 → 6 месяцев

**1 месяц. Цель:** первые ситуации. **Практика:** база. **На выходе:** меньше страха.

### [3] Как занятие превращается в результат

**Тема → диалог → обратная связь → повторение.**

### [4] Что станет получаться

- ✓ поддерживать простой диалог.

### [5] Навык → практическая польза

| Навык | Где поможет |
| --- | --- |
| Переспрашивать | Не теряться в разговоре |

### [6] Чек-лист до старта

- □ Выбрать материал.
"""


class TempRunMixin:
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "status.json").write_text("{}", encoding="utf-8")
        self.registry = self.root / "registry.json"
        self.events = self.root / "events.jsonl"
        self.registry.write_text(
            json.dumps(
                {
                    "runs": {
                        "abc123": {
                            "run_dir": str(self.run_dir),
                            "audio": "lesson.m4a",
                            "chat_id": "42",
                        }
                    },
                    "pending_reviews": {
                        "42": {
                            "run_key": "abc123",
                            "run_dir": str(self.run_dir),
                            "audio": "lesson.m4a",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def handler(self, extra_config: dict[str, str] | None = None):
        config = {
            "token": "token",
            "secret": "secret",
            "registry_file": str(self.registry),
            "events_file": str(self.events),
            "voice_python": "python3",
            "voice_transcriber": "noop",
            "telegram_intake_dir": str(self.root / "telegram-intake"),
            "telegram_notion_intake_state": str(self.root / "telegram-notion-intake.json"),
            "inbox_dir": str(self.root / "inbox"),
            "notion_archive_worker": "",
        }
        if extra_config:
            config.update(extra_config)
        Handler = WEBHOOK.make_handler(config)
        return object.__new__(Handler)

    def status(self) -> dict[str, object]:
        return json.loads((self.run_dir / "status.json").read_text(encoding="utf-8"))

    def notes(self) -> str:
        return (self.run_dir / "teacher-notes.md").read_text(encoding="utf-8")


class PromptRuleTests(unittest.TestCase):
    def test_verification_prompt_requires_six_symbolic_preview_blocks(self) -> None:
        prompt = (ROOT / "scripts/consultation_verification_prompt.md").read_text(encoding="utf-8")
        self.assertIn("## 6. Предварительная наглядная структура статьи", prompt)
        for number in range(1, 7):
            self.assertIn(f"### [{number}]", prompt)
        self.assertIn("Цель", prompt)
        self.assertIn("Практика", prompt)
        self.assertIn("На выходе", prompt)
        self.assertIn("не добавляй в блоки [1]-[6]", prompt)

    def test_verification_prompt_requires_numbered_generation_decisions(self) -> None:
        prompt = (ROOT / "scripts/consultation_verification_prompt.md").read_text(encoding="utf-8")

        self.assertIn("## 9. Решения перед генерацией", prompt)
        for topic in (
            "Этапы Roadmap",
            "Контент ученика",
            "Групповой и индивидуальный формат",
            "Домашняя работа",
            "Чек-лист до старта",
            "Уровень и результаты",
        ):
            self.assertIn(topic, prompt)
        self.assertIn("**Из созвона:**", prompt)
        self.assertIn("**Предложение:**", prompt)
        self.assertIn("не включается после общего ответа", prompt)

    def test_article_prompt_has_current_approval_and_p_option_rules(self) -> None:
        prompt = (ROOT / "scripts/consultation_article_prompt.md").read_text(encoding="utf-8")
        self.assertIn("считай подтверждёнными все факты", prompt)
        self.assertIn("цена, оплата, расписание, дни недели, время занятий", prompt)
        self.assertIn("уже был явно обсуждён в созвоне", prompt)
        self.assertIn("его можно включать базово без отдельного P-кода", prompt)
        self.assertIn("не подтверждает новые предложения из PDF-базы", prompt)
        self.assertIn("Система обучения, которую важно показать ученику", prompt)
        self.assertIn("что именно будет происходить на уроке", prompt)
        self.assertIn("какая будет обратная связь", prompt)
        self.assertIn("как обучение связано с интересами ученика", prompt)
        self.assertIn("Решения перед генерацией", prompt)
        self.assertIn("подтверждает только решения с меткой `Из созвона`", prompt)
        self.assertIn("не подтверждает пункты с меткой `Предложение`", prompt)
        self.assertIn("Не заполняй его догадками про оплату", prompt)
        self.assertIn("не подставляй карту 1/3/6 месяцев по умолчанию", prompt)
        self.assertNotIn("считай это внутренней деталью согласования", prompt)

    def test_gemini_style_calibration_uses_adult_study_buddy(self) -> None:
        chain = (
            ROOT / "skills/english-roadmap-rewrite/scripts/openrouter_gemini_chat_chain.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("Венни Пака", chain)
        self.assertIn("study buddy", chain)
        self.assertIn("Без иронии", chain)
        self.assertIn("детских образов", chain)
        self.assertIn("Структуру и факты не меняй", chain)

    def test_article_prompt_transfers_symbolic_verification_without_review_numbers(self) -> None:
        prompt = (ROOT / "scripts/consultation_article_prompt.md").read_text(encoding="utf-8")
        self.assertIn("Предварительная наглядная структура статьи", prompt)
        self.assertIn("не показывай служебные номера `[1]`-`[6]`", prompt)
        self.assertIn("Весь маршрут в одной цепочке", prompt)
        self.assertIn("Навык → практическая польза", prompt)

    def test_article_prompt_keeps_the_roadmap_compact_and_vertical(self) -> None:
        prompt = (ROOT / "scripts/consultation_article_prompt.md").read_text(encoding="utf-8")
        self.assertIn("вертикальными этапами", prompt)
        self.assertIn("**Цель:**", prompt)
        self.assertIn("**Практика:**", prompt)
        self.assertIn("**На выходе:**", prompt)
        self.assertIn("Не сокращай остальные разделы", prompt)
        self.assertNotIn("Только для таблицы", prompt)
        self.assertIn("не более 2-3 кратких действий", prompt)


class ArticleEditingContractTests(unittest.TestCase):
    ARTICLE = """# Roadmap

Вступление в спокойном поддерживающем тоне.

## Текущая точка

Первый содержательный абзац с уровнем A1.

Второй содержательный абзац про два занятия в неделю.

- Пункт списка остаётся неизменным.

| Срок | Результат |
| --- | --- |
| 3 месяца | A2 |
"""

    SECTION_ARTICLE = """# Путь к английскому

Короткое вступление для ученика.

## Текущая точка

Сейчас уровень A1.

### Что уже получается

- Поддерживать простой диалог.

## Roadmap

| Срок | Результат |
| --- | --- |
| 3 месяца | A2 |
| 6 месяцев | B1 |
"""

    def test_manifest_and_editor_select_heading_blocks_including_roadmap(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.SECTION_ARTICLE)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(
            [(block["id"], block["number"], block["heading"]) for block in manifest["blocks"]],
            [
                ("b_001", 1, "Путь к английскому"),
                ("b_002", 2, "Текущая точка"),
                ("b_003", 3, "Roadmap"),
            ],
        )
        roadmap = manifest["blocks"][2]
        self.assertIn("| 6 месяцев | B1 |", roadmap["text"])

        editable = ARTICLE_RENDERER.render_html(
            self.SECTION_ARTICLE,
            editor={
                "run_key": "run123",
                "article_version": 1,
                "api_url": "/roadmap-telegram/article-selection",
                "blocks": manifest["blocks"],
            },
        )
        self.assertEqual(editable.count('class="edit-choice"'), 3)
        self.assertIn('data-block-id="b_003"', editable)
        self.assertIn('class="edit-number">3<', editable)
        self.assertIn("6 месяцев", editable)

        student_html = ARTICLE_RENDERER.render_html(self.SECTION_ARTICLE)
        self.assertNotIn("edit-choice", student_html)
        self.assertNotIn("edit-number", student_html)
        self.assertNotIn("data-block-id", student_html)

    def test_legacy_paragraph_manifest_remains_editable_during_schema_transition(self) -> None:
        article = "# Roadmap\n\nСтарый абзац.\n"
        legacy_manifest = {
            "schema_version": 1,
            "article_version": 4,
            "source_sha256": ARTICLE_EDITOR.source_hash(article),
            "blocks": [{
                "id": "p_001",
                "number": 1,
                "type": "paragraph",
                "start_line": 2,
                "end_line": 3,
                "text": "Старый абзац.",
            }],
        }
        payload = ARTICLE_EDITOR.build_gemini_payload(
            article,
            legacy_manifest,
            ["p_001"],
            "Сделай короче.",
            "google/gemini-2.5-pro",
        )
        self.assertIn("p_001", payload["messages"][0]["content"])
        updated = ARTICLE_EDITOR.apply_validated_patch(
            article,
            legacy_manifest,
            ["p_001"],
            {
                "article_version": 4,
                "operations": [{
                    "block_id": "p_001",
                    "action": "replace",
                    "replacement": "Короткий абзац.",
                }],
            },
        )
        self.assertIn("Короткий абзац.", updated)

    def test_manifest_numbers_editable_sections_in_article_order(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        self.assertEqual(manifest["article_version"], 1)
        self.assertEqual(
            [(block["id"], block["number"], block["text"]) for block in manifest["blocks"]],
            [
                ("b_001", 1, "# Roadmap\n\nВступление в спокойном поддерживающем тоне."),
                (
                    "b_002",
                    2,
                    "## Текущая точка\n\nПервый содержательный абзац с уровнем A1.\n\n"
                    "Второй содержательный абзац про два занятия в неделю.\n\n"
                    "- Пункт списка остаётся неизменным.\n\n"
                    "| Срок | Результат |\n| --- | --- |\n| 3 месяца | A2 |",
                ),
            ],
        )

    def test_patch_changes_only_selected_blocks_and_preserves_order(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        updated = ARTICLE_EDITOR.apply_validated_patch(
            self.ARTICLE,
            manifest,
            ["b_002"],
            {
                "article_version": 1,
                "operations": [
                    {
                        "block_id": "b_002",
                        "action": "replace",
                        "replacement": (
                            "## Текущая точка\n\n"
                            "Первый содержательный абзац с уровнем A1.\n\n"
                            "Второй абзац стал короче, но сохранил стиль.\n\n"
                            "- Пункт списка остаётся неизменным.\n\n"
                            "| Срок | Результат |\n| --- | --- |\n| 3 месяца | A2 |"
                        ),
                    },
                ],
            },
        )
        self.assertIn("Первый содержательный абзац с уровнем A1.", updated)
        self.assertIn("Второй абзац стал короче, но сохранил стиль.", updated)
        self.assertNotIn("Второй содержательный абзац про два занятия", updated)
        self.assertIn("- Пункт списка остаётся неизменным.", updated)
        self.assertLess(updated.index("Первый содержательный"), updated.index("Второй абзац стал"))

    def test_delete_creates_new_version_and_fresh_display_numbers(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        updated = ARTICLE_EDITOR.apply_validated_patch(
            self.ARTICLE,
            manifest,
            ["b_002"],
            {
                "article_version": 1,
                "operations": [{"block_id": "b_002", "action": "delete", "replacement": ""}],
            },
        )
        next_manifest = ARTICLE_EDITOR.build_manifest(updated, previous=manifest)
        self.assertEqual(next_manifest["article_version"], 2)
        self.assertEqual([block["number"] for block in next_manifest["blocks"]], [1])
        self.assertEqual([block["id"] for block in next_manifest["blocks"]], ["b_001"])

    def test_multi_block_keep_and_delete_remain_deterministic(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        updated = ARTICLE_EDITOR.apply_validated_patch(
            self.ARTICLE,
            manifest,
            ["b_001", "b_002"],
            {
                "article_version": 1,
                "operations": [
                    {"block_id": "b_001", "action": "keep", "replacement": ""},
                    {"block_id": "b_002", "action": "delete", "replacement": ""},
                ],
            },
        )
        self.assertIn("# Roadmap", updated)
        self.assertNotIn("## Текущая точка", updated)

    def test_patch_requires_exact_selected_set_and_current_version(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        with self.assertRaisesRegex(ValueError, "selected block set"):
            ARTICLE_EDITOR.validate_patch(
                manifest,
                ["b_001", "b_002"],
                {
                    "article_version": 1,
                    "operations": [{"block_id": "b_001", "action": "keep", "replacement": ""}],
                },
            )
        with self.assertRaisesRegex(ValueError, "article version"):
            ARTICLE_EDITOR.validate_patch(
                manifest,
                ["b_002"],
                {
                    "article_version": 2,
                    "operations": [{"block_id": "b_002", "action": "keep", "replacement": ""}],
                },
            )

    def test_section_replacement_reattaches_immutable_heading(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        for replacement in (
            "Новый текст без заголовка.",
            "## Другой заголовок\n\nНовый текст.",
            "## Текущая точка\n\nНовый текст с исходным заголовком.",
        ):
            with self.subTest(replacement=replacement):
                updated = ARTICLE_EDITOR.apply_validated_patch(
                    self.ARTICLE,
                    manifest,
                    ["b_002"],
                    {
                        "article_version": 1,
                        "operations": [{
                            "block_id": "b_002",
                            "action": "replace",
                            "replacement": replacement,
                        }],
                    },
                )
                self.assertEqual(updated.count("## Текущая точка"), 1)
                self.assertNotIn("## Другой заголовок", updated)
                self.assertIn("Новый текст", updated)

    def test_replacement_cannot_inject_new_markdown_blocks_or_html(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        for replacement in (
            "Новый текст.\n\n## Чужой раздел",
            "## Текущая точка\n\nТекст.\n\n## Чужой раздел",
            "## Текущая точка\n\n<script>alert(1)</script>",
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(ValueError, "replacement"):
                    ARTICLE_EDITOR.validate_patch(
                        manifest,
                        ["b_002"],
                        {
                            "article_version": 1,
                            "operations": [
                                {"block_id": "b_002", "action": "replace", "replacement": replacement}
                            ],
                        },
                    )

    def test_gemini_request_uses_strict_schema_and_full_style_context(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        payload = ARTICLE_EDITOR.build_gemini_payload(
            self.ARTICLE,
            manifest,
            ["b_001", "b_002"],
            "Второй оставь, третий сократи.",
            "google/gemini-2.5-pro",
        )
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertTrue(payload["provider"]["require_parameters"])
        prompt = payload["messages"][0]["content"]
        self.assertIn(self.ARTICLE.strip(), prompt)
        self.assertIn("b_002", prompt)
        self.assertIn("только тело раздела", prompt)
        self.assertIn("заголовок вернёт сервер", prompt)
        self.assertIn("Второй оставь, третий сократи", prompt)

    def test_editor_html_has_fixed_numbers_and_never_uses_inner_html(self) -> None:
        manifest = ARTICLE_EDITOR.build_manifest(self.ARTICLE)
        rendered = ARTICLE_RENDERER.render_html(
            self.ARTICLE,
            editor={
                "run_key": "run123",
                "article_version": 1,
                "api_url": "/roadmap-telegram/article-selection",
                "blocks": manifest["blocks"],
            },
        )
        self.assertIn('data-block-id="b_001"', rendered)
        self.assertIn('data-block-id="b_002"', rendered)
        self.assertIn('class="edit-number">1<', rendered)
        self.assertIn('class="edit-number">2<', rendered)
        self.assertIn("window.Telegram && window.Telegram.WebApp", rendered)
        self.assertIn("telegram.initData", rendered)
        self.assertNotIn("innerHTML", rendered)


class ArticleSelectionSecurityTests(unittest.TestCase):
    def signed_init_data(self, token: str, user_id: int, auth_date: int) -> str:
        values = {
            "auth_date": str(auth_date),
            "query_id": "query-1",
            "user": json.dumps({"id": user_id, "first_name": "Teacher"}, separators=(",", ":")),
        }
        check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        return urllib.parse.urlencode(values)

    def test_signed_webapp_data_returns_verified_teacher_id(self) -> None:
        now = 1_800_000_000
        init_data = self.signed_init_data("bot-token", 42, now - 10)
        self.assertEqual(
            WEBHOOK.validate_telegram_webapp_init_data(init_data, "bot-token", now=now),
            "42",
        )

    def test_webapp_data_rejects_bad_signature_and_expired_request(self) -> None:
        now = 1_800_000_000
        valid = self.signed_init_data("bot-token", 42, now - 10)
        with self.assertRaisesRegex(ValueError, "signature"):
            WEBHOOK.validate_telegram_webapp_init_data(valid, "other-token", now=now)
        expired = self.signed_init_data("bot-token", 42, now - 86_401)
        with self.assertRaisesRegex(ValueError, "expired"):
            WEBHOOK.validate_telegram_webapp_init_data(expired, "bot-token", now=now)

    def test_selection_is_bound_to_owner_current_version_and_known_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            manifest = ARTICLE_EDITOR.build_manifest("# Title\n\nOne.\n\nTwo.\n")
            (run_dir / "roadmap-article-blocks.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            registry = {
                "runs": {
                    "run123": {"run_dir": str(run_dir), "audio": "lesson.m4a", "chat_id": "42"}
                },
                "active_articles": {
                    "42": {
                        "status": "active",
                        "run_key": "run123",
                        "run_dir": str(run_dir),
                        "audio": "lesson.m4a",
                        "article_version": 1,
                    }
                },
            }

            selected = WEBHOOK.update_article_selection(
                registry,
                teacher_id="42",
                run_key="run123",
                article_version=1,
                selected_block_ids=["b_001"],
                action="set",
                now="2026-08-26T12:00:00Z",
            )
            self.assertEqual(selected, ["b_001"])
            self.assertEqual(registry["pending_article_edits"]["42"]["selected_block_ids"], ["b_001"])

            with self.assertRaisesRegex(PermissionError, "owner"):
                WEBHOOK.update_article_selection(
                    registry, "99", "run123", 1, ["b_001"], "set", "now"
                )
            with self.assertRaisesRegex(ValueError, "version"):
                WEBHOOK.update_article_selection(
                    registry, "42", "run123", 2, ["b_001"], "set", "now"
                )
            with self.assertRaisesRegex(ValueError, "block"):
                WEBHOOK.update_article_selection(
                    registry, "42", "run123", 1, ["b_999"], "set", "now"
                )

    def test_get_selection_does_not_mutate_and_empty_set_clears_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            manifest = ARTICLE_EDITOR.build_manifest("# Title\n\nOne.\n")
            (run_dir / "roadmap-article-blocks.json").write_text(json.dumps(manifest), encoding="utf-8")
            registry = {
                "runs": {"run123": {"run_dir": str(run_dir), "chat_id": "42"}},
                "active_articles": {
                    "42": {
                        "status": "active",
                        "run_key": "run123",
                        "run_dir": str(run_dir),
                        "audio": "lesson.m4a",
                        "article_version": 1,
                    }
                },
                "pending_article_edits": {
                    "42": {
                        "run_key": "run123",
                        "article_version": 1,
                        "selected_block_ids": ["b_001"],
                    }
                },
            }
            selected = WEBHOOK.update_article_selection(
                registry, "42", "run123", 1, [], "get", "now"
            )
            self.assertEqual(selected, ["b_001"])
            WEBHOOK.update_article_selection(registry, "42", "run123", 1, [], "set", "now")
            self.assertNotIn("42", registry["pending_article_edits"])

    def test_selection_is_rejected_while_previous_article_edit_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            article = "# Roadmap\n\nТекст.\n"
            (run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
            (run_dir / "roadmap-article-blocks.json").write_text(
                json.dumps(ARTICLE_EDITOR.build_manifest(article)), encoding="utf-8"
            )
            (run_dir / "status.json").write_text(
                json.dumps({"article_edit_status": "delivery_started"}), encoding="utf-8"
            )
            registry = {
                "runs": {"run123": {"run_dir": str(run_dir), "audio": "lesson.m4a", "chat_id": "42"}},
                "active_articles": {
                    "42": {
                        "status": "active",
                        "run_key": "run123",
                        "run_dir": str(run_dir),
                        "audio": "lesson.m4a",
                        "article_version": 1,
                    }
                },
            }
            with self.assertRaisesRegex(ValueError, "in progress"):
                WEBHOOK.update_article_selection(
                    registry, "42", "run123", 1, ["b_001"], "set", "now"
                )
            self.assertNotIn("pending_article_edits", registry)


class ArticleEditWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.article = self.run_dir / "roadmap-article.md"
        self.article.write_text(
            "# Roadmap\n\nПервый абзац.\n\n## Второй блок\n\nВторой абзац.\n",
            encoding="utf-8",
        )
        self.manifest = ARTICLE_EDITOR.build_manifest(self.article.read_text(encoding="utf-8"))
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8"
        )
        (self.run_dir / "status.json").write_text(
            json.dumps({"article_status": "done", "telegram_chat_id": "42"}), encoding="utf-8"
        )
        self.job = self.run_dir / "article-edit-jobs" / "job-101.json"
        self.job.parent.mkdir()
        self.job.write_text(json.dumps({
            "job_id": "job-101",
            "status": "queued",
            "run_key": "run123",
            "run_dir": str(self.run_dir),
            "audio": "lesson.m4a",
            "chat_id": "42",
            "article_version": 1,
            "selected_block_ids": ["b_002"],
            "instruction": "Второй абзац сократи.",
        }, ensure_ascii=False), encoding="utf-8")
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fake_run(self, command: list[str], **_kwargs: object):
        self.commands.append(command)
        if command[0] == "renderer":
            output = Path(command[command.index("-o") + 1])
            output.write_text("<html><body>article</body></html>" * 10, encoding="utf-8")
        elif command[0] == "wkhtmltopdf":
            Path(command[-1]).write_bytes(b"%PDF" + b"x" * 300)
        return subprocess.CompletedProcess(command, 0)

    def valid_patch(self) -> dict[str, object]:
        return {
            "article_version": 1,
            "operations": [{
                "block_id": "b_002",
                "action": "replace",
                "replacement": "Второй абзац стал короче.",
            }],
        }

    def test_worker_stages_new_version_and_notifies_once(self) -> None:
        calls: list[dict[str, object]] = []

        def model_request(payload: dict[str, object], _api_key: str) -> dict[str, object]:
            calls.append(payload)
            return self.valid_patch()

        result = ARTICLE_EDITOR.process_edit_job(
            self.job,
            env={"OPENROUTER_API_KEY": "secret", "ARTICLE_EDIT_MODEL": "google/gemini-2.5-pro"},
            model_request=model_request,
            run_command=self.fake_run,
            renderer="renderer",
            notifier="notifier",
        )

        self.assertEqual(result, "done")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.article.read_text(encoding="utf-8").count("## Второй блок"), 1)
        self.assertIn("Второй абзац стал короче.", self.article.read_text(encoding="utf-8"))
        next_manifest = json.loads((self.run_dir / "roadmap-article-blocks.json").read_text(encoding="utf-8"))
        self.assertEqual(next_manifest["article_version"], 2)
        self.assertTrue((self.run_dir / "article-versions" / "v1" / "roadmap-article.md").exists())
        self.assertEqual(len([command for command in self.commands if command[0] == "notifier"]), 1)
        saved_job = json.loads(self.job.read_text(encoding="utf-8"))
        self.assertEqual(saved_job["status"], "done")

    def test_invalid_first_patch_is_retried_once(self) -> None:
        responses = [
            {"article_version": 1, "operations": []},
            self.valid_patch(),
        ]
        result = ARTICLE_EDITOR.process_edit_job(
            self.job,
            env={"OPENROUTER_API_KEY": "secret"},
            model_request=lambda _payload, _key: responses.pop(0),
            run_command=self.fake_run,
            renderer="renderer",
            notifier="notifier",
        )
        self.assertEqual(result, "done")
        self.assertEqual(responses, [])

    def test_retry_names_validation_error_and_records_safe_diagnostics(self) -> None:
        payloads: list[dict[str, object]] = []

        def model_request(payload: dict[str, object], _api_key: str) -> dict[str, object]:
            payloads.append(payload)
            return {
                "article_version": 1,
                "operations": [{
                    "block_id": "b_002",
                    "action": "replace",
                    "replacement": "Новый текст.\n\n## Чужой раздел",
                }],
            }

        with self.assertRaisesRegex(RuntimeError, "Gemini patch validation failed"):
            ARTICLE_EDITOR.process_edit_job(
                self.job,
                env={"OPENROUTER_API_KEY": "secret"},
                model_request=model_request,
                run_command=self.fake_run,
                renderer="renderer",
                notifier="notifier",
            )

        retry_prompt = payloads[1]["messages"][-1]["content"]
        self.assertIn("replacement cannot add a sibling block", retry_prompt)
        diagnostics = json.loads(
            (self.job.parent / self.job.stem / "validation-attempts.json").read_text(encoding="utf-8")
        )
        self.assertEqual([item["attempt"] for item in diagnostics], [1, 2])
        self.assertEqual(diagnostics[0]["error_code"], "sibling_block")
        self.assertEqual(diagnostics[0]["block_id"], "b_002")
        self.assertNotIn("Второй абзац сократи", json.dumps(diagnostics, ensure_ascii=False))

    def test_unparseable_first_response_is_retried_once(self) -> None:
        calls = 0

        def model_request(_payload: dict[str, object], _key: str) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError("invalid structured JSON")
            return self.valid_patch()

        result = ARTICLE_EDITOR.process_edit_job(
            self.job,
            env={"OPENROUTER_API_KEY": "secret"},
            model_request=model_request,
            run_command=self.fake_run,
            renderer="renderer",
            notifier="notifier",
        )
        self.assertEqual(result, "done")
        self.assertEqual(calls, 2)

    def test_two_invalid_patches_leave_current_article_untouched(self) -> None:
        original = self.article.read_text(encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Gemini patch validation failed"):
            ARTICLE_EDITOR.process_edit_job(
                self.job,
                env={"OPENROUTER_API_KEY": "secret"},
                model_request=lambda _payload, _key: {"article_version": 1, "operations": []},
                run_command=self.fake_run,
                renderer="renderer",
                notifier="notifier",
            )
        self.assertEqual(self.article.read_text(encoding="utf-8"), original)
        self.assertEqual(json.loads(self.job.read_text(encoding="utf-8"))["status"], "failed")
        self.assertFalse(any(command[0] == "notifier" for command in self.commands))

    def test_completed_job_is_idempotent(self) -> None:
        job = json.loads(self.job.read_text(encoding="utf-8"))
        job["status"] = "done"
        self.job.write_text(json.dumps(job), encoding="utf-8")
        result = ARTICLE_EDITOR.process_edit_job(
            self.job,
            env={"OPENROUTER_API_KEY": "secret"},
            model_request=lambda *_args: self.fail("model must not be called"),
            run_command=self.fake_run,
            renderer="renderer",
            notifier="notifier",
        )
        self.assertEqual(result, "already_done")
        self.assertEqual(self.commands, [])

    def test_worker_failure_notice_is_visible_and_contains_no_instruction(self) -> None:
        ARTICLE_EDITOR.notify_edit_failure(
            self.job,
            {"PIPELINE_ENV_FILE": "/safe/env"},
            notifier="notifier",
            run_command=self.fake_run,
        )
        command = self.commands[-1]
        self.assertEqual(command[0], "notifier")
        self.assertIn("--text", command)
        text = command[command.index("--text") + 1]
        self.assertIn("Не смог применить правки", text)
        self.assertNotIn("Второй абзац сократи", text)

    def test_notifier_failure_never_leaves_contradictory_done_status(self) -> None:
        def failing_notify(command: list[str], **kwargs: object):
            if command[0] == "notifier":
                raise subprocess.CalledProcessError(1, command)
            return self.fake_run(command, **kwargs)

        with self.assertRaises(subprocess.CalledProcessError):
            ARTICLE_EDITOR.process_edit_job(
                self.job,
                env={"OPENROUTER_API_KEY": "secret"},
                model_request=lambda _payload, _key: self.valid_patch(),
                run_command=failing_notify,
                renderer="renderer",
                notifier="notifier",
            )
        status = json.loads((self.run_dir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["article_edit_status"], "failed")
        self.assertNotIn("article_edit_done_at", status)
        self.assertEqual(json.loads(self.job.read_text(encoding="utf-8"))["status"], "failed")

    def test_stale_worker_lock_is_recovered(self) -> None:
        lock_dir = self.run_dir / ".article-edit.lock"
        lock_dir.mkdir()
        old = time.time() - ARTICLE_EDITOR.ARTICLE_EDIT_LOCK_STALE_SECONDS - 10
        os.utime(lock_dir, (old, old))
        result = ARTICLE_EDITOR.process_edit_job(
            self.job,
            env={"OPENROUTER_API_KEY": "secret"},
            model_request=lambda _payload, _key: self.valid_patch(),
            run_command=self.fake_run,
            renderer="renderer",
            notifier="notifier",
        )
        self.assertEqual(result, "done")
        self.assertFalse(lock_dir.exists())


class CodexArticleScriptTests(unittest.TestCase):
    def test_codex_article_wrapper_has_terra_retry_and_fallback_contract(self) -> None:
        script = (ROOT / "scripts/generate_article_with_codex.sh").read_text(encoding="utf-8")
        runner = (ROOT / "scripts/run_codex_article_hedge.py").read_text(encoding="utf-8")
        self.assertIn('CODEX_ARTICLE_MODEL:-gpt-5.6-terra', script)
        self.assertIn('CODEX_ARTICLE_REASONING_EFFORT:-high', script)
        self.assertIn('CODEX_ARTICLE_HEDGE_DELAY_SECONDS:-65', script)
        self.assertIn('CODEX_ARTICLE_FALLBACK_AFTER_SECONDS:-145', script)
        self.assertIn('CODEX_ARTICLE_PRIMARY_TIMEOUT_SECONDS:-900', script)
        self.assertIn('CODEX_ARTICLE_HEDGE_TIMEOUT_SECONDS:-${CODEX_ARTICLE_TIMEOUT_SECONDS:-80}', script)
        self.assertIn('CODEX_ARTICLE_HEDGE_RUNNER:-/usr/local/bin/run-codex-article-hedge', script)
        self.assertIn('CODEX_ARTICLE_FALLBACK_SCRIPT:-/usr/local/bin/generate-article-with-openrouter', script)
        self.assertIn('model_reasoning_effort=', runner)
        self.assertIn('article_generation_provider', script)
        self.assertIn('article_codex_attempts', script)
        self.assertIn('article_fallback_reason', script)
        self.assertIn('tempfile.mkstemp', runner)
        self.assertIn('start_new_session=True', runner)
        self.assertIn('winner_lock.mkdir()', runner)

    @unittest.skipUnless(Path("/bin/bash").exists(), "requires a POSIX shell")
    def test_codex_success_does_not_call_openrouter_fallback(self) -> None:
        result, status, calls, fallback_called = self._run_wrapper(failures_before_success=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn("gpt-5.6-terra", calls[0])
        self.assertIn('model_reasoning_effort="high"', calls[0])
        self.assertFalse(fallback_called)
        self.assertEqual(status["article_generation_provider"], "codex_cli")
        self.assertEqual(status["article_codex_attempts"], 1)
        self.assertNotIn("article_fallback_reason", status)

    @unittest.skipUnless(Path("/bin/bash").exists(), "requires a POSIX shell")
    def test_second_codex_attempt_can_succeed_without_openrouter_fallback(self) -> None:
        result, status, calls, fallback_called = self._run_wrapper(failures_before_success=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertFalse(fallback_called)
        self.assertEqual(status["article_generation_provider"], "codex_cli")
        self.assertEqual(status["article_codex_attempts"], 2)
        self.assertNotIn("article_fallback_reason", status)

    @unittest.skipUnless(Path("/bin/bash").exists(), "requires a POSIX shell")
    def test_two_codex_failures_call_openrouter_once(self) -> None:
        result, status, calls, fallback_called = self._run_wrapper(failures_before_success=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertTrue(fallback_called)
        self.assertEqual(status["article_generation_provider"], "openrouter_fallback")
        self.assertEqual(status["article_codex_attempts"], 2)
        self.assertEqual(status["article_fallback_reason"], "codex_no_valid_result_before_fallback")

    @unittest.skipUnless(Path("/bin/bash").exists(), "requires a POSIX shell")
    def test_all_provider_failures_are_recorded_in_status(self) -> None:
        result, status, calls, fallback_called = self._run_wrapper(
            failures_before_success=2,
            fallback_succeeds=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)
        self.assertTrue(fallback_called)
        self.assertEqual(status["article_status"], "failed")
        self.assertEqual(status["article_generation_provider"], "failed")
        self.assertEqual(status["article_codex_attempts"], 2)
        self.assertTrue(status["article_fallback_started"])
        self.assertTrue(status["article_codex_last_error"])

    def _run_wrapper(
        self,
        *,
        failures_before_success: int,
        fallback_succeeds: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object], list[list[str]], bool]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            bin_dir = root / "bin"
            run_dir.mkdir()
            bin_dir.mkdir()
            (run_dir / "transcript.md").write_text("Транскрипт", encoding="utf-8")
            (run_dir / "verification.md").write_text("Проверка", encoding="utf-8")
            (run_dir / "status.json").write_text("{}", encoding="utf-8")
            prompt = root / "prompt.md"
            enhancements = root / "enhancements.md"
            prompt.write_text("Шаблон", encoding="utf-8")
            enhancements.write_text("P1", encoding="utf-8")
            calls_path = root / "codex-calls.jsonl"
            fallback_marker = root / "fallback-called"

            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "calls = pathlib.Path(os.environ['FAKE_CODEX_CALLS'])\n"
                "with calls.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "call_count = len(calls.read_text(encoding='utf-8').splitlines())\n"
                "if call_count <= int(os.environ.get('FAKE_CODEX_FAILURES_BEFORE_SUCCESS', '0')):\n"
                "    raise SystemExit(1)\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "out.write_text('# Имя\\n\\n## Roadmap\\n\\n| Период | Результат | Что делаем |\\n| --- | --- | --- |\\n| 1 месяц | Результат | Практика |\\n\\n' + ('Полезный текст. ' * 40), encoding='utf-8')\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            fallback = bin_dir / "fallback"
            fallback.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"touch '{fallback_marker}'\n"
                "if [[ \"${FAKE_FALLBACK_FAIL:-0}\" == 1 ]]; then exit 1; fi\n"
                "printf '# Fallback\\n\\n' > \"$1/roadmap-article.md\"\n"
                "for _ in $(seq 1 40); do printf 'OpenRouter article. ' >> \"$1/roadmap-article.md\"; done\n",
                encoding="utf-8",
            )
            fallback.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "CODEX_BIN": str(fake_codex),
                "CODEX_ARTICLE_HEDGE_RUNNER": str(ROOT / "scripts" / "run_codex_article_hedge.py"),
                "CODEX_ARTICLE_PROMPT": str(prompt),
                "ROADMAP_ENHANCEMENTS_PROMPT": str(enhancements),
                "CODEX_ARTICLE_FALLBACK_SCRIPT": str(fallback),
                "CODEX_ARTICLE_HEDGE_DELAY_SECONDS": "0.3",
                "CODEX_ARTICLE_FALLBACK_AFTER_SECONDS": "0.8",
                "CODEX_ARTICLE_PRIMARY_TIMEOUT_SECONDS": "1.5",
                "CODEX_ARTICLE_HEDGE_TIMEOUT_SECONDS": "0.5",
                "CODEX_ARTICLE_FALLBACK_TIMEOUT_SECONDS": "1.0",
                "CODEX_ARTICLE_POLL_INTERVAL_SECONDS": "0.005",
                "ROADMAP_MARKDOWN_TO_HTML": "missing-roadmap-renderer",
                "FAKE_CODEX_CALLS": str(calls_path),
                "FAKE_CODEX_FAILURES_BEFORE_SUCCESS": str(failures_before_success),
                "FAKE_FALLBACK_FAIL": "0" if fallback_succeeds else "1",
            })
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "scripts/generate_article_with_codex.sh"), str(run_dir)],
                capture_output=True,
                text=True,
                env=env,
            )
            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
            return result, status, calls, fallback_marker.exists()


@unittest.skipUnless(Path("/bin/bash").exists(), "requires a POSIX process model")
class CodexHedgeRunnerTests(unittest.TestCase):
    def run_scenario(
        self,
        plan: list[dict[str, object]],
        *,
        fallback_mode: str = "success",
        fallback_delay: float = 0.01,
        hedge_delay: float = 0.2,
        fallback_after: float = 0.7,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object], str, list[dict[str, object]], list[int], int]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            for name in ("transcript.md", "verification.md", "teacher-notes.md"):
                (run_dir / name).write_text(name, encoding="utf-8")
            (run_dir / "status.json").write_text("{}", encoding="utf-8")
            prompt = run_dir / "prompt.md"
            output = run_dir / "roadmap-article.md"
            last_output = run_dir / "last.md"
            result_path = run_dir / "result.json"
            prompt.write_text("prompt", encoding="utf-8")

            calls_path = root / "calls.json"
            completions_path = root / "completions.json"
            lock_path = root / "calls.lock"
            fallback_calls = root / "fallback-calls.txt"
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import fcntl, json, os, pathlib, sys, time\n"
                "calls_path = pathlib.Path(os.environ['FAKE_CALLS'])\n"
                "lock_path = pathlib.Path(os.environ['FAKE_LOCK'])\n"
                "with lock_path.open('a') as lock:\n"
                "    fcntl.flock(lock, fcntl.LOCK_EX)\n"
                "    calls = json.loads(calls_path.read_text() or '[]') if calls_path.exists() else []\n"
                "    index = len(calls) + 1\n"
                "    calls.append({'index': index, 'pid': os.getpid()})\n"
                "    calls_path.write_text(json.dumps(calls))\n"
                "spec = json.loads(os.environ['FAKE_PLAN'])[index - 1]\n"
                "time.sleep(float(spec.get('delay', 0)))\n"
                "mode = spec.get('mode', 'success')\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
                "if mode == 'success':\n"
                "    out.write_text(('# Winner %d\\n\\n' % index) + ('Useful article. ' * 40))\n"
                "elif mode == 'invalid':\n"
                "    out.write_text('short')\n"
                "else:\n"
                "    raise SystemExit(1)\n"
                "with lock_path.open('a') as lock:\n"
                "    fcntl.flock(lock, fcntl.LOCK_EX)\n"
                "    done = json.loads(pathlib.Path(os.environ['FAKE_COMPLETIONS']).read_text() or '[]') if pathlib.Path(os.environ['FAKE_COMPLETIONS']).exists() else []\n"
                "    done.append(index)\n"
                "    pathlib.Path(os.environ['FAKE_COMPLETIONS']).write_text(json.dumps(done))\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            fake_fallback = root / "fallback"
            fake_fallback.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys, time\n"
                "marker = pathlib.Path(os.environ['FAKE_FALLBACK_CALLS'])\n"
                "marker.write_text(marker.read_text() + '1\\n' if marker.exists() else '1\\n')\n"
                "time.sleep(float(os.environ.get('FAKE_FALLBACK_DELAY', '0.01')))\n"
                "if os.environ.get('FAKE_FALLBACK_MODE') != 'success':\n"
                "    raise SystemExit(1)\n"
                "pathlib.Path(sys.argv[1], 'roadmap-article.md').write_text('# Fallback\\n\\n' + ('OpenRouter article. ' * 40))\n",
                encoding="utf-8",
            )
            fake_fallback.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "FAKE_CALLS": str(calls_path),
                "FAKE_COMPLETIONS": str(completions_path),
                "FAKE_LOCK": str(lock_path),
                "FAKE_PLAN": json.dumps(plan),
                "FAKE_FALLBACK_CALLS": str(fallback_calls),
                "FAKE_FALLBACK_MODE": fallback_mode,
                "FAKE_FALLBACK_DELAY": str(fallback_delay),
            })
            command = [
                sys.executable,
                str(ROOT / "scripts" / "run_codex_article_hedge.py"),
                str(run_dir),
                "--prompt", str(prompt),
                "--output", str(output),
                "--last-output", str(last_output),
                "--result", str(result_path),
                "--codex-bin", str(fake_codex),
                "--fallback-script", str(fake_fallback),
                "--hedge-delay", str(hedge_delay),
                "--fallback-after", str(fallback_after),
                "--primary-timeout", "1.5",
                "--hedge-timeout", "1.0",
                "--fallback-timeout", "1.0",
                "--poll-interval", "0.005",
                "--minimum-bytes", "200",
            ]
            process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=3)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            article = output.read_text(encoding="utf-8") if output.exists() else ""
            calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
            completions = json.loads(completions_path.read_text(encoding="utf-8")) if completions_path.exists() else []
            fallback_count = len(fallback_calls.read_text().splitlines()) if fallback_calls.exists() else 0
            for call in calls:
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(call["pid"]), 0)
            return process, result, article, calls, completions, fallback_count

    def test_primary_finishes_before_hedge_is_started(self) -> None:
        process, result, article, calls, completions, fallback_count = self.run_scenario([
            {"delay": 0.01, "mode": "success"},
        ])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "codex_primary")
        self.assertEqual(len(calls), 1)
        self.assertEqual(completions, [1])
        self.assertEqual(fallback_count, 0)
        self.assertIn("Winner 1", article)

    def test_hedge_wins_and_primary_process_is_cancelled(self) -> None:
        process, result, article, calls, completions, fallback_count = self.run_scenario([
            {"delay": 1.0, "mode": "success"},
            {"delay": 0.01, "mode": "success"},
        ])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "codex_hedge")
        self.assertEqual(len(calls), 2)
        self.assertEqual(completions, [2])
        self.assertEqual(fallback_count, 0)
        self.assertIn("Winner 2", article)

    def test_primary_can_win_after_hedge_has_started(self) -> None:
        process, result, article, calls, completions, fallback_count = self.run_scenario([
            {"delay": 0.35, "mode": "success"},
            {"delay": 0.5, "mode": "success"},
        ])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "codex_primary")
        self.assertEqual(len(calls), 2)
        self.assertEqual(completions, [1])
        self.assertEqual(fallback_count, 0)
        self.assertIn("Winner 1", article)

    def test_invalid_hedge_cannot_win_and_fallback_is_used_once(self) -> None:
        process, result, article, calls, _completions, fallback_count = self.run_scenario([
            {"delay": 1.0, "mode": "success"},
            {"delay": 0.01, "mode": "invalid"},
        ])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "openrouter_fallback")
        self.assertEqual(len(calls), 2)
        self.assertEqual(fallback_count, 1)
        self.assertIn("Fallback", article)

    def test_two_codex_failures_start_only_one_fallback(self) -> None:
        process, result, article, calls, _completions, fallback_count = self.run_scenario([
            {"delay": 0.01, "mode": "fail"},
            {"delay": 0.01, "mode": "fail"},
        ])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "openrouter_fallback")
        self.assertEqual(len(calls), 2)
        self.assertEqual(fallback_count, 1)
        self.assertIn("Fallback", article)

    def test_all_workers_can_fail_without_leaving_an_article(self) -> None:
        process, result, article, calls, _completions, fallback_count = self.run_scenario([
            {"delay": 0.01, "mode": "fail"},
            {"delay": 0.01, "mode": "fail"},
        ], fallback_mode="fail")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(fallback_count, 1)
        self.assertEqual(article, "")

    def test_primary_can_win_after_fallback_has_started(self) -> None:
        process, result, article, calls, _completions, fallback_count = self.run_scenario([
            {"delay": 0.6, "mode": "success"},
            {"delay": 0.01, "mode": "invalid"},
        ], fallback_delay=0.5, fallback_after=0.4)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(result["winner"], "codex_primary")
        self.assertTrue(result["fallback_started"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(fallback_count, 1)
        self.assertIn("Winner 1", article)

    def test_near_simultaneous_codex_results_commit_only_one_winner(self) -> None:
        process, result, article, calls, _completions, fallback_count = self.run_scenario([
            {"delay": 0.5, "mode": "success"},
            {"delay": 0.3, "mode": "success"},
        ], fallback_after=1.0)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(result["winner"], {"codex_primary", "codex_hedge"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(fallback_count, 0)
        self.assertEqual(article.count("# Winner"), 1)


class TelegramVoiceTranscriptionTests(unittest.TestCase):
    def test_webhook_voice_python_uses_standard_pipeline_runtime_fallback(self) -> None:
        source = (ROOT / "scripts/telegram_roadmap_webhook.py").read_text(encoding="utf-8")
        self.assertIn(
            'env.get("TELEGRAM_VOICE_TRANSCRIBE_PYTHON", env.get("PIPELINE_PYTHON", DEFAULT_VOICE_PYTHON))',
            source,
        )

    def test_voice_transcriber_defaults_to_openrouter_without_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "voice.oga"
            audio.write_bytes(b"audio")
            argv = ["transcribe_telegram_voice.py", str(audio)]
            with patch.dict(os.environ, {}, clear=True), \
                patch.object(sys, "argv", argv), \
                patch.object(VOICE_TRANSCRIBER, "transcribe_openrouter", return_value="teacher correction") as openrouter_mock, \
                patch.object(VOICE_TRANSCRIBER, "transcribe_local") as local_mock, \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
                VOICE_TRANSCRIBER.main()

        openrouter_mock.assert_called_once()
        local_mock.assert_not_called()
        self.assertEqual(stdout.getvalue().strip(), "teacher correction")

    def test_webhook_voice_transcription_timeout_is_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "voice.oga"
            audio.write_bytes(b"audio")
            with patch.object(WEBHOOK.subprocess, "run") as run_mock:
                run_mock.return_value.stdout = "ok\n"
                text = WEBHOOK.transcribe_voice(
                    {
                        "voice_python": "python3",
                        "voice_transcriber": "transcribe-telegram-voice",
                        "voice_transcribe_timeout": "777",
                        "voice_provider": "openrouter",
                        "voice_openrouter_model": "openai/whisper-large-v3-turbo",
                        "voice_openrouter_fallback": "local",
                        "voice_local_model": "small",
                        "voice_language": "ru",
                    },
                    audio,
                )

        self.assertEqual(text, "ok")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 777)
        self.assertEqual(
            run_mock.call_args.args[0],
            [
                "python3",
                "transcribe-telegram-voice",
                str(audio),
                "--provider",
                "openrouter",
                "--openrouter-model",
                "openai/whisper-large-v3-turbo",
                "--openrouter-fallback",
                "local",
                "--model",
                "small",
                "--language",
                "ru",
            ],
        )


class GeminiRewriteScriptTests(unittest.TestCase):
    def test_composite_script_contains_required_stages_and_guards(self) -> None:
        script = (ROOT / "scripts/generate_article_with_gemini_rewrite.sh").read_text(encoding="utf-8")
        self.assertIn("CODEX_ARTICLE_SCRIPT", script)
        self.assertIn("GEMINI_REWRITE_SCRIPT", script)
        self.assertIn("GEMINI_TIMEOUT_SECONDS", script)
        self.assertIn("--production-safe", script)
        self.assertIn("roadmap-article-draft.md", script)
        self.assertIn('GEMINI_FINAL="$GEMINI_DIR/final.md"', script)
        self.assertIn("article_status=rewriting", script)
        self.assertIn("gemini_rewrite_status=started", script)
        self.assertIn("gemini_rewrite_status=failed", script)
        self.assertIn("gemini_rewrite_status\"] = \"done\"", script)
        self.assertNotIn('"shorts",', script)
        self.assertNotIn('"reels",', script)
        self.assertNotIn('"foreign company",', script)
        self.assertIn("GEMINI_REWRITE_VALIDATOR", script)
        self.assertIn("article_recovery_status=awaiting_choice", script)
        self.assertIn("gemini_rewrite_reused=true", script)
        self.assertIn("article_source\"] = \"gemini_rewrite\"", script)

    def test_composite_script_reuses_current_draft_and_caps_rewrite_budget(self) -> None:
        script = (ROOT / "scripts/generate_article_with_gemini_rewrite.sh").read_text(encoding="utf-8")
        self.assertIn('NOTES="$RUN_DIR/teacher-notes.md"', script)
        self.assertIn('[[ -s "$DRAFT"', script)
        self.assertIn('"$DRAFT" -nt "$NOTES"', script)
        self.assertIn("GEMINI_REWRITE_MAX_TOKENS", script)
        self.assertIn('GEMINI_ARGS+=(--max-tokens "$GEMINI_MAX_TOKENS")', script)
        self.assertIn('if value == "__DELETE__"', script)
        self.assertIn('"article_done_at=__DELETE__"', script)
        self.assertIn('data.pop("gemini_rewrite_failed_reason", None)', script)

    def test_processor_defaults_to_composite_gemini_script(self) -> None:
        self.assertEqual(APPROVED.DEFAULT_ARTICLE_SCRIPT, "/usr/local/bin/generate-article-with-gemini-rewrite")


class OpenRouterRoadmapGeneratorTests(unittest.TestCase):
    def test_article_prompt_uses_verification_teacher_notes_enhancements_and_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            prompt = root / "article-prompt.md"
            enhancements = root / "enhancements.md"
            prompt.write_text("ARTICLE TEMPLATE", encoding="utf-8")
            enhancements.write_text("P1 Progress.me", encoding="utf-8")
            (run_dir / "verification.md").write_text("VERIFICATION", encoding="utf-8")
            (run_dir / "teacher-notes.md").write_text("P1 добавить", encoding="utf-8")
            (run_dir / "transcript.md").write_text("TRANSCRIPT", encoding="utf-8")

            built = OPENROUTER_GENERATOR.build_article_prompt(run_dir, prompt, enhancements)

        self.assertIn("ARTICLE TEMPLATE", built)
        self.assertIn("VERIFICATION", built)
        self.assertIn("P1 добавить", built)
        self.assertIn("P1 Progress.me", built)
        self.assertIn("TRANSCRIPT", built)

    def test_openrouter_wrappers_do_not_call_codex(self) -> None:
        verification = (ROOT / "scripts/generate_verification_with_openrouter.sh").read_text(encoding="utf-8")
        article = (ROOT / "scripts/generate_article_with_openrouter.sh").read_text(encoding="utf-8")
        self.assertIn("--mode verification", verification)
        self.assertIn("--mode article", article)
        self.assertIn("openrouter-roadmap-generate", verification)
        self.assertIn("openrouter-roadmap-generate", article)
        self.assertIn("OPENROUTER_ARTICLE_MAX_TOKENS", article)
        self.assertIn('--max-tokens "$MAX_TOKENS"', article)
        self.assertNotIn("codex ", verification)
        self.assertNotIn("codex ", article)

    def test_pipeline_runner_is_env_configurable_and_defaults_to_codex_generation(self) -> None:
        runner = (ROOT / "scripts/notion_pipeline_runner.sh").read_text(encoding="utf-8")
        self.assertIn("/etc/zoom-audio-pipeline/pipeline.env", runner)
        self.assertIn('telegram-notion-archive-worker --env-file "$ENV_FILE"', runner)
        self.assertIn('notion-pull-audio --env-file "$ENV_FILE"', runner)
        self.assertIn("VERIFICATION_SCRIPT:-/usr/local/bin/generate-verification-with-openrouter", runner)
        self.assertIn("ARTICLE_DRAFT_SCRIPT:-/usr/local/bin/generate-article-with-codex", runner)
        self.assertIn('LOCAL_STT_MODEL="${LOCAL_STT_MODEL:-tiny}"', runner)
        self.assertIn('--model "$LOCAL_STT_MODEL"', runner)
        self.assertIn('--device "$LOCAL_STT_DEVICE"', runner)
        self.assertIn('--compute-type "$LOCAL_STT_COMPUTE_TYPE"', runner)
        self.assertIn('--language "$LOCAL_STT_LANGUAGE"', runner)
        self.assertIn('--transcribing-stale-after-sec "$TRANSCRIPTION_STALE_AFTER_SEC"', runner)
        self.assertIn('process-approved-roadmaps --article-script "$ARTICLE_SCRIPT"', runner)
        self.assertNotIn("/root/codex-audio/nastya-a2/.venv/bin/python", runner)

    def test_notion_webhook_supports_public_health_path(self) -> None:
        receiver = (ROOT / "scripts/notion_webhook_receiver.py").read_text(encoding="utf-8")
        self.assertIn('"/notion/health"', receiver)

    def test_packaging_installer_contains_required_files(self) -> None:
        installer = (ROOT / "scripts/install_vps.sh").read_text(encoding="utf-8")
        doctor = (ROOT / "scripts/doctor_vps.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        for expected in [
            "notion-webhook-receiver",
            "openrouter-roadmap-generate",
            "generate-verification-with-openrouter",
            "generate-article-with-openrouter",
            "generate-article-with-codex",
            "roadmap-article-editor",
            "validate-gemini-rewrite",
            "roadmap-pipeline-doctor",
            "consultation_verification_prompt.md",
            "consultation_article_prompt.md",
            "roadmap_enhancement_options.md",
            "notion-pipeline-poll.service",
            "notion-webhook-receiver.service",
            "telegram-roadmap-webhook.service",
            "configure-pipeline-env-from-legacy",
        ]:
            self.assertIn(expected, installer)
        self.assertIn("TELEGRAM_BOT_TOKEN", doctor)
        self.assertIn("OPENROUTER_API_KEY", doctor)
        self.assertIn("ROADMAP_PUBLIC_BASE_URL", doctor)
        self.assertIn("roadmap-article-editor", doctor)
        self.assertIn("python3 scripts/roadmap_pipeline_tests.py", workflow)
        self.assertTrue((ROOT / "scripts/bootstrap_ubuntu.sh").exists())
        self.assertTrue((ROOT / "docs/HANDOFF_DEPLOY.md").exists())
        self.assertTrue((ROOT / "deploy/Caddyfile.example").exists())

    def test_env_migrator_merges_legacy_files_without_printing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "pipeline.env"
            target.write_text("TELEGRAM_BOT_TOKEN=\nROADMAP_PUBLIC_ROOT=/custom/public\n", encoding="utf-8")
            telegram = root / "roadmap-bot.env"
            telegram.write_text(
                "TELEGRAM_BOT_TOKEN=bot-secret\nTELEGRAM_WEBHOOK_SECRET=webhook-secret\nTELEGRAM_CHAT_ID=42\n",
                encoding="utf-8",
            )
            notion = root / "notion.env"
            notion.write_text("NOTION_API_KEY=notion-secret\nNOTION_TARGET=https://notion.example/page\n", encoding="utf-8")
            openrouter = root / "openrouter.env"
            openrouter.write_text("OPENROUTER_API_KEY=openrouter-secret\n", encoding="utf-8")
            telethon = root / "telegram-e2e.env"
            telethon.write_text(
                "SHORTTALK_REAL_TG_API_ID=123\nSHORTTALK_REAL_TG_API_HASH=hash-secret\n",
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "configure_pipeline_env_from_legacy.py",
                "--target",
                str(target),
                "--source",
                str(telegram),
                "--source",
                str(notion),
                "--source",
                str(openrouter),
                "--source",
                str(telethon),
            ]), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(ENV_MIGRATOR.main(), 0)

            output = stdout.getvalue()
            merged = ENV_MIGRATOR.load_env(target)
            self.assertIn("configured_keys=", output)
            self.assertNotIn("bot-secret", output)
            self.assertNotIn("hash-secret", output)
            self.assertEqual(merged["TELEGRAM_BOT_TOKEN"], "bot-secret")
            self.assertEqual(merged["TELEGRAM_API_BASE_URL"], "http://127.0.0.1:8081")
            self.assertEqual(merged["TELEGRAM_LOCAL_API_ID"], "123")
            self.assertEqual(merged["TELEGRAM_LOCAL_API_HASH"], "hash-secret")
            self.assertEqual(merged["TELEGRAM_API_ID"], "123")
            self.assertEqual(merged["TELEGRAM_API_HASH"], "hash-secret")
            self.assertEqual(merged["ROADMAP_PUBLIC_ROOT"], "/custom/public")

    def test_service_templates_use_single_pipeline_env_file(self) -> None:
        telegram_service = (ROOT / "deploy/systemd/telegram-roadmap-webhook.service").read_text(encoding="utf-8")
        notion_service = (ROOT / "deploy/systemd/notion-webhook-receiver.service").read_text(encoding="utf-8")
        local_bot_api_service = (ROOT / "deploy/systemd/telegram-bot-api-local.service").read_text(encoding="utf-8")
        installer = (ROOT / "scripts/install_vps.sh").read_text(encoding="utf-8")
        self.assertIn("--env-file /etc/zoom-audio-pipeline/pipeline.env", telegram_service)
        self.assertIn("--notion-env-file /etc/zoom-audio-pipeline/pipeline.env", telegram_service)
        self.assertIn("--env-file /etc/zoom-audio-pipeline/pipeline.env", notion_service)
        self.assertIn("--webhook-env-file /etc/zoom-audio-pipeline/notion-webhook.env", notion_service)
        self.assertIn("EnvironmentFile=/etc/zoom-audio-pipeline/pipeline.env", local_bot_api_service)
        self.assertIn("ExecStart=/usr/local/bin/telegram-bot-api", local_bot_api_service)
        self.assertNotIn("--api-hash", local_bot_api_service)
        self.assertIn("telegram-bot-api-local.service", installer)

    def test_notify_can_take_public_base_url_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "verification.md").write_text(VERIFICATION_MD, encoding="utf-8")
            env_file = root / "pipeline.env"
            env_file.write_text(
                "\n".join([
                    "TELEGRAM_BOT_TOKEN=token",
                    "TELEGRAM_CHAT_ID=42",
                    "ROADMAP_PUBLIC_BASE_URL=https://roadmap.example.com/roadmap-reader",
                ]),
                encoding="utf-8",
            )
            sent: list[dict[str, object]] = []

            def fake_telegram_request(
                _token: str,
                method: str,
                payload: dict[str, object] | None = None,
                **_kwargs: object,
            ):
                if method == "sendMessage" and payload:
                    sent.append(payload)
                return {"ok": True, "result": {"message_id": 101}}

            with patch.object(sys, "argv", [
                "telegram_roadmap_notify.py",
                "--env-file",
                str(env_file),
                "--stage",
                "verification_ready",
                "--audio",
                "lesson.m4a",
                "--run-dir",
                str(run_dir),
                "--registry-file",
                str(root / "registry.json"),
                "--public-root",
                str(root / "public"),
            ]), \
                patch.object(NOTIFY, "telegram_request", side_effect=fake_telegram_request), \
                patch.object(NOTIFY.subprocess, "run"):
                self.assertEqual(NOTIFY.main(), 0)

        url = sent[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]  # type: ignore[index]
        self.assertTrue(str(url).startswith("https://roadmap.example.com/roadmap-reader/"))


class OpenRouterTranscriptionTests(unittest.TestCase):
    def test_openrouter_transcription_writes_standard_artifacts_without_secret(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps(
                    {
                        "text": "Привет. Это тестовая расшифровка.",
                        "usage": {"seconds": 8.5, "cost": 0.0001},
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        captured: dict[str, object] = {}

        def fake_urlopen(req, timeout: int):
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret-key"}, clear=False), \
            patch.object(PROCESS_AUDIO.request, "urlopen", side_effect=fake_urlopen):
            root = Path(tmp)
            audio_path = root / "lesson.m4a"
            run_dir = root / "run"
            run_dir.mkdir()
            audio_path.write_bytes(b"fake-audio")

            metadata = PROCESS_AUDIO.transcribe_audio(
                audio_path,
                run_dir,
                provider="openrouter",
                model_name="base",
                device="cpu",
                compute_type="int8",
                language="ru",
                beam_size=5,
                openrouter_model="openai/whisper-large-v3-turbo",
                openrouter_api_key_env="OPENROUTER_API_KEY",
                openrouter_api_key_file="",
                openrouter_endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                openrouter_timeout=123,
                openrouter_retries=1,
                openrouter_retry_delay=0,
                openrouter_compress_threshold_mb=20,
                openrouter_ffmpeg="ffmpeg",
                openrouter_ffmpeg_timeout=300,
            )

            self.assertEqual((run_dir / "transcript-plain.txt").read_text(encoding="utf-8"), "Привет. Это тестовая расшифровка.\n")
            self.assertIn("Привет. Это тестовая расшифровка.", (run_dir / "transcript.md").read_text(encoding="utf-8"))
            self.assertEqual(metadata["provider"], "openrouter")
            self.assertEqual(metadata["model"], "openai/whisper-large-v3-turbo")
            self.assertFalse(metadata["timestamps"])
            self.assertEqual(metadata["duration"], 8.5)
            self.assertEqual(metadata["audio_format"], "m4a")
            self.assertEqual(captured["timeout"], 123)
            self.assertEqual(captured["payload"]["input_audio"]["format"], "m4a")  # type: ignore[index]
            self.assertEqual(captured["payload"]["language"], "ru")  # type: ignore[index]

            all_artifacts = "\n".join(path.read_text(encoding="utf-8") for path in run_dir.iterdir())
            self.assertNotIn("test-secret-key", all_artifacts)

    def test_openrouter_transcription_requires_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            audio_path = root / "lesson.m4a"
            run_dir = root / "run"
            run_dir.mkdir()
            audio_path.write_bytes(b"fake-audio")

            with self.assertRaisesRegex(RuntimeError, "OpenRouter API key is missing"):
                PROCESS_AUDIO.transcribe_audio_openrouter(
                    audio_path,
                    run_dir,
                    model_name="openai/whisper-large-v3-turbo",
                    language="ru",
                    api_key_env="OPENROUTER_API_KEY",
                    api_key_file=str(root / "missing-key"),
                    endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                    timeout=10,
                    retries=1,
                    retry_delay=0,
                    compress_threshold_mb=20,
                    ffmpeg_path="ffmpeg",
                    ffmpeg_timeout=300,
                )

    def test_openrouter_transcription_can_read_key_file(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"text": "ok"}'

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {}, clear=True), \
            patch.object(PROCESS_AUDIO.request, "urlopen", return_value=FakeResponse()) as urlopen_mock:
            root = Path(tmp)
            audio_path = root / "lesson.mp3"
            run_dir = root / "run"
            key_file = root / "api_key"
            run_dir.mkdir()
            audio_path.write_bytes(b"fake-audio")
            key_file.write_text("file-secret-key\n", encoding="utf-8")

            PROCESS_AUDIO.transcribe_audio_openrouter(
                audio_path,
                run_dir,
                model_name="openai/whisper-large-v3-turbo",
                language="ru",
                api_key_env="OPENROUTER_API_KEY",
                api_key_file=str(key_file),
                endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                timeout=10,
                retries=1,
                retry_delay=0,
                compress_threshold_mb=20,
                ffmpeg_path="ffmpeg",
                ffmpeg_timeout=300,
            )

            req = urlopen_mock.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "Bearer file-secret-key")
            self.assertNotIn("file-secret-key", (run_dir / "transcript-meta.json").read_text(encoding="utf-8"))

    def test_openrouter_retries_retriable_http_errors_before_success(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"text": "ok after retry", "usage": {"seconds": 1}}'

        failures = [
            HTTPError("url", 502, "bad gateway", {}, None),
            HTTPError("url", 503, "unavailable", {}, None),
        ]
        for failure in failures:
            failure.fp = io.BytesIO(b"temporary upstream error")

        def fake_urlopen(_req, timeout: int):
            if failures:
                raise failures.pop(0)
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret-key"}, clear=False), \
            patch.object(PROCESS_AUDIO.request, "urlopen", side_effect=fake_urlopen) as urlopen_mock, \
            patch.object(PROCESS_AUDIO.time, "sleep"):
            root = Path(tmp)
            audio_path = root / "lesson.m4a"
            run_dir = root / "run"
            run_dir.mkdir()
            audio_path.write_bytes(b"fake-audio")

            metadata = PROCESS_AUDIO.transcribe_audio_openrouter(
                audio_path,
                run_dir,
                model_name="openai/whisper-large-v3-turbo",
                language="ru",
                api_key_env="OPENROUTER_API_KEY",
                api_key_file="",
                endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                timeout=10,
                retries=3,
                retry_delay=0,
                compress_threshold_mb=20,
                ffmpeg_path="ffmpeg",
                ffmpeg_timeout=300,
            )

            self.assertEqual(urlopen_mock.call_count, 3)
            self.assertEqual(metadata["openrouter_attempts"], 3)
            self.assertEqual(len(metadata["openrouter_retry_errors"]), 2)
            self.assertEqual((run_dir / "transcript-plain.txt").read_text(encoding="utf-8"), "ok after retry\n")

    def test_openrouter_does_not_retry_non_retriable_http_error(self) -> None:
        failure = HTTPError("url", 401, "unauthorized", {}, None)
        failure.fp = io.BytesIO(b"unauthorized")

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret-key"}, clear=False), \
            patch.object(PROCESS_AUDIO.request, "urlopen", side_effect=failure) as urlopen_mock, \
            patch.object(PROCESS_AUDIO.time, "sleep") as sleep_mock:
            root = Path(tmp)
            audio_path = root / "lesson.m4a"
            run_dir = root / "run"
            run_dir.mkdir()
            audio_path.write_bytes(b"fake-audio")

            with self.assertRaises(PROCESS_AUDIO.OpenRouterTranscriptionError):
                PROCESS_AUDIO.transcribe_audio_openrouter(
                    audio_path,
                    run_dir,
                    model_name="openai/whisper-large-v3-turbo",
                    language="ru",
                    api_key_env="OPENROUTER_API_KEY",
                    api_key_file="",
                    endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                    timeout=10,
                    retries=3,
                    retry_delay=0,
                    compress_threshold_mb=20,
                    ffmpeg_path="ffmpeg",
                    ffmpeg_timeout=300,
                )

            self.assertEqual(urlopen_mock.call_count, 1)
            sleep_mock.assert_not_called()

    def test_openrouter_compresses_large_audio_before_upload(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return b'{"text": "compressed ok"}'

        captured: dict[str, object] = {}

        def fake_run(command, check: bool, stdout, stderr, timeout: int):
            Path(command[-1]).write_bytes(b"mp3-small")

        def fake_urlopen(req, timeout: int):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-secret-key"}, clear=False), \
            patch.object(PROCESS_AUDIO.subprocess, "run", side_effect=fake_run) as run_mock, \
            patch.object(PROCESS_AUDIO.request, "urlopen", side_effect=fake_urlopen):
            root = Path(tmp)
            audio_path = root / "lesson.m4a"
            run_dir = root / "run"
            run_dir.mkdir()
            audio_path.write_bytes(b"x" * 1024)

            metadata = PROCESS_AUDIO.transcribe_audio_openrouter(
                audio_path,
                run_dir,
                model_name="openai/whisper-large-v3-turbo",
                language="ru",
                api_key_env="OPENROUTER_API_KEY",
                api_key_file="",
                endpoint="https://openrouter.ai/api/v1/audio/transcriptions",
                timeout=10,
                retries=1,
                retry_delay=0,
                compress_threshold_mb=0.0001,
                ffmpeg_path="ffmpeg",
                ffmpeg_timeout=300,
            )

            self.assertEqual(run_mock.call_count, 1)
            self.assertEqual(captured["payload"]["input_audio"]["format"], "mp3")  # type: ignore[index]
            self.assertTrue(metadata["openrouter_upload"]["compressed"])
            self.assertEqual(metadata["openrouter_upload"]["request_audio_bytes"], len(b"mp3-small"))


class ProcessNewAudioIntakeNotifyTests(unittest.TestCase):
    def test_telegram_oga_audio_is_supported_by_processor(self) -> None:
        self.assertIn(".oga", PROCESS_AUDIO.AUDIO_SUFFIXES)
        self.assertIn(".ogg", PROCESS_AUDIO.AUDIO_SUFFIXES)
        self.assertIn(".opus", PROCESS_AUDIO.AUDIO_SUFFIXES)

    def test_identical_audio_in_distinct_inbox_paths_gets_distinct_process_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox"
            inbox.mkdir()
            first = inbox / "lesson.m4a"
            second = inbox / "lesson-2.m4a"
            first.write_bytes(b"same audio")
            second.write_bytes(b"same audio")

            first_key = PROCESS_AUDIO.file_key(first)
            second_key = PROCESS_AUDIO.file_key(second)

        self.assertNotEqual(first_key, second_key)

    def test_telegram_intake_sidecar_adds_chat_id_to_notify_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio_path = root / "inbox" / "lesson.m4a"
            run_dir = root / "runs" / "run"
            audio_path.parent.mkdir()
            run_dir.mkdir(parents=True)
            audio_path.write_bytes(b"audio")
            sidecar = PROCESS_AUDIO.intake_sidecar_path(audio_path)
            sidecar.write_text(
                json.dumps({"telegram_chat_id": "1607901073", "intake_id": "telegram:abc"}, ensure_ascii=False),
                encoding="utf-8",
            )

            meta = PROCESS_AUDIO.load_intake_sidecar(audio_path)
            args = PROCESS_AUDIO.notify_args("verification_ready", audio_path.name, run_dir, meta)

        self.assertEqual(args[:6], ["--stage", "verification_ready", "--audio", "lesson.m4a", "--run-dir", str(run_dir)])
        self.assertEqual(args[-2:], ["--chat-id", "1607901073"])

    def test_openrouter_fallback_failure_marks_status_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "inbox"
            runs = root / "runs"
            inbox.mkdir()
            runs.mkdir()
            audio = inbox / "lesson.m4a"
            audio.write_bytes(b"audio")
            state = root / "state.json"
            events = root / "events.jsonl"

            def fake_transcribe(_audio, _run_dir, *, provider, **_kwargs):
                if provider == "openrouter":
                    raise PROCESS_AUDIO.OpenRouterTranscriptionError("HTTP 502", status_code=502, retriable=True)
                raise ModuleNotFoundError("No module named 'faster_whisper'")

            with patch.object(sys, "argv", [
                "process_new_audio.py",
                "--inbox-dir",
                str(inbox),
                "--runs-dir",
                str(runs),
                "--state-file",
                str(state),
                "--events-file",
                str(events),
                "--transcription-provider",
                "openrouter",
            ]), patch.object(PROCESS_AUDIO, "transcribe_audio", side_effect=fake_transcribe):
                with self.assertRaises(ModuleNotFoundError):
                    PROCESS_AUDIO.main()

            run_dirs = list(runs.iterdir())
            self.assertEqual(len(run_dirs), 1)
            status = json.loads((run_dirs[0] / "status.json").read_text(encoding="utf-8"))
            processed = json.loads(state.read_text(encoding="utf-8"))["processed"]
            entry = next(iter(processed.values()))
            self.assertEqual(status["status"], "error")
            self.assertEqual(entry["status"], "error")
            self.assertIn("faster_whisper", status["error"])
            event_text = events.read_text(encoding="utf-8")
            self.assertIn("transcription_provider_fallback", event_text)
            self.assertIn("transcription_error", event_text)

    def test_stale_transcribing_entry_is_recovered_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "inbox"
            runs = root / "runs"
            old_run = runs / "old-run"
            inbox.mkdir()
            old_run.mkdir(parents=True)
            audio = inbox / "lesson.m4a"
            audio.write_bytes(b"audio")
            key = PROCESS_AUDIO.file_key(audio)
            old_started = "2000-01-01T00:00:00Z"
            state = root / "state.json"
            events = root / "events.jsonl"
            state.write_text(
                json.dumps(
                    {
                        "processed": {
                            key: {
                                "status": "transcribing",
                                "audio_path": str(audio),
                                "audio_name": audio.name,
                                "run_dir": str(old_run),
                                "started_at": old_started,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (old_run / "status.json").write_text(
                json.dumps({"status": "transcribing", "started_at": old_started}, ensure_ascii=False),
                encoding="utf-8",
            )

            def fake_transcribe(_audio, run_dir, *, provider, **_kwargs):
                return PROCESS_AUDIO.write_transcript_artifacts(
                    audio,
                    run_dir,
                    plain_lines=["ok"],
                    timed_lines=["[00:00 - 00:01] ok"],
                    markdown_lines=["# Transcript", "", "ok"],
                    metadata={"provider": provider},
                )

            with patch.object(sys, "argv", [
                "process_new_audio.py",
                "--inbox-dir",
                str(inbox),
                "--runs-dir",
                str(runs),
                "--state-file",
                str(state),
                "--events-file",
                str(events),
                "--transcribing-stale-after-sec",
                "1",
            ]), patch.object(PROCESS_AUDIO, "transcribe_audio", side_effect=fake_transcribe):
                self.assertEqual(PROCESS_AUDIO.main(), 0)

            old_status = json.loads((old_run / "status.json").read_text(encoding="utf-8"))
            processed = json.loads(state.read_text(encoding="utf-8"))["processed"]
            entry = processed[key]
            self.assertEqual(old_status["status"], "error")
            self.assertEqual(entry["status"], "transcribed")
            self.assertNotEqual(entry["run_dir"], str(old_run))
            self.assertTrue(Path(entry["transcript"]).exists())
            event_text = events.read_text(encoding="utf-8")
            self.assertIn("transcription_stale_recovered", event_text)
            self.assertIn("transcription_done", event_text)


class WebhookApprovalTests(TempRunMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.safe_patch = patch.object(
            WEBHOOK,
            "safe_telegram_request",
            side_effect=lambda _token, method, payload, **_kwargs: self.sent.append((method, payload)),
        )
        self.run_patch = patch.object(WEBHOOK.subprocess, "run")
        self.start_patch = patch.object(WEBHOOK, "start_pipeline_async")
        self.safe_patch.start()
        self.run_mock = self.run_patch.start()
        self.start_mock = self.start_patch.start()

    def tearDown(self) -> None:
        self.start_patch.stop()
        self.run_patch.stop()
        self.safe_patch.stop()
        super().tearDown()

    def test_approval_text_detection(self) -> None:
        approvals = ["согласен", "Совсем согласен", "всё верно", "подтверждаю", "делай статью"]
        for text in approvals:
            self.assertTrue(WEBHOOK.is_approval_text(text), text)

        not_approvals = ["P1 добавить", "P7 да", "цена 3000", "исправь пункт 2"]
        for text in not_approvals:
            self.assertFalse(WEBHOOK.is_approval_text(text), text)

    def test_approve_button_creates_teacher_note_and_cleans_pending(self) -> None:
        self.handler().handle_callback(
            {
                "id": "cb1",
                "data": "roadmap:approve:abc123",
                "message": {"chat": {"id": 42}},
            }
        )

        status = self.status()
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        notes = self.notes()

        self.assertEqual(status["teacher_verification_decision"], "approved_for_article")
        self.assertEqual(status["telegram_chat_id"], "42")
        self.assertIn("цену, оплату, расписание", notes)
        self.assertIn("PDF-опции P1-P14", notes)
        self.assertNotIn("42", registry.get("pending_reviews", {}))
        self.assertIn("verification_approve", self.events.read_text(encoding="utf-8"))
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Принято в работу" in text for text in sent_texts))
        self.assertTrue(any("HTML и PDF" in text for text in sent_texts))

    def test_repeated_approve_button_does_not_start_duplicate_pipeline(self) -> None:
        (self.run_dir / "status.json").write_text(
            json.dumps({"article_status": "started"}, ensure_ascii=False),
            encoding="utf-8",
        )

        self.handler().handle_callback(
            {
                "id": "cb1",
                "data": "roadmap:approve:abc123",
                "message": {"chat": {"id": 42}},
            }
        )

        self.start_mock.assert_not_called()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Уже принято в работу" in text for text in sent_texts))

    def test_approve_button_uses_callback_from_as_chat_fallback(self) -> None:
        self.handler().handle_callback(
            {
                "id": "cb1",
                "data": "roadmap:approve:abc123",
                "from": {"id": 42},
            }
        )

        self.start_mock.assert_called_once()
        sent_messages = [payload for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(sent_messages)
        self.assertEqual(sent_messages[-1]["chat_id"], 42)
        self.assertIn("Принято в работу", sent_messages[-1]["text"])

    def test_unknown_callback_sends_visible_chat_message_when_possible(self) -> None:
        self.handler().handle_callback(
            {
                "id": "cb1",
                "data": "roadmap:approve:missing",
                "from": {"id": 42},
            }
        )

        self.start_mock.assert_not_called()
        sent_messages = [payload for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(sent_messages)
        self.assertIn("Не нашёл этот запуск", sent_messages[-1]["text"])

    def test_gemini_retry_callback_queues_only_gemini_stage(self) -> None:
        (self.run_dir / "status.json").write_text(json.dumps({
            "article_status": "recovery_required",
            "article_recovery_status": "awaiting_choice",
            "telegram_chat_id": "42",
        }), encoding="utf-8")

        self.handler().handle_callback({
            "id": "cb-retry",
            "data": "roadmap:gemini_retry:abc123",
            "message": {"chat": {"id": 42}},
        })

        status = self.status()
        self.assertEqual(status["article_status"], "recovery_requested")
        self.assertEqual(status["article_recovery_action"], "retry_gemini")
        self.assertTrue(status["gemini_force_retry"])
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Принято в работу" in text for text in sent_texts))

    def test_gpt_version_callback_queues_draft_without_duplicate_start(self) -> None:
        (self.run_dir / "status.json").write_text(json.dumps({
            "article_status": "recovery_required",
            "article_recovery_status": "awaiting_choice",
            "telegram_chat_id": "42",
        }), encoding="utf-8")
        callback = {
            "id": "cb-gpt",
            "data": "roadmap:gpt_version:abc123",
            "message": {"chat": {"id": 42}},
        }

        self.handler().handle_callback(callback)
        self.handler().handle_callback(callback)

        status = self.status()
        self.assertEqual(status["article_status"], "recovery_requested")
        self.assertEqual(status["article_recovery_action"], "use_gpt_draft")
        self.assertEqual(self.start_mock.call_count, 1)

    def test_second_recovery_choice_cannot_replace_active_action(self) -> None:
        (self.run_dir / "status.json").write_text(json.dumps({
            "article_status": "recovery_required",
            "article_recovery_status": "awaiting_choice",
            "telegram_chat_id": "42",
        }), encoding="utf-8")

        self.handler().handle_callback({
            "id": "cb-retry",
            "data": "roadmap:gemini_retry:abc123",
            "message": {"chat": {"id": 42}},
        })
        self.handler().handle_callback({
            "id": "cb-gpt",
            "data": "roadmap:gpt_version:abc123",
            "message": {"chat": {"id": 42}},
        })

        status = self.status()
        self.assertEqual(status["article_recovery_action"], "retry_gemini")
        self.assertEqual(self.start_mock.call_count, 1)

    def test_recovery_callback_rejects_another_chat(self) -> None:
        (self.run_dir / "status.json").write_text(json.dumps({
            "article_status": "recovery_required",
            "article_recovery_status": "awaiting_choice",
            "telegram_chat_id": "42",
        }), encoding="utf-8")

        self.handler().handle_callback({
            "id": "cb-foreign",
            "data": "roadmap:gpt_version:abc123",
            "message": {"chat": {"id": 99}},
        })

        self.assertEqual(self.status()["article_status"], "recovery_required")
        self.start_mock.assert_not_called()

    def test_text_approval_same_as_button(self) -> None:
        self.handler().handle_message({"chat": {"id": 42}, "text": "совсем согласен"})

        status = self.status()
        notes = self.notes()
        self.assertEqual(status["teacher_verification_decision"], "approved_for_article")
        self.assertNotIn("teacher_revision_notes_received", status)
        self.assertIn("цену, оплату, расписание", notes)
        self.assertIn("PDF-опции P1-P14", notes)
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Принято в работу" in text for text in sent_texts))

    def test_voice_approval_same_as_button(self) -> None:
        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("совсем согласен", "voice")):
            self.handler().handle_message({"chat": {"id": 42}, "voice": {"file_id": "voice-file"}})

        status = self.status()
        notes = self.notes()
        self.assertEqual(status["teacher_verification_decision"], "approved_for_article")
        self.assertNotIn("teacher_revision_notes_received", status)
        self.assertIn("PDF-опции P1-P14", notes)
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertIn("Голосовое получил", sent_texts[0])
        self.assertTrue(any("Принято в работу" in text for text in sent_texts))

    def test_revision_text_saved_as_teacher_notes(self) -> None:
        self.handler().handle_message({"chat": {"id": 42}, "text": "P1 добавить, цену оставить, вторник 19:00"})

        status = self.status()
        notes = self.notes()
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(status["teacher_verification_decision"], "approved_for_article")
        self.assertTrue(status["teacher_revision_notes_received"])
        self.assertIn("P1 добавить", notes)
        self.assertIn("вторник 19:00", notes)
        self.assertNotIn("42", registry.get("pending_reviews", {}))
        self.assertIn("verification_revision_notes_received", self.events.read_text(encoding="utf-8"))
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("пришлю HTML и PDF" in text for text in sent_texts))

    def test_audio_without_pending_is_accepted_starts_pipeline_and_archive_worker(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        result = {
            "status": "accepted",
            "intake_id": "telegram:unique-id",
            "file_name": "zoom-call.m4a",
            "local_path": "/var/lib/zoom-audio-pipeline/telegram-intake/zoom-call.m4a",
            "inbox_path": "/var/lib/zoom-audio-pipeline/inbox/zoom-call.m4a",
        }
        message = {
            "chat": {"id": 42},
            "document": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "zoom-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 123,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", return_value=result) as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(message)

        accept_mock.assert_called_once()
        self.start_mock.assert_called_once()
        worker_mock.assert_called_once()
        self.assertIn("telegram_intake_accepted", self.events.read_text(encoding="utf-8"))
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Аудио получил" in text and "архивирую в Notion" in text for text in sent_texts))
        self.assertTrue(any("Pipeline запущен" in text and "Notion-архивация" in text for text in sent_texts))
        self.assertTrue(any("zoom-call.m4a" in text for text in sent_texts))

    def test_audio_without_pending_reports_accept_failure_without_starting_pipeline(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        message = {
            "chat": {"id": 42},
            "audio": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "zoom-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 123,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", side_effect=RuntimeError("disk down")):
            self.handler().handle_message(message)

        self.start_mock.assert_not_called()
        self.assertIn("telegram_intake_failed", self.events.read_text(encoding="utf-8"))
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Не смог принять аудио в pipeline" in text for text in sent_texts))

    def test_voice_without_pending_is_not_accepted_as_new_pipeline_file(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        message = {
            "chat": {"id": 42},
            "voice": {
                "file_id": "voice-file",
                "file_unique_id": "voice-unique",
                "file_size": 123,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline") as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(message)

        self.assertIsNone(WEBHOOK.extract_audio_message(message))
        accept_mock.assert_not_called()
        self.start_mock.assert_not_called()
        worker_mock.assert_not_called()
        events = self.events.read_text(encoding="utf-8")
        self.assertIn("telegram_voice_without_pending_review", events)
        self.assertNotIn("telegram_intake_accepted", events)
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("активной проверки" in text for text in sent_texts))

    def test_voice_with_article_selection_creates_one_edit_job(self) -> None:
        article = "# Roadmap\n\nПервый абзац.\n\n## Второй блок\n\nВторой абзац.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        manifest = ARTICLE_EDITOR.build_manifest(article)
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["pending_article_edits"] = {
            "42": {
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
                "selected_block_ids": ["b_002"],
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        message = {"message_id": 501, "chat": {"id": 42}, "voice": {"file_id": "voice-file"}}

        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("Второй сократи.", "voice")), \
            patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message(message)
            self.handler().handle_message(message)

        worker_mock.assert_called_once()
        job_path = Path(worker_mock.call_args.args[1])
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job["article_version"], 1)
        self.assertEqual(job["selected_block_ids"], ["b_002"])
        self.assertEqual(job["instruction"], "Второй сократи.")
        saved_registry = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertNotIn("42", saved_registry.get("pending_article_edits", {}))
        self.assertEqual(self.status()["article_edit_status"], "queued")
        self.start_mock.assert_not_called()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("выбранным блокам" in text for text in sent_texts))

    def test_active_article_without_fresh_selection_edits_all_blocks(self) -> None:
        article = "# Roadmap\n\nПервый блок.\n\n## Второй блок\n\nВторой абзац.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        manifest = ARTICLE_EDITOR.build_manifest(article)
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        registry["pending_article_edits"] = {
            "42": {
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
                "selected_block_ids": ["b_001"],
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")

        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("Сократи.", "voice")), \
            patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message({"message_id": 601, "chat": {"id": 42}, "voice": {"file_id": "v1"}})
            (self.run_dir / "status.json").write_text(
                json.dumps({"article_edit_status": "done"}), encoding="utf-8"
            )
            self.handler().handle_message({"message_id": 602, "chat": {"id": 42}, "voice": {"file_id": "v2"}})

        self.assertEqual(worker_mock.call_count, 2)
        second_job_path = Path(worker_mock.call_args.args[1])
        second_job = json.loads(second_job_path.read_text(encoding="utf-8"))
        self.assertEqual(second_job["selected_block_ids"], ["b_001", "b_002"])
        self.assertEqual(second_job["selection_scope"], "whole_article")
        saved = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(saved["active_articles"]["42"]["run_key"], "abc123")
        self.assertNotIn("42", saved.get("pending_article_edits", {}))
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("ко всей статье" in text.lower() for text in sent_texts))
        self.assertFalse(any("нет активной проверки" in text.lower() for text in sent_texts))

    def test_text_without_selection_edits_all_blocks_once_for_duplicate_update(self) -> None:
        article = "# Roadmap\n\nПервый блок.\n\n## Второй блок\n\nВторой абзац.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        manifest = ARTICLE_EDITOR.build_manifest(article)
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        message = {"message_id": 604, "chat": {"id": 42}, "text": "Убери повторы по всему тексту."}

        with patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message(message)
            self.handler().handle_message(message)

        worker_mock.assert_called_once()
        job_path = Path(worker_mock.call_args.args[1])
        job = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(job["selected_block_ids"], ["b_001", "b_002"])
        self.assertEqual(job["selection_scope"], "whole_article")
        self.assertEqual(job["source"], "text")
        self.assertEqual(job["instruction"], "Убери повторы по всему тексту.")

    def test_whole_article_edit_rejects_stale_manifest_without_starting_worker(self) -> None:
        article = "# Roadmap\n\nПервый блок.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        manifest = ARTICLE_EDITOR.build_manifest(article)
        manifest["article_version"] = 2
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")

        with patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message(
                {"message_id": 605, "chat": {"id": 42}, "voice": {"file_id": "stale"}}
            )

        worker_mock.assert_not_called()
        saved = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertNotIn("42", saved.get("pending_article_edits", {}))
        self.assertIn("telegram_article_edit_whole_article_rejected", self.events.read_text(encoding="utf-8"))
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Не смог подготовить актуальную статью" in text for text in sent_texts))

    def test_second_voice_during_article_edit_does_not_create_parallel_job(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        (self.run_dir / "status.json").write_text(
            json.dumps({"article_edit_status": "queued"}), encoding="utf-8"
        )

        with patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message({"message_id": 603, "chat": {"id": 42}, "voice": {"file_id": "v3"}})

        worker_mock.assert_not_called()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("уже выполняется" in text.lower() for text in sent_texts))

    def test_three_sequential_article_edits_each_require_fresh_checkbox_selection(self) -> None:
        article = "# Roadmap\n\nВерсия 1.\n"
        article_path = self.run_dir / "roadmap-article.md"
        manifest_path = self.run_dir / "roadmap-article-blocks.json"
        article_path.write_text(article, encoding="utf-8")
        manifest = ARTICLE_EDITOR.build_manifest(article)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")

        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("Измени блок.", "voice")), \
            patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            for cycle in range(1, 4):
                registry = json.loads(self.registry.read_text(encoding="utf-8"))
                selected = WEBHOOK.update_article_selection(
                    registry, "42", "abc123", cycle, ["b_001"], "set", f"cycle-{cycle}"
                )
                self.assertEqual(selected, ["b_001"])
                self.registry.write_text(json.dumps(registry), encoding="utf-8")
                self.handler().handle_message({
                    "message_id": 610 + cycle,
                    "chat": {"id": 42},
                    "voice": {"file_id": f"voice-{cycle}"},
                })
                saved = json.loads(self.registry.read_text(encoding="utf-8"))
                self.assertNotIn("42", saved.get("pending_article_edits", {}))
                self.assertEqual(saved["active_articles"]["42"]["article_version"], cycle)
                (self.run_dir / "status.json").write_text(
                    json.dumps({"article_edit_status": "done"}), encoding="utf-8"
                )
                if cycle < 3:
                    next_article = f"# Roadmap\n\nВерсия {cycle + 1}.\n"
                    article_path.write_text(next_article, encoding="utf-8")
                    manifest = ARTICLE_EDITOR.build_manifest(next_article, previous=manifest)
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    saved["active_articles"]["42"]["article_version"] = cycle + 1
                    self.registry.write_text(json.dumps(saved), encoding="utf-8")

        self.assertEqual(worker_mock.call_count, 3)

    def test_closed_article_rejects_stale_checkbox_page(self) -> None:
        article = "# Roadmap\n\nТекст.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(ARTICLE_EDITOR.build_manifest(article)), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["active_articles"] = {
            "42": {"status": "closed", "reason": "new_audio", "closed_at": "now"}
        }
        with self.assertRaisesRegex(ValueError, "not active"):
            WEBHOOK.update_article_selection(
                registry, "42", "abc123", 1, ["b_001"], "set", "later"
            )

    def test_new_audio_closes_active_article_only_after_successful_accept(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        registry["pending_article_edits"] = {
            "42": {
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
                "selected_block_ids": ["b_001"],
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        message = {
            "message_id": 604,
            "chat": {"id": 42},
            "document": {
                "file_id": "new-call",
                "file_unique_id": "new-call-unique",
                "file_name": "new-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 123,
            },
        }
        result = {
            "status": "accepted",
            "intake_id": "telegram:new-call",
            "file_name": "new-call.m4a",
            "local_path": "/tmp/new-call.m4a",
            "inbox_path": "/tmp/inbox/new-call.m4a",
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", return_value=result), \
            patch.object(WEBHOOK, "start_notion_archive_worker_async"):
            self.handler().handle_message(message)

        saved = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(saved["active_articles"]["42"]["status"], "closed")
        self.assertEqual(saved["active_articles"]["42"]["reason"], "new_audio")
        self.assertNotIn("42", saved.get("pending_article_edits", {}))

    def test_failed_new_audio_keeps_active_article_and_checkbox_selection(self) -> None:
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["active_articles"] = {
            "42": {
                "status": "active",
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
            }
        }
        registry["pending_article_edits"] = {
            "42": {
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
                "selected_block_ids": ["b_001"],
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        message = {
            "message_id": 605,
            "chat": {"id": 42},
            "audio": {
                "file_id": "broken-call",
                "file_unique_id": "broken-call-unique",
                "file_name": "broken-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 123,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", side_effect=RuntimeError("disk down")):
            self.handler().handle_message(message)

        saved = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(saved["active_articles"]["42"]["status"], "active")
        self.assertEqual(saved["pending_article_edits"]["42"]["selected_block_ids"], ["b_001"])

    def test_text_with_article_selection_uses_same_edit_flow(self) -> None:
        article = "# Roadmap\n\nПервый абзац.\n"
        (self.run_dir / "roadmap-article.md").write_text(article, encoding="utf-8")
        (self.run_dir / "roadmap-article-blocks.json").write_text(
            json.dumps(ARTICLE_EDITOR.build_manifest(article)), encoding="utf-8"
        )
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["pending_reviews"] = {}
        registry["pending_article_edits"] = {
            "42": {
                "run_key": "abc123",
                "run_dir": str(self.run_dir),
                "audio": "lesson.m4a",
                "article_version": 1,
                "selected_block_ids": ["b_001"],
            }
        }
        self.registry.write_text(json.dumps(registry), encoding="utf-8")

        with patch.object(WEBHOOK, "start_article_edit_worker_async") as worker_mock:
            self.handler().handle_message({"message_id": 502, "chat": {"id": 42}, "text": "Удалить первый."})

        worker_mock.assert_called_once()
        job = json.loads(Path(worker_mock.call_args.args[1]).read_text(encoding="utf-8"))
        self.assertEqual(job["instruction"], "Удалить первый.")
        self.assertEqual(job["source"], "text")
        self.start_mock.assert_not_called()

    def test_repeated_voice_without_pending_is_silent(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        message = {
            "message_id": 1001,
            "chat": {"id": 42},
            "voice": {
                "file_id": "voice-file",
                "file_unique_id": "voice-unique",
                "file_size": 123,
            },
        }

        self.handler().handle_message(message)
        self.handler().handle_message(message)

        sent_messages = [payload for method, payload in self.sent if method == "sendMessage"]
        self.assertEqual(len(sent_messages), 1)
        events = [line for line in self.events.read_text(encoding="utf-8").splitlines() if "telegram_voice_without_pending_review" in line]
        self.assertEqual(len(events), 1)

    def test_cloud_telegram_audio_above_20mb_is_rejected_before_download(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        message = {
            "chat": {"id": 42},
            "document": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "zoom-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 21 * 1024 * 1024,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline") as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(message)

        accept_mock.assert_not_called()
        self.start_mock.assert_not_called()
        worker_mock.assert_not_called()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Telegram Bot API" in text for text in sent_texts))

    def test_local_bot_api_audio_above_20mb_is_accepted(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        result = {
            "status": "accepted",
            "intake_id": "telegram:unique-id",
            "file_name": "zoom-call.m4a",
            "local_path": "/var/lib/zoom-audio-pipeline/telegram-intake/zoom-call.m4a",
            "inbox_path": "/var/lib/zoom-audio-pipeline/inbox/zoom-call.m4a",
        }
        message = {
            "chat": {"id": 42},
            "document": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "zoom-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 21 * 1024 * 1024,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", return_value=result) as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler({"telegram_api_base_url": "http://127.0.0.1:8081"}).handle_message(message)

        accept_mock.assert_called_once()
        self.start_mock.assert_called_once()
        worker_mock.assert_called_once()

    def test_repeated_audio_upload_message_is_silent(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        result = {
            "status": "accepted",
            "intake_id": "telegram:unique-id",
            "file_name": "zoom-call.m4a",
            "local_path": "/var/lib/zoom-audio-pipeline/telegram-intake/zoom-call.m4a",
            "inbox_path": "/var/lib/zoom-audio-pipeline/inbox/zoom-call.m4a",
        }
        message = {
            "message_id": 1002,
            "chat": {"id": 42},
            "document": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "zoom-call.m4a",
                "mime_type": "audio/mp4",
                "file_size": 123,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", return_value=result) as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(message)
            self.handler().handle_message(message)

        accept_mock.assert_called_once()
        self.start_mock.assert_called_once()
        worker_mock.assert_called_once()
        sent_messages = [payload for method, payload in self.sent if method == "sendMessage"]
        self.assertEqual(len(sent_messages), 2)
        self.assertFalse(any("уже был принят" in payload["text"] for payload in sent_messages))

    def test_same_file_in_forwarded_new_message_starts_second_pipeline(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        results = [
            {
                "status": "accepted",
                "intake_id": "telegram-message:42:2001",
                "file_name": "zoom-call.m4a",
                "local_path": "/var/lib/zoom-audio-pipeline/telegram-intake/zoom-call.m4a",
                "inbox_path": "/var/lib/zoom-audio-pipeline/inbox/zoom-call.m4a",
            },
            {
                "status": "accepted",
                "intake_id": "telegram-message:42:2002",
                "file_name": "zoom-call-2.m4a",
                "local_path": "/var/lib/zoom-audio-pipeline/telegram-intake/zoom-call-2.m4a",
                "inbox_path": "/var/lib/zoom-audio-pipeline/inbox/zoom-call-2.m4a",
            },
        ]
        document = {
            "file_id": "same-file-id",
            "file_unique_id": "same-unique-id",
            "file_name": "zoom-call.m4a",
            "mime_type": "audio/mp4",
            "file_size": 123,
        }
        first = {"message_id": 2001, "chat": {"id": 42}, "document": dict(document)}
        forwarded = {
            "message_id": 2002,
            "chat": {"id": 42},
            "forward_origin": {"type": "user"},
            "document": dict(document),
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline", side_effect=results) as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(first)
            self.handler().handle_message(forwarded)

        self.assertEqual(accept_mock.call_count, 2)
        self.assertEqual(self.start_mock.call_count, 2)
        self.assertEqual(worker_mock.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in worker_mock.call_args_list],
            ["telegram-message:42:2001", "telegram-message:42:2002"],
        )
        sent_messages = [payload for method, payload in self.sent if method == "sendMessage"]
        self.assertFalse(any("уже был принят" in payload["text"] for payload in sent_messages))

    def test_large_audio_without_pending_reports_limit_without_starting_pipeline(self) -> None:
        self.registry.write_text(json.dumps({"runs": {}, "pending_reviews": {}}, ensure_ascii=False), encoding="utf-8")
        message = {
            "chat": {"id": 42},
            "document": {
                "file_id": "file-id",
                "file_unique_id": "unique-id",
                "file_name": "huge.m4a",
                "mime_type": "audio/mp4",
                "file_size": 51 * 1024 * 1024,
            },
        }

        with patch.object(WEBHOOK, "accept_audio_message_for_pipeline") as accept_mock, \
            patch.object(WEBHOOK, "start_notion_archive_worker_async") as worker_mock:
            self.handler().handle_message(message)

        accept_mock.assert_not_called()
        self.start_mock.assert_not_called()
        worker_mock.assert_not_called()
        events = self.events.read_text(encoding="utf-8")
        self.assertIn("telegram_intake_rejected", events)
        self.assertIn("file_too_large", events)
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertTrue(any("Telegram Bot API" in text and "лимит" in text for text in sent_texts))
        self.assertFalse(any("Аудио получил" in text for text in sent_texts))

    def test_repeated_pending_voice_correction_does_not_fall_through_to_no_pending_message(self) -> None:
        message = {"message_id": 1003, "chat": {"id": 42}, "voice": {"file_id": "voice-file"}}
        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("P1 РґРѕР±Р°РІРёС‚СЊ", "voice")), \
            patch.object(WEBHOOK, "accept_audio_message_for_pipeline") as accept_mock:
            self.handler().handle_message(message)
            self.handler().handle_message(message)

        accept_mock.assert_not_called()
        self.start_mock.assert_called_once()
        sent_texts = [payload["text"] for method, payload in self.sent if method == "sendMessage"]
        self.assertEqual(len(sent_texts), 2)
        self.assertFalse(any("нет активной проверки" in text for text in sent_texts))

    def test_pending_audio_remains_teacher_correction_not_notion_intake(self) -> None:
        with patch.object(WEBHOOK, "correction_text_from_message", return_value=("P1 добавить", "voice")), \
            patch.object(WEBHOOK, "accept_audio_message_for_pipeline") as accept_mock:
            self.handler().handle_message({"chat": {"id": 42}, "voice": {"file_id": "voice-file"}})

        accept_mock.assert_not_called()
        self.assertEqual(self.status()["teacher_verification_decision"], "approved_for_article")
        self.assertEqual(self.status()["teacher_voice_transcription_provider"], "openrouter")
        self.assertEqual(self.status()["teacher_voice_transcription_model"], "openai/whisper-large-v3-turbo")
        self.start_mock.assert_called_once()


class TelegramNotionIntakeTests(unittest.TestCase):
    def test_telegram_urls_can_target_local_bot_api(self) -> None:
        self.assertEqual(
            WEBHOOK.telegram_method_url("token", "getFile", "http://127.0.0.1:8081/"),
            "http://127.0.0.1:8081/bottoken/getFile",
        )
        self.assertEqual(
            WEBHOOK.telegram_file_url("token", "audio/file.m4a", "http://127.0.0.1:8081/"),
            "http://127.0.0.1:8081/file/bottoken/audio/file.m4a",
        )
        self.assertFalse(WEBHOOK.is_cloud_telegram_api("http://127.0.0.1:8081"))

    def test_download_telegram_file_copies_local_bot_api_absolute_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_root = root / "telegram-bot-api"
            source = api_root / "token" / "documents" / "lesson.m4a"
            destination = root / "intake" / "lesson.m4a"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"audio")

            def fake_telegram_request(
                _token: str,
                method: str,
                payload: dict[str, object] | None = None,
                **kwargs: object,
            ) -> dict[str, object]:
                self.assertEqual(method, "getFile")
                self.assertEqual(payload, {"file_id": "file-id"})
                self.assertEqual(kwargs.get("api_base_url"), "http://127.0.0.1:8081")
                return {"ok": True, "result": {"file_path": str(source), "file_size": 51 * 1024 * 1024}}

            with patch.object(WEBHOOK, "telegram_request", side_effect=fake_telegram_request):
                WEBHOOK.download_telegram_file(
                    "token",
                    "file-id",
                    destination,
                    api_base_url="http://127.0.0.1:8081",
                    local_bot_api_root=api_root,
                )

            self.assertEqual(destination.read_bytes(), b"audio")

    def test_local_bot_api_file_copy_rejects_paths_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_root = root / "telegram-bot-api"
            api_root.mkdir()
            outside = root / "outside.m4a"
            outside.write_bytes(b"audio")
            with self.assertRaises(RuntimeError):
                WEBHOOK.copy_local_bot_api_file(str(outside), root / "copy.m4a", api_root)

    def test_load_env_supports_export_lines_used_by_notion_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "notion.env"
            env_path.write_text(
                "export NOTION_API_KEY='secret-value'\nexport NOTION_TARGET=\"https://notion.so/3b635d73584c80368c5bcfeb579c16d8\"\n",
                encoding="utf-8",
            )
            values = WEBHOOK.load_env(env_path)

        self.assertEqual(values["NOTION_API_KEY"], "secret-value")
        self.assertEqual(values["NOTION_TARGET"], "https://notion.so/3b635d73584c80368c5bcfeb579c16d8")

    def test_notion_payloads_match_existing_page_audio_block_structure(self) -> None:
        page_payload = WEBHOOK.notion_child_page_payload("root-page-id", "Миша а1.m4a")
        self.assertEqual(page_payload["parent"], {"type": "page_id", "page_id": "root-page-id"})
        title = page_payload["properties"]["title"]["title"][0]["text"]["content"]
        self.assertEqual(title, "Миша а1.m4a")

        block_payload = WEBHOOK.notion_audio_block_payload("file-upload-id")
        child = block_payload["children"][0]
        self.assertEqual(child["type"], "audio")
        self.assertEqual(child["audio"]["type"], "file_upload")
        self.assertEqual(child["audio"]["file_upload"]["id"], "file-upload-id")

    def test_notion_upload_content_type_normalizes_m4a(self) -> None:
        self.assertEqual(WEBHOOK.notion_upload_content_type(Path("Настя а2.m4a"), "audio/m4a"), "audio/mp4")
        self.assertEqual(WEBHOOK.notion_upload_content_type(Path("Настя а2.m4a"), "audio/x-m4a"), "audio/mp4")
        self.assertEqual(WEBHOOK.notion_upload_content_type(Path("call.mp3"), "audio/mp3"), "audio/mpeg")

    def test_notion_small_upload_uses_single_part(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "small.m4a"
            audio.write_bytes(b"audio")
            requests: list[tuple[str, dict[str, object] | None]] = []

            def fake_json(_key, method, path, payload=None):
                requests.append((path, payload))
                self.assertEqual(method, "POST")
                return {"id": "file-upload-id", "status": "uploaded"}

            with patch.object(WEBHOOK, "notion_json_request", side_effect=fake_json), patch.object(
                WEBHOOK,
                "notion_multipart_file_request",
                return_value={"id": "file-upload-id", "status": "uploaded"},
            ) as send_mock:
                result = WEBHOOK.notion_upload_file_request("key", audio, "audio/mp4")

        self.assertEqual(result["id"], "file-upload-id")
        self.assertEqual(
            requests,
            [("/file_uploads", {"mode": "single_part", "filename": "small.m4a", "content_type": "audio/mp4"})],
        )
        send_mock.assert_called_once()

    def test_notion_large_upload_uses_multi_part_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "large.m4a"
            audio.write_bytes(b"x" * (WEBHOOK.NOTION_SINGLE_PART_MAX_BYTES + 1))
            requests: list[tuple[str, dict[str, object] | None]] = []
            sent_parts: list[tuple[int | None, int]] = []

            def fake_json(_key, method, path, payload=None):
                requests.append((path, payload))
                self.assertEqual(method, "POST")
                if path == "/file_uploads":
                    return {"id": "file-upload-id", "status": "pending"}
                if path == "/file_uploads/file-upload-id/complete":
                    return {"id": "file-upload-id", "status": "uploaded"}
                raise AssertionError(path)

            def fake_send(_key, _file_upload_id, _filename, _content_type, file_bytes, part_number=None):
                sent_parts.append((part_number, len(file_bytes)))
                return {"id": "file-upload-id", "status": "pending"}

            with patch.object(WEBHOOK, "notion_json_request", side_effect=fake_json), patch.object(
                WEBHOOK,
                "notion_send_file_part_request",
                side_effect=fake_send,
            ):
                result = WEBHOOK.notion_upload_file_request("key", audio, "audio/mp4")

        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(
            requests[0],
            (
                "/file_uploads",
                {
                    "mode": "multi_part",
                    "filename": "large.m4a",
                    "content_type": "audio/mp4",
                    "number_of_parts": 3,
                },
            ),
        )
        self.assertEqual(requests[-1], ("/file_uploads/file-upload-id/complete", {}))
        self.assertEqual([part for part, _size in sent_parts], [1, 2, 3])
        self.assertEqual(sum(size for _part, size in sent_parts), WEBHOOK.NOTION_SINGLE_PART_MAX_BYTES + 1)

    def test_accept_audio_message_allows_same_file_in_forwarded_new_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            config = {
                "telegram_notion_intake_state": str(state_path),
                "telegram_intake_dir": str(root / "intake"),
                "inbox_dir": str(root / "inbox"),
                "telegram_cloud_max_download_bytes": "20971520",
            }
            audio = {
                "file_id": "same-file-id",
                "file_unique_id": "same-unique-id",
                "file_name": "existing.m4a",
                "mime_type": "audio/mp4",
            }

            def fake_download(_token, _file_id, destination, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"same audio")

            with patch.object(WEBHOOK, "download_telegram_file", side_effect=fake_download) as download_mock:
                first = WEBHOOK.accept_audio_message_for_pipeline(
                    config,
                    "token",
                    {"message_id": 3001, "chat": {"id": 42}, "audio": dict(audio)},
                )
                forwarded = WEBHOOK.accept_audio_message_for_pipeline(
                    config,
                    "token",
                    {
                        "message_id": 3002,
                        "chat": {"id": 42},
                        "forward_origin": {"type": "user"},
                        "audio": dict(audio),
                    },
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(forwarded["status"], "accepted")
        self.assertEqual(first["intake_id"], "telegram-message:42:3001")
        self.assertEqual(forwarded["intake_id"], "telegram-message:42:3002")
        self.assertNotEqual(first["inbox_path"], forwarded["inbox_path"])
        self.assertEqual(download_mock.call_count, 2)
        self.assertIn("telegram-message:42:3001", state["files"])
        self.assertIn("telegram-message:42:3002", state["files"])

    def test_accept_audio_message_replay_of_same_message_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "telegram_notion_intake_state": str(root / "state.json"),
                "telegram_intake_dir": str(root / "intake"),
                "inbox_dir": str(root / "inbox"),
                "telegram_cloud_max_download_bytes": "20971520",
            }
            message = {
                "message_id": 4001,
                "chat": {"id": 42},
                "document": {
                    "file_id": "file-id",
                    "file_unique_id": "file-unique-id",
                    "file_name": "existing.m4a",
                    "mime_type": "audio/mp4",
                },
            }

            def fake_download(_token, _file_id, destination, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"audio")

            with patch.object(WEBHOOK, "download_telegram_file", side_effect=fake_download) as download_mock:
                first = WEBHOOK.accept_audio_message_for_pipeline(config, "token", message)
                replay = WEBHOOK.accept_audio_message_for_pipeline(config, "token", message)

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(replay["intake_id"], "telegram-message:42:4001")
        download_mock.assert_called_once()

    def test_legacy_file_unique_entry_does_not_block_new_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({
                    "files": {
                        "telegram:file-unique-id": {
                            "status": "accepted",
                            "intake_id": "telegram:file-unique-id",
                            "file_name": "existing.m4a",
                        }
                    }
                }),
                encoding="utf-8",
            )
            config = {
                "telegram_notion_intake_state": str(state_path),
                "telegram_intake_dir": str(root / "intake"),
                "inbox_dir": str(root / "inbox"),
                "telegram_cloud_max_download_bytes": "20971520",
            }
            message = {
                "message_id": 5001,
                "chat": {"id": 42},
                "document": {
                    "file_id": "file-id",
                    "file_unique_id": "file-unique-id",
                    "file_name": "existing.m4a",
                    "mime_type": "audio/mp4",
                },
            }

            def fake_download(_token, _file_id, destination, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"audio")

            with patch.object(WEBHOOK, "download_telegram_file", side_effect=fake_download):
                result = WEBHOOK.accept_audio_message_for_pipeline(config, "token", message)

            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["intake_id"], "telegram-message:42:5001")
        self.assertIn("telegram:file-unique-id", state["files"])
        self.assertIn("telegram-message:42:5001", state["files"])

    def test_large_audio_message_is_rejected_before_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(WEBHOOK, "download_telegram_file") as download_mock:
            with self.assertRaises(WEBHOOK.TelegramFileTooLargeError) as caught:
                WEBHOOK.accept_audio_message_for_pipeline(
                    {
                        "telegram_notion_intake_state": str(Path(tmp) / "state.json"),
                        "telegram_intake_dir": str(Path(tmp) / "intake"),
                        "telegram_cloud_max_download_bytes": "10",
                        "notion_api_key": "unused",
                        "notion_target": "https://notion.so/3b635d73584c80368c5bcfeb579c16d8",
                    },
                    "token",
                    {
                        "document": {
                            "file_id": "file-id",
                            "file_unique_id": "tg-unique-2",
                            "file_name": "huge.m4a",
                            "mime_type": "audio/mp4",
                            "file_size": 11,
                        }
                    },
                )
        self.assertEqual(caught.exception.file_size, 11)
        self.assertEqual(caught.exception.max_bytes, 10)
        download_mock.assert_not_called()


class DurableArchiveAndCleanupTests(unittest.TestCase):
    def test_archive_worker_failure_keeps_file_and_schedules_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "telegram-intake" / "lesson.m4a"
            local.parent.mkdir()
            local.write_bytes(b"audio")
            registry = root / "registry.json"
            events = root / "events.jsonl"
            registry.write_text(
                json.dumps(
                    {
                        "files": {
                            "telegram:abc": {
                                "intake_id": "telegram:abc",
                                "source": "telegram",
                                "file_name": "lesson.m4a",
                                "local_path": str(local),
                                "pipeline_status": "pipeline_done",
                                "notion_upload_status": "pending",
                                "notion_upload_attempts": 0,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "telegram_notion_archive_worker.py",
                "--registry-file",
                str(registry),
                "--events-file",
                str(events),
                "--lock-file",
                str(root / "archive.lock"),
                "--env-file",
                str(root / "missing.env"),
                "--intake-id",
                "telegram:abc",
            ]), patch.object(ARCHIVE_WORKER, "archive_entry", side_effect=RuntimeError("notion down")):
                self.assertEqual(ARCHIVE_WORKER.main(), 0)

            data = json.loads(registry.read_text(encoding="utf-8"))
            entry = data["files"]["telegram:abc"]
            self.assertTrue(local.exists())
            self.assertEqual(entry["notion_upload_status"], "failed_retry_wait")
            self.assertEqual(entry["notion_upload_attempts"], 1)
            self.assertIn("next_retry_at", entry)
            self.assertIn("notion_archive_upload_failed", events.read_text(encoding="utf-8"))

    def test_archive_worker_success_marks_uploaded_for_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "lesson.m4a"
            local.write_bytes(b"audio")
            registry = root / "registry.json"
            registry.write_text(
                json.dumps(
                    {
                        "files": {
                            "telegram:abc": {
                                "intake_id": "telegram:abc",
                                "source": "telegram",
                                "file_name": "lesson.m4a",
                                "local_path": str(local),
                                "pipeline_status": "pipeline_done",
                                "notion_upload_status": "failed_retry_wait",
                                "next_retry_at": "2000-01-01T00:00:00Z",
                                "notion_upload_attempts": 1,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_archive(_config, entry):
                return {**entry, "notion_upload_status": "uploaded", "notion_page_id": "page-id"}

            with patch.object(sys, "argv", [
                "telegram_notion_archive_worker.py",
                "--registry-file",
                str(registry),
                "--events-file",
                str(root / "events.jsonl"),
                "--lock-file",
                str(root / "archive.lock"),
                "--env-file",
                str(root / "missing.env"),
                "--intake-id",
                "telegram:abc",
            ]), patch.object(ARCHIVE_WORKER, "archive_entry", side_effect=fake_archive):
                self.assertEqual(ARCHIVE_WORKER.main(), 0)

            entry = json.loads(registry.read_text(encoding="utf-8"))["files"]["telegram:abc"]
            self.assertEqual(entry["notion_upload_status"], "uploaded")
            self.assertEqual(entry["notion_page_id"], "page-id")
            self.assertEqual(entry["notion_upload_attempts"], 2)

    def test_cleanup_never_deletes_until_notion_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.m4a"
            inbox = Path(tmp) / "inbox.m4a"
            local.write_bytes(b"audio")
            inbox.write_bytes(b"audio")
            entry = {
                "pipeline_status": "pipeline_done",
                "notion_upload_status": "failed_retry_wait",
                "local_path": str(local),
                "inbox_path": str(inbox),
            }

            deleted = CLEANUP.cleanup_entry(entry, now=time.time() + 86400, min_age_days=0, dry_run=False)

            self.assertEqual(deleted, [])
            self.assertTrue(local.exists())
            self.assertTrue(inbox.exists())

    def test_cleanup_deletes_only_when_pipeline_done_and_notion_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.m4a"
            inbox = Path(tmp) / "inbox.m4a"
            local.write_bytes(b"audio")
            inbox.write_bytes(b"audio")
            entry = {
                "pipeline_status": "pipeline_done",
                "notion_upload_status": "uploaded",
                "local_path": str(local),
                "inbox_path": str(inbox),
            }

            deleted = CLEANUP.cleanup_entry(entry, now=time.time() + 86400, min_age_days=0, dry_run=False)

            self.assertEqual(set(deleted), {str(local), str(inbox)})
            self.assertFalse(local.exists())
            self.assertFalse(inbox.exists())

    def test_notion_puller_skips_telegram_archive_marker_page(self) -> None:
        def fake_notion_get(path: str, _api_key: str):
            self.assertIn("/blocks/page-id/children", path)
            return {
                "results": [
                    {
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"plain_text": "intake_id: telegram:abc\nsource: telegram"}
                            ]
                        },
                    }
                ]
            }

        with patch.object(NOTION_PULL, "notion_get", side_effect=fake_notion_get):
            self.assertTrue(NOTION_PULL.has_telegram_intake_marker("page-id", "api-key"))

    def test_notion_puller_loads_registry_skip_page_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(
                json.dumps({"files": {"telegram:abc": {"source": "telegram", "notion_page_id": "page-id"}}}),
                encoding="utf-8",
            )
            self.assertEqual(NOTION_PULL.load_intake_skip_page_ids(registry), {"page-id"})


class NotifyFormattingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        (self.run_dir / "verification.md").write_text(VERIFICATION_MD, encoding="utf-8")
        (self.run_dir / "roadmap-article.md").write_text("# Article\n\nText", encoding="utf-8")
        (self.run_dir / "roadmap-article.html").write_text("<html><body>Text</body></html>", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_article_version_url_replaces_only_cache_buster(self) -> None:
        url = NOTIFY.version_public_url(
            "https://roadmap.example/article.html?source=telegram&source=bot&v=1#article",
            4,
        )

        self.assertEqual(
            url,
            "https://roadmap.example/article.html?source=telegram&source=bot&v=4#article",
        )

    def test_verification_message_is_short(self) -> None:
        message = NOTIFY.build_verification_message(self.run_dir, "lesson.m4a")
        self.assertLess(len(message), 700)
        self.assertIn("Файл: lesson.m4a", message)
        self.assertIn("Имя: Даниил", message)
        self.assertIn("Уровень: A0", message)
        self.assertNotIn("Предложения из PDF-базы", message)
        self.assertNotIn("Нужны правки", message)
        self.assertNotIn("Подтвердить нужные правки", message)

    def test_verification_brief_uses_current_next_step_copy(self) -> None:
        brief = NOTIFY.build_verification_brief(VERIFICATION_MD, "lesson.m4a")
        self.assertIn("## 8. Решения перед генерацией", brief)
        self.assertIn("## 9. Предложения из PDF-базы", brief)
        self.assertIn("уже звучать в созвоне", brief)
        self.assertIn("нажми «Согласен»", brief)
        self.assertIn("правку голосом или текстом", brief)
        self.assertNotIn("нажми «Подтвердить»", brief)
        self.assertNotIn("нажми «Нужны правки»", brief)

    def test_verification_brief_preserves_numbered_generation_decisions(self) -> None:
        markdown = VERIFICATION_MD + """

## 9. Решения перед генерацией

1. **Из созвона:** Этапы Roadmap — 1, 3 и 6 месяцев. Оставляем ровно три этапа?
2. **Предложение:** Контент ученика — добавить YouTube только для индивидуального формата?
3. **Из созвона:** Домашняя работа — повторение и вывод лексики в речь. Формулировка верна?
"""

        brief = NOTIFY.build_verification_brief(markdown, "lesson.m4a")

        self.assertIn("8.1. **Из созвона:** Этапы Roadmap", brief)
        self.assertIn("8.2. **Предложение:** Контент ученика", brief)
        self.assertIn("8.3. **Из созвона:** Домашняя работа", brief)
        self.assertLess(brief.index("8.1."), brief.index("8.2."))
        self.assertLess(brief.index("8.2."), brief.index("8.3."))

    def test_verification_brief_preserves_all_symbolic_preview_blocks(self) -> None:
        brief = NOTIFY.build_verification_brief(SYMBOLIC_VERIFICATION_MD, "lesson.m4a")
        self.assertIn("## Предварительная схема будущей статьи", brief)
        for number in range(1, 7):
            self.assertEqual(brief.count(f"### [{number}]"), 1)
        self.assertIn("A0 → первые диалоги → уверенная речь → B1", brief)
        self.assertIn("- ✓ поддерживать простой диалог", brief)
        self.assertIn("| Переспрашивать | Не теряться в разговоре |", brief)
        self.assertIn("- □ Выбрать материал", brief)

    def test_legacy_verification_without_symbolic_preview_still_renders(self) -> None:
        brief = NOTIFY.build_verification_brief(VERIFICATION_MD, "lesson.m4a")
        self.assertIn("## 2. Сроки и результаты из созвона", brief)
        self.assertIn("2.1. 1 месяц", brief)
        self.assertNotIn("## Предварительная схема будущей статьи", brief)

    def test_pdf_options_are_split_into_existing_and_optional(self) -> None:
        markdown = VERIFICATION_MD + """

## 9. Дополнительные факты

- На созвоне уже обсуждали Progress.me, интерактивную платформу и ситуации для путешествий.
"""
        brief = NOTIFY.build_verification_brief(markdown, "lesson.m4a")
        existing_header = "9.1. Уже есть в созвоне или первичном анализе"
        optional_header = "9.2. Можно дополнительно усилить roadmap"
        self.assertIn(existing_header, brief)
        self.assertIn(optional_header, brief)
        self.assertIn("P1. **Уже было в созвоне:** Progress.me", brief)
        self.assertIn("P7. **Уже было в созвоне:** Сценарии путешествий", brief)
        self.assertLess(brief.index(existing_header), brief.index("P1. **Уже было в созвоне:** Progress.me"))
        self.assertLess(brief.index(existing_header), brief.index("P7. **Уже было в созвоне:** Сценарии путешествий"))
        self.assertLess(brief.index(optional_header), brief.index("P2. Голосовые сообщения"))
        self.assertLess(brief.index(optional_header), brief.index("P13. Гибкий режим"))
        self.assertNotIn("P2. **Уже было в созвоне:**", brief)
        self.assertNotIn("P13. **Уже было в созвоне:**", brief)

    def test_verification_keyboard_only_open_and_approve(self) -> None:
        sent: list[dict[str, object]] = []

        def fake_telegram_request(
            _token: str,
            method: str,
            payload: dict[str, object] | None = None,
            **_kwargs: object,
        ):
            if method == "sendMessage" and payload:
                sent.append(payload)
            return {"ok": True, "result": {"message_id": 101}}

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=False), \
            patch.object(sys, "argv", [
                "telegram_roadmap_notify.py",
                "--chat-id",
                "42",
                "--stage",
                "verification_ready",
                "--audio",
                "lesson.m4a",
                "--run-dir",
                str(self.run_dir),
                "--registry-file",
                str(self.root / "registry.json"),
                "--public-root",
                str(self.root / "public"),
            ]), \
            patch.object(NOTIFY, "telegram_request", side_effect=fake_telegram_request), \
            patch.object(NOTIFY.subprocess, "run"):
            self.assertEqual(NOTIFY.main(), 0)

        keyboard = sent[-1]["reply_markup"]["inline_keyboard"]  # type: ignore[index]
        labels = [button["text"] for row in keyboard for button in row]
        self.assertEqual(labels, ["Открыть", "Согласен"])

    def test_article_ready_sends_html_and_pdf(self) -> None:
        sent_texts: list[dict[str, object]] = []
        sent_docs: list[tuple[Path, str | None]] = []
        commands: list[list[str]] = []

        def fake_telegram_request(
            _token: str,
            method: str,
            payload: dict[str, object] | None = None,
            **_kwargs: object,
        ):
            if method == "sendMessage" and payload:
                sent_texts.append(payload)
            return {"ok": True, "result": {"message_id": 202}}

        def fake_multipart(
            _token: str,
            method: str,
            fields: dict[str, object],
            file_field: str,
            file_path: Path,
            display_filename: str | None = None,
            **_kwargs: object,
        ):
            self.assertEqual(method, "sendDocument")
            self.assertEqual(file_field, "document")
            sent_docs.append((file_path, display_filename))
            return {"ok": True}

        def fake_pdf(run_dir: Path) -> Path:
            pdf = run_dir / "roadmap-article.pdf"
            pdf.write_text("pdf", encoding="utf-8")
            return pdf

        def fake_run(command: list[str], **_kwargs: object):
            commands.append(command)
            if command and command[0].endswith("roadmap-article-editor"):
                (self.run_dir / "roadmap-article-blocks.json").write_text(
                    json.dumps({
                        "schema_version": 2,
                        "article_version": 3,
                        "blocks": [],
                    }),
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(command, 0)

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=False), \
            patch.object(sys, "argv", [
                "telegram_roadmap_notify.py",
                "--chat-id",
                "42",
                "--stage",
                "article_ready",
                "--audio",
                "Настя а2.m4a",
                "--run-dir",
                str(self.run_dir),
                "--registry-file",
                str(self.root / "registry.json"),
                "--public-root",
                str(self.root / "public"),
            ]), \
            patch.object(NOTIFY, "telegram_request", side_effect=fake_telegram_request), \
            patch.object(NOTIFY, "telegram_multipart_request", side_effect=fake_multipart), \
            patch.object(NOTIFY, "ensure_article_pdf", side_effect=fake_pdf), \
            patch.object(NOTIFY.subprocess, "run", side_effect=fake_run):
            self.assertEqual(NOTIFY.main(), 0)

        self.assertIn("Открой красивую версию", sent_texts[-1]["text"])
        labels = [button["text"] for row in sent_texts[-1]["reply_markup"]["inline_keyboard"] for button in row]  # type: ignore[index]
        self.assertEqual(labels, ["Открыть красиво"])
        article_url = sent_texts[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]  # type: ignore[index]
        self.assertTrue(str(article_url).endswith("/article.html?v=3"))
        self.assertEqual([path.name for path, _name in sent_docs], ["roadmap-article.html", "roadmap-article.pdf"])
        self.assertEqual([name for _path, name in sent_docs], ["Настя а2 roadmap.html", "Настя а2 roadmap.pdf"])
        editor_commands = [
            command for command in commands
            if command and command[0].endswith("roadmap-article-editor")
        ]
        self.assertEqual(len(editor_commands), 1)
        self.assertIn("prepare", editor_commands[0])
        saved_registry = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))
        self.assertEqual(len(saved_registry["runs"]), 1)
        self.assertEqual(saved_registry["active_articles"]["42"]["status"], "active")
        self.assertEqual(saved_registry["active_articles"]["42"]["article_version"], 3)
        self.assertNotIn("42", saved_registry.get("pending_article_edits", {}))

    def test_article_recovery_sends_two_choice_buttons(self) -> None:
        sent: list[dict[str, object]] = []
        registry = self.root / "recovery-registry.json"

        def fake_telegram_request(
            _token: str,
            method: str,
            payload: dict[str, object] | None = None,
            **_kwargs: object,
        ):
            if method == "sendMessage" and payload:
                sent.append(payload)
            return {"ok": True, "result": {"message_id": 303}}

        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=False), \
            patch.object(sys, "argv", [
                "telegram_roadmap_notify.py",
                "--chat-id",
                "42",
                "--stage",
                "article_recovery",
                "--audio",
                "Дмитрий а1-.m4a",
                "--run-dir",
                str(self.run_dir),
                "--registry-file",
                str(registry),
            ]), \
            patch.object(NOTIFY, "telegram_request", side_effect=fake_telegram_request):
            self.assertEqual(NOTIFY.main(), 0)

        labels = [
            button["text"]
            for row in sent[-1]["reply_markup"]["inline_keyboard"]  # type: ignore[index]
            for button in row
        ]
        self.assertEqual(labels, ["Повторить через Gemini", "Получить GPT-версию"])
        saved = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["runs"]), 1)


class ApprovedProcessorTests(unittest.TestCase):
    def test_corrupt_retry_state_is_due_and_backoff_is_bounded(self) -> None:
        self.assertTrue(APPROVED.retry_is_due({"article_next_retry_at_epoch": "invalid"}, now=1_000.0))
        self.assertEqual(APPROVED.retry_delay_seconds(1_000, 120, 1_800), 1_800)

    def test_done_article_notifies_once_with_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            status_path = run_dir / "status.json"
            status = {
                "teacher_verification_decision": "approved_for_article",
                "article_status": "done",
                "telegram_chat_id": "42",
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            calls: list[list[str]] = []

            with patch.object(APPROVED, "notify", side_effect=lambda _script, args: calls.append(args)):
                APPROVED.notify_article_if_needed(status_path, status, "notify", "lesson.m4a", run_dir)
                updated = json.loads(status_path.read_text(encoding="utf-8"))
                APPROVED.notify_article_if_needed(status_path, updated, "notify", "lesson.m4a", run_dir)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][:2], ["--chat-id", "42"])
            self.assertIn("article_notified_at", json.loads(status_path.read_text(encoding="utf-8")))

    def test_validation_warning_is_sent_after_article_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            status_path = run_dir / "status.json"
            status = {
                "article_status": "done",
                "article_notified_at": "already-sent",
                "article_validation_status": "warning",
                "article_validation_warnings": ["Изменилось количество разделов статьи."],
                "telegram_chat_id": "42",
            }
            status_path.write_text(json.dumps(status), encoding="utf-8")
            calls: list[list[str]] = []

            with patch.object(APPROVED, "notify", side_effect=lambda _script, args: calls.append(args)):
                APPROVED.notify_validation_warning_if_needed(status_path, status, "notify", "lesson.m4a")
                updated = json.loads(status_path.read_text(encoding="utf-8"))
                APPROVED.notify_validation_warning_if_needed(status_path, updated, "notify", "lesson.m4a")

            self.assertEqual(len(calls), 1)
            self.assertIn("1. Изменилось количество разделов статьи.", calls[0][-1])

    def test_validation_failure_waits_for_recovery_choice_without_timed_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            status_path = run_dir / "status.json"
            (run_dir / "verification.md").write_text("verification", encoding="utf-8")
            status_path.write_text(json.dumps({
                "teacher_verification_decision": "approved_for_article",
                "article_status": "started",
                "audio_path": str(run_dir / "audio.m4a"),
                "telegram_chat_id": "42",
            }), encoding="utf-8")
            notifications: list[list[str]] = []

            def fail_validation(_command: list[str], check: bool) -> None:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status.update({
                    "article_status": "recovery_required",
                    "article_recovery_status": "awaiting_choice",
                    "article_recovery_reason": "gemini_validation_failed",
                })
                status_path.write_text(json.dumps(status), encoding="utf-8")
                raise APPROVED.subprocess.CalledProcessError(3, _command)

            with patch.object(sys, "argv", [
                "process_approved_roadmaps.py",
                "--runs-dir",
                str(root),
                "--events-file",
                str(root / "events.jsonl"),
            ]), \
                patch.object(APPROVED.subprocess, "run", side_effect=fail_validation), \
                patch.object(APPROVED, "notify", side_effect=lambda _script, args: notifications.append(args)):
                self.assertEqual(APPROVED.main(), 0)

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertNotIn("article_next_retry_at_epoch", status)
            self.assertIn("article_recovery_notified_at", status)
            self.assertEqual(len(notifications), 1)
            self.assertIn("article_recovery", notifications[0])

    def test_failed_article_schedules_retry_notifies_once_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            for run_dir in (first, second):
                run_dir.mkdir()
                (run_dir / "verification.md").write_text("verification", encoding="utf-8")
                (run_dir / "status.json").write_text(
                    json.dumps({
                        "teacher_verification_decision": "approved_for_article",
                        "article_status": "failed",
                        "audio_path": str(run_dir / "audio.m4a"),
                        "telegram_chat_id": "42",
                    }),
                    encoding="utf-8",
                )

            calls: list[str] = []
            notifications: list[list[str]] = []

            def fake_run(command: list[str], check: bool) -> None:
                calls.append(command[-1])
                if command[-1] == str(first):
                    raise APPROVED.subprocess.CalledProcessError(1, command)
                status_path = second / "status.json"
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status["article_status"] = "done"
                status_path.write_text(json.dumps(status), encoding="utf-8")

            with patch.object(sys, "argv", [
                "process_approved_roadmaps.py",
                "--runs-dir",
                str(root),
                "--events-file",
                str(root / "events.jsonl"),
                "--retry-base-seconds",
                "120",
            ]), \
                patch.object(APPROVED.subprocess, "run", side_effect=fake_run), \
                patch.object(APPROVED, "notify", side_effect=lambda _script, args: notifications.append(args)), \
                patch.object(APPROVED.time, "time", return_value=1_000.0):
                self.assertEqual(APPROVED.main(), 0)

            first_status = json.loads((first / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(calls, [str(first), str(second)])
            self.assertEqual(first_status["article_retry_attempt"], 1)
            self.assertEqual(first_status["article_next_retry_at_epoch"], 1_120.0)
            self.assertIn("article_retry_notified_at", first_status)
            self.assertEqual(len(notifications), 2)

    def test_failed_article_waits_until_retry_is_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "verification.md").write_text("verification", encoding="utf-8")
            (run_dir / "status.json").write_text(
                json.dumps({
                    "teacher_verification_decision": "approved_for_article",
                    "article_status": "failed",
                    "audio_path": str(run_dir / "audio.m4a"),
                    "article_next_retry_at_epoch": 2_000.0,
                }),
                encoding="utf-8",
            )

            with patch.object(sys, "argv", [
                "process_approved_roadmaps.py",
                "--runs-dir",
                str(root),
                "--events-file",
                str(root / "events.jsonl"),
            ]), \
                patch.object(APPROVED.subprocess, "run") as run_mock, \
                patch.object(APPROVED.time, "time", return_value=1_000.0):
                self.assertEqual(APPROVED.main(), 0)

            run_mock.assert_not_called()

    def test_gpt_recovery_finalizes_saved_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            draft = run_dir / "roadmap-article-draft.md"
            draft.write_text("# GPT draft\n\nUseful article.\n", encoding="utf-8")
            status_path = run_dir / "status.json"
            status_path.write_text(json.dumps({
                "article_status": "recovery_requested",
                "article_recovery_action": "use_gpt_draft",
            }), encoding="utf-8")

            with patch.object(APPROVED.subprocess, "run") as render:
                APPROVED.finalize_gpt_draft(run_dir, status_path, "renderer")

            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual((run_dir / "roadmap-article.md").read_text(encoding="utf-8"), draft.read_text(encoding="utf-8"))
            self.assertEqual(status["article_status"], "done")
            self.assertEqual(status["article_source"], "gpt_draft")
            self.assertEqual(status["gemini_rewrite_status"], "bypassed_by_teacher")
            self.assertEqual(status["article_validation_status"], "bypassed_by_teacher")
            self.assertNotIn("article_recovery_action", status)
            render.assert_called_once()


class GeminiRewriteValidatorTests(unittest.TestCase):
    def run_validator(self, draft: str, final: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_path = root / "draft.md"
            final_path = root / "final.md"
            report_path = root / "report.json"
            draft_path.write_text(draft, encoding="utf-8")
            final_path.write_text(final, encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(ROOT / "scripts" / "validate_gemini_rewrite.py"),
                str(draft_path),
                str(final_path),
                "--report",
                str(report_path),
            ], capture_output=True, text=True)
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            return result, report

    def test_localized_heading_is_silent_and_usable(self) -> None:
        draft = "# Дмитрий\n\n## Roadmap\n\n" + ("Полезный текст. " * 80)
        final = "# Дмитрий\n\n## План действий\n\n" + ("Полезный текст. " * 80)
        result, report = self.run_validator(draft, final)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["warnings"], [])

    def test_missing_section_is_nonblocking_actionable_warning(self) -> None:
        draft = "# Дмитрий\n\n## Сейчас\n\nТекст.\n\n## План\n\n" + ("Подробность. " * 80)
        final = "# Дмитрий\n\n## Сейчас\n\n" + ("Подробность. " * 80)
        result, report = self.run_validator(draft, final)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["status"], "warning")
        self.assertTrue(report["warnings"])

    def test_missing_numeric_condition_is_nonblocking_actionable_warning(self) -> None:
        draft = "# Дмитрий\n\n## План\n\nЦена 3000 рублей, срок 6-9 месяцев.\n" + ("Подробность. " * 80)
        final = "# Дмитрий\n\n## План действий\n\n" + ("Подробность. " * 80)
        result, report = self.run_validator(draft, final)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["status"], "warning")
        self.assertTrue(any("3000 рублей" in warning for warning in report["warnings"]))

    def test_equivalent_numeric_range_wording_is_silent(self) -> None:
        draft = "# Дмитрий\n\n## План\n\nСрок 6–9 месяцев.\n" + ("Подробность. " * 80)
        final = "# Дмитрий\n\n## План действий\n\nСрок от 6 до 9 месяцев.\n" + ("Подробность. " * 80)
        result, report = self.run_validator(draft, final)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["warnings"], [])

    def test_truncated_final_requires_recovery_choice(self) -> None:
        draft = "# Дмитрий\n\n## План\n\n" + ("Подробный текст. " * 200)
        final = "# Дмитрий\n\nОборвано."
        result, report = self.run_validator(draft, final)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["status"], "recovery_required")
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
