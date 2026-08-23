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


if __name__ == "__main__":
    unittest.main()
