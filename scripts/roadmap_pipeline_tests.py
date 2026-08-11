#!/usr/bin/env python3
"""Focused tests for the roadmap Telegram approval and article flow."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
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
        self.assertNotIn("считай это внутренней деталью согласования", prompt)


class TelegramVoiceTranscriptionTests(unittest.TestCase):
    def test_voice_transcriber_uses_openrouter_provider_without_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "voice.oga"
            audio.write_bytes(b"audio")
            argv = [
                "transcribe_telegram_voice.py",
                str(audio),
                "--provider",
                "openrouter",
            ]
            with patch.object(sys, "argv", argv), \
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
                    },
                    audio,
                )

        self.assertEqual(text, "ok")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 777)


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
        self.assertIn("heading mismatch", script)
        self.assertIn("unexpected P-codes added", script)
        self.assertIn("article_source\"] = \"gemini_rewrite\"", script)

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
        self.assertNotIn("codex ", verification)
        self.assertNotIn("codex ", article)

    def test_pipeline_runner_is_env_configurable_and_defaults_to_openrouter_generation(self) -> None:
        runner = (ROOT / "scripts/notion_pipeline_runner.sh").read_text(encoding="utf-8")
        self.assertIn("/etc/zoom-audio-pipeline/pipeline.env", runner)
        self.assertIn('telegram-notion-archive-worker --env-file "$ENV_FILE"', runner)
        self.assertIn('notion-pull-audio --env-file "$ENV_FILE"', runner)
        self.assertIn("VERIFICATION_SCRIPT:-/usr/local/bin/generate-verification-with-openrouter", runner)
        self.assertIn("ARTICLE_DRAFT_SCRIPT:-/usr/local/bin/generate-article-with-openrouter", runner)
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

    def test_accept_audio_message_is_idempotent_by_telegram_unique_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "telegram:tg-unique": {
                                "status": "accepted",
                                "intake_id": "telegram:tg-unique",
                                "file_name": "existing.m4a",
                                "pipeline_status": "pipeline_started",
                                "notion_upload_status": "uploaded",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = WEBHOOK.accept_audio_message_for_pipeline(
                {
                    "telegram_notion_intake_state": str(state_path),
                    "telegram_intake_dir": str(root / "intake"),
                    "inbox_dir": str(root / "inbox"),
                    "telegram_cloud_max_download_bytes": "20971520",
                    "notion_api_key": "unused",
                    "notion_target": "https://notion.so/3b635d73584c80368c5bcfeb579c16d8",
                },
                "token",
                {
                    "audio": {
                        "file_id": "file-id",
                        "file_unique_id": "tg-unique",
                        "file_name": "existing.m4a",
                        "mime_type": "audio/mp4",
                    }
                },
            )

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["intake_id"], "telegram:tg-unique")

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
        self.assertIn("## 8. Предложения из PDF-базы", brief)
        self.assertIn("уже звучать в созвоне", brief)
        self.assertIn("нажми «Согласен»", brief)
        self.assertIn("правку голосом или текстом", brief)
        self.assertNotIn("нажми «Подтвердить»", brief)
        self.assertNotIn("нажми «Нужны правки»", brief)

    def test_pdf_options_are_split_into_existing_and_optional(self) -> None:
        markdown = VERIFICATION_MD + """

## 9. Дополнительные факты

- На созвоне уже обсуждали Progress.me, интерактивную платформу и ситуации для путешествий.
"""
        brief = NOTIFY.build_verification_brief(markdown, "lesson.m4a")
        existing_header = "8.1. Уже есть в созвоне или первичном анализе"
        optional_header = "8.2. Можно дополнительно усилить roadmap"
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
                "--public-root",
                str(self.root / "public"),
            ]), \
            patch.object(NOTIFY, "telegram_request", side_effect=fake_telegram_request), \
            patch.object(NOTIFY, "telegram_multipart_request", side_effect=fake_multipart), \
            patch.object(NOTIFY, "ensure_article_pdf", side_effect=fake_pdf), \
            patch.object(NOTIFY.subprocess, "run"):
            self.assertEqual(NOTIFY.main(), 0)

        self.assertIn("Открой красивую версию", sent_texts[-1]["text"])
        labels = [button["text"] for row in sent_texts[-1]["reply_markup"]["inline_keyboard"] for button in row]  # type: ignore[index]
        self.assertEqual(labels, ["Открыть красиво"])
        self.assertEqual([path.name for path, _name in sent_docs], ["roadmap-article.html", "roadmap-article.pdf"])
        self.assertEqual([name for _path, name in sent_docs], ["Настя а2 roadmap.html", "Настя а2 roadmap.pdf"])


class ApprovedProcessorTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
