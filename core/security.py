"""Brute-force protection + HTTP security headers."""
from datetime import timedelta

from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from . import safe
from .models import LoginAttempt

WINDOW_MINUTES = 15
MAX_USER_FAILURES = 5
MAX_IP_FAILURES = 20

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def lock_state(username, ip, now=None):
    """-> (locked, seconds_to_wait). Keyed on the typed username (real or not) and on the IP,
    so it also does not reveal which usernames exist."""
    now = now or timezone.now()
    since = now - timedelta(minutes=WINDOW_MINUTES)
    uq = LoginAttempt.objects.filter(success=False, created_at__gte=since, username=username.lower()[:150])
    iq = LoginAttempt.objects.filter(success=False, created_at__gte=since, ip=ip) if ip else LoginAttempt.objects.none()
    uf, ifl = uq.count(), iq.count()
    if not safe.is_locked(uf, ifl, MAX_USER_FAILURES, MAX_IP_FAILURES):
        return False, 0
    times = list(uq.values_list("created_at", flat=True)) if uf >= MAX_USER_FAILURES else list(iq.values_list("created_at", flat=True))
    return True, safe.retry_after_seconds(times, now, WINDOW_MINUTES * 60)


def record_attempt(username, ip, success):
    LoginAttempt.objects.create(username=username.lower()[:150], ip=ip, success=success)
    if success:      # a good sign-in resets that user's counter
        LoginAttempt.objects.filter(username=username.lower()[:150], success=False).delete()
    LoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=30)).delete()


def safe_redirect_target(request, url, default):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return default


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store")          # nothing private stays in the browser cache
        return resp
