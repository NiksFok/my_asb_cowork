"""Cascade applier — writes accepted proposals to goal files.

Rotates 3-weekly.md to archive/, generates new 3-weekly.md, optionally
refreshes 2-monthly.md and 1-yearly-{YEAR}.md based on what's in the
proposal.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return date.today().isoformat()


def _yaml_block(fields: dict[str, Any]) -> str:
    """Render a tiny YAML frontmatter block."""
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, str):
            needs_quotes = any(ch in value for ch in [":", "#", "'", '"'])
            if needs_quotes:
                escaped = value.replace('"', '\\"')
                value_repr = f'"{escaped}"'
            else:
                value_repr = value
        else:
            value_repr = str(value)
        lines.append(f"{key}: {value_repr}")
    lines.append("---")
    return "\n".join(lines)


def _render_weekly(proposal_week: dict[str, Any], next_week_id: str) -> str:
    week_num = next_week_id.split("-W")[-1] if "-W" in next_week_id else "?"
    energy = proposal_week.get("energy_level", "medium")
    one_thing = proposal_week.get("one_big_thing", "(не задано)")
    rationale = proposal_week.get("rationale", "")
    must_do = proposal_week.get("must_do_3", []) or []
    should_do = proposal_week.get("should_do", []) or []
    could_do = proposal_week.get("could_do", []) or []
    risks = proposal_week.get("risks", []) or []

    front = _yaml_block({
        "type": "weekly",
        "period": next_week_id,
        "updated": _today_iso(),
        "last_accessed": _today_iso(),
        "relevance": 1.0,
        "tier": "active",
        "week": next_week_id,
    })

    lines = [
        front,
        "",
        f"# Weekly Focus — {next_week_id}",
        "",
        "## ONE Big Thing",
        "",
        "> **If I accomplish nothing else, I will:**",
        f"> {one_thing}",
        "",
    ]
    if rationale:
        lines += ["**Зачем именно это:** " + rationale, ""]
    lines += [
        "---",
        "",
        "## Week at a Glance",
        "",
        f"**Week:** {week_num} of 52",
        f"**Energy Level:** {energy.capitalize()}",
        "",
        "---",
        "",
        "## Priority Tasks",
        "",
        "### Must Do (Non-negotiable)",
        "",
    ]
    for item in must_do:
        if isinstance(item, dict):
            task = item.get("task", "")
            link = item.get("links_to_monthly")
            due = item.get("due", "")
            extras = []
            if due:
                extras.append(f"due: {due}")
            if link:
                extras.append(f"связь: {link}")
            tail = f" — {', '.join(extras)}" if extras else ""
            lines.append(f"- [ ] {task}{tail}")
        else:
            lines.append(f"- [ ] {item}")
    lines.append("")

    if should_do:
        lines += ["### Should Do (Important)", ""]
        for item in should_do:
            lines.append(f"- [ ] {item}")
        lines.append("")
    if could_do:
        lines += ["### Could Do (If time permits)", ""]
        for item in could_do:
            lines.append(f"- [ ] {item}")
        lines.append("")
    if risks:
        lines += ["---", "", "## Risks", ""]
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_monthly(monthly: dict[str, Any]) -> str:
    period = monthly.get("period", date.today().strftime("%Y-%m"))
    theme = monthly.get("theme", "")
    top3 = monthly.get("top_3_priorities", []) or []
    rationale = monthly.get("rationale", "")

    front = _yaml_block({
        "type": "monthly",
        "period": period,
        "updated": _today_iso(),
        "last_accessed": _today_iso(),
        "relevance": 1.0,
        "tier": "active",
    })

    month_label_en = {
        "01": "January", "02": "February", "03": "March", "04": "April",
        "05": "May", "06": "June", "07": "July", "08": "August",
        "09": "September", "10": "October", "11": "November", "12": "December",
    }
    parts = period.split("-")
    label = f"{month_label_en.get(parts[1], parts[1])} {parts[0]}" if len(parts) == 2 else period

    lines = [front, "", f"# Monthly Focus — {label}", ""]
    if theme:
        lines += [f"**Тема месяца:** {theme}", ""]
    lines += ["## Top 3 Priorities", ""]

    for idx, p in enumerate(top3, start=1):
        title = p.get("title", "")
        desc = p.get("description", "")
        why = p.get("why", "")
        actions = p.get("key_actions", []) or []
        dod = p.get("definition_of_done", "")
        lines.append(f"### Priority {idx}: {title}")
        if desc:
            lines += [desc, ""]
        if why:
            lines += [f"**Why it matters:** {why}", ""]
        if actions:
            lines.append("**Key Actions:**")
            for a in actions:
                lines.append(f"- [ ] {a}")
            lines.append("")
        if dod:
            lines += [f"**Definition of Done:** {dod}", ""]
        lines.append("---")
        lines.append("")

    if rationale:
        lines += ["## Rationale", "", rationale, ""]

    return "\n".join(lines).rstrip() + "\n"


def _render_yearly(yearly: dict[str, Any]) -> str:
    period = str(yearly.get("period") or date.today().year)
    theme = yearly.get("theme", "")
    areas = yearly.get("areas", []) or []
    rationale = yearly.get("rationale", "")

    front = _yaml_block({
        "type": "yearly",
        "period": period,
        "updated": _today_iso(),
        "last_accessed": _today_iso(),
        "relevance": 1.0,
        "tier": "active",
    })

    lines = [front, "", f"# Goals {period}", ""]
    if theme:
        lines += ["## Annual Theme", "", f"**{theme}**", "", "---", ""]

    for area in areas:
        name = area.get("name", "Area")
        lines += [f"## {name}", ""]
        for g_idx, goal in enumerate(area.get("goals", []) or [], start=1):
            title = goal.get("title", "")
            metrics = goal.get("success_metrics", []) or []
            milestones = goal.get("quarterly_milestones", {}) or {}
            lines.append(f"### Goal {g_idx}: {title}")
            lines.append("")
            if metrics:
                lines.append("**Success Metrics:**")
                for m in metrics:
                    lines.append(f"- [ ] {m}")
                lines.append("")
            if milestones:
                lines.append("**Quarterly Milestones:**")
                for q in ("Q1", "Q2", "Q3", "Q4"):
                    if milestones.get(q):
                        lines.append(f"- {q}: {milestones[q]}")
                lines.append("")
        lines.append("---")
        lines.append("")

    if rationale:
        lines += ["## Why we refreshed now", "", rationale, ""]

    return "\n".join(lines).rstrip() + "\n"


class CascadeApplier:
    """Applies an accepted cascade proposal to goal files."""

    def __init__(self, vault_path: Path | str) -> None:
        self.vault_path = Path(vault_path)
        self.goals_dir = self.vault_path / "goals"
        self.archive_dir = self.goals_dir / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def apply(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Apply weekly (always) + optional monthly/yearly. Returns summary."""
        result: dict[str, Any] = {"weekly": None, "monthly": None, "yearly": None}

        next_week_id = proposal.get("next_week_id")
        next_week = proposal.get("next_week") or {}
        if next_week_id and next_week:
            result["weekly"] = self._apply_weekly(next_week_id, next_week)

        monthly = proposal.get("monthly_refresh")
        if monthly:
            result["monthly"] = self._apply_monthly(monthly)

        yearly = proposal.get("yearly_refresh")
        if yearly:
            result["yearly"] = self._apply_yearly(yearly)

        return result

    def _apply_weekly(self, next_week_id: str, week_proposal: dict[str, Any]) -> dict[str, Any]:
        weekly_path = self.goals_dir / "3-weekly.md"
        if weekly_path.exists():
            old_text = weekly_path.read_text(encoding="utf-8")
            old_week = self._extract_week_id(old_text) or "unknown"
            archive_path = self.archive_dir / f"3-weekly-{old_week}.md"
            counter = 1
            while archive_path.exists():
                archive_path = self.archive_dir / f"3-weekly-{old_week}-{counter}.md"
                counter += 1
            archive_path.write_text(old_text, encoding="utf-8")
            logger.info("Archived old 3-weekly to %s", archive_path.name)

        new_text = _render_weekly(week_proposal, next_week_id)
        weekly_path.write_text(new_text, encoding="utf-8")
        return {"path": str(weekly_path), "next_week_id": next_week_id}

    def _apply_monthly(self, monthly: dict[str, Any]) -> dict[str, Any]:
        monthly_path = self.goals_dir / "2-monthly.md"
        if monthly_path.exists():
            old_text = monthly_path.read_text(encoding="utf-8")
            old_period = self._extract_period(old_text) or "unknown"
            archive_path = self.archive_dir / f"2-monthly-{old_period}.md"
            counter = 1
            while archive_path.exists():
                archive_path = self.archive_dir / f"2-monthly-{old_period}-{counter}.md"
                counter += 1
            archive_path.write_text(old_text, encoding="utf-8")
            logger.info("Archived old 2-monthly to %s", archive_path.name)

        new_text = _render_monthly(monthly)
        monthly_path.write_text(new_text, encoding="utf-8")
        return {"path": str(monthly_path), "period": monthly.get("period")}

    def _apply_yearly(self, yearly: dict[str, Any]) -> dict[str, Any]:
        period = str(yearly.get("period") or date.today().year)
        yearly_path = self.goals_dir / f"1-yearly-{period}.md"
        old_path = self._find_existing_yearly()
        if old_path and old_path.exists():
            old_text = old_path.read_text(encoding="utf-8")
            old_period = self._extract_period(old_text) or old_path.stem.replace("1-yearly-", "")
            archive_path = self.archive_dir / f"1-yearly-{old_period}.md"
            counter = 1
            while archive_path.exists():
                archive_path = self.archive_dir / f"1-yearly-{old_period}-{counter}.md"
                counter += 1
            archive_path.write_text(old_text, encoding="utf-8")
            if old_path != yearly_path:
                old_path.unlink()
            logger.info("Archived old yearly to %s", archive_path.name)

        new_text = _render_yearly(yearly)
        yearly_path.write_text(new_text, encoding="utf-8")
        return {"path": str(yearly_path), "period": period}

    def _find_existing_yearly(self) -> Path | None:
        candidates = sorted(self.goals_dir.glob("1-yearly-*.md"))
        if not candidates:
            return None
        return candidates[-1]

    @staticmethod
    def _extract_week_id(text: str) -> str | None:
        for line in text.splitlines()[:20]:
            if line.startswith("week:"):
                return line.split(":", 1)[1].strip()
            if line.startswith("period:"):
                return line.split(":", 1)[1].strip()
        return None

    @staticmethod
    def _extract_period(text: str) -> str | None:
        for line in text.splitlines()[:20]:
            if line.startswith("period:"):
                return line.split(":", 1)[1].strip()
        return None
