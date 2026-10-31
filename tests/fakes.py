"""Test doubles: a scripted chat model, so the agent loop can be tested without any LLM."""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """A chat model that answers with a fixed script and records what it was shown.

    Each call pops the next item of ``script``: an ``AIMessage`` is returned, an error is raised.

    Attributes:
        script: Replies (or errors) to give, in order.
        seen: The messages received on each call, so tests can inspect prompt and history.
    """

    script: list[Any] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedChatModel":
        """Pretend to bind tools: the script already says which ones to call."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.seen.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    """An AI message asking to run one tool."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])
