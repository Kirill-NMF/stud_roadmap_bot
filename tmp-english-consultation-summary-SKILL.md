---
name: english-consultation-summary
description: Workflow for creating English learning consultation summaries. Use when processing audio transcripts from introductory student meetings to generate a structured analysis, ask clarifying questions, and produce a highly motivational final summary in Russian.
license: Complete terms in LICENSE.txt
---

# English Consultation Summary Workflow

This skill defines the strict, two-step process for converting a student's introductory consultation audio transcript into a final, motivational summary document. It ensures all critical details are captured, interpreted correctly, and verified with the user before final generation.

## The Two-Step Process

You MUST follow this process exactly in order. Do not skip straight to the final summary.

### Step 1: Pre-generation Analysis & Clarification
1. Read the full audio transcript of the consultation.
2. Create an Analysis & Proposals document based on the `Analysis Template` below.
3. Send this document to the user via the `message` tool (using the `ask` type) to get their approval and clarifications on specific points.

### Step 2: Final Summary Generation
1. Wait for the user's answers to your clarifying questions.
2. Apply ALL of the user's corrections (what to keep, what to remove, what to change).
3. Generate the final summary document based on the `Final Summary Template` below.
4. Deliver the final markdown file to the user.

---

## Output Patterns & Templates

### Step 1: Analysis Template (Pre-generation)

ALWAYS use this exact structure when presenting the initial analysis to the user. It uses the "80% from audio, 20% interpretation" rule.

```markdown
# 📋 АНАЛИЗ АУДИО И ПРЕДЛОЖЕНИЯ ДЛЯ [ИМЯ] ([УРОВЕНЬ])

Я проанализировал транскрибирование консультации и создал предложения на основе 80% контента из аудио + 20% структурирования. Вот что я услышал:

---

## 🎯 РАЗДЕЛ: ТЕКУЩИЙ УРОВЕНЬ И ЦЕЛЬ

**ИЗ АУДИО (80%):**
- Текущий уровень: [Уровень из аудио, например, A1 с пробелами]
- Проблемы: [Основные боли студента]
- Цель: [Глобальная цель, например, путешествия или работа]
- Дедлайн: [Сроки, если есть]
- Контекст: [Профессия, интересы, бэкграунд]

**МОЕ ПРЕДЛОЖЕНИЕ (20% интерпретация):**
- Проблема 1: [Формулировка проблемы]
- Проблема 2: [Формулировка проблемы]
- Решение: [Краткое решение]

**ВОПРОСЫ ДЛЯ УТОЧНЕНИЯ:**
❓ **Вопрос 1:** [Спроси про сроки или уровни, например: "Ты сказал, что за месяц можно разговориться... Это правильно?"]
❓ **Вопрос 2:** [Спроси про специфичную деталь из контекста]

---

## 🚀 РАЗДЕЛ: ФИНАЛЬНЫЙ РЕЗУЛЬТАТ И СРОКИ

**ИЗ АУДИО (80%):**
- [Этапы и сроки, упомянутые в аудио]
- [Стоимость и варианты расписания]

**МОЕ ПРЕДЛОЖЕНИЕ (20% интерпретация):**
| Этап | Сроки | Результат | Формат |
|------|-------|-----------|--------|
| **Этап 1** | [Срок] | [Результат] | [Формат] |
| **Этап 2** | [Срок] | [Результат] | [Формат] |

**ВОПРОСЫ ДЛЯ УТОЧНЕНИЯ:**
❓ **Вопрос 3:** [Уточни варианты оплаты или расписания]
❓ **Вопрос 4:** [Уточни реалистичность сроков]

---

## 💡 РАЗДЕЛ: КАК МЫ БУДЕМ УЧИТЬСЯ

**ИЗ АУДИО (80%):**
- [Упоминания платформ, например Progress.me или Edvibe]
- [Типы домашки]

**МОЕ ПРЕДЛОЖЕНИЕ (20% интерпретация):**
[Структура урока по шагам]

**ВОПРОСЫ ДЛЯ УТОЧНЕНИЯ:**
❓ **Вопрос 5:** Ты упоминал про платформу. Это Progress.me или другая платформа?
❓ **Вопрос 6:** [Спроси про интеграцию интересов студента в контент]

---

## 📊 ИТОГОВЫЕ ВОПРОСЫ:

1. Все ли ключевые моменты из аудио я поймал?
2. Нужно ли что-то добавить или убрать?
3. Какие из моих предложений (вопросы 1-[N]) требуют твоего уточнения?

Жду твоих ответов перед финальной генерацией! 🚀
```

### Step 2: Final Summary Template

After receiving clarifications, generate the final document. ALWAYS use this structure and tone.

**Key Tone Rules:**
- Highly motivational and empathetic ("Ты начинаешь с нуля, и это нормально").
- Use bullet points with hyphens (`-`), NEVER asterisks (`*`).
- Address the student directly ("Привет, [Имя]!").
- Keep paragraphs short and punchy.

```markdown
# Твой путь к [Главная цель, например: свободному английскому для путешествий]

[Мотивирующий лид на 2-3 предложения с конкретными сроками. Пример: За месяц ты разговоришься. За три месяца будешь готов к любой ситуации. За полгода достигнешь уверенного A2.]

---

## Привет, [Имя]!

[2-3 абзаца эмпатии. Опиши их текущую ситуацию, боли и покажи, что их цель реальна.]

---

## Что ты получишь

- **[Выгода 1]** — [Конкретное описание, как изменится жизнь. Не абстрактные уроки, а реальные ситуации.]
- **[Выгода 2]** — [Описание]
- **[Выгода 3]** — [Описание]
- **[Выгода 4]** — [Описание]

---

## Твой путь к [Целевой уровень]

| **Период** | **Результат** | **Что происходит** |
| --- | --- | --- |
| **[Срок 1]** | [Краткий результат] | [Описание процесса, например: Первый месяц — это адаптация к тому, что урок только на английском, но потом всё становится проще.] |
| **[Срок 2]** | [Краткий результат] | [Описание процесса] |
| **[Срок 3]** | [Краткий результат] | [Описание процесса] |

---

## Как мы будем учиться

**Структура урока (60 минут):**

1. **Разговор и проверка контента**
  - Обсуждаем, как дела, что нового
  - Проверяем контент, который ты смотрел

2. **Стандартная разговорная программа на [Progress.me / Edvibe]**
  - Коммуникативные практики из платформы
  - Базовые практики
  - Объяснение слов по-английски

3. **Контент-практика**
  - Обсуждаем видео, которое ты смотрел
  - Отрабатываем слова и разговариваемся на основе контента

4. **Обратная связь**
  - Даю feedback по грамматике, произношению, интонации
  - Даю задание на контент (скидывать видео, которое тебе нравится)
  - Даю задание на [Progress.me / Edvibe]

**Самостоятельная работа дома:**

- **[Progress.me / Edvibe]:** Коммуникативные практики, грамматика, лексика, аудирование, говорение
- **Контент:** Смотреть видео на английском (YouTube, TikTok, Shorts)
- **Скидывание:** Скидывать контент мне перед уроком

---

## Платформа [Progress.me / Edvibe] — твой полный набор материалов

Это не просто приложение. Это полная система для обучения базовому разговорному английскому на уровне [Уровень].

**Что там есть:**

- **Коммуникативные практики:** Базовые разговорные практики, которые тебе реально нужны. Не все практики. Только те, которые ты будешь применять в речи.
- **Грамматика:** Основные грамматические конструкции (20%), которые тебе реально нужны. Не все правила. Только те, которые ты будешь применять в речи.
- **Лексика:** Слова с определениями, синонимами, примерами, картинками. Все структурировано.
- **Аудирование:** Видео, подкасты, диалоги с озвучкой. Ты слушаешь, понимаешь, повторяешь.
- **Говорение:** Упражнения для практики речи. Ты записываешь себя, слушаешь, исправляешь.
- **Озвучка:** Все материалы озвучены. Ты слышишь правильное произношение.

**Как это работает:**

1. Ты входишь в платформу
2. Выбираешь тему (коммуникативные практики, грамматика, лексика, аудирование, говорение)
3. Делаешь упражнения
4. Видишь результаты

---

## График и оплата

- **Формат:** [Индивидуальные занятия / 2 раза в неделю и т.д.]
- **Время:** [Согласованное время]
- **Гибкость:** Можно переносить занятия внутри недели
- **Варианты:** [Укажи цены, например: 12 000 ₽/месяц (1 раз в неделю) или 24 000 ₽/месяц (2 раза в неделю) — в полтора раза быстрее результаты]
- **Начало:** [Дата начала]

---

## Почему ты точно справишься

[Финальный мотивирующий абзац. Напомни главную цель студента.]

Давай начнем! 🚀
```

## Anti-Patterns (What NOT to do)

- **NEVER generate the final summary immediately.** Always send the Analysis & Proposals document first with clarifying questions.
- **NEVER break the lesson structure.** Ensure the platform (Progress.me/Edvibe) is explicitly integrated into both the "Структура урока" (Step 2 and Step 4) and the "Самостоятельная работа дома" sections.
- **NEVER use asterisks (`*`) for lists.** Only use hyphens (`-`).
- **NEVER hallucinate timelines.** Use standard timelines unless corrected by the user (1 month = adaptation/start speaking, 3 months = ready for travel/situations, 6 months = solid A1/A2, 1.5 years = confident A2/B1).
- **NEVER include internal technical details** like "MГИМО approach" or "thrown in the pond" stress metaphors unless explicitly approved. Keep it positive.

