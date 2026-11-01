"""Terminal chat with the agent: ``python -m emporio_agent.cli`` (or ``make chat``)."""

import os
import sys
import uuid
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv

from emporio_agent.agent import build_agent, create_model, is_daily_quota_error, reply_text
from emporio_agent.config import ConfigError, load_settings
from emporio_agent.data_loader import build_database
from emporio_agent.policies import load_policy_text

_EXIT_WORDS = frozenset({"sair", "exit", "quit"})
_BANNER = (
    "Empório da Música — atendimento virtual. "
    "Digite 'sair' para encerrar ou '/novo' para recomeçar a conversa."
)
_FAREWELL = "Até logo! 🎸"


def _failure_message(error: Exception) -> str:
    """Explain a model failure to the customer without a stack trace."""
    if is_daily_quota_error(error):
        return (
            "O atendimento automático atingiu o limite diário de uso. Fale com a nossa equipe "
            "pelo WhatsApp (67) 3321-4500."
        )
    return (
        f"Não consegui falar com o modelo ({type(error).__name__}). "
        "Confira a chave e o modelo no .env e tente de novo."
    )


def run_chat(
    agent: Any,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Chat with the agent until the customer leaves.

    Args:
        agent: Agent built by ``agent.build_agent``.
        read: Reads one line; called with the prompt. Tests pass a scripted reader.
        write: Writes one line to the customer.
    """
    write(_BANNER)
    thread_id = uuid.uuid4().hex
    while True:
        try:
            text = read("Você: ").strip()
        except (EOFError, KeyboardInterrupt):
            write(_FAREWELL)
            return
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            write(_FAREWELL)
            return
        if text.lower() == "/novo":
            thread_id = uuid.uuid4().hex
            write("(nova conversa)")
            continue
        try:
            result = agent.invoke(
                {"messages": [("user", text)]}, config={"configurable": {"thread_id": thread_id}}
            )
        except Exception as error:
            write(_failure_message(error))
            continue
        write(f"Atendente: {reply_text(result)}")


def main() -> None:
    """Load the settings, rebuild the database and start the terminal chat."""
    load_dotenv()
    try:
        settings = load_settings(os.environ)
    except ConfigError as error:
        sys.exit(f"Erro de configuração: {error}")
    build_database(settings.data_dir, settings.db_path)

    agent = build_agent(
        create_model(settings.llm_model, settings.llm_temperature),
        fallback_models=[
            create_model(name, settings.llm_temperature) for name in settings.llm_fallback_models
        ],
        grounding_max_retries=settings.grounding_max_retries,
        db_path=settings.db_path,
        escalations_path=settings.escalations_path,
        policy_text=load_policy_text(settings.policy_pdf),
    )
    run_chat(agent)


if __name__ == "__main__":
    main()
