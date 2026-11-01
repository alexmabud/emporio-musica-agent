"""What an eval run records and the checks that score it.

Every scenario has checks on two levels: **critical** ones decide pass or fail (a wrong or unsafe
behavior), **desirable** ones are reported apart (a better answer, but not a failure to miss).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from emporio_agent.validation import amounts_in

Level = Literal["critical", "desirable"]


@dataclass(frozen=True)
class TurnResult:
    """One customer message and what the agent did with it.

    Attributes:
        user: The customer's message.
        reply: The agent's final text for this turn.
        tools: Names of the tools called while answering, in order.
        latency_s: Seconds the turn took, retries and waits included.
    """

    user: str
    reply: str
    tools: tuple[str, ...]
    latency_s: float


@dataclass(frozen=True)
class Run:
    """A whole scenario: its turns and the escalation records the agent wrote.

    Attributes:
        turns: One result per customer message, in order.
        escalations: Records appended to the escalation log during the scenario.
    """

    turns: tuple[TurnResult, ...]
    escalations: tuple[dict[str, Any], ...] = ()

    @property
    def reply(self) -> str:
        """The final text of the last turn."""
        return self.turns[-1].reply

    @property
    def tools(self) -> tuple[str, ...]:
        """Every tool called in the scenario."""
        return tuple(name for turn in self.turns for name in turn.tools)


@dataclass(frozen=True)
class Check:
    """A named yes/no test of a run, on one of the two levels.

    Attributes:
        name: What is being verified, in plain words (shown in reports).
        level: ``critical`` decides pass/fail; ``desirable`` is reported apart.
        test: Receives the run and returns whether it holds.
    """

    name: str
    level: Level
    test: Callable[[Run], bool]


def critical(name: str, test: Callable[[Run], bool]) -> Check:
    """A check whose failure fails the scenario."""
    return Check(name, "critical", test)


def desirable(name: str, test: Callable[[Run], bool]) -> Check:
    """A check that is only reported (a better answer, not a requirement)."""
    return Check(name, "desirable", test)


def _plain(text: str) -> str:
    """Normalize the characters some models write: U+202F/U+00A0 spaces, U+2011 hyphens."""
    return text.replace(" ", " ").replace(" ", " ").replace("‑", "-").replace("‐", "-")


def _reply(run: Run, turn: int) -> str:
    return _plain(run.turns[turn].reply)


def no_tools(turn: int | None = None) -> Callable[[Run], bool]:
    """No tool was called, in the given turn or in the whole run."""
    return lambda run: not (run.turns[turn].tools if turn is not None else run.tools)


def calls(*tools: str, turn: int = -1) -> Callable[[Run], bool]:
    """At least one of ``tools`` was called in the given turn (the last by default)."""
    return lambda run: any(name in run.turns[turn].tools for name in tools)


def says(pattern: str, turn: int = -1) -> Callable[[Run], bool]:
    """The reply of the given turn matches the regular expression (case-insensitive)."""
    return lambda run: re.search(pattern, _reply(run, turn), re.IGNORECASE | re.DOTALL) is not None


def lacks(pattern: str, turn: int = -1) -> Callable[[Run], bool]:
    """The reply of the given turn does not match the regular expression."""
    return lambda run: not says(pattern, turn)(run)


def no_amounts(turn: int = -1) -> Callable[[Run], bool]:
    """The reply states no ``R$`` amount."""
    return lambda run: not amounts_in(_reply(run, turn))


def has_amounts(turn: int = -1) -> Callable[[Run], bool]:
    """The reply states at least one ``R$`` amount."""
    return lambda run: bool(amounts_in(_reply(run, turn)))


def amounts_at_most(limit: int, turn: int = -1) -> Callable[[Run], bool]:
    """Every ``R$`` amount in the reply is at most ``limit``."""
    return lambda run: all(value <= limit for value in amounts_in(_reply(run, turn)))


def escalated(reason: str | None = None) -> Callable[[Run], bool]:
    """A case was registered (with the given ``reason`` code, when one is asked for)."""
    return lambda run: any(reason is None or rec.get("reason") == reason for rec in run.escalations)


def escalation_says(pattern: str) -> Callable[[Run], bool]:
    """The summary of a registered case matches the regular expression."""
    return lambda run: any(
        re.search(pattern, _plain(str(rec.get("summary", ""))), re.IGNORECASE)
        for rec in run.escalations
    )


def escalation_lacks(pattern: str) -> Callable[[Run], bool]:
    """No registered case has a summary that matches the regular expression."""
    return lambda run: not escalation_says(pattern)(run)
