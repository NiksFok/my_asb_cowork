"""Bot FSM states."""

from aiogram.fsm.state import State, StatesGroup


class DoCommandState(StatesGroup):
    """States for the /do request flow."""

    waiting_for_input = State()  # Waiting for the first voice/text after /do
    in_conversation = State()    # Follow-up turns continue the same session


class EditModeState(StatesGroup):
    """States for edit mode (batch corrections)."""

    collecting = State()   # Collecting voice/text edit instructions
    confirming = State()   # Waiting for user to confirm preview


class AgentSessionState(StatesGroup):
    """States for interactive Claude session."""

    in_session = State()          # Active session, waiting for user commands
    awaiting_permission = State() # Claude paused, waiting for user approval


class SettingsState(StatesGroup):
    """States for Settings menu flows."""

    waiting_for_city = State()  # Waiting for user to type a new city name
