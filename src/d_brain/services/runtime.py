"""Process-wide singletons for the shared persistent Claude session.

The bot, the nightly pipeline and the watchdog must all talk to ONE persistent
interactive Claude Code session (subscription billing — no headless `claude -p`).
This module builds it lazily from Settings and hands the same instance to every
caller. An asyncio lock serializes ask() calls within the bot process (the
cross-process flock in ClaudeSession is the real mutex; this just avoids piling
up blocked worker threads).

Adapted for this fork: work_dir is the VAULT (settings.vault_path) so the nightly
pipeline's vault-relative prompts (`.claude/skills/...`, `daily/…`, `goals/…`,
`MEMORY.md`) resolve exactly as they did under `cd "$VAULT_DIR"` in process.sh.
The mcp-config and persona files live at the project root and are passed as
ABSOLUTE paths, so cwd does not affect them. TODOIST_API_KEY is baked into the
tmux start command via env_prefix.
"""

import asyncio
import logging
import uuid
from pathlib import Path

from d_brain.config import Settings
from d_brain.services.claude_session import ClaudeSession

logger = logging.getLogger(__name__)

_session: ClaudeSession | None = None
_cron_session: ClaudeSession | None = None
_processor = None  # type: ignore[var-annotated]
_ask_lock = asyncio.Lock()


def reset() -> None:
    """Drop the singletons (tests only)."""
    global _session, _cron_session, _processor
    _session = None
    _cron_session = None
    _processor = None


def _persisted_name(settings: Settings) -> str:
    if settings.brain_session_name:
        return settings.brain_session_name
    # Randomize per install (fingerprint hygiene) and persist so restarts
    # reuse the same tmux session name.
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    name_file = settings.runtime_dir / "brain.name"
    if name_file.exists():
        return name_file.read_text().strip()
    name = f"dbrain_{uuid.uuid4().hex[:8]}"
    name_file.write_text(name + "\n")
    return name


def _build_session(
    settings: Settings, *, session_name: str, runtime_dir: Path
) -> ClaudeSession:
    # Resolve to absolute: vault_path may be configured relative ("vault"), and
    # the tmux start command does `cd work_dir` before referencing the persona /
    # mcp-config. A relative persona path would break after that cd (it would be
    # looked up inside the vault, where deploy/ does not exist). Absolute paths
    # survive the cd regardless of where the process was launched from.
    vault = settings.vault_path.resolve()
    project_root = vault.parent
    mcp = project_root / "mcp-config.json"
    brain_prompt = project_root / "deploy" / "brain-system.md"
    # Boot assertion: without the persona file the brain would silently start as
    # a vanilla agent (no identity, no reply contract). Refuse loudly.
    if (
        not brain_prompt.exists()
        or "# d-brain session contract" not in brain_prompt.read_text()
    ):
        raise RuntimeError(
            f"persona file missing or invalid: {brain_prompt} — "
            "refusing to start a personality-less brain"
        )
    env_prefix: dict[str, str] = {}
    if settings.todoist_api_key:
        env_prefix["TODOIST_API_KEY"] = settings.todoist_api_key
    return ClaudeSession(
        session_name=session_name,
        work_dir=vault,
        runtime_dir=runtime_dir,
        mcp_config=mcp if mcp.exists() else None,
        system_prompt_file=brain_prompt,
        model=settings.claude_model or None,
        env_prefix=env_prefix or None,
    )


def get_session(settings: Settings) -> ClaudeSession:
    """Return the shared interactive ClaudeSession singleton."""
    global _session
    if _session is None:
        _session = _build_session(
            settings,
            session_name=_persisted_name(settings),
            runtime_dir=settings.runtime_dir,
        )
    return _session


def get_cron_session(settings: Settings) -> ClaudeSession:
    """Return the cron brain — a second, isolated ClaudeSession.

    Same persona and vault as the main brain, but its own tmux session and
    its own runtime dir (cron_dir: pane.lock / pane.log / ready), so scheduled
    jobs never block on or pollute the user's live conversation.
    """
    global _cron_session
    if _cron_session is None:
        _cron_session = _build_session(
            settings,
            session_name=f"{_persisted_name(settings)}_cron",
            runtime_dir=settings.cron_dir,
        )
    return _cron_session


def get_processor(settings: Settings):
    """Return the shared ClaudeProcessor bound to the persistent session."""
    global _processor
    if _processor is None:
        from d_brain.services.processor import ClaudeProcessor

        _processor = ClaudeProcessor(
            settings.vault_path,
            settings.todoist_api_key,
            session=get_session(settings),
        )
    return _processor


def get_ask_lock() -> asyncio.Lock:
    return _ask_lock


def ask_text(
    prompt: str,
    *,
    timeout: float = 120.0,
    request_id: str | None = None,
    wrap: bool = True,
) -> str:
    """Blocking convenience for helper call sites (summaries, JSON extraction).

    Asks the shared persistent session and returns the reply text, or "" on any
    non-ok status / error. Run it inside ``asyncio.to_thread`` from async code.
    """
    from d_brain.config import get_settings

    try:
        res = get_session(get_settings()).ask(
            prompt, timeout=timeout, request_id=request_id, wrap=wrap
        )
        return (res.reply or "") if res.ok else ""
    except Exception:  # noqa: BLE001 — helper must never crash the caller
        logger.exception("ask_text failed")
        return ""
