"""Identity in the Notion mirror: which page is which local row.

    python -m unittest discover -s tests

`import` used to append every Notion event on every run, because events were
keyed on nothing while applications were keyed on their URL. These tests pin
the identity rules down; they need no network, only the pure helpers and a
throwaway database.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import notion_sync  # noqa: E402
import tracker  # noqa: E402


PAGE = "1234567890abcdef1234567890abcdef"
VIEW = "fedcba0987654321fedcba0987654321"
EXPECTED = "12345678-90ab-cdef-1234-567890abcdef"


class NormaliseIdTestCase(unittest.TestCase):
    """What `provision --parent-page` accepts. No database, no network."""

    def test_a_database_url_yields_the_page_id_not_the_view_id(self):
        # The ?v= parameter is a view, and it is the last id in the string.
        self.assertEqual(
            notion_sync.normalise_id(f"https://www.notion.so/ws/Job-Tracker-{PAGE}?v={VIEW}"),
            EXPECTED,
        )

    def test_a_fragment_is_dropped_too(self):
        self.assertEqual(
            notion_sync.normalise_id(f"https://www.notion.so/ws/Job-Tracker-{PAGE}#block{VIEW}"),
            EXPECTED,
        )

    def test_a_slug_that_looks_like_hex_does_not_shadow_the_id(self):
        # "Name-Cafe-" is a run of hex characters and dashes; matching 36 of
        # those loosely used to swallow the front of the real id.
        self.assertEqual(
            notion_sync.normalise_id(f"https://www.notion.so/My-Page-Name-Cafe-{PAGE}"),
            EXPECTED,
        )

    def test_a_dashed_id_in_a_url_still_works(self):
        self.assertEqual(
            notion_sync.normalise_id(f"https://www.notion.so/ws/T-{EXPECTED}?v={VIEW}"),
            EXPECTED,
        )

    def test_a_bare_id_passes_through_in_either_shape(self):
        self.assertEqual(notion_sync.normalise_id(PAGE), EXPECTED)
        self.assertEqual(notion_sync.normalise_id(EXPECTED), EXPECTED)

    def test_no_id_at_all_is_a_TrackerError_not_a_traceback(self):
        # main() only handles TrackerError; a bare ValueError reached the user.
        for value in ("https://www.notion.so/no-id-here", "", "Cafe-Babe"):
            with self.assertRaises(notion_sync.TrackerError):
                notion_sync.normalise_id(value)


class EventIdentityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-notion-test-"))
        self._real_repo = tracker.REPO
        self._real_db = tracker.DB_PATH

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
        with self.conn:
            self.app = tracker.add_application(
                self.conn, self.cfg, company="Acme", role="Frontend Engineer", url=None
            )

    def tearDown(self) -> None:
        self.conn.close()
        tracker.REPO = self._real_repo
        tracker.DB_PATH = self._real_db
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def event(self, type_="screening", when="2026-08-25T14:00", outcome="passed",
              page_id=None):
        with self.conn:
            row = tracker.add_event(
                self.conn, self.cfg, self.app["slug"], type_, when, None, outcome
            )
            if page_id:
                self.conn.execute(
                    "UPDATE events SET notion_page_id = ? WHERE id = ?",
                    (page_id, row["id"]),
                )
        return row

    def find(self, page_id="page-1", type_="screening",
             when="2026-08-25T14:00:00.000+01:00", outcome="passed"):
        return notion_sync.existing_event_id(
            self.conn, page_id, int(self.app["id"]), type_, when, outcome
        )

    # -- the precision the two sides agree on ------------------------------

    def test_notion_offset_datetime_matches_what_tracker_stored(self):
        # Notion returns "…T14:00:00.000+01:00"; tracker.py stored "…T14:00".
        self.event(when="2026-08-25T14:00")
        self.assertIsNotNone(self.find(when="2026-08-25T14:00:00.000+01:00"))

    def test_a_different_time_on_the_same_day_is_a_different_event(self):
        self.event(when="2026-08-25T14:00")
        self.assertIsNone(self.find(when="2026-08-25T16:30:00.000+01:00"))

    def test_norm_when_trims_to_minutes_and_accepts_a_space_separator(self):
        self.assertEqual(notion_sync.norm_when("2026-08-25 14:00:00.000+01:00"),
                         "2026-08-25T14:00")
        self.assertEqual(notion_sync.norm_when("2026-08-25"), "2026-08-25")
        self.assertEqual(notion_sync.norm_when(None), "")

    # -- identity ----------------------------------------------------------

    def test_page_id_wins_over_the_field_match(self):
        tagged = self.event(when="2026-01-01", outcome="failed", page_id="page-1")
        self.assertEqual(self.find(page_id="page-1", when="2026-08-25T14:00"),
                         int(tagged["id"]))

    def test_an_event_that_is_not_here_yet_reads_as_new(self):
        self.assertIsNone(self.find())

    def test_an_event_already_claimed_by_another_page_is_not_reused(self):
        self.event(page_id="page-other")
        self.assertIsNone(self.find(page_id="page-1"))

    # -- the case a naive UNIQUE key would have broken ---------------------

    def test_same_type_and_day_but_a_different_outcome_stays_two_events(self):
        # Two follow-up emails the same evening: one answered, one not.
        self.event(type_="other", when="2026-08-19", outcome="passed",
                   page_id="page-a")
        second = self.find(page_id="page-b", type_="other",
                           when="2026-08-19", outcome="pending")
        self.assertIsNone(second)

    def test_the_matching_outcome_still_finds_its_own_event(self):
        self.event(type_="other", when="2026-08-19", outcome="pending")
        self.assertIsNotNone(
            self.find(page_id="page-b", type_="other",
                      when="2026-08-19", outcome="pending")
        )

    def test_an_event_with_no_outcome_matches_one_with_no_outcome(self):
        self.event(type_="manager", when="2026-08-19", outcome=None)
        self.assertIsNotNone(
            self.find(page_id="page-c", type_="manager", when="2026-08-19",
                      outcome=None)
        )


if __name__ == "__main__":
    unittest.main()
