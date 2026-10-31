"""Store opening hours (policy section 2), computed in code instead of left to the LLM.

Monday to Friday 09:00-18:00, Saturday 09:00-13:00, closed on Sunday. At closing time the store is
already closed.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

STORE_TIMEZONE = ZoneInfo("America/Campo_Grande")

_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

# Weekday (Monday = 0) -> (opens, closes). Sunday is absent: closed.
_HOURS = {
    **dict.fromkeys(range(5), (time(9), time(18))),
    5: (time(9), time(13)),
}

# National holidays with a fixed date, as (month, day). Movable holidays (Carnival, Good Friday,
# Corpus Christi) and state/city holidays are NOT modeled: they would need a calendar.
_FIXED_HOLIDAYS = frozenset(
    {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}
)


@dataclass(frozen=True)
class StoreStatus:
    """Whether the store is open at a given moment, in words the agent can quote.

    Attributes:
        is_open: ``True`` while the store is open.
        description: Customer-ready sentence in Portuguese, including when it reopens if closed.
    """

    is_open: bool
    description: str


def _opening(day: date) -> tuple[time, time] | None:
    """Opening and closing time of a day, or ``None`` if the store is closed all day."""
    if (day.month, day.day) in _FIXED_HOLIDAYS:
        return None
    return _HOURS.get(day.weekday())


def _when(day: date, today: date) -> str:
    """Say a day relative to today: 'hoje', 'amanhã (quinta-feira)' or 'segunda-feira'."""
    name = _WEEKDAYS[day.weekday()]
    if day == today:
        return "hoje"
    if day == today + timedelta(days=1):
        return f"amanhã ({name})"
    return name


def store_status(now: datetime) -> StoreStatus:
    """Tell whether the store is open at ``now`` and, if not, when it reopens.

    Args:
        now: A time-zone-aware moment (converted to the store's time zone).

    Returns:
        The status with a customer-ready description.
    """
    local = now.astimezone(STORE_TIMEZONE)
    today = local.date()
    hours = _opening(today)
    if hours is not None:
        opens, closes = hours
        if opens <= local.time() < closes:
            return StoreStatus(True, f"Aberta agora; hoje fecha às {closes:%H:%M}")
    holiday = (today.month, today.day) in _FIXED_HOLIDAYS
    closed = "Fechada agora (feriado)." if holiday else "Fechada agora."
    for offset in range(8):
        day = today + timedelta(days=offset)
        day_hours = _opening(day)
        if day_hours is None or (offset == 0 and local.time() >= day_hours[0]):
            continue
        return StoreStatus(False, f"{closed} Reabre {_when(day, today)} às {day_hours[0]:%H:%M}")
    raise AssertionError("the store has regular hours every week")  # pragma: no cover
