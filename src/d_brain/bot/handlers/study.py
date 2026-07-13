"""Handler for study mode ("🎓 Учёба") — dialogue tutoring on a separate session.

The tutor runs in its OWN persistent tmux Claude session (see
``runtime.get_study_session``): a cheaper model (Sonnet by default), its own
persona (deploy/study-system.md + the study-tutor skill) and its own context,
so long lessons never pollute the second brain's conversation — and vice
versa. The tutor persists lesson progress into vault ``study/`` files, so a
session restart loses nothing; on the very first launch (no study/profile.md)
it interviews the user and creates the profile and plan itself.

The session starts lazily on the first press of the button (cold start up to
~1.5 min); the watchdog intentionally does not keep it warm.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.keyboards import get_main_keyboard, get_study_keyboard
from d_brain.bot.states import StudyState
from d_brain.config import get_settings
from d_brain.services.runtime import get_study_session

router = Router(name="study")
logger = logging.getLogger(__name__)

# Exit the lesson on the 🛑 button or an explicit /stop.
_EXIT_TRIGGERS = {"🛑 Завершить учёбу", "🛑 Завершить", "/stop"}

ASK_TIMEOUT = 300.0

_COLD_START_NOTE = "<i>Первый ответ после холодного старта может занять до ~1.5 минут.</i>"


async def enter_study(message: Message, state: FSMContext) -> None:
    """Enter study mode and warm the tutor session in the background."""
    settings = get_settings()
    await state.set_state(StudyState.in_lesson)
    if (settings.vault_path / "study" / "profile.md").exists():
        greeting = (
            "🎓 <b>Учебная сессия</b>\n\n"
            "Скажи, что разбираем — тему, вопрос или «продолжим по плану».\n"
            + _COLD_START_NOTE
        )
    else:
        greeting = (
            "🎓 <b>Учебная сессия</b>\n\n"
            "Похоже, мы занимаемся впервые! Расскажи, что хочешь выучить или к "
            "чему подготовиться — репетитор задаст несколько вопросов и составит "
            "план.\n" + _COLD_START_NOTE
        )
    await message.answer(greeting, reply_markup=get_study_keyboard())

    def _warm() -> None:
        try:
            get_study_session(get_settings()).ensure_session()
        except Exception:
            logger.exception("study session warm-up failed")

    # Fire-and-forget so the first real question lands on a ready pane.
    asyncio.get_running_loop().run_in_executor(None, _warm)


@router.message(Command("study"))
async def cmd_study(message: Message, state: FSMContext) -> None:
    """Handle /study: enter the tutor dialogue."""
    await enter_study(message, state)


@router.message(StudyState.in_lesson)
async def handle_lesson(message: Message, bot: Bot, state: FSMContext) -> None:
    """Route lesson turns to the study session until the user leaves."""
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS:
        await state.clear()
        await message.answer(
            "✅ Учёба завершена. Конспект и прогресс — в vault (study/).",
            reply_markup=get_main_keyboard(),
        )
        return
    # Any other /command means "leave the lesson and run that instead".
    if text.startswith("/"):
        await state.clear()
        await message.answer(
            "Вышел из учёбы. Отправь команду ещё раз.",
            reply_markup=get_main_keyboard(),
        )
        return

    from d_brain.bot.handlers.do import _extract_prompt

    prompt = await _extract_prompt(message, bot)
    if not prompt:
        return  # stay in the lesson; nothing usable to forward

    status_msg = await message.answer("⏳ Думаю...")
    session = get_study_session(get_settings())

    task = asyncio.create_task(asyncio.to_thread(session.ask, prompt, timeout=ASK_TIMEOUT))
    elapsed = 0
    while not task.done():
        await asyncio.sleep(30)
        elapsed += 30
        if not task.done():
            try:
                await status_msg.edit_text(f"⏳ Думаю... ({elapsed // 60}m {elapsed % 60}s)")
            except Exception:
                pass
    res = await task

    reply = (res.reply or "").strip()
    if not res.ok or not reply:
        detail = res.detail or res.status
        await status_msg.edit_text(f"❌ Учебная сессия не ответила: {detail}")
        return
    if len(reply) > 4000:
        reply = reply[:4000] + "…"
    try:
        await status_msg.edit_text(reply)
    except Exception:
        await status_msg.edit_text(reply, parse_mode=None)
    # State stays in_lesson; the persistent keyboard is already shown.
