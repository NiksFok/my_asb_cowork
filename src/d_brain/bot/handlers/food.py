"""Food logging session handler.

Flow:
  [🍽 Еда] or /food  → enter FoodState.collecting
                      → show food keyboard (✅ Записал всё / ❌ Отмена)
  photos/voice/text   → buffer file_ids and texts, confirm each message
  [✅ Записал всё]    → process session → show КБЖУ analysis
  [❌ Отмена]         → cancel session → return to main keyboard
  10-min timeout      → auto-process whatever was collected
  /weight <value>     → log body weight (available anywhere in food mode or as standalone command)
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from d_brain.bot.keyboards import get_food_keyboard, get_main_keyboard, get_meal_done_keyboard
from d_brain.bot.states import FoodState
from d_brain.config import get_settings
from d_brain.services.git import VaultGit
from d_brain.services.session import SessionStore
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="food")
logger = logging.getLogger(__name__)

_FOOD_TIMEOUT_SECS = 600  # 10 minutes
_timeout_tasks: dict[int, asyncio.Task[None]] = {}

_MEAL_TYPE_EMOJI = {
    "завтрак": "🌅",
    "обед": "☀️",
    "ужин": "🌙",
    "перекус": "🍎",
}


# ────────────────────────── entry points ──────────────────────────

async def enter_food_mode(message: Message, state: FSMContext) -> None:
    """Start a food logging session."""
    if not message.from_user:
        return
    settings = get_settings()
    if not settings.nutrition_enabled:
        await message.answer("🍽 Нутрициолог отключён. Включи в /settings.")
        return
    await state.set_state(FoodState.collecting)
    await state.update_data(file_ids=[], texts=[], msg_count=0)
    _schedule_timeout(message.from_user.id, state, message.bot)
    await message.answer(
        "🍽 <b>Режим записи еды</b>\n\n"
        "Отправляй фото, голосовые или текст — всё, что ел.\n"
        "Когда закончишь — нажми <b>✅ Записал всё</b>.",
        reply_markup=get_food_keyboard(),
    )


@router.message(Command("food"))
async def cmd_food(message: Message, state: FSMContext) -> None:
    await enter_food_mode(message, state)


# ────────────────────────── message collection ──────────────────────────

@router.message(FoodState.collecting, lambda m: m.photo is not None)
async def food_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Buffer a photo during food session."""
    if not message.photo or not message.from_user:
        return
    data = await state.get_data()
    file_ids: list[dict] = data.get("file_ids", [])
    # Store the largest photo variant file_id
    file_ids.append({"type": "photo", "file_id": message.photo[-1].file_id})
    msg_count = data.get("msg_count", 0) + 1
    await state.update_data(file_ids=file_ids, msg_count=msg_count)
    _reschedule_timeout(message.from_user.id, state, bot)
    await message.answer(f"📷 Фото добавлено ({msg_count} шт. в сессии)")


@router.message(FoodState.collecting, lambda m: m.voice is not None)
async def food_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    """Transcribe and buffer a voice message during food session."""
    if not message.voice or not message.from_user:
        return
    await message.chat.do(action="typing")
    settings = get_settings()
    transcriber = DeepgramTranscriber(settings.deepgram_api_key)
    try:
        file = await bot.get_file(message.voice.file_id)
        if not file.file_path:
            await message.answer("Не удалось скачать голосовое")
            return
        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await message.answer("Не удалось скачать голосовое")
            return
        transcript = await transcriber.transcribe(file_bytes.read())
        if not transcript:
            await message.answer("Не удалось распознать речь")
            return
    except Exception as e:
        logger.exception("Voice transcription error in food mode")
        await message.answer(f"Ошибка транскрипции: {e}")
        return

    data = await state.get_data()
    texts: list[str] = data.get("texts", [])
    texts.append(transcript)
    msg_count = data.get("msg_count", 0) + 1
    await state.update_data(texts=texts, msg_count=msg_count)
    _reschedule_timeout(message.from_user.id, state, bot)
    await message.answer(f"🎤 <i>{transcript}</i>\n\n✓ Добавлено")


@router.message(FoodState.collecting, F.text, ~F.text.in_({"✅ Записал всё", "❌ Отмена"}))
async def food_text(message: Message, state: FSMContext, bot: Bot) -> None:
    """Buffer a text message during food session."""
    if not message.text or not message.from_user:
        return
    data = await state.get_data()
    texts: list[str] = data.get("texts", [])
    texts.append(message.text)
    msg_count = data.get("msg_count", 0) + 1
    await state.update_data(texts=texts, msg_count=msg_count)
    _reschedule_timeout(message.from_user.id, state, bot)
    await message.answer("✓ Текст добавлен")


# ────────────────────────── finish / cancel ──────────────────────────

@router.message(FoodState.collecting, F.text == "✅ Записал всё")
async def food_done(message: Message, state: FSMContext, bot: Bot) -> None:
    """Process buffered food data and show analysis."""
    if not message.from_user:
        return
    _cancel_timeout(message.from_user.id)
    await _process_food_session(message.from_user.id, state, bot, message)


@router.message(FoodState.collecting, F.text == "❌ Отмена")
async def food_cancel(message: Message, state: FSMContext) -> None:
    """Cancel food session without analysis."""
    if not message.from_user:
        return
    _cancel_timeout(message.from_user.id)
    await state.clear()
    await message.answer("Отменено.", reply_markup=get_main_keyboard())


# ────────────────────────── /weight command ──────────────────────────

@router.message(Command("weight"))
async def cmd_weight(message: Message) -> None:
    """Log body weight. Usage: /weight 92.5"""
    if not message.from_user or not message.text:
        return
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        await message.answer("Supabase не настроен. Добавь SUPABASE_URL и SUPABASE_KEY в .env")
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /weight 92.5")
        return
    raw = parts[1].replace(",", ".")
    m = re.match(r"(\d+(?:\.\d+)?)", raw)
    if not m:
        await message.answer("Не распознал число. Пример: /weight 92.5")
        return
    weight_kg = float(m.group(1))
    note = " ".join(parts[2:]) if len(parts) > 2 else ""
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        await svc.log_weight(message.from_user.id, weight_kg, note)
        await message.answer(f"⚖️ Вес записан: <b>{weight_kg} кг</b>")
    except Exception as e:
        logger.exception("Weight log error")
        await message.answer(f"Ошибка: {e}")


# ────────────────────────── processing ──────────────────────────

async def _process_food_session(
    user_id: int,
    state: FSMContext,
    bot: Bot,
    trigger_message: Message | None = None,
) -> None:
    """Download photos, call NutritionService, post result, clear state."""
    data = await state.get_data()
    file_ids: list[dict] = data.get("file_ids", [])
    texts: list[str] = data.get("texts", [])

    if not file_ids and not texts:
        await state.clear()
        if trigger_message:
            await trigger_message.answer(
                "Ничего не добавлено. Сессия закрыта.",
                reply_markup=get_main_keyboard(),
            )
        return

    # Send "thinking" indicator
    chat_id = trigger_message.chat.id if trigger_message else user_id
    processing_msg = await bot.send_message(
        chat_id,
        "🔍 Анализирую питание… это займёт несколько секунд.",
    )

    # Download photos
    photo_bytes_list: list[bytes] = []
    for item in file_ids:
        try:
            file = await bot.get_file(item["file_id"])
            if file.file_path:
                fb = await bot.download_file(file.file_path)
                if fb:
                    photo_bytes_list.append(fb.read())
        except Exception:
            logger.exception("Failed to download food photo %s", item["file_id"])

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        await bot.edit_message_text(
            "⚠️ Supabase не настроен. Добавь SUPABASE_URL и SUPABASE_KEY в .env",
            chat_id=chat_id,
            message_id=processing_msg.message_id,
        )
        await state.clear()
        await bot.send_message(chat_id, "Сессия закрыта.", reply_markup=get_main_keyboard())
        return

    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        analysis = await svc.analyze_meal(
            user_id=user_id,
            photo_bytes_list=photo_bytes_list,
            texts=texts,
        )
        progress = await svc.get_today_progress(user_id)
    except Exception as e:
        logger.exception("Nutrition analysis error")
        await bot.edit_message_text(
            f"Ошибка анализа: {e}",
            chat_id=chat_id,
            message_id=processing_msg.message_id,
        )
        await state.clear()
        await bot.send_message(chat_id, "Сессия закрыта.", reply_markup=get_main_keyboard())
        return

    await state.clear()

    # ── Write to vault daily note so main agent sees food context ──
    _write_meal_to_vault(settings, analysis, user_id)

    # Build result message
    emoji = _MEAL_TYPE_EMOJI.get(analysis.meal_type, "🍽")
    report = _format_analysis(emoji, analysis, progress)

    await bot.edit_message_text(
        report,
        chat_id=chat_id,
        message_id=processing_msg.message_id,
        parse_mode="HTML",
    )
    await bot.send_message(
        chat_id,
        "✅ Записано в базу.",
        reply_markup=get_main_keyboard(),
    )
    if analysis.meal_id:
        await bot.send_message(
            chat_id,
            "Ошибка в записи? Отмени:",
            reply_markup=get_meal_done_keyboard(analysis.meal_id),
        )


def _write_meal_to_vault(settings: "Settings", analysis: "MealAnalysis", user_id: int) -> None:  # type: ignore[name-defined]
    """Write a one-line food entry to vault daily note for main agent context."""
    try:
        storage = VaultStorage(settings.vault_path)
        entry = (
            f"🍽 {analysis.meal_type.capitalize()}: {analysis.description} "
            f"— {analysis.calories} ккал "
            f"(Б:{analysis.protein}г Ж:{analysis.fat}г У:{analysis.carbs}г)\n"
            f"  💬 {analysis.comment}\n"
            f"  💡 {analysis.recommendation}"
        )
        storage.append_to_daily(entry, datetime.now(), "[food]")

        session = SessionStore(settings.vault_path)
        session.append(user_id, "food", text=entry)

        if settings.obsidian_sync_enabled:
            asyncio.create_task(asyncio.to_thread(
                VaultGit(settings.vault_path).commit_and_push, "sync: food"
            ))
    except Exception:
        logger.exception("Failed to write meal to vault")


def _format_analysis(emoji: str, a: "MealAnalysis", progress: dict) -> str:  # type: ignore[name-defined]
    kcal_total = progress.get("total_calories", 0)
    kcal_goal = progress.get("goal_calories", 2000)
    prot_total = progress.get("total_protein", 0)
    fat_total = progress.get("total_fat", 0)
    carb_total = progress.get("total_carbs", 0)

    kcal_bar = _bar(kcal_total, kcal_goal)
    kcal_left = max(0, kcal_goal - kcal_total)

    return (
        f"{emoji} <b>{a.meal_type.capitalize()}</b>\n"
        f"{a.description}\n\n"
        f"<b>КБЖУ этого приёма:</b>\n"
        f"  Калории: <b>{a.calories}</b> ккал\n"
        f"  Белки: <b>{a.protein}</b> г  |  Жиры: <b>{a.fat}</b> г  |  Углеводы: <b>{a.carbs}</b> г\n\n"
        f"<b>За сегодня ({kcal_bar}):</b>\n"
        f"  {kcal_total} / {kcal_goal} ккал  (осталось <b>{kcal_left}</b>)\n"
        f"  Б: {prot_total}г  Ж: {fat_total}г  У: {carb_total}г\n\n"
        f"<i>{a.comment}</i>\n\n"
        f"💡 <b>Совет:</b> {a.recommendation}"
    )


def _bar(value: float, goal: float, width: int = 10) -> str:
    """Simple ASCII progress bar."""
    if goal <= 0:
        return "░" * width
    filled = min(int(round(value / goal * width)), width)
    over = value > goal
    char = "█" if not over else "▓"
    return char * filled + "░" * (width - filled)


# ────────────────────────── meal undo ──────────────────────────

@router.callback_query(F.data.startswith("food:undo:"))
async def cb_meal_undo(callback: CallbackQuery) -> None:
    """Delete a specific meal by ID (inline ↩️ button after analysis)."""
    if not callback.from_user or not callback.data:
        return
    meal_id = callback.data.removeprefix("food:undo:")
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        deleted = await svc.delete_meal(meal_id, callback.from_user.id)
    except Exception as e:
        logger.exception("Meal undo error")
        await callback.answer(f"Ошибка: {e}", show_alert=True)
        return
    if deleted:
        await callback.answer("Запись удалена")
        await callback.message.edit_text("↩️ Запись отменена.")  # type: ignore[union-attr]
    else:
        await callback.answer("Запись не найдена (уже удалена?)", show_alert=True)


@router.message(Command("meal_undo"))
async def cmd_meal_undo(message: Message) -> None:
    """Delete the last recorded meal. Usage: /meal_undo"""
    if not message.from_user:
        return
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_key:
        await message.answer("Supabase не настроен.")
        return
    try:
        from d_brain.services.nutrition import get_nutrition_service
        svc = get_nutrition_service()
        deleted = await svc.delete_last_meal(message.from_user.id)
    except Exception as e:
        logger.exception("meal_undo error")
        await message.answer(f"Ошибка: {e}")
        return
    if deleted:
        t = deleted.get("meal_type", "запись")
        desc = deleted.get("description", "")[:50]
        kcal = deleted.get("calories", 0)
        await message.answer(f"↩️ Удалено: <b>{t}</b> — {desc} ({kcal} ккал)")
    else:
        await message.answer("Нет записей для удаления.")


# ────────────────────────── timeout helpers ──────────────────────────

def _schedule_timeout(user_id: int, state: FSMContext, bot: Bot | None) -> None:
    _cancel_timeout(user_id)
    if bot is None:
        return
    task = asyncio.create_task(
        _run_timeout(user_id, state, bot),
        name=f"food_timeout_{user_id}",
    )
    _timeout_tasks[user_id] = task


def _reschedule_timeout(user_id: int, state: FSMContext, bot: Bot) -> None:
    _schedule_timeout(user_id, state, bot)


def _cancel_timeout(user_id: int) -> None:
    task = _timeout_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()


async def _run_timeout(user_id: int, state: FSMContext, bot: Bot) -> None:
    await asyncio.sleep(_FOOD_TIMEOUT_SECS)
    current = await state.get_state()
    if current == FoodState.collecting.state:
        logger.info("Food session timeout for user %s — auto-processing", user_id)
        await bot.send_message(user_id, "⏱ Время ожидания вышло, обрабатываю что собрал…")
        await _process_food_session(user_id, state, bot)
