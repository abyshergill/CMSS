"""Security tests: run with  python manage.py test core"""
import os
from django.test import Client
from django.urls import reverse

from core.models import Completion, Equipment, Job, LoginAttempt, Profile
from core.security import MAX_IP_FAILURES, MAX_USER_FAILURES
from .helpers import Base, User, PW, d, iso, pdf, png

BAD = "definitely-wrong-password"


def bad_login(c, username="tina", ip="127.0.0.1", pw=BAD):
    return c.post(reverse("login"), dict(username=username, password=pw), REMOTE_ADDR=ip)


class BruteForce(Base):
    def test_account_locks_after_repeated_failures_even_with_the_right_password(self):
        c = Client()
        for _ in range(MAX_USER_FAILURES):
            self.assertEqual(bad_login(c).status_code, 200)
        r = bad_login(c, pw=PW)                                      # correct password, but locked
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("_auth_user_id", c.session)
        self.assertContains(r, "Too many failed attempts", status_code=429)

    def test_lockout_applies_to_unknown_usernames_too_so_nothing_leaks(self):
        c = Client()
        for _ in range(MAX_USER_FAILURES):
            bad_login(c, username="ghost")
        self.assertEqual(bad_login(c, username="ghost").status_code, 429)

    def test_same_error_for_unknown_user_and_wrong_password(self):
        a, b = bad_login(Client(), "ghost"), bad_login(Client(), "tina")
        self.assertContains(a, "Wrong username or password."); self.assertContains(b, "Wrong username or password.")

    def test_other_accounts_are_not_locked_by_one_account_failing(self):
        c = Client()
        for _ in range(MAX_USER_FAILURES):
            bad_login(c, "tina")
        r = c.post(reverse("login"), dict(username="tom", password=PW), REMOTE_ADDR="127.0.0.1")
        self.assertEqual(r.status_code, 302)

    def test_one_ip_spraying_many_usernames_is_blocked(self):
        c = Client()
        for i in range(MAX_IP_FAILURES):
            bad_login(c, username=f"user{i}", ip="10.9.8.7")
        self.assertEqual(bad_login(c, username="brand-new", ip="10.9.8.7").status_code, 429)
        r = c.post(reverse("login"), dict(username="tina", password=PW), REMOTE_ADDR="10.1.1.1")      # another IP is fine
        self.assertEqual(r.status_code, 302)

    def test_success_resets_the_counter(self):
        c = Client()
        for _ in range(MAX_USER_FAILURES - 1):
            bad_login(c)
        self.assertEqual(c.post(reverse("login"), dict(username="tina", password=PW)).status_code, 302)
        self.assertEqual(LoginAttempt.objects.filter(username="tina", success=False).count(), 0)
        c.logout()
        for _ in range(MAX_USER_FAILURES - 1):
            self.assertEqual(bad_login(c).status_code, 200)

    def test_attempts_are_recorded_and_shown_to_admin_only(self):
        bad_login(Client(), "intruder-xyz")
        self.assertEqual(LoginAttempt.objects.filter(username="intruder-xyz", success=False).count(), 1)
        self.assertContains(self.as_user(self.admin).get(reverse("setup"), {"tab": "security"}), "intruder-xyz")
        self.assertEqual(self.as_user(self.tech).get(reverse("setup")).status_code, 403)

    def test_deactivated_user_cannot_sign_in(self):
        User.objects.filter(pk=self.tech.pk).update(is_active=False)
        c = Client()
        r = c.post(reverse("login"), dict(username="tina", password=PW))
        self.assertEqual(r.status_code, 200); self.assertNotIn("_auth_user_id", c.session)


class SessionsAndRedirects(Base):
    def test_open_redirect_is_blocked(self):
        for evil in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "http://evil.example"):
            r = Client().post(reverse("login") + "?next=" + evil, dict(username="tina", password=PW))
            self.assertEqual(r.status_code, 302); self.assertEqual(r["Location"], reverse("today"), evil)

    def test_local_next_is_honoured(self):
        r = Client().post(reverse("login") + "?next=/jobs/", dict(username="tina", password=PW))
        self.assertEqual(r["Location"], "/jobs/")

    def test_session_key_changes_on_login(self):
        c = Client()
        s = c.session; s["probe"] = 1; s.save()                       # an anonymous session already exists (the attacker planted it)
        before = c.cookies["sessionid"].value
        c.post(reverse("login"), dict(username="tina", password=PW))
        self.assertNotEqual(before, c.cookies["sessionid"].value)      # login rotates the key: session fixation is not possible

    def test_cookie_flags(self):
        c = Client(); c.post(reverse("login"), dict(username="tina", password=PW))
        ck = c.cookies["sessionid"]
        self.assertTrue(ck["httponly"]); self.assertEqual(ck["samesite"], "Lax")

    def test_logout_is_post_only_and_ends_the_session(self):
        c = self.as_user(self.tech)
        self.assertEqual(c.get(reverse("logout")).status_code, 405)
        self.assertEqual(c.get(reverse("today")).status_code, 200)
        c.post(reverse("logout"))
        self.assertEqual(c.get(reverse("today")).status_code, 302)

    def test_no_django_admin_site(self):
        self.assertEqual(self.as_user(self.admin).get("/admin/").status_code, 404)

    def test_login_page_is_not_cached(self):
        self.assertIn("no-store", Client().get(reverse("login"))["Cache-Control"])


class Headers(Base):
    def test_security_headers_on_pages_and_errors(self):
        c = self.as_user(self.tech)
        for r in (c.get(reverse("today")), c.get("/nope/"), Client().get(reverse("login"))):
            self.assertIn("default-src 'self'", r["Content-Security-Policy"])
            self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"])
            self.assertIn("script-src 'self'", r["Content-Security-Policy"])
            self.assertNotIn("unsafe-inline", r["Content-Security-Policy"])
            self.assertEqual(r["X-Content-Type-Options"], "nosniff")
            self.assertEqual(r["X-Frame-Options"], "DENY")
            self.assertEqual(r["Referrer-Policy"], "same-origin")
            self.assertIn("no-store", r["Cache-Control"])

    def test_wrong_host_is_rejected(self):
        self.assertEqual(Client().get(reverse("login"), HTTP_HOST="evil.example").status_code, 400)


class Csrf(Base):
    def test_post_without_token_is_rejected_everywhere(self):
        c = Client(enforce_csrf_checks=True); c.login(username="boss", password=PW)
        j = self.make_job("X")
        posts = [(reverse("login"), {}), (reverse("done"), dict(job=j.pk, due_date=iso(d(0)))), (reverse("skip_create"), {}),
                 (reverse("job_delete", args=[j.pk]), {}), (reverse("logout"), {}), (reverse("user_add"), {}),
                 (reverse("job_new"), job_data()), (reverse("project_new"), {})]
        for url, data in posts:
            self.assertEqual(c.post(url, data).status_code, 403, url)
        self.assertTrue(Job.objects.filter(pk=j.pk).exists()); self.assertEqual(Completion.objects.count(), 0)

    def test_post_with_token_works(self):
        c = Client(enforce_csrf_checks=True)
        token = c.get(reverse("login")).cookies["csrftoken"].value
        r = c.post(reverse("login"), dict(username="tina", password=PW, csrfmiddlewaretoken=token))
        self.assertEqual(r.status_code, 302)

    def test_every_form_in_the_templates_carries_a_token(self):
        j = self.make_job("X", assignee=self.tech)
        c = self.as_user(self.admin)
        for url in (reverse("today"), reverse("jobs"), reverse("job_detail", args=[j.pk]), reverse("equipment"), reverse("job_new"),
                    *[reverse("setup") + f"?tab={t}" for t in ("people", "groups", "areas", "security")]):
            html = c.get(url).content.decode()
            forms_post = html.lower().count('method="post"'); tokens = html.count("csrfmiddlewaretoken")
            self.assertGreaterEqual(tokens, forms_post, url)


def job_data():
    return dict(title="t", kind="once", due_date=iso(d(0)), weight=1)


class Authorisation(Base):
    def test_anonymous_gets_redirected_never_data(self):
        j = self.make_job("SECRET-TITLE")
        for url in (reverse("jobs"), reverse("job_detail", args=[j.pk]), reverse("history"), reverse("export"), reverse("setup"),
                    reverse("attachment", args=[1]), reverse("evidence", args=[1]), reverse("project_detail", args=[1])):
            r = self.client.get(url); self.assertEqual(r.status_code, 302, url); self.assertIn(reverse("login"), r["Location"])
            self.assertNotIn(b"SECRET-TITLE", r.content)

    def test_technician_is_blocked_from_every_admin_action(self):
        c = self.as_user(self.tech); j = self.make_job("X", "repeat", assignee=self.tech)
        gets = [reverse("setup"), reverse("job_new"), reverse("project_new"), reverse("group_detail", args=[1])] + \
               [reverse("setup") + f"?tab={t}" for t in ("people", "groups", "areas", "security")]
        posts = [reverse("job_edit", args=[j.pk]), reverse("job_toggle", args=[j.pk]), reverse("job_delete", args=[j.pk]),
                 reverse("job_upload", args=[j.pk]), reverse("skip_create"), reverse("move_create"),
                 reverse("group_members_add", args=[1]), reverse("group_member_remove", args=[1, 1]),
                 reverse("area_add"), reverse("area_delete", args=[self.area.pk]), reverse("user_add"), reverse("user_toggle", args=[self.admin.pk]),
                 reverse("user_password", args=[self.admin.pk]), reverse("user_role", args=[self.tech.pk]), reverse("equipment_new"), reverse("equipment_edit", args=[self.eq.pk]),
                 reverse("equipment_archive", args=[self.eq.pk]), reverse("project_delete", args=[1]),
                 reverse("group_add"), reverse("group_edit", args=[1]), reverse("group_delete", args=[1])]
        for u in gets:
            self.assertEqual(c.get(u).status_code, 403, u)
        for u in posts:
            self.assertEqual(c.post(u, {}).status_code, 403, u)
        self.assertTrue(Job.objects.filter(pk=j.pk, active=True).exists())
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password(PW))
        self.assertTrue(Equipment.objects.get(pk=self.eq.pk).active)

    def test_technician_cannot_promote_themselves(self):
        c = self.as_user(self.tech)
        c.post(reverse("user_add"), dict(username="evil", password1=PW, password2=PW, role="admin"))
        c.post(reverse("user_toggle", args=[self.tech.pk]))
        self.assertFalse(User.objects.filter(username="evil").exists())
        self.assertEqual(Profile.objects.get(user=self.tech).role, "tech")

    def test_idor_on_other_peoples_projects_and_jobs(self):
        from core.models import Project
        p = Project.objects.create(name="Boss project", owner=self.admin)
        t = Job.objects.create(project=p, title="task", kind="once", start_date=d(1), assignee=self.tech2)
        c = self.as_user(self.tech)
        self.assertEqual(c.post(reverse("project_toggle", args=[p.pk])).status_code, 403)
        self.assertEqual(c.get(reverse("project_edit", args=[p.pk])).status_code, 403)
        self.assertEqual(c.get(reverse("task_new", args=[p.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("done"), dict(job=t.pk, due_date=iso(d(1)))).status_code, 403)
        self.assertEqual(c.post(reverse("job_upload", args=[t.pk]), dict(files=[pdf()])).status_code, 403)
        p.refresh_from_db(); self.assertEqual(p.status, "active")

    def test_admin_cannot_deactivate_self(self):
        self.as_user(self.admin).post(reverse("user_toggle", args=[self.admin.pk]))
        self.assertTrue(User.objects.get(pk=self.admin.pk).is_active)

    def test_state_changing_urls_reject_get(self):
        c = self.as_user(self.admin); j = self.make_job("X")
        for u in (reverse("done"), reverse("job_delete", args=[j.pk]), reverse("job_toggle", args=[j.pk]), reverse("skip_create"),
                  reverse("user_add"), reverse("area_add"), reverse("equipment_archive", args=[self.eq.pk]), reverse("project_delete", args=[1]),
                  reverse("group_add"), reverse("group_edit", args=[1]), reverse("group_delete", args=[1]),
                  reverse("group_members_add", args=[1]), reverse("group_member_remove", args=[1, 1])):
            self.assertEqual(c.get(u).status_code, 405, u)
        self.assertTrue(Job.objects.filter(pk=j.pk).exists())


class SqlInjection(Base):
    PAYLOADS = ["' OR '1'='1", "'; DROP TABLE core_job;--", "1 UNION SELECT username,password FROM auth_user--", '" OR ""="', "%' OR 1=1 --", "\\", "%00", "a" * 5000]

    def test_search_boxes_treat_payloads_as_plain_text(self):
        self.make_job("Visible job", assignee=self.tech)
        Completion.objects.create(job=Job.objects.get(), due_date=d(0), planned_date=d(0), done_by=self.tech, note="n")
        c = self.as_user(self.admin)
        for p in self.PAYLOADS:
            for url in (reverse("jobs"), reverse("history")):
                r = c.get(url, {"q": p})
                self.assertEqual(r.status_code, 200, (url, p[:20]))
                self.assertNotContains(r, "Visible job")                         # '1'='1 must not match everything
                self.assertNotContains(r, "pbkdf2_sha256")
        self.assertTrue(Job.objects.exists()); self.assertEqual(c.get(reverse("jobs"), {"q": "Visible"}).status_code, 200)

    def test_other_query_parameters_are_harmless(self):
        c = self.as_user(self.admin)
        for p in self.PAYLOADS[:5]:
            for url, params in ((reverse("jobs"), {"kind": p, "page": p, "scope": p}), (reverse("today"), {"area": p, "scope": p}),
                                (reverse("calendar"), {"m": p}), (reverse("history"), {"page": p}),
                                (reverse("export"), {"date_from": p, "date_to": p, "format": p, "project": p, "person": p})):
                self.assertIn(c.get(url, params).status_code, (200, 302), (url, p[:20]))

    def test_posted_ids_are_validated(self):
        c = self.as_user(self.admin)
        for p in self.PAYLOADS[:4]:
            self.assertEqual(c.post(reverse("done"), dict(job=p, due_date=p)).status_code, 302)
            self.assertEqual(c.post(reverse("skip_create"), dict(job=p, start_date=p, end_date=p)).status_code, 302)
        self.assertEqual(Completion.objects.count(), 0)

    def test_login_payload_does_not_bypass(self):
        r = Client().post(reverse("login"), dict(username="tina' OR '1'='1", password="x' OR '1'='1"))
        self.assertEqual(r.status_code, 200); self.assertNotIn("_auth_user_id", r.client.session)


class Xss(Base):
    EVIL = '<script>alert(1)</script>'

    def test_user_text_is_escaped_on_every_page(self):
        from core.models import Project
        j = Job.objects.create(equipment=self.eq, title=self.EVIL, instructions=self.EVIL, kind="once", start_date=d(0), assignee=self.tech)
        p = Project.objects.create(name=self.EVIL, description=self.EVIL, owner=self.tech)
        Job.objects.create(project=p, title=self.EVIL, kind="once", start_date=d(0), assignee=self.tech)
        Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech, note=self.EVIL)
        self.area.name = self.EVIL; self.area.save()
        from core.models import Group
        g = Group.objects.create(name=self.EVIL); g.members.add(self.tech)
        Job.objects.create(equipment=self.eq, title="for the group", kind="once", start_date=d(0), assigned_group=g)
        c = self.as_user(self.admin)
        for url in (reverse("today"), reverse("calendar"), reverse("jobs"), reverse("job_detail", args=[j.pk]), reverse("projects"),
                    reverse("project_detail", args=[p.pk]), reverse("history"), reverse("insights"), reverse("equipment"),
                    reverse("equipment_detail", args=[self.eq.pk]), reverse("job_new"), reverse("group_detail", args=[g.pk]),
                    *[reverse("setup") + f"?tab={t}" for t in ("people", "groups", "areas", "security")]):
            html = c.get(url).content.decode()
            self.assertNotIn(self.EVIL, html, url)
            self.assertNotRegex(html, r"<script[^>]*>alert", url)

    def test_chart_labels_are_escaped(self):
        from core.models import Project
        Project.objects.create(name='"><img src=x onerror=alert(1)>', owner=self.tech)
        html = self.as_user(self.admin).get(reverse("insights")).content.decode()
        self.assertNotIn("<img src=x", html)

    def test_no_inline_script_or_handlers_in_pages(self):
        import re
        html = self.as_user(self.admin).get(reverse("today")).content.decode()
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        self.assertNotRegex(html, r"\son\w+=")


class FileSecurity(Base):
    def test_dangerous_files_are_refused(self):
        j = self.make_job("J"); c = self.as_user(self.admin)
        for name in ("shell.php", "run.exe", "x.html", "x.svg", "x.js", "x.sh", "x.pdf.exe", "x.docm", "x.xlsm", "noext", "x."):
            c.post(reverse("job_upload", args=[j.pk]), dict(files=[pdf(name)]))
        self.assertEqual(j.attachments.count(), 0)

    def test_content_must_match_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        j = self.make_job("J"); c = self.as_user(self.admin)
        for name, body in (("a.pdf", b"<?php system($_GET[1]); ?>"), ("a.png", b"<script>alert(1)</script>"), ("a.docx", b"PK\x03\x04junk"),
                           ("a.xlsx", b"not a zip"), ("a.csv", b"a,b\n\x00\x00\x00\x00"), ("a.jpg", b"GIF89a....")):
            c.post(reverse("job_upload", args=[j.pk]), dict(files=[SimpleUploadedFile(name, body)]))
        self.assertEqual(j.attachments.count(), 0)

    def test_empty_and_oversized_files(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        j = self.make_job("J"); c = self.as_user(self.admin)
        c.post(reverse("job_upload", args=[j.pk]), dict(files=[SimpleUploadedFile("e.pdf", b"")]))
        with override_settings(MAX_UPLOAD_MB=1):
            c.post(reverse("job_upload", args=[j.pk]), dict(files=[SimpleUploadedFile("big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 10))]))
        self.assertEqual(j.attachments.count(), 0)

    def test_nothing_stored_under_the_web_root_or_with_the_user_supplied_name(self):
        from django.conf import settings
        j = self.make_job("J"); self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf("..\\..\\boot.pdf")]))
        a = j.attachments.get()
        self.assertNotIn("boot", a.file.name); self.assertTrue(os.path.realpath(a.file.path).startswith(os.path.realpath(str(settings.MEDIA_ROOT))))
        self.assertFalse(str(settings.MEDIA_ROOT).startswith(str(settings.STATIC_ROOT)))
        self.assertEqual(self.client.get("/media/" + a.file.name).status_code, 404)

    def test_files_are_served_as_downloads_with_a_sandbox(self):
        j = self.make_job("J"); self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[png("p.png")]))
        r = self.client.get(reverse("attachment", args=[j.attachments.get().pk]))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff"); self.assertIn("sandbox", r["Content-Security-Policy"])


class PasswordsAndAccounts(Base):
    def test_weak_passwords_rejected_when_adding_and_resetting_users(self):
        c = self.as_user(self.admin)
        for pw in ("short1", "password123", "1234567890", "qwertyuiop"):
            c.post(reverse("user_add"), dict(username="newbie", password1=pw, password2=pw, role="tech"))
            self.assertFalse(User.objects.filter(username="newbie").exists(), pw)
            c.post(reverse("user_password", args=[self.tech.pk]), dict(password=pw))
            self.assertTrue(User.objects.get(pk=self.tech.pk).check_password(PW), pw)

    def test_admin_can_add_and_reset(self):
        c = self.as_user(self.admin)
        c.post(reverse("user_add"), dict(username="newbie", password1=PW, password2=PW, role="tech"))
        u = User.objects.get(username="newbie"); self.assertEqual(u.profile.role, "tech")
        c.post(reverse("user_password", args=[u.pk]), dict(password="An0ther-Str0ng-One"))
        self.assertTrue(User.objects.get(pk=u.pk).check_password("An0ther-Str0ng-One"))

    def test_duplicate_usernames_differing_by_case_are_refused(self):
        self.as_user(self.admin).post(reverse("user_add"), dict(username="TINA", password1=PW, password2=PW, role="tech"))
        self.assertEqual(User.objects.filter(username__iexact="tina").count(), 1)

    def test_passwords_are_hashed(self):
        self.assertTrue(User.objects.get(username="tina").password.startswith(("pbkdf2_", "argon2", "bcrypt")))

    def test_password_change_needs_old_password_and_strength(self):
        c = self.as_user(self.tech)
        c.post(reverse("password_change"), dict(old_password="wrong", new_password1="An0ther-Str0ng-One", new_password2="An0ther-Str0ng-One"))
        self.assertTrue(User.objects.get(username="tina").check_password(PW))
        c.post(reverse("password_change"), dict(old_password=PW, new_password1="abc", new_password2="abc"))
        self.assertTrue(User.objects.get(username="tina").check_password(PW))
        c.post(reverse("password_change"), dict(old_password=PW, new_password1="An0ther-Str0ng-One", new_password2="An0ther-Str0ng-One"))
        self.assertTrue(User.objects.get(username="tina").check_password("An0ther-Str0ng-One"))

    def test_first_run_is_closed_once_a_user_exists(self):
        r = Client().post(reverse("first_run"), dict(username="hacker", password1=PW, password2=PW))
        self.assertEqual(r.status_code, 302); self.assertFalse(User.objects.filter(username="hacker").exists())

    def test_group_membership_cannot_be_self_assigned(self):
        from core.models import Group
        g = Group.objects.create(name="Locked")
        self.as_user(self.tech).post(reverse("group_edit", args=[g.pk]), dict(name="Locked", members=[self.tech.pk]))
        self.assertEqual(g.members.count(), 0)
        j = Job.objects.create(equipment=self.eq, title="g", kind="once", start_date=d(0), assigned_group=g)
        self.assertEqual(self.as_user(self.tech).post(reverse("done"), dict(job=j.pk, due_date=iso(d(0)))).status_code, 403)


class SettingsAudit(Base):
    def test_production_settings(self):
        from django.conf import settings
        self.assertFalse(settings.DEBUG)
        self.assertNotIn("*", settings.ALLOWED_HOSTS)
        self.assertGreaterEqual(len(settings.SECRET_KEY), 50)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY and settings.CSRF_COOKIE_HTTPONLY)
        self.assertEqual(settings.X_FRAME_OPTIONS, "DENY")
        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 24 * 3600)
        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)
        self.assertGreaterEqual(len(settings.AUTH_PASSWORD_VALIDATORS), 4)
        self.assertGreaterEqual([v for v in settings.AUTH_PASSWORD_VALIDATORS if "MinimumLength" in v["NAME"]][0]["OPTIONS"]["min_length"], 10)
        self.assertNotIn(settings.SECRET_KEY.lower(), ("change-me", "changeme"))
