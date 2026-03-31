"""Reply keyboards for Telegram bot."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

if TYPE_CHECKING:
    from d_brain.config import Settings


def get_main_keyboard(settings: Settings | None = None) -> ReplyKeyboardMarkup:
    """Main reply keyboard. Dynamic based on settings (help onboarding, improve mode)."""
    from d_brain.config import get_settings as _get_settings
    if settings is None:
        settings = _get_settings()

    builder = ReplyKeyboardBuilder()
    # Row 1: fixed buttons
    builder.button(text="✨ Запрос")
    builder.button(text="📅 Неделя")
    builder.button(text="⚙️ Настройки")

    # Row 2: Еда (if nutrition enabled) + Правки + optional onboarding/improve buttons
    row2 = []
    if settings.nutrition_enabled:
        row2.append("🍽 Еда")
    row2.append("✏️ Правки")
    if settings.first_seen:
        try:
            days_since = (date.today() - date.fromisoformat(settings.first_seen)).days
            if days_since < 30:
                row2.append("❓ Помощь")
        except ValueError:
            pass
    if settings.improve_mode:
        row2.append("🔧 Улучшить")

    for text in row2:
        builder.button(text=text)

    builder.adjust(3, len(row2))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_food_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard shown during food logging session."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Записал всё")
    builder.button(text="❌ Отмена")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_session_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard shown during an active Claude session."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="📋 Журнал")
    builder.button(text="🛑 Завершить сессию")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_edit_mode_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard shown during edit mode (collecting entries)."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Готово")
    builder.button(text="❌ Отмена")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_edit_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Keyboard shown after edit preview (confirm/cancel)."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Применить")
    builder.button(text="❌ Отменить")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def get_start_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for /start message with quick-access commands."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Запрос", callback_data="cmd:do")
    builder.button(text="📅 Неделя", callback_data="cmd:weekly")
    builder.button(text="⚙️ Настройки", callback_data="cmd:settings")
    builder.adjust(3)
    return builder.as_markup()


def get_help_inline_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for /help message with quick-access commands."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Запрос", callback_data="cmd:do")
    builder.button(text="📅 Неделя", callback_data="cmd:weekly")
    builder.adjust(2)
    return builder.as_markup()


def get_meal_done_keyboard(meal_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard shown after meal is recorded — allows quick undo."""
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Отменить запись", callback_data=f"food:undo:{meal_id}")
    return builder.as_markup()


def get_settings_keyboard(
    night_notifications: bool = True,
    health_enabled: bool = False,
    obsidian_sync_enabled: bool = False,
    improve_mode: bool = False,
    nutrition_enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Inline keyboard for Settings menu."""
    builder = InlineKeyboardBuilder()
    toggle_label = "🔔 Ночные уведомления: ВКЛ" if night_notifications else "🔕 Ночные уведомления: ВЫКЛ"
    builder.button(text=toggle_label, callback_data="settings:toggle_night")
    health_label = "🫀 Здоровье (Oura): ВКЛ" if health_enabled else "🫀 Здоровье (Oura): ВЫКЛ"
    builder.button(text=health_label, callback_data="settings:toggle_health")
    nutrition_label = "🍽 Нутрициолог: ВКЛ" if nutrition_enabled else "🍽 Нутрициолог: ВЫКЛ"
    builder.button(text=nutrition_label, callback_data="settings:toggle_nutrition")
    sync_label = "📡 Obsidian Sync: ВКЛ" if obsidian_sync_enabled else "📡 Obsidian Sync: ВЫКЛ"
    builder.button(text=sync_label, callback_data="settings:toggle_obsidian_sync")
    improve_label = "🔧 Улучшения: ВКЛ" if improve_mode else "🔧 Улучшения: ВЫКЛ"
    builder.button(text=improve_label, callback_data="settings:toggle_improve")
    builder.button(text="🏙️ Сменить город", callback_data="settings:change_city")
    builder.button(text="❓ Помощь", callback_data="settings:help")
    builder.adjust(1)
    return builder.as_markup()
