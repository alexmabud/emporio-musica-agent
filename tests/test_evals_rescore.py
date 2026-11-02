"""Tests for ``--rescore``: scoring a saved result again with the current checks, no model call.

A check that turns out to be wrong must be fixable without spending another day of quota, and
without touching the replies: the transcripts stay, only the verdicts change, and the change is
logged with the version of the checks that produced it.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from evals.checks import critical, says
from evals.rescore import format_comparison, rescore_document
from evals.run import main
from evals.scenarios import SCENARIOS
from evals.version import checks_version
from langchain_core.messages import AIMessage

from tests.fakes import ScriptedChatModel
from tests.test_evals import _factory
from tests.test_evals_resume import QUOTA

THREE = [s for s in SCENARIOS if s.id in {"3", "9b", "14"}]
ANSWERS = [
    "Pode devolver em até 7 dias.",
    "Fica na Rua 14 de Maio, 3200.",
    "Pode devolver em até 7 dias, na embalagem original.",
]
V1 = {"model": "fake:m", "prompt_sha": "p1", "tools_sha": "t1"}


def _too_strict(scenarios: list[Any]) -> list[Any]:
    """The same scenarios, but scenario 3 has a wrong critical check (it asks for 'garantia')."""
    wrong = (critical("mentions the warranty", says("garantia")),)
    return [replace(s, checks=wrong) if s.id == "3" else s for s in scenarios]


def _complete_result(tmp_path: Path, db_path: Path, policy: str) -> dict[str, Any]:
    """A complete scripted run whose scenario 3 is failed by the too-strict check.

    The file is stamped with another ``checks_sha``, as if an older version of the checks had
    scored it (the injected scenarios live in the same source files as the current ones).
    """
    model = ScriptedChatModel(script=[AIMessage(content=text) for text in ANSWERS])
    main(
        ["--model", "fake:m", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        version=V1,
        scenarios=_too_strict(THREE),
        sleep=lambda seconds: None,
    )
    path = tmp_path / "fake-m.json"
    document = json.loads(path.read_text(encoding="utf-8")) | {"checks_sha": "older-checks"}
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def _rescore(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Run ``--rescore`` with the real checks; no agent is injected, so no model can be called."""
    assert (
        main(["--model", "fake:m", "--rescore", "--results-dir", str(tmp_path)], scenarios=THREE)
        == 0
    )
    return capsys.readouterr().out


def test_rescoring_changes_the_verdicts_but_not_the_replies_and_logs_it(
    db_path: Path, policy: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scenario 3 flips from FAIL to PASS; replies, transcripts and counters stay as saved."""
    before = _complete_result(tmp_path, db_path, policy)
    assert before["totals"]["scenarios_passed"] == 2 and before["scenarios"][0]["passed"] is False

    out = _rescore(tmp_path, capsys)
    after = json.loads((tmp_path / "fake-m.json").read_text(encoding="utf-8"))

    assert after["scenarios"][0]["passed"] is True and after["totals"]["scenarios_passed"] == 3
    for old, new in zip(before["scenarios"], after["scenarios"], strict=True):
        assert old["turns"] == new["turns"] and old["transcript"] == new["transcript"]
        assert old["model_calls"] == new["model_calls"] and old["date"] == new["date"]
    assert after["totals"]["model_calls"] == before["totals"]["model_calls"]
    assert after["checks_sha"] == checks_version() and before["checks_sha"] == "older-checks"
    entry = after["rescoring"][0]
    assert entry["from_checks_sha"] == "older-checks"
    assert entry["to_checks_sha"] == checks_version()
    assert {c["scenario"] for c in entry["changes"]} == {"3"}  # only the scenario with new checks
    assert all(c["before"] is None for c in entry["changes"])  # the wrong check is gone
    assert any(c["level"] == "critical" and c["after"] is True for c in entry["changes"])
    assert "critical 3/3" in after["summary"] and "re-scored" in after["summary"]
    assert "FAIL" in out and "PASS" in out and "<- changed" in out  # before/after table


def test_rescoring_calls_no_model_and_needs_no_agent(
    db_path: Path, policy: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--rescore builds no agent at all: nothing to call, no API key needed."""
    _complete_result(tmp_path, db_path, policy)
    _rescore(tmp_path, capsys)  # _rescore passes no agent_factory; a model call would fail loudly


def test_rescoring_twice_with_the_same_checks_changes_nothing(
    db_path: Path, policy: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second pass finds the file already scored by these checks and leaves it alone."""
    _complete_result(tmp_path, db_path, policy)
    _rescore(tmp_path, capsys)
    saved = (tmp_path / "fake-m.json").read_text(encoding="utf-8")
    assert "already scored" in _rescore(tmp_path, capsys)
    assert (tmp_path / "fake-m.json").read_text(encoding="utf-8") == saved


def test_rescoring_refuses_when_there_is_no_complete_result_or_a_scenario_changed(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Partial and subset files are not results; edited scenario messages cannot be re-scored."""
    with pytest.raises(SystemExit, match="Nothing to re-score"):
        main(["--model", "fake:m", "--rescore", "--results-dir", str(tmp_path)], scenarios=THREE)

    document = _complete_result(tmp_path, db_path, policy)
    edited = [replace(s, messages=("outra pergunta",)) if s.id == "9b" else s for s in THREE]
    with pytest.raises(ValueError, match="other messages"):
        rescore_document(document, edited, today="2026-11-02", checks_sha="x")
    with pytest.raises(ValueError, match="no longer exists"):
        rescore_document(document, THREE[:1], today="2026-11-02", checks_sha="x")


def test_rescore_cannot_be_combined_with_options_that_run_the_model(tmp_path: Path) -> None:
    """--rescore only re-scores; --only/--resume/--restart would make it ambiguous."""
    for flag in ("--only=3", "--resume", "--restart"):
        with pytest.raises(SystemExit):
            main(["--model", "fake:m", "--rescore", flag, "--results-dir", str(tmp_path)])


def test_the_comparison_table_marks_only_the_scenarios_whose_result_changed(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Critical verdict and desirable passed/total, before and after, per scenario."""
    before = _complete_result(tmp_path, db_path, policy)
    after = rescore_document(before, THREE, today="2026-11-02", checks_sha=checks_version())
    rows = format_comparison(before, after).splitlines()
    changed = [row for row in rows if "<- changed" in row]
    assert len(changed) == 1 and changed[0].startswith("3 ") and "FAIL" in changed[0]


def test_resuming_scores_the_kept_scenarios_with_the_current_checks(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The partial run's first scenario was scored by a wrong check; resume fixes its verdict."""
    model = ScriptedChatModel(script=[AIMessage(content=ANSWERS[0]), QUOTA])
    main(
        ["--model", "fake:m", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        version=V1,
        scenarios=_too_strict(THREE),
        sleep=lambda seconds: None,
    )
    partial = json.loads((tmp_path / "fake-m-partial.json").read_text(encoding="utf-8"))
    assert partial["scenarios"][0]["passed"] is False

    model = ScriptedChatModel(script=[AIMessage(content=t) for t in ANSWERS[1:]])
    main(
        ["--model", "fake:m", "--resume", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        version=V1,
        scenarios=THREE,
        sleep=lambda seconds: None,
    )
    full = json.loads((tmp_path / "fake-m.json").read_text(encoding="utf-8"))
    assert [s["passed"] for s in full["scenarios"]] == [True, True, True]
    assert full["checks_sha"] == checks_version()
