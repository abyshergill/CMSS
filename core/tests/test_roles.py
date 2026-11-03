"""Three roles (Admin / Manager / Engineer), the job-name download filter, the sidebar user box.
Run with  python manage.py test core.tests.test_roles"""
import csv, io
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import Completion, Job, Profile
from .helpers import Base, PW, d, iso

User = get_user_model()


def set_role(user, role):
    Profile.objects.filter(user=user).update(role=role)


class RoleBase(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.mgr = User.objects.create_user("mona", password=PW); set_role(cls.mgr, "manager")
        cls.mgr2 = User.objects.create_user("max", password=PW); set_role(cls.mgr2, "manager")
        cls.adm2 = User.objects.create_user("adam", password=PW); set_role(cls.adm2, "admin")      # an Admin who is not the main account

    def post_add(self, who, role, name="newbie"):
        return self.as_user(who).post(reverse("user_add"), dict(username=name, password1=PW, password2=PW, role=role))


class ManagerPowers(RoleBase):
    def test_manager_can_open_every_management_page(self):
        j = self.make_job("J", "repeat", assignee=self.tech)
        c = self.as_user(self.mgr)
        for url in (reverse("setup"), reverse("job_new"), reverse("job_edit", args=[j.pk]), reverse("project_new"), reverse("equipment"),
                    reverse("setup") + "?tab=groups", reverse("setup") + "?tab=areas", reverse("setup") + "?tab=security"):
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_manager_gets_the_admin_three_way_scope_switch(self):
        self.assertContains(self.as_user(self.mgr).get(reverse("jobs")), "Assigned by me")

    def test_manager_can_add_engineers_and_managers(self):
        self.assertEqual(self.post_add(self.mgr, "tech", "e1").status_code, 302)
        self.assertEqual(self.post_add(self.mgr, "manager", "m1").status_code, 302)
        self.assertEqual(User.objects.get(username="e1").profile.role, "tech")
        self.assertEqual(User.objects.get(username="m1").profile.role, "manager")

    def test_manager_cannot_create_an_admin(self):
        self.post_add(self.mgr, "admin", "sneaky")
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_manager_does_not_even_see_the_admin_choice(self):
        r = self.as_user(self.mgr).get(reverse("setup"))
        self.assertEqual([v for v, _ in r.context["new_user"].fields["role"].choices], ["manager", "tech"])
        self.assertEqual([v for v, _ in r.context["role_choices"]], ["manager", "tech"])

    def test_admin_can_create_an_admin(self):
        self.post_add(self.admin, "admin", "second")
        self.assertEqual(User.objects.get(username="second").profile.role, "admin")
        self.assertEqual([v for v, _ in self.as_user(self.admin).get(reverse("setup")).context["role_choices"]], ["admin", "manager", "tech"])

    def test_manager_cannot_touch_an_admin_account(self):
        c = self.as_user(self.mgr)
        for target in (self.admin, self.adm2):                                  # the main account and a normal Admin
            c.post(reverse("user_password", args=[target.pk]), dict(password="Another-Str0ng-Pass!1"))
            c.post(reverse("user_toggle", args=[target.pk]))
            c.post(reverse("user_role", args=[target.pk]), dict(role="tech"))
        self.assertTrue(User.objects.get(pk=self.admin.pk).check_password(PW))
        self.assertTrue(User.objects.get(pk=self.adm2.pk).check_password(PW))
        self.assertTrue(User.objects.get(pk=self.adm2.pk).is_active)
        self.assertEqual(Profile.objects.get(user=self.adm2).role, "admin")

    def test_manager_cannot_make_anyone_an_admin(self):
        self.as_user(self.mgr).post(reverse("user_role", args=[self.tech.pk]), dict(role="admin"))
        self.assertEqual(Profile.objects.get(user=self.tech).role, "tech")

    def test_manager_can_manage_engineers_and_other_managers(self):
        c = self.as_user(self.mgr)
        c.post(reverse("user_role", args=[self.tech.pk]), dict(role="manager"))
        self.assertEqual(Profile.objects.get(user=self.tech).role, "manager")
        c.post(reverse("user_password", args=[self.mgr2.pk]), dict(password="Another-Str0ng-Pass!1"))
        self.assertTrue(User.objects.get(pk=self.mgr2.pk).check_password("Another-Str0ng-Pass!1"))
        c.post(reverse("user_toggle", args=[self.tech2.pk]))
        self.assertFalse(User.objects.get(pk=self.tech2.pk).is_active)

    def test_manage_button_is_replaced_for_admin_rows_when_a_manager_looks(self):
        r = self.as_user(self.mgr).get(reverse("setup"), {"q": "adam"})
        self.assertContains(r, "Admin only"); self.assertNotContains(r, reverse("user_password", args=[self.adm2.pk]))
        self.assertContains(self.as_user(self.admin).get(reverse("setup"), {"q": "adam"}), reverse("user_password", args=[self.adm2.pk]))


class ChangingRoles(RoleBase):
    def test_admin_can_set_any_role(self):
        c = self.as_user(self.admin)
        for role in ("manager", "admin", "tech"):
            c.post(reverse("user_role", args=[self.tech.pk]), dict(role=role))
            self.assertEqual(Profile.objects.get(user=self.tech).role, role)

    def test_nobody_changes_their_own_role_and_the_main_account_is_fixed(self):
        self.as_user(self.adm2).post(reverse("user_role", args=[self.adm2.pk]), dict(role="tech"))
        self.assertEqual(Profile.objects.get(user=self.adm2).role, "admin")
        self.as_user(self.adm2).post(reverse("user_role", args=[self.admin.pk]), dict(role="tech"))          # superuser
        self.assertEqual(Profile.objects.get(user=self.admin).role, "admin")
        self.assertEqual(self.as_user(self.admin).get(reverse("setup")).status_code, 200)

    def test_unknown_role_is_refused_and_get_is_not_allowed(self):
        c = self.as_user(self.admin)
        c.post(reverse("user_role", args=[self.tech.pk]), dict(role="owner"))
        self.assertEqual(Profile.objects.get(user=self.tech).role, "tech")
        self.assertEqual(c.get(reverse("user_role", args=[self.tech.pk])).status_code, 405)

    def test_engineer_cannot_change_roles(self):
        self.assertEqual(self.as_user(self.tech).post(reverse("user_role", args=[self.tech.pk]), dict(role="admin")).status_code, 403)
        self.assertEqual(Profile.objects.get(user=self.tech).role, "tech")

    def test_a_demoted_admin_loses_the_powers_at_once(self):
        self.as_user(self.admin).post(reverse("user_role", args=[self.adm2.pk]), dict(role="tech"))
        self.assertEqual(self.as_user(self.adm2).get(reverse("setup")).status_code, 403)

    def test_role_filters_on_the_people_tab(self):
        c = self.as_user(self.admin)
        names = lambda role: {u.username for u in c.get(reverse("setup"), {"role": role}).context["page"]}
        self.assertEqual(names("manager"), {"mona", "max"})
        self.assertEqual(names("admin"), {"boss", "adam"})
        self.assertEqual(names("tech"), {"tina", "tom"})


class Labels(RoleBase):
    def test_sidebar_shows_the_role_and_a_proper_sign_out(self):
        for user, label in ((self.admin, "Admin"), (self.mgr, "Manager"), (self.tech, "Engineer")):
            r = self.as_user(user).get(reverse("today"))
            self.assertContains(r, f'role-{ {"Admin": "admin", "Manager": "manager", "Engineer": "tech"}[label] }">{label}</span>')
            self.assertContains(r, "Sign out"); self.assertContains(r, 'action="' + reverse("logout") + '"')

    def test_the_word_technician_is_gone(self):
        for url in (reverse("today"), reverse("setup"), reverse("history")):
            self.assertNotContains(self.as_user(self.admin).get(url), "Technician")

    def test_engineers_are_still_limited(self):
        c = self.as_user(self.tech)
        for url in (reverse("setup"), reverse("job_new")):
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertEqual(c.post(reverse("user_role", args=[self.tech2.pk]), dict(role="manager")).status_code, 403)


class DownloadByJobName(RoleBase):
    def setUp(self):
        for title, who in (("Lubricate rollers", self.tech), ("Lubricate gearbox", self.tech2), ("Check fuel level", self.tech), ("100% clean", self.tech)):
            Completion.objects.create(job=self.make_job(title, assignee=who), due_date=d(0), planned_date=d(0), done_by=who)

    def titles(self, user, **kw):
        p = dict(date_from=iso(d(-7)), date_to=iso(d(0)), format="csv", scope="all"); p.update(kw)
        r = self.as_user(user).get(reverse("export"), p)
        self.assertEqual(r.status_code, 200)
        text = b"".join(r.streaming_content).decode("utf-8-sig")
        return sorted(row[2] for row in list(csv.reader(io.StringIO(text)))[1:]), r

    def test_no_job_name_means_everything(self):
        self.assertEqual(len(self.titles(self.admin)[0]), 4)

    def test_part_of_the_name_any_case(self):
        self.assertEqual(self.titles(self.admin, job="lubric")[0], ["Lubricate gearbox", "Lubricate rollers"])
        self.assertEqual(self.titles(self.admin, job="  FUEL ")[0], ["Check fuel level"])

    def test_it_combines_with_the_other_filters(self):
        self.assertEqual(self.titles(self.admin, job="lubric", person=self.tech.pk)[0], ["Lubricate rollers"])
        self.assertEqual(self.titles(self.tech, job="lubric", scope="mine")[0], ["Lubricate rollers"])
        self.assertEqual(self.titles(self.admin, job="lubric", date_from=iso(d(-7)), date_to=iso(d(-3)))[0], [])

    def test_wildcards_are_plain_text_and_nothing_found_gives_an_empty_file(self):
        self.assertEqual(self.titles(self.admin, job="%")[0], ["100% clean"])
        self.assertEqual(self.titles(self.admin, job="_")[0], [])
        self.assertEqual(self.titles(self.admin, job="nothing like this")[0], [])

    def test_file_name_mentions_the_job_and_stays_safe(self):
        _, r = self.titles(self.admin, job='Lubricate "rollers"\r\nX-Evil: 1')
        cd = r["Content-Disposition"]
        self.assertIn("_Lubricate-rollers-X-Evil-1.csv", cd); self.assertNotIn("\n", cd)

    def test_too_long_a_name_is_refused(self):
        r = self.as_user(self.admin).get(reverse("export"), dict(date_from=iso(d(-7)), date_to=iso(d(0)), format="csv", scope="all", job="x" * 81))
        self.assertEqual(r.status_code, 302)

    def test_the_history_page_offers_the_field_to_everyone(self):
        for u in (self.admin, self.mgr, self.tech):
            self.assertContains(self.as_user(u).get(reverse("history")), 'name="job"')
