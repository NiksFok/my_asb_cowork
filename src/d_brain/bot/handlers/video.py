"""Video message handler — transcribes audio from mp4/video files."""

import logging
from datetime import datetime

from aiogram import Bot, Router
from aiogram.types import Message

from d_brain.config import get_settings
from d_brain.services.corrections import CorrectionsService
from d_brain.services.session import SessionStore
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="video")
logger = logging.getLogger(__name__)


@router.message(lambda m: (m.video is not None or m.video_note is not None) and m.forward_origin is None)
async def handle_video(message: Message, bot: Bot) -> None:
    """Handle directly sent video/video_note — extract and transcribe audio."""
    if not message.from_user:
        return

    await message.chat.do(action="typing")

    settings = get_settings()
    storage = VaultStorage(settings.vault_path)

    video = message.video or message.video_note
    if not video:
        return

    # Download
    try:
        file = await bot.get_file(video.file_id)
        if not file.file_path:
            await message.answer("❌ Не удалось скачать видео")
            return
        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            await message.answer("❌ Не удалось скачать видео")
            return
        video_bytes = file_bytes.read()
    except Exception as e:
        logger.exception("Failed to download video")
        await message.answer(f"❌ Ошибка загрузки: {e}")
        return

    # Transcribe
    transcript = ""
    try:
        transcriber = DeepgramTranscriber(settings.deepgram_api_key)
        transcript = await transcriber.transcribe(video_bytes)
    except Exception:
        logger.warning("Video transcription failed", exc_info=True)

    timestamp = datetime.fromtimestamp(message.date.timestamp())

    if transcript:
        corrections = CorrectionsService(settings.vault_path)
        corrected, applied = corrections.apply(transcript)

        caption = message.caption or ""
        content = f"{caption}\n\n{corrected}".strip() if caption else corrected

        storage.append_to_daily(content, timestamp, "[video]")

        session = SessionStore(settings.vault_path)
        session.append(
            message.from_user.id,
            "video",
            text=corrected,
            msg_id=message.message_id,
        )

        note = f"\n<i>Исправлено: {', '.join(applied)}</i>" if applied else ""
        await message.answer(f"🎬 {corrected}\n\n✓ Сохранено{note}")
        logger.info("Video transcribed: %d chars", len(corrected))
    else:
        content = message.caption or "[video]"
        storage.append_to_daily(content, timestamp, "[video]")

        session = SessionStore(settings.vault_path)
        session.append(
            message.from_user.id,
            "video",
            text=content,
            msg_id=message.message_id,
        )

        await message.answer("🎬 ✓ Сохранено (аудио не распознано)")
        logger.info("Video saved without transcript")
