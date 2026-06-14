"""Handler for /do command and the "✨ Запрос" one-shot flow.

ASB v3.0 billing migration: the old per-user streaming sessions (headless
`claude --print --resume`) are gone. Every request now runs as a turn on the
shared persistent interactive tmux session via ``ClaudeProcessor`` (which calls
``ClaudeSession.ask``). There is no token stream, so the UX is a periodic
"⏳ выполняю… Ns" progress edit followed by the final reply.

After the first reply the chat stays in a conversation: because the brain is one
long-lived session that keeps its own context, follow-up messages continue the
same thread without pressing "✨ Запрос" again. The user leaves the conversation
with the 🛑 button (or any /command); outside it, plain text is saved to the
vault as before.
"""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.formatters import format_process_report
from d_brain.bot.keyboards import get_conversation_keyboard, get_main_keyboard
from d_brain.bot.states import DoCommandState
from d_brain.config import get_settings
from d_brain.services.runtime import get_processor
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="do")
logger = logging.getLogger(__name__)


# ─── Entry point ("✨ Запрос" button / cmd:do inline) ─────────────────────────

async def open_session(message: Message, state: FSMContext) -> None:
    """Enter the one-shot request flow: wait for the next text/voice message."""
    await state.set_state(DoCommandState.waiting_for_input)
    await message.answer(
        "🤖 <b>Запрос к Claude</b>\n\n"
        "Отправь команду — текстом или голосом. Отвечу одним сообщением.",
        reply_markup=get_main_keyboard(),
    )


# ─── Prompt extraction helper ────────────────────────────────────────────────

async def _extract_prompt(message: Message, bot: Bot) -> str | None:
    """Extract a text prompt from a voice or text message."""
    if message.voice:
        await message.chat.do(action="typing")
        settings = get_settings()
        transcriber = DeepgramTranscriber(settings.deepgram_api_key)
        try:
            file = await bot.get_file(message.voice.file_id)
            if not file.file_path:
                await message.answer("❌ Не удалось скачать голосовое")
                return None
            file_bytes = await bot.download_file(file.file_path)
            if not file_bytes:
                await message.answer("❌ Не удалось скачать голосовое")
                return None
            audio_bytes = file_bytes.read()
            prompt = await transcriber.transcribe(audio_bytes)
        except Exception as e:
            logger.exception("Failed to transcribe voice for /do")
            await message.answer(f"❌ Не удалось транскрибировать: {html.escape(str(e))}")
            return None
        if not prompt:
            await message.answer("❌ Не удалось распознать речь")
            return None
        await message.answer(f"🎤 <i>{html.escape(prompt)}</i>")
        return prompt

    if message.text:
        return message.text

    await message.answer("❌ Отправь текст или голосовое сообщение")
    return None


# ─── /do command ─────────────────────────────────────────────────────────────

@router.message(Command("do"))
async def cmd_do(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Handle /do: one-shot with args, otherwise wait for the next message."""
    user_id = message.from_user.id if message.from_user else 0
    if command.args:
        await process_request(message, command.args, user_id)
        await _enter_conversation(message, state)
        return
    await open_session(message, state)


@router.message(DoCommandState.waiting_for_input)
async def handle_do_input(message: Message, bot: Bot, state: FSMContext) -> None:
    """Handle the first voice/text request that follows /do or "✨ Запрос"."""
    prompt = await _extract_prompt(message, bot)
    if not prompt:
        return  # stay in waiting_for_input until a usable prompt arrives
    user_id = message.from_user.id if message.from_user else 0
    await process_request(message, prompt, user_id)
    await _enter_conversation(message, state)


async def process_request(message: Message, prompt: str, user_id: int = 0) -> None:
    """Run a one-shot Claude request on the shared persistent session."""
    status_msg = await message.answer("⏳ Выполняю...")
    processor = get_processor(get_settings())

    async def run_with_progress() -> dict:
        task = asyncio.create_task(
            asyncio.to_thread(processor.execute_prompt, prompt, user_id)
        )
        elapsed = 0
        while not task.done():
            await asyncio.sleep(30)
            elapsed += 30
            if not task.done():
                try:
                    await status_msg.edit_text(
                        f"⏳ Выполняю... ({elapsed // 60}m {elapsed % 60}s)"
                    )
                except Exception:
                    pass
        return await task

    report = await run_with_progress()
    formatted = format_process_report(report)
    try:
        await status_msg.edit_text(formatted)
    except Exception:
        await status_msg.edit_text(formatted, parse_mode=None)


# ─── Conversation continuation ───────────────────────────────────────────────

# Exit the conversation on the 🛑 button, its old label, or an explicit /stop.
_EXIT_TRIGGERS = {"🛑 Завершить", "🛑 Завершить сессию", "/stop"}


async def _enter_conversation(message: Message, state: FSMContext) -> None:
    """Keep the chat in the shared session so follow-ups continue the thread."""
    await state.set_state(DoCommandState.in_conversation)
    await message.answer(
        "💬 Диалог открыт — пиши или говори дальше, контекст сохраняется.\n"
        "🛑 Завершить, когда закончишь.",
        reply_markup=get_conversation_keyboard(),
    )


@router.message(DoCommandState.in_conversation)
async def handle_conversation(message: Message, bot: Bot, state: FSMContext) -> None:
    """Route follow-up turns to the same session until the user leaves it."""
    text = (message.text or "").strip()
    if text in _EXIT_TRIGGERS:
        await state.clear()
        await message.answer("✅ Диалог завершён.", reply_markup=get_main_keyboard())
        return
    # Any other /command means "leave the conversation and run that instead".
    if text.startswith("/"):
        await state.clear()
        await message.answer(
            "Вышел из диалога. Отправь команду ещё раз.",
            reply_markup=get_main_keyboard(),
        )
        return
    prompt = await _extract_prompt(message, bot)
    if not prompt:
        return  # stay in the conversation; nothing usable to forward
    user_id = message.from_user.id if message.from_user else 0
    await process_request(message, prompt, user_id)
    # State stays in_conversation; the persistent keyboard is already shown.
