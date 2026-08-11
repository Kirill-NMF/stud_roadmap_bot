#!/usr/bin/env python3
"""Process newly downloaded Zoom audio files.

Current automated stage:
- detect unprocessed audio files in inbox
- transcribe with faster-whisper
- write transcript artifacts and status/event logs

The later consultation-summary stages intentionally remain separate because the
summary skill requires human clarification before final student-facing output.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
import os
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request


DEFAULT_INBOX_DIR = "/var/lib/zoom-audio-pipeline/inbox"
DEFAULT_RUNS_DIR = "/var/lib/zoom-audio-pipeline/runs"
DEFAULT_STATE_FILE = "/var/lib/zoom-audio-pipeline/audio-process-state.json"
DEFAULT_EVENTS_FILE = "/var/log/zoom-audio-pipeline/events.jsonl"
DEFAULT_OPENROUTER_TRANSCRIPTIONS_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_OPENROUTER_STT_MODEL = "openai/whisper-large-v3-turbo"
DEFAULT_OPENROUTER_API_KEY_FILE = "~/.config/openrouter/api_key"
RETRIABLE_OPENROUTER_HTTP_CODES = {429, 500, 502, 503, 504}
AUDIO_SUFFIXES = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".webm"}


def safe_slug(name: str) -> str:
    value = Path(name).stem
    value = re.sub(r"[^\wа-яА-ЯёЁ.-]+", "-", value, flags=re.UNICODE).strip("-._")
    return value or "audio"


def file_key(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"processed": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def parse_utc_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def mark_transcription_error(
    *,
    status_path: Path,
    status: dict[str, Any],
    processed: dict[str, Any],
    key: str,
    state: dict[str, Any],
    state_path: Path,
    events_path: Path,
    audio_path: Path,
    run_dir: Path,
    error: Exception,
) -> None:
    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    status.update({"status": "error", "error": repr(error), "finished_at": finished_at})
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    processed[key].update({
        "status": "error",
        "error": repr(error),
        "finished_at": finished_at,
    })
    save_state(state_path, state)
    append_event(events_path, {"stage": "transcription_error", "audio": audio_path.name, "run_dir": str(run_dir), "error": repr(error)})


def intake_sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_name(audio_path.name + ".telegram-intake.json")


def load_intake_sidecar(audio_path: Path) -> dict[str, Any]:
    sidecar = intake_sidecar_path(audio_path)
    if not sidecar.exists():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def notify_args(stage: str, audio_name: str, run_dir: Path, intake_meta: dict[str, Any] | None = None) -> list[str]:
    args = ["--stage", stage, "--audio", audio_name, "--run-dir", str(run_dir)]
    chat_id = str((intake_meta or {}).get("telegram_chat_id") or "").strip()
    if chat_id:
        args.extend(["--chat-id", chat_id])
    return args


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def write_transcript_artifacts(
    audio_path: Path,
    run_dir: Path,
    *,
    plain_lines: list[str],
    timed_lines: list[str],
    markdown_lines: list[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    plain_text = "\n".join(line.strip() for line in plain_lines if line.strip()).strip() + "\n"
    timed_text = "\n".join(line.strip() for line in timed_lines if line.strip()).strip() + "\n"
    markdown_text = "\n".join(markdown_lines).strip() + "\n"

    (run_dir / "transcript-plain.txt").write_text(plain_text, encoding="utf-8")
    (run_dir / "transcript.txt").write_text(timed_text, encoding="utf-8")
    (run_dir / "transcript.md").write_text(markdown_text, encoding="utf-8")
    metadata = {
        "audio_path": str(audio_path),
        "audio_name": audio_path.name,
        **metadata,
    }
    (run_dir / "transcript-meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def transcribe_audio_local(
    audio_path: Path,
    run_dir: Path,
    *,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
    beam_size: int,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
    )
    segments = list(segments_iter)

    plain_lines: list[str] = []
    timed_lines: list[str] = []
    markdown_lines: list[str] = [
        f"# Transcript: {audio_path.name}",
        "",
        f"- Language: {getattr(info, 'language', '')}",
        f"- Duration: {round(getattr(info, 'duration', 0.0), 2)} seconds",
        "",
        "## Text",
        "",
    ]

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        plain_lines.append(text)
        timed_lines.append(f"[{format_timestamp(segment.start)} - {format_timestamp(segment.end)}] {text}")
        markdown_lines.append(f"**{format_timestamp(segment.start)} - {format_timestamp(segment.end)}**")
        markdown_lines.append("")
        markdown_lines.append(text)
        markdown_lines.append("")

    return write_transcript_artifacts(
        audio_path,
        run_dir,
        plain_lines=plain_lines,
        timed_lines=timed_lines,
        markdown_lines=markdown_lines,
        metadata={
            "provider": "local",
            "timestamps": True,
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": getattr(info, "duration", None),
            "segments": len(segments),
            "model": model_name,
            "device": device,
            "compute_type": compute_type,
        },
    )


def audio_format(audio_path: Path) -> str:
    suffix = audio_path.suffix.lower().lstrip(".")
    if suffix == "mp4":
        return "mp4"
    return suffix or "m4a"


class OpenRouterTranscriptionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retriable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retriable = retriable


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
    run_dir: Path,
    *,
    compress_threshold_mb: float,
    ffmpeg_path: str,
    ffmpeg_timeout: int,
) -> tuple[Path, str, dict[str, Any]]:
    original_bytes = audio_path.stat().st_size
    threshold_bytes = int(compress_threshold_mb * 1024 * 1024)
    details: dict[str, Any] = {
        "source_audio_bytes": original_bytes,
        "compressed": False,
    }
    if threshold_bytes <= 0 or original_bytes <= threshold_bytes:
        details["request_audio_bytes"] = original_bytes
        return audio_path, audio_format(audio_path), details

    compressed_path = run_dir / "openrouter-stt-input.mp3"
    ffmpeg_log = run_dir / "openrouter-ffmpeg.log"
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
    try:
        with ffmpeg_log.open("w", encoding="utf-8") as log:
            subprocess.run(command, check=True, stdout=log, stderr=log, timeout=ffmpeg_timeout)
    except Exception as error:
        details["compression_error"] = repr(error)
        details["request_audio_bytes"] = original_bytes
        return audio_path, audio_format(audio_path), details

    compressed_bytes = compressed_path.stat().st_size if compressed_path.exists() else 0
    if compressed_bytes <= 0 or compressed_bytes >= original_bytes:
        details["compression_error"] = "compressed file missing, empty, or not smaller"
        details["request_audio_bytes"] = original_bytes
        return audio_path, audio_format(audio_path), details

    details.update(
        {
            "compressed": True,
            "compression_format": "mp3",
            "request_audio_bytes": compressed_bytes,
            "compression_ratio": round(compressed_bytes / original_bytes, 4),
        }
    )
    return compressed_path, "mp3", details


def create_openrouter_transcription_request(
    endpoint: str,
    *,
    api_key: str,
    audio_path: Path,
    audio_format_value: str,
    model_name: str,
    language: str | None,
) -> request.Request:
    encoded_audio = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    payload: dict[str, Any] = {
        "model": model_name,
        "input_audio": {
            "data": encoded_audio,
            "format": audio_format_value,
        },
        "temperature": 0,
    }
    if language:
        payload["language"] = language

    return request.Request(
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


def post_openrouter_transcription(req: request.Request, *, timeout: int) -> dict[str, Any]:
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise OpenRouterTranscriptionError(
            f"OpenRouter transcription failed: HTTP {error.code}: {detail}",
            status_code=error.code,
            retriable=error.code in RETRIABLE_OPENROUTER_HTTP_CODES,
        ) from error
    except urlerror.URLError as error:
        raise OpenRouterTranscriptionError(
            f"OpenRouter transcription failed: {error.reason}",
            retriable=True,
        ) from error


def transcribe_audio_openrouter(
    audio_path: Path,
    run_dir: Path,
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
) -> dict[str, Any]:
    api_key = read_openrouter_api_key(api_key_env, api_key_file)
    request_audio_path, fmt, upload_details = prepare_openrouter_audio(
        audio_path,
        run_dir,
        compress_threshold_mb=compress_threshold_mb,
        ffmpeg_path=ffmpeg_path,
        ffmpeg_timeout=ffmpeg_timeout,
    )

    attempts = max(1, retries)
    errors: list[str] = []
    result: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        req = create_openrouter_transcription_request(
            endpoint,
            api_key=api_key,
            audio_path=request_audio_path,
            audio_format_value=fmt,
            model_name=model_name,
            language=language,
        )
        try:
            result = post_openrouter_transcription(req, timeout=timeout)
            text_for_check = str(result.get("text", "")).strip()
            if not text_for_check:
                raise OpenRouterTranscriptionError(
                    "OpenRouter transcription response did not contain text",
                    retriable=True,
                )
            break
        except OpenRouterTranscriptionError as error:
            errors.append(f"attempt {attempt}/{attempts}: {error}")
            if not error.retriable or attempt >= attempts:
                raise OpenRouterTranscriptionError("; ".join(errors), status_code=error.status_code) from error
            time.sleep(retry_delay * (2 ** (attempt - 1)))

    if result is None:
        raise RuntimeError("OpenRouter transcription failed without a response")

    text = str(result.get("text", "")).strip()
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    markdown_lines = [
        f"# Transcript: {audio_path.name}",
        "",
        f"- Language: {language or ''}",
        f"- Provider: openrouter",
        f"- Model: {model_name}",
        "",
        "## Text",
        "",
        text,
    ]

    return write_transcript_artifacts(
        audio_path,
        run_dir,
        plain_lines=[text],
        timed_lines=[text],
        markdown_lines=markdown_lines,
        metadata={
            "provider": "openrouter",
            "timestamps": False,
            "language": language,
            "language_probability": None,
            "duration": usage.get("seconds"),
            "segments": 1,
            "model": model_name,
            "audio_format": fmt,
            "source_audio_format": audio_format(audio_path),
            "openrouter_attempts": len(errors) + 1 if errors else 1,
            "openrouter_retry_errors": errors,
            "openrouter_upload": upload_details,
            "usage": usage,
        },
    )


def transcribe_audio(
    audio_path: Path,
    run_dir: Path,
    *,
    provider: str,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
    beam_size: int,
    openrouter_model: str,
    openrouter_api_key_env: str,
    openrouter_api_key_file: str,
    openrouter_endpoint: str,
    openrouter_timeout: int,
    openrouter_retries: int,
    openrouter_retry_delay: float,
    openrouter_compress_threshold_mb: float,
    openrouter_ffmpeg: str,
    openrouter_ffmpeg_timeout: int,
) -> dict[str, Any]:
    if provider == "openrouter":
        return transcribe_audio_openrouter(
            audio_path,
            run_dir,
            model_name=openrouter_model,
            language=language,
            api_key_env=openrouter_api_key_env,
            api_key_file=openrouter_api_key_file,
            endpoint=openrouter_endpoint,
            timeout=openrouter_timeout,
            retries=openrouter_retries,
            retry_delay=openrouter_retry_delay,
            compress_threshold_mb=openrouter_compress_threshold_mb,
            ffmpeg_path=openrouter_ffmpeg,
            ffmpeg_timeout=openrouter_ffmpeg_timeout,
        )
    if provider != "local":
        raise ValueError(f"Unsupported transcription provider: {provider}")
    return transcribe_audio_local(
        audio_path,
        run_dir,
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        language=language,
        beam_size=beam_size,
    )


def run_verification(
    script_path: str,
    run_dir: Path,
    audio_name: str,
    events_path: Path,
    *,
    force: bool = False,
) -> None:
    if not script_path:
        return
    verification_path = run_dir / "verification.md"
    if verification_path.exists() and verification_path.stat().st_size > 0 and not force:
        print(f"verification_skipped: {audio_name} -> {verification_path}")
        return

    append_event(events_path, {"stage": "verification_started", "audio": audio_name, "run_dir": str(run_dir)})
    print(f"verification: {audio_name} -> {verification_path}")
    try:
        subprocess.run([script_path, str(run_dir)], check=True)
    except Exception as error:
        append_event(events_path, {"stage": "verification_error", "audio": audio_name, "run_dir": str(run_dir), "error": repr(error)})
        raise
    append_event(events_path, {"stage": "verification_done", "audio": audio_name, "run_dir": str(run_dir), "verification": str(verification_path)})
    print(f"verification_done: {audio_name} -> {verification_path}")


def notify(script_path: str, args: list[str]) -> None:
    if not script_path:
        return
    try:
        subprocess.run([script_path, *args], check=True)
    except Exception as error:
        print(f"telegram_notify_error: {error!r}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe new audio files from pipeline inbox.")
    parser.add_argument("--inbox-dir", default=DEFAULT_INBOX_DIR)
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--events-file", default=DEFAULT_EVENTS_FILE)
    parser.add_argument("--model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--transcription-provider",
        choices=["local", "openrouter"],
        default=os.environ.get("TRANSCRIPTION_PROVIDER", "local"),
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
    parser.add_argument(
        "--openrouter-timeout",
        type=int,
        default=int(os.environ.get("OPENROUTER_STT_TIMEOUT", "900")),
    )
    parser.add_argument(
        "--openrouter-retries",
        type=int,
        default=int(os.environ.get("OPENROUTER_STT_RETRIES", "3")),
    )
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
    parser.add_argument(
        "--openrouter-ffmpeg",
        default=os.environ.get("OPENROUTER_STT_FFMPEG", "ffmpeg"),
    )
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
    parser.add_argument(
        "--transcribing-stale-after-sec",
        type=int,
        default=int(os.environ.get("TRANSCRIPTION_STALE_AFTER_SEC", "3600")),
    )
    parser.add_argument("--verification-script", default="")
    parser.add_argument("--notify-script", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    inbox_dir = Path(args.inbox_dir)
    runs_dir = Path(args.runs_dir)
    state_path = Path(args.state_file)
    events_path = Path(args.events_file)
    state = load_state(state_path)
    processed = state.setdefault("processed", {})

    audio_files = sorted(
        path for path in inbox_dir.iterdir() if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
    ) if inbox_dir.exists() else []

    print(f"audio_files={len(audio_files)}")
    for audio_path in audio_files:
        key = file_key(audio_path)
        intake_meta = load_intake_sidecar(audio_path)
        if key in processed and not args.force:
            entry = processed[key]
            print(f"skipped: {audio_path.name} -> {entry.get('run_dir')}")
            run_dir_value = entry.get("run_dir")
            transcript_path = Path(run_dir_value) / "transcript.md" if run_dir_value else None
            if transcript_path and transcript_path.exists():
                run_verification(args.verification_script, Path(run_dir_value), audio_path.name, events_path)
                continue
            elif entry.get("status") == "transcribing":
                started_at = parse_utc_ts(str(entry.get("started_at") or ""))
                age_seconds = time.time() - started_at if started_at else 0
                if args.transcribing_stale_after_sec > 0 and started_at and age_seconds >= args.transcribing_stale_after_sec:
                    stale_run_dir = Path(run_dir_value) if run_dir_value else None
                    if stale_run_dir and (stale_run_dir / "status.json").exists():
                        stale_status = load_state(stale_run_dir / "status.json")
                        stale_status.update({
                            "status": "error",
                            "error": f"stale transcribing after {int(age_seconds)} seconds without transcript",
                            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        })
                        (stale_run_dir / "status.json").write_text(json.dumps(stale_status, ensure_ascii=False, indent=2), encoding="utf-8")
                    append_event(
                        events_path,
                        {
                            "stage": "transcription_stale_recovered",
                            "audio": audio_path.name,
                            "run_dir": run_dir_value,
                            "age_seconds": int(age_seconds),
                        },
                    )
                    processed.pop(key, None)
                    save_state(state_path, state)
                else:
                    append_event(
                        events_path,
                        {
                            "stage": "transcription_in_progress_skipped",
                            "audio": audio_path.name,
                            "run_dir": run_dir_value,
                        },
                    )
                    continue
            else:
                continue

        run_dir = runs_dir / f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{safe_slug(audio_path.name)}-{key[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        status_path = run_dir / "status.json"
        status = {
            "status": "transcribing",
            "audio_path": str(audio_path),
            "audio_name": audio_path.name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if intake_meta:
            status["intake"] = intake_meta
            if intake_meta.get("telegram_chat_id"):
                status["telegram_chat_id"] = str(intake_meta["telegram_chat_id"])
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        processed[key] = {
            "status": "transcribing",
            "audio_path": str(audio_path),
            "audio_name": audio_path.name,
            "run_dir": str(run_dir),
            "started_at": status["started_at"],
            "bytes": audio_path.stat().st_size,
        }
        if intake_meta:
            processed[key]["intake"] = intake_meta
            if intake_meta.get("telegram_chat_id"):
                processed[key]["telegram_chat_id"] = str(intake_meta["telegram_chat_id"])
        save_state(state_path, state)
        append_event(events_path, {"stage": "transcription_started", "audio": audio_path.name, "run_dir": str(run_dir)})
        print(f"transcribing: {audio_path.name} -> {run_dir}")

        try:
            metadata = transcribe_audio(
                audio_path,
                run_dir,
                provider=args.transcription_provider,
                model_name=args.model,
                device=args.device,
                compute_type=args.compute_type,
                language=args.language or None,
                beam_size=args.beam_size,
                openrouter_model=args.openrouter_model,
                openrouter_api_key_env=args.openrouter_api_key_env,
                openrouter_api_key_file=args.openrouter_api_key_file,
                openrouter_endpoint=args.openrouter_endpoint,
                openrouter_timeout=args.openrouter_timeout,
                openrouter_retries=args.openrouter_retries,
                openrouter_retry_delay=args.openrouter_retry_delay,
                openrouter_compress_threshold_mb=args.openrouter_compress_threshold_mb,
                openrouter_ffmpeg=args.openrouter_ffmpeg,
                openrouter_ffmpeg_timeout=args.openrouter_ffmpeg_timeout,
            )
        except Exception as error:
            if args.transcription_provider == "openrouter" and args.openrouter_fallback == "local":
                append_event(
                    events_path,
                    {
                        "stage": "transcription_provider_fallback",
                        "audio": audio_path.name,
                        "run_dir": str(run_dir),
                        "from": "openrouter",
                        "to": "local",
                        "error": repr(error),
                    },
                )
                print(f"transcription_provider_fallback: {audio_path.name} openrouter -> local", file=sys.stderr)
                try:
                    metadata = transcribe_audio(
                        audio_path,
                        run_dir,
                        provider="local",
                        model_name=args.model,
                        device=args.device,
                        compute_type=args.compute_type,
                        language=args.language or None,
                        beam_size=args.beam_size,
                        openrouter_model=args.openrouter_model,
                        openrouter_api_key_env=args.openrouter_api_key_env,
                        openrouter_api_key_file=args.openrouter_api_key_file,
                        openrouter_endpoint=args.openrouter_endpoint,
                        openrouter_timeout=args.openrouter_timeout,
                        openrouter_retries=args.openrouter_retries,
                        openrouter_retry_delay=args.openrouter_retry_delay,
                        openrouter_compress_threshold_mb=args.openrouter_compress_threshold_mb,
                        openrouter_ffmpeg=args.openrouter_ffmpeg,
                        openrouter_ffmpeg_timeout=args.openrouter_ffmpeg_timeout,
                    )
                except Exception as fallback_error:
                    mark_transcription_error(
                        status_path=status_path,
                        status=status,
                        processed=processed,
                        key=key,
                        state=state,
                        state_path=state_path,
                        events_path=events_path,
                        audio_path=audio_path,
                        run_dir=run_dir,
                        error=fallback_error,
                    )
                    raise
            else:
                mark_transcription_error(
                    status_path=status_path,
                    status=status,
                    processed=processed,
                    key=key,
                    state=state,
                    state_path=state_path,
                    events_path=events_path,
                    audio_path=audio_path,
                    run_dir=run_dir,
                    error=error,
                )
                raise

        status.update({
            "status": "transcribed",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "transcript": str(run_dir / "transcript.md"),
            "metadata": metadata,
        })
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        processed[key] = {
            "status": "transcribed",
            "audio_path": str(audio_path),
            "audio_name": audio_path.name,
            "run_dir": str(run_dir),
            "transcript": str(run_dir / "transcript.md"),
            "processed_at": status["finished_at"],
            "bytes": audio_path.stat().st_size,
        }
        save_state(state_path, state)
        append_event(events_path, {"stage": "transcription_done", "audio": audio_path.name, "run_dir": str(run_dir), "transcript": str(run_dir / "transcript.md")})
        print(f"transcribed: {audio_path.name} -> {run_dir / 'transcript.md'}")
        notify(
            args.notify_script,
            notify_args("transcript_ready", audio_path.name, run_dir, intake_meta),
        )

        run_verification(args.verification_script, run_dir, audio_path.name, events_path)
        if args.verification_script and (run_dir / "verification.md").exists():
            notify(
                args.notify_script,
                notify_args("verification_ready", audio_path.name, run_dir, intake_meta),
            )

    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
