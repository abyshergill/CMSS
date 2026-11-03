"""Scheduling engine - pure Python, no Django.

Turns one job + its exceptions + its completions into dated occurrences.
`job` is any object with: kind ('once'|'repeat'), start_date, every, unit, end_date.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import calendar

from . import recurrence as rec

LOOKBACK_DAYS = 60         # missed repeating dates stay on the Overdue list this long
MOVE_LIMIT_DAYS = 92       # one date can be moved at most ~3 months
MAX_SKIP_SPAN_DAYS = 366   # one exception range can cover at most a year


@dataclass
class Occ:
    job: Any
    orig: date             # the date the schedule says (key for done / skip / move)
    when: date             # the date it will really happen (differs if moved)
    status: str            # done | skipped | overdue | today | upcoming
    moved: bool = False
    skip_range: Any = None
    completion: Any = None
    exception: Any = None
    can_do: bool = False
    can_manage: bool = False
    state: str = "ok"          # ok | early | closed - may it be marked done today? (see finish_state)
    opens: Any = None          # first day it may be marked done
    closes: Any = None         # last day it may be marked done


def status_of(when, today, finished, skipped=False):
    if finished:
        return "done"
    if skipped:
        return "skipped"
    return "overdue" if when < today else "today" if when == today else "upcoming"


def expand(job, lo, hi, today, *, skip_ranges=(), moves=None, done_on=None, include_skipped=False):
    """Occurrences whose real date is in [lo, hi].

    skip_ranges: objects with .start_date / .end_date (inclusive) - every repeating date inside is skipped
    moves:       {original_date: new_date}
    done_on:     {original_date: date_it_was_completed}
    """
    moves, done_on = moves or {}, done_on or {}
    skip_ranges = list(skip_ranges)
    if job.kind == "once":
        origs = [job.start_date]
    else:
        first = max(job.start_date, lo - timedelta(days=MOVE_LIMIT_DAYS))
        limit = hi + timedelta(days=MOVE_LIMIT_DAYS)
        origs = []
        for o in rec.occurrences(job.start_date, job.every, job.unit, first):
            if o > limit or (job.end_date and o > job.end_date):
                break
            origs.append(o)
    out = []
    for o in origs:
        finished = done_on.get(o)
        new = moves.get(o) if job.kind == "repeat" else None
        when = new or o
        hit = None
        if job.kind == "repeat" and new is None and finished is None:
            hit = next((r for r in skip_ranges if r.start_date <= o <= r.end_date), None)
        if hit and not include_skipped:
            continue
        # a one-off job never falls off the list until the day after it is done
        keep_old = job.kind == "once" and when < lo and (finished is None or finished == today)
        if not (lo <= when <= hi or keep_old):
            continue
        out.append(Occ(job=job, orig=o, when=when, moved=new is not None, skip_range=hit,
                       status=status_of(when, today, finished is not None, skipped=hit is not None)))
    out.sort(key=lambda o: o.when)
    return out


def period_kind(unit, every=1):
    """Which calendar period a repeating job belongs to: day | week | month | year.
    'Every 12 months' is a yearly job, so it counts as one."""
    if unit == "year" or (unit == "month" and every and every % 12 == 0):
        return "year"
    return unit if unit in ("day", "week", "month") else "day"


PERIOD_WORDS = {"day": "that day", "week": "that week (Mon-Sun)", "month": "that month", "year": "that year"}


def window(job, when):
    """(first, last) day on which the occurrence planned for `when` may be marked done: its own day, week (Mon-Sun),
    month or year. A one-off has no window -> (None, None)."""
    if job.kind != "repeat":
        return None, None
    k = period_kind(job.unit, job.every)
    if k == "day":
        return when, when
    if k == "week":
        mon = when - timedelta(days=when.weekday())
        return mon, mon + timedelta(days=6)
    if k == "month":
        return when.replace(day=1), when.replace(day=calendar.monthrange(when.year, when.month)[1])
    return date(when.year, 1, 1), date(when.year, 12, 31)


def finish_state(job, when, today):
    """('ok'|'early'|'closed', first_day, last_day).
    early  = its period has not started: a repeating job cannot be finished in advance
    closed = its period is over (missed); only an admin may still record it, as a late entry"""
    lo, hi = window(job, when)
    if lo is None:
        return "ok", None, None
    state = "early" if today < lo else "closed" if today > hi else "ok"
    return state, lo, hi


def valid_occurrence(job, d):
    """True if `d` is a real (original) date of this job's schedule."""
    if job.kind == "once":
        return d == job.start_date
    if d < job.start_date or (job.end_date and d > job.end_date):
        return False
    return next(rec.occurrences(job.start_date, job.every, job.unit, d)) == d


def once_status(job, finished_on, today):
    """Status of a one-off (e.g. project task)."""
    return status_of(job.start_date, today, finished_on is not None)


def health(statuses):
    """'overdue' | 'soon' | 'ok' for one piece of equipment."""
    s = set(statuses)
    return "overdue" if "overdue" in s else "soon" if s & {"today", "upcoming"} else "ok"
