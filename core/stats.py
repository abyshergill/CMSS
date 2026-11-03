"""Small statistics helpers - pure Python (no Django) so they can be unit-tested."""
from collections import Counter
from datetime import date, timedelta


def monday(d):
    return d - timedelta(days=d.weekday())


def weekly_buckets(days, today, weeks=8):
    """Count dates per week for the last `weeks` weeks (oldest first). Returns [(monday, count)]."""
    start = monday(today) - timedelta(weeks=weeks - 1)
    c = Counter(monday(d) for d in days if d >= start and d <= today)
    return [(start + timedelta(weeks=i), c.get(start + timedelta(weeks=i), 0)) for i in range(weeks)]


def on_time_split(pairs):
    """pairs = [(done_date, planned_date)] -> (on_time, late)."""
    on = sum(1 for done, plan in pairs if done <= plan)
    return on, len(pairs) - on


def top_counts(keys, limit=8, blank="—"):
    c = Counter((k or blank) for k in keys)
    return c.most_common(limit)


def percent(done, total):
    return 0 if total <= 0 else round(100 * done / total)


def weighted_progress(tasks):
    """tasks = [(weight, is_done)] -> dict(total, done, pct, weight_total, weight_done)."""
    wt = sum(w for w, _ in tasks)
    wd = sum(w for w, d in tasks if d)
    return dict(total=len(tasks), done=sum(1 for _, d in tasks if d), pct=percent(wd, wt),
                weight_total=wt, weight_done=wd)
