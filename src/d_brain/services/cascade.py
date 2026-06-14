"""Cascade goal review service.

Manages the lifecycle of a pending cascade review: store proposal JSON,
record user feedback, mark applied/cancelled. State persists in a single
file so it survives bot restarts (bot is restarted twice a day).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PENDING_FILENAME = "cascade-pending.json"
_FEEDBACK_LOG_FILENAME = "cascade-feedback.md"


class CascadeService:
    """Service for managing the pending cascade review state."""

    def __init__(self, vault_path: Path | str) -> None:
        self.session_dir = Path(vault_path) / ".session"
        self.session_dir.mkdir(parents=True, exist_ok=True)

    @property
    def pending_path(self) -> Path:
        return self.session_dir / _PENDING_FILENAME

    @property
    def feedback_log_path(self) -> Path:
        return self.session_dir / _FEEDBACK_LOG_FILENAME

    def start(self, week_just_finished: str, proposal: dict[str, Any], deadline: datetime) -> None:
        """Create pending state with the initial proposal from the cascade reviewer."""
        state = {
            "week_just_finished": week_just_finished,
            "started": datetime.now().astimezone().isoformat(),
            "deadline": deadline.isoformat(),
            "stage": "awaiting_decision",
            "iterations": 0,
            "feedback_history": [],
            "proposal": proposal,
        }
        self.pending_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Cascade review started for week %s", week_just_finished)

    def get(self) -> dict[str, Any] | None:
        """Return current pending state or None."""
        if not self.pending_path.exists():
            return None
        try:
            return json.loads(self.pending_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to read cascade pending state: %s", e)
            return None

    def is_active(self) -> bool:
        state = self.get()
        return state is not None and state.get("stage") == "awaiting_decision"

    def is_in_feedback_mode(self) -> bool:
        state = self.get()
        return state is not None and state.get("stage") == "awaiting_feedback"

    def enter_feedback_mode(self) -> None:
        state = self.get()
        if state is None:
            return
        state["stage"] = "awaiting_feedback"
        self.pending_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def record_feedback(self, text: str) -> None:
        """Append user feedback for the next reviewer iteration."""
        state = self.get()
        if state is None:
            return
        state.setdefault("feedback_history", []).append(
            {"ts": datetime.now().astimezone().isoformat(), "text": text}
        )
        self.pending_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.feedback_log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n{text}\n")

    def update_proposal(self, proposal: dict[str, Any]) -> None:
        """Replace proposal after a re-run with new feedback."""
        state = self.get()
        if state is None:
            return
        state["proposal"] = proposal
        state["iterations"] = state.get("iterations", 0) + 1
        state["stage"] = "awaiting_decision"
        self.pending_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def mark_applied(self) -> None:
        state = self.get()
        if state is None:
            return
        state["stage"] = "applied"
        state["applied_at"] = datetime.now().astimezone().isoformat()
        self.pending_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def clear(self) -> None:
        if self.pending_path.exists():
            self.pending_path.unlink()
            logger.info("Cascade pending state cleared")

    def is_expired(self) -> bool:
        state = self.get()
        if state is None:
            return False
        try:
            deadline = datetime.fromisoformat(state.get("deadline", ""))
            now = datetime.now(tz=deadline.tzinfo) if deadline.tzinfo else datetime.now()
            return now >= deadline
        except Exception:
            return False
