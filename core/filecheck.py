"""Upload validation - pure Python. Checks extension AND file content, never trusts the browser."""
import io, os, re, zipfile

ALLOWED_EXT = {
    "pdf": "PDF", "png": "Image", "jpg": "Image", "jpeg": "Image", "gif": "Image", "webp": "Image",
    "doc": "Word", "docx": "Word", "csv": "CSV", "xls": "Excel", "xlsx": "Excel",
}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_ZIP_ENTRIES, MAX_ZIP_UNPACKED = 5000, 200 * 1024 * 1024
ACCEPT_ATTR = ",".join("." + e for e in sorted(ALLOWED_EXT))


def extension(name):
    base = os.path.basename(name.replace("\\", "/"))
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def display_name(name, limit=120):
    """Safe name to show/store as text: no path, no control characters."""
    base = os.path.basename((name or "file").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip() or "file"
    return base[:limit]


def _zip_ok(f, folder):
    try:
        with zipfile.ZipFile(f) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNPACKED:
                return False                                    # zip-bomb guard
            names = {i.filename for i in infos}
            if any(n.startswith(("/", "..")) or ".." in n.split("/") for n in names):
                return False
            if "[Content_Types].xml" not in names or not any(n.startswith(folder) for n in names):
                return False
            return not any(n.lower().endswith("vbaproject.bin") for n in names)   # macro-enabled
    except (zipfile.BadZipFile, OSError):
        return False


def validate_upload(name, fileobj, size, max_bytes):
    """Return None if OK, else a short human-readable reason."""
    if not name or "\x00" in name:
        return "Invalid file name."
    ext = extension(name)
    if ext not in ALLOWED_EXT:
        return "File type not allowed. Use PDF, image, Word, CSV or Excel."
    if size <= 0:
        return "File is empty."
    if size > max_bytes:
        return f"File is larger than {max_bytes // (1024 * 1024)} MB."
    pos = fileobj.tell()
    fileobj.seek(0)
    head = fileobj.read(65536)
    fileobj.seek(0)
    ok = True
    if ext == "pdf":
        ok = head.startswith(b"%PDF-")
    elif ext == "png":
        ok = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif ext in ("jpg", "jpeg"):
        ok = head.startswith(b"\xff\xd8\xff")
    elif ext == "gif":
        ok = head[:6] in (b"GIF87a", b"GIF89a")
    elif ext == "webp":
        ok = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    elif ext in ("doc", "xls"):
        ok = head.startswith(OLE)
    elif ext in ("docx", "xlsx"):
        ok = head.startswith(b"PK\x03\x04") and _zip_ok(fileobj, "word/" if ext == "docx" else "xl/")
        fileobj.seek(0)
    elif ext == "csv":
        ok = b"\x00" not in head and head.count(b"<script") == 0
        if ok and head:
            odd = sum(1 for b in head if b < 9 or 13 < b < 32)
            ok = odd / len(head) < 0.02
    fileobj.seek(pos if False else 0)
    return None if ok else "File content does not match its type."
