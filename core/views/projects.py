from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import charts
from ..forms import ProjectForm, JobForm
from ..models import Project
from ..permissions import admin_required, can_manage_project, in_group, is_admin, scope_q
from ..services import get_scope, project_stats_map, project_tasks, today
from ..views.work import _job_form_view
from ._util import paginate


@login_required
def project_list(request):
    scope = get_scope(request)
    qs = Project.objects.select_related("owner")
    if scope == "mine":
        qs = qs.filter(Q(owner=request.user) | scope_q(request.user, "mine", "tasks__")).distinct()
    elif scope == "by_me":
        qs = qs.filter(Q(owner=request.user) | Q(tasks__created_by=request.user)).distinct()
    q = request.GET.get("q", "").strip()[:80]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(owner__username__icontains=q))
    page = paginate(request, qs, default=12)
    projects = list(page)
    st = project_stats_map(projects)
    cards = [dict(p=p, s=st[p.id], ring=charts.ring(st[p.id]["pct"], size=72, label=p.name)) for p in projects]
    return render(request, "projects.html", dict(cards=cards, page=page, q=q, scope=scope, admin=is_admin(request.user)))


@login_required
def project_detail(request, pk):
    p = get_object_or_404(Project.objects.select_related("owner"), pk=pk)
    scope, t = get_scope(request), today()
    all_rows = project_tasks(p, t)
    s = project_stats_map([p], t)[p.id]
    mine = lambda j: j.assignee_id == request.user.id or in_group(request.user, j)
    if scope == "mine":
        rows = [r for r in all_rows if mine(r["job"])]
    elif scope == "by_me":
        rows = [r for r in all_rows if r["job"].created_by_id == request.user.id]
    else:
        rows = all_rows
    counts = Counter(r["status"] for r in all_rows)
    donut = charts.donut([("Done", counts["done"], "c-green"), ("Overdue", counts["overdue"], "c-red"),
                          ("Due today", counts["today"], "c-amber"), ("To do", counts["upcoming"], "c1")],
                         center_text=f'{s["pct"]}%', label="Project status")
    pts = Counter()
    for r in all_rows:
        if r["status"] == "done":
            pts[r["job"].assigned_name or "Unassigned"] += r["job"].weight
    people = charts.hbars(pts.most_common(8), label="Points completed per person") if pts else ""
    page = paginate(request, rows, default=25)
    return render(request, "project_detail.html", dict(p=p, rows=list(page), page=page, s=s, donut=donut, people=people, scope=scope,
                                                       can_manage=can_manage_project(request.user, p), admin=is_admin(request.user)))


@admin_required
def project_new(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        messages.success(request, "Project created. Now add the tasks.")
        return redirect("project_detail", p.pk)
    return render(request, "project_form.html", dict(form=form, project=None))


@login_required
def project_edit(request, pk):
    p = get_object_or_404(Project, pk=pk)
    if not can_manage_project(request.user, p):
        raise PermissionDenied
    form = ProjectForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Project saved.")
        return redirect("project_detail", p.pk)
    return render(request, "project_form.html", dict(form=form, project=p))


@require_POST
@login_required
def project_toggle(request, pk):
    p = get_object_or_404(Project, pk=pk)
    if not can_manage_project(request.user, p):
        raise PermissionDenied
    p.status = Project.ACTIVE if p.status == Project.CLOSED else Project.CLOSED
    p.save(update_fields=["status"])
    return redirect("project_detail", pk)


@require_POST
@admin_required
def project_delete(request, pk):
    p = get_object_or_404(Project, pk=pk)
    for j in p.tasks.all():
        for a in j.attachments.all():
            a.file.delete(save=False)
        for c in j.completions.exclude(evidence=""):
            c.evidence.delete(save=False)
    p.delete()
    messages.success(request, "Project and its tasks deleted.")
    return redirect("projects")


@login_required
def task_new(request, pk):
    p = get_object_or_404(Project, pk=pk)
    if not can_manage_project(request.user, p):
        raise PermissionDenied
    return _job_form_view(request, project=p)
