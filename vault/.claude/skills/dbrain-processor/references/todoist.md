---
type: note
title: Todoist Integration
last_accessed: 2026-02-27
relevance: 0.1
tier: archive
---
# Todoist Integration

## Project Structure

| Project | ID | Notes |
|---------|-----|-------|
| Inbox | 6GppRGHVwV96Q637 | Default fallback |
| work | 6H452QVfQfXv3hxh | All work tasks |
| Поручения | 6fm4jHQ5pXj6JCr2 | Subproject of work — delegated tasks waiting for result |
| Недбайки | 6WcQmq7R888p62M7 | Family project with wife |
| Личное | 6fm4jvRJPg76C5MR | Personal tasks (not involving wife) |
| Еженедельный обзор | 6fjxRcj3w54m85vR | Legacy — do not use (replaced by bot processing) |

---

## Available MCP Tools

### Reading Tasks
- `get-overview` — all projects with hierarchy
- `find-tasks` — search by text, project, section
- `find-tasks-by-date` — tasks by date range

### Writing Tasks
- `add-tasks` — create new tasks
- `complete-tasks` — mark as done
- `update-tasks` — modify existing

---

## Pre-Creation Checklist

### 1. Check Workload (REQUIRED)

```
find-tasks-by-date:
  startDate: "today"
  daysCount: 7
  limit: 50
```

Build workload map. If day has 3+ tasks — shift to next free day.

### 2. Check Duplicates (REQUIRED)

```
find-tasks:
  searchText: "key words from new task"
```

If similar exists → mark as duplicate, don't create.

---

## Project Detection Rules

### work (6H452QVfQfXv3hxh)
Keywords: рабочий, коллеги, офис, встреча, Сальников, Абазов, Загребин, Яшков, Алабыжев, Назаренко, аналитика, ИИзация, стажёры, Loomni, AI Conductors, Гриша, OWE, Топливо, Т-Авто, Афиша, команда, подкаст, Этo считается

### Поручения — subproject of work (6fm4jHQ5pXj6JCr2)
Use this project when:
- Entry contains delegation pattern: "попросил X сделать", "поручил X", "ждать от X", "проверить что X сделал"
- The task is a WAITING task — you delegated and expect a result back
- Title style: "Ждать [результат] от [имя]" or "Проверить [задача] — [имя]"

### Недбайки (6WcQmq7R888p62M7)
Keywords: Галя, жена, вместе, семья, дом, квартира, ремонт, путешествие, отпуск вместе, покупки для дома, совместный

### Личное (6fm4jvRJPg76C5MR)
Keywords: досуг, здоровье, спорт, хобби, книга, кино, учёба, курс, личное развитие, вокал, мой отдых
Use when topic clearly doesn't involve wife and is personal to user.

### Inbox fallback (6GppRGHVwV96Q637)
Use when no clear project match. Better Inbox than wrong project.

### DO NOT USE
- Еженедельный обзор (6fjxRcj3w54m85vR) — replaced by bot weekly/daily processing

---

## Priority Rules

| Situation | Priority |
|-----------|----------|
| Work + client deadline / срочно / критично | p1 |
| Work + ONE Big Thing alignment / важно | p2 |
| Work + regular operations | p3 |
| Поручения (delegated, just a reminder) | p3 |
| Недбайки with deadline | p2 |
| Недбайки regular | p3 |
| Личное | p4 |

---

## Date Mapping

| Context | dueString |
|---------|-----------|
| Urgent | today / tomorrow |
| This week | **см. правило ниже — только конкретная dueDate** |
| Next week | next monday |
| Поручения (follow-up reminder) | in 3 days |
| Personal / strategic | in 7 days |
| Not specified | in 3 days |

### Russian → dueString

| Russian | dueString |
|---------|-----------|
| сегодня | today |
| завтра | tomorrow |
| в понедельник | monday |
| в пятницу | friday |
| на этой неделе | **см. правило ниже — только конкретная dueDate** |
| на следующей неделе | next monday |
| через неделю | in 7 days |

### «This week» / «на этой неделе» — обязательный выбор конкретного дня

ЗАПРЕЩЕНО использовать dueString: `friday`, `this week`, `saturday` или любой другой нечёткий вариант.

ОБЯЗАТЕЛЬНАЯ процедура при смысле «на этой неделе»:
1. Запустить `find-tasks-by-date` (startDate: today, daysCount: 7) — построить карту нагрузки
2. Выбрать ближайший день с менее чем 3 задачами (начиная с завтра или сегодня)
3. Использовать `dueDate: "YYYY-MM-DD"` (конкретная дата, не строка)
4. В отчёте указать: "назначено на [дата] (с учётом нагрузки)"

---

## Task Title Style

Direct, specific, actionable. User prefers: прямота, ясность, конкретика.

✅ Good:
- "Отправить презентацию Сальникову"
- "Ждать отчёт по разбивке планов от Наташи"
- "Обсудить с Галей подарок на день рождения"
- "Записаться к врачу"

❌ Bad:
- "Подумать о презентации"
- "Разобраться с командой"
- "Что-то по ИИзации"

---

## Workload Balancing

If target day has 3+ tasks:
1. Find next day with fewer than 3 tasks
2. Use that day instead
3. Mention in report: "сдвинуто на [day] (перегрузка)"

---

## Error Handling

CRITICAL: Never suggest "добавь вручную".

If `add-tasks` fails:
1. Include EXACT error message in report
2. Continue with next entry
3. Don't mark entry as processed

WRONG: "Не удалось добавить. Добавь вручную: Task title"
CORRECT: "Ошибка создания задачи: [exact error from MCP tool]"
