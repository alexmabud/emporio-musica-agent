"""Behavior tests for the agent loop, memory, retry and history trimming (no real LLM)."""

from datetime import datetime
from pathlib import Path
from typing import Any

import groq
import httpx
import pytest
from google.genai.errors import ClientError, ServerError
from langchain_core.messages import AIMessage, ToolMessage
from langchain_google_genai.chat_models import GoogleRateLimitError

from emporio_agent.agent import is_daily_quota_error, reply_text
from tests.fakes import ScriptedChatModel, tool_call
from tests.helpers import CG, SATURDAY_MORNING, ask, make_agent


def test_the_agent_runs_a_real_tool_and_answers(db_path: Path, policy: str, tmp_path: Path) -> None:
    """The model asks for search_products, the real tool runs on the data, the model answers."""
    model = ScriptedChatModel(
        script=[
            tool_call("search_products", {"category": "Violões", "max_price": 1000}),
            AIMessage(content="Tenho 12 violões até R$ 1.000!"),
        ]
    )
    result = ask(make_agent(model, db_path, policy, tmp_path), "Quais violões até R$1000?")

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert '"total_matches": 12' in tool_messages[0].content
    assert reply_text(result) == "Tenho 12 violões até R$ 1.000!"


def test_the_system_prompt_is_rebuilt_on_every_model_call(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A conversation that crosses closing time must not keep saying the store is open."""
    now = {"value": SATURDAY_MORNING}
    replies = [AIMessage(content="Estamos abertos!"), AIMessage(content="Já fechamos.")]
    model = ScriptedChatModel(script=replies)
    agent = make_agent(model, db_path, policy, tmp_path, clock=lambda: now["value"])

    ask(agent, "Vocês estão abertos?")
    now["value"] = datetime(2026, 10, 31, 13, 30, tzinfo=CG)
    ask(agent, "E agora?")

    first, second = (calls[0].content for calls in model.seen)
    assert "sábado, 31/10/2026, 10:30" in first and "Aberta agora; hoje fecha às 13:00" in first
    assert "sábado, 31/10/2026, 13:30" in second and "Fechada agora" in second
    assert "INÍCIO DO MANUAL DE POLÍTICAS" in second


def test_each_conversation_remembers_itself_and_only_itself(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """'E quanto custa esse?' works within a thread; another thread knows nothing of it."""
    model = ScriptedChatModel(
        script=[AIMessage(content="r1"), AIMessage(content="r2"), AIMessage(content="r3")]
    )
    agent = make_agent(model, db_path, policy, tmp_path)

    ask(agent, "Quero o Takamine GD20", thread="ana")
    ask(agent, "E quanto custa esse?", thread="ana")
    ask(agent, "Oi", thread="bruno")

    def human_texts(call: int) -> list[str]:
        return [m.text for m in model.seen[call] if m.type == "human"]

    assert human_texts(1) == ["Quero o Takamine GD20", "E quanto custa esse?"]
    assert human_texts(2) == ["Oi"]


def _server_error(code: int = 503) -> Exception:
    """An error shaped like the ones the Gemini API returns (503 overloaded, 504 timeout...)."""
    return ServerError(code, {"error": {"code": code, "message": "high demand"}})


def test_transient_model_errors_are_retried_until_the_model_answers(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Two 503s in a row are absorbed: the customer only sees the final answer."""
    model = ScriptedChatModel(
        script=[_server_error(503), _server_error(504), AIMessage(content="Olá! Posso ajudar?")]
    )
    result = ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert reply_text(result) == "Olá! Posso ajudar?"
    assert len(model.seen) == 3


def test_a_rate_limit_error_is_also_retried(db_path: Path, policy: str, tmp_path: Path) -> None:
    """HTTP 429 (free-tier quota) is transient too."""
    limited = ClientError(429, {"error": {"code": 429, "message": "quota"}})
    model = ScriptedChatModel(script=[limited, AIMessage(content="ok")])
    assert reply_text(ask(make_agent(model, db_path, policy, tmp_path), "Oi")) == "ok"


def test_permanent_errors_are_not_retried(db_path: Path, policy: str, tmp_path: Path) -> None:
    """A bad request will not get better by retrying: it fails at once."""
    bad_request = ClientError(400, {"error": {"code": 400, "message": "invalid argument"}})
    model = ScriptedChatModel(script=[bad_request, AIMessage(content="never reached")])
    with pytest.raises(ClientError):
        ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert len(model.seen) == 1


def test_when_every_retry_fails_the_customer_gets_a_polite_message(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """After 1 try + 4 retries the agent answers with an apology and the WhatsApp number."""
    model = ScriptedChatModel(script=[_server_error(503) for _ in range(5)])
    result = ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert len(model.seen) == 5
    assert "instabilidade" in reply_text(result)
    assert "(67) 3321-4500" in reply_text(result)


def test_old_messages_are_dropped_and_the_kept_history_starts_with_the_customer(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A long chat keeps the latest messages, never opens on an orphan tool result."""
    script: list[Any] = [AIMessage(content="r1")]
    script += [tool_call("list_active_promotions", {}), AIMessage(content="r2")]
    script += [AIMessage(content="r3"), AIMessage(content="r4")]
    model = ScriptedChatModel(script=script)
    agent = make_agent(model, db_path, policy, tmp_path, max_history_messages=4)

    for turn in ("t1", "t2", "t3", "t4"):
        ask(agent, turn)

    last_call = model.seen[-1]
    kinds = [m.type for m in last_call]
    assert kinds[0] == "system" and kinds[1] == "human"  # never starts on tool/ai
    humans = [m.text for m in last_call if m.type == "human"]
    assert humans[-1] == "t4" and "t1" not in humans
    for call in model.seen:  # every tool result keeps the AI request that produced it
        for i, message in enumerate(call):
            if message.type == "tool":
                assert call[i - 1].type == "ai" and call[i - 1].tool_calls


DAILY_QUOTA = (
    "Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. "
    "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20. Generate content "
    "requests per day limit exceeded on Free Tier. Please retry in 11h42m."
)
MINUTE_QUOTA = (
    "Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. "
    "Generate content requests per minute limit exceeded. Please retry in 21s."
)


def test_a_per_minute_quota_error_is_retried(db_path: Path, policy: str, tmp_path: Path) -> None:
    """The Gemini client raises GoogleRateLimitError (no status code): per-minute limits pass."""
    model = ScriptedChatModel(
        script=[GoogleRateLimitError(MINUTE_QUOTA), AIMessage(content="De volta!")]
    )
    assert reply_text(ask(make_agent(model, db_path, policy, tmp_path), "Oi")) == "De volta!"
    assert len(model.seen) == 2


def test_a_daily_quota_error_is_never_retried(db_path: Path, policy: str, tmp_path: Path) -> None:
    """Waiting seconds cannot fix a quota that resets in hours: fail at once, say it plainly."""
    model = ScriptedChatModel(script=[GoogleRateLimitError(DAILY_QUOTA), AIMessage(content="no")])
    with pytest.raises(GoogleRateLimitError) as raised:
        ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert len(model.seen) == 1
    assert is_daily_quota_error(raised.value)
    assert not is_daily_quota_error(GoogleRateLimitError(MINUTE_QUOTA))


def test_a_fallback_model_answers_when_the_primary_quota_is_gone(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Free-tier quotas are per model: the next model in the chain takes the customer's message."""
    primary = ScriptedChatModel(script=[GoogleRateLimitError(DAILY_QUOTA)])
    backup = ScriptedChatModel(script=[AIMessage(content="Resposta do reserva")])
    agent = make_agent(primary, db_path, policy, tmp_path, fallback_models=[backup])

    assert reply_text(ask(agent, "Oi")) == "Resposta do reserva"
    assert len(primary.seen) == 1 and len(backup.seen) == 1  # no retry loop on the dead quota
    assert "INÍCIO DO MANUAL DE POLÍTICAS" in backup.seen[0][0].content  # same prompt and rules


def test_the_fallback_is_not_touched_while_the_primary_works(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """The reserve models keep their own daily quota for when they are needed."""
    primary = ScriptedChatModel(script=[AIMessage(content="ok")])
    backup = ScriptedChatModel(script=[AIMessage(content="should not be used")])
    ask(make_agent(primary, db_path, policy, tmp_path, fallback_models=[backup]), "Oi")
    assert backup.seen == []


def test_an_overloaded_primary_also_hands_over_to_the_fallback(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """A 503 on the primary is answered by the next model instead of making the customer wait."""
    primary = ScriptedChatModel(script=[_server_error(503)])
    backup = ScriptedChatModel(script=[AIMessage(content="Estou aqui")])
    agent = make_agent(primary, db_path, policy, tmp_path, fallback_models=[backup])
    assert reply_text(ask(agent, "Oi")) == "Estou aqui"


def test_when_every_model_has_no_quota_the_error_reaches_the_caller(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Nothing left to try: the quota error propagates (the CLI turns it into a polite message)."""
    first = ScriptedChatModel(script=[GoogleRateLimitError(DAILY_QUOTA)])
    second = ScriptedChatModel(script=[GoogleRateLimitError(DAILY_QUOTA)])
    agent = make_agent(first, db_path, policy, tmp_path, fallback_models=[second])
    with pytest.raises(GoogleRateLimitError):
        ask(agent, "Oi")
    assert len(first.seen) == len(second.seen) == 1


def _groq_error(cls: type[groq.APIStatusError], status: int, message: str) -> groq.APIStatusError:
    """An error shaped like the ones the Groq client raises (status in ``status_code``)."""
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body={"error": {"message": message}})


TPM_LIMIT = "Rate limit reached for model `m` on tokens per minute (TPM): Limit 8000. Try in 22s."
RPD_LIMIT = "Rate limit reached for model `m` on requests per day (RPD): Limit 1000. Try in 2h."


def test_a_groq_tokens_per_minute_limit_is_retried(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """Groq reports the status in status_code (not code): its per-minute limit passes if retried."""
    limited = _groq_error(groq.RateLimitError, 429, TPM_LIMIT)
    model = ScriptedChatModel(script=[limited, AIMessage(content="Pronto!")])
    assert reply_text(ask(make_agent(model, db_path, policy, tmp_path), "Oi")) == "Pronto!"
    assert len(model.seen) == 2


def test_a_groq_daily_limit_is_not_retried(db_path: Path, policy: str, tmp_path: Path) -> None:
    """'requests per day' is the daily quota whichever provider says it."""
    limited = _groq_error(groq.RateLimitError, 429, RPD_LIMIT)
    model = ScriptedChatModel(script=[limited, AIMessage(content="no")])
    with pytest.raises(groq.RateLimitError) as raised:
        ask(make_agent(model, db_path, policy, tmp_path), "Oi")
    assert len(model.seen) == 1 and is_daily_quota_error(raised.value)


def test_groq_server_errors_are_retried_and_bad_requests_are_not(
    db_path: Path, policy: str, tmp_path: Path
) -> None:
    """503 is transient; 400 is not."""
    flaky = ScriptedChatModel(
        script=[_groq_error(groq.InternalServerError, 503, "overloaded"), AIMessage(content="ok")]
    )
    assert reply_text(ask(make_agent(flaky, db_path, policy, tmp_path), "Oi")) == "ok"

    bad_request = _groq_error(groq.BadRequestError, 400, "bad")
    broken = ScriptedChatModel(script=[bad_request, AIMessage(content="never reached")])
    with pytest.raises(groq.BadRequestError):
        ask(make_agent(broken, db_path, policy, tmp_path), "Oi")
    assert len(broken.seen) == 1
