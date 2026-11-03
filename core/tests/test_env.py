"""Pure Python (no Django): .env parsing, secret-key rules, database selection, make_env script."""
import importlib.util, os, stat, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from config import env as E   # noqa: E402


def _load_make_env():
    spec = importlib.util.spec_from_file_location("make_env", ROOT / "scripts" / "make_env.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class Parse(unittest.TestCase):
    def test_basic_lines(self):
        t = '# comment\n\nA=1\nexport B = two words \nC="quoted # not a comment"\nD=\'single\'\nE=value # trailing\nF=postgres://u:p@h:5432/db?sslmode=require\n'
        self.assertEqual(E.parse_env_text(t), {"A": "1", "B": "two words", "C": "quoted # not a comment", "D": "single",
                                               "E": "value", "F": "postgres://u:p@h:5432/db?sslmode=require"})

    def test_equals_sign_inside_value(self):
        self.assertEqual(E.parse_env_text("K=a=b=c")["K"], "a=b=c")

    def test_bad_lines_are_reported_with_line_number(self):
        with self.assertRaisesRegex(E.ConfigError, "line 2"):
            E.parse_env_text("A=1\nnonsense\n")
        with self.assertRaisesRegex(E.ConfigError, "not a valid variable name"):
            E.parse_env_text("1BAD=x")

    def test_real_environment_wins_over_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"; f.write_text("A=from-file\nB=from-file\n")
            environ = {"A": "from-env"}
            E.load_dotenv(f, environ)
            self.assertEqual(environ, {"A": "from-env", "B": "from-file"})

    def test_missing_file_is_fine(self):
        self.assertEqual(E.load_dotenv("/nonexistent/.env", {}), {})

    def test_the_shipped_sample_parses(self):
        v = E.parse_env_text((ROOT / ".env.example").read_text())
        for k in ("DJANGO_SECRET_KEY", "DB_ENGINE", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT", "DJANGO_ALLOWED_HOSTS"):
            self.assertIn(k, v)


class SecretKey(unittest.TestCase):
    def test_unset_means_fall_back(self):
        self.assertIsNone(E.secret_key_from_env({}))

    def test_sample_value_is_refused(self):
        for bad in ("change-me", "CHANGE-ME", "django-insecure-abc", "changeme"):
            with self.assertRaises(E.ConfigError, msg=bad):
                E.secret_key_from_env({"DJANGO_SECRET_KEY": bad})

    def test_short_key_is_refused(self):
        with self.assertRaisesRegex(E.ConfigError, "too short"):
            E.secret_key_from_env({"DJANGO_SECRET_KEY": "x" * 20})

    def test_good_key_is_accepted(self):
        k = "k" * 60
        self.assertEqual(E.secret_key_from_env({"DJANGO_SECRET_KEY": k}), k)


class Database(unittest.TestCase):
    def db(self, **env):
        return E.database_settings(env, "/app", "/app/data")["default"]

    def test_default_is_sqlite(self):
        c = self.db()
        self.assertTrue(c["ENGINE"].endswith("sqlite3")); self.assertEqual(c["NAME"], "/app/data/db.sqlite3")

    def test_postgres_from_separate_values(self):
        c = self.db(DB_ENGINE="postgres", POSTGRES_DB="m", POSTGRES_USER="u", POSTGRES_PASSWORD="p", POSTGRES_HOST="db.internal", POSTGRES_PORT="6543")
        self.assertEqual((c["ENGINE"], c["NAME"], c["USER"], c["PASSWORD"], c["HOST"], c["PORT"]),
                         ("django.db.backends.postgresql", "m", "u", "p", "db.internal", "6543"))
        self.assertEqual(c["OPTIONS"]["sslmode"], "prefer"); self.assertTrue(c["CONN_HEALTH_CHECKS"])

    def test_postgres_is_picked_automatically_when_postgres_values_exist(self):
        self.assertTrue(self.db(POSTGRES_DB="m", POSTGRES_USER="u")["ENGINE"].endswith("postgresql"))

    def test_database_url_with_encoded_password_and_sslmode(self):
        c = self.db(DATABASE_URL="postgres://app:p%40ss%2Fw0rd@pg.example.com:5433/maint?sslmode=require")
        self.assertEqual((c["USER"], c["PASSWORD"], c["HOST"], c["PORT"], c["NAME"]), ("app", "p@ss/w0rd", "pg.example.com", "5433", "maint"))
        self.assertEqual(c["OPTIONS"]["sslmode"], "require")

    def test_database_url_must_be_postgres_and_complete(self):
        for bad in ("mysql://u:p@h/db", "postgres://u:p@/db", "postgres://u:p@h/"):
            with self.assertRaises(E.ConfigError, msg=bad):
                self.db(DATABASE_URL=bad)

    def test_missing_values_are_named(self):
        with self.assertRaisesRegex(E.ConfigError, "POSTGRES_DB and POSTGRES_USER"):
            self.db(DB_ENGINE="postgres")

    def test_remote_server_needs_a_real_password_and_valid_sslmode(self):
        base = dict(DB_ENGINE="postgres", POSTGRES_DB="m", POSTGRES_USER="u", POSTGRES_HOST="db.example.com")
        with self.assertRaisesRegex(E.ConfigError, "password is required"):
            self.db(**base)
        with self.assertRaisesRegex(E.ConfigError, "sample value"):
            self.db(**base, POSTGRES_PASSWORD="change-me")
        with self.assertRaisesRegex(E.ConfigError, "DB_SSLMODE"):
            self.db(**base, POSTGRES_PASSWORD="good-one", DB_SSLMODE="whatever")
        self.assertEqual(self.db(**base, POSTGRES_PASSWORD="good-one", DB_SSLMODE="verify-full")["OPTIONS"]["sslmode"], "verify-full")

    def test_local_postgres_may_use_trust_auth_without_password(self):
        self.assertEqual(self.db(DB_ENGINE="postgres", POSTGRES_DB="m", POSTGRES_USER="u")["HOST"], "localhost")

    def test_unknown_engine_and_bad_numbers(self):
        with self.assertRaisesRegex(E.ConfigError, "DB_ENGINE"):
            self.db(DB_ENGINE="oracle")
        with self.assertRaisesRegex(E.ConfigError, "POSTGRES_PORT"):
            self.db(DB_ENGINE="postgres", POSTGRES_DB="m", POSTGRES_USER="u", POSTGRES_PORT="abc")

    def test_sample_env_with_placeholders_is_rejected_for_the_secret_key_first(self):
        v = E.parse_env_text((ROOT / ".env.example").read_text())
        with self.assertRaises(E.ConfigError):
            E.secret_key_from_env(v)


class MakeEnv(unittest.TestCase):
    def setUp(self):
        self.m = _load_make_env()
        self.sample = (ROOT / ".env.example").read_text()

    def test_generates_strong_unique_keys_and_keeps_everything_else(self):
        t1, k1, pw1 = self.m.build(self.sample); t2, k2, _ = self.m.build(self.sample)
        self.assertNotEqual(k1, k2); self.assertGreaterEqual(len(k1), 80)
        v = E.parse_env_text(t1)
        self.assertEqual(E.secret_key_from_env(v), k1)                       # the generated key passes the loader's own rules
        self.assertEqual(v["POSTGRES_PASSWORD"], pw1)
        self.assertEqual(E.parse_env_text(self.sample)["POSTGRES_DB"], v["POSTGRES_DB"])
        self.assertTrue(E.database_settings(v, "/a", "/a/data")["default"]["ENGINE"].endswith("postgresql"))

    def test_sqlite_switch(self):
        t, _, _ = self.m.build(self.sample, sqlite=True)
        self.assertTrue(E.database_settings(E.parse_env_text(t), "/a", "/a/data")["default"]["ENGINE"].endswith("sqlite3"))

    def test_generated_file_is_private_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / ".env.example").write_text(self.sample)
            self.m.ROOT = root
            self.assertEqual(self.m.main([]), 0)
            f = root / ".env"; self.assertTrue(f.exists())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
            before = f.read_text()
            self.assertEqual(self.m.main([]), 1); self.assertEqual(f.read_text(), before)          # refuses to overwrite
            self.assertEqual(self.m.main(["--force"]), 0); self.assertNotEqual(f.read_text(), before)


class Hygiene(unittest.TestCase):
    def test_env_file_is_git_ignored_but_sample_is_not(self):
        g = (ROOT / ".gitignore").read_text().splitlines()
        self.assertIn(".env", g); self.assertIn("!.env.example", g)

    def test_postgres_driver_is_a_requirement(self):
        self.assertIn("psycopg", (ROOT / "requirements.txt").read_text())

    def test_sample_contains_no_real_looking_secret(self):
        for k, v in E.parse_env_text((ROOT / ".env.example").read_text()).items():
            if "SECRET" in k or "PASSWORD" in k:
                self.assertIn(v.lower(), E.PLACEHOLDERS, k)

    def test_settings_use_the_loader_and_not_a_hardcoded_secret(self):
        s = (ROOT / "config" / "settings.py").read_text()
        self.assertIn("load_dotenv", s); self.assertIn("database_settings", s); self.assertIn("secret_key_from_env", s)

    def test_compose_does_not_publish_postgres_to_the_network(self):
        c = (ROOT / "docker-compose.yml").read_text()
        self.assertIn('"127.0.0.1:', c); self.assertNotIn("POSTGRES_PASSWORD: postgres", c)


class FormJavaScript(unittest.TestCase):
    """Runs every core/tests/js/*.test.js with Node (skipped when Node is not installed)."""
    def run_js(self, name):
        import shutil, subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js not installed")
        r = subprocess.run([node, str(ROOT / "core" / "tests" / "js" / name)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_one_time_repeat_switch(self):
        self.run_js("kind_toggle.test.js")

    def test_skip_days_picker(self):
        self.run_js("skip_dates.test.js")

    def test_hide_menu_and_done_dialog(self):
        self.run_js("menu_and_done.test.js")

    def test_every_js_test_file_is_covered_here(self):
        files = sorted(p.name for p in (ROOT / "core" / "tests" / "js").glob("*.test.js"))
        self.assertEqual(files, ["kind_toggle.test.js", "menu_and_done.test.js", "skip_dates.test.js"])


if __name__ == "__main__":
    unittest.main()
