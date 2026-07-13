# study/ file formats

All tutor state lives in the vault `study/` directory. The tutor creates and
maintains these files itself; the user only reads them (e.g. in Obsidian).

## study/profile.md — who is studying and how they like it

Created from the onboarding interview. Example:

```markdown
# Профиль ученика

- **Цель:** сдать экзамен по статистике (университетский курс)
- **Дедлайн:** 2026-09-15
- **Бэкграунд:** математика на уровне 1 курса; Python — уверенно
- **Время занятий:** будни вечером, ~45 минут

## Предпочтения по стилю

- Методика по умолчанию (сквозной пример, «всё ли понятно», проверочные вопросы)
- Больше практики, меньше теории
```

## study/plan.md — topic plan with statuses

Statuses: ⬜ не начата · 📖 превью · 📝 конспект готов · ✅ отвечена как на экзамене

```markdown
# План подготовки: статистика

**Дедлайн:** 15.09.2026

| № | Тема | Статус | Последнее повторение |
|---|------|--------|----------------------|
| 1 | Описательные статистики | ✅ | 12.07 |
| 2 | Распределения | 📝 | 13.07 |
| 3 | Доверительные интервалы | ⬜ | — |

**Следующее занятие:** тема 3 — «Доверительные интервалы».
```

The `**Следующее занятие:**` line is REQUIRED — the morning briefing reads it.

## study/progress.md — lesson journal

Append one row per lesson:

```markdown
| Дата | Темы | Итог |
|------|------|------|
| 13.07 | 2 | Распределения: нормальное, биномиальное. 3/3 вопроса верно. Путал σ и σ² — повторить. |
```

## study/mistakes.md — mistake bank for spaced repetition

One bullet per real mistake, with date and topic:

```markdown
- [13.07, тема 2] Путал дисперсию и стандартное отклонение — σ² vs σ.
```

## study/конспекты/ (optional)

Per-topic notes if the user wants them; link from plan.md. Files under
`study/` get the `learning` memory type (slow Ebbinghaus decay) via
`.memory-config.json`.
