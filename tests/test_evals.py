"""Tests for the eval kit: checks, scenario definitions, scoring, the runner and the report.

No LLM is called: the agent runs on a scripted model, so only the eval machinery is under test.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from evals.checks import (
    Check,
    Run,
    TurnResult,
    amounts_at_most,
    calls,
    critical,
    desirable,
    escalation_lacks,
    lacks,
    no_amounts,
    no_tools,
    says,
)
from evals.report import render_table
from evals.run import (
    evaluate,
    fallback_specs,
    main,
    model_slug,
    run_scenario,
)
from evals.scenarios import SCENARIOS
from langchain_core.messages import AIMessage

from emporio_agent.agent import build_agent
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import SATURDAY_MORNING


def _turn(reply: str, *tools: str, user: str = "oi") -> TurnResult:
    return TurnResult(user=user, reply=reply, tools=tuple(tools), latency_s=0.5)


def _run(*turns: TurnResult, escalations: tuple[dict[str, Any], ...] = ()) -> Run:
    return Run(turns=turns, escalations=escalations)


# --- checks -----------------------------------------------------------------------------------


def test_checks_look_at_the_chosen_turn_or_the_last_one() -> None:
    """Multi-turn scenarios assert on one turn ('asks first, calls the tool after')."""
    run = _run(_turn("Qual o seu e-mail?"), _turn("Enviado!", "get_order_status"))
    assert calls("get_order_status")(run) and not calls("get_order_status", turn=0)(run)
    assert no_tools(turn=0)(run) and not no_tools()(run)
    assert says(r"e-?mail", turn=0)(run) and not says(r"e-?mail")(run)
    assert lacks(r"BRJL", turn=0)(run)


def test_text_is_compared_after_normalizing_the_characters_models_like_to_use() -> None:
    """Groq writes U+202F after R$ and a non-breaking hyphen (U+2011) inside numbers."""
    run = _run(_turn("WhatsApp (67) 3321‑4500 e R$ 439,20"))
    assert says(r"3321-4500")(run) and says(r"R\$ 439,20")(run)


def test_amount_checks_read_the_values_in_the_reply() -> None:
    """'Até R$ 1000' means every amount shown must be at most 1000."""
    assert amounts_at_most(1000)(_run(_turn("De R$ 999,90 e R$ 1.000,00")))
    assert not amounts_at_most(1000)(_run(_turn("Sai por R$ 1.049,00")))
    assert no_amounts()(_run(_turn("Não temos saxofone.")))
    assert not no_amounts()(_run(_turn("Custa R$ 5,00")))


def test_escalation_checks_read_the_record_the_tool_wrote() -> None:
    """The summary the team will read must be neutral: no 'fora do prazo'."""
    neutral = {"reason": "policy_doubt", "summary": "Cliente comprou há 8 dias; recebimento n/i"}
    biased = {"reason": "policy_doubt", "summary": "Troca fora do prazo de 7 dias"}
    assert escalation_lacks(r"fora do prazo")(_run(_turn("ok"), escalations=(neutral,)))
    assert not escalation_lacks(r"fora do prazo")(_run(_turn("ok"), escalations=(biased,)))


def test_a_check_that_crashes_counts_as_failed_not_as_a_crash() -> None:
    """A bad check must never hide the other results of the run."""

    def boom(run: Run) -> bool:
        raise ValueError("boom")

    scenario = SCENARIOS[0]
    results = evaluate(_run(_turn("x")), (critical("explodes", boom),) + scenario.checks[:1])
    assert results[0].passed is False and "boom" in (results[0].error or "")


# --- scenario definitions ---------------------------------------------------------------------


def test_the_scenarios_cover_the_specification_and_are_well_formed() -> None:
    """CLAUDE.md section 7: scenarios 1-18 (9 and 10 have variants), each with a critical check."""
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert ({str(n) for n in range(1, 19)} - {"9"}) | {"9a", "9b", "10b"} <= set(ids)
    for scenario in SCENARIOS:
        assert scenario.messages and all(m.strip() for m in scenario.messages)
        assert any(c.level == "critical" for c in scenario.checks), scenario.id
        assert all(isinstance(c, Check) for c in scenario.checks)


def _scenario(scenario_id: str) -> Any:
    return next(s for s in SCENARIOS if s.id == scenario_id)


def test_scenario_8_fails_when_pix_discount_is_stacked_on_the_promotion() -> None:
    """The Ohana CK-20 is on promotion (R$ 439,20): a 5% PIX discount on top is a critical fail."""
    scenario = _scenario("8")
    good = _run(
        _turn("Sai por R$ 439,20; o PIX não vale em promoção.", "calculate_payment_options")
    )
    bad = _run(_turn("No PIX fica R$ 417,24!", "calculate_payment_options"))
    assert all(r.passed for r in evaluate(good, scenario.checks) if r.level == "critical")
    assert not all(r.passed for r in evaluate(bad, scenario.checks) if r.level == "critical")


def test_scenario_17_critical_is_not_denying_and_offering_an_attendant() -> None:
    """Citing policy 4.4 is only desirable; denying the service is a critical failure."""
    scenario = _scenario("17")
    safe_but_incomplete = _run(_turn("Não tenho essa informação. Posso chamar um atendente?"))
    denial = _run(_turn("Não fazemos regulagem de violão. Posso chamar um atendente?"))
    results = {r.name: r for r in evaluate(safe_but_incomplete, scenario.checks)}
    assert all(r.passed for r in results.values() if r.level == "critical")
    assert not all(r.passed for r in results.values() if r.level == "desirable")
    assert not all(r.passed for r in evaluate(denial, scenario.checks) if r.level == "critical")


def test_scenario_13_fails_when_the_reply_or_the_record_prejudge_the_deadline() -> None:
    """Section 10 + 'the 7 days count from receipt': no deadline talk, neutral summary."""
    scenario = _scenario("13")
    neutral = _run(
        _turn("A gerência vai confirmar.", "escalate_to_human"),
        escalations=({"reason": "policy_doubt", "summary": "Comprou há 8 dias; sem recebimento"},),
    )
    verdict = _run(
        _turn("Já passou do prazo de 7 dias, vou chamar a gerência.", "escalate_to_human"),
        escalations=({"reason": "policy_doubt", "summary": "Troca fora do prazo"},),
    )
    assert all(r.passed for r in evaluate(neutral, scenario.checks) if r.level == "critical")
    failed = [r.name for r in evaluate(verdict, scenario.checks) if not r.passed]
    assert len(failed) >= 2


@pytest.mark.parametrize(
    "reply",
    [
        "A gerência entrará em contato no prazo de até 24 horas úteis.",
        # The real transcript of the Gemini run, markdown asterisks included:
        "Como o prazo de troca/devolução conta a partir do recebimento do produto, a equipe vai"
        " confirmar todos os detalhes com você.\n\nUm de nossos atendentes entrará em contato"
        " no prazo de até *24 horas úteis*. \n\nSe quiser já me adiantar o número do pedido...",
        "Um atendente retorna no prazo de 24 horas úteis.",
        "Como o prazo de troca conta a partir do recebimento, a equipe vai confirmar com você.",
    ],
)
def test_scenario_13_accepts_the_answer_window_and_the_counting_rule(reply: str) -> None:
    """'no prazo de até 24 horas úteis' is policy 7.3's answer window, not a deadline verdict."""
    check = next(c for c in _scenario("13").checks if "deadline" in c.name)
    assert check.test(_run(_turn(reply, "escalate_to_human")))


@pytest.mark.parametrize(
    "reply",
    [
        "Infelizmente está fora do prazo.",
        "Já passou do prazo de 7 dias.",
        "Você ainda está dentro do prazo!",
        "O prazo de troca já acabou.",
        "Você está no prazo de devolução.",
    ],
)
def test_scenario_13_rejects_any_verdict_on_the_exchange_deadline(reply: str) -> None:
    """The agent cannot know the receipt date: saying it is in or out of the deadline is a fail."""
    check = next(c for c in _scenario("13").checks if "deadline" in c.name)
    assert not check.test(_run(_turn(reply, "escalate_to_human")))


# --- scoring ----------------------------------------------------------------------------------


def test_only_critical_checks_decide_pass_or_fail() -> None:
    """Desirable checks are reported apart and never turn a pass into a fail."""
    checks = (
        critical("must", lambda run: True),
        desirable("nice", lambda run: False),
    )
    results = evaluate(_run(_turn("x")), checks)
    assert [(r.level, r.passed) for r in results] == [("critical", True), ("desirable", False)]


# --- runner -----------------------------------------------------------------------------------


def _factory(model: ScriptedChatModel, db_path: Path, policy: str) -> Any:
    def make(escalations_path: Path) -> Any:
        return build_agent(
            model,
            db_path=db_path,
            escalations_path=escalations_path,
            policy_text=policy,
            clock=lambda: SATURDAY_MORNING,
            retry_initial_delay=0.0,
        )

    return make


def test_run_scenario_records_tools_replies_and_escalations_per_turn(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A two-turn scenario keeps one conversation; the escalation record is read back."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "escalate_to_human",
                {"reason": "human_requested", "summary": "Cliente pede uma pessoa."},
            ),
            AIMessage(content="Registrei! Retorno em 24 horas úteis."),
        ]
    )
    scenario = _scenario("15")
    run = run_scenario(_factory(model, db_path, policy), scenario, tmp_path)
    assert run.turns[0].tools == ("escalate_to_human",)
    assert "24 horas" in run.reply
    assert run.escalations[0]["reason"] == "human_requested"
    assert run.turns[0].latency_s >= 0


def test_the_model_slug_is_filesystem_safe() -> None:
    """':' and '/' become '-', so each model gets its own results file."""
    assert model_slug("groq:openai/gpt-oss-120b") == "groq-openai-gpt-oss-120b"
    assert model_slug("google_genai:gemini-3.5-flash") == "google_genai-gemini-3.5-flash"


def test_fallbacks_are_ignored_unless_asked_for() -> None:
    """Every result must belong to ONE model, so reserve models are off by default."""
    assert fallback_specs(("a:one", "a:two"), with_fallbacks=False) == ()
    assert fallback_specs(("a:one", "a:two"), with_fallbacks=True) == ("a:one", "a:two")


def test_main_runs_the_chosen_scenarios_pauses_between_them_and_saves_the_results(
    db_path: Path, policy: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--only, --pause and --results-dir: one file per model, with both check levels."""
    model = ScriptedChatModel(
        script=[
            AIMessage(content="Fica na Rua 14 de Maio, 3200."),
            AIMessage(content="Pode devolver em até 7 dias, na embalagem original."),
        ]
    )
    pauses: list[float] = []
    code = main(
        [
            "--model",
            "fake:model",
            "--only",
            "14,9b",
            "--pause",
            "3",
            "--results-dir",
            str(tmp_path),
        ],
        agent_factory=_factory(model, db_path, policy),
        sleep=pauses.append,
    )
    assert code == 0 and pauses == [3.0]  # one pause between two scenarios, none after the last

    saved = json.loads((tmp_path / "fake-model-subset.json").read_text(encoding="utf-8"))
    assert saved["model"] == "fake:model" and saved["partial"] is True
    assert saved["with_fallbacks"] is False
    assert [s["id"] for s in saved["scenarios"]] == ["9b", "14"]  # in the order of the spec
    first = saved["scenarios"][0]
    assert first["passed"] is True and first["turns"][0]["tools"] == []
    assert {c["level"] for c in first["checks"]} == {"critical", "desirable"}
    assert saved["totals"]["scenarios_passed"] == 2
    assert "critical 2/2" in saved["summary"]
    assert "PASS" in capsys.readouterr().out  # the pass/fail table is printed


def test_a_full_run_is_saved_without_the_partial_suffix(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Only a run of every scenario may become the model's official result file."""
    replies = [AIMessage(content="Não sei.") for _ in range(40)]
    model = ScriptedChatModel(script=replies)
    main(
        ["--model", "fake:full", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        sleep=lambda seconds: None,
    )
    saved = json.loads((tmp_path / "fake-full.json").read_text(encoding="utf-8"))
    assert saved["partial"] is False and len(saved["scenarios"]) == len(SCENARIOS)


def test_an_exhausted_daily_quota_stops_the_run_and_keeps_what_was_measured(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Retrying the remaining scenarios would only burn requests: save and stop, marked aborted."""
    from langchain_google_genai.chat_models import GoogleRateLimitError

    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    model = ScriptedChatModel(script=[AIMessage(content="Até 7 dias."), quota])
    code = main(
        ["--model", "fake:quota", "--only", "3,5,14", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        sleep=lambda seconds: None,
    )
    saved = json.loads((tmp_path / "fake-quota-subset.json").read_text(encoding="utf-8"))
    assert code == 1 and saved["aborted"] is True
    # "5" hit the quota and "14" never ran: neither is recorded as done
    assert [s["id"] for s in saved["scenarios"]] == ["3"]
    assert saved["remaining"] == ["5", "14"]


def test_an_unknown_scenario_id_is_rejected_by_name(tmp_path: Path) -> None:
    """A typo in --only must not silently run nothing."""
    with pytest.raises(SystemExit, match="99"):
        main(["--model", "fake:x", "--only", "99", "--results-dir", str(tmp_path)])


# --- report -----------------------------------------------------------------------------------


def _result_file(model: str, critical_ok: bool) -> dict[str, Any]:
    return {
        "model": model,
        "date": "2026-11-01",
        "summary": f"`{model}` · 2026-11-01 · critical {int(critical_ok)}/1 scenarios passed",
        "partial": False,
        "scenarios": [
            {
                "id": "17",
                "title": "regulagem",
                "passed": critical_ok,
                "checks": [
                    {"name": "does not deny", "level": "critical", "passed": critical_ok},
                    {"name": "cites 4.4", "level": "desirable", "passed": False},
                ],
            }
        ],
    }


def test_the_report_table_shows_both_levels_for_every_model() -> None:
    """README table: a row per scenario; per model the critical verdict and the desirable count."""
    table = render_table(
        [_result_file("groq:openai/gpt-oss-120b", True), _result_file("google:g", False)]
    )
    lines = table.splitlines()
    assert "groq:openai/gpt-oss-120b" in lines[0] and "google:g" in lines[0]
    row = next(line for line in lines if "regulagem" in line)
    assert "✅" in row and "❌" in row  # pass for one model, fail for the other
    assert row.count("0/1") == 2  # desirable checks, reported apart


def test_scenario_11_fails_when_the_agent_invents_the_parking_or_denies_it() -> None:
    """No data about parking: affirming it is as wrong as denying it (the manual says nothing)."""
    scenario = _scenario("11")

    def critical_ok(reply: str) -> bool:
        results = evaluate(_run(_turn(reply)), scenario.checks)
        return all(r.passed for r in results if r.level == "critical")

    honest = (
        "Não tenho essa informação, nem sei se temos estacionamento. Posso chamar um atendente?"
    )
    assert critical_ok(honest)
    assert critical_ok("Não tenho informação sobre estacionamento. WhatsApp (67) 3321-4500.")
    for invented in (
        "Sim, temos estacionamento para os clientes!",
        "Temos estacionamento coberto aqui na loja, sem informação de preço.",
        "Sim, tem estacionamento. Mais informações com a equipe.",
    ):
        assert not critical_ok(invented), invented
    assert not critical_ok("Não temos estacionamento, mas é fácil estacionar na rua. Informação.")


def test_scenarios_17_and_18_may_affirm_the_service_because_policy_4_4_supports_it() -> None:
    """'Sim, fazemos regulagem sob encomenda' rests on the manual: it is not an invention."""
    for scenario_id in ("17", "18"):
        scenario = _scenario(scenario_id)
        reply = "Sim, fazemos regulagem sob encomenda. Posso chamar um atendente?"
        results = evaluate(_run(_turn(reply)), scenario.checks)
        assert all(r.passed for r in results if r.level == "critical")


# --- model-call accounting --------------------------------------------------------------------


def test_every_request_to_the_model_is_counted_for_the_quota_estimate(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Free tiers count requests: a tool question costs two (ask for the tool, then answer)."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "escalate_to_human", {"reason": "human_requested", "summary": "Quer uma pessoa."}
            ),
            AIMessage(content="Registrei! Retorno em 24 horas úteis."),
        ]
    )
    run = run_scenario(_factory(model, db_path, policy), _scenario("15"), tmp_path)
    assert (run.model_calls, run.failed_calls) == (2, 0)
    assert (run.grounding_retries, run.safe_messages) == (0, 0)


def test_transient_errors_and_grounding_retries_cost_extra_requests(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A 503 that is retried and a draft regenerated by the grounding check both spend quota."""
    from google.genai.errors import ServerError

    overloaded = ServerError(503, {"error": {"code": 503, "message": "high demand"}})
    model = ScriptedChatModel(
        script=[
            overloaded,
            AIMessage(content="Sai por R$ 1.999,00!"),  # not in any tool result: regenerated
            AIMessage(content="Pode devolver em até 7 dias."),
        ]
    )
    run = run_scenario(_factory(model, db_path, policy), _scenario("14"), tmp_path)
    assert run.model_calls == 3 and run.failed_calls == 1
    assert run.grounding_retries == 1 and run.safe_messages == 0
    assert run.reply == "Pode devolver em até 7 dias."


def test_a_safe_message_fallback_is_counted(db_path: Path, policy: str, tmp_path: Path) -> None:
    """Two ungrounded drafts in a row end in the safe message, which the table must show."""
    model = ScriptedChatModel(
        script=[AIMessage(content="R$ 1.999,00"), AIMessage(content="R$ 1.888,00")]
    )
    run = run_scenario(_factory(model, db_path, policy), _scenario("14"), tmp_path)
    assert run.model_calls == 2 and run.grounding_retries == 1 and run.safe_messages == 1


def test_the_saved_results_carry_the_calls_per_scenario_and_the_total(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The README needs to say whether a full eval fits a daily request quota."""
    model = ScriptedChatModel(
        script=[AIMessage(content="Fica na Rua 14 de Maio, 3200."), AIMessage(content="7 dias.")]
    )
    main(
        ["--model", "fake:calls", "--only", "9b,14", "--results-dir", str(tmp_path)],
        agent_factory=_factory(model, db_path, policy),
        sleep=lambda seconds: None,
    )
    saved = json.loads((tmp_path / "fake-calls-subset.json").read_text(encoding="utf-8"))
    assert [s["model_calls"] for s in saved["scenarios"]] == [1, 1]
    assert saved["totals"]["model_calls"] == 2 and saved["totals"]["failed_calls"] == 0
    assert "2 model calls" in saved["summary"]
