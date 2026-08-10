#!/usr/bin/env python3
"""Transcribe a Telegram voice/audio correction to text."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request


DEFAULT_OPENROUTER_TRANSCRIPTIONS_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_OPENROUTER_STT_MODEL = "openai/whisper-large-v3-turbo"
DEFAULT_OPENROUTER_API_KEY_FILE = "~/.config/openrouter/api_key"
RETRIABLE_OPENROUTER_HTTP_CODES = {429, 500, 502, 503, 504}


class OpenRouterTranscriptionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retriable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable


def audio_format(audio_path: Path) -> str:
    suffix = audio_path.suffix.lower().lstrip(".")
    if suffix == "oga":
        return "ogg"
    if suffix == "m4a":
        return "mp4"
    return suffix or "m4a"


def read_openrouter_api_key(api_key_env: str, api_key_file: str) -> str:
    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key and api_key_file:
        key_path = Path(api_key_file).expanduser()
        if key_path.exists():
            api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise RuntimeError(f"OpenRouter API key is missing: env={api_key_env}, file={api_key_file}")
    return api_key


def prepare_openrouter_audio(
    audio_path: Path,
    work_dir: Path,
    *,
    compress_threshold_mb: float,
    ffmpeg_path: str,
    ffmpeg_timeout: int,
) -> tuple[Path, str]:
    threshold_bytes = int(compress_threshold_mb * 1024 * 1024)
    if threshold_bytes <= 0 or audio_path.stat().st_size <= threshold_bytes:
        return audio_path, audio_format(audio_path)

    compressed_path = work_dir / "openrouter-voice-stt-input.mp3"
    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "32k",
        str(compressed_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=ffmpeg_timeout)
    if not compressed_path.exists() or compressed_path.stat().st_size <= 0:
        raise RuntimeError("ffmpeg produced an empty OpenRouter voice input")
    return compressed_path, "mp3"


def transcribe_openrouter(
    audio_path: Path,
    *,
    model_name: str,
    language: str | None,
    api_key_env: str,
    api_key_file: str,
    endpoint: str,
    timeout: int,
    retries: int,
    retry_delay: float,
    compress_threshold_mb: float,
    ffmpeg_path: str,
    ffmpeg_timeout: int,
) -> str:
    api_key = read_openrouter_api_key(api_key_env, api_key_file)
    with tempfile.TemporaryDirectory(prefix="roadmap-voice-stt-") as tmp:
        request_audio_path, fmt = prepare_openrouter_audio(
            audio_path,
            Path(tmp),
            compress_threshold_mb=compress_threshold_mb,
            ffmpeg_path=ffmpeg_path,
            ffmpeg_timeout=ffmpeg_timeout,
        )
        encoded_audio = base64.b64encode(request_audio_path.read_bytes()).decode("ascii")

    payload: dict[str, object] = {
        "model": model_name,
        "input_audio": {
            "data": encoded_audio,
            "format": fmt,
        },
        "temperature": 0,
    }
    if language:
        payload["language"] = language

    attempts = max(1, retries)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        req = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://dev.short-talk.space",
                "X-Title": "ShortTalk Roadmap Pipeline",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            text = str(result.get("text", "")).strip()
            if not text:
                raise OpenRouterTranscriptionError(
                    "OpenRouter transcription response did not contain text",
                    retriable=True,
                )
            return text
        except urlerror.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            exc = OpenRouterTranscriptionError(
                f"OpenRouter transcription failed: HTTP {error.code}: {detail}",
                status_code=error.code,
                retriable=error.code in RETRIABLE_OPENROUTER_HTTP_CODES,
            )
        except urlerror.URLError as error:
            exc = OpenRouterTranscriptionError(
                f"OpenRouter transcription failed: {error.reason}",
                retriable=True,
            )
        errors.append(f"attempt {attempt}/{attempts}: {exc}")
        if not exc.retriable or attempt >= attempts:
            raise OpenRouterTranscriptionError("; ".join(errors), status_code=exc.status_code) from exc
        time.sleep(retry_delay * (2 ** (attempt - 1)))

    raise RuntimeError("OpenRouter transcription failed without a response")


def transcribe_local(audio_path: Path, *, model_name: str, language: str, device: str, compute_type: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(audio_path), language=language, vad_filter=True, beam_size=1)
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe Telegram voice note.")
    parser.add_argument("audio")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument(
        "--provider",
        choices=["local", "openrouter"],
        default=os.environ.get("TELEGRAM_VOICE_TRANSCRIPTION_PROVIDER", os.environ.get("TRANSCRIPTION_PROVIDER", "local")),
    )
    parser.add_argument(
        "--openrouter-model",
        default=os.environ.get("OPENROUTER_STT_MODEL", DEFAULT_OPENROUTER_STT_MODEL),
    )
    parser.add_argument(
        "--openrouter-api-key-env",
        default=os.environ.get("OPENROUTER_API_KEY_ENV", "OPENROUTER_API_KEY"),
    )
    parser.add_argument(
        "--openrouter-api-key-file",
        default=os.environ.get("OPENROUTER_API_KEY_FILE", DEFAULT_OPENROUTER_API_KEY_FILE),
    )
    parser.add_argument(
        "--openrouter-endpoint",
        default=os.environ.get("OPENROUTER_TRANSCRIPTIONS_URL", DEFAULT_OPENROUTER_TRANSCRIPTIONS_URL),
    )
    parser.add_argument("--openrouter-timeout", type=int, default=int(os.environ.get("OPENROUTER_STT_TIMEOUT", "900")))
    parser.add_argument("--openrouter-retries", type=int, default=int(os.environ.get("OPENROUTER_STT_RETRIES", "3")))
    parser.add_argument(
        "--openrouter-retry-delay",
        type=float,
        default=float(os.environ.get("OPENROUTER_STT_RETRY_DELAY", "3")),
    )
    parser.add_argument(
        "--openrouter-compress-threshold-mb",
        type=float,
        default=float(os.environ.get("OPENROUTER_STT_COMPRESS_THRESHOLD_MB", "20")),
    )
    parser.add_argument("--openrouter-ffmpeg", default=os.environ.get("OPENROUTER_STT_FFMPEG", "ffmpeg"))
    parser.add_argument(
        "--openrouter-ffmpeg-timeout",
        type=int,
        default=int(os.environ.get("OPENROUTER_STT_FFMPEG_TIMEOUT", "300")),
    )
    parser.add_argument(
        "--openrouter-fallback",
        choices=["local", "none"],
        default=os.environ.get("OPENROUTER_STT_FALLBACK", "local"),
    )
    args = parser.parse_args()

    audio_path = Path(args.audio)
    if args.provider == "openrouter":
        try:
            text = transcribe_openrouter(
                audio_path,
                model_name=args.openrouter_model,
                language=args.language or None,
                api_key_env=args.openrouter_api_key_env,
                api_key_file=args.openrouter_api_key_file,
                endpoint=args.openrouter_endpoint,
                timeout=args.openrouter_timeout,
                retries=args.openrouter_retries,
                retry_delay=args.openrouter_retry_delay,
                compress_threshold_mb=args.openrouter_compress_threshold_mb,
                ffmpeg_path=args.openrouter_ffmpeg,
                ffmpeg_timeout=args.openrouter_ffmpeg_timeout,
            )
        except Exception as error:
            if args.openrouter_fallback != "local":
                raise
            print(f"OpenRouter voice transcription failed, falling back to local: {error!r}", file=sys.stderr)
            text = transcribe_local(
                audio_path,
                model_name=args.model,
                language=args.language,
                device=args.device,
                compute_type=args.compute_type,
            )
    else:
        text = transcribe_local(
            audio_path,
            model_name=args.model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
        )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
