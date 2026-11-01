"""What the terminal chat and the Streamlit app share: asking, failures, wiring, rendering."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage

from emporio_agent.agent import build_agent, create_model, is_daily_quota_error, reply_text
from emporio_agent.config import ConfigError, Settings, load_settings
from emporio_agent.data_loader import build_database
from emporio_agent.policies import load_policy_text
from emporio_agent.validation import GroundingEvents

# *bold* as WhatsApp writes it: not part of ** or of a word, with no space after the opening mark.
_WHATSAPP_BOLD = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![*\w])")


_EMAIL = re.compile(r"([\w.+-]{1,2})[\w.+-]*@([\w-]+(?:\.[\w-]+)+)")
_PHONE_FORMATTED = re.compile(r"\((\d{2})\)\s*(\d)\d{3,4}-(\d{4})")
_PHONE_DIGITS = re.compile(r"(?<!\d)(\d{2})(9)\d{4}(\d{4})(?!\d)")

EXAMPLE_QUESTIONS = [
    "Quais violões até R$ 1000?",
    "Qual o status do meu pedido 8?",
    "Me arrependi da compra, posso devolver?",
    "Vocês fazem regulagem de violão?",
]

Grounding = Literal["approved", "retry", "safe_message"]


@dataclass(frozen=True)
class ToolCall:
    """A tool the agent called while answering.

    Attributes:
        name: The tool's name.
        args: Its arguments as short ``key=value`` text, personal data masked.
    """

    name: str
    args: str


@dataclass(frozen=True)
class Escalation:
    """A case the agent registered for a person (personal data masked).

    Attributes:
        reason: The short reason code.
        summary: The neutral summary written for the team.
    """

    reason: str
    summary: str


@dataclass(frozen=True)
class Turn:
    """One answer and what happened inside the agent to produce it.

    Attributes:
        reply: What the assistant said to the customer (empty when the model failed).
        tools: Tools called for this message, in order.
        grounding: ``approved`` (first draft accepted), ``retry`` (a draft was regenerated),
            ``safe_message`` (replaced by the safe message), or ``None`` if the model failed.
        retries: How many drafts the grounding check sent back.
        escalations: Cases registered for a person during this message.
        error: A system message when the model failed (quota, key...): it is not something the
            assistant said, so interfaces show it apart from the conversation.
    """

    reply: str = ""
    tools: tuple[ToolCall, ...] = ()
    grounding: Grounding | None = None
    retries: int = 0
    escalations: tuple[Escalation, ...] = ()
    error: str | None = None


def mask_pii(text: str) -> str:
    """Hide most of an e-mail address or phone number: ``an***@x.com``, ``(67) 9****-7821``."""
    text = _EMAIL.sub(r"\1***@\2", text)
    text = _PHONE_FORMATTED.sub(r"(\1) \2****-\3", text)
    return _PHONE_DIGITS.sub(r"(\1) \2****-\3", text)


def summarize_args(args: Mapping[str, Any], limit: int = 40) -> str:
    """Tool arguments as short ``key=value`` text: masked, long values cut, empty ones skipped."""
    parts = []
    for key, value in args.items():
        if value is None:
            continue
        shown = mask_pii(str(value))
        if len(shown) > limit:
            shown = shown[: limit - 1] + "…"
        parts.append(f"{key}={shown!r}" if isinstance(value, str) else f"{key}={shown}")
    return ", ".join(parts)


def startup_failure_message(error: Exception) -> str:
    """Explain why the agent could not start, without details that could expose a key."""
    if isinstance(error, ConfigError):
        return f"Erro de configuração: {error}"
    return (
        f"Não foi possível iniciar o atendimento ({type(error).__name__}). "
        "Confira a chave e o modelo no .env e tente de novo."
    )


def failure_message(error: Exception) -> str:
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


def ask(agent: Any, thread_id: str, text: str) -> Turn:
    """Send one customer message and return the reply with what happened inside the agent.

    Args:
        agent: Agent built by ``agent.build_agent``.
        thread_id: Conversation the message belongs to (memory is per thread).
        text: What the customer wrote.

    Returns:
        The turn: the reply, the tools called with masked arguments, the grounding outcome and
        the cases registered for a person; if the model fails, only ``error`` is set.
    """
    with GroundingEvents() as grounding:
        try:
            result = agent.invoke(
                {"messages": [("user", text)]}, config={"configurable": {"thread_id": thread_id}}
            )
        except Exception as error:
            return Turn(error=failure_message(error))
    messages = result["messages"]
    last_human = max(i for i, message in enumerate(messages) if message.type == "human")
    calls = [
        call
        for message in messages[last_human:]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    retries = grounding.events.count("grounding_retry")
    outcome: Grounding = "approved"
    if "grounding_safe_message" in grounding.events:
        outcome = "safe_message"
    elif retries:
        outcome = "retry"
    return Turn(
        reply=reply_text(result),
        tools=tuple(ToolCall(call["name"], summarize_args(call["args"])) for call in calls),
        grounding=outcome,
        retries=retries,
        escalations=tuple(
            Escalation(
                reason=mask_pii(str(call["args"].get("reason", ""))),
                summary=mask_pii(str(call["args"].get("summary", ""))),
            )
            for call in calls
            if call["name"] == "escalate_to_human"
        ),
    )


def model_override(argv: Sequence[str], env: Mapping[str, str]) -> str | None:
    """The model the app was asked to use: ``--model X`` (argument) or ``APP_LLM_MODEL``."""
    for position, arg in enumerate(argv):
        if arg == "--model" and position + 1 < len(argv):
            return argv[position + 1]
        if arg.startswith("--model="):
            return arg.split("=", 1)[1]
    return (env.get("APP_LLM_MODEL") or "").strip() or None


def app_settings(env: Mapping[str, str], override: str | None = None) -> Settings:
    """Settings for the app. A chosen model replaces ``LLM_MODEL`` and drops the reserve models.

    Dropping the reserves guarantees that testing the UI on one provider never falls back to
    another one and spends its quota.
    """
    if override:
        env = {**env, "LLM_MODEL": override, "LLM_FALLBACK_MODELS": ""}
    return load_settings(env)


def build_agent_from_settings(settings: Settings) -> Any:
    """Rebuild the database and create the agent the settings describe (model and reserves)."""
    build_database(settings.data_dir, settings.db_path)
    return build_agent(
        create_model(settings.llm_model, settings.llm_temperature),
        fallback_models=[
            create_model(name, settings.llm_temperature) for name in settings.llm_fallback_models
        ],
        grounding_max_retries=settings.grounding_max_retries,
        db_path=settings.db_path,
        escalations_path=settings.escalations_path,
        policy_text=load_policy_text(settings.policy_pdf),
    )


def to_markdown(text: str) -> str:
    """Make an agent reply safe and faithful in a markdown renderer.

    ``$`` is escaped, because markdown renderers read ``$...$`` as math and
    ``R$ 549,00 ... R$ 439,20`` would become a formula. WhatsApp-style ``*bold*`` becomes
    ``**bold**``.
    """
    return _WHATSAPP_BOLD.sub(r"**\1**", text).replace("$", "\\$")
