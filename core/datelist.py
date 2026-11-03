"""Parse a list of calendar dates typed or picked by a person - pure Python (no Django), unit-tested."""
import re
from datetime import date

MAX_DATES = 366
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SPLIT = re.compile(r"[,\s;]+")


def parse_date_list(text, *, limit=MAX_DATES, min_year=2000, max_year=2100):
    """'2026-11-03, 2026-11-10' -> [date(2026,11,3), date(2026,11,10)]  (sorted, no duplicates).

    Dates may be separated by commas, semicolons, spaces or new lines. They do NOT have to be consecutive.
    Only YYYY-MM-DD is accepted. Raises ValueError with a message that can be shown to the user."""
    found = set()
    for part in _SPLIT.split((text or "").strip()):
        if not part:
            continue
        m = _ISO.match(part)
        if not m:
            raise ValueError(f"'{part[:20]}' is not a date. Use the format YYYY-MM-DD, e.g. 2026-11-03.")
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            raise ValueError(f"'{part}' is not a real calendar date.")
        if not (min_year <= d.year <= max_year):
            raise ValueError(f"'{part}' is outside the years {min_year}-{max_year}.")
        found.add(d)
        if len(found) > limit:
            raise ValueError(f"Please list at most {limit} days.")
    return sorted(found)


def format_date_list(dates):
    return ", ".join(d.isoformat() for d in sorted(set(dates)))
