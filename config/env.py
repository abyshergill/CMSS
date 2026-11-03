"""Reads the .env file and builds the database settings. Pure Python (no Django import) so it can be unit-tested.

Real environment variables always win over the .env file, so a server / container can override anything.
"""
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

PLACEHOLDERS = {"", "change-me", "changeme", "replace-me", "your-secret-key", "django-insecure"}
MIN_SECRET_LENGTH = 50


class ConfigError(ValueError):
    """Raised for settings that are missing or unsafe. The message says what to fix."""


def parse_env_text(text):
    """KEY=value lines -> dict. Supports comments, blank lines, 'export KEY=..', and 'single' / "double" quotes."""
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f".env line {n}: expected KEY=value")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ConfigError(f".env line {n}: '{key}' is not a valid variable name")
        if value[:1] in ("'", '"'):
            q = value[0]
            end = value.find(q, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            if " #" in value:                      # trailing comment
                value = value.split(" #", 1)[0]
            value = value.strip()
        out[key] = value
    return out


def load_dotenv(path, environ=None):
    """Load `path` into `environ` (default os.environ) without overriding variables that are already set.
    Returns the dict that was read (empty if there is no file)."""
    environ = os.environ if environ is None else environ
    p = Path(path)
    if not p.is_file():
        return {}
    values = parse_env_text(p.read_text(encoding="utf-8"))
    for k, v in values.items():
        environ.setdefault(k, v)
    return values


def secret_key_from_env(environ):
    """Returns the key from DJANGO_SECRET_KEY, or None when it is not set.
    A placeholder or a short key is refused: it would make sessions and password-reset tokens guessable."""
    key = (environ.get("DJANGO_SECRET_KEY") or "").strip()
    if not key:
        return None
    if key.lower() in PLACEHOLDERS or key.lower().startswith(("change-me", "django-insecure")):
        raise ConfigError("DJANGO_SECRET_KEY still has the sample value. Run  python scripts/make_env.py  to create a real one.")
    if len(key) < MIN_SECRET_LENGTH:
        raise ConfigError(f"DJANGO_SECRET_KEY is too short ({len(key)} characters, need at least {MIN_SECRET_LENGTH}).")
    return key


def _int(environ, name, default):
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not '{raw}'.")


def _from_url(url):
    u = urlsplit(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ConfigError("DATABASE_URL must start with postgres:// or postgresql://")
    name = unquote(u.path.lstrip("/"))
    if not u.hostname or not name:
        raise ConfigError("DATABASE_URL needs a host and a database name, e.g. postgres://user:pass@localhost:5432/maintain")
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    return dict(NAME=name, USER=unquote(u.username or ""), PASSWORD=unquote(u.password or ""), HOST=u.hostname,
                PORT=str(u.port or 5432), SSLMODE=q.get("sslmode", ""))


def database_settings(environ, base_dir, data_dir):
    """-> Django DATABASES dict.  DB_ENGINE=sqlite (default) or postgres.
    For postgres give either DATABASE_URL or POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST / POSTGRES_PORT."""
    engine = (environ.get("DB_ENGINE") or "").strip().lower()
    if not engine:
        engine = "postgres" if (environ.get("DATABASE_URL") or environ.get("POSTGRES_DB")) else "sqlite"
    if engine in ("sqlite", "sqlite3"):
        return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(Path(data_dir) / "db.sqlite3"), "OPTIONS": {"timeout": 20}}}
    if engine not in ("postgres", "postgresql", "pg"):
        raise ConfigError(f"DB_ENGINE must be 'sqlite' or 'postgres', not '{engine}'.")
    if environ.get("DATABASE_URL"):
        c = _from_url(environ["DATABASE_URL"])
    else:
        c = dict(NAME=(environ.get("POSTGRES_DB") or "").strip(), USER=(environ.get("POSTGRES_USER") or "").strip(),
                 PASSWORD=environ.get("POSTGRES_PASSWORD") or "", HOST=(environ.get("POSTGRES_HOST") or "localhost").strip(),
                 PORT=str(_int(environ, "POSTGRES_PORT", 5432)), SSLMODE="")
        missing = [k for k, v in (("POSTGRES_DB", c["NAME"]), ("POSTGRES_USER", c["USER"])) if not v]
        if missing:
            raise ConfigError("PostgreSQL is selected but " + " and ".join(missing) + " not set in .env")
    if not c["PASSWORD"] and c["HOST"] not in ("localhost", "127.0.0.1", "::1", ""):
        raise ConfigError("A password is required for a PostgreSQL server that is not on this machine (POSTGRES_PASSWORD).")
    if c["PASSWORD"].lower() in PLACEHOLDERS and c["HOST"] not in ("localhost", "127.0.0.1", "::1", ""):
        raise ConfigError("POSTGRES_PASSWORD still has the sample value. Set a real password.")
    sslmode = (environ.get("DB_SSLMODE") or c["SSLMODE"] or "prefer").strip()
    if sslmode not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        raise ConfigError(f"DB_SSLMODE '{sslmode}' is not valid.")
    return {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": c["NAME"], "USER": c["USER"], "PASSWORD": c["PASSWORD"],
        "HOST": c["HOST"], "PORT": c["PORT"],
        "CONN_MAX_AGE": _int(environ, "DB_CONN_MAX_AGE", 60), "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": sslmode, "connect_timeout": 10},
    }}
