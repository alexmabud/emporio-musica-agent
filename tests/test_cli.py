"""Behavior tests for the terminal chat loop, driven by scripted input."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from google.genai.errors import ClientError
from langchain_core.messages import AIMessage
from langchain_google_genai.chat_models import GoogleRateLimitError

from emporio_agent.cli import run_chat
from tests.fakes import ScriptedChatModel
from tests.helpers import make_agent


def chat(agent: Any, lines: list[str | type[BaseException]]) -> list[str]:
    """Run the loop with scripted input and return everything it printed."""
    feed: Iterator[str | type[BaseException]] = iter(lines)

    def read(prompt: str) -> str:
        item = next(feed)
        if isinstance(item, type):
            raise item
        return item

    printed: list[str] = []
    run_chat(agent, read=read, write=printed.append)
    return printed


def test_the_customer_talks_and_leaves(db_path: Path, policy: str, tmp_path: Path) -> None:
    """A message gets the agent's reply; 'sair' ends the chat; blank lines are ignored."""
    model = ScriptedChatModel(script=[AIMessage(content="Olá! Como posso ajudar?")])
    printed = chat(make_agent(model, db_path, policy, tmp_path), ["", "Oi", "SAIR"])
    assert any("Empório da Música" in line for line in printed)  # welcome banner
    assert "Atendente: Olá! Como posso ajudar?" in printed
    assert len(model.seen) == 1  # the blank line never reached the model


@pytest.mark.parametrize("ending", [EOFError, KeyboardInterrupt])
def test_ctrl_d_and_ctrl_c_end_the_chat_cleanly(
    db_path: Path,
    policy: str,
    tmp_path: Path,
    ending: type[BaseException],
) -> None:
    """No traceback when the customer closes the terminal."""
    model = ScriptedChatModel(script=[])
    printed = chat(make_agent(model, db_path, policy, tmp_path), [ending])
    assert printed[-1].startswith("Até logo")


def test_novo_starts_a_fresh_conversation(db_path: Path, policy: str, tmp_path: Path) -> None:
    """'/novo' forgets everything said before, like opening a new chat."""
    model = ScriptedChatModel(script=[AIMessage(content="r1"), AIMessage(content="r2")])
    chat(make_agent(model, db_path, policy, tmp_path), ["Quero um violão", "/novo", "Oi", "sair"])
    humans_second_call = [m.text for m in model.seen[1] if m.type == "human"]
    assert humans_second_call == ["Oi"]


def test_a_model_failure_is_reported_and_the_chat_goes_on(
    db_path: Path,
    policy: str,
    tmp_path: Path,
) -> None:
    """A permanent model error (say, an invalid API key) must not kill the session."""
    bad_key = ClientError(400, {"error": {"code": 400, "message": "API key not valid"}})
    model = ScriptedChatModel(script=[bad_key, AIMessage(content="Voltei!")])
    printed = chat(make_agent(model, db_path, policy, tmp_path), ["Oi", "Oi de novo", "sair"])
    failure = [line for line in printed if "Não consegui falar com o modelo" in line]
    assert failure and "ClientError" in failure[0]
    assert not failure[0].startswith("Atendente:")  # a system message, not the attendant talking
    assert "Atendente: Voltei!" in printed


def test_an_exhausted_daily_quota_gets_a_clear_message_with_the_whatsapp_number(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Quota gone on every model: no stack trace, and the customer is pointed to a human."""
    quota = GoogleRateLimitError(
        "429 RESOURCE_EXHAUSTED. Generate content requests per day limit exceeded on Free Tier."
    )
    model = ScriptedChatModel(script=[quota])
    printed = chat(make_agent(model, db_path, policy, tmp_path), ["Oi", "sair"])
    limit_lines = [line for line in printed if "limite diário" in line]
    assert limit_lines and "(67) 3321-4500" in limit_lines[0]
    assert not limit_lines[0].startswith("Atendente:")
    assert not any("Confira a chave" in line for line in printed)


def test_a_startup_failure_is_a_clear_message_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key (or any failure building the agent): one line that names the fix, no stack trace.

    The text of the error stays out, because it could carry a key.
    """
    import emporio_agent.cli as cli

    def broken(settings: object) -> None:
        raise ValueError("API key required: sk-secret-value")

    monkeypatch.setenv("LLM_MODEL", "fake:model")
    monkeypatch.setattr(cli, "build_agent_from_settings", broken)
    with pytest.raises(SystemExit) as stop:
        cli.main()
    message = str(stop.value)
    assert "Não foi possível iniciar" in message and ".env" in message and "ValueError" in message
    assert "sk-secret-value" not in message


def test_with_only_a_google_key_the_chat_starts_even_if_a_groq_reserve_is_listed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real build of the agent (no model call): the Groq reserve has no key, so it is skipped."""
    import emporio_agent.cli as cli

    started: list[Any] = []
    monkeypatch.setenv("LLM_MODEL", "google_genai:gemini-3.5-flash")
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key-never-sent")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv(
        "GROQ_API_KEY", raising=False
    )  # truly absent: building the Groq client fails
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)  # never read the developer's own .env
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "groq:openai/gpt-oss-120b")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "emporio.db"))
    monkeypatch.setenv("ESCALATIONS_PATH", str(tmp_path / "escalations.jsonl"))
    monkeypatch.setattr(cli, "run_chat", started.append)
    cli.main()
    assert len(started) == 1  # the agent was built and the chat would have begun
