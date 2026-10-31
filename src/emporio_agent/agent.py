"""The customer service agent: a tool-calling loop with memory, retry and a trimmed history."""

import re
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentState,
    ModelFallbackMiddleware,
    ModelRequest,
    ModelRetryMiddleware,
    before_model,
    dynamic_prompt,
)
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import RemoveMessage, trim_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from emporio_agent.hours import STORE_TIMEZONE
from emporio_agent.prompts import build_system_prompt
from emporio_agent.tools import build_tools

# Free tiers throttle (429) and busy models answer 503/504 now and then: a retry usually passes.
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_MODEL_RETRIES = 4

# Waits 4, 8, 16 and 32 s: about a minute, the span of a per-minute token window (Groq free tier).
RETRY_INITIAL_DELAY = 4.0

# Messages (customer, model and tool) kept per conversation; older ones stop being re-sent.
MAX_HISTORY_MESSAGES = 40

APOLOGY = (
    "Estou com uma instabilidade para consultar o sistema agora. Pode tentar de novo em alguns "
    "instantes? Se preferir, fale com a nossa equipe pelo WhatsApp (67) 3321-4500."
)


def is_daily_quota_error(error: Exception) -> bool:
    """Tell whether a model error means the free-tier *daily* quota is used up.

    Google words it as "requests per day limit exceeded" (quota id ``...PerDay...``). Waiting
    seconds cannot fix that: it resets only the next day.
    """
    return re.search(r"per ?day", str(error), re.IGNORECASE) is not None


def is_transient(error: Exception) -> bool:
    """Tell whether a failed model call is worth retrying.

    Args:
        error: The exception raised by the model call.

    Returns:
        ``True`` for per-minute rate limits, overload/timeout statuses and network failures;
        ``False`` for the daily quota and for anything retrying cannot fix (bad request, invalid
        key, programming errors).
    """
    if is_daily_quota_error(error):
        return False
    if isinstance(error, httpx.TransportError | ModelRateLimitError):
        return True
    # Google's errors carry the HTTP status in ``code``; Groq's (OpenAI-style) in ``status_code``.
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return status in _TRANSIENT_STATUS_CODES


def build_agent(
    model: BaseChatModel,
    *,
    db_path: Path,
    escalations_path: Path,
    policy_text: str,
    clock: Callable[[], datetime] | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    retry_initial_delay: float = RETRY_INITIAL_DELAY,
    max_history_messages: int = MAX_HISTORY_MESSAGES,
    fallback_models: Sequence[BaseChatModel] = (),
) -> Any:
    """Build the agent: the model, the six store tools, the prompt and per-thread memory.

    Args:
        model: Chat model that supports tool calling.
        db_path: SQLite file built by ``data_loader.build_database``.
        escalations_path: JSON Lines file where human escalations are appended.
        policy_text: Clean text of the policy manual.
        clock: Returns the current moment (store time zone by default); tests inject one.
        checkpointer: Where conversations are remembered (in memory by default).
        retry_initial_delay: Seconds before the first retry of a failed model call.
        max_history_messages: How many of the latest messages of a conversation are kept.
        fallback_models: Models tried in order when the main one fails. Free-tier quotas are per
            model, so reserves keep the agent answering when one runs out.

    Returns:
        A compiled agent; invoke it with ``{"messages": [...]}`` and a ``thread_id``.
    """
    now = clock or (lambda: datetime.now(STORE_TIMEZONE))

    @dynamic_prompt
    def system_prompt(request: ModelRequest) -> str:
        """Rebuild the prompt before each model call so the date and opening status stay true."""
        return build_system_prompt(policy_text, now())

    @before_model
    def trim_history(state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Keep the latest messages, cutting only where a customer message begins.

        Starting on a customer message guarantees a tool result is never separated from the
        model request that produced it.
        """
        messages = state["messages"]
        if len(messages) <= max_history_messages:
            return None
        kept = trim_messages(
            messages,
            strategy="last",
            token_counter=len,
            max_tokens=max_history_messages,
            start_on="human",
        )
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *kept]}

    return create_agent(
        model,
        tools=build_tools(db_path, escalations_path, now=now),
        middleware=[
            ModelRetryMiddleware(
                max_retries=MAX_MODEL_RETRIES,
                retry_on=is_transient,
                on_failure=lambda error: APOLOGY,
                initial_delay=retry_initial_delay,
            ),
            *([ModelFallbackMiddleware(*fallback_models)] if fallback_models else []),
            trim_history,
            system_prompt,
        ],
        checkpointer=checkpointer or InMemorySaver(),
    )


def reply_text(result: dict[str, Any]) -> str:
    """Text of the agent's final message in an invoke result."""
    return str(result["messages"][-1].text)
