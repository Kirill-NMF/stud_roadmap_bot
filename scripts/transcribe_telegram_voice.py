#!/usr/bin/env python3
"""Transcribe a short Telegram voice/audio file to text."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe Telegram voice note.")
    parser.add_argument("audio")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    audio_path = Path(args.audio)
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, _info = model.transcribe(str(audio_path), language=args.language, vad_filter=True, beam_size=1)
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
