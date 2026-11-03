from datetime import timedelta
import calendar as calmod

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import charts, engine, filecheck, stats
from ..forms import AttachForm, DoneForm, JobForm, MoveForm, SkipForm
from ..models import Area, Completion, Job, JobAttachment, MoveException, SkipRange
from ..permissions import admin_required, can_do_job, can_manage_job, is_admin, scope_q
from ..security import safe_redirect_target
from ..services import (get_scope, load_occurrences, planned_date_for, scoped_completions, sync_skip_dates, today)
from ._util import form_errors, paginate, save_attachments, serve_file


def _back(request, default):
    return safe_redirect_target(request, request.POST.get("back"), default)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Today
@login_required
def today_view(request):
    t, scope = today(), get_scope(request)
    occs = load_occurrences(t - timedelta(days=engine.LOOKBACK_DAYS), t + timedelta(days=7), request.user, scope=scope)
    area = _int(request.GET.get("area"))
    if area:
        occs = [o for o in occs if o.job.equipment_id and o.job.equipment.area_id == area]
    groups = {k: [o for o in occs if o.status == k] for k in ("overdue", "today", "upcoming")}
    counts = {k: len(v) for k, v in groups.items()}
    page = paginate(request, groups["overdue"] + groups["today"] + groups["upcoming"], default=25)      # one list, in this order
    shown = {k: [o for o in page if o.status == k] for k in groups}
    done_today = [o for o in occs if o.status == "done" and o.completion and o.completion.done_date == t]
    start = t - timedelta(weeks=8)
    from ..services import day_bounds
    lo_dt, _ = day_bounds(start, t)
    dates = [c.done_date for c in scoped_completions(Completion.objects.filter(done_at__gte=lo_dt), request.user, scope)]
    donut = charts.donut([("Overdue", len(groups["overdue"]), "c-red"), ("Due today", len(groups["today"]), "c-amber"),
                          ("Coming up", len(groups["upcoming"]), "c1"), ("Done today", len(done_today), "c-green")],
                         center_text=len(groups["overdue"]) + len(groups["today"]) + len(groups["upcoming"]), label="Work by status")
    weekly = charts.bars([(d.strftime("%d %b"), n) for d, n in stats.weekly_buckets(dates, t, 8)], label="Jobs done per week", css="c-green")
    return render(request, "today.html", dict(groups=shown, counts=counts, page=page, done_today=done_today, areas=Area.objects.all(), area=area,
                                              scope=scope, donut=donut, weekly=weekly, today=t,
                                              admin=is_admin(request.user)))


# ------------------------------------------------------------------ Calendar
@login_required
def calendar_view(request):
    t, scope = today(), get_scope(request)
    try:
        y, m = map(int, request.GET.get("m", t.strftime("%Y-%m")).split("-"))
        first = t.replace(year=y, month=m, day=1)
    except (ValueError, TypeError):
        first = t.replace(day=1)
    weeks = calmod.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    by_day = {}
    for o in load_occurrences(weeks[0][0], weeks[-1][-1], request.user, scope=scope, include_skipped=True):
        by_day.setdefault(o.when, []).append(o)
    grid = []
    for w in weeks:
        row = []
        for d in w:
            items = by_day.get(d, [])
            row.append(dict(date=d, in_month=d.month == first.month, is_today=d == t, items=items[:3], extra=max(0, len(items) - 3)))
        grid.append(row)
    return render(request, "calendar.html", dict(grid=grid, first=first, scope=scope,
                                                 prev=(first - timedelta(days=1)).strftime("%Y-%m"),
                                                 nxt=(first + timedelta(days=32)).strftime("%Y-%m")))


# ------------------------------------------------------------------ Jobs
@login_required
def jobs_list(request):
    scope, t = get_scope(request), today()
    q, kind = request.GET.get("q", "").strip()[:80], request.GET.get("kind", "")
    qs = Job.objects.select_related("equipment__area", "assignee", "assigned_group", "project", "assigned_by", "created_by")
    sq = scope_q(request.user, scope)
    if sq is not None:
        qs = qs.filter(sq)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(equipment__name__icontains=q) | Q(equipment__code__icontains=q) | Q(project__name__icontains=q))
    if kind == "once":
        qs = qs.filter(kind=Job.ONCE, project__isnull=True)
    elif kind == "repeat":
        qs = qs.filter(kind=Job.REPEAT)
    elif kind == "task":
        qs = qs.filter(project__isnull=False)
    qs = qs.order_by("-active", "title")
    page = paginate(request, qs, default=25)
    nxt = {}
    for o in load_occurrences(t - timedelta(days=engine.LOOKBACK_DAYS), t + timedelta(days=120), request.user,
                              jobs=Job.objects.filter(pk__in=[j.pk for j in page]), include_inactive=True):
        if o.status in ("overdue", "today", "upcoming") and o.job.pk not in nxt:
            nxt[o.job.pk] = o
    for j in page:
        j.next_occ = nxt.get(j.pk)
    return render(request, "jobs.html", dict(page=page, q=q, kind=kind, scope=scope, admin=is_admin(request.user)))


@login_required
def job_detail(request, pk):
    job = get_object_or_404(Job.objects.select_related("equipment__area", "assignee", "assigned_group", "project__owner"), pk=pk)
    t = today()
    sched = load_occurrences(t - timedelta(days=engine.LOOKBACK_DAYS), t + timedelta(days=180), request.user,
                             jobs=Job.objects.filter(pk=pk), include_skipped=True, include_inactive=True)
    ctx = dict(job=job, upcoming=[o for o in sched if o.status != "done"][:14],
               history=paginate(request, job.completions.select_related("done_by", "assigned_by", "job__assignee", "job__assigned_group",
                                                                         "job__assigned_by", "job__created_by"), default=10, key="hp", per_key="hper"),
               attachments=job.attachments.select_related("uploaded_by"),
               skip_ranges=job.skip_ranges.select_related("created_by"), moves=job.moves.select_related("created_by"),
               can_manage=can_manage_job(request.user, job), admin=is_admin(request.user), attach_form=AttachForm(),
               move_limit=engine.MOVE_LIMIT_DAYS)
    return render(request, "job_detail.html", ctx)


def _job_form_view(request, job=None, project=None):
    before = (job.assignee_id, job.assigned_group_id) if job else (None, None)      # read BEFORE the form edits the instance
    form = JobForm(request.POST or None, request.FILES or None, instance=job, project=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if job is None:
                obj.created_by = request.user
            if (obj.assignee_id, obj.assigned_group_id) != before:                   # (re)assigned, or un-assigned
                obj.assigned_by, obj.assigned_at = (request.user, timezone.now()) if obj.is_assigned else (None, None)
            obj.save()
            save_attachments(obj, form.cleaned_data.get("files") or [], request.user)
            if form.add_prefix("skip_dates") in form.data:                 # only when the field was submitted
                sync_skip_dates(obj, form.cleaned_data.get("skip_dates") or [], request.user)
        messages.success(request, "Job saved.")
        return redirect("project_detail" if obj.project_id and project else "job_detail", obj.project_id if project else obj.pk)
    return render(request, "job_form.html", dict(form=form, job=job, project=project or (job.project if job else None)))


@admin_required
def job_new(request):
    return _job_form_view(request)


@login_required
def job_edit(request, pk):
    job = get_object_or_404(Job.objects.select_related("project"), pk=pk)
    if not can_manage_job(request.user, job):
        raise PermissionDenied
    return _job_form_view(request, job=job)


@require_POST
@login_required
def job_toggle(request, pk):
    job = get_object_or_404(Job.objects.select_related("project"), pk=pk)
    if not can_manage_job(request.user, job):
        raise PermissionDenied
    job.active = not job.active
    job.save(update_fields=["active"])
    return redirect("job_detail", pk)


@require_POST
@login_required
def job_delete(request, pk):
    job = get_object_or_404(Job.objects.select_related("project"), pk=pk)
    if not can_manage_job(request.user, job):
        raise PermissionDenied
    proj = job.project_id
    for a in job.attachments.all():
        a.file.delete(save=False)
    for c in job.completions.exclude(evidence=""):
        c.evidence.delete(save=False)
    job.delete()
    messages.success(request, "Job and its history deleted.")
    return redirect("project_detail", proj) if proj else redirect("jobs")


# ------------------------------------------------------------------ Files
@require_POST
@login_required
def job_upload(request, pk):
    job = get_object_or_404(Job.objects.select_related("project"), pk=pk)
    if not can_manage_job(request.user, job):
        raise PermissionDenied
    form = AttachForm(request.POST, request.FILES)
    if form.is_valid():
        save_attachments(job, form.cleaned_data["files"], request.user)
        messages.success(request, f"{len(form.cleaned_data['files'])} file(s) attached.")
    else:
        form_errors(request, form)
    return redirect("job_detail", pk)


@login_required
def attachment_download(request, pk):
    a = get_object_or_404(JobAttachment, pk=pk)
    return serve_file(a.file, a.original_name)


@require_POST
@login_required
def attachment_delete(request, pk):
    a = get_object_or_404(JobAttachment.objects.select_related("job__project"), pk=pk)
    if not can_manage_job(request.user, a.job):
        raise PermissionDenied
    a.file.delete(save=False)
    a.delete()
    messages.success(request, "File removed.")
    return redirect("job_detail", a.job_id)


@login_required
def evidence_download(request, pk):
    c = get_object_or_404(Completion, pk=pk)
    if not c.evidence:
        return redirect("jobs")
    return serve_file(c.evidence, c.evidence_name or "evidence")


# ------------------------------------------------------------------ Exceptions
@require_POST
@admin_required
def skip_create(request):
    form = SkipForm(request.POST)
    back = _back(request, reverse("jobs"))
    if form.is_valid():
        d = form.cleaned_data
        r = SkipRange(job=d["job"], start_date=d["start_date"], end_date=d["end_date"], reason=d["reason"], created_by=request.user)
        r.full_clean()
        r.save()
        messages.success(request, f"Skipped {r.days} day{'s' if r.days != 1 else ''}: {r.start_date:%d %b} to {r.end_date:%d %b}.")
        back = reverse("job_detail", args=[d["job"].pk]) if request.POST.get("goto_job") else back
    else:
        form_errors(request, form)
    return redirect(back)


@require_POST
@admin_required
def skip_undo(request, pk):
    r = get_object_or_404(SkipRange, pk=pk)
    job_id = r.job_id
    r.delete()
    messages.success(request, "Back to the normal schedule for those days.")
    return redirect("job_detail", job_id) if job_id else redirect("setup")


@require_POST
@admin_required
def move_create(request):
    form = MoveForm(request.POST)
    back = _back(request, reverse("jobs"))
    if form.is_valid():
        d = form.cleaned_data
        MoveException.objects.update_or_create(job=d["job"], orig_date=d["orig_date"],
                                               defaults=dict(new_date=d["new_date"], reason=d["reason"], created_by=request.user))
        messages.success(request, f"{d['orig_date']:%a %d %b} moved to {d['new_date']:%a %d %b}.")
    else:
        form_errors(request, form)
    return redirect(back)


@require_POST
@admin_required
def move_undo(request, pk):
    m = get_object_or_404(MoveException, pk=pk)
    job_id = m.job_id
    m.delete()
    messages.success(request, "Back to the normal schedule for that date.")
    return redirect("job_detail", job_id)


# ------------------------------------------------------------------ Doing the work
@require_POST
@login_required
def done(request):
    form = DoneForm(request.POST, request.FILES, user=request.user)
    back = _back(request, reverse("today"))
    valid = form.is_valid()
    job = form.cleaned_data.get("job")
    if job is not None and not can_do_job(request.user, job):       # who you are is checked before anything else is revealed
        raise PermissionDenied
    if not valid:
        form_errors(request, form)
        return redirect(back)
    d = form.cleaned_data
    if not job.active:
        messages.error(request, "This job is paused.")
        return redirect(back)
    f = d.get("evidence")
    try:
        with transaction.atomic():
            c = Completion(job=job, due_date=d["due_date"], planned_date=d["planned"], done_by=request.user, note=d["note"].strip(),
                           evidence=f or "", evidence_name=filecheck.display_name(f.name) if f else "",
                           checkpoints=d["checks"], evidence_was_required=job.evidence_required)
            c.snapshot_assignment(job)
            c.save()
        messages.success(request, f"Done: {job.title}")
    except IntegrityError:
        messages.error(request, "Already marked done.")
    return redirect(back)


@require_POST
@admin_required
def completion_undo(request, pk):
    c = get_object_or_404(Completion, pk=pk)
    job_id = c.job_id
    if c.evidence:
        c.evidence.delete(save=False)
    c.delete()
    messages.success(request, "Marked as not done.")
    return redirect(_back(request, reverse("job_detail", args=[job_id])))
