# Approved Pro-Style Rewrite Calibration

Use this reference when the user wants to reproduce the approved Gemini Pro website rewrite style for Russian student-facing English roadmap articles.

## Preferred Model Route

When available, run the Pro-style pass through the VPS OpenRouter chat-chain wrapper:

```bash
ssh shorttalk-dev "/usr/local/bin/openrouter-gemini-chat-chain '<input-file>' --save-dir '<run-dir>'"
```

Default model: `google/gemini-2.5-pro`.

Preview model, only when explicitly requested: `google/gemini-3.1-pro-preview`.

Use the Pro model for the staged rewrite chain whenever the user asks for the approved Gemini Pro website result. Use Flash only for quick tests or when Pro is unavailable.

The chat-chain wrapper imitates one Gemini web chat by sending the full accumulated `messages` array on every pass:

```text
user: pass 1 prompt + source article
assistant: pass 1 answer
user: pass 2 prompt
assistant: pass 2 answer
user: pass 3 prompt
assistant: final answer
```

For faithful Gemini-window testing, do not add a `system` prompt and do not append extra instructions to passes 2 and 3. For a later production-stability mode, an optional system prompt can be added, but that is not a one-to-one imitation of the website experience.

## Target Result

The target style is:

- noticeably more alive than the initial roadmap draft
- warm and supportive, but not sentimental
- conversational Russian for a 25-30-year-old student
- friendly and a little modern, without becoming teenage slang
- commercially appealing through confidence, specificity, and relief
- structured exactly like the source article
- fact-preserving

The ideal output feels like a confident teacher writing directly to the student after a consultation: "I see your situation, you already have a base, here is the realistic path, and I will help you move through it."

## Exact Working Chain From Gemini Window

Source file used for calibration:
`C:\Users\bests\.codex\attachments\d77960c7-3566-4f03-9331-c5a04475c082\pasted-text.txt`

The approved Gemini Pro result came from this chain. Reuse these prompts as literally as possible.

### Pass 1: Big Strict Rewrite Prompt

Use this exact first-pass prompt header. After `ИСХОДНЫЙ ТЕКСТ:`, paste the full roadmap article from the previous stage.

```text
Ты русский редактор и рерайтер student-facing текстов для аудитории 25–30 лет.

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
[paste full source article]
```

### Pass 2: Venny Pack Style Calibration

```text
Перепиши это также, только давай в стиле Венни Пака.
```

Use this as a style direction, not as permission to break facts or structure. If the model becomes too slang-heavy, continue with the anglicism passes below.

### Pass 3: Anglicism Calibration

```text
Просто идеально. Только я хочу, чтобы ты использовал чуть меньше англицизмов. Это значит то, что убери англицизмы, которые не свойственны русской речи, которые обычно в текстах пишутся английскими буквами. Те, что пишутся русскими буквами, можно оставить из предыдущего текста.
```

This pass means: remove awkward English-letter words that are not natural in Russian copy, but keep natural Cyrillic borrowings from the previous version.

## Style Dial

Aim for this balance:

- "классная база", "живой разговор", "без перегруза", "не с нуля", "вокруг твоей реальной жизни" are good.
- "на чиле", "анлокнуть", "смарт-система", "тулз", "коннектить", "шерь", "нейтивы" are too slang-heavy unless the user explicitly wants that register.
- English-letter anglicisms should be limited to terms that are natural or required in the domain: Progress.me, YouTube, shorts, reels, small talk, foreign company.
- Cyrillic anglicisms may be used sparingly if they sound natural for the teacher's audience: фидбек, кейсы, спикинг, лексика, контент. Avoid stacking too many in one paragraph.

## Final Acceptance Checklist

Before returning the final rewrite, verify:

- same headings and same order
- same table position and same columns
- same factual roadmap
- no new guarantees
- B1 after 6 months if confirmed in the source
- Progress.me preserved if present
- sensitive details omitted
- tone feels human, current, and supportive
- slang is controlled, not excessive
- English-letter anglicisms are not overused
