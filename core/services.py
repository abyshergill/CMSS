"""Glue between the database and the pure scheduling engine."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from . import engine, stats
from .models import Completion, Job, MoveException, SkipRange
from .permissions import can_do_job, can_manage_job, is_admin, scope_q

User = get_user_model()


def today():
    return timezone.localdate()


def get_scope(request):
    """'all', 'mine' or (admins only) 'by_me'. Remembered in the session; techs start on 'mine', admins on 'all'."""
    allowed = ("mine", "all", "by_me") if is_admin(request.user) else ("mine", "all")
    v = request.GET.get("scope")
    if v in allowed:
        request.session["scope"] = v
        return v
    stored = request.session.get("scope")
    return stored if stored in allowed else ("all" if is_admin(request.user) else "mine")


def scoped_completions(qs, user, scope):
    if scope == "mine":
        qs = qs.filter(Q(done_by=user) | scope_q(user, "mine", "job__"))
    elif scope == "by_me":
        qs = qs.filter(scope_q(user, "by_me", "job__"))
    return qs


def day_bounds(d1, d2):
    """aware datetimes covering the local days d1..d2 inclusive -> [start, end)"""
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(d1, time.min), tz),
            timezone.make_aware(datetime.combine(d2 + timedelta(days=1), time.min), tz))


def load_occurrences(lo, hi, user, *, scope="all", jobs=None, include_skipped=False, include_inactive=False):
    """All dated occurrences with real date in [lo, hi], with exceptions + completions applied."""
    t = today()
    qs = jobs if jobs is not None else Job.objects.all()
    qs = qs.select_related("equipment__area", "assignee", "assigned_group", "project", "project__owner").prefetch_related("attachments")
    if not include_inactive:
        qs = qs.filter(active=True).filter(Q(equipment__isnull=True) | Q(equipment__active=True)) \
               .exclude(project__status="closed")
    q = scope_q(user, scope)
    if q is not None:
        qs = qs.filter(q)
    jobs_l = list(qs)
    if not jobs_l:
        return []
    ids = [j.id for j in jobs_l]
    moves, move_objs = defaultdict(dict), {}
    for m in MoveException.objects.filter(job_id__in=ids):
        moves[m.job_id][m.orig_date] = m.new_date
        move_objs[(m.job_id, m.orig_date)] = m
    own_ranges = defaultdict(list)                 # skipped days belong to ONE job; there are no plant-wide closures
    for r in SkipRange.objects.filter(job_id__in=ids).select_related("created_by"):
        own_ranges[r.job_id].append(r)
    comps, done_on = defaultdict(dict), defaultdict(dict)
    for c in Completion.objects.filter(job_id__in=ids).select_related("done_by"):
        comps[c.job_id][c.due_date] = c
    for j in jobs_l:
        if j.kind == Job.ONCE and comps[j.id]:                       # one-offs: any completion counts,
            c = next(iter(comps[j.id].values()))                      # even if the due date was edited later
            comps[j.id] = {j.start_date: c}
        for d, c in comps[j.id].items():
            done_on[j.id][d] = c.done_date
    out = []
    for j in jobs_l:
        ranges = own_ranges[j.id] if j.kind == Job.REPEAT else []
        for o in engine.expand(j, lo, hi, t, skip_ranges=ranges, moves=moves[j.id], done_on=done_on[j.id],
                               include_skipped=include_skipped):
            o.completion = comps[j.id].get(o.orig)
            o.exception = move_objs.get((j.id, o.orig))
            o.can_do = can_do_job(user, j)
            o.can_manage = can_manage_job(user, j) and j.kind == Job.REPEAT
            if j.kind == Job.REPEAT and o.status in ("overdue", "today", "upcoming"):
                o.state, o.opens, o.closes = engine.finish_state(j, o.when, t)
            out.append(o)
    out.sort(key=lambda o: (o.when, o.job.title.lower()))
    return out


def sync_skip_dates(job, dates, user):
    """Make the job's single skipped days equal `dates` (a list of date objects, any days, not necessarily consecutive).
    Multi-day ranges added from the job page are left alone. A one-time job keeps no skipped days."""
    singles = job.skip_ranges.filter(start_date=F("end_date"))
    if job.kind != Job.REPEAT:
        singles.delete()
        return
    want = set(dates)
    have = {r.start_date: r.pk for r in singles}
    gone = [pk for day, pk in have.items() if day not in want]
    if gone:
        SkipRange.objects.filter(pk__in=gone).delete()
    SkipRange.objects.bulk_create([SkipRange(job=job, start_date=d, end_date=d, created_by=user) for d in sorted(want - set(have))])


def planned_date_for(job, due):
    m = MoveException.objects.filter(job=job, orig_date=due).first()
    return m.new_date if m else due


def project_tasks(project, t=None):
    t = t or today()
    comp = Completion.objects.filter(job=OuterRef("pk"))
    rows = []
    for j in project.tasks.select_related("assignee", "assigned_group").annotate(is_done=Exists(comp)).order_by("start_date", "title"):
        c = j.completions.first() if j.is_done else None
        rows.append(dict(job=j, completion=c,
                         status=engine.status_of(j.start_date, t, j.is_done)))
    return rows


def project_stats_map(projects, t=None):
    """{project_id: stats dict + overdue count}, computed with a single query."""
    t = t or today()
    comp = Completion.objects.filter(job=OuterRef("pk"))
    by = defaultdict(list)
    for j in Job.objects.filter(project__in=projects, active=True).annotate(is_done=Exists(comp)):
        by[j.project_id].append(j)
    res = {}
    for p in projects:
        tasks = by.get(p.id, [])
        s = stats.weighted_progress([(j.weight, j.is_done) for j in tasks])
        s["overdue"] = sum(1 for j in tasks if not j.is_done and j.start_date < t)
        s["open"] = s["total"] - s["done"]
        res[p.id] = s
    return res


def equipment_health_map(user, t=None):
    t = t or today()
    st = defaultdict(list)
    for o in load_occurrences(t - timedelta(days=engine.LOOKBACK_DAYS), t + timedelta(days=7), user):
        if o.job.equipment_id:
            st[o.job.equipment_id].append(o.status)
    return {k: engine.health(v) for k, v in st.items()}, {k: v.count("overdue") for k, v in st.items()}
