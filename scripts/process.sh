#!/bin/bash
set -e

source "$(dirname "$0")/common.sh"
init
init_mcp

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== d-brain processing for $TODAY ==="

# ── FREE RAM: stop bot during heavy processing ──
BOT_WAS_RUNNING=false
if systemctl is-active --quiet d-brain-bot; then
    BOT_WAS_RUNNING=true
    echo "=== Pausing bot to free RAM ==="
    sudo systemctl stop d-brain-bot || true
fi
# Ensure bot restarts even on failure
trap '
    if [ "$BOT_WAS_RUNNING" = true ]; then
        echo "=== Restarting bot ==="
        sudo systemctl start d-brain-bot || true
    fi
' EXIT

# Pull latest vault changes
echo "=== Pulling latest vault changes ==="
cd "$PROJECT_DIR"
git pull --rebase --autostash || echo "Git pull failed (non-critical)"

# ── ORIENT PHASE: pre-flight checks ──
DAILY_FILE="$VAULT_DIR/daily/$TODAY.md"
HANDOFF_FILE="$VAULT_DIR/.session/handoff.md"
GRAPH_FILE="$VAULT_DIR/.graph/vault-graph.json"
SESSION_DIR="$VAULT_DIR/.session"
mkdir -p "$SESSION_DIR"

# Check daily file exists and has content
if [ ! -f "$DAILY_FILE" ]; then
    echo "ORIENT: daily/$TODAY.md not found — creating empty file"
    echo "# $TODAY" > "$DAILY_FILE"
fi

DAILY_SIZE=$(wc -c < "$DAILY_FILE" 2>/dev/null || echo "0")
if [ "$DAILY_SIZE" -lt 50 ]; then
    echo "ORIENT: daily/$TODAY.md is empty ($DAILY_SIZE bytes) — skipping Claude processing"
    echo "ORIENT: Running graph rebuild only"

    # Still rebuild graph and commit
    cd "$VAULT_DIR"
    uv run .claude/skills/graph-builder/scripts/analyze.py || echo "Graph rebuild failed (non-critical)"
    uv run .claude/skills/agent-memory/scripts/memory-engine.py decay . || echo "Memory decay failed (non-critical)"
    cd "$PROJECT_DIR"

    git add vault/
    git commit -m "chore: process daily $TODAY" || true
    git push || true
    echo "=== Done (empty daily, graph-only) ==="
    exit 0
fi

# Check handoff exists
if [ ! -f "$HANDOFF_FILE" ]; then
    echo "ORIENT: handoff.md not found — creating stub"
    echo -e "---\nupdated: $(date -Iseconds)\n---\n\n## Last Session\n(none)\n\n## Observations" > "$HANDOFF_FILE"
fi

# Check graph freshness (warn if >7 days old)
if [ -f "$GRAPH_FILE" ]; then
    GRAPH_AGE=$(( ($(date +%s) - $(stat -c %Y "$GRAPH_FILE" 2>/dev/null || echo 0)) / 86400 ))
    if [ "$GRAPH_AGE" -gt 7 ]; then
        echo "ORIENT: vault-graph.json is $GRAPH_AGE days old (>7)"
    fi
fi

echo "ORIENT: daily=$DAILY_SIZE bytes, handoff=OK"
# ── END ORIENT PHASE ──

# Find yearly goals file (auto-detect year)
YEARLY_GOALS=$(ls "$VAULT_DIR/goals/1-yearly-"*.md 2>/dev/null | tail -1)
YEARLY_GOALS_NAME=$(basename "$YEARLY_GOALS" 2>/dev/null || echo "1-yearly.md")

# MCP prompt (needed for Phase 2)
MCP_PROMPT="CRITICAL: MCP loads in 10-30 seconds. You are NOT in subprocess — MCP IS running, just initializing.
Algorithm: 1) Call tool. 2) Error? Wait 10 sec. 3) Call again. 4) Wait 20 sec. 5) Call — GUARANTEED to work.
DO NOT say MCP unavailable. It is available. Just wait and call."

CAPTURE_FILE="$SESSION_DIR/capture.json"
EXECUTE_FILE="$SESSION_DIR/execute.json"

cd "$VAULT_DIR"

# ── ORIENT: Log Check ──
LOG_ERRORS=$(journalctl -u d-brain-bot -u d-brain-process -u d-brain-morning \
    --since "24 hours ago" --priority err --no-pager -q 2>/dev/null | tail -20) || LOG_ERRORS=""

# ── 3-PHASE PIPELINE ──
# Phase 1: CAPTURE (classify entries → JSON) — no MCP needed
# Phase 2: EXECUTE (create tasks, save thoughts → JSON) — with MCP/Todoist
# Phase 3: REFLECT (generate HTML report, update MEMORY → HTML) — no MCP needed
# Each phase = fresh Claude context for better quality.

# ── Phase 1: CAPTURE ──
echo "=== Phase 1: CAPTURE ==="
# ASB v3.0: phases run on the persistent interactive session (subscription),
# not headless `claude -p`. The prompt now lives in d_brain.pipeline.
CAPTURE=$(cd "$PROJECT_DIR" && TODAY="$TODAY" uv run python -m d_brain.pipeline capture 2>&1) || true

# Save raw output for debugging
echo "$CAPTURE" > "$SESSION_DIR/capture.raw.log"

# Extract JSON using robust parser
echo "$CAPTURE" | python3 "$SCRIPTS_DIR/extract_json.py" > "$CAPTURE_FILE" 2>/dev/null \
    || echo '{"error": "capture failed"}' > "$CAPTURE_FILE"

echo "Capture saved: $(wc -c < "$CAPTURE_FILE") bytes"

# Check if capture produced valid entries
if grep -q '"error"' "$CAPTURE_FILE"; then
    echo "WARN: Capture phase had issues, falling back to monolith mode"
    # Fallback to monolith processing (same as old process.sh)
    REPORT=$(cd "$PROJECT_DIR" && TODAY="$TODAY" uv run python -m d_brain.pipeline daily 2>>"$PROJECT_DIR/logs/pipeline-daily-$TODAY.log") || true
else
    # ── Phase 2: EXECUTE ──
    # Uses mcp-cli (Bash) for Todoist — no --mcp-config needed
    echo "=== Phase 2: EXECUTE ==="
    EXECUTE_OK=false
    for EXECUTE_ATTEMPT in 1 2; do
        EXECUTE=$(cd "$PROJECT_DIR" && TODAY="$TODAY" uv run python -m d_brain.pipeline execute 2>&1) || true

        # Save raw output for debugging
        echo "$EXECUTE" > "$SESSION_DIR/execute.raw.log"

        # Log full raw output if suspiciously short (raw_length < 10)
        EXECUTE_RAW_LEN=${#EXECUTE}
        if [ "$EXECUTE_RAW_LEN" -lt 10 ]; then
            echo "ERROR: Phase 2 raw output empty (len=$EXECUTE_RAW_LEN, attempt=$EXECUTE_ATTEMPT)" >&2
            mkdir -p "$PROJECT_DIR/logs"
            {
                echo "=== execute-error $TODAY attempt=$EXECUTE_ATTEMPT ==="
                echo "raw_length=$EXECUTE_RAW_LEN"
                printf '%s\n' "$EXECUTE"
            } >> "$PROJECT_DIR/logs/execute-error-$TODAY.log"
        fi

        # Extract JSON and validate before writing execute.json
        EXECUTE_TMP=$(echo "$EXECUTE" | python3 "$SCRIPTS_DIR/extract_json.py" 2>/dev/null) || EXECUTE_TMP=""
        if echo "$EXECUTE_TMP" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d and 'error' not in d else 1)" 2>/dev/null; then
            echo "$EXECUTE_TMP" > "$EXECUTE_FILE"
            EXECUTE_OK=true
            break
        else
            echo "WARN: Phase 2 attempt $EXECUTE_ATTEMPT returned invalid/empty JSON" >&2
            echo "$EXECUTE_TMP" > "$EXECUTE_FILE.err"
            echo '{"error": "execute failed"}' > "$EXECUTE_FILE"
        fi
    done

    if [ "$EXECUTE_OK" = false ]; then
        echo "ERROR: Phase 2 failed after all attempts — execute.json invalid" >&2
        exit 1
    fi

    echo "Execute saved: $(wc -c < "$EXECUTE_FILE") bytes"

    # ── Phase 3: REFLECT ──
    # -- Pre-REFLECT: Fetch Oura + nutrition context (d-doctor integration) --
    OURA_CONTEXT=""
    NUTRITION_CONTEXT=""
    if [ "${DDOCTOR_ENABLED:-false}" = "true" ]; then
        echo "=== Fetching Oura health context (d-doctor) ==="
        OURA_PROMPT="Today is $TODAY. Call ALL of the following Oura MCP tools and return a compact plain text summary in Russian (no HTML, no markdown):
1. get_daily_sleep — ночной сон: score, duration, efficiency, deep/REM/light breakdown
2. get_readiness — readiness score + key contributors (HRV balance, recovery index, body temperature)
3. get_stress — stress level timeline over the day, peak stress moments
4. get_heart_rate — resting HR, any notable spikes or drops
5. get_daily_activity — шаги, active calories, distance, movement goal progress
Format: one line per metric category, values in brackets. Example: Сон: [score=78] 7ч 20мин, эффективность 88%, глубокий 1ч 10мин. Keep each line concise."
        OURA_CONTEXT=$(claude --print --dangerously-skip-permissions --model claude-sonnet-4-6 --mcp-config "$PROJECT_DIR/mcp-config.json" -p "$OURA_PROMPT" 2>&1) || OURA_CONTEXT=""  # ALLOW-CLAUDE-P: d-doctor Oura, gated by DDOCTOR_ENABLED (default off); migrated in Phase 6
        # Strip any preamble Claude might add
        OURA_CONTEXT=$(echo "$OURA_CONTEXT" | grep -v '^$' | grep -v 'Here is\|Вот\|Let me\|I will\|Calling' | head -20 || echo "$OURA_CONTEXT")

        echo "=== Fetching nutrition context (d-doctor) ==="
        NUTRITION_CONTEXT=$(uv run python /home/niks/my_doctor/scripts/nutrition_context.py 2>/dev/null || echo "")
    fi

    HEALTH_SECTION=""
    if [ -n "$OURA_CONTEXT" ]; then
        HEALTH_SECTION="
OURA HEALTH DATA (полный день):
${OURA_CONTEXT}

Add a health section to the HTML report (after tasks section):
<b>🫀 Здоровье за день:</b>
Структура: 2-3 строки.
Строка 1: сон — одним предложением, только если что-то важное (плохой сон, рекорд, необычное).
Строка 2: активность + стресс — корреляция с событиями дня из capture.json. Когда был пик стресса и что происходило в это время?
Строка 3 (если нужна): один практичный вывод для завтра на основе данных.
Не перечисляй цифры — давай ВЫВОДЫ."
    fi

    NUTRITION_SECTION=""
    if [ -n "$NUTRITION_CONTEXT" ]; then
        NUTRITION_SECTION="
ПИТАНИЕ ЗА ДЕНЬ (данные из трекера):
${NUTRITION_CONTEXT}

Add a nutrition section to the HTML report (after health section):
<b>🍽 Питание за день:</b>
Итог: X/Y ккал (Z%). Белки/жиры/углеводы — выполнено или нет.
Один вывод — что хорошо, что изменить завтра.
Связь с здоровьем: если есть Oura данные — связать питание с энергией/стрессом сегодня."
    fi

    echo "=== Phase 3: REFLECT ==="
    # Hand the shell-computed dynamic context to the pipeline via .session files
    # (the static reflect prompt now lives in d_brain.pipeline).
    mkdir -p "$SESSION_DIR"
    printf '%s' "$LOG_ERRORS" > "$SESSION_DIR/log_errors.txt"
    printf '%s\n%s' "$HEALTH_SECTION" "$NUTRITION_SECTION" > "$SESSION_DIR/reflect_extra.md"
    REPORT=$(cd "$PROJECT_DIR" && TODAY="$TODAY" uv run python -m d_brain.pipeline reflect 2>>"$PROJECT_DIR/logs/pipeline-reflect-$TODAY.log") || true
fi

cd "$PROJECT_DIR"

# Validate the report is REAL, then one strict retry, then a minimal fallback.
# The model formats the evening report as emoji+text (same as the morning brief)
# OR as Telegram HTML — both render fine — so we must NOT require <b> tags (that
# rejected good reports and sent the fallback card). A report is "bad" only if
# it is too short to be a real report or is an ask() error sentinel.
_report_is_bad() {
    local r="$1"
    [ "${#r}" -lt 120 ] && return 0
    printf '%s' "$r" | head -1 | grep -qiE "session stalled|no reply in|session start failed|could not acquire|pane lock|rate_limited|logged_out|empty prompt|unknown command|pipeline .* failed" && return 0
    return 1
}
if _report_is_bad "$REPORT"; then
    echo "WARN: Phase 3 report empty/failed — retrying with strict prompt"
    REPORT=$(cd "$PROJECT_DIR" && TODAY="$TODAY" uv run python -m d_brain.pipeline reflect-strict 2>>"$PROJECT_DIR/logs/pipeline-reflect-$TODAY.log") || true
    if _report_is_bad "$REPORT"; then
        echo "WARN: Retry also failed — sending fallback minimal card"
        REPORT="📋 <b>D-Brain $TODAY</b>
⚠️ Не удалось собрать вечерний разбор. Полный лог в vault/daily/$TODAY.md"
    fi
fi

echo "=== Claude output ==="
echo "$REPORT"
echo "===================="

REPORT_CLEAN=$(clean_claude_output "$REPORT")

# Send to Telegram immediately after report is ready (before vault ops that may timeout)
send_telegram "$REPORT_CLEAN"

# Trim handoff.md — drops accumulated "## Last Session (предыдущая…)" sections
echo "=== Rotate handoff ==="
uv run python scripts/rotate_handoff.py || echo "Handoff rotation failed (non-critical)"

# Rebuild vault graph
echo "=== Rebuilding vault graph ==="
cd "$VAULT_DIR"
uv run .claude/skills/graph-builder/scripts/analyze.py || echo "Graph rebuild failed (non-critical)"

# Remove phantom wikilinks from ## Related sections (conservative: only drops
# links that resolve to no note — e.g. unfilled template placeholders, stale
# year-rollover targets; body links are never touched).
echo "=== Cleaning phantom links ==="
uv run .claude/skills/vault-health/scripts/link_cleanup.py . --apply || echo "Link cleanup failed (non-critical)"

# Memory decay (update relevance scores and tiers)
echo "=== Memory decay ==="
uv run .claude/skills/agent-memory/scripts/memory-engine.py decay . || echo "Memory decay failed (non-critical)"

# Regenerate MOC indexes (reflections, ideas, learnings, business, projects)
echo "=== Regenerating MOC ==="
uv run .claude/skills/vault-health/scripts/generate_moc.py || echo "MOC generation failed (non-critical)"
cd "$PROJECT_DIR"

# Git commit (only vault/)
git add vault/
git commit -m "chore: process daily $TODAY" || true
git push || true

echo "=== Done ==="
