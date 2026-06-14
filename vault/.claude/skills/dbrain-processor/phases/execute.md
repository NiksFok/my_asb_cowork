---
type: note
title: Phase 2: EXECUTE
last_accessed: 2026-03-02
relevance: 0.1
tier: archive
---
# Phase 2: EXECUTE

Read capture.json from Phase 1. Create Todoist tasks, save thoughts, build links.

## Input
- `.session/capture.json` — output from Phase 1

## Todoist via mcp-cli

**ALWAYS use mcp-cli for Todoist.** Call via Bash tool.

### Commands:

```bash
# Today's tasks (check workload)
mcp-cli call todoist find-tasks-by-date '{"startDate": "today"}'

# Tasks for 7 days
mcp-cli call todoist find-tasks-by-date '{"startDate": "today", "daysCount": 7}'

# Create task
mcp-cli call todoist add-tasks '{"tasks": [{"content": "Task", "dueString": "tomorrow", "priority": 2}]}'

# Find tasks by label
mcp-cli call todoist find-tasks '{"labels": ["process-goal"]}'

# Complete tasks
mcp-cli call todoist complete-tasks '{"ids": ["task_id"]}'
```

### Due dates:

**NEVER use vague dueString values like "this week", "next week", "на этой неделе", "на следующей неделе".**
**CRITICAL: `dueString: "this week"` is FORBIDDEN — Todoist converts it to Saturday, not Friday.**
Always compute a concrete `dueDate` in `YYYY-MM-DD` format based on today's actual date:
- "this week" / "на этой неделе" → nearest Friday (compute YYYY-MM-DD from today)
- "next week" / "на следующей неделе" → next Monday (compute YYYY-MM-DD from today)
- "this weekend" / "на выходных" → nearest Saturday (compute YYYY-MM-DD from today)

Always use `dueDate` (YYYY-MM-DD), never `dueString` for scheduling.

### Priorities:
- 1 = p1 (highest)
- 2 = p2 (high)
- 3 = p3 (medium)
- 4 = p4 (default)

## Task

### 1. Create Todoist tasks

For each entry with `classification: "task"`:

**Required fields for every task:**
1. `content` — concise task title
2. `projectId` — determine from context: work tasks → Work project, personal → Personal, etc. Use `find-projects` if unsure.
3. `dueDate` (YYYY-MM-DD) — always a concrete date, never vague strings like "this week"
4. `description` — include the user's original words verbatim from the voice/text entry

```bash
mcp-cli call todoist find-projects '{}'
mcp-cli call todoist add-tasks '{"tasks": [{"content": "...", "projectId": "...", "dueDate": "YYYY-MM-DD", "priority": N, "description": "Оригинальные слова пользователя..."}]}'
```

Record created task IDs.

### 2. Check process goals

```bash
mcp-cli call todoist find-tasks '{"labels": ["process-goal"]}'
```

If missing or stale → create from goals.

### 3. Save thoughts

For each entry with classification idea/reflection/learning/project:
- Create file in `thoughts/{category}/YYYY-MM-DD-slug.md`
- Include frontmatter with description field (retrieval filter, ~150 chars)
- Add wiki-links to related entities
- Add typed relationships in Related section:
  ```markdown
  ## Related
  - [[thoughts/ideas/some-note|Title]] — context: discussed during processing
  ```

### 4. Build links

For all created/updated files:
- Search for related notes in vault
- Add wiki-links with context phrases

### 5. Scan inbox tasks without project or deadline

```bash
# Step 1: get tasks without a due date (filter "no date & no project" is NOT supported by Todoist API)
mcp-cli call todoist find-tasks '{"filter": "!date"}'
```

Step 2 (in agent logic): from the returned list, keep only tasks where `project_id` is null or equals the Inbox project id.

For each found task:
- Determine the best project based on task content and recent context (last 2-3 days)
- Compute a concrete dueDate (YYYY-MM-DD)
- **Do NOT use `update-tasks`** — bug: ignores dueDate when other fields are present
- Complete the original task, then re-create via `add-tasks` with explicit `dueDate`:
```bash
mcp-cli call todoist complete-tasks '{"ids": ["<original_task_id>"]}'
mcp-cli call todoist add-tasks '{"tasks": [{"content": "...", "projectId": "...", "dueDate": "YYYY-MM-DD", "priority": N}]}'
```
- If context is insufficient → include in the report as requiring user clarification

### 6. Check workload

```bash
mcp-cli call todoist find-tasks-by-date '{"startDate": "today", "daysCount": 7}'
```

## mcp-cli retry algorithm

```
1. Call mcp-cli
2. Error? Wait 10 sec, read vault files
3. Call again
4. Error? Wait 20 sec
5. Third call — GUARANTEED to work
```

NEVER say "mcp-cli unavailable". Always retry 3x.

## Output Format

Print ONLY valid JSON:

```json
{
  "tasks_created": [
    {"id": "8501234567", "content": "Follow-up task", "priority": 2, "due": "tomorrow"}
  ],
  "thoughts_saved": [
    {"path": "thoughts/ideas/2026-03-02-layered-memory.md", "title": "AI agents need layered memory", "category": "ideas"}
  ],
  "links_created": [
    {"from": "thoughts/ideas/2026-03-02-layered-memory.md", "to": "goals/3-weekly.md", "context": "supports weekly focus"}
  ],
  "process_goals": {
    "active": 5,
    "overdue": 1,
    "created": 0
  },
  "workload": {
    "mon": 3, "tue": 2, "wed": 4, "thu": 1, "fri": 2, "sat": 0, "sun": 0
  },
  "observations": []
}
```
