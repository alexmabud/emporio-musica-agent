"""The 👍/👎 votes customers give to answers in the Streamlit UI, kept in a local JSON Lines file.

One record per answer, keyed by conversation and turn: voting again replaces the record (the last
vote wins) and taking the vote back removes it. E-mails and phones are masked before anything is
written, the customer's own comment included, so the file holds no raw personal data (LGPD).
"""

import json
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from emporio_agent.chat import mask_pii
from emporio_agent.hours import STORE_TIMEZONE

Rating = Literal["up", "down"]

# Streamlit runs every browser session in a thread of one process; the file is rewritten whole.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Feedback:
    """A customer's vote on one answer.

    Attributes:
        thread_id: The conversation the answer belongs to.
        turn: Position of the answer in that conversation, starting at 1.
        question: What the customer asked.
        reply: What the assistant answered.
        tools: Names of the tools the agent called to answer.
        rating: ``up``, ``down`` or ``None`` when the customer took the vote back.
        comment: What went wrong, in the customer's words (only asked after a 👎).
    """

    thread_id: str
    turn: int
    question: str
    reply: str
    tools: tuple[str, ...]
    rating: Rating | None
    comment: str = ""


def rating_from_widget(value: int | None) -> Rating | None:
    """Translate ``st.feedback("thumbs")``: ``1`` is 👍, ``0`` is 👎, ``None`` is no vote."""
    return None if value is None else ("up" if value == 1 else "down")


def _key(record: dict[str, Any]) -> tuple[Any, Any]:
    return record["thread_id"], record["turn"]


def save_feedback(path: Path, vote: Feedback, *, now: datetime | None = None) -> None:
    """Store a vote, replacing any earlier vote on the same answer.

    Args:
        path: The JSON Lines file (created, with its folder, when missing).
        vote: The vote; with ``rating=None`` the record of that answer is removed instead.
        now: The moment to stamp (the store's time zone); the current time by default.
    """
    stamp = (now or datetime.now(STORE_TIMEZONE)).isoformat(timespec="seconds")
    record = {
        "created_at": stamp,
        **asdict(vote),
        "question": mask_pii(vote.question),
        "reply": mask_pii(vote.reply),
        "tools": list(vote.tools),
        "comment": mask_pii(vote.comment),
    }
    with _LOCK:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        records = [json.loads(line) for line in lines if line.strip()]
        records = [r for r in records if _key(r) != _key(record)]
        if vote.rating is not None:
            records.append(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as handle:
            handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        Path(handle.name).replace(path)
