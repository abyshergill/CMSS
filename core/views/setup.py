from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import AreaForm, GroupForm, NewUserForm, ResetPasswordForm
from ..models import Area, Group, LoginAttempt, Profile
from ..permissions import admin_required, assignable_roles, can_manage_account, is_full_admin, role_of
from ..security import safe_redirect_target
from ._util import form_errors, paginate

User = get_user_model()
PAGE = 25
MEMBER_PAGE = 30
SEARCH_RESULTS = 30
TABS = ("people", "groups", "areas", "security")
ROLES = (("all", "Everyone"), ("admin", "Admins"), ("manager", "Managers"), ("tech", "Engineers"), ("inactive", "Deactivated"))


def _tab_url(tab, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return reverse("setup") + f"?tab={tab}" + (f"&{q}" if q else "")


def _back(request, default):
    return safe_redirect_target(request, request.POST.get("back"), default)


def _is_admin_q(prefix=""):
    return Q(**{f"{prefix}is_superuser": True}) | Q(**{f"{prefix}profile__role": Profile.ADMIN})


def _is_manager_q():
    return Q(profile__role=Profile.MANAGER) & ~Q(is_superuser=True)


def _refuse_admin_account(request, target):
    """Managers may not touch Admin accounts (change role, reset password, deactivate). True = refused."""
    if can_manage_account(request.user, target):
        return False
    messages.error(request, "Only an Admin can change an Admin account.")
    return True


@admin_required
def setup_view(request):
    tab = request.GET.get("tab")
    tab = tab if tab in TABS else "people"
    q = request.GET.get("q", "").strip()[:80]
    ctx = dict(tab=tab, q=q, counts=dict(people=User.objects.count(), groups=Group.objects.count(), areas=Area.objects.count()))

    if tab == "people":
        role = request.GET.get("role", "all")
        role = role if role in dict(ROLES) else "all"
        qs = User.objects.select_related("profile").prefetch_related("work_groups")
        if q:
            qs = qs.filter(username__icontains=q)
        if role == "admin":
            qs = qs.filter(_is_admin_q())
        elif role == "manager":
            qs = qs.filter(_is_manager_q())
        elif role == "tech":
            qs = qs.exclude(_is_admin_q()).exclude(_is_manager_q())
        elif role == "inactive":
            qs = qs.filter(is_active=False)
        page = paginate(request, qs.order_by("-is_active", "username"), default=PAGE)
        ctx.update(page=page, role=role, roles=ROLES, new_user=NewUserForm(actor=request.user), role_choices=assignable_roles(request.user), total=page.paginator.count)

    elif tab == "groups":
        qs = Group.objects.annotate(nmembers=Count("members", distinct=True), njobs=Count("jobs", distinct=True))
        if q:
            qs = qs.filter(name__icontains=q)
        page = paginate(request, qs.order_by("name"), default=PAGE)
        preview = {}                                        # first few names per group, in ONE query
        through = Group.members.through
        for gid, name in through.objects.filter(group_id__in=[g.pk for g in page]).order_by("user__username").values_list("group_id", "user__username"):
            preview.setdefault(gid, [])
            if len(preview[gid]) < 4:
                preview[gid].append(name)
        for g in page:
            g.preview = preview.get(g.pk, [])
            g.more = max(0, g.nmembers - len(g.preview))
        ctx.update(page=page, group_form=GroupForm(), total=page.paginator.count)

    elif tab == "areas":
        ctx.update(areas=Area.objects.annotate(nequip=Count("equipment", distinct=True)), area_form=AreaForm())

    else:
        from datetime import timedelta
        recent = LoginAttempt.objects.filter(success=False, created_at__gte=timezone.now() - timedelta(hours=24))
        ctx.update(failed=LoginAttempt.objects.filter(success=False)[:50], failed_24h=recent.count(),
                   ips_24h=recent.exclude(ip__isnull=True).values("ip").distinct().count())
    return render(request, "setup.html", ctx)


# ------------------------------------------------------------------ areas
@require_POST
@admin_required
def area_add(request):
    f = AreaForm(request.POST)
    if f.is_valid():
        f.save(); messages.success(request, "Area added.")
    else:
        form_errors(request, f)
    return redirect(_tab_url("areas"))


@require_POST
@admin_required
def area_delete(request, pk):
    a = get_object_or_404(Area, pk=pk)
    if a.equipment.filter(active=True).exists():
        messages.error(request, "Move or archive that area's equipment first.")
    else:
        a.delete(); messages.success(request, "Area deleted.")
    return redirect(_tab_url("areas"))


# ------------------------------------------------------------------ people
@require_POST
@admin_required
def user_add(request):
    f = NewUserForm(request.POST, actor=request.user)          # a Manager's form does not accept role=admin
    if f.is_valid():
        u = User.objects.create_user(f.cleaned_data["username"], password=f.cleaned_data["password1"])
        Profile.objects.update_or_create(user=u, defaults={"role": f.cleaned_data["role"]})
        messages.success(request, f"{u.username} added.")
    else:
        form_errors(request, f)
    return redirect(_back(request, _tab_url("people")))


@require_POST
@admin_required
def user_toggle(request, pk):
    u = get_object_or_404(User, pk=pk)
    if u.pk == request.user.pk:
        messages.error(request, "You can't deactivate yourself.")
    elif _refuse_admin_account(request, u):
        pass
    else:
        u.is_active = not u.is_active
        u.save(update_fields=["is_active"])
        messages.success(request, f"{u.username} is now {'active' if u.is_active else 'deactivated'}.")
    return redirect(_back(request, _tab_url("people")))


@require_POST
@admin_required
def user_password(request, pk):
    u = get_object_or_404(User, pk=pk)
    if _refuse_admin_account(request, u):
        return redirect(_back(request, _tab_url("people")))
    f = ResetPasswordForm(request.POST, user=u)
    if f.is_valid():
        u.set_password(f.cleaned_data["password"]); u.save(); messages.success(request, f"Password changed for {u.username}.")
    else:
        form_errors(request, f)
    return redirect(_back(request, _tab_url("people")))


@require_POST
@admin_required
def user_role(request, pk):
    """Change somebody's role. Admin: any role. Manager: Manager <-> Engineer, never an Admin account, never to Admin."""
    u = get_object_or_404(User, pk=pk)
    role = request.POST.get("role", "")
    if u.pk == request.user.pk:
        messages.error(request, "You can't change your own role.")
    elif u.is_superuser:
        messages.error(request, f"{u.username} is the main administrator account - its role is fixed.")
    elif _refuse_admin_account(request, u):
        pass
    elif role not in dict(assignable_roles(request.user)):
        messages.error(request, "You can't give that role." if role in dict(Profile.ROLES) else "Choose a role.")
    else:
        Profile.objects.update_or_create(user=u, defaults={"role": role})
        messages.success(request, f"{u.username} is now {dict(Profile.ROLES)[role]}.")
    return redirect(_back(request, _tab_url("people")))


# ------------------------------------------------------------------ groups
@admin_required
def group_detail(request, pk):
    g = get_object_or_404(Group, pk=pk)
    mq, q = request.GET.get("mq", "").strip()[:80], request.GET.get("q", "").strip()[:80]
    members = g.members.order_by("username")
    if mq:
        members = members.filter(username__icontains=mq)
    page = paginate(request, members, default=MEMBER_PAGE)
    cand = User.objects.filter(is_active=True).exclude(work_groups=g).order_by("username")
    if q:
        cand = cand.filter(username__icontains=q)
    found = list(cand[:SEARCH_RESULTS + 1])
    return render(request, "group_detail.html", dict(
        g=g, page=page, mq=mq, q=q, candidates=found[:SEARCH_RESULTS], more_candidates=len(found) > SEARCH_RESULTS,
        total_members=g.members.count(), njobs=g.jobs.count(), form=GroupForm(instance=g)))


@require_POST
@admin_required
def group_add(request):
    f = GroupForm(request.POST)
    if f.is_valid():
        g = f.save()
        messages.success(request, f"Group '{g.name}' created. Now add its members.")
        return redirect("group_detail", g.pk)
    form_errors(request, f)
    return redirect(_tab_url("groups"))


@require_POST
@admin_required
def group_edit(request, pk):
    """Rename only. Members are changed with the add / remove buttons, so a rename can never wipe them."""
    g = get_object_or_404(Group, pk=pk)
    f = GroupForm(request.POST, instance=g)
    if f.is_valid():
        f.save(); messages.success(request, "Group renamed.")
    else:
        form_errors(request, f)
    return redirect("group_detail", pk)


@require_POST
@admin_required
def group_members_add(request, pk):
    g = get_object_or_404(Group, pk=pk)
    ids = [int(x) for x in request.POST.getlist("users")[:200] if x.isdigit()]
    users = list(User.objects.filter(pk__in=ids, is_active=True))
    if users:
        g.members.add(*users)
        messages.success(request, f"Added {len(users)} {'person' if len(users) == 1 else 'people'}.")
    else:
        messages.error(request, "Tick at least one person to add.")
    return redirect(_back(request, reverse("group_detail", args=[pk])))


@require_POST
@admin_required
def group_member_remove(request, pk, uid):
    g = get_object_or_404(Group, pk=pk)
    u = get_object_or_404(User, pk=uid)
    g.members.remove(u)
    messages.success(request, f"{u.username} removed from {g.name}.")
    return redirect(_back(request, reverse("group_detail", args=[pk])))


@require_POST
@admin_required
def group_delete(request, pk):
    g = get_object_or_404(Group, pk=pk)
    n = g.jobs.count()
    g.delete()                      # jobs stay, but become "anyone" (SET_NULL)
    messages.success(request, "Group deleted." + (f" {n} job(s) it had are now open to anyone." if n else ""))
    return redirect(_tab_url("groups"))
