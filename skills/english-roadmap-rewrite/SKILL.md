---
name: english-roadmap-rewrite
description: Rewrite and polish Russian student-facing English consultation roadmap articles after transcript analysis and roadmap drafting. Use when Codex needs to turn a draft English learning roadmap summary into a clear, warm, human, supportive, commercially strong final article for a 25-30-year-old Russian-speaking student while preserving the original block structure, confirmed facts, timelines, prices, platforms, goals, and teacher promises, then prepare a sendable standalone HTML article file when requested or when the workflow calls for deliverable formatting.
---

# English Roadmap Rewrite

## Overview

Use this skill as the third stage of the consultation-summary pipeline: transcript -> roadmap article -> polished final rewrite. The rewrite must improve clarity, emotional confidence, student-perceived value, and conversion quality without changing the factual plan or reorganizing the article structure.

When the user asks to match the approved Gemini Pro website result, reproduce the multi-pass style chain in `references/pro-style-calibration.md`.

## Core Workflow

### 1. Lock the Facts

Before rewriting, extract a compact "fact lock" from the draft and any user corrections:

- student name and current level
- main goal and near-term context
- roadmap periods and promised outcomes
- platforms and lesson format
- schedule, price, start date, if included
- sensitive facts to omit
- long-term goals that must be framed carefully

Never rewrite in a way that changes the fact lock. If a fact is unclear, keep it vague or ask the user.

### 2. Lock the Structure

Before rewriting, extract a compact "structure lock" from the draft:

- title
- heading order
- table placement and columns
- bullet-list placement
- closing block

Preserve the existing blocks, headings, order, and overall article architecture unless the user explicitly asks to restructure. Rewrite inside each block; do not merge, remove, rename, or reorder blocks by default.

If a heading is awkward, lightly improve the wording while keeping its meaning and position. If a block is weak, strengthen it in place.

### 3. Improve the Article

Rewrite the article so the student clearly sees:

- where they are now
- what their main obstacle is
- why the goal is realistic for them
- what will happen after 1 month, 3 months, and 6 months
- how lessons, Progress.me, content, grammar, and feedback fit together
- what life benefit the work creates
- what to do before the first lesson, if relevant

Keep the structure article-like, not report-like. Use short paragraphs, clear headings, and concrete student-facing language. The result should read like a skilled Russian rewrite/copy editor polished it for a 25-30-year-old audience: natural, alive, emotionally intelligent, and easy to send in a message or document.

### 4. Optional Gemini Rewrite Pass

If the user asks to use Gemini, or if a Gemini pass is already part of the local workflow, use Gemini only as an external editor, not as the final authority.

Preferred VPS command for the approved Pro-style pass when OpenRouter is configured:

```bash
ssh shorttalk-dev "/usr/local/bin/openrouter-gemini-chat-chain '<input-file>' --save-dir '<run-dir>'"
```

This wrapper imitates one continuous Gemini web chat: it sends the full growing `messages` history on every pass, including previous assistant responses. Use it instead of three independent `openrouter-pro-auth -p` calls.

The chat-chain wrapper defaults to `google/gemini-2.5-pro` for stable Pro-style rewriting. Use `-m google/gemini-3.1-pro-preview` only when the user explicitly wants the preview model or when a reasoning-model experiment is intended.

Older Gemini CLI command, if explicitly requested:

```bash
ssh shorttalk-dev "/usr/local/bin/gemini-auth -p '<prompt>' --model gemini-2.5-flash"
```

For long drafts, avoid fragile shell quoting. Write the source text to a temporary file on the VPS, then run the chat-chain wrapper with that file path.

If the user references the approved Pro-site result or asks to "закрепить тот вариант", read `references/pro-style-calibration.md` and use its staged chain:

1. exact big strict rewrite prompt from the Gemini window
2. exact "в стиле Венни Пака" calibration
3. combined exact anglicism calibration

For the first pass, do not send a short prompt. Use the exact large prompt from the reference and paste the full source article after `ИСХОДНЫЙ ТЕКСТ:`. The later passes should stay close to the exact user messages in the reference.

When testing the approved Gemini-window experience, run in faithful mode:

- no extra `system` prompt
- no extra follow-up text appended to passes 2 and 3
- preserve the whole chat history between passes
- do not manually rewrite the final model output
- save `pass1.md`, `pass2.md`, `pass3.md`, request messages, and usage logs for review

### 5. Create the HTML Deliverable

After the final Markdown article is accepted or ready to send, create a standalone HTML file. This is a formatting step only: do not rewrite the article text while converting it to HTML.

Use the bundled converter:

```bash
python scripts/roadmap_markdown_to_html.py '<final.md>' -o '<final.html>'
```

On the VPS, use:

```bash
roadmap-markdown-to-html '<run-dir>/final.md' -o '<run-dir>/final.html'
```

The HTML file should be self-contained with embedded CSS, readable on desktop and mobile, and suitable for sending to the student as the polished article. Preserve headings, paragraphs, bullet lists, and Markdown tables.

After Gemini returns a rewrite, run a final Codex editorial pass:

- verify every fact against the fact lock
- verify every heading and block against the structure lock
- remove overpromises
- restore any important nuance Gemini dropped
- tighten headings and paragraphs
- ensure the final article sounds natural in Russian

## Tone Rules

The final article must feel:

- warm, calm, and confident
- practical, not hype-heavy
- personal to the student
- motivating without pressure
- commercially strong because the value is obvious
- honest about effort, but clear that the path is manageable
- human and conversational, not institutional
- written in natural modern Russian for a 25-30-year-old audience

Use direct "ты" language. Prefer "мы сделаем путь понятным" over "это будет легко". Prefer concrete life changes over abstract claims.

## Human Rewrite Style

Rewrite like a strong Russian-language editor polishing a text for a smart 25-30-year-old student:

- use natural Russian phrasing, not literal AI-style constructions
- keep sentences smooth and readable
- make support feel specific, not generic
- avoid corporate, academic, bureaucratic, or overly glossy marketing tone
- avoid childish encouragement and loud hype
- keep the teacher's confidence, but make it feel calm and earned
- use phrases that reduce anxiety and create a sense of a manageable path
- keep the text easy to read on a phone

The rewrite should feel like: "I see your situation, here is the path, it is realistic, and you will not be alone in the process."

Use a controlled modern register. Phrases such as "классная база", "без перегруза", "вокруг твоей реальной жизни", and "живой разговор" are useful. Avoid excessive slang such as "на чиле", "анлокнуть", "смарт-система", "тулз", "шерь", and "коннектить" unless the user explicitly asks for a more slang-heavy register.

Anglicism rule: remove English-letter anglicisms that are not natural in Russian copy. Preserve required names and domain terms such as Progress.me, YouTube, shorts, reels, small talk, foreign company, A2, and B1. Cyrillic anglicisms such as фидбек, кейсы, спикинг, лексика, and контент may be used sparingly when they fit the teacher's voice.

Good examples:

- "Ты не начинаешь с нуля: у тебя уже есть база, понимание и конкретная цель."
- "Сейчас задача не в том, чтобы выучить весь английский заново, а в том, чтобы активировать то, что уже есть."
- "Через 6 месяцев у тебя будет B1, и на этой базе можно будет двигаться к foreign company."

## Required Content for Roadmap Articles

When rewriting an English consultation roadmap article, include or preserve these blocks unless the user asks otherwise:

1. Title with the student's main goal
2. Current point: level, strengths, obstacle
3. Main focus for the next months
4. Why the student can succeed
5. Roadmap: 1 month, 2-3 months, 3-6 months
6. How lessons will work
7. Progress.me or other platform, if confirmed
8. Content-based learning from videos/reels/YouTube, if relevant
9. Grammar framing: useful tool, not a source of pressure
10. Before-start actions, if relevant
11. Closing thought that summarizes the path

## Commercial Clarity

Make the value obvious by showing the student what they receive:

- a personalized path, not random lessons
- scenario practice for real life
- visible progress markers
- a platform with structured materials
- content connected to their interests
- teacher feedback and adaptation
- a clear route from current level to target level

Do not use pushy sales language. The article should sell through clarity, relief, and specificity.

## Anti-Patterns

- Do not invent facts, deadlines, prices, platforms, schedule, or outcomes.
- Do not change the article structure, heading order, tables, or block logic unless explicitly asked.
- Do not soften confirmed roadmap promises unless the user asks.
- Do not make vague claims like "you will become fluent" unless explicitly confirmed.
- Do not include sensitive personal details that are not useful for the student's learning plan.
- Do not mention "audio", "transcript", "analysis", "draft", "Gemini", "Codex", or "skill" in the student-facing article.
- Do not over-explain methodology in a way that sounds technical.
- Do not turn the article into a contract or formal report.
- Do not use shame, pressure, or fear as motivation.

## Output

By default, output only the rewritten article in Markdown.

If the user asks for editing context, add a short note before or after the article with:

- what was strengthened
- what facts were preserved
- what was intentionally omitted
