"""Behavior tests for the store opening hours (policy section 2)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from emporio_agent.hours import store_status

CG = ZoneInfo("America/Campo_Grande")


def _at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    """A moment in 2026, in the store's time zone (2026-11-04 is a Wednesday)."""
    return datetime(2026, month, day, hour, minute, tzinfo=CG)


@pytest.mark.parametrize(
    ("moment", "is_open", "description"),
    [
        (_at(4, 10), True, "Aberta agora; hoje fecha às 18:00"),
        (_at(4, 17, 59), True, "Aberta agora; hoje fecha às 18:00"),
        (_at(4, 18), False, "Fechada agora. Reabre amanhã (quinta-feira) às 09:00"),
        (_at(4, 8, 59), False, "Fechada agora. Reabre hoje às 09:00"),
        (_at(6, 19), False, "Fechada agora. Reabre amanhã (sábado) às 09:00"),
        (_at(7, 12, 59), True, "Aberta agora; hoje fecha às 13:00"),
        (_at(7, 13), False, "Fechada agora. Reabre segunda-feira às 09:00"),
        (_at(8, 11), False, "Fechada agora. Reabre amanhã (segunda-feira) às 09:00"),
    ],
)
def test_weekly_hours(moment: datetime, is_open: bool, description: str) -> None:
    """Mon-Fri 09:00-18:00, Saturday 09:00-13:00, Sunday closed; closing time is already closed."""
    status = store_status(moment)
    assert status.is_open is is_open
    assert status.description == description


def test_a_moment_in_another_time_zone_is_read_in_the_store_time_zone() -> None:
    """15:00 UTC on Saturday 2026-10-31 is 11:00 in Campo Grande (UTC-4): the store is open."""
    status = store_status(datetime(2026, 10, 31, 15, 0, tzinfo=UTC))
    assert status.is_open
    assert status.description == "Aberta agora; hoje fecha às 13:00"


@pytest.mark.parametrize(
    ("moment", "description"),
    [
        # Finados, Monday 2026-11-02, closed although it is a weekday.
        (_at(2, 10), "Fechada agora (feriado). Reabre amanhã (terça-feira) às 09:00"),
        # Sunday before the holiday: the next opening skips Monday.
        (_at(1, 10), "Fechada agora. Reabre terça-feira às 09:00"),
        # Independence Day, Monday 2026-09-07.
        (_at(7, 11, month=9), "Fechada agora (feriado). Reabre amanhã (terça-feira) às 09:00"),
        # Christmas, Friday 2026-12-25: reopens on Saturday.
        (_at(25, 10, month=12), "Fechada agora (feriado). Reabre amanhã (sábado) às 09:00"),
    ],
)
def test_national_holidays_with_a_fixed_date_close_the_store(
    moment: datetime, description: str
) -> None:
    """Policy: 'Domingo e Feriados: Fechado'. Movable holidays are not modeled (documented)."""
    status = store_status(moment)
    assert not status.is_open
    assert status.description == description
