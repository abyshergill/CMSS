import csv
import re
from datetime import timedelta
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.db.models import F, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from .. import charts, stats
from ..forms import ExportForm
from ..models import Area, Completion, Project
from ..permissions import is_admin
from ..safe import csv_safe
from ..services import day_bounds, get_scope, project_stats_map, scoped_completions, today
from ._util import form_errors, paginate

User = get_user_model()
HEADERS = ["Date done", "Time done", "Job", "Type", "Project", "Equipment code", "Equipment", "Area", "Planned for",
           "On time", "Done by", "Assigned to", "Note", "Has file", "Assigned by", "Assigned on", "Checkpoints", "File required"]
ROW_LIMIT = 50000


def _rows(qs):
    for c in qs.iterator(chunk_size=500):
        j, loc = c.job, None
        loc = timezone.localtime(c.done_at)
        yield [loc.strftime("%Y-%m-%d"), loc.strftime("%H:%M"), j.title,
               "Project task" if j.project_id else ("Repeating" if j.kind == "repeat" else "One-off"),
               j.project.name if j.project else "", j.equipment.code if j.equipment else "",
               j.equipment.name if j.equipment else "", j.equipment.area.name if j.equipment and j.equipment.area else "",
               c.planned_date.isoformat(), "Yes" if c.on_time else "Late",
               c.done_by.username if c.done_by else "", j.assigned_name,
               c.note, "Yes" if c.evidence else "",
               c.shown_assigned_by.username if c.shown_assigned_by else "",
               timezone.localtime(c.shown_assigned_at).strftime("%Y-%m-%d %H:%M") if c.shown_assigned_at else "",
               " | ".join(("\u2713 " if k.get("ok") else "\u2717 ") + str(k.get("text", "")) for k in c.checkpoints),
               "Yes" if c.evidence_was_required else ""]


def completions_between(user, d1, d2, scope="all", project=None, person=None, job_name=None):
    lo, hi = day_bounds(d1, d2)
    qs = Completion.objects.filter(done_at__gte=lo, done_at__lt=hi).select_related(
        "job__equipment__area", "job__project", "job__assignee", "job__assigned_group", "job__assigned_by", "job__created_by",
        "done_by", "assigned_by").order_by("done_at")
    qs = scoped_completions(qs, user, scope)
    if project:
        qs = qs.filter(job__project=project)
    if person:
        qs = qs.filter(done_by=person)
    if job_name:                                        # the ORM escapes it - "50%" or "_" are plain text here
        qs = qs.filter(job__title__icontains=job_name)
    return qs


@login_required
def history_view(request):
    scope, t = get_scope(request), today()
    q = request.GET.get("q", "").strip()[:80]
    late = request.GET.get("late") == "1"
    qs = scoped_completions(Completion.objects.select_related(
        "job__equipment", "job__project", "job__assignee", "job__assigned_group", "job__assigned_by", "job__created_by",
        "done_by", "assigned_by"), request.user, scope)
    if q:
        qs = qs.filter(Q(job__title__icontains=q) | Q(job__equipment__name__icontains=q) | Q(job__equipment__code__icontains=q)
                       | Q(note__icontains=q) | Q(done_by__username__icontains=q))
    if late:                                                      # done after the planned date
        qs = qs.annotate(done_day=TruncDate("done_at")).filter(done_day__gt=F("planned_date"))
    page = paginate(request, qs, default=25)
    form = ExportForm(initial=dict(date_from=t - timedelta(days=30), date_to=t, scope=scope))
    if not is_admin(request.user):
        del form.fields["person"]
        form.fields["scope"].choices = form.SCOPES[:2]          # "assigned by me" is for admins
    return render(request, "history.html", dict(page=page, q=q, late=late, scope=scope, form=form))


class _Echo:
    def write(self, v):
        return v


@login_required
def export_view(request):
    form = ExportForm(request.GET)
    if not form.is_valid():
        form_errors(request, form)
        from django.shortcuts import redirect
        return redirect("history")
    d = form.cleaned_data
    person = d["person"] if is_admin(request.user) else None
    job_name = (d.get("job") or "").strip()
    qs = completions_between(request.user, d["date_from"], d["date_to"], d["scope"], d["project"], person, job_name)
    name = f"jobs_done_{d['date_from']:%Y-%m-%d}_to_{d['date_to']:%Y-%m-%d}"
    if job_name:                                         # file name only ever gets letters, digits and dashes
        name += "_" + (re.sub(r"[^A-Za-z0-9]+", "-", job_name).strip("-")[:30] or "job")
    if d["format"] == "csv":
        w = csv.writer(_Echo())

        def gen():
            yield "\ufeff"                                   # lets Excel open UTF-8 correctly
            yield w.writerow(HEADERS)
            for n, r in enumerate(_rows(qs)):
                if n >= ROW_LIMIT:
                    break
                yield w.writerow([csv_safe(x) for x in r])
        resp = StreamingHttpResponse(gen(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
        return resp
    from openpyxl import Workbook
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Jobs done")
    ws.append(HEADERS)
    for n, r in enumerate(_rows(qs)):
        if n >= ROW_LIMIT:
            break
        ws.append([csv_safe(x) for x in r])
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return resp


@login_required
def insights_view(request):
    scope, t = get_scope(request), today()
    since = t - timedelta(weeks=12)
    lo, _ = day_bounds(since, t)
    comps = list(scoped_completions(Completion.objects.filter(done_at__gte=lo), request.user, scope)
                 .select_related("job__equipment__area", "job__project", "done_by"))
    weekly = charts.bars([(d.strftime("%d %b"), n) for d, n in stats.weekly_buckets([c.done_date for c in comps], t, 12)],
                         width=560, label="Jobs done per week", css="c-green")
    on, late = stats.on_time_split([(c.done_date, c.planned_date) for c in comps])
    ontime = charts.donut([("On time", on, "c-green"), ("Late", late, "c-red")], center_text=f"{stats.percent(on, on + late)}%", label="On-time rate")
    by_area = charts.hbars(stats.top_counts([c.job.equipment.area.name if c.job.equipment and c.job.equipment.area else "" for c in comps], blank="No area"), label="Done by area") if comps else ""
    by_person = charts.hbars(stats.top_counts([c.done_by.username if c.done_by else "" for c in comps], blank="Unknown"), label="Done by person") if comps and is_admin(request.user) else ""
    kinds = stats.top_counts(["Project task" if c.job.project_id else "Repeating" if c.job.kind == "repeat" else "One-off" for c in comps])
    mix = charts.donut([(k, v, ["c1", "c2", "c3"][i % 3]) for i, (k, v) in enumerate(kinds)], center_text=len(comps), label="Types of work done")
    plist = list(Project.objects.filter(status="active").select_related("owner"))
    st = project_stats_map(plist, t)
    prog = charts.hbars([(p.name, st[p.id]["pct"]) for p in plist][:8], label="Project progress (%)") if plist else ""
    return render(request, "insights.html", dict(scope=scope, weekly=weekly, ontime=ontime, by_area=by_area, by_person=by_person,
                                                 mix=mix, prog=prog, total=len(comps), on=on, late=late))
