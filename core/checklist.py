"""Checkpoints for a job - pure Python. One checkpoint per line in the form; stored as a list of short texts."""

MAX_ITEMS, MAX_LEN = 30, 200
_BULLETS = "-*\u2022\u25cf\u25aa\u2013\u2014 \t"


def parse_checkpoints(text):
    """'1 per line' -> ['Check oil level', ...]. Blank lines and leading bullets are dropped; repeats are ignored.
    Raises ValueError with a human message when there are too many or a line is too long."""
    out, seen = [], set()
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = " ".join(raw.lstrip(_BULLETS).split())
        if not line:
            continue
        if len(line) > MAX_LEN:
            raise ValueError(f"A checkpoint can have at most {MAX_LEN} characters: \u201c{line[:30]}\u2026\u201d")
        if line.lower() in seen:
            continue
        seen.add(line.lower())
        out.append(line)
    if len(out) > MAX_ITEMS:
        raise ValueError(f"At most {MAX_ITEMS} checkpoints per job (you entered {len(out)}).")
    return out


def format_checkpoints(items):
    return "\n".join(items or [])


def tick_snapshot(items, ticked):
    """What gets stored with a completion: every checkpoint text with its result.
    `ticked` = posted values (strings); only valid positions count. Returns (snapshot, missing_texts)."""
    ok = set()
    for v in ticked or ():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(items):
            ok.add(n)
    snap = [{"text": t, "ok": i in ok} for i, t in enumerate(items or [])]
    return snap, [s["text"] for s in snap if not s["ok"]]
