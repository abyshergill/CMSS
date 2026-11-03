from django.contrib import messages
from django.core.paginator import Paginator
from django.http import FileResponse, Http404

from .. import filecheck
from ..models import JobAttachment


PAGE_SIZES = (10, 25, 50, 100)


def paginate(request, items, default=25, key="page", per_key="per"):
    """One page of `items` (a queryset or list). ?page=N picks the page (bad numbers are harmless), ?per=10|25|50|100 the page size.
    Several lists on one screen use different `key`s, e.g. paginate(request, rows, key="hp")."""
    try:
        per = int(request.GET.get(per_key))
    except (TypeError, ValueError):
        per = default
    return Paginator(items, per if per in PAGE_SIZES else default).get_page(request.GET.get(key))


def form_errors(request, form):
    for field, errs in form.errors.items():
        label = "" if field == "__all__" else (form.fields[field].label or field.replace("_", " ").title()) + ": "
        for e in errs:
            messages.error(request, f"{label}{e}")


def save_attachments(job, files, user):
    for f in files:
        JobAttachment.objects.create(job=job, file=f, original_name=filecheck.display_name(f.name),
                                     size=f.size, uploaded_by=user)


def serve_file(fieldfile, name):
    """Stream a stored file. Images show inline; everything else downloads. Never executed by the browser."""
    try:
        fh = fieldfile.open("rb")
    except (FileNotFoundError, ValueError, OSError):
        raise Http404("File not found")
    inline = filecheck.extension(name) in filecheck.IMAGE_EXT
    resp = FileResponse(fh, as_attachment=not inline, filename=filecheck.display_name(name))
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return resp
