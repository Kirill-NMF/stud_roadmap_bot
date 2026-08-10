#!/usr/bin/env python3
"""Generate roadmap verification/article artifacts through OpenRouter chat API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_ENV_FILE = "/root/.openrouter/openrouter.env"
DEFAULT_MODEL = "openai/gpt-5.5"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def read_text(path: Path, fallback: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() and path.stat().st_size > 0 else fallback


def load_status(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_status(path: Path, status: dict[str, Any]) -> None:
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_status(path: Path, **values: Any) -> None:
    status = load_status(path)
    status.update(values)
    save_status(path, status)


def request_chat(
    *,
    api_key: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://codex.local",
            "X-Title": "Roadmap Pipeline",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"OpenRouter API error {error.code}: {body[:1000]}") from error
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content.strip():
        raise RuntimeError("OpenRouter returned empty content")
    return content.strip(), data


def build_verification_prompt(run_dir: Path, prompt_template: Path) -> str:
    transcript = run_dir / "transcript-plain.txt"
    if not transcript.exists() or transcript.stat().st_size == 0:
        transcript = run_dir / "transcript.md"
    if not transcript.exists() or transcript.stat().st_size == 0:
        raise RuntimeError(f"transcript not found or empty: {transcript}")
    if not prompt_template.exists() or prompt_template.stat().st_size == 0:
        raise RuntimeError(f"prompt template not found or empty: {prompt_template}")
    return "\n\n".join([
        prompt_template.read_text(encoding="utf-8"),
        "# Транскрипт консультации\n\n" + transcript.read_text(encoding="utf-8"),
    ])


def build_article_prompt(run_dir: Path, prompt_template: Path, enhancements: Path) -> str:
    transcript = run_dir / "transcript.md"
    verification = run_dir / "verification.md"
    notes = run_dir / "teacher-notes.md"
    if not transcript.exists() or transcript.stat().st_size == 0:
        raise RuntimeError(f"transcript not found or empty: {transcript}")
    if not verification.exists() or verification.stat().st_size == 0:
        raise RuntimeError(f"verification not found or empty: {verification}")
    if not prompt_template.exists() or prompt_template.stat().st_size == 0:
        raise RuntimeError(f"prompt template not found or empty: {prompt_template}")

    parts = [
        prompt_template.read_text(encoding="utf-8"),
        "# Первичный анализ и верификация\n\n" + verification.read_text(encoding="utf-8"),
        "# Правки преподавателя\n\n" + read_text(notes, "Правок преподавателя нет.\n"),
    ]
    if enhancements.exists() and enhancements.stat().st_size > 0:
        parts.append(
            "# Справочник PDF-опций P1-P14\n\n"
            + enhancements.read_text(encoding="utf-8")
            + "\n\nИспользуй эти опции только если преподаватель явно подтвердил соответствующий P-код в правках."
        )
    parts.append("# Транскрипт консультации\n\n" + transcript.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def render_html(markdown_path: Path, html_path: Path, converter: str) -> None:
    if not converter:
        return
    try:
        subprocess.run([converter, str(markdown_path), "-o", str(html_path)], check=True)
    except FileNotFoundError:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate roadmap artifacts through OpenRouter.")
    parser.add_argument("run_dir")
    parser.add_argument("--mode", choices=["verification", "article"], required=True)
    parser.add_argument("--model", default=os.getenv("OPENROUTER_ROADMAP_MODEL", DEFAULT_MODEL))
    parser.add_argument("--env-file", default=os.getenv("OPENROUTER_ENV_FILE", DEFAULT_ENV_FILE))
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--verification-prompt", default="/opt/zoom-audio-pipeline/prompts/consultation_verification_prompt.md")
    parser.add_argument("--article-prompt", default="/opt/zoom-audio-pipeline/prompts/consultation_article_prompt.md")
    parser.add_argument("--enhancements", default="/opt/zoom-audio-pipeline/prompts/roadmap_enhancement_options.md")
    parser.add_argument("--markdown-to-html", default="roadmap-markdown-to-html")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        print(f"{args.api_key_env} is not configured", file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir)
    status_path = run_dir / "status.json"
    if args.mode == "verification":
        output = run_dir / "verification.md"
        prompt_path = run_dir / "openrouter-verification.prompt.md"
        log_path = run_dir / "openrouter-verification.response.json"
        update_status(status_path, verification_status="started", verification_started_at=utc_now())
        prompt = build_verification_prompt(run_dir, Path(args.verification_prompt))
    else:
        output = run_dir / "roadmap-article.md"
        prompt_path = run_dir / "openrouter-article.prompt.md"
        log_path = run_dir / "openrouter-article.response.json"
        update_status(status_path, article_status="started", article_started_at=utc_now(), article_source="openrouter")
        prompt = build_article_prompt(run_dir, Path(args.article_prompt), Path(args.enhancements))

    prompt_path.write_text(prompt, encoding="utf-8")
    try:
        content, response = request_chat(
            api_key=api_key,
            model=args.model,
            prompt=prompt,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        )
    except Exception as error:
        failed_key = "verification_status" if args.mode == "verification" else "article_status"
        update_status(status_path, **{failed_key: "failed", f"{args.mode}_failed_at": utc_now(), f"{args.mode}_log": str(log_path)})
        log_path.write_text(json.dumps({"error": repr(error)}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"openrouter {args.mode} failed; see {log_path}", file=sys.stderr)
        return 1

    output.write_text(content + "\n", encoding="utf-8")
    log_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.mode == "article":
        html = run_dir / "roadmap-article.html"
        render_html(output, html, args.markdown_to_html)
        update_status(
            status_path,
            article_status="done",
            article_done_at=utc_now(),
            article_source="openrouter",
            article=str(output),
            article_bytes=output.stat().st_size,
            html=str(html) if html.exists() else "",
            html_bytes=html.stat().st_size if html.exists() else 0,
            openrouter_article_model=args.model,
        )
    else:
        update_status(
            status_path,
            verification_status="done",
            verification_done_at=utc_now(),
            verification=str(output),
            verification_bytes=output.stat().st_size,
            openrouter_verification_model=args.model,
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
