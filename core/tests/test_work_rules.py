"""Finishing rules, mandatory file, checkpoints, who/when assigned, pagination, hide-menu.   python manage.py test core"""
import calendar, csv, io, re
from datetime import date, timedelta

from django.contrib.messages import get_messages
from django.urls import reverse

from core import recurrence as rec
from core.models import Area, Completion, Equipment, Group, Job, MoveException, Project
from .helpers import Base, PW, User, d, iso, png, today
from .test_web import job_post


def post_done(c, job, due, **kw):
    return c.post(reverse("done"), dict(job=job.pk, due_date=iso(due), **kw))


def flashes(response_or_request):
    return [str(m) for m in get_messages(response_or_request.wsgi_request)]


def end_of_this_week():
    return today() + (timedelta(days=6 - today().weekday()))


def end_of_this_month():
    t = today()
    return t.replace(day=calendar.monthrange(t.year, t.month)[1])


class RepeatingJobsCannotBeFinishedInAdvance(Base):
    def test_daily_job_only_on_its_own_day(self):
        j = self.make_job("Daily", "repeat", start=0, every=1, unit="day", assignee=self.tech)
        c = self.as_user(self.tech)
        r = post_done(c, j, d(1))                                                    # tomorrow's one, today
        self.assertEqual(Completion.objects.count(), 0)
        self.assertTrue(any("in advance" in m for m in flashes(r)), flashes(r))
        self.assertEqual(post_done(c, j, d(0)).status_code, 302)
        self.assertEqual(Completion.objects.count(), 1)

    def test_weekly_job_any_day_of_its_own_week_but_not_next_week(self):
        sunday = end_of_this_week()
        j = self.make_job("Weekly", "repeat", assignee=self.tech)
        Job.objects.filter(pk=j.pk).update(start_date=sunday, every=1, unit="week")
        c = self.as_user(self.tech)
        post_done(c, j, sunday + (timedelta(days=7)))                              # next week's: refused
        self.assertEqual(Completion.objects.count(), 0)
        post_done(c, j, sunday)                                                      # this week's, even if due later this week: allowed
        self.assertEqual(Completion.objects.get().due_date, sunday)

    def test_monthly_job_any_day_of_its_own_month_but_not_next_month(self):
        last = end_of_this_month()
        j = self.make_job("Monthly", "repeat", assignee=self.tech)
        Job.objects.filter(pk=j.pk).update(start_date=last, every=1, unit="month")
        c = self.as_user(self.tech)
        nxt = next(rec.occurrences(last, 1, "month", last + timedelta(days=1)))
        post_done(c, j, nxt)
        self.assertEqual(Completion.objects.count(), 0)
        post_done(c, j, last)
        self.assertEqual(Completion.objects.count(), 1)

    def test_yearly_job_this_year_only(self):
        t = today()
        j = self.make_job("Yearly", "repeat", assignee=self.tech)
        Job.objects.filter(pk=j.pk).update(start_date=date(t.year, 12, 31), every=1, unit="year")
        c = self.as_user(self.tech)
        post_done(c, j, date(t.year + 1, 12, 31))
        self.assertEqual(Completion.objects.count(), 0)
        post_done(c, j, date(t.year, 12, 31))
        self.assertEqual(Completion.objects.count(), 1)

    def test_every_12_months_counts_as_yearly(self):
        t = today()
        j = self.make_job("Annual", "repeat", assignee=self.tech)
        Job.objects.filter(pk=j.pk).update(start_date=date(t.year, 12, 31), every=12, unit="month")
        post_done(self.as_user(self.tech), j, date(t.year, 12, 31))
        self.assertEqual(Completion.objects.count(), 1)

    def test_one_off_jobs_and_project_tasks_may_still_be_finished_early(self):
        j = self.make_job("Later", "once", start=10, assignee=self.tech)
        post_done(self.as_user(self.tech), j, d(10))
        self.assertEqual(Completion.objects.count(), 1)

    def test_a_date_moved_to_today_can_be_done_today_and_one_moved_ahead_cannot(self):
        j = self.make_job("Moved", "repeat", start=-7, every=1, unit="day", assignee=self.tech)
        MoveException.objects.create(job=j, orig_date=d(-7), new_date=d(0))
        MoveException.objects.create(job=j, orig_date=d(-6), new_date=d(3))
        c = self.as_user(self.tech)
        post_done(c, j, d(-6)); self.assertEqual(Completion.objects.count(), 0)
        post_done(c, j, d(-7)); self.assertEqual(Completion.objects.count(), 1)

    def test_missed_ones_are_closed_for_technicians_but_an_admin_can_record_them_late(self):
        j = self.make_job("Missed", "repeat", start=-3, every=1, unit="day", assignee=self.tech)
        r = post_done(self.as_user(self.tech), j, d(-3))
        self.assertEqual(Completion.objects.count(), 0)
        self.assertTrue(any("late entry" in m for m in flashes(r)), flashes(r))
        a = self.as_user(self.admin)
        post_done(a, j, d(-3)); self.assertEqual(Completion.objects.count(), 0)         # an admin must say why
        post_done(a, j, d(-3), note="Forgot to log it"); comp = Completion.objects.get()
        self.assertEqual((comp.due_date, comp.done_by, comp.note), (d(-3), self.admin, "Forgot to log it"))
        self.assertFalse(comp.on_time)

    def test_the_rule_cannot_be_bypassed_by_posting_other_fields(self):
        j = self.make_job("Daily", "repeat", start=2, every=1, unit="day", assignee=self.tech)
        post_done(self.as_user(self.tech), j, d(2), planned_date=iso(d(2)), done_at="2000-01-01", state="ok")
        self.assertEqual(Completion.objects.count(), 0)

    def test_nobody_learns_anything_from_a_job_they_may_not_do(self):
        j = self.make_job("Theirs", "repeat", start=5, every=1, unit="day", assignee=self.tech2)
        self.assertEqual(post_done(self.as_user(self.tech), j, d(5)).status_code, 403)

    def test_screens_say_when_it_opens_or_that_it_was_missed(self):
        soon = self.make_job("SOON-JOB", "repeat", start=2, every=1, unit="day", assignee=self.tech)
        old = self.make_job("MISSED-JOB", "repeat", start=-2, every=1, unit="day", assignee=self.tech, end_date=d(-1))      # only past dates
        button = lambda j: f'data-open="dlg-done" data-job="{j.pk}"'
        html = self.as_user(self.tech).get(reverse("today")).content.decode()
        self.assertIn("Opens", html); self.assertIn("Missed", html)
        self.assertNotIn(button(soon), html); self.assertNotIn(button(old), html)
        html = self.as_user(self.admin).get(reverse("today"), {"scope": "all"}).content.decode()
        self.assertIn("Record late", html); self.assertIn(button(old), html); self.assertNotIn(button(soon), html)

    def test_job_page_explains_the_rule(self):
        j = self.make_job("W", "repeat", assignee=self.tech)
        self.assertContains(self.as_user(self.admin).get(reverse("job_detail", args=[j.pk])), "that week (Mon-Sun)")

    def test_form_offers_yearly_and_saves_the_year_unit(self):
        c = self.as_user(self.admin)
        r = c.post(reverse("job_new"), job_post(title="Fire drill", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="year"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Job.objects.get(title="Fire drill").unit, "year")
        self.assertContains(c.get(reverse("job_new")), 'data-preset="1:year"')


class MandatoryFile(Base):
    def make(self, required):
        return self.make_job("Photo job", "once", assignee=self.tech, evidence_required=required)

    def test_admin_can_switch_it_on_when_creating_the_job(self):
        c = self.as_user(self.admin)
        self.assertContains(c.get(reverse("job_new")), 'name="evidence_required"')
        c.post(reverse("job_new"), job_post(title="Need photo", equipment=self.eq.pk, evidence_required="on"))
        c.post(reverse("job_new"), job_post(title="No photo", equipment=self.eq.pk))
        self.assertTrue(Job.objects.get(title="Need photo").evidence_required)
        self.assertFalse(Job.objects.get(title="No photo").evidence_required)

    def test_cannot_finish_without_the_file_when_required(self):
        j = self.make(True)
        r = post_done(self.as_user(self.tech), j, d(0), note="done")
        self.assertEqual(Completion.objects.count(), 0)
        self.assertTrue(any("file is required" in m for m in flashes(r)), flashes(r))

    def test_finishes_with_the_file_and_remembers_it_was_required(self):
        j = self.make(True)
        post_done(self.as_user(self.tech), j, d(0), evidence=png("proof.png"))
        c = Completion.objects.get()
        self.assertEqual(c.evidence_name, "proof.png"); self.assertTrue(c.evidence_was_required)

    def test_not_required_means_optional(self):
        j = self.make(False)
        post_done(self.as_user(self.tech), j, d(0))
        self.assertEqual(Completion.objects.count(), 1)

    def test_a_bad_file_is_still_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        j = self.make(True)
        post_done(self.as_user(self.tech), j, d(0), evidence=SimpleUploadedFile("evil.exe", b"MZ"))
        self.assertEqual(Completion.objects.count(), 0)

    def test_admins_are_held_to_it_too(self):
        j = self.make(True)
        post_done(self.as_user(self.admin), j, d(0))
        self.assertEqual(Completion.objects.count(), 0)

    def test_button_tells_the_dialog_to_demand_a_file(self):
        j = self.make(True)
        html = self.as_user(self.tech).get(reverse("today")).content.decode()
        self.assertIn('data-evidence_required="1"', html); self.assertIn("file required", html)

    def test_job_page_shows_it(self):
        self.assertContains(self.as_user(self.admin).get(reverse("job_detail", args=[self.make(True).pk])), "Required")


class Checkpoints(Base):
    def setUp(self):
        self.j = self.make_job("Inspect", "once", assignee=self.tech, checkpoints=["Oil level OK", "No leaks", "Guard fitted"])

    def test_admin_enters_one_per_line(self):
        c = self.as_user(self.admin)
        c.post(reverse("job_new"), job_post(title="Lube", equipment=self.eq.pk, checkpoints="- Grease points\n\n• Wipe excess\nGrease points"))
        self.assertEqual(Job.objects.get(title="Lube").checkpoints, ["Grease points", "Wipe excess"])

    def test_edit_form_shows_them_one_per_line_and_they_can_be_cleared(self):
        c = self.as_user(self.admin)
        self.assertContains(c.get(reverse("job_edit", args=[self.j.pk])), "Oil level OK\nNo leaks\nGuard fitted")
        c.post(reverse("job_edit", args=[self.j.pk]), job_post(title="Inspect", equipment=self.eq.pk, due_date=iso(d(0)), checkpoints=""))
        self.j.refresh_from_db(); self.assertEqual(self.j.checkpoints, [])

    def test_too_many_or_too_long_is_refused(self):
        c = self.as_user(self.admin)
        r = c.post(reverse("job_new"), job_post(title="Big", equipment=self.eq.pk, checkpoints="\n".join(f"step {i}" for i in range(31))))
        self.assertEqual(r.status_code, 200); self.assertFalse(Job.objects.filter(title="Big").exists())
        r = c.post(reverse("job_new"), job_post(title="Long", equipment=self.eq.pk, checkpoints="x" * 201))
        self.assertEqual(r.status_code, 200); self.assertFalse(Job.objects.filter(title="Long").exists())

    def test_every_checkpoint_must_be_ticked(self):
        c = self.as_user(self.tech)
        for ticks in ([], ["0"], ["0", "1"], ["0", "1", "7"], ["0", "1", "x"], ["0", "0", "0"]):
            r = post_done(c, self.j, d(0), checks=ticks)
            self.assertEqual(Completion.objects.count(), 0, ticks)
        self.assertTrue(any("Tick every checkpoint" in m for m in flashes(r)), flashes(r))
        post_done(c, self.j, d(0), checks=["0", "1", "2"])
        self.assertEqual(Completion.objects.count(), 1)

    def test_what_was_checked_is_kept_in_the_history_even_if_the_job_changes_later(self):
        post_done(self.as_user(self.tech), self.j, d(0), checks=["0", "1", "2"])
        comp = Completion.objects.get()
        self.assertEqual([k["text"] for k in comp.checkpoints], ["Oil level OK", "No leaks", "Guard fitted"])
        self.assertTrue(all(k["ok"] for k in comp.checkpoints)); self.assertEqual(comp.checks_ok, 3)
        Job.objects.filter(pk=self.j.pk).update(checkpoints=["Something new"])
        comp.refresh_from_db(); self.assertEqual(len(comp.checkpoints), 3)
        for url in (reverse("history"), reverse("job_detail", args=[self.j.pk]), reverse("equipment_detail", args=[self.eq.pk])):
            html = self.as_user(self.admin).get(url).content.decode()
            self.assertIn("Oil level OK", html, url); self.assertIn("3/3 checked", html, url)

    def test_a_job_without_checkpoints_is_unchanged(self):
        j = self.make_job("Plain", "once", assignee=self.tech)
        post_done(self.as_user(self.tech), j, d(0))
        self.assertEqual(Completion.objects.get().checkpoints, [])

    def test_button_carries_the_list_for_the_dialog(self):
        html = self.as_user(self.tech).get(reverse("today")).content.decode()
        self.assertIn("data-checkpoints=", html)
        self.assertIn("Oil level OK", html); self.assertIn("&quot;No leaks&quot;", html)             # escaped inside the attribute

    def test_project_tasks_can_have_checkpoints_too(self):
        p = Project.objects.create(name="P", owner=self.admin)
        r = self.as_user(self.admin).post(reverse("task_new", args=[p.pk]), dict(title="T", due_date=iso(d(3)), weight=1, instructions="", checkpoints="Step 1\nStep 2"))
        self.assertEqual(r.status_code, 302); self.assertEqual(Job.objects.get(title="T").checkpoints, ["Step 1", "Step 2"])

    def test_html_in_checkpoints_is_shown_as_text(self):
        evil = '<script>alert(1)</script>'
        j = self.make_job("Evil", "once", assignee=self.tech, checkpoints=[evil])
        post_done(self.as_user(self.tech), j, d(0), checks=["0"])
        for url in (reverse("today"), reverse("history"), reverse("job_detail", args=[j.pk])):
            self.assertNotIn(evil, self.as_user(self.admin).get(url, {"scope": "all"}).content.decode(), url)


class WhoAssignedAndWhen(Base):
    def new(self, **kw):
        self.as_user(self.admin).post(reverse("job_new"), job_post(title="Given", equipment=self.eq.pk, **kw))
        return Job.objects.get(title="Given")

    def test_creating_an_assigned_job_records_who_and_when(self):
        j = self.new(assignee=self.tech.pk)
        self.assertEqual(j.assigned_by, self.admin); self.assertIsNotNone(j.assigned_at)

    def test_unassigned_job_has_no_assignment(self):
        j = self.new()
        self.assertIsNone(j.assigned_by); self.assertIsNone(j.assigned_at); self.assertIsNone(j.assignment_by)

    def test_group_assignment_counts_too(self):
        g = Group.objects.create(name="Crew"); g.members.add(self.tech)
        j = self.new(assigned_group=g.pk)
        self.assertEqual((j.assigned_by, j.assigned_group), (self.admin, g))

    def test_editing_something_else_keeps_the_original_date(self):
        j = self.new(assignee=self.tech.pk)
        before = j.assigned_at
        self.as_user(self.admin).post(reverse("job_edit", args=[j.pk]), job_post(title="Renamed", equipment=self.eq.pk, assignee=self.tech.pk))
        j.refresh_from_db(); self.assertEqual((j.title, j.assigned_at), ("Renamed", before))

    def test_reassigning_updates_it_and_unassigning_clears_it(self):
        j = self.new(assignee=self.tech.pk)
        Job.objects.filter(pk=j.pk).update(assigned_by=self.tech, assigned_at=j.assigned_at.replace(year=2020))
        self.as_user(self.admin).post(reverse("job_edit", args=[j.pk]), job_post(title="Given", equipment=self.eq.pk, assignee=self.tech2.pk))
        j.refresh_from_db(); self.assertEqual((j.assignee, j.assigned_by), (self.tech2, self.admin)); self.assertGreater(j.assigned_at.year, 2020)
        self.as_user(self.admin).post(reverse("job_edit", args=[j.pk]), job_post(title="Given", equipment=self.eq.pk))
        j.refresh_from_db(); self.assertEqual((j.assignee, j.assigned_by, j.assigned_at), (None, None, None))

    def test_completion_keeps_a_copy_so_history_stays_true_after_reassigning(self):
        j = self.new(assignee=self.tech.pk)
        post_done(self.as_user(self.tech), j, d(0))
        comp = Completion.objects.get()
        self.assertEqual((comp.assigned_to, comp.assigned_by, comp.assignment_recorded), ("tina", self.admin, True))
        self.assertIsNotNone(comp.assigned_at)
        self.as_user(self.admin).post(reverse("job_edit", args=[j.pk]), job_post(title="Given", equipment=self.eq.pk, assignee=self.tech2.pk))
        comp.refresh_from_db(); self.assertEqual(comp.shown_assigned_to, "tina")
        self.assertEqual(comp.shown_assigned_by, self.admin)

    def test_history_page_shows_assigned_to_by_and_when(self):
        j = self.new(assignee=self.tech.pk)
        post_done(self.as_user(self.tech), j, d(0))
        html = self.as_user(self.admin).get(reverse("history")).content.decode()
        self.assertIn("<th>Assigned</th>", html); self.assertIn("<th>Checkpoints</th>", html)
        self.assertRegex(html, r"tina</b>\s*<div class=\"meta\">by boss</div>\s*<div class=\"meta\">on ")

    def test_older_completions_without_a_copy_fall_back_to_the_job(self):
        j = self.make_job("Old", "once", assignee=self.tech)
        Job.objects.filter(pk=j.pk).update(created_by=self.admin)
        comp = Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech)
        j.refresh_from_db()
        self.assertEqual((comp.shown_assigned_to, comp.shown_assigned_by), ("tina", self.admin))
        self.assertEqual(self.as_user(self.admin).get(reverse("history")).status_code, 200)

    def test_job_page_and_job_list_show_who_assigned(self):
        j = self.new(assignee=self.tech.pk)
        c = self.as_user(self.admin)
        self.assertContains(c.get(reverse("job_detail", args=[j.pk])), "Assigned by")
        self.assertContains(c.get(reverse("jobs")), "by boss")

    def test_download_has_the_new_columns(self):
        j = self.make_job("Dl", "once", assignee=self.tech, checkpoints=["Alpha", "Beta"])
        Job.objects.filter(pk=j.pk).update(assigned_by=self.admin, assigned_at=j.created_at)
        post_done(self.as_user(self.tech), j, d(0), checks=["0", "1"])
        r = self.as_user(self.admin).get(reverse("export"), dict(date_from=iso(d(-1)), date_to=iso(d(1)), format="csv", scope="all"))
        rows = list(csv.reader(io.StringIO(b"".join(r.streaming_content).decode("utf-8-sig"))))
        h = rows[0]
        for col in ("Assigned by", "Assigned on", "Checkpoints", "File required"):
            self.assertIn(col, h)
        row = dict(zip(h, rows[1]))
        self.assertEqual(row["Assigned by"], "boss"); self.assertEqual(row["Checkpoints"], "\u2713 Alpha | \u2713 Beta")
        self.assertEqual(row["Assigned to"], "tina")


class HistoryFilters(Base):
    def test_late_only(self):
        j1, j2 = self.make_job("ON-TIME-JOB", "once", assignee=self.tech), self.make_job("LATE-JOB", "once", assignee=self.tech)
        Completion.objects.create(job=j1, due_date=d(0), planned_date=d(0), done_by=self.tech)
        Completion.objects.create(job=j2, due_date=d(-5), planned_date=d(-5), done_by=self.tech)
        c = self.as_user(self.admin)
        both = c.get(reverse("history")).content.decode()
        self.assertIn("ON-TIME-JOB", both); self.assertIn("LATE-JOB", both)
        late = c.get(reverse("history"), {"late": "1"}).content.decode()
        self.assertIn("LATE-JOB", late); self.assertNotIn("ON-TIME-JOB", late)

    def test_search_by_person(self):
        j = self.make_job("J", "once")
        Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech)
        self.assertEqual(self.as_user(self.admin).get(reverse("history"), {"q": "tina"}).context["page"].paginator.count, 1)
        self.assertEqual(self.as_user(self.admin).get(reverse("history"), {"q": "tom"}).context["page"].paginator.count, 0)


class Pagination(Base):
    def many_jobs(self, n):
        Job.objects.bulk_create([Job(equipment=self.eq, title=f"Job {i:03d}", kind="once", start_date=d(0), created_by=self.admin) for i in range(n)])

    def test_jobs_list_pages_and_page_size(self):
        self.many_jobs(60)
        c = self.as_user(self.admin)
        r = c.get(reverse("jobs"))
        self.assertEqual((len(r.context["page"]), r.context["page"].paginator.num_pages), (25, 3))
        self.assertContains(r, "60 in total")
        self.assertEqual(len(c.get(reverse("jobs"), {"per": 10}).context["page"]), 10)
        self.assertEqual(c.get(reverse("jobs"), {"per": 10}).context["page"].paginator.num_pages, 6)
        self.assertEqual(len(c.get(reverse("jobs"), {"per": 100}).context["page"]), 60)
        for junk in ("7", "-5", "abc", "0", "<script>", "9999999999999999999"):               # only 10/25/50/100 are accepted
            self.assertEqual(c.get(reverse("jobs"), {"per": junk}).context["page"].paginator.per_page, 25, junk)

    def test_bad_page_numbers_are_harmless(self):
        self.many_jobs(60)
        c = self.as_user(self.admin)
        self.assertEqual(c.get(reverse("jobs"), {"page": "abc"}).context["page"].number, 1)
        self.assertEqual(c.get(reverse("jobs"), {"page": 999}).context["page"].number, 3)
        self.assertEqual(c.get(reverse("jobs"), {"page": -4}).status_code, 200)

    def test_page_links_keep_search_and_size(self):
        self.many_jobs(60)
        html = self.as_user(self.admin).get(reverse("jobs"), {"q": "Job", "per": 10}).content.decode()
        self.assertRegex(html, r'href="\?[^"]*page=2[^"]*"'); self.assertIn("per=10", html); self.assertIn("q=Job", html)
        self.assertRegex(html, r'href="\?[^"]*per=50')

    def test_page_size_choice_shown_whenever_there_is_something_to_page(self):
        self.many_jobs(11)
        self.assertContains(self.as_user(self.admin).get(reverse("jobs")), "per=100")
        Job.objects.all().delete(); self.many_jobs(5)
        self.assertNotContains(self.as_user(self.admin).get(reverse("jobs")), "per=100")

    def test_today_is_paged_and_counts_stay_whole(self):
        for i in range(30):
            self.make_job(f"Daily {i:02d}", "repeat", start=0, every=1, unit="day")
        r = self.as_user(self.admin).get(reverse("today"))
        self.assertEqual(r.context["counts"]["today"], 30); self.assertEqual(len(r.context["page"]), 25)
        self.assertEqual(r.context["page"].paginator.count, 30 * 8)                           # today + the next 7 days, every job
        self.assertEqual(len(r.context["groups"]["today"]), 25)
        r2 = self.as_user(self.admin).get(reverse("today"), {"page": 2})
        self.assertEqual(len(r2.context["groups"]["today"]), 5)
        self.assertContains(r2, "240 in total")

    def test_history_is_paged(self):
        j = self.make_job("Daily", "repeat", start=-40, every=1, unit="day")
        Completion.objects.bulk_create([Completion(job=j, due_date=d(-i), planned_date=d(-i), done_by=self.tech) for i in range(40)])
        c = self.as_user(self.admin)
        self.assertEqual(len(c.get(reverse("history")).context["page"]), 25)
        self.assertEqual(len(c.get(reverse("history"), {"page": 2}).context["page"]), 15)

    def test_job_page_history_has_its_own_page_number(self):
        j = self.make_job("Daily", "repeat", start=-40, every=1, unit="day")
        Completion.objects.bulk_create([Completion(job=j, due_date=d(-i), planned_date=d(-i), done_by=self.tech) for i in range(25)])
        c = self.as_user(self.admin)
        r = c.get(reverse("job_detail", args=[j.pk]))
        self.assertEqual((len(r.context["history"]), r.context["history"].paginator.count), (10, 25))
        self.assertEqual(len(c.get(reverse("job_detail", args=[j.pk]), {"hp": 3}).context["history"]), 5)
        self.assertEqual(len(c.get(reverse("job_detail", args=[j.pk]), {"hp": 2, "hper": 50}).context["history"]), 25)
        self.assertRegex(r.content.decode(), r'href="\?[^"]*hp=2')

    def test_equipment_list_and_search(self):
        Equipment.objects.bulk_create([Equipment(code=f"X{i:03d}", name=f"Machine {i:03d}", area=self.area) for i in range(30)])
        c = self.as_user(self.admin)
        self.assertEqual(len(c.get(reverse("equipment")).context["rows"]), 25)
        self.assertEqual(len(c.get(reverse("equipment"), {"page": 2}).context["rows"]), 6)             # 31 incl. the one from setUp
        self.assertEqual([e.code for e in c.get(reverse("equipment"), {"q": "X017"}).context["rows"]], ["X017"])
        self.assertContains(c.get(reverse("equipment"), {"q": "zzz"}), "Nothing matches")

    def test_equipment_page_lists_have_independent_pages(self):
        Job.objects.bulk_create([Job(equipment=self.eq, title=f"J{i:02d}", kind="once", start_date=d(0)) for i in range(15)])
        c = self.as_user(self.admin)
        r = c.get(reverse("equipment_detail", args=[self.eq.pk]), {"jp": 2})
        self.assertEqual((r.context["jobs"].number, len(r.context["jobs"])), (2, 5))
        self.assertEqual(r.context["history"].number, 1)

    def test_projects_and_tasks_are_paged(self):
        Project.objects.bulk_create([Project(name=f"Proj {i:02d}", owner=self.admin) for i in range(15)])
        c = self.as_user(self.admin)
        self.assertEqual(len(c.get(reverse("projects")).context["cards"]), 12)
        self.assertEqual(len(c.get(reverse("projects"), {"page": 2}).context["cards"]), 3)
        self.assertEqual(len(c.get(reverse("projects"), {"q": "Proj 07"}).context["cards"]), 1)
        p = Project.objects.first()
        Job.objects.bulk_create([Job(project=p, title=f"T{i:02d}", kind="once", start_date=d(i), created_by=self.admin) for i in range(30)])
        r = c.get(reverse("project_detail", args=[p.pk]))
        self.assertEqual((len(r.context["rows"]), r.context["s"]["total"]), (25, 30))               # progress counts every task


class HideTheMenu(Base):
    def test_menu_is_open_by_default_and_has_both_buttons(self):
        html = self.as_user(self.tech).get(reverse("today")).content.decode()
        self.assertNotIn("side-hidden", html)
        self.assertIn("Hide menu", html); self.assertIn("Show the menu", html)
        self.assertEqual(html.count("data-side-toggle"), 2)
        self.assertIn('aria-controls="sidebar"', html); self.assertIn('id="sidebar"', html)

    def test_cookie_makes_the_server_draw_it_hidden_without_flicker(self):
        c = self.as_user(self.tech)
        c.cookies["sidebar"] = "hidden"
        html = c.get(reverse("today")).content.decode()
        self.assertRegex(html, r'<body class="side-hidden">'); self.assertIn('aria-expanded="false"', html)
        c.cookies["sidebar"] = "shown"
        self.assertNotIn("side-hidden", c.get(reverse("today")).content.decode())

    def test_junk_cookie_is_ignored(self):
        c = self.as_user(self.tech)
        c.cookies["sidebar"] = '"><script>alert(1)</script>'
        html = c.get(reverse("today")).content.decode()
        self.assertNotIn("side-hidden", html); self.assertNotIn("<script>alert(1)", html)

    def test_login_page_still_works(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
