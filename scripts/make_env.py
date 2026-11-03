#!/usr/bin/env python3
"""Create .env from .env.example with a fresh SECRET KEY and a random database password.

    python scripts/make_env.py            # refuses to overwrite an existing .env
    python scripts/make_env.py --force    # overwrite
    python scripts/make_env.py --sqlite   # use SQLite instead of PostgreSQL

Needs only the Python standard library, so it runs before 'pip install'.
"""
import re
import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(sample_text, sqlite=False, key=None, db_password=None):
    key = key or secrets.token_urlsafe(64)
    pw = db_password or secrets.token_urlsafe(24)
    text = re.sub(r"^DJANGO_SECRET_KEY=.*$", lambda m: f"DJANGO_SECRET_KEY={key}", sample_text, flags=re.M)
    text = re.sub(r"^POSTGRES_PASSWORD=.*$", lambda m: f"POSTGRES_PASSWORD={pw}", text, flags=re.M)
    if sqlite:
        text = re.sub(r"^DB_ENGINE=.*$", "DB_ENGINE=sqlite", text, flags=re.M)
    return text, key, pw


def main(argv):
    force, sqlite = "--force" in argv, "--sqlite" in argv
    target, sample = ROOT / ".env", ROOT / ".env.example"
    if not sample.exists():
        print("Cannot find .env.example next to manage.py"); return 1
    if target.exists() and not force:
        print(".env already exists - not touched. Use --force to overwrite."); return 1
    text, _, pw = build(sample.read_text(encoding="utf-8"), sqlite=sqlite)
    target.write_text(text, encoding="utf-8")
    try:
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)          # 600: only you can read it
    except OSError:
        pass
    print(f"Created {target}")
    print("  - new SECRET KEY written")
    if sqlite:
        print("  - database: SQLite (no setup needed)")
    else:
        print(f"  - database: PostgreSQL, password generated. Create the database with the same values, e.g.\n"
              f"      CREATE USER maintain WITH PASSWORD '{pw}';\n      CREATE DATABASE maintain OWNER maintain;\n"
              f"    (or run:  docker compose up -d db)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
