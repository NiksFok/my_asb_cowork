"""Button handlers for reply keyboard."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from d_brain.bot.keyboards import get_main_keyboard

router = Router(name="buttons")

# Every 🛑 stop label from every mode (study / do-conversation / agent session),
# plus /stop. Handled by a single state-INDEPENDENT router registered BEFORE the
# stateful handlers, so a 🛑 press always exits — even when the FSM state was
# wiped by a bot restart (MemoryStorage) or when a stop label from one mode is
# pressed while another mode's handler is active. Without this, e.g. "🛑
# Завершить учёбу" pressed inside a /do conversation is not in that handler's
# exit set and gets executed as a Claude prompt instead of ending the mode.
_ALL_EXIT_TRIGGERS = {
    "🛑 Завершить",
    "🛑 Завершить учёбу",
    "🛑 Завершить сессию",
    "/stop",
}

exit_router = Router(name="exit")


@exit_router.message(F.text.in_(_ALL_EXIT_TRIGGERS))
async def btn_exit_any(message: Message, state: FSMContext) -> None:
    """Exit any mode and return to the main menu, regardless of FSM state."""
    label = (message.text or "").strip()
    await state.clear()
    if label == "🛑 Завершить учёбу":
        text = "✅ Учёба завершена. Конспект и прогресс — в vault (study/)."
    else:
        text = "✅ Завершено. Возврат в главное меню."
    await message.answer(text, reply_markup=get_main_keyboard())


@router.message(F.text == "📅 Неделя")
async def btn_weekly(message: Message) -> None:
    """Handle Weekly button."""
    from d_brain.bot.handlers.weekly import cmd_weekly

    await cmd_weekly(message)


@router.message(F.text == "✨ Запрос")
async def btn_do(message: Message, state: FSMContext) -> None:
    """Handle Do button - open interactive Claude session."""
    from d_brain.bot.handlers.do import open_session

    await open_session(message, state)


@router.message(F.text == "🎓 Учёба")
async def btn_study(message: Message, state: FSMContext) -> None:
    """Handle Study button - enter the tutor dialogue."""
    from d_brain.bot.handlers.study import enter_study

    await enter_study(message, state)


@router.message(F.text == "✏️ Правки")
async def btn_edit(message: Message, state: FSMContext) -> None:
    """Handle Edit button - enter edit mode."""
    from d_brain.bot.handlers.edit import enter_edit_mode

    await enter_edit_mode(message, state)


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message) -> None:
    """Handle Help button."""
    from d_brain.bot.handlers.commands import cmd_help

    await cmd_help(message)


@router.message(F.text == "⚙️ Настройки")
async def btn_settings(message: Message) -> None:
    """Handle Settings button."""
    from d_brain.bot.handlers.commands import cmd_settings

    await cmd_settings(message)


@router.message(F.text == "🔧 Улучшить")
async def btn_improve(message: Message) -> None:
    """Handle Improve button - shortcut to /improve."""
    from d_brain.bot.handlers.improve import cmd_improve

    await cmd_improve(message)


