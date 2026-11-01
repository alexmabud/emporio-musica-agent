"""Grounding check: a reply may only state amounts and tracking codes that come from a source.

Policy 7.1 says the agent never states a price without checking the system. The prompt asks for it;
this module makes it a guarantee in code. A ``R$`` amount in the final reply must appear in a tool
result, in the policy manual or in what the customer wrote, and a tracking code must appear in a
tool result. Otherwise the model is asked once more (with a note naming the offending values) and,
if it still fails, the customer gets a safe message with no amount in it.
"""

import json
import logging
import re
import uuid
from decimal import Decimal
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, after_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

SAFE_MESSAGE = (
    "Prefiro não te passar um valor sem ter certeza. Pode me perguntar de novo, ou falar com a "
    "nossa equipe pelo WhatsApp (67) 3321-4500 que eles confirmam pra você."
)

# Correction notes are told apart from real customer messages by their id (provider-safe).
_NOTE_ID_PREFIX = "grounding-note-"

# A number as written: digits with "." or "," between digit groups ("1.000,50", "439.20", "1,000").
# A separator at the end of a sentence ("R$ 500,00.") is not part of it.
_TOKEN = r"\d+(?:[.,]\d+)*"
_MONEY = re.compile(rf"R\$\s*({_TOKEN})")
_NUMBER = re.compile(_TOKEN)
_THOUSANDS_DOT = re.compile(r"\d{1,3}(?:\.\d{3})+")
_THOUSANDS_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+")
_TRACKING = re.compile(r"\b[A-Z]{2,4}\d{7,9}[A-Z]{2}\b")


def _clean(text: str) -> str:
    """Turn the odd space characters (U+202F, U+00A0) some models write after ``R$`` into spaces."""
    return text.replace(" ", " ").replace(" ", " ")


def _value(token: str) -> Decimal:
    """The numeric value of an amount as written, in Brazilian or American format.

    A thousands separator always separates groups of exactly 3 digits; anything else after a
    separator is the decimal part. So "1.000" and "12.500" are thousands, "439.20" is 439 and 20
    cents, "1.000,50" and "1,000.50" are both 1000.50, and "1,000" is 1000.
    """
    if "." in token and "," in token:
        decimal_sep = "." if token.rindex(".") > token.rindex(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        return Decimal(token.replace(thousands_sep, "").replace(decimal_sep, "."))
    if "," in token:
        if _THOUSANDS_COMMA.fullmatch(token):
            return Decimal(token.replace(",", ""))
        return Decimal(token.replace(",", "."))
    if _THOUSANDS_DOT.fullmatch(token):
        return Decimal(token.replace(".", ""))
    return Decimal(token)


def amounts_in(text: str) -> set[Decimal]:
    """The ``R$`` amounts written in a text, by value (``R$ 1.000`` equals ``R$1000,00``)."""
    return {_value(token) for token in _MONEY.findall(_clean(text))}


def _numbers_in(text: str) -> set[Decimal]:
    """Every number in a text, by value; used for customer messages ("até 1000")."""
    return {_value(token) for token in _NUMBER.findall(_clean(text))}


def tracking_codes_in(text: str) -> set[str]:
    """The tracking codes (like ``BRJL5544332BR``) written in a text."""
    return set(_TRACKING.findall(text.upper()))


def find_ungrounded(
    reply: str, tool_texts: list[str], customer_texts: list[str], policy_text: str
) -> list[str]:
    """List the amounts and tracking codes in ``reply`` that no source supports.

    Args:
        reply: The model's final text for the customer.
        tool_texts: Tool results of the whole conversation (prices from earlier turns count).
        customer_texts: What the customer wrote ("violões até 1000" grounds an echo of 1000).
        policy_text: The policy manual (it holds amounts such as the R$ 500 free-shipping rule).

    Returns:
        The offending values as written in the reply (empty when everything is grounded).
    """
    known_amounts = amounts_in(policy_text)
    known_codes: set[str] = set()
    for text in tool_texts:
        known_amounts |= amounts_in(text)
        known_codes |= tracking_codes_in(text)
    for text in customer_texts:
        known_amounts |= _numbers_in(text)

    reply = _clean(reply)
    ungrounded = [
        match.group(0)
        for match in _MONEY.finditer(reply)
        if _value(match.group(1)) not in known_amounts
    ]
    ungrounded += [code for code in sorted(tracking_codes_in(reply)) if code not in known_codes]
    return ungrounded


def correction_note(ungrounded: list[str]) -> str:
    """The message that asks the model to redo a reply that stated unverified values."""
    return (
        f"[Verificação automática] Sua resposta citou valores que não vieram de nenhuma consulta: "
        f"{', '.join(ungrounded)}. Consulte a ferramenta adequada e responda de novo usando "
        "somente os valores que ela devolver, ou diga que vai confirmar com a equipe. Escreva os "
        "valores no formato brasileiro (R$ 1.234,56)."
    )


def _log_failure(event: str, ungrounded: list[str], **fields: int) -> None:
    """Log one grounding failure as a JSON line: the unverified values, no customer text."""
    logger.warning(
        json.dumps({"event": event, "ungrounded": ungrounded, **fields}, ensure_ascii=False)
    )


class GroundingEvents(logging.Handler):
    """Records the grounding check's failures while it is active (use it as a context manager).

    Attributes:
        events: Event names in order: ``grounding_retry`` and ``grounding_safe_message``.
    """

    def __init__(self) -> None:
        """Start with no events."""
        super().__init__(level=logging.WARNING)
        self.events: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep the event name of a grounding record."""
        try:
            self.events.append(json.loads(record.getMessage())["event"])
        except (ValueError, KeyError):
            pass

    def __enter__(self) -> "GroundingEvents":
        """Start recording."""
        logger.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> None:
        """Stop recording."""
        logger.removeHandler(self)


def _is_note(message: BaseMessage) -> bool:
    return (message.id or "").startswith(_NOTE_ID_PREFIX)


def build_grounding_check(policy_text: str, max_retries: int) -> AgentMiddleware[Any, Any]:
    """Build the middleware that checks the final reply right after the model writes it.

    Args:
        policy_text: The policy manual, a source of valid amounts.
        max_retries: How many times the model may redo an ungrounded reply before the safe
            message is sent (``0`` goes straight to the safe message).

    Returns:
        An ``after_model`` middleware. Replies that still call tools are not checked; the final
        text is. Bad drafts and correction notes are removed from the history.
    """

    @after_model(can_jump_to=["model"])
    def grounding_check(state: AgentState[Any], runtime: Runtime[Any]) -> dict[str, Any] | None:
        """Accept a grounded reply, or regenerate it, or replace it with the safe message."""
        messages = state["messages"]
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None

        turn_start = max(
            (i for i, m in enumerate(messages) if m.type == "human" and not _is_note(m)),
            default=0,
        )
        notes = [m for m in messages[turn_start:] if _is_note(m)]
        ungrounded = find_ungrounded(
            last.text,
            tool_texts=[m.text for m in messages if m.type == "tool"],
            customer_texts=[m.text for m in messages if m.type == "human" and not _is_note(m)],
            policy_text=policy_text,
        )

        if not ungrounded:
            return {"messages": [RemoveMessage(id=n.id) for n in notes if n.id]} if notes else None
        if len(notes) < max_retries:
            _log_failure(
                "grounding_retry", ungrounded, attempt=len(notes) + 1, max_retries=max_retries
            )
            note = HumanMessage(
                content=correction_note(ungrounded), id=f"{_NOTE_ID_PREFIX}{uuid.uuid4().hex}"
            )
            return {"messages": [RemoveMessage(id=last.id or ""), note], "jump_to": "model"}
        _log_failure("grounding_safe_message", ungrounded, retries_used=len(notes))
        removals = [RemoveMessage(id=m.id) for m in (last, *notes) if m.id]
        return {"messages": [*removals, AIMessage(content=SAFE_MESSAGE)]}

    return grounding_check
