#!/usr/bin/env python
"""Finalize weekly reflection — called by /done or Monday 09:00 systemd timer.

Reads pending reflection file, calls Claude to structure it and append
a reflection section to the weekly summary, then clears the flag.
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from d_brain.config import get_settings
from d_brain.services.reflection import ReflectionService
from d_brain.services.git import VaultGit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 1200  # 20 minutes


def finalize_reflection(settings, week: str, reflection_path: Path, summary_path: Path) -> str:
    """Process the weekly reflection through the persistent session and append
    it to the summary.

    ASB v3.0: runs on the shared interactive session (subscription billing),
    not headless `claude --print`. Paths are passed ABSOLUTE because the session
    cwd is the vault, not the project root.

    Returns:
        Output string to send to user.
    """
    from d_brain.services.runtime import get_session

    refl_abs = reflection_path.resolve()
    summ_abs = summary_path.resolve()

    prompt = f"""Сегодня я завершаю недельную рефлексию за {week}.

ФАЙЛЫ:
- Сырые ответы рефлексии: {refl_abs}
- Недельный дайджест: {summ_abs}

ЗАДАЧА:
1. Прочитай файл рефлексии {refl_abs}
2. Прочитай недельный дайджест {summ_abs}
3. Структурируй ответы пользователя по 6 вопросам рефлексии:
   1. Что запомнилось сильнее всего?
   2. Самый успешный день?
   3. Достигнутые цели?
   4. Что принесло больше всего радости?
   5. Что принесло больше всего пользы?
   6. Какого опыта хотелось бы избежать?
4. Сформулируй 2-3 конкретные рекомендации для следующей недели
   на основе рефлексии и целей из vault/goals/3-weekly.md
5. ДОЗАПИШИ в конец файла {summ_abs} следующий раздел в Markdown формате:

## 🪞 Рефлексия недели

[структурированные ответы по 6 вопросам]

### 💡 Рекомендации на следующую неделю
[2-3 рекомендации]

CRITICAL OUTPUT FORMAT:
- Return ONLY raw HTML for Telegram (parse_mode=HTML)
- Start with ✅ <b>Рефлексия недели {week} сохранена</b>
- Кратко перечисли ключевые инсайты (3-5 пунктов)
- Allowed tags: <b>, <i>, <code>, <s>, <u>"""

    res = get_session(settings).ask(
        prompt, timeout=DEFAULT_TIMEOUT, request_id="maint-reflect-finalize", wrap=True
    )
    if res.status == "logged_out":
        logger.error("session logged out")
        return "❌ Рефлексия: Claude разлогинился — нужен вход (/login)"
    if res.status == "rate_limited":
        logger.error("subscription rate limited")
        return "❌ Рефлексия: лимит подписки исчерпан, попробуй позже"
    if not res.ok:
        logger.error("session ask failed: %s %s", res.status, res.detail)
        return f"❌ Ошибка обработки рефлексии: {(res.detail or res.status)[:300]}"

    # Clean output using the same logic as processor.py
    import re
    output = (res.reply or "").strip()
    if "\n---\n" in output or output.startswith("---\n"):
        lines = output.split("\n")
        seps = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
        if len(seps) >= 2:
            output = "\n".join(lines[seps[0] + 1 : seps[-1]]).strip()
        elif len(seps) == 1:
            idx = seps[0]
            before = "\n".join(lines[:idx]).strip()
            after = "\n".join(lines[idx + 1 :]).strip()
            if re.match(r"^[📅📊✅❌<🧠📝✨💡🎯🪞]", before):
                output = before
            else:
                output = after

    # Strip preamble phrases
    preamble_patterns = [
        r"^Вот сырой HTML для Telegram[:\s]*",
        r"^Вот HTML для Telegram[:\s]*",
        r"^Вот готовый HTML[:\s]*",
        r"^HTML для Telegram[:\s]*",
    ]
    for pattern in preamble_patterns:
        output = re.sub(pattern, "", output, flags=re.IGNORECASE).strip()

    return output


async def main() -> None:
    """Main entry point."""
    settings = get_settings()
    reflection = ReflectionService(settings.vault_path)

    week = reflection.get_pending_week()
    if not week:
        logger.info("No pending reflection — nothing to do.")
        print("No pending reflection.")
        return

    if not reflection.has_content(week):
        logger.info("Reflection for %s is empty — skipping.", week)
        print(f"Reflection for {week} is empty.")
        return

    reflection_path = reflection.get_reflection_path(week)
    summary_path = reflection.get_summary_path(week)

    if not summary_path.exists():
        logger.warning("Weekly summary %s not found — will create standalone reflection.", summary_path)

    logger.info("Finalizing reflection for week %s", week)
    output = finalize_reflection(settings, week, reflection_path, summary_path)

    # Never send an empty message — Telegram rejects it (this was the recurring
    # d-brain-reflect.service failure). Fall back to a minimal card.
    if not output or len(output.strip()) < 20:
        logger.warning("empty reflection output — sending fallback card")
        output = (
            f"🪞 <b>Рефлексия недели {week}</b>\n"
            f"Обработка завершена, но текст отчёта пуст. "
            f"Загляни в {summary_path.name}."
        )

    # Clear flag file
    reflection.clear(week)
    logger.info("Reflection flag cleared.")

    # Commit to git
    git = VaultGit(settings.vault_path)
    try:
        git.commit_and_push(f"chore: reflection {week}")
    except Exception as e:
        logger.warning("Git commit failed: %s", e)

    # Send result to Telegram
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        user_id = settings.allowed_user_ids[0] if settings.allowed_user_ids else None
        if not user_id:
            logger.error("No allowed_user_ids configured")
            print(output)
            return

        try:
            await bot.send_message(chat_id=user_id, text=output)
        except Exception:
            await bot.send_message(chat_id=user_id, text=output, parse_mode=None)

        logger.info("Reflection result sent to user %s", user_id)
        print(output)
    finally:
        await bot.session.close()

    # --- trigger cascade goal review ---
    try:
        cascade_script = Path(__file__).parent / "cascade_review.py"
        logger.info("Launching cascade review for week %s", week)
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(cascade_script), week],
            cwd=str(settings.vault_path.parent),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning("Cascade review exited with %s: %s", proc.returncode, proc.stderr[:300])
    except Exception:
        logger.exception("Cascade review trigger failed")


if __name__ == "__main__":
    asyncio.run(main())
