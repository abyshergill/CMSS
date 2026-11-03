"""Groups, group assignment, admin 'assigned by me' view and the One time / Repeats form.  python manage.py test core"""
import csv, io, re

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse

from core.models import Completion, Group, Job, Project
from core.services import load_occurrences
from .helpers import Base, PW, d, iso
from .test_web import job_post

User = get_user_model()


def tag(html, name, value):
    """The <input ...> tag with this name and value (attribute order does not matter)."""
    for t in re.findall(r"<input\b[^>]*>", html):
        if f'name="{name}"' in t and f'value="{value}"' in t:
            return t
    return None


class GroupBase(Base):
    def setUp(self):
        self.team = Group.objects.create(name="Night shift")
        self.team.members.add(self.tech, self.tech2)
        self.outsider = User.objects.create_user("olga", password=PW)


class ManagingGroups(GroupBase):
    def test_admin_creates_group_then_lands_on_its_page(self):
        r = self.as_user(self.admin).post(reverse("group_add"), dict(name="  Day   crew "))
        g = Group.objects.get(name="Day crew")                                  # spaces tidied
        self.assertRedirects(r, reverse("group_detail", args=[g.pk]))

    def test_add_several_people_at_once_and_ignore_junk(self):
        self.as_user(self.admin).post(reverse("group_members_add", args=[self.team.pk]), {"users": [self.outsider.pk, "abc", "99999", ""]})
        self.assertEqual(set(self.team.members.all()), {self.tech, self.tech2, self.outsider})

    def test_inactive_people_cannot_be_added(self):
        User.objects.filter(pk=self.outsider.pk).update(is_active=False)
        self.as_user(self.admin).post(reverse("group_members_add", args=[self.team.pk]), {"users": [self.outsider.pk]})
        self.assertNotIn(self.outsider, self.team.members.all())

    def test_remove_one_member(self):
        self.as_user(self.admin).post(reverse("group_member_remove", args=[self.team.pk, self.tech.pk]))
        self.assertEqual(list(self.team.members.all()), [self.tech2])

    def test_duplicate_names_refused_ignoring_case(self):
        self.as_user(self.admin).post(reverse("group_add"), dict(name="NIGHT SHIFT"))
        self.assertEqual(Group.objects.count(), 1)

    def test_rename_never_touches_members(self):
        self.as_user(self.admin).post(reverse("group_edit", args=[self.team.pk]), dict(name="Nights"))
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Nights"); self.assertEqual(self.team.members.count(), 2)

    def test_deleting_a_group_keeps_its_jobs(self):
        j = self.make_job("Keep me", assigned_group=self.team)
        self.as_user(self.admin).post(reverse("group_delete", args=[self.team.pk]))
        j.refresh_from_db()
        self.assertIsNone(j.assigned_group); self.assertEqual(Group.objects.count(), 0)

    def test_technicians_cannot_manage_groups(self):
        c = self.as_user(self.tech)
        self.assertEqual(c.post(reverse("group_add"), dict(name="Hack")).status_code, 403)
        self.assertEqual(c.post(reverse("group_edit", args=[self.team.pk]), dict(name="X")).status_code, 403)
        self.assertEqual(c.post(reverse("group_members_add", args=[self.team.pk]), {"users": [self.tech.pk]}).status_code, 403)
        self.assertEqual(c.post(reverse("group_member_remove", args=[self.team.pk, self.tech2.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("group_delete", args=[self.team.pk])).status_code, 403)
        self.assertEqual(c.get(reverse("group_detail", args=[self.team.pk])).status_code, 403)
        self.assertEqual(Group.objects.get().name, "Night shift"); self.assertEqual(self.team.members.count(), 2)

    def test_groups_tab_lists_groups_and_page_shows_members(self):
        r = self.as_user(self.admin).get(reverse("setup"), {"tab": "groups"})
        self.assertContains(r, "Night shift"); self.assertContains(r, reverse("group_detail", args=[self.team.pk]))
        page = self.client.get(reverse("group_detail", args=[self.team.pk]))
        self.assertContains(page, "tina"); self.assertContains(page, "tom"); self.assertContains(page, reverse("group_members_add", args=[self.team.pk]))


class AssigningToAGroup(GroupBase):
    def test_job_form_offers_person_and_group(self):
        html = self.as_user(self.admin).get(reverse("job_new")).content.decode()
        self.assertIn('name="assigned_group"', html); self.assertIn("Night shift", html); self.assertIn('name="assignee"', html)

    def test_create_job_for_a_group(self):
        r = self.as_user(self.admin).post(reverse("job_new"), job_post(title="Night check", equipment=self.eq.pk, assigned_group=self.team.pk))
        self.assertEqual(r.status_code, 302)
        j = Job.objects.get(title="Night check"); self.assertEqual((j.assigned_group, j.assignee), (self.team, None))

    def test_person_and_group_together_is_refused(self):
        r = self.as_user(self.admin).post(reverse("job_new"), job_post(title="Both", equipment=self.eq.pk, assigned_group=self.team.pk, assignee=self.tech.pk))
        self.assertEqual(r.status_code, 200); self.assertEqual(Job.objects.count(), 0)

    def test_database_also_refuses_both(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Job.objects.create(equipment=self.eq, title="x", kind="once", start_date=d(0), assignee=self.tech, assigned_group=self.team)

    def test_switching_from_person_to_group_when_editing(self):
        j = self.make_job("J", assignee=self.tech)
        self.as_user(self.admin).post(reverse("job_edit", args=[j.pk]), job_post(title="J", equipment=self.eq.pk, assignee="", assigned_group=self.team.pk))
        j.refresh_from_db(); self.assertEqual((j.assignee, j.assigned_group), (None, self.team))

    def test_project_task_can_go_to_a_group(self):
        p = Project.objects.create(name="P", owner=self.admin)
        data = dict(title="Group task", equipment="", assignee="", assigned_group=self.team.pk, weight=2, instructions="", due_date=iso(d(2)))
        self.assertEqual(self.as_user(self.admin).post(reverse("task_new", args=[p.pk]), data).status_code, 302)
        self.assertEqual(p.tasks.get().assigned_group, self.team)


class GroupJobsInCalendars(GroupBase):
    def setUp(self):
        super().setUp()
        self.gj = self.make_job("GROUP-JOB-TITLE", assigned_group=self.team)
        self.solo = self.make_job("OTHER-PERSON-JOB", assignee=self.outsider)

    def test_job_appears_for_every_member_on_every_page(self):
        for user in (self.tech, self.tech2):
            c = self.as_user(user)
            for name in ("today", "calendar", "jobs"):
                self.assertContains(c.get(reverse(name)), "GROUP-JOB-TITLE", msg_prefix=f"{user.username} {name}")
                self.assertNotContains(c.get(reverse(name)), "OTHER-PERSON-JOB", msg_prefix=f"{user.username} {name}")

    def test_not_in_calendar_of_non_members_unless_all(self):
        c = self.as_user(self.outsider)
        for name in ("today", "calendar", "jobs"):
            self.assertNotContains(c.get(reverse(name)), "GROUP-JOB-TITLE", msg_prefix=name)
        self.assertContains(c.get(reverse("jobs"), {"scope": "all"}), "GROUP-JOB-TITLE")

    def test_recurring_group_job_lands_on_each_members_dates(self):
        rj = self.make_job("Daily group", "repeat", start=-2, every=1, unit="day", assigned_group=self.team)
        for u in (self.tech, self.tech2):
            dates = [o.when for o in load_occurrences(d(-2), d(2), u, scope="mine") if o.job == rj]
            self.assertEqual(dates, [d(i) for i in range(-2, 3)])
        self.assertEqual([o for o in load_occurrences(d(-2), d(2), self.outsider, scope="mine") if o.job == rj], [])

    def test_member_added_later_sees_it_and_removed_member_loses_it(self):
        self.team.members.add(self.outsider)
        self.assertIn(self.gj, [o.job for o in load_occurrences(d(-1), d(1), self.outsider, scope="mine")])
        self.team.members.remove(self.tech)
        self.assertNotIn(self.gj, [o.job for o in load_occurrences(d(-1), d(1), self.tech, scope="mine")])

    def test_job_shows_group_name_and_members(self):
        r = self.as_user(self.admin).get(reverse("job_detail", args=[self.gj.pk]))
        self.assertContains(r, "Night shift"); self.assertContains(r, "tina"); self.assertContains(r, "tom")

    def test_admin_in_the_group_sees_it_under_assigned_to_me(self):
        self.team.members.add(self.admin)
        self.assertContains(self.as_user(self.admin).get(reverse("calendar"), {"scope": "mine"}), "GROUP-JOB-TITLE")

    def test_no_duplicates_when_person_is_in_two_groups(self):
        other = Group.objects.create(name="Weekend"); other.members.add(self.tech)
        self.assertEqual(len([o for o in load_occurrences(d(0), d(0), self.tech, scope="mine") if o.job == self.gj]), 1)
        self.assertEqual(self.as_user(self.tech).get(reverse("jobs")).content.decode().count("GROUP-JOB-TITLE"), 1)


class DoingGroupJobs(GroupBase):
    def setUp(self):
        super().setUp()
        self.gj = self.make_job("Group job", assigned_group=self.team)

    def test_member_can_complete_and_it_counts_for_everyone(self):
        r = self.as_user(self.tech).post(reverse("done"), dict(job=self.gj.pk, due_date=iso(d(0))))
        self.assertEqual(r.status_code, 302)
        c = Completion.objects.get(); self.assertEqual(c.done_by, self.tech)
        for u in (self.tech, self.tech2):
            self.assertEqual([o.status for o in load_occurrences(d(0), d(0), u, scope="mine") if o.job == self.gj], ["done"])
        self.assertEqual(self.as_user(self.tech2).post(reverse("done"), dict(job=self.gj.pk, due_date=iso(d(0)))).status_code, 302)
        self.assertEqual(Completion.objects.count(), 1)                          # the second member cannot complete it twice

    def test_non_member_cannot_complete(self):
        self.assertEqual(self.as_user(self.outsider).post(reverse("done"), dict(job=self.gj.pk, due_date=iso(d(0)))).status_code, 403)
        self.assertEqual(Completion.objects.count(), 0)

    def test_removed_member_can_no_longer_complete(self):
        self.team.members.remove(self.tech)
        self.assertEqual(self.as_user(self.tech).post(reverse("done"), dict(job=self.gj.pk, due_date=iso(d(0)))).status_code, 403)

    def test_history_mine_includes_group_work_done_by_a_colleague(self):
        self.as_user(self.tech).post(reverse("done"), dict(job=self.gj.pk, due_date=iso(d(0)), note="GROUP-NOTE"))
        self.assertContains(self.as_user(self.tech2).get(reverse("history"), {"scope": "mine"}), "GROUP-NOTE")
        self.assertNotContains(self.as_user(self.outsider).get(reverse("history"), {"scope": "mine"}), "GROUP-NOTE")

    def test_export_shows_the_group_name(self):
        Completion.objects.create(job=self.gj, due_date=d(0), planned_date=d(0), done_by=self.tech)
        r = self.as_user(self.admin).get(reverse("export"), dict(date_from=iso(d(-1)), date_to=iso(d(0)), format="csv", scope="all"))
        rows = list(csv.reader(io.StringIO(b"".join(r.streaming_content).decode("utf-8-sig"))))
        self.assertEqual(rows[1][11], "Night shift")

    def test_project_progress_counts_group_tasks(self):
        p = Project.objects.create(name="P", owner=self.admin)
        t = Job.objects.create(project=p, title="t", kind="once", start_date=d(1), weight=4, assigned_group=self.team)
        self.as_user(self.tech2).post(reverse("done"), dict(job=t.pk, due_date=iso(d(1))))
        from core.services import project_stats_map
        self.assertEqual(project_stats_map([p])[p.id]["pct"], 100)
        link = 'href="/projects/%d/"' % p.pk
        self.assertContains(self.as_user(self.tech).get(reverse("projects")), link)          # visible under "mine" via the group
        self.assertNotContains(self.as_user(self.outsider).get(reverse("projects")), link)
        self.assertContains(self.as_user(self.outsider).get(reverse("projects"), {"scope": "all"}), link)


class AdminAssignedByMe(Base):
    def setUp(self):
        from core.models import Profile
        self.boss2 = User.objects.create_user("boss2", password=PW)
        Profile.objects.filter(user=self.boss2).update(role="admin")
        self.by_me = self.make_job("MADE-BY-BOSS", assignee=self.tech, created_by=self.admin)
        self.by_other = self.make_job("MADE-BY-BOSS2", assignee=self.tech, created_by=self.boss2)
        self.to_me = self.make_job("FOR-BOSS", assignee=self.admin, created_by=self.boss2)

    def test_three_way_switch_for_admins_two_way_for_technicians(self):
        r = self.as_user(self.admin).get(reverse("calendar"))
        html = r.content.decode()
        self.assertIn('class="scope3"', html); self.assertEqual(html.count('name="scope"'), 3)
        for label in ("Everything", "Assigned to me", "Assigned by me"):
            self.assertIn(label, html)
        t = self.as_user(self.tech).get(reverse("calendar")).content.decode()
        self.assertNotIn("scope3", t); self.assertIn('class="switch"', t)

    def test_admin_calendar_assigned_by_me(self):
        r = self.as_user(self.admin).get(reverse("calendar"), {"scope": "by_me"})
        self.assertEqual(r.context["scope"], "by_me")
        self.assertContains(r, "MADE-BY-BOSS</a>"); self.assertNotContains(r, "MADE-BY-BOSS2"); self.assertNotContains(r, "FOR-BOSS")

    def test_admin_calendar_assigned_to_me(self):
        r = self.as_user(self.admin).get(reverse("calendar"), {"scope": "mine"})
        self.assertContains(r, "FOR-BOSS"); self.assertNotContains(r, "MADE-BY-BOSS")

    def test_admin_calendar_everything(self):
        r = self.as_user(self.admin).get(reverse("calendar"), {"scope": "all"})
        for t in ("MADE-BY-BOSS</a>", "MADE-BY-BOSS2", "FOR-BOSS"):
            self.assertContains(r, t)

    def test_same_views_on_jobs_and_today(self):
        c = self.as_user(self.admin)
        for name in ("jobs", "today"):
            r = c.get(reverse(name), {"scope": "by_me"})
            self.assertContains(r, "MADE-BY-BOSS"); self.assertNotContains(r, "FOR-BOSS", msg_prefix=name)
            r = c.get(reverse(name), {"scope": "mine"})
            self.assertContains(r, "FOR-BOSS"); self.assertNotContains(r, "MADE-BY-BOSS", msg_prefix=name)

    def test_choice_is_remembered_across_pages(self):
        c = self.as_user(self.admin); c.get(reverse("calendar"), {"scope": "by_me"})
        self.assertEqual(c.get(reverse("jobs")).context["scope"], "by_me")

    def test_technicians_cannot_use_by_me(self):
        c = self.as_user(self.tech)
        r = c.get(reverse("jobs"), {"scope": "by_me"})
        self.assertEqual(r.context["scope"], "mine"); self.assertNotIn("by_me", c.session.get("scope", ""))

    def test_by_me_in_history_and_insights_does_not_crash(self):
        Completion.objects.create(job=self.by_me, due_date=d(0), planned_date=d(0), done_by=self.tech)
        c = self.as_user(self.admin)
        for name in ("history", "insights", "projects"):
            self.assertEqual(c.get(reverse(name), {"scope": "by_me"}).status_code, 200, name)
        self.assertContains(c.get(reverse("history"), {"scope": "by_me"}), "MADE-BY-BOSS")

    def test_jobs_created_through_the_form_record_who_created_them(self):
        self.as_user(self.boss2).post(reverse("job_new"), job_post(title="FORM-MADE", equipment=self.eq.pk))
        self.assertEqual(Job.objects.get(title="FORM-MADE").created_by, self.boss2)


class OneTimeRepeatForm(Base):
    """These render the real form. (Posting straight to the view, as other tests do, can never catch a broken form.)"""

    def test_kind_is_two_radio_buttons_not_a_dropdown(self):
        html = self.as_user(self.admin).get(reverse("job_new")).content.decode()
        self.assertNotRegex(html, r'<select[^>]*name="kind"')
        self.assertIsNotNone(tag(html, "kind", "once")); self.assertIsNotNone(tag(html, "kind", "repeat"))
        self.assertIn('type="radio"', tag(html, "kind", "repeat"))

    def test_new_job_starts_on_one_time(self):
        html = self.as_user(self.admin).get(reverse("job_new")).content.decode()
        self.assertIn("checked", tag(html, "kind", "once")); self.assertNotIn("checked", tag(html, "kind", "repeat"))

    def test_both_sections_and_all_their_fields_are_on_the_page(self):
        html = self.as_user(self.admin).get(reverse("job_new")).content.decode()
        for needle in ('id="once-box"', 'id="repeat-box"', 'name="due_date"', 'name="first_date"', 'name="every"', 'name="unit"', 'name="end_date"', 'data-preset="1:week"'):
            self.assertIn(needle, html)
        self.assertEqual(len(re.findall(r'id="once-box"', html)), 1)

    def test_editing_a_repeating_job_opens_on_repeats(self):
        j = self.make_job("R", "repeat", every=2, unit="week")
        html = self.as_user(self.admin).get(reverse("job_edit", args=[j.pk])).content.decode()
        self.assertIn("checked", tag(html, "kind", "repeat")); self.assertNotIn("checked", tag(html, "kind", "once"))
        self.assertIn('value="2"', html)

    def test_editing_a_one_off_job_opens_on_one_time(self):
        j = self.make_job("O", start=3)
        html = self.as_user(self.admin).get(reverse("job_edit", args=[j.pk])).content.decode()
        self.assertIn("checked", tag(html, "kind", "once")); self.assertIn(iso(d(3)), html)

    def test_failed_save_keeps_the_chosen_option(self):
        html = self.as_user(self.admin).post(reverse("job_new"), job_post(title="", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day")).content.decode()
        self.assertIn("checked", tag(html, "kind", "repeat"))

    def test_project_task_form_has_no_repeat_option(self):
        p = Project.objects.create(name="P", owner=self.admin)
        html = self.as_user(self.admin).get(reverse("task_new", args=[p.pk])).content.decode()
        self.assertIsNone(tag(html, "kind", "repeat")); self.assertIn('name="due_date"', html)

    def test_end_to_end_one_time_then_repeat_then_switch(self):
        c = self.as_user(self.admin)
        c.post(reverse("job_new"), job_post(title="A", equipment=self.eq.pk, kind="once", due_date=iso(d(4))))
        c.post(reverse("job_new"), job_post(title="B", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=3, unit="day", end_date=iso(d(30))))
        a, b = Job.objects.get(title="A"), Job.objects.get(title="B")
        self.assertEqual((a.kind, a.start_date, a.every, a.unit), ("once", d(4), None, ""))
        self.assertEqual((b.kind, b.every, b.unit, b.end_date), ("repeat", 3, "day", d(30)))
        # an existing one-off can be turned into a repeating job and back
        c.post(reverse("job_edit", args=[a.pk]), job_post(title="A", equipment=self.eq.pk, kind="repeat", first_date=iso(d(1)), every=1, unit="week"))
        a.refresh_from_db(); self.assertEqual((a.kind, a.start_date, a.every, a.unit), ("repeat", d(1), 1, "week"))
        c.post(reverse("job_edit", args=[a.pk]), job_post(title="A", equipment=self.eq.pk, kind="once", due_date=iso(d(9))))
        a.refresh_from_db(); self.assertEqual((a.kind, a.start_date, a.every, a.unit, a.end_date), ("once", d(9), None, "", None))

    def test_page_loads_the_script_that_runs_the_switch(self):
        from django.conf import settings
        self.assertContains(self.as_user(self.admin).get(reverse("job_new")), "app.js")
        self.assertIn("input[name=kind]", (settings.BASE_DIR / "static" / "app.js").read_text())


class DownloadByScope(Base):
    def test_admin_can_download_what_they_assigned(self):
        a = self.make_job("BY-ME", assignee=self.tech, created_by=self.admin)
        b = self.make_job("NOT-BY-ME", assignee=self.tech, created_by=self.tech2)
        for j in (a, b):
            Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=self.tech)
        r = self.as_user(self.admin).get(reverse("export"), dict(date_from=iso(d(-1)), date_to=iso(d(0)), format="csv", scope="by_me"))
        titles = [x[2] for x in csv.reader(io.StringIO(b"".join(r.streaming_content).decode("utf-8-sig")))][1:]
        self.assertEqual(titles, ["BY-ME"])

    def test_technician_by_me_falls_back_to_their_own_work(self):
        a = self.make_job("MINE", assignee=self.tech, created_by=self.admin)
        b = self.make_job("SOMEONE-ELSES", assignee=self.tech2, created_by=self.admin)
        for j, u in ((a, self.tech), (b, self.tech2)):
            Completion.objects.create(job=j, due_date=d(0), planned_date=d(0), done_by=u)
        r = self.as_user(self.tech).get(reverse("export"), dict(date_from=iso(d(-1)), date_to=iso(d(0)), format="csv", scope="by_me"))
        titles = [x[2] for x in csv.reader(io.StringIO(b"".join(r.streaming_content).decode("utf-8-sig")))][1:]
        self.assertEqual(titles, ["MINE"])
