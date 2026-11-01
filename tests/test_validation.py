"""Tests for the grounding check: the pure functions and the middleware inside the agent."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from emporio_agent.agent import reply_text
from emporio_agent.validation import (
    SAFE_MESSAGE,
    amounts_in,
    correction_note,
    find_ungrounded,
    tracking_codes_in,
)
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import ask, make_agent

NNBSP = " "  # narrow no-break space: what the Groq gpt-oss models write after "R$"
NBSP = " "

TAKAMINE = '{"final_price": "R$ 2.199,00", "pix_price": "R$ 2.089,05"}'


def test_amounts_are_compared_by_value_not_by_spelling() -> None:
    """R$ 1.000, R$1000 and R$ 1.000,00 are the same amount; so are the odd space characters."""
    assert amounts_in("R$ 1.000") == amounts_in("R$1000") == amounts_in("R$ 1.000,00")
    assert amounts_in(f"custa R${NNBSP}2.199,00 ou R${NBSP}87,84") == amounts_in(
        "R$ 2.199,00 e R$ 87,84"
    )
    assert len(amounts_in("R$ 2.199,00 e R$ 87,84")) == 2


@pytest.mark.parametrize(
    ("written", "value"),
    [
        ("R$ 1.000,50", "1000.50"),  # Brazilian: dot groups thousands, comma has the cents
        ("R$ 1,000.50", "1000.50"),  # American: the last separator is the decimal one
        ("R$ 1.000", "1000"),  # dot + exactly 3 digits: thousands
        ("R$ 12.500", "12500"),
        ("R$ 1.234.567", "1234567"),
        ("R$ 439.20", "439.20"),  # dot + 2 digits at the end, no comma: decimal
        ("R$ 439.2", "439.2"),
        ("R$ 439,20", "439.20"),
        ("R$ 439", "439"),
        ("R$ 1,000", "1000"),  # comma + exactly 3 digits is not cents: thousands
        ("R$ 500,00.", "500.00"),  # the final dot belongs to the sentence
    ],
)
def test_amount_formats_are_read_by_value(written: str, value: str) -> None:
    """Thousands dots are groups of exactly 3 digits; anything else after a dot is the decimal."""
    assert amounts_in(written) == {Decimal(value)}


def test_a_dot_decimal_price_is_grounded_by_the_tool_amount_with_a_comma() -> None:
    """R$ 439.20 is the tool's R$ 439,20 written another way, so it passes."""
    tool = '{"unit_price": "R$ 439,20"}'
    assert find_ungrounded("Sai por R$ 439.20.", [tool], [], "") == []


def test_an_amount_missing_its_cents_is_not_grounded_by_a_price_with_cents() -> None:
    """R$ 439 is not R$ 439,20; only a tool amount of 439,00 would support it."""
    assert find_ungrounded("Sai por R$ 439.", ['{"p": "R$ 439,20"}'], [], "") == ["R$ 439"]
    assert find_ungrounded("Sai por R$ 439.", ['{"p": "R$ 439,00"}'], [], "") == []


def test_the_correction_note_asks_for_the_brazilian_format() -> None:
    """The retry tells the model how to write amounts, so the second draft parses unambiguously."""
    note = correction_note(["R$ 439.20"])
    assert "R$ 439.20" in note and "R$ 1.234,56" in note


def test_a_price_that_no_source_contains_is_ungrounded() -> None:
    """The model must not state an amount that neither a tool, the manual nor the customer gave."""
    bad = find_ungrounded(
        "O Takamine sai por R$ 1.999,00!",
        tool_texts=[TAKAMINE],
        customer_texts=["quanto custa o Takamine?"],
        policy_text="",
    )
    assert bad == ["R$ 1.999,00"]


def test_a_price_from_a_tool_result_is_grounded_whatever_the_spaces() -> None:
    """The gpt-oss models write R$ + U+202F; it must still match the tool's plain space."""
    reply = f"No PIX fica R${NNBSP}2.089,05 e no cartão R$ 2.199,00."
    assert find_ungrounded(reply, [TAKAMINE], [], "") == []


def test_amounts_from_the_manual_or_the_customer_are_grounded() -> None:
    """Frete grátis acima de R$ 500 comes from the manual; 'até 1000' from the customer."""
    manual = "Frete grátis para compras acima de R$ 500,00."
    assert find_ungrounded("Acima de R$ 500 o frete é grátis.", [], [], manual) == []
    assert find_ungrounded("Separei violões até R$ 1.000.", [], ["violões até 1000"], "") == []
    assert find_ungrounded("Separei violões até R$ 1.000.", [], ["violões até R$1000"], "") == []


def test_a_tracking_code_must_come_from_a_tool_result() -> None:
    """Tracking codes are copied or absent, never invented."""
    assert tracking_codes_in("Rastreio: BRJL5544332BR (Correios)") == {"BRJL5544332BR"}
    tool = '{"tracking_code": "BRJL5544332BR"}'
    assert find_ungrounded("Rastreio BRJL5544332BR", [tool], [], "") == []
    assert find_ungrounded("Rastreio BRXX1234567BR", [tool], [], "") == ["BRXX1234567BR"]


def test_a_reply_without_amounts_or_codes_is_always_grounded() -> None:
    """Greetings and policy answers have nothing to check."""
    assert find_ungrounded("Oi! Como posso ajudar?", [], [], "") == []


def test_the_safe_message_states_no_amount_and_offers_the_whatsapp_number() -> None:
    """The fallback must pass its own check and send the customer to a human."""
    assert amounts_in(SAFE_MESSAGE) == set()
    assert "(67) 3321-4500" in SAFE_MESSAGE


# --- the middleware inside the agent --------------------------------------------------------


def test_a_grounded_answer_goes_through_untouched(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The price in the reply comes from a tool result: no extra model call is made."""
    model = ScriptedChatModel(
        script=[
            tool_call("get_product_details", {"product_name": "Takamine GD20"}),
            AIMessage(content="O Takamine GD20 sai por R$ 2.199,00."),
        ]
    )
    result = ask(make_agent(model, db_path, policy, tmp_path), "Quanto custa o Takamine GD20?")
    assert reply_text(result) == "O Takamine GD20 sai por R$ 2.199,00."
    assert len(model.seen) == 2


def test_an_invented_price_is_regenerated_once_with_a_correction_note(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Price quoted without a lookup: the model gets a note naming the value and tries again."""
    model = ScriptedChatModel(
        script=[
            AIMessage(content="O Takamine GD20 sai por R$ 1.999,00!"),
            tool_call("get_product_details", {"product_name": "Takamine GD20"}),
            AIMessage(content="O Takamine GD20 sai por R$ 2.199,00."),
        ]
    )
    result = ask(make_agent(model, db_path, policy, tmp_path), "Quanto custa o Takamine GD20?")

    assert reply_text(result) == "O Takamine GD20 sai por R$ 2.199,00."
    note = model.seen[1][-1]
    assert note.type == "human" and "R$ 1.999,00" in note.text
    assert not any("1.999" in m.text for m in model.seen[1][:-1])  # the bad draft is not re-sent


def test_the_correction_notes_and_bad_drafts_do_not_stay_in_the_history(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Later turns must not see the ungrounded draft or a fake 'customer' message."""
    model = ScriptedChatModel(
        script=[
            AIMessage(content="Sai por R$ 1.999,00!"),
            tool_call("get_product_details", {"product_name": "Takamine GD20"}),
            AIMessage(content="O Takamine GD20 sai por R$ 2.199,00."),
            AIMessage(content="De nada!"),
        ]
    )
    agent = make_agent(model, db_path, policy, tmp_path)
    ask(agent, "Quanto custa o Takamine GD20?")
    ask(agent, "Obrigado")

    last_call = model.seen[-1]
    assert [m.text for m in last_call if m.type == "human"] == [
        "Quanto custa o Takamine GD20?",
        "Obrigado",
    ]
    assert not any("1.999" in m.text for m in last_call)


def test_after_the_retries_the_customer_gets_the_safe_message(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Still ungrounded after the one retry: no amount reaches the customer."""
    model = ScriptedChatModel(
        script=[
            AIMessage(content="Sai por R$ 1.999,00!"),
            AIMessage(content="Sai por R$ 1.888,00!"),
        ]
    )
    result = ask(make_agent(model, db_path, policy, tmp_path), "Quanto custa o Takamine GD20?")

    assert reply_text(result) == SAFE_MESSAGE
    assert len(model.seen) == 2
    assert not any("1.888" in str(m.content) for m in result["messages"])


@pytest.mark.parametrize(("retries", "calls"), [(0, 1), (2, 3)])
def test_the_number_of_retries_is_configurable(
    db_path: Path, policy: str, tmp_path: Path, retries: int, calls: int
) -> None:
    """GROUNDING_MAX_RETRIES=0 goes straight to the safe message; 2 allows two regenerations."""
    model = ScriptedChatModel(script=[AIMessage(content="Sai por R$ 1.999,00!") for _ in range(3)])
    agent = make_agent(model, db_path, policy, tmp_path, grounding_max_retries=retries)
    assert reply_text(ask(agent, "Quanto custa?")) == SAFE_MESSAGE
    assert len(model.seen) == calls


def test_a_price_from_an_earlier_turn_is_still_grounded(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """'E no PIX?' may repeat a price the tools returned two turns ago."""
    model = ScriptedChatModel(
        script=[
            tool_call("get_product_details", {"product_name": "Takamine GD20"}),
            AIMessage(content="Sai por R$ 2.199,00."),
            AIMessage(content="Como eu disse, R$ 2.199,00 no cartão."),
        ]
    )
    agent = make_agent(model, db_path, policy, tmp_path)
    ask(agent, "Quanto custa o Takamine GD20?")
    assert reply_text(ask(agent, "Pode repetir?")) == "Como eu disse, R$ 2.199,00 no cartão."
    assert len(model.seen) == 3


def test_a_reply_that_still_calls_tools_is_not_checked(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Only the final reply is checked: a tool-call step has no text for the customer."""
    ai = AIMessage(
        content="Vou consultar... R$ 9.999,00?",
        tool_calls=[{"name": "list_active_promotions", "args": {}, "id": "c1"}],
    )
    model = ScriptedChatModel(script=[ai, AIMessage(content="Veja as promoções!")])
    assert reply_text(ask(make_agent(model, db_path, policy, tmp_path), "Promoções?")) == (
        "Veja as promoções!"
    )
    assert len(model.seen) == 2


def test_every_grounding_failure_is_logged_as_one_json_line(
    db_path: Path, policy: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A retry and a fallback to the safe message each leave a structured record to audit later."""
    model = ScriptedChatModel(
        script=[
            AIMessage(content="Sai por R$ 1.999,00!"),
            AIMessage(content="Sai por R$ 1.888,00!"),
        ]
    )
    with caplog.at_level("WARNING", logger="emporio_agent.validation"):
        ask(make_agent(model, db_path, policy, tmp_path), "Quanto custa o Takamine GD20?")

    records = [json.loads(r.getMessage()) for r in caplog.records]
    assert [r["event"] for r in records] == ["grounding_retry", "grounding_safe_message"]
    assert records[0] == {
        "event": "grounding_retry",
        "ungrounded": ["R$ 1.999,00"],
        "attempt": 1,
        "max_retries": 1,
    }
    assert records[1]["ungrounded"] == ["R$ 1.888,00"]
    assert "Takamine" not in caplog.text  # no customer text in the log


def test_a_grounded_reply_logs_nothing(
    db_path: Path, policy: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The log holds failures only, so it stays readable."""
    model = ScriptedChatModel(script=[AIMessage(content="Oi! Como posso ajudar?")])
    with caplog.at_level("INFO", logger="emporio_agent.validation"):
        ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert caplog.records == []
