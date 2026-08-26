#!/usr/bin/env python3
"""Classify a Gemini rewrite without treating harmless wording as structural damage."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_MARKERS = ["Progress.me", "YouTube", "A0", "A1", "A2", "B1", "B2"]
P_CODE_PATTERN = re.compile(r"\bP(?:1[0-4]|[1-9])\b")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+\S")
UNIT_PATTERN = (
    r"%|руб(?:лей|ля|ль)?|месяц(?:а|ев)?|недел(?:я|и|ь)|раз(?:а)?|"
    r"минут(?:а|ы)?|час(?:а|ов)?|USDT"
)
RANGE_PATTERN = re.compile(
    rf"(?:\bот\s+)?(\d+(?:[.,]\d+)?)\s*(?:[-–—]|\bдо\b)\s*"
    rf"(\d+(?:[.,]\d+)?)\s*({UNIT_PATTERN})\b",
    re.IGNORECASE,
)
FACT_PATTERN = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:%|руб(?:лей|ля|ль)?|месяц(?:а|ев)?|недел(?:я|и|ь)|раз(?:а)?|"
    r"минут(?:а|ы)?|час(?:а|ов)?|USDT)\b",
    re.IGNORECASE,
)


def heading_levels(markdown: str) -> list[int]:
    levels: list[int] = []
    for line in markdown.splitlines():
        match = HEADING_PATTERN.match(line.strip())
        if match:
            levels.append(len(match.group(1)))
    return levels


def normalized_facts(markdown: str) -> set[str]:
    facts = {
        f"{match.group(1).replace(',', '.')}-{match.group(2).replace(',', '.')} {match.group(3).lower()}"
        for match in RANGE_PATTERN.finditer(markdown)
    }
    without_ranges = RANGE_PATTERN.sub(" ", markdown)
    facts.update(
        re.sub(r"\s+", " ", match.group(0).lower().replace(",", "."))
        for match in FACT_PATTERN.finditer(without_ranges)
    )
    return facts


def classify_rewrite(draft: str, final: str) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    draft_text = draft.strip()
    final_text = final.strip()

    minimum_length = max(400, int(len(draft_text) * 0.45))
    if len(final_text) < minimum_length:
        errors.append("Финальный текст выглядит оборванным или слишком коротким.")

    draft_levels = heading_levels(draft)
    final_levels = heading_levels(final)
    if len(draft_levels) != len(final_levels):
        warnings.append("Изменилось количество разделов статьи.")
    elif draft_levels != final_levels:
        warnings.append("Изменилась вложенность разделов статьи.")

    if "|" in draft and "|" not in final:
        warnings.append("Из статьи исчезла Markdown-таблица.")

    missing_markers = [marker for marker in REQUIRED_MARKERS if marker in draft and marker not in final]
    if missing_markers:
        warnings.append("Исчезли важные обозначения: " + ", ".join(missing_markers) + ".")

    extra_p_codes = sorted(set(P_CODE_PATTERN.findall(final)) - set(P_CODE_PATTERN.findall(draft)))
    if extra_p_codes:
        warnings.append("Появились неподтверждённые PDF-опции: " + ", ".join(extra_p_codes) + ".")

    missing_facts = sorted(normalized_facts(draft) - normalized_facts(final))
    if missing_facts:
        warnings.append("Исчезли числовые сроки или условия: " + ", ".join(missing_facts) + ".")

    status = "recovery_required" if errors else ("warning" if warnings else "ok")
    return {
        "status": status,
        "warnings": warnings,
        "errors": errors,
        "metrics": {
            "draft_characters": len(draft_text),
            "final_characters": len(final_text),
            "draft_sections": len(draft_levels),
            "final_sections": len(final_levels),
        },
    }


def save_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Gemini roadmap rewrite.")
    parser.add_argument("draft")
    parser.add_argument("final")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    draft = Path(args.draft).read_text(encoding="utf-8")
    final = Path(args.final).read_text(encoding="utf-8")
    report = classify_rewrite(draft, final)
    save_report(Path(args.report), report)
    print(report["status"])
    return 1 if report["status"] == "recovery_required" else 0


if __name__ == "__main__":
    raise SystemExit(main())
