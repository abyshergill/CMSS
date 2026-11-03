"""Upload validation (extension + real content). Pure Python."""
import io, unittest, zipfile

from core import filecheck as fc

MB = 1024 * 1024


def check(name, data, limit=10 * MB):
    return fc.validate_upload(name, io.BytesIO(data), len(data), limit)


def make_zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return b.getvalue()


DOCX = make_zip({"[Content_Types].xml": "<x/>", "word/document.xml": "<d/>"})
XLSX = make_zip({"[Content_Types].xml": "<x/>", "xl/workbook.xml": "<w/>"})


class Accepts(unittest.TestCase):
    def test_real_files(self):
        self.assertIsNone(check("manual.pdf", b"%PDF-1.7\n..."))
        self.assertIsNone(check("a.PNG", b"\x89PNG\r\n\x1a\nxxxx"))
        self.assertIsNone(check("a.jpg", b"\xff\xd8\xff\xe0xxxx"))
        self.assertIsNone(check("a.gif", b"GIF89a...."))
        self.assertIsNone(check("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "))
        self.assertIsNone(check("old.doc", fc.OLE + b"\x00" * 50))
        self.assertIsNone(check("old.xls", fc.OLE + b"\x00" * 50))
        self.assertIsNone(check("new.docx", DOCX))
        self.assertIsNone(check("new.xlsx", XLSX))
        self.assertIsNone(check("data.csv", b"a,b,c\n1,2,3\n"))
        self.assertIsNone(check("data.csv", "ชื่อ,ค่า\nก,1\n".encode("utf-8")))     # Thai text is fine

    def test_stream_is_rewound_so_django_can_save_it(self):
        f = io.BytesIO(b"%PDF-1.4 hello")
        fc.validate_upload("a.pdf", f, 14, MB)
        self.assertEqual(f.tell(), 0)


class Rejects(unittest.TestCase):
    def test_dangerous_extensions(self):
        for n in ("virus.exe", "a.php", "a.html", "a.svg", "a.js", "a.sh", "a.bat", "a.py", "a.docm", "a.xlsm", "a.zip", "noext", "a."):
            self.assertIsNotNone(check(n, b"%PDF-1.4 x"), n)

    def test_double_extension_uses_the_last_one(self):
        self.assertIsNotNone(check("report.pdf.exe", b"%PDF-1.4 x"))
        self.assertIsNone(check("report.exe.pdf", b"%PDF-1.4 x"))

    def test_content_must_match_type(self):
        self.assertIn("does not match", check("fake.pdf", b"MZ\x90\x00 this is an exe"))
        self.assertIn("does not match", check("fake.png", b"<html><script>alert(1)</script>"))
        self.assertIn("does not match", check("fake.jpg", b"%PDF-1.4"))
        self.assertIn("does not match", check("fake.docx", b"just text"))
        self.assertIn("does not match", check("fake.xlsx", DOCX))               # a docx renamed to xlsx
        self.assertIn("does not match", check("fake.docx", XLSX))

    def test_macro_enabled_ooxml_rejected(self):
        z = make_zip({"[Content_Types].xml": "<x/>", "xl/workbook.xml": "<w/>", "xl/vbaProject.bin": "evil"})
        self.assertIn("does not match", check("macro.xlsx", z))

    def test_zip_bomb_and_traversal(self):
        many = make_zip({"[Content_Types].xml": "<x/>", **{f"xl/s{i}.xml": "a" for i in range(fc.MAX_ZIP_ENTRIES + 5)}})
        self.assertIn("does not match", check("bomb.xlsx", many))
        trav = make_zip({"[Content_Types].xml": "<x/>", "xl/a.xml": "a", "../evil.txt": "x"})
        self.assertIn("does not match", check("t.xlsx", trav))

    def test_csv_with_binary_or_script(self):
        self.assertIn("does not match", check("a.csv", b"a,b\x00\x01\x02\x03,c"))
        self.assertIn("does not match", check("a.csv", b"<script>alert(1)</script>"))

    def test_size_rules(self):
        self.assertIn("empty", check("a.pdf", b""))
        self.assertIn("larger", check("a.pdf", b"%PDF-" + b"x" * 100, limit=50))

    def test_bad_names(self):
        self.assertIsNotNone(check("", b"%PDF-1"))
        self.assertIsNotNone(check("a\x00.pdf.exe", b"%PDF-1"))


class Names(unittest.TestCase):
    def test_display_name_strips_paths_and_control_chars(self):
        self.assertEqual(fc.display_name("../../etc/passwd"), "passwd")
        self.assertEqual(fc.display_name("C:\\temp\\evil.pdf"), "evil.pdf")
        self.assertEqual(fc.display_name("a\x00b\nc.pdf"), "abc.pdf")
        self.assertEqual(fc.display_name(""), "file")
        self.assertLessEqual(len(fc.display_name("x" * 500 + ".pdf")), 120)

    def test_extension(self):
        self.assertEqual(fc.extension("A.B.PDF"), "pdf")
        self.assertEqual(fc.extension("noext"), "")
        self.assertEqual(fc.extension("dir.pdf/file"), "")


if __name__ == "__main__":
    unittest.main()
