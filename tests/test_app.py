"""Tests for the Streamlit chat UI, driven with Streamlit's own test harness (no real model)."""

from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_google_genai.chat_models import GoogleRateLimitError
from streamlit.testing.v1 import AppTest

from emporio_agent.chat import EXAMPLE_QUESTIONS as EXAMPLES
from emporio_agent.chat import to_markdown
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import make_agent

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _app(model: ScriptedChatModel, db_path: Path, policy: str, tmp_path: Path) -> AppTest:
    """The app with a scripted agent in the session, so nothing calls a real model."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["agent"] = make_agent(model, db_path, policy, tmp_path)
    at.session_state["model"] = "fake:model"
    return at.run()


def _texts(at: AppTest) -> list[str]:
    return [m.markdown[0].value for m in at.chat_message if m.markdown]


def test_the_chat_opens_with_a_welcome_message(db_path: Path, policy: str, tmp_path: Path) -> None:
    """The first thing the customer sees is the assistant greeting."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert not at.exception
    assert len(at.chat_message) == 1 and "Empório da Música" in _texts(at)[0]


def test_a_message_gets_the_agents_reply_and_both_stay_on_screen(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The conversation accumulates: welcome, the customer's text, the reply."""
    model = ScriptedChatModel(script=[AIMessage(content="Posso ajudar sim!")])
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    assert not at.exception
    assert _texts(at)[1:] == ["Oi", "Posso ajudar sim!"]
    assert [m.name for m in at.chat_message] == ["assistant", "user", "assistant"]


def test_the_dollar_sign_of_prices_is_escaped_so_it_is_not_read_as_math() -> None:
    """Streamlit renders $...$ as LaTeX: 'R$ 439,20 ... R$ 549,00' would turn into formulas."""
    assert to_markdown("de R$ 549,00 por R$ 439,20") == r"de R\$ 549,00 por R\$ 439,20"


def test_whatsapp_bold_becomes_markdown_bold() -> None:
    """The agent writes *bold* (WhatsApp style); markdown would show it as italic."""
    assert to_markdown("Preço *promocional* hoje") == "Preço **promocional** hoje"
    assert to_markdown("já **negrito** fica") == "já **negrito** fica"
    assert to_markdown("5 * 3 não muda") == "5 * 3 não muda"


def test_prices_reach_the_screen_escaped(db_path: Path, policy: str, tmp_path: Path) -> None:
    """End to end: the reply with prices is shown with the dollar signs escaped."""
    model = ScriptedChatModel(script=[AIMessage(content="Sai por R$ 2.199,00 ou R$ 2.089,05.")])
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Vi R$ 2.199,00 ou R$ 2.089,05, ainda vale?").run()
    assert _texts(at)[-1] == r"Sai por R\$ 2.199,00 ou R\$ 2.089,05."


def test_new_conversation_forgets_the_previous_one(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The sidebar button clears the screen and starts a fresh thread, like /novo in the CLI."""
    model = ScriptedChatModel(script=[AIMessage(content="r1"), AIMessage(content="r2")])
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Quero um violão").run()
    labels = [b.label for b in at.sidebar.button]
    at.sidebar.button[labels.index("Nova conversa")].click().run()
    assert len(at.chat_message) == 1  # only the welcome again
    at.chat_input[0].set_value("Oi").run()
    humans = [m.text for m in model.seen[1] if m.type == "human"]
    assert humans == ["Oi"]


def test_a_model_failure_is_shown_as_a_message_not_a_traceback(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """When the daily quota is gone the customer reads the WhatsApp number, not a stack trace."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    at = _app(ScriptedChatModel(script=[quota]), db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    assert not at.exception
    assert "(67) 3321" in _texts(at)[-1]


# --- sidebar: model, examples, "Por dentro do agente" ------------------------------------------


def _sidebar_text(at: AppTest) -> str:
    """Every piece of text in the sidebar, panel included."""
    parts = [m.value for m in at.sidebar.markdown] + [c.value for c in at.sidebar.caption]
    for expander in at.sidebar.expander:
        parts += [m.value for m in expander.markdown]
    return "\n".join(parts)


def test_the_sidebar_shows_the_model_in_use(db_path: Path, policy: str, tmp_path: Path) -> None:
    """The person testing the UI always sees which model answers."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert "fake:model" in _sidebar_text(at)


def test_example_buttons_send_the_question_as_if_typed(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """One click asks 'Me arrependi da compra, posso devolver?' and shows the answer."""
    model = ScriptedChatModel(script=[AIMessage(content="Em até 7 dias corridos.")])
    at = _app(model, db_path, policy, tmp_path)
    labels = [b.label for b in at.sidebar.button]
    assert labels[:4] == EXAMPLES
    assert "Nova conversa" in labels

    at.sidebar.button[labels.index("Me arrependi da compra, posso devolver?")].click().run()
    assert not at.exception
    assert _texts(at)[1:] == ["Me arrependi da compra, posso devolver?", "Em até 7 dias corridos."]
    assert [m.text for m in model.seen[0] if m.type == "human"] == [EXAMPLES[2]]


def test_the_panel_waits_for_the_first_message(db_path: Path, policy: str, tmp_path: Path) -> None:
    """Before anything is asked there is nothing to show, and it says so."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert at.sidebar.expander[0].label == "Por dentro do agente"
    assert "Envie uma mensagem" in _sidebar_text(at)


def test_the_panel_lists_the_tools_with_masked_personal_data(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """After an order lookup: the tool and its arguments, e-mail masked, grounding approved."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "get_order_status",
                {"order_id": 8, "email_or_phone": "anacarol.ferreira@coldmail.com"},
            ),
            AIMessage(content="Pedido enviado. Rastreio BRJL5544332BR."),
        ]
    )
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Pedido 8, e-mail anacarol.ferreira@coldmail.com").run()
    assert not at.exception
    panel = _sidebar_text(at)
    assert "get_order_status" in panel and "order_id=8" in panel
    assert "an***@coldmail.com" in panel
    assert "anacarol.ferreira" not in panel  # the raw address never reaches the panel
    assert "aprovado" in panel


def test_the_panel_shows_a_grounding_retry_and_the_safe_message(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The three outcomes are visible: approved, retry, safe message."""
    retried = ScriptedChatModel(
        script=[AIMessage(content="Sai por R$ 1.999,00!"), AIMessage(content="Vou confirmar.")]
    )
    at = _app(retried, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Quanto custa?").run()
    assert "retry" in _sidebar_text(at) and "Vou confirmar." in _texts(at)[-1]

    safe = ScriptedChatModel(
        script=[AIMessage(content="R$ 1.999,00"), AIMessage(content="R$ 1.888,00")]
    )
    at = _app(safe, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Quanto custa?").run()
    assert "mensagem segura" in _sidebar_text(at)


def test_the_panel_lists_registered_cases_and_keeps_a_history_per_turn(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Escalations appear with reason and summary; earlier turns stay in the history."""
    model = ScriptedChatModel(
        script=[
            tool_call(
                "escalate_to_human",
                {"reason": "complaint", "summary": "Violão rachado. Fone (67) 99654-7821."},
            ),
            AIMessage(content="Registrei sua reclamação."),
            AIMessage(content="De nada!"),
        ]
    )
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Meu violão chegou rachado").run()
    panel = _sidebar_text(at)
    assert "complaint" in panel and "Violão rachado" in panel and "(67) 9****-7821" in panel
    assert "99654" not in panel

    at.chat_input[0].set_value("Obrigado").run()
    panel = _sidebar_text(at)
    assert "Turno 1" in panel and "Turno 2" in panel and "escalate_to_human" in panel


def test_a_new_conversation_clears_the_panel(db_path: Path, policy: str, tmp_path: Path) -> None:
    """'Nova conversa' forgets the turns too."""
    model = ScriptedChatModel(
        script=[tool_call("list_active_promotions", {}), AIMessage(content="Veja!")]
    )
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Promoções?").run()
    assert "list_active_promotions" in _sidebar_text(at)
    labels = [b.label for b in at.sidebar.button]
    at.sidebar.button[labels.index("Nova conversa")].click().run()
    assert "list_active_promotions" not in _sidebar_text(at)
