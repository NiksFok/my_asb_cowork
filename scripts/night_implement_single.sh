#!/bin/bash
# night_implement_single.sh — on-demand implementation of a single concept
# Usage: bash scripts/night_implement_single.sh NOTE_ID CONCEPT_FILE
# Returns: "DONE: ..." or "FAILED: ..." to stdout

NOTE_ID="${1:-}"
CONCEPT_FILE="${2:-}"

source "$(dirname "$0")/common.sh"
init

if [ -z "$NOTE_ID" ] || [ -z "$CONCEPT_FILE" ]; then
    echo "FAILED: не указаны аргументы (note_id и concept_file)"
    exit 0
fi

FULL_CONCEPT_PATH="$PROJECT_DIR/$CONCEPT_FILE"

if [ ! -f "$FULL_CONCEPT_PATH" ]; then
    echo "FAILED: файл концепта не найден: $CONCEPT_FILE"
    exit 0
fi

# Extract title and implementation spec from concept doc
TITLE=$(head -5 "$FULL_CONCEPT_PATH" | grep '^#' | sed 's/^# //' | head -1)

SPEC=$(python3 -c "
import pathlib, re
doc = pathlib.Path('$FULL_CONCEPT_PATH').read_text(encoding='utf-8')
m = re.search(r'## Как реализовать\n(.*?)(?=\n##|\Z)', doc, re.DOTALL)
spec = m.group(1).strip() if m else doc[:600]
print(spec[:800])
" 2>/dev/null || echo "")

ITEM_FILE=$(python3 -c "
import pathlib, re
doc = pathlib.Path('$FULL_CONCEPT_PATH').read_text(encoding='utf-8')
m = re.search(r'src/d_brain/\S+\.py', doc)
print(m.group(0) if m else '')
" 2>/dev/null || echo "")

cd "$PROJECT_DIR"

# ASB v3.0: self-coding turn runs on the persistent interactive session
# (subscription), not headless `claude -p`. The session's cwd is the vault, so
# the prompt uses ABSOLUTE project paths. wrap=True gives a clean reply (the
# echoed prompt — which itself contains "DONE:"/"FAILED:" lines — is excluded).
ABS_ITEM_FILE=""
[ -n "$ITEM_FILE" ] && ABS_ITEM_FILE="$PROJECT_DIR/$ITEM_FILE"
RESULT=$(printf '%s' "Отвечай исключительно на русском языке.

Implement this improvement to the d-brain Telegram bot project.
Project root (edit files here, use ABSOLUTE paths): $PROJECT_DIR

Concept document: $FULL_CONCEPT_PATH

Title: $TITLE
Target file (if identified): $ABS_ITEM_FILE
Implementation steps: $SPEC

RULES:
- Read the full concept document first for complete context
- Read the target file before making changes
- Make ONLY the specific change described
- Verify Python syntax after editing (cd $PROJECT_DIR first)
- If spec is too vague or change would break functionality → FAILED

Return EXACTLY ONE of:
DONE: одно предложение на русском, описывающее что изменено
FAILED: одно предложение на русском, объясняющее причину (конкретно)" \
    | uv run python -m d_brain.pipeline ask 2>/dev/null) || RESULT="FAILED: ошибка выполнения сессии"

RESULT_LINE=$(echo "$RESULT" | grep -E '^(DONE|FAILED):' | head -1)
if [ -z "$RESULT_LINE" ]; then
    RESULT_LINE="FAILED: нет результата от claude"
fi

# Output result to stdout for Python to read
echo "$RESULT_LINE"
