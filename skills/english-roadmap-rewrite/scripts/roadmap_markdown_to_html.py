#!/usr/bin/env python3
"""Convert a student roadmap Markdown article into a standalone HTML file."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path


CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --paper: #ffffff;
  --ink: #202124;
  --muted: #5f6368;
  --line: #dfe3ea;
  --accent: #2f6f73;
  --accent-soft: #e6f3f1;
  --warm: #f5b759;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
  line-height: 1.62;
}

.page {
  max-width: 860px;
  margin: 0 auto;
  padding: 40px 20px 56px;
}

.article {
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 42px 48px;
  box-shadow: 0 10px 30px rgba(32, 33, 36, 0.06);
}

h1 {
  margin: 0 0 22px;
  font-size: 34px;
  line-height: 1.18;
  font-weight: 750;
  color: #153b3f;
}

h2 {
  margin: 34px 0 14px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  font-size: 23px;
  line-height: 1.25;
  color: #153b3f;
}

h3 {
  margin: 28px 0 12px;
  font-size: 20px;
  line-height: 1.3;
  color: #153b3f;
}

p {
  margin: 0 0 15px;
}

ul {
  margin: 0 0 18px;
  padding-left: 22px;
}

li {
  margin: 6px 0;
}

strong {
  font-weight: 700;
}

table {
  width: 100%;
  margin: 18px 0 22px;
  border-collapse: collapse;
  font-size: 15px;
}

th,
td {
  border: 1px solid var(--line);
  padding: 12px 14px;
  vertical-align: top;
  text-align: left;
}

th {
  background: var(--accent-soft);
  color: #153b3f;
  font-weight: 700;
}

tr:nth-child(even) td {
  background: #fafbfc;
}

.lead {
  font-size: 18px;
  color: var(--muted);
}

@media (max-width: 640px) {
  .page {
    padding: 0;
  }

  .article {
    border: 0;
    border-radius: 0;
    padding: 28px 20px 40px;
    box-shadow: none;
  }

  h1 {
    font-size: 28px;
  }

  table {
    display: block;
    overflow-x: auto;
    white-space: normal;
  }
}
"""


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return escaped


def is_table_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", line))


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def render_table(lines: list[str]) -> str:
    rows = [split_table_row(line) for line in lines if not is_table_separator(line)]
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    out = ["<table>", "<thead><tr>"]
    out.extend(f"<th>{inline_markdown(cell)}</th>" for cell in header)
    out.append("</tr></thead>")
    if body:
        out.append("<tbody>")
        for row in body:
            out.append("<tr>")
            out.extend(f"<td>{inline_markdown(cell)}</td>" for cell in row)
            out.append("</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def markdown_to_body(markdown: str) -> tuple[str, str]:
    lines = markdown.strip().splitlines()
    title = "Roadmap"
    output: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    i = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(part.strip() for part in paragraph if part.strip())
            cls = ' class="lead"' if not output and not text.startswith("<") else ""
            output.append(f"<p{cls}>{inline_markdown(text)}</p>")
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            output.append("<ul>")
            output.extend(f"<li>{inline_markdown(item)}</li>" for item in list_items)
            output.append("</ul>")
            list_items = []

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_list()
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            flush_paragraph()
            flush_list()
            table_lines = [stripped, lines[i + 1].strip()]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            output.append(render_table(table_lines))
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            if level == 1:
                title = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                output.append(f"<h1>{inline_markdown(text)}</h1>")
            elif level == 2:
                output.append(f"<h2>{inline_markdown(text)}</h2>")
            else:
                output.append(f"<h3>{inline_markdown(text)}</h3>")
            i += 1
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            flush_paragraph()
            list_items.append(bullet_match.group(1).strip())
            i += 1
            continue

        if not output:
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            output.append(f"<h1>{inline_markdown(stripped)}</h1>")
        else:
            paragraph.append(stripped)
        i += 1

    flush_paragraph()
    flush_list()
    return title, "\n".join(output)


def render_html(markdown: str) -> str:
    title, body = markdown_to_body(markdown)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <main class="page">
    <article class="article">
{body}
    </article>
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert roadmap Markdown to standalone HTML.")
    parser.add_argument("input", nargs="?", help="Markdown input file. If omitted, stdin is used.")
    parser.add_argument("-o", "--output", help="HTML output file. Defaults to input path with .html.")
    args = parser.parse_args()

    if args.input:
        input_path = Path(args.input)
        markdown = input_path.read_text(encoding="utf-8")
        output_path = Path(args.output) if args.output else input_path.with_suffix(".html")
    else:
        markdown = sys.stdin.read()
        if not args.output:
            raise SystemExit("--output is required when reading from stdin")
        output_path = Path(args.output)

    output_path.write_text(render_html(markdown), encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
