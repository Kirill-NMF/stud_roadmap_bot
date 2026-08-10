#!/usr/bin/env python3
"""Run the approved Gemini-style rewrite chain as one continuous chat."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "google/gemini-2.5-pro"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PASS_1_HEADER = """Ты русский редактор и рерайтер student-facing текстов для аудитории 25–30 лет.

Твоя задача: переписать текст более по-человечески, тепло, поддерживающе и естественно, НО не менять структуру и факты.

ЖЁСТКИЕ ПРАВИЛА:

1. Сохрани структуру на 100%:

- не меняй порядок блоков;

- не переименовывай заголовки;

- не удаляй заголовки;

- не добавляй новые заголовки;

- не объединяй блоки;

- не переставляй абзацы между блоками;

- таблицу оставь на том же месте;

- списки оставь списками;

- финальный блок оставь финальным блоком.

2. Сохрани факты на 100%:

- не меняй уровень ученицы;

- не меняй сроки;

- не меняй обещания;

- не меняй платформу;

- не меняй цели;

- не добавляй новые результаты;

- не усиливай обещания сверх исходного текста;

- не добавляй новые детали, которых нет в исходнике.

3. Что можно менять:

- формулировки внутри абзацев;

- тон;

- плавность;

- поддержку;

- естественность русского языка;

- убрать сухость, отчетность и AI-стиль;

- сделать текст более живым и понятным.

4. Стиль:

- русский текст, как у хорошего рерайтера;

- аудитория: 25–30 лет;

- тон: спокойный, тёплый, уверенный;

- без корпоративного стиля;

- без инфоцыганских обещаний;

- без детской мотивации;

- без чрезмерного восторга;

- без фраз вроде “ты точно добьёшься невероятных результатов”;

- текст должен звучать как сообщение от внимательного преподавателя, а не как маркетинговая рассылка.

5. Очень важно:

Если сомневаешься, лучше оставить структуру и факт как в исходнике.

Твоя задача — рерайт, а не редизайн текста.

Верни только финальный переписанный текст в Markdown.

Не добавляй комментарии, объяснения или список изменений.

ИСХОДНЫЙ ТЕКСТ:
"""

PASS_2_PROMPT = "Перепиши это также, только давай в стиле Венни Пака."

SAFE_PASS_2_PROMPT = (
    "Перепиши это также, только давай в стиле Венни Пака."
)

PASS_3_PROMPT = (
    "Просто идеально. Только я хочу, чтобы ты использовал чуть меньше англицизмов. "
    "Смотри, я сказал немного убери англицизмы. Это значит то, что убери англицизмы, "
    "которые не свойственны русской речи, которые обычно в текстах пишутся английскими "
    "буквами. Те, что пишутся русскими буквами, можно оставить из предыдущего текста."
)

SAFE_PASS_3_PROMPT = (
    "Просто идеально. Только я хочу, чтобы ты использовал чуть меньше англицизмов. "
    "Это значит то, что убери англицизмы, которые не свойственны русской речи, которые "
    "обычно в текстах пишутся английскими буквами. Те, что пишутся русскими буквами, "
    "можно оставить из предыдущего текста."
)

SAFE_SYSTEM_PROMPT = """Ты работаешь как аккуратный редактор финальной student-facing статьи.

Твоя главная задача - улучшить тон и естественность русского языка, не меняя смысл.

Жёсткие ограничения:
- не меняй факты;
- не меняй имя, уровень, цели, сроки, результаты, цену, расписание, платформы и формат занятий;
- не добавляй новые цели, события, экзамены, интервью, работу, страну, поездку или платформу, если их нет в исходном тексте;
- не переименовывай заголовки;
- не меняй порядок блоков;
- не удаляй и не добавляй блоки;
- не меняй структуру таблиц;
- не переводи и не заменяй доменные термины, если они уже есть в тексте: Progress.me, YouTube, shorts, reels, small talk, foreign company, A0, A1, A2, B1, B2;
- если пользователь просит стиль или вайб, это относится только к тону, плавности и живости языка.

Если не уверен, сохраняй исходную формулировку ближе к тексту."""


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def read_source(path: str | None) -> str:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if not text:
        raise SystemExit("No source text provided")
    return text


def assistant_history_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if content is None:
        content = ""
    history_message: dict[str, Any] = {"role": "assistant", "content": content}
    # Gemini 3.x reasoning models via OpenRouter can require these fields on follow-ups.
    for key in ("reasoning", "reasoning_details"):
        if message.get(key):
            history_message[key] = message[key]
    return history_message


def request_chat(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    top_p: float | None,
    reasoning: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if reasoning is not None:
        payload["reasoning"] = reasoning

    request = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "HTTP-Referer": "https://codex.local",
            "X-Title": "Codex Gemini Chat Chain",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        raise SystemExit(body) from error

    choice = data["choices"][0]
    message = choice["message"]
    content = message.get("content")
    if content is None:
        finish_reason = choice.get("finish_reason")
        reasoning_text = message.get("reasoning", "")
        preview = reasoning_text[:500] if reasoning_text else ""
        raise SystemExit(
            "Model returned no content. "
            f"finish_reason={finish_reason!r}. "
            "Increase --max-tokens, lower/disable reasoning, or choose a non-reasoning Pro model. "
            f"Reasoning preview: {preview}"
        )
    return content, message, data


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the approved three-pass Gemini rewrite chain as one continuous OpenRouter chat."
    )
    parser.add_argument("input", nargs="?", help="UTF-8 text file. If omitted, stdin is used.")
    parser.add_argument("-m", "--model", default=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL))
    parser.add_argument("-t", "--temperature", type=float, default=0.3)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--env-file", default="/root/.openrouter/openrouter.env")
    parser.add_argument("--save-dir", help="Directory for pass outputs, messages, and usage logs.")
    parser.add_argument(
        "--system",
        help="Optional production stabilizer system prompt. Omit for faithful Gemini-web mode.",
    )
    parser.add_argument(
        "--production-safe",
        action="store_true",
        help="Use stricter system and follow-up prompts for production fact/structure preservation.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high"),
        default="minimal",
        help="OpenRouter reasoning effort. Default minimal keeps mandatory-reasoning Gemini models stable.",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    source = read_source(args.input)
    save_dir = Path(args.save_dir) if args.save_dir else None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)

    messages: list[dict[str, Any]] = []
    if args.production_safe and not args.system:
        args.system = SAFE_SYSTEM_PROMPT

    if args.system:
        messages.append({"role": "system", "content": args.system})

    reasoning = {"exclude": True} if args.reasoning_effort == "none" else {"effort": args.reasoning_effort, "exclude": True}
    pass_prompts = [
        PASS_1_HEADER + "\n" + source,
        SAFE_PASS_2_PROMPT if args.production_safe else PASS_2_PROMPT,
        SAFE_PASS_3_PROMPT if args.production_safe else PASS_3_PROMPT,
    ]

    final_content = ""
    for index, prompt in enumerate(pass_prompts, start=1):
        messages.append({"role": "user", "content": prompt})
        if save_dir:
            write_json(save_dir / f"pass{index}-request-messages.json", messages)

        started = time.time()
        content, assistant_message, raw = request_chat(
            api_key=api_key,
            model=args.model,
            messages=messages,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            top_p=args.top_p,
            reasoning=reasoning,
        )
        elapsed = round(time.time() - started, 3)
        messages.append(assistant_history_message(assistant_message))
        final_content = content

        if save_dir:
            (save_dir / f"pass{index}.md").write_text(content, encoding="utf-8")
            usage = {
                "model": raw.get("model"),
                "provider": raw.get("provider"),
                "usage": raw.get("usage"),
                "finish_reason": raw.get("choices", [{}])[0].get("finish_reason"),
                "elapsed_seconds": elapsed,
                "has_reasoning": bool(assistant_message.get("reasoning_details") or assistant_message.get("reasoning")),
            }
            write_json(save_dir / f"pass{index}-usage.json", usage)

    if save_dir:
        write_json(save_dir / "final-history.json", messages)
        (save_dir / "final.md").write_text(final_content, encoding="utf-8")

    print(final_content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
