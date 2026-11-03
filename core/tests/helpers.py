import io, tempfile, zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Area, Equipment, Job, Profile

User = get_user_model()
PW = "Str0ng-Passw0rd!x"
MEDIA = tempfile.mkdtemp(prefix="maintain-test-")


def today():
    return timezone.localdate()


def d(n):
    return today() + timedelta(days=n)


def iso(x):
    return x.isoformat()


def pdf(name="manual.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n% test file\n", content_type="application/pdf")


def png(name="photo.png"):
    return SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\n" + b"0" * 32, content_type="image/png")


def xlsx(name="data.xlsx"):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>"); z.writestr("xl/workbook.xml", "<w/>")
    return SimpleUploadedFile(name, b.getvalue())


@override_settings(MEDIA_ROOT=MEDIA)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("boss", password=PW)
        cls.tech = User.objects.create_user("tina", password=PW)
        cls.tech2 = User.objects.create_user("tom", password=PW)
        cls.area = Area.objects.create(name="Line 1")
        cls.eq = Equipment.objects.create(code="E1", name="Press", area=cls.area)

    def as_user(self, user):
        self.client.logout()
        self.assertTrue(self.client.login(username=user.username, password=PW))
        return self.client

    def make_job(self, title="Job", kind="once", start=0, assignee=None, **kw):
        extra = dict(every=1, unit="week") if kind == "repeat" else {}
        extra.update(kw)
        return Job.objects.create(equipment=self.eq, title=title, kind=kind, start_date=d(start), assignee=assignee, **extra)
