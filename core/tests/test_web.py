"""Feature tests: run with  python manage.py test core"""
import csv, io
from datetime import timedelta

from django.urls import reverse

from core.models import Completion, Job, JobAttachment, MoveException, Project, SkipRange
from core.services import load_occurrences, project_stats_map
from .helpers import Base, User, PW, d, iso, pdf, png, today, xlsx


def job_post(**kw):
    data = dict(title="Grease", equipment="", kind="once", due_date=iso(d(0)), first_date="", every="", unit="", end_date="",
                assignee="", weight=1, instructions="")
    data.update(kw)
    return data


class FirstRunAndLogin(Base):
    def test_first_run_creates_admin_when_no_users(self):
        User.objects.all().delete()
        self.assertRedirects(self.client.get(reverse("login")), reverse("first_run"))
        r = self.client.post(reverse("first_run"), dict(username="owner", password1=PW, password2=PW))
        self.assertRedirects(r, reverse("login"))
        self.assertTrue(User.objects.get(username="owner").is_superuser)
        self.assertEqual(self.client.get(reverse("first_run")).status_code, 302)       # closed once a user exists

    def test_weak_password_rejected_on_first_run(self):
        User.objects.all().delete()
        r = self.client.post(reverse("first_run"), dict(username="owner", password1="12345678", password2="12345678"))
        self.assertEqual(r.status_code, 200); self.assertFalse(User.objects.exists())

    def test_pages_need_login(self):
        for name in ("today", "calendar", "jobs", "projects", "equipment", "history", "insights"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name); self.assertIn(reverse("login"), r["Location"])


class NormalAndRepeatingJobs(Base):
    def test_admin_creates_one_off_and_repeating_jobs(self):
        c = self.as_user(self.admin)
        r = c.post(reverse("job_new"), job_post(title="Fix guard", equipment=self.eq.pk, kind="once", due_date=iso(d(3))))
        self.assertEqual(r.status_code, 302)
        r = c.post(reverse("job_new"), job_post(title="Lube", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=2, unit="week"))
        self.assertEqual(r.status_code, 302)
        once, rep = Job.objects.get(title="Fix guard"), Job.objects.get(title="Lube")
        self.assertEqual((once.kind, once.start_date, once.every), ("once", d(3), None))
        self.assertEqual((rep.kind, rep.every, rep.unit, rep.start_date), ("repeat", 2, "week", d(0)))

    def test_repeating_job_validation(self):
        c = self.as_user(self.admin)
        r = c.post(reverse("job_new"), job_post(equipment=self.eq.pk, kind="repeat", first_date="", every="", unit=""))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Job.objects.count(), 0)
        r = c.post(reverse("job_new"), job_post(equipment=self.eq.pk, kind="repeat", first_date=iso(d(5)), every=1, unit="day", end_date=iso(d(1))))
        self.assertEqual(Job.objects.count(), 0)

    def test_equipment_required_for_normal_jobs(self):
        r = self.as_user(self.admin).post(reverse("job_new"), job_post(equipment=""))
        self.assertEqual(r.status_code, 200); self.assertEqual(Job.objects.count(), 0)

    def test_technician_cannot_create_jobs(self):
        self.assertEqual(self.as_user(self.tech).get(reverse("job_new")).status_code, 403)
        self.assertEqual(self.as_user(self.tech).post(reverse("job_new"), job_post(equipment=self.eq.pk)).status_code, 403)


class MultiDayExceptions(Base):
    def setUp(self):
        self.job = self.make_job("Daily check", "repeat", start=-6, every=1, unit="day")

    def window(self, **kw):
        return [(o.when, o.status) for o in load_occurrences(d(-6), d(3), self.admin, **kw)]

    def test_skip_several_days_in_one_go(self):
        r = self.as_user(self.admin).post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(-4)), end_date=iso(d(-2)), reason="Repair"))
        self.assertEqual(r.status_code, 302)
        sr = SkipRange.objects.get()
        self.assertEqual((sr.start_date, sr.end_date, sr.days, sr.created_by), (d(-4), d(-2), 3, self.admin))
        dates = [w for w, _ in self.window()]
        for gone in (d(-4), d(-3), d(-2)):
            self.assertNotIn(gone, dates)
        for kept in (d(-6), d(-5), d(-1), d(0)):
            self.assertIn(kept, dates)

    def test_single_day_still_works(self):
        self.as_user(self.admin).post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(-1)), end_date=iso(d(-1))))
        self.assertNotIn(d(-1), [w for w, _ in self.window()])

    def test_several_separate_ranges(self):
        c = self.as_user(self.admin)
        c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(-5)), end_date=iso(d(-4))))
        c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(1)), end_date=iso(d(2))))
        self.assertEqual(SkipRange.objects.count(), 2)
        dates = [w for w, _ in self.window()]
        self.assertTrue({d(-5), d(-4), d(1), d(2)}.isdisjoint(dates))

    def test_skipped_days_visible_when_requested(self):
        self.as_user(self.admin).post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(-3)), end_date=iso(d(-2))))
        st = dict(self.window(include_skipped=True))
        self.assertEqual((st[d(-3)], st[d(-2)]), ("skipped", "skipped"))

    def test_invalid_ranges_rejected(self):
        c = self.as_user(self.admin)
        c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(3)), end_date=iso(d(1))))               # end before start
        c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(0)), end_date=iso(d(400))))             # longer than a year
        once = self.make_job("one-off")
        c.post(reverse("skip_create"), dict(job=once.pk, start_date=iso(d(0)), end_date=iso(d(1))))                   # not a repeating job
        self.assertEqual(SkipRange.objects.count(), 0)

    def test_undo_range(self):
        c = self.as_user(self.admin)
        c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(-3)), end_date=iso(d(-2))))
        c.post(reverse("skip_undo", args=[SkipRange.objects.get().pk]))
        self.assertEqual(SkipRange.objects.count(), 0); self.assertIn(d(-3), [w for w, _ in self.window()])

    def test_there_are_no_plant_wide_closures_and_old_ones_are_ignored(self):
        """Some jobs are meant to run on shutdown days, so nothing may close repeating jobs automatically."""
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse("closure_add")
        SkipRange.objects.create(job=None, start_date=d(-3), end_date=d(0), reason="legacy row from an older version")
        dates = [w for w, _ in self.window()]
        for day in (d(-3), d(-2), d(-1), d(0)):
            self.assertIn(day, dates)

    def test_move_one_date(self):
        r = self.as_user(self.admin).post(reverse("move_create"), dict(job=self.job.pk, orig_date=iso(d(-1)), new_date=iso(d(2)), reason="Parts"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(MoveException.objects.get().new_date, d(2))
        occ = {o.orig: o for o in load_occurrences(d(-6), d(3), self.admin)}
        self.assertTrue(occ[d(-1)].moved); self.assertEqual(occ[d(-1)].when, d(2))

    def test_move_rules(self):
        c = self.as_user(self.admin)
        c.post(reverse("move_create"), dict(job=self.job.pk, orig_date=iso(d(-1)), new_date=iso(d(-1))))           # same day
        c.post(reverse("move_create"), dict(job=self.job.pk, orig_date=iso(d(-1)), new_date=iso(d(300))))          # too far
        c2 = self.make_job("Weekly", "repeat", start=0, every=1, unit="week")
        c.post(reverse("move_create"), dict(job=c2.pk, orig_date=iso(d(3)), new_date=iso(d(4))))                   # not a schedule date
        self.assertEqual(MoveException.objects.count(), 0)

    def test_only_admin_can_add_exceptions(self):
        c = self.as_user(self.tech)
        self.assertEqual(c.post(reverse("skip_create"), dict(job=self.job.pk, start_date=iso(d(0)), end_date=iso(d(1)))).status_code, 403)
        self.assertEqual(c.post(reverse("move_create"), dict(job=self.job.pk, orig_date=iso(d(0)), new_date=iso(d(1)))).status_code, 403)
        self.assertEqual(SkipRange.objects.count(), 0)


class DoingTheWork(Base):
    def test_assignee_marks_done_and_series_continues(self):
        j = self.make_job("Check", "repeat", start=-7, assignee=self.tech)
        r = self.as_user(self.tech).post(reverse("done"), dict(job=j.pk, due_date=iso(d(0)), note="ok"))      # today's date (a week-old one is closed)
        self.assertEqual(r.status_code, 302)
        c = Completion.objects.get()
        self.assertEqual((c.done_by, c.due_date, c.planned_date, c.note), (self.tech, d(0), d(0), "ok"))
        st = {o.orig: o.status for o in load_occurrences(d(-7), d(8), self.tech)}
        self.assertEqual((st[d(-7)], st[d(0)], st[d(7)]), ("overdue", "done", "upcoming"))

    def test_done_on_moved_date_records_planned_date(self):
        j = self.make_job("Check", "repeat", start=-7, every=1, unit="day", assignee=self.tech)
        MoveException.objects.create(job=j, orig_date=d(-7), new_date=d(0))                  # the old date was moved to today
        self.as_user(self.tech).post(reverse("done"), dict(job=j.pk, due_date=iso(d(-7))))
        c = Completion.objects.get(); self.assertEqual(c.planned_date, d(0)); self.assertTrue(c.on_time)

    def test_cannot_complete_someone_elses_job_but_can_take_unassigned(self):
        theirs = self.make_job("Theirs", assignee=self.tech2)
        free = self.make_job("Free")
        c = self.as_user(self.tech)
        self.assertEqual(c.post(reverse("done"), dict(job=theirs.pk, due_date=iso(d(0)))).status_code, 403)
        self.assertEqual(c.post(reverse("done"), dict(job=free.pk, due_date=iso(d(0)))).status_code, 302)
        self.assertEqual(Completion.objects.count(), 1)

    def test_admin_can_complete_anything(self):
        j = self.make_job("Theirs", assignee=self.tech2)
        self.as_user(self.admin).post(reverse("done"), dict(job=j.pk, due_date=iso(d(0))))
        self.assertEqual(Completion.objects.count(), 1)

    def test_double_submit_and_bad_dates(self):
        j = self.make_job("Once", assignee=self.tech)
        c = self.as_user(self.tech)
        c.post(reverse("done"), dict(job=j.pk, due_date=iso(d(0)))); c.post(reverse("done"), dict(job=j.pk, due_date=iso(d(0))))
        self.assertEqual(Completion.objects.count(), 1)
        w = self.make_job("Weekly", "repeat", start=0, assignee=self.tech)
        c.post(reverse("done"), dict(job=w.pk, due_date=iso(d(3))))
        self.assertEqual(Completion.objects.filter(job=w).count(), 0)

    def test_done_by_cannot_be_forged(self):
        j = self.make_job("X", assignee=self.tech)
        self.as_user(self.tech).post(reverse("done"), dict(job=j.pk, due_date=iso(d(0)), done_by=self.admin.pk, planned_date="2000-01-01"))
        c = Completion.objects.get(); self.assertEqual(c.done_by, self.tech); self.assertEqual(c.planned_date, d(0))

    def test_evidence_upload_and_download(self):
        j = self.make_job("X", assignee=self.tech)
        c = self.as_user(self.tech)
        c.post(reverse("done"), dict(job=j.pk, due_date=iso(d(0)), evidence=png("proof.png")))
        comp = Completion.objects.get()
        self.assertEqual(comp.evidence_name, "proof.png")
        r = c.get(reverse("evidence", args=[comp.pk]))
        self.assertEqual(r.status_code, 200); self.assertEqual(r["X-Content-Type-Options"], "nosniff")

    def test_only_admin_can_undo(self):
        j = self.make_job("X", assignee=self.tech)
        comp = Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech)
        self.assertEqual(self.as_user(self.tech).post(reverse("completion_undo", args=[comp.pk])).status_code, 403)
        self.as_user(self.admin).post(reverse("completion_undo", args=[comp.pk]))
        self.assertEqual(Completion.objects.count(), 0)


class MineVersusAllToggle(Base):
    def setUp(self):
        self.mine = self.make_job("MY-JOB-TITLE", assignee=self.tech)
        self.theirs = self.make_job("THEIR-JOB-TITLE", assignee=self.tech2)
        self.free = self.make_job("FREE-JOB-TITLE")

    def test_technician_default_is_mine_on_every_page(self):
        c = self.as_user(self.tech)
        for name in ("today", "calendar", "jobs"):
            r = c.get(reverse(name)); self.assertEqual(r.context["scope"], "mine", name)
            self.assertContains(r, "MY-JOB-TITLE", msg_prefix=name)
            self.assertNotContains(r, "THEIR-JOB-TITLE", msg_prefix=name)
            self.assertNotContains(r, "FREE-JOB-TITLE", msg_prefix=name)

    def test_switch_to_all_shows_overall_jobs(self):
        c = self.as_user(self.tech)
        for name in ("today", "calendar", "jobs"):
            r = c.get(reverse(name), {"scope": "all"})
            for t in ("MY-JOB-TITLE", "THEIR-JOB-TITLE", "FREE-JOB-TITLE"):
                self.assertContains(r, t, msg_prefix=name)

    def test_choice_is_remembered_in_session(self):
        c = self.as_user(self.tech)
        c.get(reverse("jobs"), {"scope": "all"})
        self.assertEqual(c.get(reverse("jobs")).context["scope"], "all")
        self.assertEqual(c.get(reverse("today")).context["scope"], "all")
        c.get(reverse("calendar"), {"scope": "mine"})
        self.assertEqual(c.get(reverse("jobs")).context["scope"], "mine")

    def test_admin_default_is_all_and_can_switch(self):
        c = self.as_user(self.admin)
        r = c.get(reverse("jobs")); self.assertEqual(r.context["scope"], "all"); self.assertContains(r, "THEIR-JOB-TITLE")
        r = c.get(reverse("jobs"), {"scope": "mine"}); self.assertNotContains(r, "THEIR-JOB-TITLE")

    def test_junk_scope_value_is_ignored(self):
        self.assertIn(self.as_user(self.tech).get(reverse("jobs"), {"scope": "<script>"}).context["scope"], ("mine", "all"))

    def test_toggle_switch_is_rendered(self):
        r = self.as_user(self.tech).get(reverse("jobs"))
        self.assertContains(r, 'class="switch"'); self.assertContains(r, "data-autosubmit")

    def test_projects_scope(self):
        p1 = Project.objects.create(name="MINE-PROJ", owner=self.admin)
        Job.objects.create(project=p1, title="task", kind="once", start_date=d(1), assignee=self.tech)
        Project.objects.create(name="OTHER-PROJ", owner=self.admin)
        c = self.as_user(self.tech)
        r = c.get(reverse("projects"))
        self.assertContains(r, "MINE-PROJ"); self.assertNotContains(r, "OTHER-PROJ")
        self.assertContains(c.get(reverse("projects"), {"scope": "all"}), "OTHER-PROJ")


class Projects(Base):
    def setUp(self):
        self.p = Project.objects.create(name="Line 2 upgrade", owner=self.tech, due_date=d(10))

    def add_task(self, user, **kw):
        data = dict(title="Task", equipment="", assignee=self.tech2.pk, weight=1, instructions="", due_date=iso(d(2)))
        data.update(kw)
        return self.as_user(user).post(reverse("task_new", args=[self.p.pk]), data)

    def test_admin_creates_project(self):
        r = self.as_user(self.admin).post(reverse("project_new"), dict(name="New proj", description="d", owner=self.tech.pk, due_date=iso(d(5))))
        self.assertEqual(r.status_code, 302); self.assertTrue(Project.objects.filter(name="New proj").exists())
        self.assertEqual(self.as_user(self.tech).get(reverse("project_new")).status_code, 403)

    def test_owner_and_admin_can_add_tasks_others_cannot(self):
        self.assertEqual(self.add_task(self.admin).status_code, 302)
        self.assertEqual(self.add_task(self.tech).status_code, 302)               # the project owner
        self.assertEqual(self.add_task(self.tech2).status_code, 403)
        self.assertEqual(self.p.tasks.count(), 2)
        t = self.p.tasks.first(); self.assertEqual((t.kind, t.project, t.assignee), ("once", self.p, self.tech2))

    def test_task_needs_due_date_and_valid_weight(self):
        self.add_task(self.admin, due_date=""); self.add_task(self.admin, weight=0); self.add_task(self.admin, weight=11)
        self.assertEqual(self.p.tasks.count(), 0)

    def test_progress_is_judged_by_effort_points(self):
        small = Job.objects.create(project=self.p, title="s", kind="once", start_date=d(1), weight=1, assignee=self.tech2)
        Job.objects.create(project=self.p, title="s2", kind="once", start_date=d(1), weight=1, assignee=self.tech2)
        Job.objects.create(project=self.p, title="big", kind="once", start_date=d(1), weight=8, assignee=self.tech)
        self.assertEqual(project_stats_map([self.p])[self.p.id]["pct"], 0)
        self.as_user(self.tech2).post(reverse("done"), dict(job=small.pk, due_date=iso(d(1))))
        s = project_stats_map([self.p])[self.p.id]
        self.assertEqual((s["done"], s["total"], s["pct"], s["weight_done"], s["weight_total"]), (1, 3, 10, 1, 10))

    def test_project_page_shows_progress_and_charts(self):
        j = Job.objects.create(project=self.p, title="only", kind="once", start_date=d(-1), weight=3, assignee=self.tech2)
        r = self.as_user(self.admin).get(reverse("project_detail", args=[self.p.pk]))
        self.assertEqual(r.context["s"]["overdue"], 1); self.assertContains(r, "<svg"); self.assertContains(r, "<progress")
        self.as_user(self.tech2).post(reverse("done"), dict(job=j.pk, due_date=iso(d(-1))))
        self.assertEqual(self.as_user(self.admin).get(reverse("project_detail", args=[self.p.pk])).context["s"]["pct"], 100)

    def test_editing_due_date_of_a_finished_task_keeps_it_done(self):
        j = Job.objects.create(project=self.p, title="t", kind="once", start_date=d(1), assignee=self.tech)
        self.as_user(self.tech).post(reverse("done"), dict(job=j.pk, due_date=iso(d(1))))
        j.start_date = d(5); j.save()
        self.assertEqual(project_stats_map([self.p])[self.p.id]["pct"], 100)

    def test_closed_projects_leave_the_schedule(self):
        j = Job.objects.create(project=self.p, title="t", kind="once", start_date=d(1), assignee=self.tech)
        self.assertTrue(load_occurrences(d(0), d(3), self.tech))
        self.as_user(self.tech).post(reverse("project_toggle", args=[self.p.pk]))
        self.assertFalse(load_occurrences(d(0), d(3), self.tech))

    def test_tasks_appear_in_jobs_and_today(self):
        Job.objects.create(project=self.p, title="PROJECT-TASK-X", kind="once", start_date=d(0), assignee=self.tech)
        c = self.as_user(self.tech)
        self.assertContains(c.get(reverse("today")), "PROJECT-TASK-X"); self.assertContains(c.get(reverse("jobs"), {"kind": "task"}), "PROJECT-TASK-X")

    def test_deleting_project_removes_tasks_and_files(self):
        j = Job.objects.create(project=self.p, title="t", kind="once", start_date=d(1))
        self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf()]))
        self.as_user(self.admin).post(reverse("project_delete", args=[self.p.pk]))
        self.assertEqual((Project.objects.count(), Job.objects.count(), JobAttachment.objects.count()), (0, 0, 0))


class AttachmentsOnJobs(Base):
    def test_attach_several_file_types_when_creating_a_job(self):
        files = [pdf("manual.pdf"), png("photo.png"), xlsx("data.xlsx")]
        r = self.as_user(self.admin).post(reverse("job_new"), job_post(title="With files", equipment=self.eq.pk, files=files))
        self.assertEqual(r.status_code, 302)
        j = Job.objects.get(title="With files")
        self.assertEqual(sorted(a.original_name for a in j.attachments.all()), ["data.xlsx", "manual.pdf", "photo.png"])

    def test_bad_files_block_the_whole_save(self):
        exe = pdf("evil.exe"); fake = pdf("fake.png")
        for f in (exe, fake):
            r = self.as_user(self.admin).post(reverse("job_new"), job_post(title="Bad", equipment=self.eq.pk, files=[f]))
            self.assertEqual(r.status_code, 200)
        self.assertEqual(Job.objects.count(), 0); self.assertEqual(JobAttachment.objects.count(), 0)

    def test_too_many_files(self):
        r = self.as_user(self.admin).post(reverse("job_new"), job_post(title="Many", equipment=self.eq.pk, files=[pdf(f"{i}.pdf") for i in range(11)]))
        self.assertEqual(r.status_code, 200); self.assertEqual(Job.objects.count(), 0)

    def test_stored_under_random_name_and_original_name_sanitised(self):
        j = self.make_job("J")
        self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf("../../etc/evil.pdf")]))
        a = JobAttachment.objects.get()
        self.assertEqual(a.original_name, "evil.pdf")
        self.assertRegex(a.file.name, r"^attachments/[0-9a-f]{32}\.pdf$")

    def test_download_needs_login_and_is_safe(self):
        j = self.make_job("J")
        self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf("m.pdf"), png("p.png")]))
        pdf_a, png_a = JobAttachment.objects.order_by("id")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("attachment", args=[pdf_a.pk])).status_code, 302)
        r = self.as_user(self.tech).get(reverse("attachment", args=[pdf_a.pk]))                 # every signed-in user can read
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"]); self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", r["Content-Security-Policy"])
        self.assertNotIn("attachment", self.client.get(reverse("attachment", args=[png_a.pk]))["Content-Disposition"])   # images show inline
        self.assertEqual(self.client.get(reverse("attachment", args=[99999])).status_code, 404)

    def test_visible_on_the_job_page_and_row(self):
        j = self.make_job("J"); self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf("checklist.pdf")]))
        self.assertContains(self.client.get(reverse("job_detail", args=[j.pk])), "checklist.pdf")
        self.assertContains(self.client.get(reverse("today")), "📎 1 file")

    def test_technician_cannot_attach_or_remove(self):
        j = self.make_job("J", assignee=self.tech)
        self.assertEqual(self.as_user(self.tech).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf()])).status_code, 403)
        self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf()]))
        a = JobAttachment.objects.get()
        self.assertEqual(self.as_user(self.tech).post(reverse("attachment_delete", args=[a.pk])).status_code, 403)
        self.as_user(self.admin).post(reverse("attachment_delete", args=[a.pk])); self.assertEqual(JobAttachment.objects.count(), 0)

    def test_deleting_a_job_removes_its_files_from_disk(self):
        import os
        j = self.make_job("J"); self.as_user(self.admin).post(reverse("job_upload", args=[j.pk]), dict(files=[pdf()]))
        path = JobAttachment.objects.get().file.path; self.assertTrue(os.path.exists(path))
        self.as_user(self.admin).post(reverse("job_delete", args=[j.pk])); self.assertFalse(os.path.exists(path))


class DownloadJobsDone(Base):
    def setUp(self):
        self.j1 = self.make_job("Old job", assignee=self.tech)
        self.j2 = self.make_job("New job", assignee=self.tech2)
        self.j3 = self.make_job("Formula job", assignee=self.tech)
        from django.utils import timezone
        self.c_old = Completion.objects.create(job=self.j1, due_date=d(0), planned_date=d(0), done_by=self.tech, note="old")
        Completion.objects.filter(pk=self.c_old.pk).update(done_at=timezone.now() - timedelta(days=40))
        Completion.objects.create(job=self.j2, due_date=d(0), planned_date=d(0), done_by=self.tech2, note="new")
        Completion.objects.create(job=self.j3, due_date=d(0), planned_date=d(0), done_by=self.tech, note="=HYPERLINK(\"http://evil\")")

    def params(self, **kw):
        p = dict(date_from=iso(d(-7)), date_to=iso(d(0)), format="csv", scope="all")
        p.update(kw)
        return p

    def csv_rows(self, resp):
        text = b"".join(resp.streaming_content).decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_csv_contains_only_the_chosen_period(self):
        r = self.as_user(self.admin).get(reverse("export"), self.params())
        self.assertEqual(r.status_code, 200); self.assertIn("text/csv", r["Content-Type"])
        self.assertIn(f"jobs_done_{iso(d(-7))}_to_{iso(d(0))}.csv", r["Content-Disposition"])
        rows = self.csv_rows(r); titles = [x[2] for x in rows[1:]]
        self.assertEqual(rows[0][:3], ["Date done", "Time done", "Job"])
        self.assertIn("New job", titles); self.assertNotIn("Old job", titles)

    def test_wider_period_includes_older_work(self):
        titles = [x[2] for x in self.csv_rows(self.as_user(self.admin).get(reverse("export"), self.params(date_from=iso(d(-60)))))[1:]]
        self.assertIn("Old job", titles)

    def test_excel_download(self):
        from openpyxl import load_workbook
        r = self.as_user(self.admin).get(reverse("export"), self.params(format="xlsx"))
        self.assertEqual(r.status_code, 200); self.assertIn("spreadsheetml", r["Content-Type"])
        rows = list(load_workbook(io.BytesIO(r.content)).active.values)
        self.assertEqual(rows[0][2], "Job"); self.assertIn("New job", [x[2] for x in rows[1:]])

    def test_spreadsheet_formula_injection_is_neutralised(self):
        rows = self.csv_rows(self.as_user(self.admin).get(reverse("export"), self.params()))
        note = [x[12] for x in rows[1:] if x[2] == "Formula job"][0]
        self.assertTrue(note.startswith("'="))

    def test_mine_scope_and_person_filter(self):
        titles = lambda resp: [x[2] for x in self.csv_rows(resp)[1:]]
        mine = titles(self.as_user(self.tech).get(reverse("export"), self.params(scope="mine")))
        self.assertIn("Formula job", mine); self.assertNotIn("New job", mine)
        one = titles(self.as_user(self.admin).get(reverse("export"), self.params(person=self.tech2.pk)))
        self.assertEqual(one, ["New job"])

    def test_person_filter_is_ignored_for_technicians(self):
        titles = [x[2] for x in self.csv_rows(self.as_user(self.tech).get(reverse("export"), self.params(person=self.tech2.pk)))[1:]]
        self.assertIn("Formula job", titles); self.assertIn("New job", titles)

    def test_bad_dates_redirect_back_with_message(self):
        c = self.as_user(self.admin)
        self.assertEqual(c.get(reverse("export"), self.params(date_from=iso(d(0)), date_to=iso(d(-5)))).status_code, 302)
        self.assertEqual(c.get(reverse("export"), self.params(date_from="garbage")).status_code, 302)
        self.assertEqual(c.get(reverse("export"), self.params(date_from=iso(d(-2000)))).status_code, 302)
        self.assertEqual(c.get(reverse("export"), self.params(format="exe")).status_code, 302)

    def test_export_requires_login(self):
        self.client.logout(); self.assertEqual(self.client.get(reverse("export"), self.params()).status_code, 302)

    def test_history_page_has_the_download_form(self):
        self.assertContains(self.as_user(self.admin).get(reverse("history")), reverse("export"))


class ChartsAndPages(Base):
    def test_every_page_renders_for_admin_and_technician(self):
        j = self.make_job("R", "repeat", start=-3, assignee=self.tech)
        p = Project.objects.create(name="P", owner=self.admin); Job.objects.create(project=p, title="T", kind="once", start_date=d(1), assignee=self.tech)
        Completion.objects.create(job=j, due_date=d(-3), planned_date=d(-3), done_by=self.tech)
        urls = [reverse("today"), reverse("calendar"), reverse("calendar") + "?m=2027-02", reverse("calendar") + "?m=garbage", reverse("jobs"),
                reverse("job_detail", args=[j.pk]), reverse("projects"), reverse("project_detail", args=[p.pk]), reverse("equipment"),
                reverse("equipment_detail", args=[self.eq.pk]), reverse("history"), reverse("insights"), reverse("password_change")]
        for u in (self.admin, self.tech):
            c = self.as_user(u)
            for url in urls:
                self.assertEqual(c.get(url).status_code, 200, f"{u.username} {url}")
        admin_only = [reverse("setup"), reverse("job_new"), reverse("job_edit", args=[j.pk]), reverse("project_new"), reverse("project_edit", args=[p.pk])]
        for url in admin_only:
            self.assertEqual(self.as_user(self.admin).get(url).status_code, 200, url)

    def test_today_and_insights_include_svg_charts(self):
        j = self.make_job("R", assignee=self.tech)
        Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech)
        c = self.as_user(self.admin)
        self.assertGreaterEqual(c.get(reverse("today")).content.decode().count("<svg"), 2)
        self.assertGreaterEqual(c.get(reverse("insights")).content.decode().count("<svg"), 4)

    def test_weekly_chart_counts_completions(self):
        j = self.make_job("R", assignee=self.tech)
        for i in range(3):
            Completion.objects.create(job=j, due_date=d(-i), planned_date=d(-i), done_by=self.tech)
        self.assertContains(self.as_user(self.admin).get(reverse("insights")), 'aria-label="Jobs done per week"')

    def test_equipment_health(self):
        self.make_job("Late", start=-2)
        r = self.as_user(self.admin).get(reverse("equipment")); self.assertContains(r, "1 overdue")
