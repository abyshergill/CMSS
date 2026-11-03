# CMMS - Computerized Maintenance Management System Version 2.1

Computerized Maintenance Management System (CMMS) designed to track equipment status, manage maintenance logs, and monitor component lifecycles with expiry dates and image uploads. Plan repeating and one-off maintenance jobs, assign them to
engineers, run projects made of small weighted tasks, attach reference files, and download what was done.

## 1. Install and run

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python scripts/make_env.py             # creates .env with a fresh SECRET KEY (add --sqlite to skip PostgreSQL)
# ... set up PostgreSQL (section 2), or run with --sqlite ...

python manage.py makemigrations core   # first time, AND again after upgrading to a new version (adds the new columns; data is kept)
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000 . On the very first visit you are asked to create the **admin** account
(this page disappears as soon as one user exists). Then add people and groups under **Setup**.

Optional example data (empty database only, weak demo passwords - never for real use): `python manage.py demo`

## 2. Settings: `.env` and PostgreSQL

All settings come from a `.env` file next to `manage.py` (real environment variables override it).
`.env.example` documents every option; `.env` itself is git-ignored.

| Variable | Meaning |
|---|---|
| `DJANGO_SECRET_KEY` | 50+ random characters. `python scripts/make_env.py` writes one. The sample value `change-me` and short keys are **refused at start-up**. If it is not set at all, a key is generated into `data/secret.key`. |
| `DJANGO_DEBUG` | `0` (default). Never `1` on a server. |
| `DJANGO_ALLOWED_HOSTS` | Host names that may open the site. |
| `DB_ENGINE` | `postgres` or `sqlite` (default when no PostgreSQL value is set). |
| `POSTGRES_DB/USER/PASSWORD/HOST/PORT` | PostgreSQL connection. Or one line: `DATABASE_URL=postgres://user:pass@host:5432/db?sslmode=require` (percent-encode special characters in the password). |
| `DB_SSLMODE` | `prefer` by default; use `require` or `verify-full` for a remote server. |
| `MAINTAIN_DATA` | Folder for uploads (and the SQLite file). Back it up. |
| `MAINTAIN_TZ` | Time zone (default `Asia/Bangkok`). |
| `MAINTAIN_HTTPS`, `MAINTAIN_BEHIND_PROXY` | See "Before putting it on a real network". |

**PostgreSQL in 3 steps**

1. `python scripts/make_env.py` - prints the SQL for the generated password. Either run it in `psql`:
   `CREATE USER maintain WITH PASSWORD '...'; CREATE DATABASE maintain OWNER maintain;`
   or start a local one: `docker compose up -d db` (listens on 127.0.0.1 only).
2. `python manage.py makemigrations core && python manage.py migrate`
3. `python manage.py runserver`

For `python manage.py test core` on PostgreSQL the user also needs `ALTER USER maintain CREATEDB;`
(Django creates and drops a `test_maintain` database).
Moving existing SQLite data to PostgreSQL: `python manage.py dumpdata --exclude contenttypes --exclude auth.permission > data.json`
(with `DB_ENGINE=sqlite`), switch `.env`, `migrate`, then `loaddata data.json`.

## 2. What is where

| Request | Where |
|---|---|
| **Days a repeating job must NOT run** | On the job form, *Repeats* section → **Days this job should NOT happen**: pick a day, press *+ Add this day*, repeat for each day (they need not be consecutive). Each day shows as a chip you can remove. Edit the job any time to change the list. For a block of days in a row, use the job page → *Skip one or more days* (From / To). A single date can also be *moved*. There are **no plant-wide closures**: nothing closes repeating jobs automatically, because some jobs exist precisely for shutdown days. |
| **When a repeating job can be marked done** | Only inside the period its date belongs to — **never in advance**. *Daily*: that day only. *Weekly / every N weeks*: any day of that calendar week (Mon–Sun). *Monthly / quarterly*: any day of that calendar month. *Yearly* (or "every 12 months"): any day of that year. Before the period starts the list shows **Opens Mon 03 Nov** instead of a button. After it ends the job shows **Missed**; only an admin can still record it (**Record late**, a reason is required, and it is marked *late*). One-off jobs and project tasks are not affected. A date moved by an admin uses its new date. The rule is enforced on the server, not just by hiding the button. |
| **Yearly repeat** | The job form now has *year(s)* as an interval and a **Yearly** preset. |
| **Checkpoints** | Job form → **Checkpoints**, one per line. Whoever marks the job done must tick every one (the Mark-done window lists them). A copy of what was checked is stored with the completion, so History stays true if the list changes later. Max 30 per job. |
| **File compulsory or optional** | Job form → tick **A photo or file is required to mark this job done**. Left unticked the upload stays optional. Applies to everyone, admins included. |
| **Who assigned the job, and when** | Recorded whenever a person/group is chosen or changed (not when other fields are edited). Shown on the job page, the Jobs list and in History, and copied into each completion, so History still shows the original assignee after a re-assignment. Jobs created before this version fall back to their creator and creation date. |
| **History** | **History** shows for every completed job: when it was done and for which date, the job, **assigned to / by / on**, the **checkpoints** (click to expand), notes and file, and who did it. Filter *Done late only*, search by job, equipment, person or note. The download has extra columns: *Assigned by, Assigned on, Checkpoints, File required*. |
| **Pagination** | Every long list is paged: Today, Jobs, History, Equipment (with search), Projects (with search), project tasks, the job page's History, the equipment page's Jobs and History, and the Setup lists. Choose **10 / 25 / 50 / 100** per page at the bottom of the list; search and filters are kept. |
| **Hide / show the menu** | **« Hide menu** at the bottom of the left menu; a **☰ Menu** button appears top-left to bring it back. The choice is remembered (a small cookie) and applied by the server, so the page does not flicker. |
| **Groups** | **Setup → Groups**: type a name and press *Create group*; on the group's page search for people and add them (any number, with *select all shown*).  When creating a job (or project task) choose *a person* **or** *a group*. A group job appears in the calendar, Today list and Jobs list of **every member**; any member can mark it done, and that completes it for the whole group. Removing someone from a group removes the job from their calendar. |
| **Mine / All** switch | Toggle at the top of Today, Calendar, Jobs, Projects, History, Insights. Engineers: *My jobs only* switch (includes their groups' jobs). **Admins and Managers get a three-way switch: Everything / Assigned to me / Assigned by me.** Remembered per session. |
| **Download jobs done in a period** | **History → Download** (From / To, Excel or CSV, mine/all, optional project/person). |
| **Create project** | **Projects → + Create project**, then *Add task*: assignee, due date and *effort points* (1–10). Progress % = points done ÷ total points. |
| **One time / Repeats** | Radio choice on the job form: *One time* shows a due date; *Repeats* shows presets (daily, weekly…), interval, first date and optional end date. |
| **Attach files to a job** | When creating/editing a job, or later on the job page. PDF, images, Word, CSV, Excel; 10 MB each, 10 per upload. |
| **Charts** | Today, Project pages, Projects list, Insights (SVG, no JavaScript libraries). |

**Setup page** (admins): tabs for **People**, **Groups**, **Areas** and **Security** so only one section is on screen. People: search, role filter, 25 per page, one *Manage* button per row (password reset / deactivate in a shared dialog) and *+ Add person*. Groups: list with a member preview, each group has its own page with a searchable, paged member list and an *Add people* search - the page stays small with hundreds of users.

Roles (shown under the user name in the left menu, and in Setup > People):

| Role | Can do |
|---|---|
| **Admin** | Everything, including creating other Admins, changing anybody's role and resetting an Admin's password. |
| **Manager** | Everything an Admin can (jobs, projects, equipment, people, groups, areas, exceptions, late entries) **except** creating Admins, giving the Admin role, or changing / deactivating / resetting the password of an Admin account. |
| **Engineer** | Sees the schedule, marks the jobs assigned to them (or unassigned ones) done, and downloads their own history. (This role used to be called *Technician*.) A project owner can manage their own project's tasks. |

Change a role in Setup > People > *Manage* > *Role*. You cannot change your own role, and the account created on first run always stays Admin.

**Download** (History > *Download jobs done in a period*): dates, format, who it was assigned to, **job name** (part of the name, any case), project and person can be combined in one download.

## 3. Security measures

| Threat | What is done |
|---|---|
| SQL injection | Only the Django ORM; no raw SQL. Search text and IDs are bound parameters or validated by forms. Tested with injection payloads. |
| Brute force | 5 failed sign-ins per username (real or not) or 20 per IP in 15 min → HTTP 429 even with the right password. Generic error message. All attempts are logged (visible to admins in Setup). Password hashing is PBKDF2 with a 10-character minimum, common/numeric/similar-to-username passwords refused. |
| CSRF | Django CSRF middleware; every POST form has a token; state-changing URLs accept POST only; logout is POST. |
| XSS | Template auto-escaping everywhere, no `\|safe`; charts escape every label; strict CSP (`script-src 'self'`, no inline scripts/styles). |
| Clickjacking / sniffing | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`. |
| Broken access control / IDOR | Admin-only actions return 403 for Engineers; a Manager cannot create an Admin or touch an Admin account (checked on the server, not just hidden); completing, editing and attaching are checked per object; tested for every admin endpoint. |
| Malicious uploads | Extension allow-list **and** content check (magic bytes, ZIP structure for docx/xlsx, macro files refused, zip-bomb limits). Stored under random names outside the web root, served only to signed-in users as downloads with `nosniff` and a sandbox CSP. |
| CSV/Excel formula injection | Exported cells starting with `= + - @` are neutralised. |
| Session fixation / open redirect | Session key rotates at login; `next=` only accepts same-site URLs. |
| Host header attacks | `ALLOWED_HOSTS` restricted (default localhost). |
| Secrets | Secret key and database password live in `.env` (git-ignored, written with mode 600), sample/short keys are refused. Falls back to a generated `data/secret.key`. `DEBUG` is off unless `DJANGO_DEBUG=1`. Django admin site is **not** enabled. |

### Before putting it on a real network

```bash
# in .env:
DJANGO_ALLOWED_HOSTS=maintain.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://maintain.example.com
MAINTAIN_HTTPS=1               # secure cookies, HTTPS redirect, HSTS
MAINTAIN_BEHIND_PROXY=1        # only if a reverse proxy sets X-Forwarded-Proto
DB_SSLMODE=require             # if PostgreSQL is on another machine
# then:
pip install gunicorn && gunicorn config.wsgi
```

Serve it over HTTPS (nginx/Caddy), and make sure the proxy passes the real client address, otherwise the
per-IP brute-force limit sees only the proxy. Also: keep `data/` unreadable by other users, and back it up.

## 4. Tests

```bash
python manage.py test core -v 2
```

| File | Needs Django | Covers |
|---|---|---|
| `core/tests/test_engine.py` | no | repeat rules, multi-day skips, moves, statuses |
| `core/tests/test_datelist.py` | no | parsing the list of skip days (separators, bad dates, limits) |
| `core/tests/test_windows.py` | no | day/week/month/year finishing windows and their edges, yearly dates (29 Feb), checkpoint parsing and ticking |
| `core/tests/test_filecheck.py` | no | upload allow-list, content checks, zip bombs, name sanitising |
| `core/tests/test_safe_charts_stats.py` | no | lockout policy, CSV injection, SVG escaping, statistics |
| `core/tests/test_env.py` | no | `.env` parsing, secret-key rules, PostgreSQL/SQLite selection, `make_env.py`, docker file, **and runs the JavaScript tests with Node** (One time/Repeats switch, skip-days picker, hide-menu button, Mark-done window) |
| `core/tests/test_static_security.py` | no | audits source + templates: no raw SQL, CSRF tokens, multipart forms, no `\|safe`, CSP, settings |
| `core/tests/test_setup.py` | **yes** | Setup tabs, search, paging with 60-80 users, shared Manage dialog, group pages, skip days on the job form, no closures |
| `core/tests/test_work_rules.py` | **yes** | no finishing in advance (day/week/month/year, moved dates, late entries), compulsory file, checkpoints, who/when assigned, history columns, pagination on every list, hide-menu cookie |
| `core/tests/test_groups.py` | **yes** | groups, group jobs in every member's calendar, completing as a group, admin *assigned by me*, the One time/Repeats form as rendered |
| `core/tests/test_web.py` | **yes** | login, jobs, multi-day exceptions, mine/all, projects & progress, attachments, export |
| `core/tests/test_security.py` | **yes** | lockout, CSRF, headers, authorisation matrix, SQLi/XSS payloads, upload attacks, passwords |

Without Django installed you can still run the pure-Python ones:

```bash
python -m unittest core.tests.test_engine core.tests.test_filecheck core.tests.test_safe_charts_stats core.tests.test_static_security core.tests.test_env core.tests.test_datelist core.tests.test_windows
```
