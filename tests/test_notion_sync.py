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


class MirrorTestCase(unittest.TestCase):
    """A throwaway repo with one application. No network, no real database."""

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


class EventIdentityTestCase(MirrorTestCase):
    """import: which Notion page is which local row."""

    def find(self, page_id="page-1", type_="screening",
             when="2026-08-25T14:00:00.000+01:00", outcome="passed",
             claimed=None):
        return notion_sync.existing_event_id(
            self.conn, page_id, int(self.app["id"]), type_, when, outcome, claimed
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

    # -- a dry run has to predict what the real run will do ----------------

    def test_a_dry_run_does_not_report_two_pages_as_one_present_event(self):
        # Both pages field-match the single local event. The real run adopts
        # the first and imports the second, so the preview must say so too.
        self.event(when="2026-08-25T14:00")
        claimed: set[int] = set()
        first = self.find(page_id="page-1", claimed=claimed)
        self.assertIsNotNone(first)
        claimed.add(first)
        self.assertIsNone(self.find(page_id="page-2", claimed=claimed))


class PushEventsTestCase(MirrorTestCase):
    """push: SQLite -> Notion, without touching Notion."""

    IDS = {"events": {"database_id": "db-events", "data_source_id": "ds-events"}}
    APP_PAGE = "app-page-1"

    def setUp(self) -> None:
        super().setUp()
        self.calls: list[tuple[str, str, dict]] = []
        self._real_request = notion_sync.request
        notion_sync.request = self.fake_request
        self.addCleanup(setattr, notion_sync, "request", self._real_request)

    def fake_request(self, method, path, payload=None):
        self.calls.append((method, path, payload or {}))
        return {"id": f"created-{len(self.calls)}"}

    def detail(self):
        return tracker.application_detail(self.conn, self.cfg, self.app["slug"])

    def push(self, index=None, dry_run=False):
        return notion_sync.push_events(
            self.conn, self.IDS, self.detail(), self.APP_PAGE,
            {} if index is None else index, dry_run,
        )

    def page_ids(self):
        return [
            r["notion_page_id"]
            for r in self.conn.execute("SELECT notion_page_id FROM events ORDER BY id")
        ]

    def test_an_event_notion_has_never_seen_is_created(self):
        self.event(when="2026-08-25T14:00")
        self.assertEqual(self.push(), (1, 0))
        self.assertEqual([c[0] for c in self.calls], ["POST"])
        self.assertEqual(self.calls[0][1], "/pages")
        # The page id comes back onto the local row, so the next push updates.
        self.assertEqual(self.page_ids(), ["created-1"])

    def test_the_created_page_carries_the_application_relation(self):
        self.event(when="2026-08-25T14:00", outcome="passed")
        self.push()
        props = self.calls[0][2]["properties"]
        self.assertEqual(props["Application"]["relation"], [{"id": self.APP_PAGE}])
        self.assertEqual(props["Date"]["date"]["start"], "2026-08-25T14:00:00")
        self.assertIn("Acme", props["Name"]["title"][0]["text"]["content"])
        self.assertIn("Outcome", props)

    def test_an_event_with_no_outcome_does_not_send_an_empty_select(self):
        self.event(type_="manager", when="2026-08-19", outcome=None)
        self.push()
        self.assertNotIn("Outcome", self.calls[0][2]["properties"])

    def test_pushing_the_same_event_twice_updates_rather_than_duplicates(self):
        self.event(when="2026-08-25T14:00")
        self.push()
        self.calls.clear()
        self.assertEqual(self.push(), (0, 1))
        self.assertEqual([c[0] for c in self.calls], ["PATCH"])
        self.assertEqual(self.calls[0][1], "/pages/created-1")

    def test_an_update_never_rewrites_the_title(self):
        # The tracker has no title field, so it has nothing better to say than
        # what is already on the page. On the live mirror 54 of 135 titles
        # carried what the Type select did not.
        self.event(when="2026-08-25T14:00")
        self.push()
        self.calls.clear()
        self.push()
        self.assertNotIn("Name", self.calls[0][2]["properties"])
        # The rest is still tracker-driven.
        for field in ("Type", "Date", "Participants", "Application"):
            self.assertIn(field, self.calls[0][2]["properties"])

    def test_an_adopted_page_keeps_its_title_too(self):
        self.event(when="2026-08-25T14:00")
        index = {
            notion_sync.event_key(
                self.APP_PAGE, self.cfg.label("event_types", "screening"),
                "2026-08-25T14:00", self.cfg.label("outcomes", "passed"),
            ): "hand-written-page"
        }
        self.push(index=index)
        self.assertNotIn("Name", self.calls[0][2]["properties"])

    def test_a_page_typed_into_notion_by_hand_is_adopted_not_duplicated(self):
        # Until push could write events, typing them into Notion was the only
        # way. Those pages have no local id and must not be doubled.
        self.event(when="2026-08-25T14:00")
        index = {
            notion_sync.event_key(
                self.APP_PAGE, self.cfg.label("event_types", "screening"),
                "2026-08-25T14:00:00.000+01:00", self.cfg.label("outcomes", "passed"),
            ): "hand-written-page"
        }
        self.assertEqual(self.push(index=index), (0, 1))
        self.assertEqual([c[0] for c in self.calls], ["PATCH"])
        self.assertEqual(self.calls[0][1], "/pages/hand-written-page")
        self.assertEqual(self.page_ids(), ["hand-written-page"])

    def test_two_events_differing_only_by_outcome_get_two_pages(self):
        # The case existing_event_id()'s docstring names: two follow-up emails
        # sent the same evening, one answered and one not. Keyed on three fields
        # the second PATCHed the page the first had just created, both local rows
        # ended up with the same notion_page_id, and one event left the mirror.
        self.event(type_="follow_up", when="2026-08-19", outcome="passed")
        self.event(type_="follow_up", when="2026-08-19", outcome=None)
        self.assertEqual(self.push(), (2, 0))
        self.assertEqual([c[0] for c in self.calls], ["POST", "POST"])
        self.assertEqual(self.page_ids(), ["created-1", "created-2"])

    def test_two_events_alike_in_every_mirrored_field_get_two_pages(self):
        # Nothing in the tracker forbids them, and the import side already
        # accounts for this with `claimed`; the push side has to match.
        self.event(type_="follow_up", when="2026-08-19", outcome="passed")
        self.event(type_="follow_up", when="2026-08-19", outcome="passed")
        self.assertEqual(self.push(), (2, 0))
        self.assertEqual(self.page_ids(), ["created-1", "created-2"])

    def test_a_dry_run_writes_nothing(self):
        self.event(when="2026-08-25T14:00")
        self.assertEqual(self.push(dry_run=True), (1, 0))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.page_ids(), [None])

    def test_notion_date_gets_the_seconds_notion_wants(self):
        self.assertEqual(notion_sync.notion_date("2026-08-25T14:00"),
                         "2026-08-25T14:00:00")
        self.assertEqual(notion_sync.notion_date("2026-08-25 14:00"),
                         "2026-08-25T14:00:00")
        # An all-day event stays a date, and a full datetime is left alone.
        self.assertEqual(notion_sync.notion_date("2026-08-25"), "2026-08-25")
        self.assertEqual(notion_sync.notion_date("2026-08-25T14:00:00+01:00"),
                         "2026-08-25T14:00:00+01:00")

    def test_the_two_directions_agree_on_what_one_event_is(self):
        # event_key and existing_event_id must normalise the date the same
        # way, or push and import will disagree about the same page.
        self.assertEqual(
            notion_sync.event_key(
                "a", "Screening", "2026-08-25T14:00:00.000+01:00", "Passed"),
            notion_sync.event_key(
                "a", " Screening ", "2026-08-25 14:00", " Passed "),
        )

    def test_the_two_directions_agree_that_outcome_is_part_of_it(self):
        # The import side keys on four fields; three here meant push collapsed
        # two events onto one page while import kept them apart.
        self.assertNotEqual(
            notion_sync.event_key("a", "Follow-up", "2026-08-19", "Passed"),
            notion_sync.event_key("a", "Follow-up", "2026-08-19", ""),
        )


if __name__ == "__main__":
    unittest.main()
