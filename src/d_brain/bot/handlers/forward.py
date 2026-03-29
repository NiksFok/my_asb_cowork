"""Forwarded message handler."""

import asyncio
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Router
from aiogram.types import Message

from d_brain.config import get_settings
from d_brain.services.corrections import CorrectionsService
from d_brain.services.git import VaultGit
from d_brain.services.session import SessionStore
from d_brain.services.storage import VaultStorage
from d_brain.services.transcription import DeepgramTranscriber

router = Router(name="forward")
logger = logging.getLogger(__name__)


async def _generate_summary(text: str, source: str, vault_path: Path) -> dict | None:
    """Generate 3-point summary via Claude Haiku. Returns None on timeout/failure."""
    if len(text) < 80:
        return None
    prompt = (
        f"Статья/текст от «{source}». Выдели 3 ключевых тезиса и 1 практическую идею для личного бота-помощника.\n"
        f"Верни ТОЛЬКО JSON без markdown: {{\"points\": [\"...\",\"...\",\"...\"], \"idea\": \"...\"}}\n\n"
        f"Текст:\n{text[:1500]}"
    )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: subprocess.run(
                    ["claude", "--print", "--dangerously-skip-permissions",
                     "--model", "claude-haiku-4-5-20251001", "-p", prompt],
                    capture_output=True, text=True, timeout=20,
                    cwd=str(vault_path.parent), check=False,
                )
            ),
            timeout=22,
        )
        output = result.stdout.strip()
        if "```" in output:
            output = re.sub(r"```(?:json)?\s*", "", output).strip().rstrip("`").strip()
        data = json.loads(output)
        if isinstance(data.get("points"), list) and data.get("idea"):
            return data
    except Exception as e:
        logger.debug("Summary generation skipped: %s", e)
    return None


@router.message(lambda m: m.forward_origin is not None)
async def handle_forward(message: Message, bot: Bot) -> None:
    """Handle forwarded messages."""
    if not message.from_user:
        return

    settings = get_settings()
    storage = VaultStorage(settings.vault_path)

    # Determine source name
    source_name = "Unknown"
    origin = message.forward_origin

    if hasattr(origin, "sender_user") and origin.sender_user:
        user = origin.sender_user
        source_name = user.full_name
    elif hasattr(origin, "sender_user_name") and origin.sender_user_name:
        source_name = origin.sender_user_name
    elif hasattr(origin, "chat") and origin.chat:
        chat = origin.chat
        source_name = f"@{chat.username}" if chat.username else chat.title or "Channel"
    elif hasattr(origin, "sender_name") and origin.sender_name:
        source_name = origin.sender_name

    msg_type = f"[forward from: {source_name}]"
    timestamp = datetime.fromtimestamp(message.date.timestamp())

    # Handle forwarded video — transcribe audio track
    video = message.video or message.video_note
    if video:
        await message.chat.do(action="typing")
        transcript = await _transcribe_video(bot, video, settings.deepgram_api_key)
        if transcript:
            corrections = CorrectionsService(settings.vault_path)
            corrected, applied = corrections.apply(transcript)
            caption = message.caption or ""
            content = f"{caption}\n\n{corrected}".strip() if caption else corrected
            storage.append_to_daily(content, timestamp, msg_type)
            session = SessionStore(settings.vault_path)
            session.append(
                message.from_user.id,
                "forward",
                text=content,
                source=source_name,
                msg_id=message.message_id,
            )
            note = f"\n<i>Исправлено: {', '.join(applied)}</i>" if applied else ""
            await message.answer(f"🎬 {corrected}\n\n✓ Сохранено (от {source_name}){note}")
        else:
            content = message.caption or "[video]"
            storage.append_to_daily(content, timestamp, msg_type)
            session = SessionStore(settings.vault_path)
            session.append(
                message.from_user.id,
                "forward",
                text=content,
                source=source_name,
                msg_id=message.message_id,
            )
            await message.answer(f"🎬 ✓ Сохранено (от {source_name}, аудио не распознано)")
        logger.info("Forwarded video saved from: %s", source_name)
        return

    # Text / caption / other media
    content = message.text or message.caption or "[media]"
    storage.append_to_daily(content, timestamp, msg_type)

    session = SessionStore(settings.vault_path)
    session.append(
        message.from_user.id,
        "forward",
        text=content,
        source=source_name,
        msg_id=message.message_id,
    )

    # Generate structured summary for text content
    summary = None
    if content != "[media]":
        summary = await _generate_summary(content, source_name, settings.vault_path)
        if summary:
            summary_block = (
                "📋 Резюме:\n" +
                "\n".join(f"• {p}" for p in summary["points"]) +
                f"\n💡 {summary['idea']}"
            )
            storage.append_to_daily(summary_block, timestamp, "[summary]")

    if summary and summary.get("points"):
        points_text = "\n".join(f"• {p}" for p in summary["points"])
        await message.answer(
            f"✓ Сохранено (от {source_name})\n\n{points_text}\n💡 {summary['idea']}"
        )
    else:
        await message.answer(f"✓ Сохранено (от {source_name})")

    if settings.obsidian_sync_enabled:
        asyncio.create_task(asyncio.to_thread(
            VaultGit(settings.vault_path).commit_and_push, "sync: forward"
        ))
    logger.info("Forwarded message saved from: %s", source_name)


async def _transcribe_video(bot: Bot, video: object, api_key: str) -> str:
    """Download video and transcribe audio. Returns empty string on failure."""
    try:
        file_id = getattr(video, "file_id", None)
        if not file_id:
            return ""
        file = await bot.get_file(file_id)
        if not file.file_path:
            return ""
        file_bytes = await bot.download_file(file.file_path)
        if not file_bytes:
            return ""
        video_bytes = file_bytes.read()
        transcriber = DeepgramTranscriber(api_key)
        return await transcriber.transcribe(video_bytes)
    except Exception:
        logger.warning("Video transcription failed", exc_info=True)
        return ""
