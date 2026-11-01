"""Terminal chat with the agent: ``python -m emporio_agent.cli`` (or ``make chat``)."""

import os
import sys
import uuid
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv

from emporio_agent.chat import answer, build_agent_from_settings
from emporio_agent.config import ConfigError, load_settings

_EXIT_WORDS = frozenset({"sair", "exit", "quit"})
_BANNER = (
    "Empório da Música — atendimento virtual. "
    "Digite 'sair' para encerrar ou '/novo' para recomeçar a conversa."
)
_FAREWELL = "Até logo! 🎸"


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
        write(f"Atendente: {answer(agent, thread_id, text)}")


def main() -> None:
    """Load the settings, rebuild the database and start the terminal chat."""
    load_dotenv()
    try:
        settings = load_settings(os.environ)
    except ConfigError as error:
        sys.exit(f"Erro de configuração: {error}")
    agent = build_agent_from_settings(settings)
    run_chat(agent)


if __name__ == "__main__":
    main()
