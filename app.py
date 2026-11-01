"""Streamlit chat UI: a thin layer over the same agent as the terminal chat (``make ui``).

To try another model without touching ``.env``::

    streamlit run app.py -- --model groq:openai/gpt-oss-120b     # or APP_LLM_MODEL=...

A chosen model replaces ``LLM_MODEL`` and drops the reserve models, so nothing else is called.
"""

import os
import sys
import uuid
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from emporio_agent.chat import (
    EXAMPLE_QUESTIONS,
    Turn,
    app_settings,
    ask,
    build_agent_from_settings,
    model_override,
    startup_failure_message,
    to_markdown,
)

WELCOME = (
    "Oi! Sou o assistente virtual da Empório da Música 🎸 Posso ajudar com produtos, pedidos, "
    "trocas e dúvidas da loja. É só digitar aqui embaixo 👇"
)

# The theme's primary color (.streamlit/config.toml); the fallback is the same green.
PRIMARY_GREEN = "#1f9d55"

_GROUNDING_LABELS = {
    "approved": "aprovado ✅",
    "retry": "retry 🔁 (um rascunho foi refeito)",
    "safe_message": "mensagem segura 🛡️ (valores não verificados)",
    None: "— (o modelo não respondeu)",
}


@st.cache_resource(show_spinner="Preparando o atendimento...")
def load_agent(override: str | None) -> tuple[Any, str]:
    """Build the agent once per server process and name the model it uses.

    Conversations are told apart by thread id, so one agent serves every browser session.
    """
    load_dotenv()
    settings = app_settings(os.environ, override)
    return build_agent_from_settings(settings), settings.llm_model


def chat_input_css(color: str) -> str:
    """CSS for the typing box: a 2px border in ``color``, rounded corners, a focus glow, white fill.

    It aims at Streamlit's internal markup (``data-testid="stChatInput"``, whose first child is the
    bordered box), which is why the Streamlit version is pinned in ``requirements.txt``.
    """
    return f"""<style>
[data-testid="stChatInput"] > div {{
    border: 2px solid {color} !important;
    border-radius: 14px !important;
    background-color: #ffffff !important;
}}
[data-testid="stChatInput"] > div:focus-within {{
    box-shadow: 0 0 0 4px {color}33 !important;
}}
</style>"""


def new_conversation() -> None:
    """Start a fresh conversation: a new memory thread, the welcome and an empty panel."""
    st.session_state.thread_id = uuid.uuid4().hex
    st.session_state.messages = [("assistant", WELCOME)]
    st.session_state.turns = []


def describe(turn: Turn) -> str:
    """Markdown with the tools called, the grounding outcome and the cases registered."""
    lines = ["**Ferramentas chamadas**"]
    lines += [f"- `{call.name}` {call.args}" for call in turn.tools] or ["- nenhuma"]
    lines.append(f"**Grounding:** {_GROUNDING_LABELS[turn.grounding]}")
    if turn.escalations:
        lines.append("**Escalações registradas**")
        lines += [f"- `{case.reason}`: {case.summary}" for case in turn.escalations]
    return "\n\n".join(lines)


def render_panel(panel: Any, model: str) -> None:
    """Fill the "Por dentro do agente" expander: the model, the last answer, then the history."""
    turns: list[Turn] = st.session_state.turns
    with panel:
        st.caption(f"Modelo: `{model}`")
        if not turns:
            st.caption("Envie uma mensagem para ver o que o agente fez.")
            return
        st.markdown("#### Última resposta")
        st.markdown(to_markdown(describe(turns[-1])))
        if len(turns) > 1:
            st.markdown("#### Histórico")
            for number, turn in enumerate(turns, start=1):
                names = ", ".join(call.name for call in turn.tools) or "nenhuma ferramenta"
                st.markdown(
                    to_markdown(
                        f"**Turno {number}** · {names} · {_GROUNDING_LABELS[turn.grounding]}"
                    )
                )


def main() -> None:
    """Draw the page and handle one customer message."""
    st.set_page_config(page_title="Empório da Música", page_icon="🎸")
    st.markdown(
        chat_input_css(st.get_option("theme.primaryColor") or PRIMARY_GREEN),
        unsafe_allow_html=True,
    )
    st.title("🎸 Empório da Música")
    st.caption("Atendimento virtual")

    if "messages" not in st.session_state:
        new_conversation()
    try:
        if "agent" in st.session_state:  # a ready agent (tests inject one)
            agent, model = st.session_state["agent"], st.session_state.get("model", "?")
        else:
            agent, model = load_agent(model_override(sys.argv[1:], os.environ))
    except Exception as error:  # a system problem, shown as a warning and not as a chat message
        st.warning(startup_failure_message(error))
        st.stop()

    clicked = None
    with st.sidebar:
        st.markdown("**Não sabe por onde começar? Experimente uma destas perguntas:**")
        for number, question in enumerate(EXAMPLE_QUESTIONS):
            if st.button(question, key=f"example-{number}", width="stretch"):
                clicked = question
        st.divider()
        with st.container(gap="xxsmall"):  # question, button and note sit close together
            st.markdown("**Gostaria de iniciar uma nova conversa?**")
            if st.button("Nova conversa", type="primary", width="stretch"):
                new_conversation()
            st.caption("O histórico atual será apagado.")
        st.divider()
        panel = st.expander("Por dentro do agente")

    for role, text in st.session_state.messages:
        if role == "warning":  # system messages (quota, errors) are not the assistant talking
            st.warning(text)
        else:
            with st.chat_message(role):
                st.markdown(to_markdown(text))

    if (
        prompt := st.chat_input(
            "Digite sua dúvida aqui (ex.: preço do Yamaha C40 ou status do meu pedido)"
        )
        or clicked
    ):
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(to_markdown(prompt))
        with st.spinner("Consultando..."):
            turn = ask(agent, st.session_state.thread_id, prompt)
        if turn.error:
            st.warning(turn.error)
            st.session_state.messages.append(("warning", turn.error))
        else:
            with st.chat_message("assistant"):
                st.markdown(to_markdown(turn.reply))
            st.session_state.messages.append(("assistant", turn.reply))
        st.session_state.turns.append(turn)

    render_panel(panel, model)


main()
