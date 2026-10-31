"""Shared helpers for tests that build an agent on a scripted model."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from emporio_agent.agent import build_agent
from tests.fakes import ScriptedChatModel

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CG = ZoneInfo("America/Campo_Grande")
SATURDAY_MORNING = datetime(2026, 10, 31, 10, 30, tzinfo=CG)


def make_agent(
    model: ScriptedChatModel, db_path: Path, policy: str, tmp_path: Path, **kwargs: Any
) -> Any:
    """Build an agent on the scripted model with a fixed clock and no retry delay."""
    kwargs.setdefault("clock", lambda: SATURDAY_MORNING)
    return build_agent(
        model,
        db_path=db_path,
        escalations_path=tmp_path / "escalations.jsonl",
        policy_text=policy,
        retry_initial_delay=0.0,
        **kwargs,
    )


def ask(agent: Any, text: str, thread: str = "t1") -> dict[str, Any]:
    """Send one customer message on a conversation thread."""
    return agent.invoke(
        {"messages": [("user", text)]}, config={"configurable": {"thread_id": thread}}
    )
