"""Pure date maths for repeating jobs (no Django, no database)."""
import calendar
from datetime import date, timedelta

UNITS = ("day", "week", "month", "year")


def add_interval(anchor: date, amount: int, unit: str) -> date:
    """anchor + amount units. Months clamp to month end (31 Jan + 1 month = 28/29 Feb)."""
    if unit == "day":
        return anchor + timedelta(days=amount)
    if unit == "week":
        return anchor + timedelta(weeks=amount)
    if unit in ("month", "year"):
        m = anchor.month - 1 + amount * (12 if unit == "year" else 1)
        y, m = anchor.year + m // 12, m % 12 + 1
        return date(y, m, min(anchor.day, calendar.monthrange(y, m)[1]))      # 29 Feb + 1 year = 28 Feb
    raise ValueError(f"unknown unit {unit!r}")


def nth(anchor: date, every: int, unit: str, k: int) -> date:
    """k-th occurrence (k=0 is the anchor). Always counted from the anchor, so no drift."""
    return add_interval(anchor, k * every, unit)


def _start_index(anchor, every, unit, start_from):
    if start_from is None or start_from <= anchor:
        return 0
    if unit in ("month", "year"):
        months = (start_from.year - anchor.year) * 12 + start_from.month - anchor.month
        return max(0, months // (every * (12 if unit == "year" else 1)) - 1)
    step = every * (7 if unit == "week" else 1)
    return max(0, (start_from - anchor).days // step - 1)


def occurrences(anchor: date, every: int, unit: str, start_from: date | None = None):
    """Infinite generator of occurrence dates >= start_from. Callers must bound it."""
    k = _start_index(anchor, every, unit, start_from)
    while True:
        d = nth(anchor, every, unit, k)
        if d.year > 9000:
            return
        if start_from is None or d >= start_from:
            yield d
        k += 1


def next_after(anchor: date, every: int, unit: str, after: date) -> date:
    """First occurrence strictly after `after`."""
    return next(occurrences(anchor, every, unit, after + timedelta(days=1)))


def describe(every: int, unit: str) -> str:
    if every == 1:
        return {"day": "Every day", "week": "Every week", "month": "Every month", "year": "Every year"}[unit]
    return f"Every {every} {unit}s"
