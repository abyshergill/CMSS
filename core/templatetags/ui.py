import json
from datetime import date, timedelta

from django import template
from django.utils.http import urlencode

from .. import engine, recurrence as rec
from ..services import today

register = template.Library()


@register.filter
def describe(job):
    if job.kind == "repeat" and job.every and job.unit:
        return rec.describe(job.every, job.unit)
    return "One time"


@register.filter
def window_name(job):
    """'that day' / 'that week (Mon-Sun)' / 'that month' / 'that year' - when a repeating job may be marked done."""
    return engine.PERIOD_WORDS[engine.period_kind(job.unit, job.every)] if job.kind == "repeat" else ""


@register.filter
def jsonify(value):
    """JSON for a data-* attribute (Django escapes the quotes when it is written into the page)."""
    return json.dumps(value or [], ensure_ascii=False)


@register.filter
def rel(d):
    if not isinstance(d, date):
        return ""
    n = (d - today()).days
    return ("today" if n == 0 else "tomorrow" if n == 1 else "yesterday" if n == -1
            else f"in {n} days" if n > 0 else f"{-n} days ago")


@register.filter
def role_key(user):
    from ..permissions import role_of
    return role_of(user)


@register.filter
def role_label(user):
    from ..permissions import role_name
    return role_name(user)


@register.filter
def manageable_by(target, actor):
    """{% if u|manageable_by:user %} - may `actor` change this account? (Managers may not touch Admins.)"""
    from ..permissions import can_manage_account
    return can_manage_account(actor, target)


@register.filter
def initials(user):
    return (user.username[:2] if user else "?").upper()


@register.simple_tag
def page_url(request, key, number, per_key="per"):
    """Link to page `number` of the list that uses ?<key>=... and keeps every other filter."""
    q = request.GET.copy()
    q[key] = number
    return "?" + q.urlencode()


@register.simple_tag
def per_url(request, per, key="page", per_key="per"):
    """Link that changes the page size and goes back to page 1."""
    q = request.GET.copy()
    q[per_key] = per
    q.pop(key, None)
    return "?" + q.urlencode()


@register.simple_tag
def qs(request, **kw):
    """Current query string with some values replaced; None removes a key."""
    q = request.GET.copy()
    for k, v in kw.items():
        if v is None or v == "":
            q.pop(k, None)
        else:
            q[k] = v
    s = q.urlencode()
    return "?" + s if s else "?"


@register.inclusion_tag("partials/scope_toggle.html")
def scope_toggle(request, scope, label="My jobs only"):
    """Engineers get a Mine/All switch. Admins get a three-way switch:
    everything / assigned to me (or my groups) / assigned by me."""
    from ..permissions import is_admin
    keep = [(k, v) for k in request.GET for v in request.GET.getlist(k) if k not in ("scope", "page")]
    options = [("all", "Everything"), ("mine", "Assigned to me"), ("by_me", "Assigned by me")]
    return dict(scope=scope, keep=keep, label=label, is_admin=is_admin(request.user), options=options)
