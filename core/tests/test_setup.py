"""Setup page (tabs, search, paging, manage dialog), groups with many people, per-job skip days, no closures.
Run with  python manage.py test core"""
import re
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Group, Job, Profile, SkipRange
from core.services import load_occurrences, sync_skip_dates
from .helpers import Base, PW, d, iso
from .test_web import job_post

User = get_user_model()


def make_people(n, prefix="user"):
    from django.contrib.auth.hashers import make_password
    hashed = make_password(PW)                           # hash once: hashing 140 passwords one by one would be slow
    User.objects.bulk_create([User(username=f"{prefix}{i:03d}", password=hashed) for i in range(n)])   # bulk_create skips the profile signal
    created = list(User.objects.filter(username__startswith=prefix).order_by("username"))
    Profile.objects.bulk_create([Profile(user=u, role="tech") for u in created])
    return created


class SetupTabs(Base):
    def get(self, **params):
        return self.as_user(self.admin).get(reverse("setup"), params)

    def test_default_tab_is_people_and_unknown_tab_falls_back(self):
        self.assertEqual(self.get().context["tab"], "people")
        self.assertEqual(self.get(tab="<script>").context["tab"], "people")

    def test_each_tab_shows_only_its_own_section(self):
        marks = {"people": "+ Add person", "groups": "+ Create group", "areas": "+ Add area", "security": "Failed sign-ins, last 24 h"}
        for tab, mark in marks.items():
            r = self.get(tab=tab)
            self.assertContains(r, mark, msg_prefix=tab)
            for other, other_mark in marks.items():
                if other != tab:
                    self.assertNotContains(r, other_mark, msg_prefix=f"{tab} must not show {other}")

    def test_tab_counts_in_header(self):
        Group.objects.create(name="G1")
        c = self.get().context["counts"]
        self.assertEqual((c["people"], c["groups"], c["areas"]), (3, 1, 1))

    def test_there_is_no_closure_section_any_more(self):
        for tab in ("people", "groups", "areas", "security"):
            self.assertNotContains(self.get(tab=tab), "closure", msg_prefix=tab)
            self.assertNotContains(self.get(tab=tab), "Closure", msg_prefix=tab)

    def test_areas_tab_add_and_delete_return_to_areas(self):
        c = self.as_user(self.admin)
        self.assertRedirects(c.post(reverse("area_add"), dict(name="Packing")), reverse("setup") + "?tab=areas")
        from core.models import Area
        a = Area.objects.get(name="Packing")
        self.assertRedirects(c.post(reverse("area_delete", args=[a.pk])), reverse("setup") + "?tab=areas")
        self.assertContains(self.get(tab="areas"), "Line 1")


class PeopleAtScale(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.crowd = make_people(60)

    def get(self, **params):
        return self.as_user(self.admin).get(reverse("setup"), params)

    def test_only_one_page_of_people_is_rendered(self):
        r = self.get()
        self.assertEqual(len(r.context["page"]), 25)
        html = r.content.decode()
        self.assertEqual(len(re.findall(r'data-open="dlg-user"', html)), 25)           # one Manage button per row ...
        self.assertEqual(html.count("dlg-user"), 25 + 1)                                  # ... but ONE shared dialog, not 63 forms
        self.assertEqual(html.count('type="password"'), 2 + 1)                            # add-person dialog (2) + manage dialog (1)
        self.assertContains(r, "63 in total"); self.assertContains(r, "Page 1 of 3")

    def test_paging_reaches_everybody_exactly_once(self):
        seen = []
        for p in (1, 2, 3):
            seen += [u.username for u in self.get(page=p).context["page"]]
        self.assertEqual(len(seen), 63); self.assertEqual(len(set(seen)), 63)
        self.assertEqual(self.get(page=999).context["page"].number, 3)                   # past the end -> last page
        self.assertEqual(self.get(page="abc").context["page"].number, 1)

    def test_search(self):
        r = self.get(q="user05")
        self.assertEqual({u.username for u in r.context["page"]}, {f"user05{i}" for i in range(10)})
        self.assertContains(r, "Clear")
        self.assertEqual(len(self.get(q="zzzz").context["page"]), 0); self.assertContains(self.get(q="zzzz"), "Nobody matches")

    def test_role_filters(self):
        User.objects.filter(username="user007").update(is_active=False)
        Profile.objects.filter(user__username="user001").update(role="admin")
        self.assertEqual({u.username for u in self.get(role="admin").context["page"]}, {"boss", "user001"})
        self.assertEqual([u.username for u in self.get(role="inactive").context["page"]], ["user007"])
        self.assertEqual(self.get(role="tech").context["page"].paginator.count, 63 - 2)
        self.assertEqual(self.get(role="nonsense").context["role"], "all")

    def test_inactive_people_sort_last(self):
        User.objects.filter(username="tom").update(is_active=False)                      # not the admin who is signed in
        self.assertEqual(self.get(page=3).context["page"][-1].username, "tom")

    def test_query_count_does_not_grow_with_people(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        c = self.as_user(self.admin)
        c.get(reverse("setup"))
        with CaptureQueriesContext(connection) as q1:
            c.get(reverse("setup"))
        Group.objects.create(name="G").members.add(*self.crowd[:25])
        with CaptureQueriesContext(connection) as q2:
            c.get(reverse("setup"))
        self.assertLessEqual(len(q2), len(q1) + 2)

    def test_manage_button_carries_urls_and_hides_deactivate_for_yourself(self):
        html = self.get(q="boss").content.decode()
        btn = re.search(r'<button[^>]*data-open="dlg-user"[^>]*>', html).group(0)
        self.assertIn(reverse("user_password", args=[self.admin.pk]), btn); self.assertIn('data-toggle=""', btn)
        html = self.get(q="tina").content.decode()
        self.assertIn(f'data-toggle="{reverse("user_toggle", args=[self.tech.pk])}"', html)

    def test_actions_return_to_the_same_filtered_page(self):
        c = self.as_user(self.admin)
        back = reverse("setup") + "?tab=people&q=user05&page=1"
        r = c.post(reverse("user_toggle", args=[self.crowd[5].pk]), {"back": back})
        self.assertRedirects(r, back, fetch_redirect_response=False)
        r = c.post(reverse("user_toggle", args=[self.crowd[5].pk]), {"back": "https://evil.example/"})
        self.assertRedirects(r, reverse("setup") + "?tab=people", fetch_redirect_response=False)       # open redirect refused

    def test_add_person_from_dialog(self):
        c = self.as_user(self.admin)
        r = c.post(reverse("user_add"), dict(username="newbie", password1=PW, password2=PW, role="tech", back=reverse("setup") + "?tab=people&q=new"))
        self.assertEqual(r.status_code, 302); self.assertEqual(User.objects.get(username="newbie").profile.role, "tech")

    def test_reset_password_and_deactivate(self):
        c = self.as_user(self.admin)
        c.post(reverse("user_password", args=[self.tech.pk]), {"password": "An0ther-Str0ng-One"})
        self.assertTrue(User.objects.get(pk=self.tech.pk).check_password("An0ther-Str0ng-One"))
        c.post(reverse("user_toggle", args=[self.tech.pk])); self.assertFalse(User.objects.get(pk=self.tech.pk).is_active)
        c.post(reverse("user_toggle", args=[self.tech.pk])); self.assertTrue(User.objects.get(pk=self.tech.pk).is_active)

    def test_groups_of_each_person_are_listed_compactly(self):
        for n in "ABCDE":
            Group.objects.create(name=f"Team {n}").members.add(self.tech)
        r = self.get(q="tina")
        html = r.content.decode()
        self.assertEqual(html.count("Team "), 3); self.assertIn("+2 more", html)


class GroupsAtScale(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.crowd = make_people(80)
        cls.big = Group.objects.create(name="Everyone")
        cls.big.members.add(*cls.crowd[:70])

    def page(self, **params):
        return self.as_user(self.admin).get(reverse("group_detail", args=[self.big.pk]), params)

    def test_groups_list_shows_a_short_preview_not_every_member(self):
        r = self.as_user(self.admin).get(reverse("setup"), {"tab": "groups"})
        html = r.content.decode()
        self.assertContains(r, "70 members"); self.assertContains(r, "+66 more")
        self.assertNotIn("user050", html)

    def test_member_list_is_paged_and_searchable(self):
        r = self.page()
        self.assertEqual(len(r.context["page"]), 30); self.assertContains(r, "Page 1 of 3")
        self.assertEqual({u.username for u in self.page(mq="user06").context["page"]}, {f"user06{i}" for i in range(10)})
        self.assertContains(self.page(mq="nobody"), "Nobody called")

    def test_add_people_list_is_capped_and_excludes_members(self):
        r = self.page()
        names = [u.username for u in r.context["candidates"]]
        self.assertEqual(len(names), 13)                                                      # 10 crowd members not in the group + boss, tina, tom
        self.assertTrue(all(n not in {u.username for u in self.crowd[:70]} for n in names))   # nobody already in the group
        self.assertNotIn("user000", names)
        r = self.page(q="user07")
        self.assertEqual({u.username for u in r.context["candidates"]}, {f"user07{i}" for i in range(10)})
        self.assertFalse(r.context["more_candidates"])

    def test_more_than_the_cap_says_so(self):
        g = Group.objects.create(name="Empty")
        r = self.as_user(self.admin).get(reverse("group_detail", args=[g.pk]))
        self.assertEqual(len(r.context["candidates"]), 30); self.assertTrue(r.context["more_candidates"])
        self.assertContains(r, "Showing the first 30")

    def test_add_ten_then_remove_one(self):
        ids = [u.pk for u in self.crowd[70:80]]
        c = self.as_user(self.admin)
        c.post(reverse("group_members_add", args=[self.big.pk]), {"users": ids})
        self.assertEqual(self.big.members.count(), 80)
        c.post(reverse("group_member_remove", args=[self.big.pk, ids[0]]))
        self.assertEqual(self.big.members.count(), 79)

    def test_adding_nobody_gives_a_message_not_an_error(self):
        r = self.as_user(self.admin).post(reverse("group_members_add", args=[self.big.pk]), {}, follow=True)
        self.assertContains(r, "Tick at least one person")

    def test_group_pages_keep_search_state_in_links(self):
        html = self.page(mq="user0", q="user07").content.decode()
        self.assertIn("mq=user0", html); self.assertIn("q=user07", html)
        self.assertIn("data-check-all", html)


class SkipDaysOnTheJobForm(Base):
    def post_repeat(self, **extra):
        data = job_post(title="Rep", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day")
        data.update(extra)
        return self.as_user(self.admin).post(reverse("job_new"), data)

    def singles(self, job):
        return sorted(r.start_date for r in job.skip_ranges.all() if r.start_date == r.end_date)

    def test_field_is_on_the_repeat_form_only(self):
        self.assertContains(self.as_user(self.admin).get(reverse("job_new")), 'name="skip_dates"')
        from core.models import Project
        p = Project.objects.create(name="P", owner=self.admin)
        self.assertNotContains(self.as_user(self.admin).get(reverse("task_new", args=[p.pk])), 'name="skip_dates"')

    def test_create_repeating_job_with_separate_skip_days(self):
        days = [d(3), d(5), d(11)]                                    # not consecutive
        self.assertEqual(self.post_repeat(skip_dates=", ".join(iso(x) for x in days)).status_code, 302)
        j = Job.objects.get(title="Rep")
        self.assertEqual(self.singles(j), days)
        self.assertTrue(all(r.created_by == self.admin for r in j.skip_ranges.all()))
        got = [o.when for o in load_occurrences(d(0), d(12), self.admin) if o.job == j]
        self.assertEqual(got, [d(i) for i in range(0, 13) if i not in (3, 5, 11)])

    def test_a_single_day_and_no_days_both_work(self):
        self.post_repeat(skip_dates=iso(d(2)))
        self.assertEqual(self.singles(Job.objects.get(title="Rep")), [d(2)])
        Job.objects.all().delete()
        self.post_repeat(skip_dates="")
        self.assertEqual(self.singles(Job.objects.get(title="Rep")), [])

    def test_bad_input_blocks_the_save_with_a_message(self):
        for bad in ("03/11/2026", "2026-02-30", "soon"):
            r = self.post_repeat(skip_dates=bad)
            self.assertEqual(r.status_code, 200, bad); self.assertEqual(Job.objects.count(), 0, bad)
            self.assertContains(r, 'class="err"', msg_prefix=bad)
        self.assertContains(self.post_repeat(skip_dates="03/11/2026"), "YYYY-MM-DD")
        r = self.post_repeat(skip_dates=",".join(iso(d(i)) for i in range(400)))
        self.assertEqual(Job.objects.count(), 0)

    def test_editing_shows_existing_days_and_adds_removes_only_what_changed(self):
        self.post_repeat(skip_dates=f"{iso(d(2))}, {iso(d(4))}, {iso(d(6))}")
        j = Job.objects.get(title="Rep")
        ids = {r.start_date: r.pk for r in j.skip_ranges.all()}
        html = self.as_user(self.admin).get(reverse("job_edit", args=[j.pk])).content.decode()
        self.assertIn(f"{iso(d(2))}, {iso(d(4))}, {iso(d(6))}", html)
        data = job_post(title="Rep", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day", skip_dates=f"{iso(d(4))}, {iso(d(8))}")
        self.client.post(reverse("job_edit", args=[j.pk]), data)
        self.assertEqual(self.singles(j), [d(4), d(8)])
        self.assertEqual(j.skip_ranges.get(start_date=d(4)).pk, ids[d(4)])           # untouched rows keep their id

    def test_multi_day_ranges_from_the_job_page_survive_editing_the_list(self):
        self.post_repeat(skip_dates=iso(d(2)))
        j = Job.objects.get(title="Rep")
        self.client.post(reverse("skip_create"), dict(job=j.pk, start_date=iso(d(6)), end_date=iso(d(8))))
        self.client.post(reverse("job_edit", args=[j.pk]), job_post(title="Rep", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day", skip_dates=""))
        self.assertEqual(j.skip_ranges.count(), 1)
        r = j.skip_ranges.get(); self.assertEqual((r.start_date, r.end_date), (d(6), d(8)))

    def test_turning_the_job_into_one_time_drops_its_skip_days(self):
        self.post_repeat(skip_dates=f"{iso(d(2))}, {iso(d(3))}")
        j = Job.objects.get(title="Rep")
        self.client.post(reverse("job_edit", args=[j.pk]), job_post(title="Rep", equipment=self.eq.pk, kind="once", due_date=iso(d(1)), skip_dates=iso(d(2))))
        self.assertEqual(j.skip_ranges.count(), 0)

    def test_editing_without_the_field_changes_nothing(self):
        self.post_repeat(skip_dates=iso(d(2)))
        j = Job.objects.get(title="Rep")
        data = job_post(title="Rep2", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day"); data.pop("skip_dates", None)
        self.client.post(reverse("job_edit", args=[j.pk]), data)
        self.assertEqual(self.singles(j), [d(2)])

    def test_job_page_lists_the_skipped_days_with_undo(self):
        self.post_repeat(skip_dates=f"{iso(d(2))}, {iso(d(9))}")
        j = Job.objects.get(title="Rep")
        r = self.client.get(reverse("job_detail", args=[j.pk]))
        for day in (d(2), d(9)):
            self.assertContains(r, day.strftime("%a %d %b %Y"))
        self.assertEqual(len(re.findall(r"/exceptions/skip/\d+/undo/", r.content.decode())), 2)      # one Undo button per skipped day

    def test_sync_function_directly(self):
        j = self.make_job("R", "repeat", start=0, every=1, unit="day")
        sync_skip_dates(j, [d(1), d(3)], self.admin); sync_skip_dates(j, [d(1), d(3)], self.admin)
        self.assertEqual(j.skip_ranges.count(), 2)                                      # idempotent
        sync_skip_dates(j, [], self.admin); self.assertEqual(j.skip_ranges.count(), 0)

    def test_only_admins_can_set_skip_days(self):
        j = self.make_job("R", "repeat", start=0, every=1, unit="day")
        r = self.as_user(self.tech).post(reverse("job_edit", args=[j.pk]), job_post(title="R", equipment=self.eq.pk, kind="repeat", first_date=iso(d(0)), every=1, unit="day", skip_dates=iso(d(1))))
        self.assertEqual(r.status_code, 403); self.assertEqual(SkipRange.objects.count(), 0)

    def test_jobs_meant_for_shutdown_days_still_run_on_them(self):
        """The reason closures were removed: a job may exist precisely for a shutdown day."""
        shutdown_job = self.make_job("Shutdown inspection", "repeat", start=0, every=1, unit="day")
        SkipRange.objects.create(job=None, start_date=d(0), end_date=d(5))                 # leftover plant-wide row from an old version
        self.assertEqual(len([o for o in load_occurrences(d(0), d(5), self.admin) if o.job == shutdown_job]), 6)
