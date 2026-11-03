from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Area, Completion, Equipment, Group, Job, MoveException, Profile, Project, SkipRange

User = get_user_model()


class Command(BaseCommand):
    help = "Load example data (do NOT use on a real installation: demo passwords are weak)."

    def handle(self, *a, **kw):
        if User.objects.exists():
            self.stdout.write("Database already has users - nothing done.")
            return
        t = timezone.localdate()
        d = lambda n: t + timedelta(days=n)
        admin = User.objects.create_superuser("admin", password="admin-demo-123")
        tech = User.objects.create_user("tech", password="tech-demo-123")
        tech2 = User.objects.create_user("maria", password="maria-demo-123")
        Profile.objects.filter(user=admin).update(role="admin")
        boss = User.objects.create_user("manager", password="manager-demo-123")
        Profile.objects.filter(user=boss).update(role="manager")
        line, engine_room, wh = (Area.objects.create(name=n) for n in ("Assembly Line", "Engine Room", "Warehouse"))
        conv = Equipment.objects.create(code="CNV-01", name="Conveyor belt", area=line)
        comp = Equipment.objects.create(code="CMP-01", name="Air compressor", area=engine_room)
        gen = Equipment.objects.create(code="GEN-01", name="Backup generator", area=engine_room)
        fork = Equipment.objects.create(code="FLT-02", name="Forklift", area=wh)
        def mk(**k):
            if k.get("assignee") or k.get("assigned_group"):          # who gave it out, and when
                k.setdefault("assigned_by", admin)
                k.setdefault("assigned_at", timezone.now() - timedelta(days=12))
            return Job.objects.create(created_by=admin, **k)
        j1 = mk(equipment=conv, title="Lubricate rollers", instructions="Grade 2 grease, all 12 rollers.", kind="repeat", start_date=d(-21), every=1, unit="week", assignee=tech,
                 checkpoints=["All 12 rollers greased", "Excess grease wiped off", "Guard back in place"])
        j2 = mk(equipment=comp, title="Drain moisture trap", kind="repeat", start_date=d(-6), every=1, unit="day", assignee=tech,
                evidence_required=True, instructions="Take a photo of the drained water as proof.")
        crew = Group.objects.create(name="Maintenance crew")
        crew.members.add(tech, tech2)
        mk(equipment=conv, title="Weekly safety walk-round", instructions="Whole crew checklist - first person to finish ticks it for everybody.",
           kind="repeat", start_date=d(-10), every=1, unit="week", assigned_group=crew)
        j3 = mk(equipment=comp, title="Replace air filter", instructions="Part no. AF-220", kind="repeat", start_date=d(-30), every=3, unit="month")
        mk(equipment=gen, title="Annual load test", kind="once", start_date=d(4))
        mk(equipment=gen, title="Fire extinguisher inspection", kind="repeat", start_date=d(-200), every=1, unit="year",
           checkpoints=["Pressure gauge in the green", "Seal intact", "Inspection tag signed"])
        mk(equipment=fork, title="Replace forklift battery", kind="once", start_date=d(-3), assignee=tech2)
        mk(equipment=gen, title="Check fuel level", kind="repeat", start_date=d(-14), every=2, unit="week", assignee=tech2)
        # exceptions: skip a few days in a row, skip separate days, move one date
        SkipRange.objects.create(job=j2, start_date=d(-4), end_date=d(-2), reason="Compressor off for repair", created_by=admin)
        MoveException.objects.create(job=j1, orig_date=d(-14), new_date=d(2), reason="Waiting for grease", created_by=admin)
        for day in (d(9), d(12), d(30)):                                  # separate days, not consecutive
            SkipRange.objects.create(job=j1, start_date=day, end_date=day, reason="Skipped", created_by=admin)
        for job, due in ((j1, d(-21)), (j2, d(-1)), (j2, d(-5))):
            c = Completion(job=job, due_date=due, planned_date=due, done_by=tech, note="All good",
                           checkpoints=[{"text": t, "ok": True} for t in job.checkpoints], evidence_was_required=job.evidence_required)
            c.snapshot_assignment(job)
            c.save()
        # a project with small tasks for different people
        p = Project.objects.create(name="Line 2 upgrade", description="Replace belts and sensors on line 2.", owner=admin, due_date=d(14))
        for title, who, due, w, done in (("Order new belts", tech, -5, 1, True), ("Remove old belts", tech, -1, 3, True), ("Fit new belts", tech2, 3, 5, False),
                                         ("Calibrate sensors", tech2, 6, 3, False), ("Test run & sign-off", admin, 10, 2, False)):
            job = mk(project=p, title=title, kind="once", start_date=d(due), assignee=who, weight=w)
            if done:
                Completion.objects.create(job=job, due_date=job.start_date, planned_date=job.start_date, done_by=who, note="Done")
        self.stdout.write(self.style.SUCCESS("Demo data loaded. Sign in as admin / admin-demo-123, manager / manager-demo-123 or tech / tech-demo-123 (an Engineer)"))
