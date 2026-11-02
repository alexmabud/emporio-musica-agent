"""Tests for the Streamlit chat UI, driven with Streamlit's own test harness (no real model)."""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_google_genai.chat_models import GoogleRateLimitError
from streamlit.testing.v1 import AppTest

from emporio_agent.chat import EXAMPLE_QUESTIONS as EXAMPLES
from emporio_agent.chat import to_markdown
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import make_agent

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def _app(model: ScriptedChatModel, db_path: Path, policy: str, tmp_path: Path) -> AppTest:
    """The app with a scripted agent in the session, so nothing calls a real model."""
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["agent"] = make_agent(model, db_path, policy, tmp_path)
    at.session_state["model"] = "fake:model"
    at.session_state["feedback_path"] = tmp_path / "feedback.jsonl"
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


def test_a_model_failure_is_a_warning_not_something_the_assistant_said(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The quota message appears as a warning outside the chat bubbles, with the WhatsApp number."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    at = _app(ScriptedChatModel(script=[quota]), db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    assert not at.exception and not at.error
    assert len(at.warning) == 1
    assert "limite diário" in at.warning[0].value and "(67) 3321-4500" in at.warning[0].value
    assert [m.name for m in at.chat_message] == ["assistant", "user"]  # no assistant bubble for it
    assert "limite diário" not in "".join(_texts(at))


def test_the_warning_stays_in_its_place_when_the_chat_goes_on(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A later message works normally; the earlier warning is still on screen, not a reply."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    model = ScriptedChatModel(script=[quota, AIMessage(content="Voltei!")])
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    at.chat_input[0].set_value("Oi de novo").run()
    assert len(at.warning) == 1
    assert _texts(at)[-1] == "Voltei!"
    assert [m.name for m in at.chat_message] == ["assistant", "user", "user", "assistant"]
    assert "o modelo não respondeu" in _sidebar_text(at)  # the panel says what happened


def test_a_configuration_error_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model configured: a warning that names the fix, not a traceback or an assistant bubble."""
    monkeypatch.setenv("LLM_MODEL", "")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and not at.error
    assert len(at.warning) == 1 and "Erro de configuração" in at.warning[0].value
    assert len(at.chat_message) == 0 and len(at.chat_input) == 0  # the page stops there


def test_a_startup_failure_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing API key (or any failure while building the agent) is explained, not dumped."""
    import emporio_agent.chat as chat

    def broken(settings: object) -> None:
        raise ValueError("GROQ_API_KEY not set")

    monkeypatch.setenv("LLM_MODEL", "fake:model")
    monkeypatch.setattr(chat, "build_agent_from_settings", broken)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and not at.error
    assert len(at.warning) == 1
    assert "Não foi possível iniciar" in at.warning[0].value and ".env" in at.warning[0].value


def test_streamlit_hides_the_deploy_button_and_the_developer_menu() -> None:
    """The project's own config makes the toolbar minimal, so a demo shows only the chat."""
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["client"]["toolbarMode"] == "minimal"


# --- sidebar: model, examples, "Por dentro do agente" ------------------------------------------


def _sidebar_text(at: AppTest) -> str:
    """Every piece of text in the sidebar, panel included."""
    parts = [m.value for m in at.sidebar.markdown] + [c.value for c in at.sidebar.caption]
    for expander in at.sidebar.expander:
        parts += [m.value for m in expander.markdown]
    return "\n".join(parts)


def test_the_model_is_the_first_line_of_the_agent_panel_not_of_the_sidebar(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The person testing sees which model answers, but the customer's sidebar has none of it."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    outside = " ".join(text for _, text in _layout(at))
    assert "fake:model" not in outside and "Modelo" not in outside
    panel = list(at.sidebar.expander[0].children.values())
    assert "Modelo: `fake:model`" in panel[0].value  # the first line of the panel

    model = ScriptedChatModel(script=[AIMessage(content="Oi!")])
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    assert "Modelo: `fake:model`" in list(at.sidebar.expander[0].children.values())[0].value


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


# --- sidebar layout and wording -----------------------------------------------------------------


def _flatten(children: dict[int, Any]) -> list[tuple[str, str]]:
    """(kind, label or text) pairs, going into plain containers but not into expanders."""
    items: list[tuple[str, str]] = []
    for element in children.values():
        if element.type in {"container", "flex_container"}:
            items += _flatten(element.children)
            continue
        items.append(
            (element.type, getattr(element, "label", None) or getattr(element, "value", "") or "")
        )
    return items


def _layout(at: AppTest) -> list[tuple[str, str]]:
    """The sidebar from top to bottom."""
    return _flatten(at.sidebar.children)


def test_the_sidebar_follows_the_agreed_order_and_wording(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Only customer content: examples, a divider, new conversation, a divider, the agent panel."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert _layout(at) == [
        ("markdown", "**Não sabe por onde começar? Experimente uma destas perguntas:**"),
        *[("button", question) for question in EXAMPLES],
        ("divider", "---"),
        ("markdown", "**Gostaria de iniciar uma nova conversa?**"),
        ("button", "Nova conversa"),
        ("caption", "O histórico atual será apagado."),
        ("divider", "---"),
        ("expander", "Por dentro do agente"),
    ]


def test_only_the_new_conversation_button_is_the_primary_one(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Primary buttons take the theme color (green), so it marks only the destructive action."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    kinds = {b.label: b.proto.type for b in at.sidebar.button}
    assert kinds["Nova conversa"] == "primary"
    assert [label for label, kind in kinds.items() if kind == "primary"] == ["Nova conversa"]


def test_the_theme_primary_color_is_green() -> None:
    """The primary color is green: its green channel dominates the other two."""
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    color = config["theme"]["primaryColor"].lstrip("#")
    red, green, blue = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    assert green > red and green > blue


# --- the typing field -----------------------------------------------------------------------------


def _css(at: AppTest) -> str:
    """The <style> block the page injects (empty if there is none)."""
    return "\n".join(m.value for m in at.markdown if "<style>" in m.value)


def test_the_typing_box_has_a_green_rounded_border_a_focus_glow_and_a_white_background(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """CSS aimed at the chat input's data-testid; the green is the theme's primaryColor."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    css = _css(at)
    green = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))[
        "theme"
    ]["primaryColor"]
    assert '[data-testid="stChatInput"] > div' in css
    assert f"border: 2px solid {green}" in css
    assert "border-radius" in css
    assert "background-color: #ffffff" in css
    assert ":focus-within" in css and "box-shadow" in css  # the soft highlight on focus


def test_the_typing_field_invites_a_question_with_two_examples(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The placeholder shows what can be asked."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert at.chat_input[0].placeholder == (
        "Digite sua dúvida aqui (ex.: preço do Yamaha C40 ou status do meu pedido)"
    )


def test_the_welcome_is_the_agreed_text_and_points_to_the_typing_box(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A fixed UI text (not the prompt) that says what the assistant does and where to type."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    assert _texts(at)[0] == (
        "Oi! Sou o assistente virtual da Empório da Música 🎸 Posso ajudar com produtos, pedidos, "
        "trocas e dúvidas da loja. É só digitar aqui embaixo 👇"
    )


def test_the_chosen_thumb_is_green_and_larger_and_the_other_one_stays_gray(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """CSS only for Streamlit's "active" feedback button, in the theme's primaryColor."""
    at = _app(ScriptedChatModel(script=[]), db_path, policy, tmp_path)
    green = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))[
        "theme"
    ]["primaryColor"]
    rules = {}  # selector -> declarations, for the rules the page injects
    for block in _css(at).split("}"):
        if "{" in block:
            selector, declarations = block.split("{", 1)
            rules[selector.strip()] = declarations
    active = rules['[data-testid="stFeedbackButtonActive"]']
    assert f"color: {green} !important" in active
    assert "transform: scale(" in active
    # No rule gives the unselected thumb a color of its own, so it keeps Streamlit's gray.
    for selector, declarations in rules.items():
        if selector != '[data-testid="stFeedbackButtonActive"]' and "stFeedbackButton" in selector:
            assert "color: " not in declarations.replace("transition", "")


def test_streamlit_is_pinned_because_the_css_uses_its_internal_selectors() -> None:
    """The CSS relies on Streamlit's internal markup, so the version is pinned and matches."""
    from importlib.metadata import version

    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    pins = [line for line in lines if line.strip().startswith("streamlit")]
    assert len(pins) == 1 and "==" in pins[0], pins
    assert pins[0].split("==")[1].split()[0] == version("streamlit")


# --- 👍/👎 feedback ----------------------------------------------------------------------------


def _votes(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "feedback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_thumbs_go_under_the_assistants_answers_and_not_under_the_welcome_or_a_warning(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Only real answers are rated: not the greeting, not a system warning about the quota."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    model = ScriptedChatModel(script=[AIMessage(content="r1"), quota, AIMessage(content="r3")])
    at = _app(model, db_path, policy, tmp_path)
    assert len(at.feedback) == 0  # welcome only
    at.chat_input[0].set_value("um").run()
    assert len(at.feedback) == 1
    at.chat_input[0].set_value("dois").run()
    assert len(at.warning) == 1 and len(at.feedback) == 1  # the warning has no thumbs
    at.chat_input[0].set_value("três").run()
    assert len(at.feedback) == 2 and not at.exception


def test_a_thumbs_up_saves_the_question_the_reply_and_the_tools(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The vote carries enough context to be read later without the conversation."""
    model = ScriptedChatModel(
        script=[
            tool_call("get_product_details", {"product_name": "Takamine GD20"}),
            AIMessage(content="O Takamine GD20 sai por R$ 2.199,00."),
        ]
    )
    at = _app(model, db_path, policy, tmp_path)
    at.chat_input[0].set_value("Quanto custa o Takamine GD20?").run()
    at.feedback[0].set_value(1).run()
    (vote,) = _votes(tmp_path)
    assert (vote["rating"], vote["turn"], vote["comment"]) == ("up", 1, "")
    assert vote["question"] == "Quanto custa o Takamine GD20?"
    assert vote["reply"] == "O Takamine GD20 sai por R$ 2.199,00."
    assert vote["tools"] == ["get_product_details"] and len(vote["thread_id"]) == 32


def test_a_thumbs_down_asks_for_an_optional_comment_and_saves_it_masked(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The box only appears after a 👎; the vote counts without a comment; the comment is masked."""
    at = _app(ScriptedChatModel(script=[AIMessage(content="r1")]), db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    assert len(at.text_input) == 0  # nothing to explain yet
    at.feedback[0].set_value(0).run()
    assert [v["rating"] for v in _votes(tmp_path)] == ["down"]  # saved even with no comment
    assert len(at.text_input) == 1 and "opcional" in at.text_input[0].label
    at.text_input[0].set_value("Me liguem em (67) 99654-7821").run()
    (vote,) = _votes(tmp_path)
    assert vote["rating"] == "down" and vote["comment"] == "Me liguem em (67) 9****-7821"


def test_the_last_vote_wins_and_changing_to_a_thumbs_up_drops_the_comment(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """One record per answer: 👎 with a comment, then 👍, leaves a single 👍 with no comment."""
    at = _app(ScriptedChatModel(script=[AIMessage(content="r1")]), db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    at.feedback[0].set_value(0).run()
    at.text_input[0].set_value("não gostei").run()
    at.feedback[0].set_value(1).run()
    (vote,) = _votes(tmp_path)
    assert vote["rating"] == "up" and vote["comment"] == ""
    assert len(at.text_input) == 0


def test_each_answer_has_its_own_vote_and_a_warning_still_counts_as_a_turn(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The vote on the third message is turn 3 even though the second one failed."""
    quota = GoogleRateLimitError("Generate content requests per day limit exceeded on Free Tier.")
    model = ScriptedChatModel(script=[AIMessage(content="r1"), quota, AIMessage(content="r3")])
    at = _app(model, db_path, policy, tmp_path)
    for text in ("um", "dois", "três"):
        at.chat_input[0].set_value(text).run()
    at.feedback[0].set_value(1).run()
    at.feedback[1].set_value(0).run()
    assert [(v["turn"], v["question"], v["rating"]) for v in _votes(tmp_path)] == [
        (1, "um", "up"),
        (3, "três", "down"),
    ]


def test_a_new_conversation_starts_without_votes_on_screen(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The thumbs belong to the conversation: after "Nova conversa" the screen is clean."""
    at = _app(ScriptedChatModel(script=[AIMessage(content="r1")]), db_path, policy, tmp_path)
    at.chat_input[0].set_value("Oi").run()
    at.feedback[0].set_value(1).run()
    labels = [b.label for b in at.sidebar.button]
    at.sidebar.button[labels.index("Nova conversa")].click().run()
    assert len(at.feedback) == 0 and not at.exception
    assert len(_votes(tmp_path)) == 1  # the earlier vote is kept
