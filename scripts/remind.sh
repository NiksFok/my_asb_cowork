#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"
init

DAY_MONTH=$(LC_TIME=ru_RU.UTF-8 date +"%d %B" 2>/dev/null || date +"%d %B")

# ASB v3.0: the "fun fact about today" used headless `claude -p` (haiku) which
# would now bill against the Agent SDK credit. Generate it through the
# persistent session instead (subscription); fall back to a static nudge if the
# session is busy or slow.
FACT=$(printf '%s' "Назови ОДИН короткий интересный факт (история, наука или культура), связанный с календарной датой ${DAY_MONTH}. Ответь одним предложением на русском, только сам факт, без преамбулы и кавычек." \
    | (cd "$PROJECT_DIR" && uv run python -m d_brain.pipeline ask 2>>"$PROJECT_DIR/logs/pipeline-remind-$(date +%F).log")) || FACT=""
FACT=$(printf '%s' "$FACT" | tr -d '\r' | head -c 280)
if [ -z "${FACT// /}" ] || [ "${#FACT}" -lt 12 ]; then
    FACT="Каждый день — это новая возможность."
fi

MESSAGE="🕗 <b>Время рефлексии!</b>

📅 <i>${DAY_MONTH}:</i> ${FACT}

До подведения итогов осталось 3 часа (в 23:00).

Расскажи мне:
• Как прошёл день?
• Что сделал, что не успел?
• Какие мысли или идеи?
• Что чувствуешь?

Чем больше расскажешь — тем точнее будет итоговый отчёт и задачи на завтра 💬"

send_telegram "$MESSAGE"

echo "Reminder sent at $(date)"
