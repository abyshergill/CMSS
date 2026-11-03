from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import engine
from ..forms import AreaForm, EquipmentForm
from ..models import Completion, Equipment, Area
from ..permissions import admin_required, is_admin
from ..services import equipment_health_map, load_occurrences, today
from ..models import Job
from ._util import form_errors, paginate


@login_required
def equipment_list(request):
    health, overdue = equipment_health_map(request.user)
    q = request.GET.get("q", "").strip()[:80]
    qs = Equipment.objects.filter(active=True).select_related("area").annotate(njobs=Count("jobs", filter=Q(jobs__active=True)))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(area__name__icontains=q))
    page = paginate(request, qs, default=25)                     # grouped by area on screen, a page at a time
    rows = list(page)
    for e in rows:
        e.health, e.overdue_n = health.get(e.id, "ok"), overdue.get(e.id, 0)
    return render(request, "equipment.html", dict(rows=rows, page=page, q=q, form=EquipmentForm(), admin=is_admin(request.user)))


@require_POST
@admin_required
def equipment_new(request):
    form = EquipmentForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Equipment added.")
    else:
        form_errors(request, form)
    return redirect("equipment")


@login_required
def equipment_detail(request, pk):
    e = get_object_or_404(Equipment.objects.select_related("area"), pk=pk)
    t = today()
    occs = [o for o in load_occurrences(t - timedelta(days=engine.LOOKBACK_DAYS), t + timedelta(days=7), request.user,
                                        jobs=Job.objects.filter(equipment=e)) if o.status != "done"]
    return render(request, "equipment_detail.html", dict(
        e=e, occs=occs, admin=is_admin(request.user), form=EquipmentForm(instance=e),
        jobs=paginate(request, e.jobs.select_related("assignee", "assigned_group"), default=10, key="jp", per_key="jper"),
        history=paginate(request, Completion.objects.filter(job__equipment=e).select_related(
            "job", "done_by", "assigned_by", "job__assignee", "job__assigned_group", "job__assigned_by", "job__created_by"),
            default=10, key="hp", per_key="hper")))


@require_POST
@admin_required
def equipment_edit(request, pk):
    e = get_object_or_404(Equipment, pk=pk)
    form = EquipmentForm(request.POST, instance=e)
    if form.is_valid():
        form.save()
        messages.success(request, "Saved.")
    else:
        form_errors(request, form)
    return redirect("equipment_detail", pk)


@require_POST
@admin_required
def equipment_archive(request, pk):
    Equipment.objects.filter(pk=pk).update(active=False)
    messages.success(request, "Equipment archived. Its jobs leave the schedule; history is kept.")
    return redirect("equipment")
