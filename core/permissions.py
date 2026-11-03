from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from functools import wraps

from django.db.models import Q

from .models import Group, Profile


def role_of(user):
    """'admin' | 'manager' | 'tech' (Engineer); '' when signed out. Superusers are always admins."""
    if not getattr(user, "is_authenticated", False):
        return ""
    if user.is_superuser:
        return Profile.ADMIN
    if not hasattr(user, "_role"):
        try:
            role = user.profile.role                       # uses select_related("profile") when the caller did that
        except ObjectDoesNotExist:
            role = Profile.objects.get_or_create(user=user)[0].role
        user._role = role if role in dict(Profile.ROLES) else Profile.TECH
    return user._role


def role_name(user):
    return dict(Profile.ROLES).get(role_of(user), "")


def is_full_admin(user):
    """Only an Admin: the one role that may create, change or reset other Admins."""
    return role_of(user) == Profile.ADMIN


def is_admin(user):
    """May manage everything (jobs, projects, equipment, people, groups): Admin and Manager."""
    return role_of(user) in (Profile.ADMIN, Profile.MANAGER)


def can_manage_account(actor, target):
    """Admins manage anybody. Managers manage Managers and Engineers - never an Admin (or they could take it over)."""
    return is_full_admin(actor) or (is_admin(actor) and role_of(target) != Profile.ADMIN)


def assignable_roles(actor):
    """[(value, label)] of the roles `actor` may give to somebody."""
    return [(v, n) for v, n in Profile.ROLES if v != Profile.ADMIN or is_full_admin(actor)]


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *a, **kw):
        if not is_admin(request.user):
            raise PermissionDenied
        return view(request, *a, **kw)
    return wrapper


def can_manage_project(user, project):
    return is_admin(user) or (project is not None and project.owner_id == user.id)


def can_manage_job(user, job):
    """Edit / attach files / delete. Admins, plus the owner of the job's project."""
    return is_admin(user) or (job.project_id is not None and job.project.owner_id == user.id)


def in_group(user, job):
    return bool(job.assigned_group_id) and job.assigned_group.members.filter(pk=user.pk).exists()


def can_do_job(user, job):
    """Mark done. Admins, the assigned person, any member of the assigned group, the project owner,
    or anyone if the job is not assigned to anybody."""
    if can_manage_job(user, job):
        return True
    if job.assignee_id is None and job.assigned_group_id is None:
        return True
    return job.assignee_id == user.id or in_group(user, job)


SCOPES = ("all", "mine", "by_me")


def scope_q(user, scope, prefix=""):
    """Q object for a scope, or None for 'all'.
    mine  = assigned to me, or to a group I belong to
    by_me = created/assigned by me (admins only; anyone else falls back to 'mine')"""
    if scope == "by_me" and is_admin(user):
        return Q(**{f"{prefix}created_by": user})
    if scope in ("mine", "by_me"):
        return Q(**{f"{prefix}assignee": user}) | Q(**{f"{prefix}assigned_group__in": Group.objects.filter(members=user)})
    return None
