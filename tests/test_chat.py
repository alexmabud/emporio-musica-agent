"""Tests for what the terminal chat and the Streamlit app share (no real model)."""

from pathlib import Path

import pytest
from google.genai.errors import ClientError
from langchain_core.messages import AIMessage
from langchain_google_genai.chat_models import GoogleRateLimitError

import emporio_agent.chat as chat
from emporio_agent.chat import (
    Escalation,
    app_settings,
    ask,
    build_agent_from_settings,
    failure_message,
    mask_pii,
    model_override,
    startup_failure_message,
    summarize_args,
)
from emporio_agent.config import ConfigError, load_settings
from emporio_agent.validation import SAFE_MESSAGE
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import make_agent


def test_ask_returns_the_agents_reply(db_path: Path, policy: str, tmp_path: Path) -> None:
    """One call: the customer's text in, the agent's final text out, on the given thread."""
    model = ScriptedChatModel(script=[AIMessage(content="Olá! Como posso ajudar?")])
    turn = ask(make_agent(model, db_path, policy, tmp_path), "t1", "Oi")
    assert turn.reply == "Olá! Como posso ajudar?" and turn.error is None


def test_a_model_failure_is_a_system_message_not_a_reply(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The daily quota is not something the assistant said: it travels apart, in `error`."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    agent = make_agent(ScriptedChatModel(script=[quota]), db_path, policy, tmp_path)
    turn = ask(agent, "t1", "Oi")
    assert turn.reply == ""
    assert turn.error is not None
    assert "limite diário" in turn.error and "(67) 3321-4500" in turn.error


def test_a_configuration_style_failure_names_the_error_type() -> None:
    """Other failures point to .env without exposing details."""
    bad = ClientError(400, {"error": {"code": 400, "message": "invalid key"}})
    assert "ClientError" in failure_message(bad) and ".env" in failure_message(bad)


def test_the_agent_is_built_from_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The main model and the reserve models come from the settings; the database is rebuilt."""
    created: list[str] = []

    def fake_create_model(spec: str, temperature: float) -> ScriptedChatModel:
        created.append(spec)
        return ScriptedChatModel(script=[AIMessage(content="Pronto!")])

    monkeypatch.setattr(chat, "create_model", fake_create_model)
    settings = load_settings(
        {
            "LLM_MODEL": "fake:main",
            "LLM_FALLBACK_MODELS": "fake:one,fake:two",
            "DB_PATH": str(tmp_path / "store.db"),
            "ESCALATIONS_PATH": str(tmp_path / "e.jsonl"),
        }
    )
    agent = build_agent_from_settings(settings)
    assert created == ["fake:main", "fake:one", "fake:two"]
    assert (tmp_path / "store.db").exists()
    assert ask(agent, "t1", "Oi").reply == "Pronto!"


# --- what the "inside the agent" panel shows --------------------------------------------------


def test_emails_and_phones_are_masked() -> None:
    """The panel may be shown on a screen: keep enough to recognize, hide the rest."""
    assert mask_pii("anacarol.ferreira@coldmail.com") == "an***@coldmail.com"
    assert mask_pii("(67) 99654-7821") == "(67) 9****-7821"
    assert mask_pii("67996547821") == "(67) 9****-7821"
    assert mask_pii("fale com bruno@x.com ou (67) 98888-1234") == (
        "fale com br***@x.com ou (67) 9****-1234"
    )
    assert mask_pii("pedido 8 por R$ 2.199,00 em 10x") == "pedido 8 por R$ 2.199,00 em 10x"


def test_tool_arguments_are_summarized_and_masked() -> None:
    """Short 'key=value' text, long values cut, personal data hidden, empty values skipped."""
    summary = summarize_args(
        {"order_id": 8, "email_or_phone": "anacarol.ferreira@coldmail.com", "note": None}
    )
    assert summary == "order_id=8, email_or_phone='an***@coldmail.com'"
    assert summarize_args({"summary": "x" * 100}).endswith("…'")
    assert len(summarize_args({"summary": "x" * 100})) < 70


def test_ask_reports_the_tools_called_with_masked_arguments(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The panel gets the trajectory of the turn: tool names and their summarized arguments."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "get_order_status",
                {"order_id": 8, "email_or_phone": "anacarol.ferreira@coldmail.com"},
            ),
            AIMessage(content="Seu pedido foi enviado. Rastreio BRJL5544332BR."),
        ]
    )
    turn = ask(make_agent(model, db_path, policy, tmp_path), "t1", "Pedido 8, meu e-mail é x")
    assert turn.reply.startswith("Seu pedido foi enviado")
    assert [(t.name, t.args) for t in turn.tools] == [
        ("get_order_status", "order_id=8, email_or_phone='an***@coldmail.com'")
    ]
    assert turn.grounding == "approved" and turn.retries == 0 and turn.escalations == ()


def test_ask_reports_only_the_tools_of_the_latest_turn(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A second question must not list the first question's tools again."""
    model = ScriptedChatModel(
        script=[
            tool_call("list_active_promotions", {}),
            AIMessage(content="Veja as promoções!"),
            AIMessage(content="De nada!"),
        ]
    )
    agent = make_agent(model, db_path, policy, tmp_path)
    assert [t.name for t in ask(agent, "t1", "Promoções?").tools] == ["list_active_promotions"]
    assert ask(agent, "t1", "Obrigado").tools == ()


def test_ask_reports_the_cases_registered_for_a_person_without_personal_data(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Escalations appear in the panel with their reason and summary, phones masked."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "escalate_to_human",
                {"reason": "complaint", "summary": "Violão rachado. Contato (67) 99654-7821."},
            ),
            AIMessage(content="Registrei sua reclamação."),
        ]
    )
    turn = ask(make_agent(model, db_path, policy, tmp_path), "t1", "Meu violão chegou rachado")
    assert turn.escalations == (
        Escalation(reason="complaint", summary="Violão rachado. Contato (67) 9****-7821."),
    )


def test_ask_reports_a_grounding_retry_and_the_safe_message(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Approved / retry / safe_message: the three outcomes of the grounding check."""
    retried = ScriptedChatModel(
        script=[AIMessage(content="Sai por R$ 1.999,00!"), AIMessage(content="Vou confirmar.")]
    )
    turn = ask(make_agent(retried, db_path, policy, tmp_path), "t1", "Quanto custa?")
    assert (turn.grounding, turn.retries, turn.reply) == ("retry", 1, "Vou confirmar.")

    safe = ScriptedChatModel(
        script=[AIMessage(content="R$ 1.999,00"), AIMessage(content="R$ 1.888,00")]
    )
    turn = ask(make_agent(safe, db_path, policy, tmp_path), "t2", "Quanto custa?")
    assert turn.grounding == "safe_message" and turn.reply == SAFE_MESSAGE


def test_a_failed_turn_has_no_grounding_tools_or_reply(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """No reply was generated, so there is nothing to ground and no tool to list."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    agent = make_agent(ScriptedChatModel(script=[quota]), db_path, policy, tmp_path)
    turn = ask(agent, "t1", "Oi")
    assert turn.grounding is None and turn.tools == () and turn.escalations == ()
    assert turn.error and "limite diário" in turn.error


def test_startup_failures_are_explained_without_a_stack_trace() -> None:
    """A missing key or model at startup becomes a short, actionable message."""
    assert "Erro de configuração" in startup_failure_message(ConfigError("LLM_MODEL is not set"))
    other = startup_failure_message(ValueError("GROQ_API_KEY not set"))
    assert "ValueError" in other and ".env" in other and "GROQ_API_KEY" not in other


# --- the model the app uses ----------------------------------------------------------------------


def test_the_model_can_be_chosen_by_argument_or_variable() -> None:
    """`streamlit run app.py -- --model X` or APP_LLM_MODEL; the argument wins; none means .env."""
    assert model_override(["--model", "groq:m"], {"APP_LLM_MODEL": "x:y"}) == "groq:m"
    assert model_override(["--model=groq:m"], {}) == "groq:m"
    assert model_override([], {"APP_LLM_MODEL": " x:y "}) == "x:y"
    assert model_override(["-q", "tests/"], {}) is None


def test_a_chosen_model_drops_the_reserve_models_so_nothing_else_is_called() -> None:
    """Testing the UI on Groq must never fall back to Gemini and spend its quota."""
    env = {
        "LLM_MODEL": "google_genai:g",
        "GOOGLE_API_KEY": "fake",  # a reserve is only kept when its provider has a key
        "LLM_FALLBACK_MODELS": "google_genai:g-lite",
    }
    assert app_settings(env, None).llm_fallback_models == ("google_genai:g-lite",)
    chosen = app_settings(env, "groq:openai/gpt-oss-120b")
    assert chosen.llm_model == "groq:openai/gpt-oss-120b" and chosen.llm_fallback_models == ()
    assert app_settings({}, "groq:m").llm_model == "groq:m"  # .env may lack LLM_MODEL
