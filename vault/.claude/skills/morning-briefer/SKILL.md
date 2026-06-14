---
type: note
description: Generates a personalized morning briefing with task recommendations based on Todoist tasks, recent reflections, goals, weather and AI news.
last_accessed: 2026-02-26
relevance: 0.1
tier: archive
name: morning-briefer
---

# Morning Briefer

Analyze context → generate actionable morning briefing → send as HTML to Telegram.

## CRITICAL: Output Format

**ALWAYS return RAW HTML. No markdown. Ever.**

Output goes directly to Telegram with `parse_mode=HTML`.
Allowed tags: `<b>`, `<i>`, `<code>`, newlines.
NEVER use: `**`, `##`, ` ``` `, `- **`, or any markdown.

## Input Context (provided in prompt)

- `=WEATHER=` — weather at user's current location (city name is included at the start of the line)
- `=AI_NEWS=` — raw headlines from HuggingFace, OpenAI, TechCrunch, VentureBeat
- `=TODAY=` — date and weekday
- Vault files: MEMORY.md, goals/3-weekly.md, goals/2-monthly.md, daily/*.md

## MCP Tools Required

- `mcp__todoist__find-tasks` — get all active tasks
- `mcp__todoist__find-tasks-by-date` — get tasks due today
- `mcp__todoist__get-task` — get task details (for description with responsible person)

## News Curation Rules

From the raw `=AI_NEWS=` headlines, select **2-3 most relevant** to user's interests:

### INCLUDE — user cares about:
- New AI models released (GPT, Claude, Gemini, Llama, Mistral, etc.)
- Benchmarks beaten or new leaderboard results
- Agent workflows, multi-agent systems, autonomous AI tools
- New tools/frameworks worth trying (LangChain, CrewAI, AutoGen, etc.)
- AI coding assistants, productivity AI
- Local AI / open source model updates
- Practical "how to" from HuggingFace blog

### EXCLUDE — user explicitly doesn't want:
- War, military, weapons
- Politics, elections, government
- Crypto, NFT, blockchain
- General business/funding news (unless directly AI-product relevant)
- Celebrity/entertainment
- Lawsuits, regulations (unless directly changes AI tool access)

### Format each news item as:
`• <b>[Source]</b> {short summary in Russian, 1 sentence} — <i>{why relevant to user}</i>`

If no clearly relevant news — write 1 item: `• Сегодня без громких AI-новостей — хороший день для глубокой работы.`

## Algorithm

1. **Read context** — MEMORY.md, goals/3-weekly.md, goals/2-monthly.md
2. **Read reflections** — daily/YYYY-MM-DD.md for last 2 days
3. **Get Todoist tasks** — find-tasks-by-date for today + overdue (find-tasks); **flag p1 tasks overdue >2 days** as `critical_overdue`
4. **Check upcoming 3 days** — scan tasks for keywords: «выступление», «презентация», «вебинар» → set flag `has_presentation`
5. **Curate news** — pick 2-3 relevant AI headlines from =AI_NEWS=
6. **Token monitoring** — read log files from `logs/` for the last 7 days; sum total tokens across sessions; check if any single session exceeded 100k tokens → set flag `token_heavy_session`; compute 7-day total → set `token_week_total`
7. **Поручения** — if today is an even day of month (day % 2 == 0): run `find-tasks` and scan each task's description for a pattern matching a responsible person (e.g. «Иван», «Маша», «Петров», «@username», or phrase «ответственный:»); keep only tasks that also have a due_date set → set `porucheniya` list
8. **Analyze** — what's urgent, what aligns with goals, what's unresolved
9. **Consider context** — weekday rhythm, weather energy impact
10. **Generate briefing** — see template below

## Weekday Logic

| Day | Recommended focus |
|-----|------------------|
| Mon | Planning week, important meetings, big tasks |
| Tue–Thu | Deep work, execution |
| Fri | Closing open loops, planning next week |
| Sat–Sun | Rest, reflection, creative work, personal projects |

## Output Template

<b>☀️ Доброе утро! {weekday}, {date}</b>

<b>🌤 {city from =WEATHER= line}:</b> {weather + 1 sentence energy tip based on weather}

<b>🤖 AI сегодня:</b>
• <b>[Source]</b> {summary} — <i>{why relevant}</i>
• <b>[Source]</b> {summary} — <i>{why relevant}</i>

<b>🎯 Фокус дня</b>
{1-2 sentences: what to concentrate on today, based on goals + weekday + reflections}

{IF critical_overdue}
<b>🚨 Критический долг</b>
• <b>[P1]</b> {task name} — просрочено {N} дн. → закрыть сегодня в {time slot, e.g. 10:00–11:30}

<b>✅ На сегодня ({count} задач)</b>
<b>Срочное:</b>
• {overdue/today p1-p2 tasks, max 3}

<b>Из целей:</b>
• {tasks aligned with weekly/monthly goals, max 2}

<i>⚠️ {count} просроченных задач — коротко что висит</i>

<b>💭 Из рефлексии</b>
{1-2 sentences: unresolved thoughts, what was on mind yesterday}

<b>💡 Совет дня</b>
{1 concrete actionable tip — specific, not generic}

{IF token_heavy_session OR token_week_total > 300k}
<b>⚡ Мониторинг токенов</b>
{IF token_heavy_session}• Вчера была тяжёлая dev-сессия ({max_session_tokens} токенов) — осторожнее с глубокими dev-сессиями.{ENDIF}
Расход за 7 дней: <b>{token_week_total}</b> токенов. {IF token_week_total > 300k}Темп высокий — стоит дробить задачи на меньшие сессии.{ENDIF}

{IF porucheniya}
<b>📋 Поручения на контроле</b>
{for each item in porucheniya}• <b>{responsible}</b> → {task name} → <i>до {due_date}</i>
{endfor}

{IF has_presentation}
<b>🎤 Скоро выступление — используй это!</b>
У тебя в ближайшие дни {презентация/выступление/вебинар}. Покажи аудитории живой кейс: как Claude Code + Obsidian автоматизируют твой рабочий день. Это сильнее любых слайдов — реальный workflow в реальном времени.

## Presentation Pattern

If tasks in the next 3 days contain any of: «выступление», «презентация», «вебинар» — include the `🎤 Скоро выступление` block in the briefing. Mention the specific event name from the task if available. This block goes after "Совет дня".

## Token Monitoring

Parse log files in `logs/` (e.g. `fetch_news.log`, `process_daily.log`, any `*.log`).
Look for lines containing token counts — common patterns: `tokens_used:`, `total_tokens`, `input_tokens`, `output_tokens`.
Sum all token values per file/session per day. A "session" = one log file run (identified by date prefix or timestamp block).

| Flag | Condition |
|------|-----------|
| `token_heavy_session` | any single session in last 7 days > 100 000 tokens |
| `token_week_total` | sum of all sessions over last 7 days |

Show the block only when `token_heavy_session` is true OR `token_week_total > 300 000`.
Signal message for heavy session: «осторожнее с глубокими dev-сессиями».

## Поручения на контроле

Run every even calendar day (day-of-month % 2 == 0).

Detection rules for responsible person in task description:
- Explicit prefix: «ответственный:», «ответственная:», «отв.:»
- @mention pattern: `@word`
- Standalone proper noun (capitalised word that is not the first word of a sentence) followed by comma or dash

Only include the task if **both** conditions hold:
1. Description contains a responsible person indicator
2. Task has `due_date` set (not null)

Output format per task: `• <b>{responsible}</b> → {task name} → <i>до {due_date}</i>`

Skip this block entirely on odd days or when `porucheniya` list is empty.

## Rules

- Max 5 tasks total in briefing
- Tone: friendly, direct, energetic — like a smart colleague
- No fluff — every sentence adds value
- Weather bad → suggest indoor focused work
- Weather good → suggest active breaks between work blocks
