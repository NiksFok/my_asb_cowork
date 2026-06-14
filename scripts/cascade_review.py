#!/usr/bin/env python
"""Cascade goal review orchestrator.

Runs after weekly reflection is finalized (or when invoked via /cascade).
Calls Claude with the cascade-reviewer agent prompt to analyze the past
week against monthly/yearly goals and propose updates. Stores the JSON
proposal as pending state and sends a verification card to the user via
Telegram.

The user then approves with "годно" / "ок" / "принимаю", or describes
changes verbally — voice/text handler routes to cascade re-run.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from d_brain.config import get_settings
from d_brain.services.cascade import CascadeService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 1500


def _iso_week(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _build_prompt(week_just_finished: str, today: date, feedback_history: list[dict]) -> str:
    next_week = _iso_week(today + timedelta(days=7 - today.isoweekday()))
    fb_block = ""
    if feedback_history:
        items = "\n".join(
            f"- ({fb.get('ts','')}) {fb.get('text','')}" for fb in feedback_history
        )
        fb_block = (
            "\n\n## Правки от пользователя на предыдущие итерации\n"
            "Учти их при формировании нового предложения. Не предлагай то, "
            "что уже было отвергнуто, и обязательно отрази изменения, о которых попросили:\n"
            + items
        )

    return f"""Сегодня {today.isoformat()} ({today.strftime('%A')}).
Только что завершилась рефлексия недели {week_just_finished}.
Следующая неделя: {next_week}.

Используй спецификацию агента из vault/.claude/agents/cascade-reviewer.md.

Прочитай (ОБЯЗАТЕЛЬНО все источники):
1. vault/.claude/agents/cascade-reviewer.md — как работать
2. vault/goals/0-vision-3y.md
3. vault/goals/1-yearly-*.md (последний по году)
4. vault/goals/2-monthly.md
5. vault/goals/3-weekly.md
6. vault/summaries/{week_just_finished}-summary.md
7. vault/summaries/{week_just_finished}-reflection.md
8. vault/daily/*.md за последние 14 дней
9. vault/summaries/*-reflection.md за последние 4 недели (для паттернов)

Затем (ОБЯЗАТЕЛЬНО):
- Вызови mcp__todoist__find-completed-tasks за последние 7 дней — реальные победы
- Вызови mcp__todoist__find-tasks без фильтров — посмотри текущий backlog

ОЦЕНИ:
- Куда двигалась эта неделя относительно месячных приоритетов и годовых целей
- Где был дрейф, где прорыв
- Что стоит делать на следующей неделе

ОПРЕДЕЛИ нужно ли обновлять monthly_refresh / yearly_refresh / vision_refresh
по правилам из cascade-reviewer.md. Если нужно — заполни соответствующий блок.
Если нет — null.

ВЫВОД: единственный JSON-объект ровно по схеме из cascade-reviewer.md.
Без markdown-обрамления, без пояснений до или после JSON.
Только текст самого объекта от первой `{{` до последней `}}`.
{fb_block}"""


def _strip_to_json(raw: str) -> str:
    """Strip code fences and locate the outer JSON object."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def call_reviewer(settings, week_just_finished: str, feedback_history: list[dict]) -> dict:
    """Run the cascade reviewer prompt on the shared session, return parsed JSON.

    ASB v3.0: on the persistent session (subscription), not headless
    `claude --print`.
    """
    from d_brain.services.runtime import get_session

    today = date.today()
    prompt = _build_prompt(week_just_finished, today, feedback_history)

    logger.info("Calling cascade reviewer (week %s, iter %d)", week_just_finished, len(feedback_history))
    res = get_session(settings).ask(
        prompt, timeout=DEFAULT_TIMEOUT, request_id="maint-cascade", wrap=True
    )
    if not res.ok:
        logger.error("Cascade reviewer failed: %s %s", res.status, res.detail)
        return {"_error": (res.detail or res.status)[:500]}

    raw = res.reply or ""
    logger.debug("Cascade reviewer raw output: %s", raw[:500])
    # Strip C0 control chars: the fixed-width pane soft-wraps long lines and the
    # capture can land a bare newline inside a JSON string (see extract_json.py).
    candidate = re.sub(r"[\x00-\x1f]", "", _strip_to_json(raw))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.error("JSON decode failed: %s; raw[:500]=%s", e, candidate[:500])
        return {"_error": f"JSON decode: {e}", "_raw": raw[:1500]}


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_card(proposal: dict, iteration: int) -> str:
    """Render the proposal as a Telegram-friendly HTML card."""
    if "_error" in proposal:
        return (
            f"❌ <b>Cascade reviewer не смог выдать предложение</b>\n\n"
            f"<code>{_esc(str(proposal.get('_error'))[:500])}</code>"
        )

    next_week_id = proposal.get("next_week_id", "?")
    assess = proposal.get("week_assessment", {}) or {}
    nw = proposal.get("next_week", {}) or {}
    advisor = proposal.get("advisor_notes", []) or []

    verdict_emoji = {
        "on_track": "🟢",
        "drifting": "🟡",
        "off_track": "🔴",
        "breakthrough": "🚀",
    }.get(assess.get("verdict", ""), "⚪")

    parts = []
    header = f"🎯 <b>Каскад целей — предложение на {_esc(next_week_id)}</b>"
    if iteration > 0:
        header += f" <i>(итерация {iteration})</i>"
    parts.append(header)
    parts.append("")

    parts.append(f"<b>Прошлая неделя:</b> {verdict_emoji} <i>{_esc(assess.get('verdict','—'))}</i>")
    if assess.get("movement_to_monthly"):
        parts.append(f"К месячным целям: {_esc(assess['movement_to_monthly'])}")
    if assess.get("movement_to_yearly"):
        parts.append(f"К годовым целям: {_esc(assess['movement_to_yearly'])}")
    if assess.get("wins"):
        parts.append("Победы: " + "; ".join(_esc(w) for w in assess["wins"]))
    if assess.get("misses"):
        parts.append("Промахи: " + "; ".join(_esc(m) for m in assess["misses"]))
    parts.append("")

    parts.append("🎯 <b>ONE Big Thing на следующую неделю:</b>")
    parts.append(_esc(nw.get("one_big_thing", "(не задано)")))
    if nw.get("rationale"):
        parts.append(f"<i>{_esc(nw['rationale'])}</i>")
    parts.append("")

    must = nw.get("must_do_3", []) or []
    if must:
        parts.append("⚡ <b>Must Do:</b>")
        for item in must:
            if isinstance(item, dict):
                line = f"• {_esc(item.get('task',''))}"
                meta = []
                if item.get("due"):
                    meta.append(f"due: {_esc(item['due'])}")
                if item.get("links_to_monthly"):
                    meta.append(f"→ {_esc(item['links_to_monthly'])}")
                if meta:
                    line += f"  <i>({', '.join(meta)})</i>"
                parts.append(line)
            else:
                parts.append(f"• {_esc(str(item))}")
        parts.append("")

    if nw.get("risks"):
        parts.append("⚠️ <b>Риски:</b>")
        for r in nw["risks"]:
            parts.append(f"• {_esc(r)}")
        parts.append("")

    if proposal.get("monthly_refresh"):
        m = proposal["monthly_refresh"]
        parts.append(f"📅 <b>Также обновлю месяц ({_esc(m.get('period',''))})</b>")
        if m.get("theme"):
            parts.append(f"Тема: <i>{_esc(m['theme'])}</i>")
        for idx, p in enumerate(m.get("top_3_priorities", []) or [], start=1):
            parts.append(f"{idx}. <b>{_esc(p.get('title',''))}</b> — {_esc(p.get('description',''))}")
        parts.append("")

    if proposal.get("yearly_refresh"):
        y = proposal["yearly_refresh"]
        parts.append(f"📆 <b>Также обновлю год ({_esc(str(y.get('period','')))})</b>")
        if y.get("theme"):
            parts.append(f"Тема: <i>{_esc(y['theme'])}</i>")
        parts.append(f"<i>{_esc(y.get('rationale',''))}</i>")
        parts.append("")

    if advisor:
        parts.append("💡 <b>Советы:</b>")
        for note in advisor:
            parts.append(f"• {_esc(note)}")
        parts.append("")

    parts.append(
        "—\n"
        "Скажи голосом или напиши:\n"
        "• <b>годно</b> / <b>ок</b> / <b>принимаю</b> — применю и обновлю файлы целей\n"
        "• любые правки — например «поменяй ONE Big Thing на …», «убери третий пункт», «добавь риск …» — пересчитаю\n"
        "• <b>отмена</b> — закрою без изменений"
    )
    return "\n".join(parts)


async def send_card(bot: Bot, user_id: int, text: str) -> None:
    chunks = []
    cur = text
    LIMIT = 3900
    while len(cur) > LIMIT:
        cut = cur.rfind("\n", 0, LIMIT)
        if cut <= 0:
            cut = LIMIT
        chunks.append(cur[:cut])
        cur = cur[cut:].lstrip()
    chunks.append(cur)
    for chunk in chunks:
        try:
            await bot.send_message(chat_id=user_id, text=chunk)
        except Exception:
            await bot.send_message(chat_id=user_id, text=chunk, parse_mode=None)


async def main(week_arg: str | None = None) -> None:
    dry_run = os.environ.get("CASCADE_DRYRUN") == "1"
    settings = get_settings()
    cascade = CascadeService(settings.vault_path)

    if week_arg:
        week = week_arg
    else:
        last_week_date = date.today() - timedelta(days=date.today().isoweekday() % 7 + 1)
        week = _iso_week(last_week_date)

    feedback_history: list[dict] = []
    existing = cascade.get()
    if existing and existing.get("stage") in {"awaiting_decision", "awaiting_feedback"}:
        feedback_history = existing.get("feedback_history", [])
        week = existing.get("week_just_finished", week)
        logger.info("Re-running cascade for %s with %d feedback items", week, len(feedback_history))

    proposal = call_reviewer(settings, week, feedback_history)

    if "_error" in proposal:
        logger.error("Cascade aborted: %s", proposal["_error"])
        if dry_run:
            print("[DRYRUN] error: " + str(proposal.get("_error")))
            return
        bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            user_id = settings.allowed_user_ids[0]
            await send_card(bot, user_id, render_card(proposal, 0))
        finally:
            await bot.session.close()
        return

    deadline = datetime.now().astimezone() + timedelta(days=7)
    if dry_run:
        import json as _json
        print("[DRYRUN] proposal JSON:")
        print(_json.dumps(proposal, ensure_ascii=False, indent=2))
        print("[DRYRUN] would render card:")
        print(render_card(proposal, 0))
        return

    if existing:
        cascade.update_proposal(proposal)
    else:
        cascade.start(week, proposal, deadline)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        user_id = settings.allowed_user_ids[0]
        iteration = (cascade.get() or {}).get("iterations", 0)
        await send_card(bot, user_id, render_card(proposal, iteration))
    finally:
        await bot.session.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg))
