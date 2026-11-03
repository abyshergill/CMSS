from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import models

from . import checklist, datelist, engine, filecheck
from .models import Area, Equipment, Group, Job, Profile, Project
from .services import today

User = get_user_model()
MAX_FILES = 10


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        super().__init__(format="%Y-%m-%d", **kw)


# ---------------------------------------------------------------- uploads
class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *a, **kw):
        kw.setdefault("widget", MultipleFileInput(attrs={"accept": filecheck.ACCEPT_ATTR}))
        super().__init__(*a, **kw)

    def clean(self, data, initial=None):
        one = super().clean
        if isinstance(data, (list, tuple)):
            return [one(d, initial) for d in data if d]
        return [one(data, initial)] if data else []


def check_uploaded_file(f):
    """Raise ValidationError unless the file is an allowed type, the right size, and its content matches."""
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    problem = filecheck.validate_upload(f.name, f, f.size, limit)
    if problem:
        raise ValidationError(f"{filecheck.display_name(f.name)}: {problem}")


def check_uploaded_files(files):
    if len(files) > MAX_FILES:
        raise ValidationError(f"Please attach at most {MAX_FILES} files at a time.")
    for f in files:
        check_uploaded_file(f)
    return files


# ---------------------------------------------------------------- accounts
class LoginForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}))
    password = forms.CharField(max_length=256, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class FirstRunForm(forms.Form):
    username = forms.CharField(max_length=150)
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Repeat password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        d = super().clean()
        if d.get("password1") != d.get("password2"):
            self.add_error("password2", "The two passwords do not match.")
        elif d.get("password1"):
            try:
                validate_password(d["password1"], User(username=d.get("username", "")))
            except ValidationError as e:
                self.add_error("password1", e)
        return d


class NewUserForm(FirstRunForm):
    role = forms.ChoiceField(choices=Profile.ROLES, initial=Profile.TECH)

    def __init__(self, *a, actor=None, **kw):
        """`actor` = the person adding the account. A Manager may add Managers and Engineers - not Admins."""
        super().__init__(*a, **kw)
        if actor is not None:
            from .permissions import assignable_roles
            self.fields["role"].choices = assignable_roles(actor)

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=u).exists():
            raise ValidationError("That username is taken.")
        return u


class ResetPasswordForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput)

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        self.target = user

    def clean_password(self):
        validate_password(self.cleaned_data["password"], self.target)
        return self.cleaned_data["password"]


# ---------------------------------------------------------------- setup
class AreaForm(forms.ModelForm):
    class Meta:
        model = Area
        fields = ["name"]


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ["code", "name", "area", "notes"]


# ---------------------------------------------------------------- jobs
class JobForm(forms.ModelForm):
    due_date = forms.DateField(required=False, widget=DateInput(), label="Due date")
    first_date = forms.DateField(required=False, widget=DateInput(), label="First date")
    weight = forms.IntegerField(min_value=1, max_value=10, initial=1, label="Effort points",
                                help_text="1 = small, 10 = big. Bigger tasks move project progress more.")
    files = MultipleFileField(required=False, label="Attach files (PDF, image, Word, CSV, Excel)")
    skip_dates = forms.CharField(required=False, max_length=8000, label="Days this job should NOT happen",
                                 widget=forms.TextInput(attrs={"placeholder": "2026-11-03, 2026-11-10"}),
                                 help_text="Optional. Add each day separately - they do not have to be in a row.")
    checkpoints = forms.CharField(required=False, max_length=8000, label="Checkpoints",
                                  widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Oil level is between MIN and MAX\nNo leaks around the seals\nGuard is back in place"}),
                                  help_text="Optional. One checkpoint per line. Whoever marks the job done must tick every one, and the History keeps the list.")
    # a real radio group: the template and the JavaScript both rely on <input type="radio" name="kind">
    kind = forms.ChoiceField(choices=[(Job.ONCE, "One time"), (Job.REPEAT, "Repeats")], initial=Job.ONCE,
                             widget=forms.RadioSelect, label="How often?")

    class Meta:
        model = Job
        fields = ["title", "equipment", "kind", "every", "unit", "end_date", "assignee", "assigned_group", "evidence_required", "weight", "instructions"]
        widgets = {"end_date": DateInput(), "instructions": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *a, project=None, **kw):
        super().__init__(*a, **kw)
        self.project = project or (self.instance.project if self.instance.pk and self.instance.project_id else None)
        self.fields["equipment"].queryset = Equipment.objects.filter(active=True)
        self.fields["equipment"].required = False
        self.fields["assignee"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["assignee"].empty_label = "— nobody in particular —"
        self.fields["assignee"].label = "Assign to a person"
        self.fields["assigned_group"].queryset = Group.objects.all()
        self.fields["assigned_group"].empty_label = "— no group —"
        self.fields["assigned_group"].label = "…or assign to a group"
        self.fields["assigned_group"].help_text = "Every member of the group sees the job in their own calendar. Whoever does it first marks it done for all."
        self.fields["every"].required = False
        self.fields["unit"].required = False
        self.fields["evidence_required"].label = "A photo or file is required to mark this job done"
        self.fields["evidence_required"].help_text = "Tick this to make the upload compulsory. Leave it off and the upload stays optional."
        if self.project:                                   # a project task is always a one-off
            for f in ("kind", "every", "unit", "end_date", "skip_dates"):
                del self.fields[f]
            self.fields["equipment"].label = "Related equipment (optional)"
            self.fields["due_date"].required = True
        if self.instance.pk:
            self.fields["checkpoints"].initial = checklist.format_checkpoints(self.instance.checkpoints)
            if self.instance.kind == Job.ONCE:
                self.fields["due_date"].initial = self.instance.start_date
            else:
                self.fields["first_date"].initial = self.instance.start_date
                if "skip_dates" in self.fields:
                    self.fields["skip_dates"].initial = datelist.format_date_list(
                        r.start_date for r in self.instance.skip_ranges.filter(start_date=models.F("end_date")))

    def clean_skip_dates(self):
        try:
            return datelist.parse_date_list(self.cleaned_data.get("skip_dates"))
        except ValueError as e:
            raise ValidationError(str(e))

    def clean_checkpoints(self):
        try:
            items = checklist.parse_checkpoints(self.cleaned_data.get("checkpoints"))
        except ValueError as e:
            raise ValidationError(str(e))
        self.instance.checkpoints = items                       # not a form-model field, so set it by hand
        return items

    def clean_files(self):
        return check_uploaded_files(self.cleaned_data.get("files") or [])

    def clean(self):
        d = super().clean()
        if d.get("assignee") and d.get("assigned_group"):
            self.add_error("assigned_group", "Choose a person or a group, not both.")
        if self.project:
            if not d.get("due_date"):
                self.add_error("due_date", "Pick a due date.")
            else:
                self.instance.start_date = d["due_date"]
            self.instance.kind = Job.ONCE
            self.instance.project = self.project
            return d
        if not d.get("equipment"):
            self.add_error("equipment", "Choose the equipment.")
        if d.get("kind") == Job.REPEAT:
            if not d.get("first_date"):
                self.add_error("first_date", "Pick the first date.")
            else:
                self.instance.start_date = d["first_date"]
            if not d.get("every") or d["every"] < 1:
                self.add_error("every", "Enter a number of 1 or more.")
            if not d.get("unit"):
                self.add_error("unit", "Choose days, weeks or months.")
            if d.get("end_date") and d.get("first_date") and d["end_date"] < d["first_date"]:
                self.add_error("end_date", "The end date is before the first date.")
        else:
            if not d.get("due_date"):
                self.add_error("due_date", "Pick a due date.")
            else:
                self.instance.start_date = d["due_date"]
            d["every"], d["unit"], d["end_date"] = None, "", None
        return d


class GroupForm(forms.ModelForm):
    """Name only. People are added / removed on the group's own page (search + buttons), which scales to any number of users."""
    class Meta:
        model = Group
        fields = ["name"]

    def clean_name(self):
        n = " ".join(self.cleaned_data["name"].split())
        qs = Group.objects.filter(name__iexact=n)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("A group with that name already exists.")
        return n


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description", "owner", "due_date"]
        widgets = {"due_date": DateInput(), "description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["owner"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["owner"].empty_label = "— nobody —"


class AttachForm(forms.Form):
    files = MultipleFileField(required=True, label="Files")

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        if not files:
            raise ValidationError("Choose at least one file.")
        return check_uploaded_files(files)


# ---------------------------------------------------------------- exceptions (skip many days / move one day)
class SkipForm(forms.Form):
    """Skip every occurrence of ONE repeating job between two dates (both included)."""
    job = forms.ModelChoiceField(Job.objects.filter(kind=Job.REPEAT))
    start_date = forms.DateField(widget=DateInput(), label="From")
    end_date = forms.DateField(widget=DateInput(), label="To (last day, included)")
    reason = forms.CharField(max_length=200, required=False)

    def clean(self):
        d = super().clean()
        s, e = d.get("start_date"), d.get("end_date")
        if s and e:
            if e < s:
                self.add_error("end_date", "The last day is before the first day.")
            elif (e - s).days + 1 > engine.MAX_SKIP_SPAN_DAYS:
                self.add_error("end_date", "A skipped period can be at most one year.")
        return d


class MoveForm(forms.Form):
    job = forms.ModelChoiceField(Job.objects.filter(kind=Job.REPEAT))
    orig_date = forms.DateField()
    new_date = forms.DateField(widget=DateInput(), label="New date")
    reason = forms.CharField(max_length=200, required=False)

    def clean(self):
        from .models import Completion
        d = super().clean()
        job, o, n = d.get("job"), d.get("orig_date"), d.get("new_date")
        if job and o:
            if not engine.valid_occurrence(job, o):
                self.add_error("orig_date", "That date is not part of this job's schedule.")
            elif Completion.objects.filter(job=job, due_date=o).exists():
                self.add_error("orig_date", "That one is already done - undo the completion first.")
        if o and n and (n == o or abs((n - o).days) > engine.MOVE_LIMIT_DAYS):
            self.add_error("new_date", f"Pick a different date within {engine.MOVE_LIMIT_DAYS} days of the original.")
        return d


class DoneForm(forms.Form):
    """Everything the server checks before a job counts as done: the date is real, repeating jobs are not finished early,
    a compulsory photo/file is attached, and every checkpoint is ticked. Nothing here trusts the browser."""
    job = forms.ModelChoiceField(Job.objects.all())
    due_date = forms.DateField()
    note = forms.CharField(required=False, max_length=4000, widget=forms.Textarea(attrs={"rows": 3}))
    evidence = forms.FileField(required=False, label="Photo or document")

    def __init__(self, *a, user=None, **kw):
        super().__init__(*a, **kw)
        self.user = user
        self.state = "ok"

    def clean_evidence(self):
        f = self.cleaned_data.get("evidence")
        if f:
            check_uploaded_file(f)
        return f

    def clean(self):
        from .models import MoveException
        from .permissions import is_admin
        d = super().clean()
        job, due = d.get("job"), d.get("due_date")
        if not (job and due):
            return d
        if not engine.valid_occurrence(job, due):
            self.add_error("due_date", "That date is not part of this job's schedule.")
            return d
        # repeating jobs: only inside their own day / week / month / year
        move = MoveException.objects.filter(job=job, orig_date=due).first()
        d["planned"] = planned = move.new_date if move else due
        self.state, first, last = engine.finish_state(job, planned, today())
        if self.state == "early":
            raise ValidationError(f"\u201c{job.title}\u201d cannot be finished in advance. It opens on {first:%a %d %b %Y}"
                                  + (f" and stays open until {last:%d %b %Y}." if last != first else "."))
        if self.state == "closed":
            if not (self.user and is_admin(self.user)):
                raise ValidationError(f"The time for this one ended on {last:%a %d %b %Y}. Ask an admin to record it as a late entry.")
            if not (d.get("note") or "").strip():
                self.add_error("note", "Say why this is being recorded late.")
        if job.evidence_required and not d.get("evidence"):
            self.add_error("evidence", "A photo or file is required for this job.")
        d["checks"], missing = checklist.tick_snapshot(job.checkpoints, self.data.getlist("checks") if hasattr(self.data, "getlist") else [])
        if missing:
            more = "\u2026" if len(missing) > 3 else ""
            self.add_error(None, "Tick every checkpoint first. Still open: " + "; ".join(missing[:3]) + more)
        return d


class ExportForm(forms.Form):
    date_from = forms.DateField(widget=DateInput(), label="From")
    date_to = forms.DateField(widget=DateInput(), label="To")
    format = forms.ChoiceField(choices=[("xlsx", "Excel (.xlsx)"), ("csv", "CSV")], initial="xlsx")
    SCOPES = [("all", "All jobs"), ("mine", "Assigned to me (or my groups)"), ("by_me", "Assigned by me")]
    scope = forms.ChoiceField(choices=SCOPES, initial="all")
    project = forms.ModelChoiceField(Project.objects.all(), required=False, empty_label="All projects / no project")
    person = forms.ModelChoiceField(User.objects.all(), required=False, empty_label="Anyone")
    job = forms.CharField(required=False, max_length=80, label="Job name",
                          widget=forms.TextInput(attrs={"placeholder": "Part of the name is enough", "autocomplete": "off"}))

    def clean(self):
        d = super().clean()
        a, b = d.get("date_from"), d.get("date_to")
        if a and b:
            if b < a:
                self.add_error("date_to", "'To' is before 'From'.")
            elif (b - a).days > 366 * 3:
                self.add_error("date_to", "Please choose a period of 3 years or less.")
        return d
