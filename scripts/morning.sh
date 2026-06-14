#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

WEEKDAY=$(LC_TIME=ru_RU.UTF-8 date +%A 2>/dev/null || date +%A)

echo "=== Morning briefing for $TODAY ==="

# ── FREE RAM: stop bot during heavy processing ──
BOT_WAS_RUNNING=false
if systemctl is-active --quiet d-brain-bot; then
    BOT_WAS_RUNNING=true
    echo "=== Pausing bot to free RAM ==="
    sudo systemctl stop d-brain-bot || true
fi
trap '
    if [ "$BOT_WAS_RUNNING" = true ]; then
        echo "=== Restarting bot ==="
        sudo systemctl start d-brain-bot || true
    fi
' EXIT

# Fetch weather + news
CONTEXT=$(python3 "$PROJECT_DIR/scripts/fetch_context.py" 2>/dev/null) || CONTEXT="=WEATHER=\nнедоступно\n=AI_NEWS=\nнедоступно"
CURRENT_CITY="${LOCATION_CITY:-Москва}"
# Fetch full article content + summaries in background (completes during Claude run)
"$PROJECT_DIR/.venv/bin/python3" "$PROJECT_DIR/scripts/fetch_news_full.py" 2>>"$PROJECT_DIR/logs/fetch_news.log" &
NEWS_PID=$!

echo "=== Context fetched ==="
echo "$CONTEXT"

# Pull latest vault changes
echo "=== Pulling latest vault changes ==="
cd "$PROJECT_DIR"
git pull --rebase --autostash || echo "Git pull failed (non-critical)"

# Check git sync freshness
LAST_COMMIT_TS=$(git log -1 --format=%ct 2>/dev/null || echo 0)
NOW_TS=$(date +%s)
DIFF_H=$(( (NOW_TS - LAST_COMMIT_TS) / 3600 ))
GIT_SYNC_WARNING=""
if [ "$DIFF_H" -gt 24 ]; then
    DIFF_DAYS=$(( DIFF_H / 24 ))
    GIT_SYNC_WARNING="⚠️ Git sync: последний коммит ${DIFF_DAYS} дней назад"
fi

# ASB v3.0: briefing runs on the persistent interactive session (subscription),
# not headless `claude -p`. The shell hands weather/news context to the pipeline
# via .session/morning_context.txt; the prompt lives in d_brain.pipeline.
mkdir -p "$VAULT_DIR/.session"
printf '%s' "$CONTEXT" > "$VAULT_DIR/.session/morning_context.txt"

run_morning() {
    cd "$PROJECT_DIR" && TODAY="$TODAY" WEEKDAY="$WEEKDAY" \
        CURRENT_CITY="$CURRENT_CITY" LOCATION_TZ="${LOCATION_TZ:-Europe/Moscow}" \
        uv run python -m d_brain.pipeline morning 2>>"$PROJECT_DIR/logs/pipeline-morning-$TODAY.log"
}

# pipeline exits non-zero on a failed turn (e.g. a cold-pane stall, where the
# first prompt never woke the session). Capture the code instead of swallowing
# it, and retry once on a fresh invocation — the pane is awake by then so the
# re-ask lands. Only after a second failure do we fall back, so a raw status
# line like "session stalled" never reaches Telegram as the "briefing".
set +e
REPORT=$(run_morning); RC=$?
if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 60 ]; then
    echo "WARN: morning pipeline failed (rc=$RC, len=${#REPORT}) — retrying once"
    sleep 5
    REPORT=$(run_morning); RC=$?
fi
set -e
cd "$PROJECT_DIR"

if [ "$RC" -ne 0 ] || [ "${#REPORT}" -lt 60 ]; then
    echo "WARN: morning pipeline failed twice (rc=$RC) — sending fallback card"
    REPORT="☀️ <b>Доброе утро!</b>
<i>Утренний брифинг временно недоступен — сессия не ответила. Нажми «✨ Запрос», и я соберу разбор.</i>"
fi

echo "=== Claude output ==="
echo "$REPORT"

wait $NEWS_PID 2>/dev/null || true  # ensure news fetch is done before git commit
REPORT_CLEAN=$(clean_claude_output "$REPORT")
send_telegram "$REPORT_CLEAN"
if [ -n "$GIT_SYNC_WARNING" ]; then
    send_telegram "$GIT_SYNC_WARNING"
fi

# Send news button (separate message so user can open /news in one tap)
send_telegram_button "📰 Утренние новости готовы" "📰 Открыть новости" "cmd:news"

# Sync vault to git (for Obsidian)
git add vault/ && git commit -m "chore: morning briefing $TODAY" || true
git push || echo "Git push failed (non-critical)"

echo "=== Morning briefing done ==="
