"""Tracker behaviour that is easy to break and expensive to get wrong.

    python -m unittest discover tests

Each test runs against a temporary repo root, so it never touches the real
database or the real application folders.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402


class TrackerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-test-"))
        self._real_repo = tracker.REPO
        self._real_db = tracker.DB_PATH

        # Point the module at a throwaway repo laid out like the real one.
        tracker.REPO = self.tmp
        tracker.DB_PATH = self.tmp / "tracker" / "careerforge.db"
        for stage in tracker.STAGES:
            (self.tmp / stage).mkdir(parents=True)
        (self.tmp / "config").mkdir()
        shutil.copy(
            self._real_repo / "config" / "config.example.toml",
            self.tmp / "config" / "config.toml",
        )
        tracker.load_config(force=True)

        tracker.init_db()
        self.cfg = tracker.load_config()
        self.conn = tracker.connect()

    def tearDown(self) -> None:
        self.conn.close()
        tracker.REPO = self._real_repo
        tracker.DB_PATH = self._real_db
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, company="Acme", role="Frontend Engineer", url=None, **kw):
        with self.conn:
            return tracker.add_application(
                self.conn, self.cfg, company=company, role=role, url=url, **kw
            )

    # -- deduplication -----------------------------------------------------

    def test_duplicate_url_is_refused(self):
        self.add(url="https://example.com/j/1")
        with self.assertRaises(TrackerError) as ctx:
            self.add(url="https://example.com/j/1")
        self.assertIn("duplicate", str(ctx.exception))

    def test_duplicate_company_and_role_is_refused_without_url(self):
        self.add(company="Acme", role="Frontend Engineer")
        with self.assertRaises(TrackerError):
            self.add(company="acme", role="frontend engineer")

    def test_force_allows_a_second_application(self):
        self.add(url="https://example.com/j/1")
        row = self.add(url="https://example.com/j/1", force=True)
        self.assertEqual(row["slug"], "acme_frontend-engineer")

    def test_second_role_at_the_same_company_gets_its_own_slug(self):
        first = self.add(role="Frontend Engineer")
        second = self.add(role="Engineering Manager")
        self.assertEqual(first["slug"], "acme")
        self.assertEqual(second["slug"], "acme_engineering-manager")

    # -- folder lifecycle --------------------------------------------------

    def test_status_change_moves_the_folder(self):
        row = self.add()
        (self.tmp / "applications" / row["slug"]).mkdir()
        (self.tmp / "applications" / row["slug"] / "job.md").write_text("x")

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertFalse((self.tmp / "applications" / row["slug"]).exists())
        self.assertTrue((self.tmp / "processing" / row["slug"] / "job.md").exists())

    def test_collision_refuses_and_changes_nothing(self):
        row = self.add()
        (self.tmp / "applications" / row["slug"]).mkdir()
        (self.tmp / "rejected" / row["slug"]).mkdir()  # name already taken

        with self.assertRaises(TrackerError) as ctx:
            with self.conn:
                tracker.set_status(self.conn, self.cfg, row["slug"], "rejected")
        self.assertIn("already exists", str(ctx.exception))

        # Neither the folder nor the status moved.
        self.assertTrue((self.tmp / "applications" / row["slug"]).exists())
        after = tracker.resolve(self.conn, row["slug"])
        self.assertEqual(after["status"], "draft")

    def test_missing_folder_is_not_an_error(self):
        row = self.add()
        with self.conn:
            _, note = tracker.set_status(self.conn, self.cfg, row["slug"], "applied")
        self.assertIn("no folder", note)

    # -- optimistic locking ------------------------------------------------

    def test_stale_write_is_refused(self):
        row = self.add()
        with self.assertRaises(TrackerError) as ctx:
            with self.conn:
                tracker.set_status(
                    self.conn, self.cfg, row["slug"], "applied",
                    expected_updated_at="1999-01-01 00:00:00",
                )
        self.assertIn("changed since", str(ctx.exception))

    # -- validation --------------------------------------------------------

    def test_unknown_status_is_refused(self):
        row = self.add()
        with self.assertRaises(TrackerError):
            with self.conn:
                tracker.set_status(self.conn, self.cfg, row["slug"], "nonsense")

    def test_unknown_event_type_is_refused(self):
        row = self.add()
        with self.assertRaises(TrackerError):
            with self.conn:
                tracker.add_event(self.conn, self.cfg, row["slug"], "teleport")

    # -- queries -----------------------------------------------------------

    def test_stage_filter_follows_the_configured_mapping(self):
        a = self.add(company="Acme")
        b = self.add(company="Globex")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, b["slug"], "interview")

        applications = tracker.list_applications(self.conn, self.cfg, stage="applications")
        processing = tracker.list_applications(self.conn, self.cfg, stage="processing")
        self.assertEqual([r["slug"] for r in applications], [a["slug"]])
        self.assertEqual([r["slug"] for r in processing], [b["slug"]])

    def test_board_columns_cover_every_configured_status(self):
        self.add()
        data = tracker.board_data(self.conn, self.cfg)
        self.assertEqual(
            [c["id"] for c in data["columns"]],
            [s["id"] for s in self.cfg.statuses],
        )
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["orphans"], [])

    def test_slugify_folds_accents_and_punctuation(self):
        self.assertEqual(tracker.slugify("Nestl" + chr(233) + " S.A."), "nestle-s-a")
        self.assertEqual(tracker.slugify("  Acme   Corp!  "), "acme-corp")
        self.assertEqual(tracker.slugify("***"), "unnamed")

    # -- deadlines, location, fit ------------------------------------------

    def test_deadline_parsing_keeps_only_real_dates(self):
        for raw, expected in [
            ("2026-09-01", "2026-09-01"),
            ("01.09.2026", "2026-09-01"),
            ("1 September 2026", "2026-09-01"),
            ("Deadline: 2026-09-15 (or until filled)", "2026-09-15"),
            # Free text must not be stored: it would make the column unsortable
            # and every deadline comparison a lie.
            ("ASAP", None),
            ("rolling", None),
            ("until filled", None),
            ("", None),
            (None, None),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(tracker.parse_deadline(raw), expected)

    def test_expired_filter_ignores_closed_applications(self):
        live = self.add(company="Acme", deadline="2000-01-01")
        closed = self.add(company="Globex", deadline="2000-01-01")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, closed["slug"], "rejected")

        expired = tracker.list_applications(self.conn, self.cfg, expired=True)
        self.assertEqual([r["slug"] for r in expired], [live["slug"]])

    def test_future_deadline_is_not_expired(self):
        row = self.add(deadline="2099-01-01")
        self.assertEqual(
            tracker.list_applications(self.conn, self.cfg, expired=True), []
        )
        self.assertEqual(tracker.resolve(self.conn, row["slug"])["is_expired"], 0)

    def test_unknown_location_verdict_is_refused(self):
        with self.assertRaises(TrackerError):
            self.add(location_verdict="maybe")

    def test_fit_strengths_and_gaps_round_trip_as_lists(self):
        row = self.add(
            fit_score=72,
            fit_strengths=["Vue 3", "design systems"],
            fit_gaps=["no Kubernetes"],
        )
        d = tracker.enrich(dict(tracker.resolve(self.conn, row["slug"])), self.cfg)
        self.assertEqual(d["fit_score"], 72)
        self.assertEqual(d["fit_strengths"], ["Vue 3", "design systems"])
        self.assertEqual(d["fit_gaps"], ["no Kubernetes"])

    def test_empty_fit_lists_store_as_nothing(self):
        row = self.add(fit_strengths=[], fit_gaps=["  "])
        d = tracker.enrich(dict(tracker.resolve(self.conn, row["slug"])), self.cfg)
        self.assertEqual(d["fit_strengths"], [])
        self.assertEqual(d["fit_gaps"], [])

    # -- staleness ---------------------------------------------------------

    def stale_slugs(self):
        return [
            r["slug"]
            for r in tracker.list_applications(self.conn, self.cfg, stale=True)
        ]

    def age(self, row, days=400):
        """Backdate an application so it counts as silent."""
        old = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat(sep=" ", timespec="seconds")
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET created_at = ?, updated_at = ? WHERE id = ?",
                (old, old, row["id"]),
            )

    def test_an_interview_gone_quiet_is_stale(self):
        # The costly kind of silence: they replied, a call happened, and then
        # nothing. Restricting --stale to the `applications` stage hid every
        # status under `processing`, which is exactly this one.
        row = self.add(company="Acme")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")
        self.age(row)
        self.assertEqual(self.stale_slugs(), [row["slug"]])

    def test_a_closed_application_is_never_stale(self):
        row = self.add(company="Globex")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "rejected")
        self.age(row)
        self.assertEqual(self.stale_slugs(), [])

    def test_a_recent_application_is_not_stale(self):
        self.add(company="Initech")
        self.assertEqual(self.stale_slugs(), [])

    # -- migrations --------------------------------------------------------

    def test_fresh_database_records_migrations_without_running_them(self):
        # setUp built this database from schema.sql, which already contains
        # everything the migrations would add. Replaying them would fail on
        # duplicate columns, so they must be recorded as a baseline instead.
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        on_disk = {p.name for p in tracker.MIGRATIONS_DIR.glob("*.sql")}
        self.assertEqual(recorded, on_disk)

    def columns(self, table="applications"):
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def use_migrations(self, sql, name="900_test.sql"):
        """Point the module at a throwaway migrations directory."""
        d = self.tmp / "migrations"
        d.mkdir(exist_ok=True)
        (d / name).write_text(sql, encoding="utf-8")
        self.addCleanup(setattr, tracker, "MIGRATIONS_DIR", tracker.MIGRATIONS_DIR)
        tracker.MIGRATIONS_DIR = d
        return d

    def test_a_migration_that_fails_halfway_leaves_nothing_behind(self):
        # executescript() commits before it runs, so `with conn:` rolled back
        # nothing: the statements before the failure stayed, the migrations row
        # never landed, and every later init replayed the file and died on the
        # duplicate column -- unrecoverable without hand-editing the database.
        self.use_migrations(
            "ALTER TABLE applications ADD COLUMN probe_one TEXT;\n"
            "ALTER TABLE applications ADD COLUMN probe_two TEXT;\n"
            "ALTER TABLE applications ADD COLUMN probe_one TEXT;\n"
        )
        with self.assertRaises(Exception):
            tracker.apply_migrations(self.conn)

        cols = self.columns()
        self.assertNotIn("probe_one", cols)
        self.assertNotIn("probe_two", cols)
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        self.assertNotIn("900_test.sql", recorded)

    def test_the_repaired_migration_then_applies_cleanly(self):
        d = self.use_migrations(
            "ALTER TABLE applications ADD COLUMN probe_one TEXT;\n"
            "ALTER TABLE applications ADD COLUMN probe_one TEXT;\n"
        )
        with self.assertRaises(Exception):
            tracker.apply_migrations(self.conn)

        (d / "900_test.sql").write_text(
            "ALTER TABLE applications ADD COLUMN probe_one TEXT;\n", encoding="utf-8"
        )
        self.assertEqual(tracker.apply_migrations(self.conn), ["900_test.sql"])
        self.assertIn("probe_one", self.columns())

    def test_a_good_migration_records_itself(self):
        self.use_migrations("ALTER TABLE applications ADD COLUMN probe_one TEXT;\n")
        self.assertEqual(tracker.apply_migrations(self.conn), ["900_test.sql"])
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        self.assertIn("900_test.sql", recorded)
        # And it runs exactly once.
        self.assertEqual(tracker.apply_migrations(self.conn), [])

    # -- .env --------------------------------------------------------------

    def test_dotenv_loads_missing_keys_only(self):
        # The real environment must always win over the file: a value set for
        # one shell session must not be shadowed by an old line in .env.
        import os
        (self.tmp / ".env").write_text(
            "# comment\n"
            "CF_TEST_NEW=from_file\n"
            'CF_TEST_QUOTED="with spaces"\n'
            "CF_TEST_EXISTING=shadowed\n"
            "not a kv line\n",
            encoding="utf-8",
        )
        os.environ["CF_TEST_EXISTING"] = "from_environment"
        tracker._dotenv_loaded = False
        try:
            tracker.load_dotenv()
            self.assertEqual(os.environ.get("CF_TEST_NEW"), "from_file")
            self.assertEqual(os.environ.get("CF_TEST_QUOTED"), "with spaces")
            self.assertEqual(os.environ.get("CF_TEST_EXISTING"), "from_environment")
        finally:
            for key in ("CF_TEST_NEW", "CF_TEST_QUOTED", "CF_TEST_EXISTING"):
                os.environ.pop(key, None)
            tracker._dotenv_loaded = False

    def test_dotenv_missing_file_is_not_an_error(self):
        tracker._dotenv_loaded = False
        try:
            tracker.load_dotenv()  # self.tmp has no .env in this test
        finally:
            tracker._dotenv_loaded = False

    def test_init_is_idempotent(self):
        before = self.add()
        tracker.init_db()
        tracker.init_db()
        after = tracker.resolve(self.conn, before["slug"])
        self.assertEqual(after["id"], before["id"])
        self.assertIn(
            "is_expired",
            [d[0] for d in self.conn.execute(
                "SELECT * FROM applications_view LIMIT 1").description],
        )


if __name__ == "__main__":
    unittest.main()
