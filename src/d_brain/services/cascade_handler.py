"""Cascade input router — interprets user response while a cascade
proposal is pending and routes it to apply/re-run/cancel.

Designed to be called from the top of voice/text handlers BEFORE the
regular reflection/daily flow runs.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

from aiogram import Bot

from d_brain.services.cascade import CascadeService
from d_brain.services.cascade_applier import CascadeApplier
from d_brain.services.git import VaultGit

logger = logging.getLogger(__name__)

Intent = Literal["accept", "cancel", "feedback", "none"]
Outcome = Literal["applied", "cancelled", "feedback_recorded", "not_active", "error"]


_ACCEPT_PATTERNS = (
    "годно", "ок ", " ок", "ок.", "ок!", "ок,",
    "принимаю", "принять", "согласен", "идёт", "идет",
    "подтверждаю", "подтверждено", "yes", "да, годно",
    "да!", "да.", "да ", "поехали", "применяй", "применить",
    "сохраняй", "сохранить", "пиши", "запиши",
)
_CANCEL_PATTERNS = (
    "отмена", "отменить", "отмени", "cancel",
    "не надо", "забей", "не сейчас", "пропусти",
)


def _classify(text: str) -> Intent:
    """Naive keyword-based intent detection. Good enough for short voice replies."""
    t = (text or "").strip().lower()
    if not t:
        return "none"

    if t in {"да", "ага", "угу", "ok", "ok!", "ok.", "ok,", "+", "👍"}:
        return "accept"
    if t in {"нет", "no", "-", "👎"}:
        return "cancel"

    for pat in _CANCEL_PATTERNS:
        if pat in t:
            return "cancel"
    for pat in _ACCEPT_PATTERNS:
        if pat in t:
            return "accept"

    if len(t) <= 200 and any(
        kw in t for kw in (
            "поменя", "замени", "убери", "удали", "добавь", "добавить",
            "вместо", "лучше", "правк", "измен", "не нравится",
            "переделай", "пересчитай", "уточни",
        )
    ):
        return "feedback"

    if len(t) > 30:
        return "feedback"

    return "none"


async def handle_cascade_reply(
    *, vault_path: Path, bot: Bot, user_id: int, text: str
) -> Outcome:
    """If cascade is pending, interpret `text` and act. Returns outcome."""
    cascade = CascadeService(vault_path)
    state = cascade.get()
    if state is None or state.get("stage") in {"applied", "cancelled"}:
        return "not_active"

    intent = _classify(text)
    if intent == "none":
        return "not_active"

    if intent == "accept":
        return await _apply(cascade, vault_path, bot, user_id)

    if intent == "cancel":
        cascade.clear()
        await bot.send_message(
            chat_id=user_id,
            text="🚫 Каскадное ревью отменено. Файлы целей не тронул.",
        )
        return "cancelled"

    cascade.record_feedback(text)
    await bot.send_message(
        chat_id=user_id,
        text="📝 Принял правки, пересчитываю предложение... это займёт минуту-две.",
    )
    asyncio.create_task(_rerun_cascade(vault_path))
    return "feedback_recorded"


async def _apply(
    cascade: CascadeService, vault_path: Path, bot: Bot, user_id: int
) -> Outcome:
    state = cascade.get()
    if state is None:
        return "error"
    proposal = state.get("proposal") or {}
    try:
        applier = CascadeApplier(vault_path)
        result = await asyncio.to_thread(applier.apply, proposal)
    except Exception as e:
        logger.exception("Cascade apply failed")
        await bot.send_message(
            chat_id=user_id,
            text=f"❌ Не смог применить предложение: <code>{str(e)[:300]}</code>",
        )
        return "error"

    cascade.mark_applied()
    cascade.clear()

    summary_lines = ["✅ <b>Каскад целей обновлён</b>", ""]
    w = result.get("weekly")
    if w:
        summary_lines.append(f"• Неделя: <code>{w.get('next_week_id')}</code> — записал в goals/3-weekly.md")
    m = result.get("monthly")
    if m:
        summary_lines.append(f"• Месяц: <code>{m.get('period')}</code> — записал в goals/2-monthly.md")
    y = result.get("yearly")
    if y:
        summary_lines.append(f"• Год: <code>{y.get('period')}</code> — записал в goals/1-yearly-{y.get('period')}.md")
    summary_lines.append("")
    summary_lines.append("Старые версии в goals/archive/. Запушу в git.")

    try:
        git = VaultGit(vault_path)
        await asyncio.to_thread(git.commit_and_push, "chore: cascade goals refresh")
    except Exception as e:
        logger.warning("Git commit failed: %s", e)
        summary_lines.append(f"<i>Git push не прошёл: {str(e)[:200]}</i>")

    await bot.send_message(chat_id=user_id, text="\n".join(summary_lines))
    return "applied"


async def _rerun_cascade(vault_path: Path) -> None:
    """Spawn cascade_review.py to re-run with the new feedback."""
    project_dir = vault_path.parent
    script = project_dir / "scripts" / "cascade_review.py"
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(script)],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except Exception:
        logger.exception("Cascade rerun failed")
