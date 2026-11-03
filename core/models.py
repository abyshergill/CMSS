import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from . import engine


def upload_path(instance, filename):
    """Files are stored under a random name; the original name is kept only as text."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"{instance.UPLOAD_DIR}/{uuid.uuid4().hex}.{ext}"


class Profile(models.Model):
    ADMIN, MANAGER, TECH = "admin", "manager", "tech"           # "tech" is the stored value for Engineer (kept so old data still works)
    ROLES = [(ADMIN, "Admin"), (MANAGER, "Manager"), (TECH, "Engineer")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=ROLES, default=TECH)

    def __str__(self):
        return f"{self.user} ({self.role})"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def make_profile(sender, instance, created, **kw):
    if created:
        Profile.objects.get_or_create(user=instance, defaults={"role": Profile.ADMIN if instance.is_superuser else Profile.TECH})


class Area(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Group(models.Model):
    """A named team. A job assigned to a group shows up in the calendar of every member."""
    name = models.CharField(max_length=80, unique=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="work_groups")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment")
    notes = models.CharField(max_length=300, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["area__name", "name"]
        verbose_name_plural = "equipment"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Project(models.Model):
    ACTIVE, CLOSED = "active", "closed"
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, max_length=4000)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_projects")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=[(ACTIVE, "Active"), (CLOSED, "Closed")], default=ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["status", "name"]

    def __str__(self):
        return self.name


class Job(models.Model):
    ONCE, REPEAT = "once", "repeat"
    UNITS = [("day", "day(s)"), ("week", "week(s)"), ("month", "month(s)"), ("year", "year(s)")]
    equipment = models.ForeignKey(Equipment, null=True, blank=True, on_delete=models.CASCADE, related_name="jobs")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=160)
    instructions = models.TextField(blank=True, max_length=6000)
    kind = models.CharField(max_length=6, choices=[(ONCE, "One time"), (REPEAT, "Repeats")], default=ONCE)
    start_date = models.DateField()                         # due date (one-off) or first date (repeating)
    every = models.PositiveSmallIntegerField(null=True, blank=True)
    unit = models.CharField(max_length=5, choices=UNITS, blank=True)
    end_date = models.DateField(null=True, blank=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_jobs")
    assigned_group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)      # when the CURRENT assignee / group was chosen
    evidence_required = models.BooleanField(default=False, help_text="Whoever marks the job done must attach a photo or document.")
    checkpoints = models.JSONField(default=list, blank=True)       # ["Check oil level", ...] - each must be ticked to finish
    weight = models.PositiveSmallIntegerField(default=1, help_text="Effort points (1-10). Bigger tasks count more towards project progress.")
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["title"]
        constraints = [
            models.CheckConstraint(condition=Q(kind="once") | (Q(every__gte=1) & ~Q(unit="")), name="repeat_needs_interval"),
            models.CheckConstraint(condition=Q(weight__gte=1, weight__lte=10), name="weight_1_to_10"),
            models.CheckConstraint(condition=Q(assignee__isnull=True) | Q(assigned_group__isnull=True), name="person_or_group_not_both"),
        ]

    def __str__(self):
        return self.title

    @property
    def is_repeat(self):
        return self.kind == self.REPEAT

    @property
    def is_task(self):
        return self.project_id is not None

    @property
    def assigned_name(self):
        """Plain text: a person's username, a group's name, or '' when anyone can do it."""
        if self.assigned_group_id:
            return self.assigned_group.name
        return self.assignee.username if self.assignee_id else ""

    @property
    def assigned_label(self):
        return f"👥 {self.assigned_group.name}" if self.assigned_group_id else self.assigned_name

    @property
    def is_assigned(self):
        return bool(self.assignee_id or self.assigned_group_id)

    @property
    def assignment_by(self):
        """Who gave the job to its current assignee. Jobs saved before this was tracked fall back to their creator."""
        return (self.assigned_by or self.created_by) if self.is_assigned else None

    @property
    def assignment_at(self):
        return (self.assigned_at or self.created_at) if self.is_assigned else None

    def clean(self):
        if self.assignee_id and self.assigned_group_id:
            raise ValidationError("Assign a job to one person or to one group, not both.")
        if self.kind == self.REPEAT and (not self.every or not self.unit):
            raise ValidationError("A repeating job needs an interval.")
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("The end date is before the first date.")


class JobAttachment(models.Model):
    """Reference material for whoever does the job (PDF, image, Word, CSV, Excel)."""
    UPLOAD_DIR = "attachments"
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=upload_path, max_length=200)
    original_name = models.CharField(max_length=120)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.original_name


class SkipRange(models.Model):
    """Days on which ONE repeating job does not happen (one day, or several in a row; a job can have many of these).
    There are no plant-wide closures: some jobs are meant to run on shutdown days.
    `job` stays nullable only so databases that already hold old plant-wide rows keep working - those rows are ignored."""
    job = models.ForeignKey(Job, null=True, blank=True, on_delete=models.CASCADE, related_name="skip_ranges")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="skip_end_after_start")]

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("The last day is before the first day.")
        if (self.end_date - self.start_date).days + 1 > engine.MAX_SKIP_SPAN_DAYS:
            raise ValidationError("A skipped period can be at most one year.")


class MoveException(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="moves")
    orig_date = models.DateField()
    new_date = models.DateField()
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["orig_date"]
        constraints = [models.UniqueConstraint(fields=["job", "orig_date"], name="one_move_per_date")]


class Completion(models.Model):
    UPLOAD_DIR = "evidence"
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="completions")
    due_date = models.DateField()                           # the schedule date this completes
    planned_date = models.DateField()                       # the date it was really planned for (after any move)
    done_at = models.DateTimeField(default=timezone.now)
    done_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="completions")
    note = models.TextField(blank=True, max_length=4000)
    evidence = models.FileField(upload_to=upload_path, max_length=200, blank=True)
    evidence_name = models.CharField(max_length=120, blank=True)
    checkpoints = models.JSONField(default=list, blank=True)     # [{"text": ..., "ok": true}] - what was checked, as it was at the time
    evidence_was_required = models.BooleanField(default=False)
    # who the job was assigned to / by whom / when - copied at the moment of completion, so history stays true after re-assigning
    assignment_recorded = models.BooleanField(default=False)
    assigned_to = models.CharField(max_length=160, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-done_at"]
        constraints = [models.UniqueConstraint(fields=["job", "due_date"], name="one_completion_per_date")]

    @property
    def done_date(self):
        return timezone.localtime(self.done_at).date()

    @property
    def on_time(self):
        return self.done_date <= self.planned_date

    # what to show in History: the copy taken at completion, or (for older records) the job's current assignment
    @property
    def shown_assigned_to(self):
        return self.assigned_to if self.assignment_recorded else self.job.assigned_name

    @property
    def shown_assigned_by(self):
        return self.assigned_by if self.assignment_recorded else self.job.assignment_by

    @property
    def shown_assigned_at(self):
        return self.assigned_at if self.assignment_recorded else self.job.assignment_at

    @property
    def checks_ok(self):
        return sum(1 for k in self.checkpoints if k.get("ok"))

    def snapshot_assignment(self, job):
        self.assignment_recorded = True
        self.assigned_to = job.assigned_name
        self.assigned_by = job.assignment_by
        self.assigned_at = job.assignment_at


class LoginAttempt(models.Model):
    """Used for brute-force protection and the 'recent failed sign-ins' list."""
    username = models.CharField(max_length=150, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
