"""Static security audit of the source code (pure Python - no Django needed).
Fails if someone adds a dangerous pattern. Run: python -m unittest core.tests.test_static_security -v"""
import re, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts and "migrations" not in p.parts]
TEMPLATES = list((ROOT / "templates").rglob("*.html"))


def read(p):
    return p.read_text(encoding="utf-8")


class SqlInjection(unittest.TestCase):
    def test_no_raw_sql_anywhere(self):
        pats = [r"\.raw\(", r"\.extra\(", r"cursor\(\)", r"\.execute\(", r"RawSQL", r"connection\.cursor"]
        bad = [(p.name, pat) for p in PY for pat in pats if re.search(pat, read(p))]
        self.assertEqual(bad, [], "Use the ORM only - raw SQL found")

    def test_no_string_built_queries(self):
        bad = [p.name for p in PY if re.search(r"(SELECT|INSERT|UPDATE|DELETE)\s.*(%s|\{|\+\s*request)", read(p), re.I) and p.name != "demo.py"]
        self.assertEqual(bad, [])


class Xss(unittest.TestCase):
    def test_no_safe_filter_or_autoescape_off_in_templates(self):
        bad = [(p.name, m) for p in TEMPLATES for m in re.findall(r"\|\s*safe\b|autoescape\s+off|\{%\s*endautoescape", read(p))]
        self.assertEqual(bad, [])

    def test_mark_safe_only_in_chart_module(self):
        offenders = [p.name for p in PY if "mark_safe" in read(p) and p.name != "charts.py"]
        self.assertEqual(offenders, [])

    def test_no_inline_script_style_or_handlers_in_templates(self):
        for p in TEMPLATES:
            s = read(p)
            self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", s), f"inline <script> in {p.name}")
            self.assertIsNone(re.search(r"\sstyle\s*=", s), f"inline style= in {p.name}")
            self.assertIsNone(re.search(r"\son[a-z]+\s*=", s), f"inline event handler in {p.name}")
            self.assertIsNone(re.search(r"javascript:", s, re.I), f"javascript: URL in {p.name}")

    def test_no_external_resources(self):
        for p in TEMPLATES:
            self.assertIsNone(re.search(r"""(src|href)\s*=\s*["']https?://""", read(p)), p.name)


class Csrf(unittest.TestCase):
    def test_every_post_form_has_csrf_token(self):
        for p in TEMPLATES:
            s = read(p)
            for m in re.finditer(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", s, re.S | re.I):
                self.assertIn("{% csrf_token %}", m.group(1), f"POST form without csrf_token in {p.name}")

    def test_no_csrf_exempt_and_no_get_forms_that_change_data(self):
        self.assertEqual([p.name for p in PY if "csrf_exempt" in read(p)], [])

    def test_state_changing_views_are_post_only(self):
        src = read(ROOT / "core" / "views" / "work.py") + read(ROOT / "core" / "views" / "projects.py") + \
              read(ROOT / "core" / "views" / "setup.py") + read(ROOT / "core" / "views" / "equipment.py")
        for name in ("job_toggle", "job_delete", "job_upload", "attachment_delete", "skip_create", "skip_undo", "move_create",
                     "move_undo", "done", "completion_undo", "project_toggle", "project_delete", "equipment_new",
                     "equipment_edit", "equipment_archive", "area_add", "area_delete", "user_add", "user_toggle",
                     "user_password", "group_add", "group_edit", "group_members_add", "group_member_remove", "group_delete"):
            m = re.search(r"((?:@\w+(?:\([^)]*\))?\n)+)def %s\(" % name, src)
            self.assertIsNotNone(m, name)
            self.assertIn("@require_POST", m.group(1), f"{name} must be POST-only")


class Settings(unittest.TestCase):
    S = read(ROOT / "config" / "settings.py")

    def test_debug_off_by_default_and_no_hardcoded_secret(self):
        self.assertIn('DEBUG = os.environ.get("DJANGO_DEBUG") == "1"', self.S)
        self.assertNotRegex(self.S, r"SECRET_KEY\s*=\s*['\"]")
        self.assertNotIn('ALLOWED_HOSTS = ["*"]', self.S)

    def test_cookie_and_header_hardening_present(self):
        for needle in ("SESSION_COOKIE_HTTPONLY = True", "CSRF_COOKIE_HTTPONLY = True", 'SESSION_COOKIE_SAMESITE = "Lax"',
                       'X_FRAME_OPTIONS = "DENY"', "SECURE_CONTENT_TYPE_NOSNIFF = True", "SECURE_HSTS_SECONDS",
                       "MinimumLengthValidator", "CommonPasswordValidator", "DATA_UPLOAD_MAX_NUMBER_FILES"):
            self.assertIn(needle, self.S, needle)

    def test_required_middleware_present_and_admin_site_off(self):
        for m in ("CsrfViewMiddleware", "XFrameOptionsMiddleware", "SecurityMiddleware", "SecurityHeadersMiddleware"):
            self.assertIn(m, self.S)
        self.assertNotIn("django.contrib.admin", self.S)

    def test_uploads_are_outside_static_and_not_routed(self):
        self.assertIn('MEDIA_ROOT = DATA_DIR / "uploads"', self.S)
        self.assertNotIn("MEDIA_URL", self.S)
        self.assertNotIn("static(", read(ROOT / "config" / "urls.py"))

    def test_csp_blocks_scripts_and_inline_styles_and_framing(self):
        sec = read(ROOT / "core" / "security.py")
        for needle in ("script-src 'self'", "style-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "form-action 'self'", "base-uri 'self'"):
            self.assertIn(needle, sec)
        self.assertNotIn("unsafe-inline", sec.split("CSP =")[1].split(")")[0])


class Misc(unittest.TestCase):
    def test_no_dangerous_calls(self):
        for p in PY:
            s = read(p)
            for pat in (r"\beval\(", r"\bexec\(", r"pickle\.loads", r"yaml\.load\(", r"subprocess", r"os\.system", r"shell=True"):
                self.assertIsNone(re.search(pat, s), f"{pat} in {p.name}")

    def test_redirect_targets_are_validated(self):
        sec = read(ROOT / "core" / "security.py")
        self.assertIn("url_has_allowed_host_and_scheme", sec)
        for f in ("work.py", "accounts.py"):
            s = read(ROOT / "core" / "views" / f)
            self.assertNotRegex(s, r"redirect\(request\.(GET|POST)", f)

    def test_uploads_use_random_names(self):
        self.assertIn("uuid.uuid4().hex", read(ROOT / "core" / "models.py"))

    def test_downloads_are_served_with_nosniff_and_sandbox(self):
        u = read(ROOT / "core" / "views" / "_util.py")
        for needle in ("X-Content-Type-Options", "nosniff", "sandbox", "as_attachment"):
            self.assertIn(needle, u)


class Templates(unittest.TestCase):
    FILES = sorted((ROOT / "templates").rglob("*.html"))

    def forms(self, text):
        return re.findall(r"<form\b([^>]*)>(.*?)</form>", text, re.S | re.I)

    def test_every_post_form_has_a_csrf_token(self):
        for f in self.FILES:
            for attrs, body in self.forms(read(f)):
                if re.search(r"method\s*=\s*[\"']?post", attrs, re.I):
                    self.assertIn("csrf_token", body, f"{f.name}: POST form without csrf_token")

    def test_no_get_form_has_a_csrf_token(self):
        for f in self.FILES:
            for attrs, body in self.forms(read(f)):
                if not re.search(r"method\s*=\s*[\"']?post", attrs, re.I):
                    self.assertNotIn("csrf_token", body, f"{f.name}: token leaked into a GET form")

    def test_forms_with_file_inputs_are_multipart(self):
        for f in self.FILES:
            text = read(f)
            for attrs, body in self.forms(text):
                if 'type="file"' in body or "form.files" in body:
                    self.assertIn("multipart/form-data", attrs, f"{f.name}: upload form is not multipart")

    def test_job_kind_is_a_radio_group_because_the_form_template_and_js_need_radio_inputs(self):
        forms = read(ROOT / "core" / "forms.py")
        self.assertTrue(re.search(r"kind\s*=\s*forms\.ChoiceField\(.*?widget=forms\.RadioSelect", forms, re.S), "JobForm.kind must be a RadioSelect")
        self.assertIn("input[name=kind]", read(ROOT / "static" / "app.js"))

    def test_hidden_attribute_cannot_be_overridden_by_css(self):
        self.assertRegex(read(ROOT / "static" / "style.css"), r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_repeat_box_is_not_hidden_in_html_so_it_works_even_if_the_script_fails(self):
        self.assertNotRegex(read(ROOT / "templates" / "job_form.html"), r'id="repeat-box"[^>]*\bhidden')

    def test_no_plant_wide_closures_anywhere(self):
        for f in self.FILES:
            self.assertNotRegex(read(f).lower(), r"closure", f.name)
        urls = read(ROOT / "core" / "urls.py")
        self.assertNotIn("closure", urls)
        svc = read(ROOT / "core" / "services.py")
        self.assertNotIn("job__isnull=True", svc); self.assertNotIn("global_ranges", svc)
        self.assertNotIn("closure_add", read(ROOT / "core" / "views" / "setup.py"))

    def test_setup_page_has_no_per_person_forms(self):
        """The old page repeated a password form + buttons for every user. Now: one shared dialog, a Manage button per row."""
        t = read(ROOT / "templates" / "setup.html")
        self.assertLessEqual(t.count('type="password"'), 1)
        self.assertIn("{% for u in page %}", t)
        row = t[t.index("{% for u in page %}"):t.index("{% empty %}", t.index("{% for u in page %}"))]
        self.assertNotIn("<form", row)
        self.assertIn("paginate(", read(ROOT / "core" / "views" / "setup.py"))
        self.assertTrue((ROOT / "templates" / "partials" / "_pager.html").exists())

    def test_group_membership_is_not_a_wall_of_checkboxes_for_every_user(self):
        self.assertNotIn("CheckboxSelectMultiple", read(ROOT / "core" / "forms.py"))
        g = read(ROOT / "templates" / "group_detail.html")
        self.assertIn("candidates", g); self.assertNotIn("{% for u in users %}", g)

    def test_no_safe_filter_on_user_text(self):
        for f in self.FILES:
            self.assertNotRegex(read(f), r"\|\s*safe\b|\{%\s*autoescape\s+off", f.name)


class FinishingRulesAndPaging(unittest.TestCase):
    """The rules live on the server (forms.py), not in the page: hiding a button is never the only protection."""
    def test_done_form_enforces_window_file_checkpoints_on_the_server(self):
        f = read(ROOT / "core" / "forms.py")
        for needle in ("engine.finish_state(", "job.evidence_required", "checklist.tick_snapshot(", "is_admin(self.user)"):
            self.assertIn(needle, f)

    def test_done_view_checks_who_you_are_before_telling_you_what_is_wrong(self):
        v = read(ROOT / "core" / "views" / "work.py")
        self.assertLess(v.index("can_do_job(request.user, job)"), v.index("form_errors(request, form)", v.index("def done(")))

    def test_every_list_uses_the_one_paginate_helper(self):
        for f in (ROOT / "core" / "views").glob("*.py"):
            if f.name != "_util.py":
                self.assertNotIn("Paginator(", read(f), f.name)
        for name in ("work", "reports", "equipment", "projects", "setup"):
            self.assertIn("paginate(", read(ROOT / "core" / "views" / f"{name}.py"), name)

    def test_big_lists_include_the_pager(self):
        for t in ("jobs", "history", "equipment", "projects", "project_detail", "today", "job_detail", "equipment_detail", "setup", "group_detail"):
            self.assertIn("_pager.html", read(ROOT / "templates" / f"{t}.html"), t)

    def test_page_size_is_limited_to_known_values(self):
        u = read(ROOT / "core" / "views" / "_util.py")
        self.assertIn("PAGE_SIZES = (10, 25, 50, 100)", u); self.assertIn("per in PAGE_SIZES", u)

    def test_no_inline_script_handlers_or_styles_added_to_templates(self):
        for f in TEMPLATES:
            self.assertNotRegex(read(f), r"\son(click|change|submit|load)\s*=|<script(?![^>]*src=)|\sstyle\s*=", f.name)

    def test_menu_toggle_and_checklist_code_never_writes_html(self):
        js = read(ROOT / "static" / "app.js")
        self.assertNotRegex(js, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(")
        self.assertIn("textContent = String(text)", js)

    def test_menu_cookie_is_only_compared_never_printed(self):
        b = read(ROOT / "templates" / "base.html")
        self.assertEqual(b.count("request.COOKIES.sidebar"), 3)
        self.assertNotRegex(b, r"\{\{\s*request\.COOKIES")


class Roles(unittest.TestCase):
    """Admin / Manager / Engineer rules that must not be lost in a refactor (pure Python)."""
    def test_adding_people_is_limited_by_who_adds(self):
        s = read(ROOT / "core" / "views" / "setup.py")
        self.assertIn("NewUserForm(request.POST, actor=request.user)", s)
        self.assertIn("NewUserForm(actor=request.user)", s)

    def test_managers_are_kept_away_from_admin_accounts(self):
        s = read(ROOT / "core" / "views" / "setup.py")
        for fn in ("user_toggle", "user_password", "user_role"):
            body = s[s.index(f"def {fn}("):]
            body = body[:body.index("\n\n\n")]
            self.assertIn("_refuse_admin_account(", body, fn)
            self.assertIn("@admin_required", s[s.index(f"def {fn}(") - 40:s.index(f"def {fn}(")], fn)

    def test_only_a_full_admin_can_hand_out_the_admin_role(self):
        p = read(ROOT / "core" / "permissions.py")
        self.assertIn("v != Profile.ADMIN or is_full_admin(actor)", p)
        self.assertIn("role_of(target) != Profile.ADMIN", p)

    def test_the_old_name_is_gone_from_the_screens(self):
        for f in TEMPLATES + [ROOT / "core" / "views" / "setup.py", ROOT / "core" / "models.py"]:
            self.assertNotIn("Technician", read(f), f.name)

    def test_export_filters_by_job_name_with_the_orm(self):
        r = read(ROOT / "core" / "views" / "reports.py")
        self.assertIn("job__title__icontains=job_name", r)
        self.assertIn('re.sub(r"[^A-Za-z0-9]+"', r)                      # file name: letters, digits, dashes only

    def test_sign_out_is_a_post_form_with_csrf(self):
        t = read(ROOT / "templates" / "base.html")
        self.assertRegex(t, r"<form method=\"post\" action=\"\{% url 'logout' %\}\">\{% csrf_token %\}<button class=\"who-btn out\"")


class TemplateStructure(unittest.TestCase):
    """Catches template mistakes that only show up as a 500 page at run time (no Django needed)."""
    PAIRS = {"if": "endif", "for": "endfor", "with": "endwith", "block": "endblock"}

    def test_tags_are_balanced(self):
        for f in TEMPLATES:
            stack = []
            for m in re.finditer(r"\{%\s*(\w+)", read(f)):
                tag = m.group(1)
                if tag in self.PAIRS:
                    stack.append(tag)
                elif tag in self.PAIRS.values():
                    self.assertTrue(stack and self.PAIRS[stack[-1]] == tag, f"{f.name}: stray {tag}")
                    stack.pop()
            self.assertEqual(stack, [], f"{f.name}: unclosed {stack}")

    def test_no_block_is_defined_twice(self):
        for f in TEMPLATES:
            names = re.findall(r"\{%\s*block\s+(\w+)", read(f))
            self.assertEqual(len(names), len(set(names)), f"{f.name}: duplicate block {sorted({n for n in names if names.count(n) > 1})}")

    def test_nothing_but_blank_after_the_last_block(self):
        for f in TEMPLATES:
            t = read(f)
            if "{% extends" in t:                                   # in a child template, text outside blocks is ignored - so it is a mistake
                self.assertEqual(t[t.rindex("{% endblock %}") + len("{% endblock %}"):].strip(), "", f.name)

    def test_every_template_loads_the_tag_libraries_it_uses(self):
        """{% load %} is per file - an included partial does not inherit it from its parent (that was a 500 on the Today page)."""
        ui_src = read(ROOT / "core" / "templatetags" / "ui.py")
        ui_names = set(re.findall(r"@register\.\w+(?:\([^)]*\))?\s*\ndef (\w+)", ui_src))
        for f in TEMPLATES:
            t = re.sub(r"\{#.*?#\}", "", read(f))
            loads = set(" ".join(re.findall(r"\{%\s*load\s+([^%]+)%\}", t)).split())
            used = set(re.findall(r"\|\s*(\w+)", t)) | set(re.findall(r"\{%\s*(\w+)", t))
            if used & ui_names:
                self.assertIn("ui", loads, f"{f.name} uses {sorted(used & ui_names)} without {{% load ui %}}")
            if re.search(r"\{%\s*static", t):
                self.assertIn("static", loads, f"{f.name} uses static without {{% load static %}}")

    def test_every_included_template_exists(self):
        for f in TEMPLATES:
            for inc in re.findall(r"\{%\s*(?:include|extends)\s+\"([^\"]+)\"", read(f)):
                self.assertTrue((ROOT / "templates" / inc).exists(), f"{f.name} -> {inc}")


if __name__ == "__main__":
    unittest.main()
