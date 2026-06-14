"""Handler for /cascade command — manually trigger cascade goal review."""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from d_brain.config import get_settings
from d_brain.services.cascade import CascadeService

router = Router(name="cascade")
logger = logging.getLogger(__name__)


@router.message(Command("cascade"))
async def cmd_cascade(message: Message) -> None:
    """Trigger cascade goal review on demand."""
    settings = get_settings()
    cascade = CascadeService(settings.vault_path)

    if cascade.is_active() or cascade.is_in_feedback_mode():
        await message.answer(
            "🎯 Каскадное ревью уже идёт. Ответь голосом или текстом:\n"
            "• <b>годно</b> — применю\n"
            "• правки — пересчитаю\n"
            "• <b>отмена</b> — закрою"
        )
        return

    project_dir = settings.vault_path.parent
    script = project_dir / "scripts" / "cascade_review.py"

    status = await message.answer("⏳ Запускаю каскадное ревью целей. Это займёт 1–3 минуты, Claude читает контекст...")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(script)],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if result.returncode != 0:
            err = result.stderr or "Unknown error"
            await status.edit_text(f"❌ Cascade review failed:\n<code>{err[:500]}</code>")
            return
        try:
            await status.delete()
        except Exception:
            pass
    except asyncio.TimeoutError:
        await status.edit_text("❌ Каскадное ревью заняло слишком много времени.")
    except Exception as e:
        logger.exception("Cascade command failed")
        await status.edit_text(f"❌ Ошибка: {e}")
