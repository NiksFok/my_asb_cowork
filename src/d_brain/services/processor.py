"""Claude processing service.

ASB v3.0 billing migration: every model turn now goes through the shared
persistent interactive tmux session (``ClaudeSession.ask``) instead of a
headless ``claude --print`` subprocess — interactive usage stays on the
subscription. The public return shape ``{report|error, processed_entries}`` is
preserved so the calling handlers (do/edit/fix/process/weekly) need no change.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from d_brain.services.session import SessionStore

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 1200  # 20 minutes

# Human-readable Russian status when the session can't answer.
_STATUS_MESSAGES = {
    "rate_limited": "Достигнут лимит подписки Claude — попробуй позже.",
    "logged_out": "Claude Code разлогинен на сервере — нужно заново войти (claude login).",
    "timeout": "Claude не ответил вовремя (таймаут).",
    "error": "Ошибка сессии Claude.",
}


class ClaudeProcessor:
    """Service for triggering Claude Code processing via the shared session."""

    def __init__(
        self,
        vault_path: Path,
        todoist_api_key: str = "",
        *,
        session: Any = None,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.todoist_api_key = todoist_api_key
        self._mcp_config_path = (self.vault_path.parent / "mcp-config.json").resolve()
        # May be None when constructed via the legacy positional ctor
        # (ClaudeProcessor(vault, key)); resolved lazily in _ask().
        self._session = session

    def _get_session(self) -> Any:
        if self._session is None:
            from d_brain.config import get_settings
            from d_brain.services.runtime import get_session

            self._session = get_session(get_settings())
        return self._session

    def _ask(
        self,
        prompt: str,
        *,
        wrap: bool = True,
        request_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a prompt to the persistent session; map AskResult → report dict."""
        try:
            session = self._get_session()
            res = session.ask(
                prompt, timeout=timeout, request_id=request_id, wrap=wrap
            )
        except Exception as e:  # noqa: BLE001 — must never crash the caller
            logger.exception("session ask failed")
            return {"error": str(e), "processed_entries": 0}

        if res.ok:
            return {
                "report": self._clean_output(res.reply or ""),
                "processed_entries": 1,
            }
        logger.error("session ask non-ok: %s %s", res.status, res.detail)
        msg = _STATUS_MESSAGES.get(res.status, res.detail or "Ошибка обработки")
        return {"error": msg, "processed_entries": 0}

    def _load_skill_content(self) -> str:
        """Load dbrain-processor skill content for inclusion in prompt.

        NOTE: @vault/ references don't work in --print mode,
        so we must include skill content directly in the prompt.
        """
        skill_path = self.vault_path / ".claude/skills/dbrain-processor/SKILL.md"
        if skill_path.exists():
            return skill_path.read_text()
        return ""

    def _load_todoist_reference(self) -> str:
        """Load Todoist reference for inclusion in prompt."""
        ref_path = self.vault_path / ".claude/skills/dbrain-processor/references/todoist.md"
        if ref_path.exists():
            return ref_path.read_text()
        return ""

    def _get_session_context(self, user_id: int) -> str:
        """Get today's session context for Claude.

        Args:
            user_id: Telegram user ID

        Returns:
            Recent session entries formatted for inclusion in prompt.
        """
        if user_id == 0:
            return ""

        session = SessionStore(self.vault_path)
        today_entries = session.get_today(user_id)
        if not today_entries:
            return ""

        lines = ["=== TODAY'S SESSION ==="]
        for entry in today_entries[-10:]:
            ts = entry.get("ts", "")[11:16]  # HH:MM from ISO
            entry_type = entry.get("type", "unknown")
            text = entry.get("text", "")[:80]
            if text:
                lines.append(f"{ts} [{entry_type}] {text}")
        lines.append("=== END SESSION ===\n")
        return "\n".join(lines)

    def _clean_output(self, output: str) -> str:
        """Strip Claude artifacts: '---' wrappers and known preamble phrases.

        Claude sometimes wraps its HTML output like:
            HTML для Telegram
            ---
            <actual content>
            ---
            Готовые HTML для вставки в Телеграм

        Or adds a preamble like:
            Теперь генерируя финальный HTML отчет
            <actual content>
        """
        import re

        text = output.strip()

        # Strip --- separator wrapper: take content between first and last ---
        if "\n---\n" in text or text.startswith("---\n"):
            lines = text.split("\n")
            sep_indices = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
            if len(sep_indices) >= 2:
                text = "\n".join(lines[sep_indices[0] + 1 : sep_indices[-1]]).strip()
            elif len(sep_indices) == 1:
                idx = sep_indices[0]
                before = "\n".join(lines[:idx]).strip()
                after = "\n".join(lines[idx + 1 :]).strip()
                if re.match(r"^[📅📊✅❌<🧠📝✨💡🎯🪞]", before):
                    # before IS the HTML content — discard Claude commentary after ---
                    text = before
                else:
                    # before is preamble — use what's after ---
                    text = after

        # Strip known preamble lines (Claude commentary before the actual HTML)
        preamble_patterns = [
            r"^Теперь генерирую финальный HTML[ -]отчёт[.:\s]*",
            r"^Теперь генерирую финальный HTML[ -]отчет[.:\s]*",
            r"^Теперь генерируя финальный HTML[ -]отчёт[.:\s]*",
            r"^Теперь генерируя финальный HTML[ -]отчет[.:\s]*",
            r"^HTML для Telegram[:\s]*",
            r"^Вот HTML для Telegram[:\s]*",
            r"^Вот сырой HTML для Telegram[:\s]*",
            r"^Вот готовый HTML[:\s]*",
            r"^Готовые HTML для вставки в Телеграм[:\s]*",
            r"^HTML отчёт[:\s]*",
            r"^HTML отчет[:\s]*",
        ]
        for pattern in preamble_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

        return text

    def _html_to_markdown(self, html: str) -> str:
        """Convert Telegram HTML to Obsidian Markdown."""
        import re

        text = html
        # <b>text</b> → **text**
        text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
        # <i>text</i> → *text*
        text = re.sub(r"<i>(.*?)</i>", r"*\1*", text)
        # <code>text</code> → `text`
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
        # <s>text</s> → ~~text~~
        text = re.sub(r"<s>(.*?)</s>", r"~~\1~~", text)
        # Remove <u> (no Markdown equivalent, just keep text)
        text = re.sub(r"</?u>", "", text)
        # <a href="url">text</a> → [text](url)
        text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](\1)", text)

        return text

    def _save_weekly_summary(self, report_html: str, week_date: date) -> Path:
        """Save weekly summary to vault/summaries/YYYY-WXX-summary.md."""
        # Calculate ISO week number
        year, week, _ = week_date.isocalendar()
        filename = f"{year}-W{week:02d}-summary.md"
        summary_path = self.vault_path / "summaries" / filename

        # Convert HTML to Markdown for Obsidian
        content = self._html_to_markdown(report_html)

        # Add frontmatter
        frontmatter = f"""---
date: {week_date.isoformat()}
type: weekly-summary
week: {year}-W{week:02d}
---

"""
        summary_path.write_text(frontmatter + content)
        logger.info("Weekly summary saved to %s", summary_path)
        return summary_path

    def _update_weekly_moc(self, summary_path: Path) -> None:
        """Add link to new summary in MOC-weekly.md."""
        moc_path = self.vault_path / "MOC" / "MOC-weekly.md"
        if moc_path.exists():
            content = moc_path.read_text()
            link = f"- [[summaries/{summary_path.name}|{summary_path.stem}]]"
            # Insert after "## Previous Weeks" if not already there
            if summary_path.stem not in content:
                content = content.replace(
                    "## Previous Weeks\n",
                    f"## Previous Weeks\n\n{link}\n",
                )
                moc_path.write_text(content)
                logger.info("Updated MOC-weekly.md with link to %s", summary_path.stem)

    def process_daily(self, day: date | None = None) -> dict[str, Any]:
        """Process daily file with Claude.

        Args:
            day: Date to process (default: today)

        Returns:
            Processing report as dict
        """
        if day is None:
            day = date.today()

        daily_file = self.vault_path / "daily" / f"{day.isoformat()}.md"

        if not daily_file.exists():
            logger.warning("No daily file for %s", day)
            return {
                "error": f"No daily file for {day}",
                "processed_entries": 0,
            }

        # Load skill content directly (@ references don't work in --print mode)
        skill_content = self._load_skill_content()

        prompt = f"""Сегодня {day}. Выполни ежедневную обработку.

=== SKILL INSTRUCTIONS ===
{skill_content}
=== END SKILL ===

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Для задач: вызови mcp__todoist__add-tasks tool
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ## , no ```, no tables
- Start directly with 📊 <b>Обработка за {day}</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- If entries already processed, return status report in same HTML format"""

        return self._ask(prompt, wrap=True, request_id=f"maint-daily-{day.isoformat()}")

    def execute_prompt(self, user_prompt: str, user_id: int = 0) -> dict[str, Any]:
        """Execute arbitrary prompt with Claude.

        Args:
            user_prompt: User's natural language request
            user_id: Telegram user ID for session context

        Returns:
            Execution report as dict
        """
        today = date.today()

        # Load context
        todoist_ref = self._load_todoist_reference()
        session_context = self._get_session_context(user_id)

        prompt = f"""Ты - персональный ассистент d-brain.

CONTEXT:
- Текущая дата: {today}
- Vault path: {self.vault_path}

{session_context}=== TODOIST REFERENCE ===
{todoist_ref}
=== END REFERENCE ===

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

USER REQUEST:
{user_prompt}

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ##, no ```, no tables, no -
- Start with emoji and <b>header</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- Be concise - Telegram has 4096 char limit

EXECUTION:
1. Analyze the request
2. Call MCP tools directly (mcp__todoist__*, read/write files)
3. Return HTML status report with results"""

        # Live user request (/do) — NOT a maintenance turn, so it is a valid
        # steering target if the user sends a follow-up mid-answer.
        return self._ask(prompt, wrap=True, request_id=f"do-{user_id or 'anon'}")

    def generate_weekly(self) -> dict[str, Any]:
        """Generate weekly digest with Claude.

        Returns:
            Weekly digest report as dict
        """
        today = date.today()

        prompt = f"""Сегодня {today}. Сгенерируй недельный дайджест.

ПЕРВЫМ ДЕЛОМ: вызови mcp__todoist__user-info чтобы убедиться что MCP работает.

CRITICAL MCP RULE:
- ТЫ ИМЕЕШЬ ДОСТУП к mcp__todoist__* tools — ВЫЗЫВАЙ ИХ НАПРЯМУЮ
- НИКОГДА не пиши "MCP недоступен" или "добавь вручную"
- Для выполненных задач: вызови mcp__todoist__find-completed-tasks tool
- Если tool вернул ошибку — покажи ТОЧНУЮ ошибку в отчёте

WORKFLOW:
1. Собери данные за неделю (daily файлы в vault/daily/, completed tasks через MCP)
2. Проанализируй прогресс по целям (goals/3-weekly.md)
3. Определи победы и вызовы
4. Сгенерируй HTML отчёт

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- NO markdown: no **, no ##, no ```, no tables
- Start with 📅 <b>Недельный дайджест</b>
- Allowed tags: <b>, <i>, <code>, <s>, <u>
- Be concise - Telegram has 4096 char limit"""

        result = self._ask(prompt, wrap=True, request_id="maint-weekly")
        if result.get("report"):
            # Save to summaries/ and update MOC
            try:
                summary_path = self._save_weekly_summary(result["report"], today)
                self._update_weekly_moc(summary_path)
            except Exception as e:
                logger.warning("Failed to save weekly summary: %s", e)
        return result
