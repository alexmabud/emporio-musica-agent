"""Tests for resuming a complete eval across days, and the version lock that protects it.

Free tiers cap the requests per day, so a complete eval may take several days. Progress is saved
after every scenario; a later run with ``--resume`` skips what is done, but only if the prompt, the
tool schemas and the model are exactly the ones that produced the saved results.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from evals.run import main
from evals.scenarios import SCENARIOS
from evals.version import compute_version, strip_moment
from google.genai.errors import ClientError
from langchain_core.messages import AIMessage
from langchain_google_genai.chat_models import GoogleRateLimitError

from emporio_agent.prompts import build_system_prompt
from emporio_agent.tools import build_tools
from tests.fakes import ScriptedChatModel, tool_call
from tests.test_evals import _factory

CG = ZoneInfo("America/Campo_Grande")
QUOTA = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
THREE = [s for s in SCENARIOS if s.id in {"3", "9b", "14"}]  # in the order of the spec: 3, 9b, 14
ANSWERS = {
    "3": "Pode devolver em até 7 dias.",
    "9b": "Fica na Rua 14 de Maio, 3200.",
    "14": "Pode devolver em até 7 dias, na embalagem original.",
}
V1 = {"model": "fake:m", "prompt_sha": "p1", "tools_sha": "t1"}


def _session(
    tmp_path: Path,
    db_path: Path,
    policy: str,
    script: list[Any],
    *flags: str,
    version: dict[str, str] | None = None,
) -> tuple[int, ScriptedChatModel]:
    """One run of the three scenarios on a scripted model; returns the exit code and the model."""
    model = ScriptedChatModel(script=script)
    code = main(
        ["--model", "fake:m", "--results-dir", str(tmp_path), *flags],
        agent_factory=_factory(model, db_path, policy),
        version=version or V1,
        scenarios=THREE,
        sleep=lambda seconds: None,
    )
    return code, model


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- the version lock ---------------------------------------------------------------------------


def test_the_prompt_part_of_the_version_ignores_the_date_and_the_store_status(policy: str) -> None:
    """Two days later the prompt only differs in the 'Agora' section: it is the same version."""
    monday = datetime(2026, 11, 2, 10, 0, tzinfo=CG)
    sunday = datetime(2026, 11, 8, 15, 0, tzinfo=CG)  # store closed
    first, second = build_system_prompt(policy, monday), build_system_prompt(policy, sunday)
    assert first != second
    assert strip_moment(first) == strip_moment(second)
    assert "Data e hora" not in strip_moment(first) and "## Regras" in strip_moment(first)


def test_the_version_changes_with_the_prompt_the_tool_schemas_and_the_model(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Any of the three differing means the saved results no longer describe this agent."""
    tools = build_tools(db_path, tmp_path / "e.jsonl")
    base = compute_version(policy, tools, "fake:m")
    assert compute_version(policy, build_tools(db_path, tmp_path / "x.jsonl"), "fake:m") == base

    assert (
        compute_version(policy + " nova regra", tools, "fake:m")["prompt_sha"] != base["prompt_sha"]
    )
    assert compute_version(policy, tools, "fake:other")["model"] == "fake:other"

    edited = build_tools(db_path, tmp_path / "e.jsonl")
    edited[0].description += " (changed)"
    changed = compute_version(policy, edited, "fake:m")
    assert changed["tools_sha"] != base["tools_sha"] and changed["prompt_sha"] == base["prompt_sha"]


# --- progress saved and resumed -----------------------------------------------------------------


def test_a_run_that_hits_the_daily_quota_stops_clean_and_keeps_what_is_done(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Two scenarios finished, the third found the quota gone: not recorded, to run next day."""
    code, _ = _session(
        tmp_path,
        db_path,
        policy,
        [AIMessage(content=ANSWERS["3"]), AIMessage(content=ANSWERS["9b"]), QUOTA],
    )
    assert code == 1
    partial = _read(tmp_path / "fake-m-partial.json")
    assert [s["id"] for s in partial["scenarios"]] == ["3", "9b"]
    assert partial["remaining"] == ["14"] and partial["aborted"] is True
    assert partial["fingerprint"] == V1
    assert not (tmp_path / "fake-m.json").exists()  # no full result yet


def test_resuming_runs_only_what_is_missing_and_then_writes_the_full_result(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Day two runs only what is missing; the full file appears and the partial goes."""
    _session(tmp_path, db_path, policy, [AIMessage(content=ANSWERS["3"]), QUOTA])
    code, second = _session(
        tmp_path,
        db_path,
        policy,
        [AIMessage(content=ANSWERS["9b"]), AIMessage(content=ANSWERS["14"])],
        "--resume",
    )

    assert code == 0 and len(second.seen) == 2  # 9b and 14 only; scenario 3 was not asked again
    full = _read(tmp_path / "fake-m.json")
    assert [s["id"] for s in full["scenarios"]] == ["3", "9b", "14"]  # in the order of the spec
    assert full["partial"] is False and full["aborted"] is False
    assert full["totals"]["scenarios_run"] == 3 and full["totals"]["model_calls"] == 3
    assert [len(session["scenarios"]) for session in full["sessions"]] == [1, 2]
    assert not (tmp_path / "fake-m-partial.json").exists()


def test_progress_is_saved_after_every_scenario_not_only_at_the_end(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A crash (here: a non-quota error that kills the process) must not lose finished scenarios."""

    class Crash(BaseException):
        """Stands for the process dying (Ctrl+C, power loss): not caught by the runner."""

    model = ScriptedChatModel(script=[AIMessage(content=ANSWERS["3"]), Crash()])
    with pytest.raises(Crash):
        main(
            ["--model", "fake:m", "--results-dir", str(tmp_path)],
            agent_factory=_factory(model, db_path, policy),
            version=V1,
            scenarios=THREE,
            sleep=lambda seconds: None,
        )
    assert [s["id"] for s in _read(tmp_path / "fake-m-partial.json")["scenarios"]] == ["3"]


def test_resuming_with_a_different_version_is_refused_and_says_what_changed(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Mixing results from two prompts would make the table lie: refuse, name the part."""
    _session(tmp_path, db_path, policy, [AIMessage(content=ANSWERS["3"]), QUOTA])
    for changed, part in (
        ({**V1, "prompt_sha": "p2"}, "prompt"),
        ({**V1, "tools_sha": "t2"}, "tool schemas"),
        ({**V1, "model": "fake:other"}, "model"),
    ):
        with pytest.raises(SystemExit, match=part):
            _session(tmp_path, db_path, policy, [], "--resume", version=changed)
    assert [s["id"] for s in _read(tmp_path / "fake-m-partial.json")["scenarios"]] == ["3"]


def test_restart_discards_the_saved_progress_and_runs_everything_again(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """After a deliberate change of the agent, --restart begins a new round on the new version."""
    _session(tmp_path, db_path, policy, [AIMessage(content=ANSWERS["3"]), QUOTA])
    new = {**V1, "prompt_sha": "p2"}
    script = [AIMessage(content=ANSWERS[i]) for i in ("3", "9b", "14")]
    code, model = _session(tmp_path, db_path, policy, script, "--restart", version=new)
    assert code == 0 and len(model.seen) == 3
    assert _read(tmp_path / "fake-m.json")["fingerprint"] == new


def test_an_existing_partial_run_is_never_overwritten_by_accident(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A run without --resume or --restart must not silently throw a day of quota away."""
    _session(tmp_path, db_path, policy, [AIMessage(content=ANSWERS["3"]), QUOTA])
    with pytest.raises(SystemExit, match="--resume"):
        _session(tmp_path, db_path, policy, [])
    assert [s["id"] for s in _read(tmp_path / "fake-m-partial.json")["scenarios"]] == ["3"]


def test_a_subset_is_a_separate_file_and_cannot_be_resumed(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """--only is for quick looks: its own file, no merge with the progress of the full run."""
    _session(tmp_path, db_path, policy, [AIMessage(content=ANSWERS["3"]), QUOTA])
    code, _ = _session(
        tmp_path, db_path, policy, [AIMessage(content=ANSWERS["14"])], "--only", "14"
    )
    assert code == 0
    assert _read(tmp_path / "fake-m-subset.json")["partial"] is True
    assert [s["id"] for s in _read(tmp_path / "fake-m-partial.json")["scenarios"]] == ["3"]
    with pytest.raises(SystemExit):
        _session(tmp_path, db_path, policy, [], "--only", "14", "--resume")


def test_a_scenario_that_fails_for_another_reason_is_recorded_not_retried_forever(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A model error that is not a quota counts as that scenario failing (it is evidence)."""
    broken = ClientError(400, {"error": {"code": 400, "message": "invalid argument"}})
    script = [broken, AIMessage(content=ANSWERS["9b"]), AIMessage(content=ANSWERS["14"])]
    code, _ = _session(tmp_path, db_path, policy, script)
    full = _read(tmp_path / "fake-m.json")
    first = full["scenarios"][0]
    assert code == 0 and first["id"] == "3" and first["passed"] is False and "error" in first


# --- transcripts ---------------------------------------------------------------------------------


def test_every_scenario_keeps_the_full_transcript_with_tool_calls_and_results(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A failure must be readable afterwards: who said what, which tool, with which arguments."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "escalate_to_human", {"reason": "human_requested", "summary": "Quer uma pessoa."}
            ),
            AIMessage(content="Registrei! Retorno em 24 horas úteis."),
        ]
    )
    main(
        ["--model", "fake:m", "--only", "15", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        version=V1,
        sleep=lambda seconds: None,
    )
    transcript = _read(tmp_path / "fake-m-subset.json")["scenarios"][0]["transcript"]
    assert [m["role"] for m in transcript] == ["user", "assistant", "tool", "assistant"]
    assert transcript[0]["content"] == "Quero falar com uma pessoa"
    assert transcript[1]["tool_calls"] == [
        {
            "name": "escalate_to_human",
            "args": {"reason": "human_requested", "summary": "Quer uma pessoa."},
        }
    ]
    assert transcript[2]["name"] == "escalate_to_human" and "registered" in transcript[2]["content"]
    assert "24 horas" in transcript[3]["content"]
