"""Small security helpers - pure Python."""


def csv_safe(value):
    """Stop spreadsheet formula injection: =, +, -, @ at the start of text become plain text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


def is_locked(user_failures, ip_failures, max_user=5, max_ip=20):
    """Brute-force policy: too many recent failures for this username OR from this IP."""
    return user_failures >= max_user or ip_failures >= max_ip


def retry_after_seconds(failure_times, now, window_seconds):
    """Seconds until the oldest failure in the window expires (0 if none). Times are datetimes."""
    recent = [t for t in failure_times if (now - t).total_seconds() < window_seconds]
    if not recent:
        return 0
    oldest = min(recent)
    return max(0, int(window_seconds - (now - oldest).total_seconds()) + 1)
