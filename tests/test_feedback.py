"""Tests for the 👍/👎 store: what is saved, what is masked, how a changed vote replaces the old."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from emporio_agent.feedback import Feedback, rating_from_widget, save_feedback
from tests.helpers import CG

NOW = datetime(2026, 11, 2, 10, 30, 5, tzinfo=CG)


def _vote(**changes: Any) -> Feedback:
    base: dict[str, Any] = {
        "thread_id": "abc123",
        "turn": 1,
        "question": "Quanto custa o Takamine GD20?",
        "reply": "Sai por R$ 2.199,00.",
        "tools": ("get_product_details",),
        "rating": "up",
        "comment": "",
    }
    return Feedback(**(base | changes))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_vote_is_saved_with_the_question_the_reply_and_the_tools(tmp_path: Path) -> None:
    """One JSON line carries what is needed to turn a poorly rated answer into an eval scenario."""
    path = tmp_path / "nested" / "feedback.jsonl"  # the folder is created on demand
    save_feedback(path, _vote(), now=NOW)
    assert _lines(path) == [
        {
            "created_at": "2026-11-02T10:30:05-04:00",
            "thread_id": "abc123",
            "turn": 1,
            "question": "Quanto custa o Takamine GD20?",
            "reply": "Sai por R$ 2.199,00.",
            "tools": ["get_product_details"],
            "rating": "up",
            "comment": "",
        }
    ]


def test_emails_and_phones_are_masked_in_the_question_the_reply_and_the_comment(
    tmp_path: Path,
) -> None:
    """LGPD: personal data never reaches the file, comment included (the customer types it)."""
    path = tmp_path / "feedback.jsonl"
    save_feedback(
        path,
        _vote(
            question="Meu e-mail é anacarol.ferreira@coldmail.com",
            reply="Achei o pedido de (67) 99654-7821.",
            rating="down",
            comment="Liguem para 67996547821 ou escrevam para ana@x.com",
        ),
        now=NOW,
    )
    saved = path.read_text(encoding="utf-8")
    for raw in ("anacarol.ferreira", "99654-7821", "67996547821", "ana@x.com"):
        assert raw not in saved
    record = _lines(path)[0]
    assert record["question"].endswith("an***@coldmail.com")
    assert "(67) 9****-7821" in record["reply"] and "(67) 9****-7821" in record["comment"]


def test_one_rating_per_answer_the_last_vote_wins(tmp_path: Path) -> None:
    """Changing 👍 to 👎 (and adding a comment) replaces the record; it does not add a second."""
    path = tmp_path / "feedback.jsonl"
    save_feedback(path, _vote(rating="up"), now=NOW)
    save_feedback(path, _vote(rating="down", comment="Faltou o preço"), now=NOW)
    records = _lines(path)
    assert len(records) == 1
    assert records[0]["rating"] == "down" and records[0]["comment"] == "Faltou o preço"


def test_other_answers_and_other_conversations_keep_their_own_votes(tmp_path: Path) -> None:
    """The key is (conversation, turn): neighbors are untouched by a changed vote."""
    path = tmp_path / "feedback.jsonl"
    save_feedback(path, _vote(turn=1), now=NOW)
    save_feedback(path, _vote(turn=2), now=NOW)
    save_feedback(path, _vote(thread_id="other", turn=1), now=NOW)
    save_feedback(path, _vote(turn=2, rating="down"), now=NOW)
    assert [(r["thread_id"], r["turn"], r["rating"]) for r in _lines(path)] == [
        ("abc123", 1, "up"),
        ("other", 1, "up"),
        ("abc123", 2, "down"),
    ]


def test_taking_the_vote_back_removes_the_record(tmp_path: Path) -> None:
    """Clicking the same thumb again clears the rating: nothing is kept for that answer."""
    path = tmp_path / "feedback.jsonl"
    save_feedback(path, _vote(turn=1), now=NOW)
    save_feedback(path, _vote(turn=2), now=NOW)
    save_feedback(path, _vote(turn=1, rating=None), now=NOW)
    assert [r["turn"] for r in _lines(path)] == [2]
    save_feedback(
        tmp_path / "never-written.jsonl", _vote(rating=None), now=NOW
    )  # no file, no crash


def test_the_widget_value_maps_to_a_rating() -> None:
    """``st.feedback("thumbs")`` returns 1 for 👍, 0 for 👎 and ``None`` when nothing is chosen."""
    assert rating_from_widget(1) == "up"
    assert rating_from_widget(0) == "down"
    assert rating_from_widget(None) is None
