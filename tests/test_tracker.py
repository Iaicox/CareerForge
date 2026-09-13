"""Tracker behaviour that is easy to break and expensive to get wrong.

    python -m unittest discover tests

Each test runs against a temporary repo root, so it never touches the real
database or the real application folders.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

import paths  # noqa: E402
import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402


PROBE_MIGRATION = (
    "def migrate(conn, tracker):\n"
    "    conn.execute('ALTER TABLE applications ADD COLUMN probe_one TEXT')\n"
)

# Stands in for the machinery that resolves a module by its own __name__ --
# dataclasses, pickle, typing.get_type_hints -- and finds nothing if the loader
# never registered it.
SELF_LOOKUP_MIGRATION = (
    "import sys\n"
    "REGISTERED = __name__ in sys.modules\n"
    "def migrate(conn, tracker):\n"
    "    if not REGISTERED:\n"
    "        raise RuntimeError('the module was not in sys.modules while it ran')\n"
    "    conn.execute('ALTER TABLE applications ADD COLUMN probe_one TEXT')\n"
)


class TrackerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-test-")).resolve()
        self._real_repo = paths.REPO
        example = paths.CONFIG_EXAMPLE

        # Point the tools at a throwaway repo laid out like the real one.
        paths.configure(self.tmp)
        for stage in paths.STAGES:
            paths.stage_dir(stage).mkdir(parents=True)
        paths.CONFIG_DIR.mkdir(parents=True)
        shutil.copy(example, paths.CONFIG)
        tracker.load_config(force=True)

        tracker.init_db()
        self.cfg = tracker.load_config()
        self.conn = tracker.connect()

    def tearDown(self) -> None:
        self.conn.close()
        paths.configure(self._real_repo)
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, company="Acme", role="Frontend Engineer", url=None, **kw):
        with self.conn:
            return tracker.add_application(
                self.conn, self.cfg, company=company, role=role, url=url, **kw
            )

    def attach(self, row, kind="cv", name="cv.pdf", stage="applications",
               replace=False):
        """A real file in the application's folder, recorded as an attachment."""
        folder = paths.stage_dir(stage) / row["slug"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("x", encoding="utf-8")
        with self.conn:
            return tracker.add_attachment(
                self.conn, row["slug"], kind, str(folder / name), replace
            ).path

    def attachment_paths(self, row):
        return sorted(
            r["path"]
            for r in self.conn.execute(
                "SELECT path FROM attachments WHERE application_id = ?", (row["id"],)
            )
        )

    def attachment_rows(self):
        # Ids included: a row deleted and written again is not a no-op.
        return sorted(
            tuple(r)
            for r in self.conn.execute(
                "SELECT id, application_id, kind, path FROM attachments"
            )
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
        (paths.stage_dir("applications") / row["slug"]).mkdir()
        (paths.stage_dir("applications") / row["slug"] / "job.md").write_text("x")

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertFalse((paths.stage_dir("applications") / row["slug"]).exists())
        self.assertTrue((paths.stage_dir("processing") / row["slug"] / "job.md").exists())

    def test_collision_refuses_and_changes_nothing(self):
        row = self.add()
        (paths.stage_dir("applications") / row["slug"]).mkdir()
        (paths.stage_dir("rejected") / row["slug"]).mkdir()  # name already taken

        with self.assertRaises(TrackerError) as ctx:
            with self.conn:
                tracker.set_status(self.conn, self.cfg, row["slug"], "rejected")
        self.assertIn("already exists", str(ctx.exception))

        # Neither the folder nor the status moved.
        self.assertTrue((paths.stage_dir("applications") / row["slug"]).exists())
        after = tracker.resolve(self.conn, row["slug"])
        self.assertEqual(after["status"], "draft")

    def test_missing_folder_is_not_an_error(self):
        row = self.add()
        with self.conn:
            _, note = tracker.set_status(self.conn, self.cfg, row["slug"], "applied")
        self.assertIn("no folder", note)

    # -- attachment paths follow the folder --------------------------------

    def test_a_stage_move_takes_the_attachment_paths_with_it(self):
        # Paths are stored with the stage directory inside them, so a folder
        # that moves without them leaves every row pointing at nothing.
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        self.attach(row, "cover", "cover.pdf")

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertEqual(
            self.attachment_paths(row),
            [
                f"data/pipeline/processing/{row['slug']}/cover.pdf",
                f"data/pipeline/processing/{row['slug']}/cv.pdf",
            ],
        )
        for stored in self.attachment_paths(row):
            self.assertTrue((paths.REPO / stored).exists(), stored)

    def test_the_status_and_the_paths_land_together_or_not_at_all(self):
        # What set_status opening no transaction of its own is for. Every other
        # test here reads back on self.conn, which sees its own uncommitted
        # writes and so passes whether the two halves are one transaction or
        # two; a second connection to the same file only sees what committed.
        row = self.add()
        self.attach(row, "cv", "cv.pdf")

        def boom(*args, **kwargs):
            raise RuntimeError("the rewrite failed")

        self.addCleanup(
            setattr, tracker, "retarget_attachments", tracker.retarget_attachments
        )
        tracker.retarget_attachments = boom

        with self.assertRaises(RuntimeError):
            with self.conn:
                tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        other = tracker.connect()
        try:
            committed = other.execute(
                "SELECT status FROM applications WHERE id = ?", (row["id"],)
            ).fetchone()["status"]
        finally:
            other.close()
        self.assertEqual(committed, "draft")

        # The folder is the half the transaction does not cover: it moved
        # before the database was touched, and /triage is where that is caught.
        self.assertTrue((paths.stage_dir("processing") / row["slug"]).is_dir())

    def test_the_note_says_the_attachment_paths_moved_too(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        self.attach(row, "cover", "cover.pdf")

        with self.conn:
            _, note = tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertIn("moved applications/", note)
        self.assertIn("2 attachment paths followed it", note)

    def test_the_note_stays_quiet_when_there_was_nothing_to_repoint(self):
        row = self.add()
        (paths.stage_dir("applications") / row["slug"]).mkdir()

        with self.conn:
            _, note = tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertIn("moved applications/", note)
        self.assertNotIn("attachment", note)

    def test_no_move_leaves_the_attachment_paths_alone(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        before = self.attachment_paths(row)

        with self.conn:
            tracker.set_status(
                self.conn, self.cfg, row["slug"], "screening", move=False
            )

        self.assertEqual(self.attachment_paths(row), before)

    def test_a_path_already_taken_collapses_to_one_row(self):
        # add_attachment is INSERT OR IGNORE against UNIQUE(application_id,
        # kind, path), so re-attaching the right path after a move appended a
        # second row instead of replacing the stale one. Rewriting the stale row
        # onto the live one has to collapse the pair, not raise IntegrityError.
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", f"data/pipeline/processing/{row['slug']}/cv.pdf"),
            )

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertEqual(
            self.attachment_paths(row),
            [f"data/pipeline/processing/{row['slug']}/cv.pdf"],
        )

    def test_only_rows_under_the_moved_folder_are_rewritten(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        strays = [
            "data/documents/cv_master.pdf",
            # Another application whose slug starts with this one.
            f"data/pipeline/applications/{row['slug']}-2/cv.pdf",
            f"data/pipeline/rejected/{row['slug']}/cv.pdf",
        ]
        with self.conn:
            for i, stray in enumerate(strays):
                self.conn.execute(
                    "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                    (row["id"], f"other{i}", stray),
                )

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertEqual(
            self.attachment_paths(row),
            sorted(strays + [f"data/pipeline/processing/{row['slug']}/cv.pdf"]),
        )

    def test_an_underscore_in_a_slug_is_not_a_wildcard(self):
        # Why the prefix is matched with startswith and not SQL LIKE. A second
        # role at a company already taken is filed as `acme_frontend-engineer`,
        # and LIKE would read that `_` as "any one character" -- so a row under
        # a folder that differs only there would be rewritten as if it were
        # this application's. The `-2` suffix the test above uses does not show
        # this: LIKE would leave that one alone too.
        self.add(role="Frontend Engineer")
        row = self.add(role="Backend Engineer")
        self.assertIn("_", row["slug"])
        self.attach(row, "cv", "cv.pdf")

        decoy = "data/pipeline/applications/{}/cv.pdf".format(
            row["slug"].replace("_", "X")
        )
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "other", decoy),
            )

        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "screening")

        self.assertEqual(
            self.attachment_paths(row),
            sorted([decoy, f"data/pipeline/processing/{row['slug']}/cv.pdf"]),
        )

    # -- attachments come back out -----------------------------------------

    def test_detaching_removes_the_row_and_leaves_the_file(self):
        row = self.add()
        stored = self.attach(row, "cv", "cv.pdf")
        with self.conn:
            gone = tracker.remove_attachment(self.conn, row["slug"], "cv")

        self.assertEqual(gone["path"], stored)
        self.assertEqual(self.attachment_paths(row), [])
        # The document is the one the employer received. It stays on disk.
        self.assertTrue((paths.REPO / stored).exists())

    def test_two_rows_of_a_kind_refuse_rather_than_guess(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", f"data/pipeline/rejected/{row['slug']}/cv.pdf"),
            )

        with self.assertRaises(TrackerError) as ctx:
            tracker.remove_attachment(self.conn, row["slug"], "cv")

        # Both candidates are named, so the user can pick one.
        self.assertIn("--id", str(ctx.exception))
        ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM attachments WHERE application_id = ?", (row["id"],)
            )
        ]
        self.assertEqual(len(ids), 2)
        for at_id in ids:
            self.assertIn(f"#{at_id}", str(ctx.exception))

    def test_an_attachment_id_belonging_to_another_application_is_refused(self):
        mine = self.add()
        theirs = self.add(company="Other Co")
        self.attach(mine, "cv", "cv.pdf")
        at_id = int(
            self.conn.execute(
                "SELECT id FROM attachments WHERE application_id = ?", (mine["id"],)
            ).fetchone()["id"]
        )

        with self.assertRaises(TrackerError):
            tracker.remove_attachment(self.conn, theirs["slug"], attachment_id=at_id)
        with self.assertRaises(TrackerError):
            tracker.remove_attachment(self.conn, mine["slug"], attachment_id=9999)
        self.assertEqual(len(self.attachment_paths(mine)), 1)

    def test_detaching_needs_something_to_go_on(self):
        # No selector must never mean "all of them".
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        self.attach(row, "cover", "cover.pdf")

        with self.assertRaises(TrackerError):
            tracker.remove_attachment(self.conn, row["slug"])
        self.assertEqual(len(self.attachment_paths(row)), 2)

    def test_detaching_touches_its_application(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET updated_at = '2000-01-01 00:00:00' "
                "WHERE id = ?", (row["id"],)
            )
        with self.conn:
            tracker.remove_attachment(self.conn, row["slug"], "cv")

        after = self.conn.execute(
            "SELECT updated_at FROM applications WHERE id = ?", (row["id"],)
        ).fetchone()["updated_at"]
        self.assertNotEqual(after, "2000-01-01 00:00:00")

    def test_the_detach_report_says_the_file_stays(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        with self.conn:
            gone = dict(tracker.remove_attachment(self.conn, row["slug"], "cv"))

        report = tracker.detached_attachment_report(self.conn, gone)
        self.assertIn("untouched", report)
        # No Notion page, so nothing is said about one.
        self.assertNotIn("Notion", report)

    def test_the_detach_report_names_the_notion_page_that_keeps_the_file(self):
        # An attachment is a page property, not a page, so there is no
        # notion_page_id to chase and nothing over there empties itself.
        row = self.add()
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", f"data/pipeline/applications/{row['slug']}/gone.pdf"),
            )
            self.conn.execute(
                "UPDATE applications SET notion_page_id = 'page-1' WHERE id = ?",
                (row["id"],),
            )
        with self.conn:
            gone = dict(tracker.remove_attachment(self.conn, row["slug"], "cv"))

        report = tracker.detached_attachment_report(self.conn, gone)
        self.assertIn("page-1", report)
        # And it says the row was already pointing at nothing.
        self.assertIn("no file at that path", report)

    # -- attaching the next version of a document --------------------------

    def test_replace_leaves_one_row_for_that_kind(self):
        row = self.add()
        self.attach(row, "cv", "cv_v1.pdf")
        self.attach(row, "cv", "cv_v2.pdf", replace=True)

        self.assertEqual(
            self.attachment_paths(row),
            [f"data/pipeline/applications/{row['slug']}/cv_v2.pdf"],
        )

    def test_replace_names_the_rows_it_dropped(self):
        # A count would be the one place those paths are not written down --
        # the rows are gone, and what they held is what tells the user whether
        # the right one went.
        row = self.add()
        first = self.attach(row, "cv", "cv_v1.pdf")
        second = self.attach(row, "cv", "cv_v2.pdf")

        folder = paths.stage_dir("applications") / row["slug"]
        (folder / "cv_v3.pdf").write_text("x", encoding="utf-8")
        with self.conn:
            done = tracker.add_attachment(
                self.conn, row["slug"], "cv", str(folder / "cv_v3.pdf"), replace=True
            )

        self.assertEqual(done.replaced, [first, second])

    def test_replace_leaves_another_application_alone(self):
        # The DELETE is scoped by application as well as by kind: two open
        # applications both have a cv, and neither is the other's.
        mine = self.add(company="Acme")
        theirs = self.add(company="Globex")
        self.attach(theirs, "cv", "cv.pdf")
        untouched = self.attachment_rows()

        self.attach(mine, "cv", "cv_v1.pdf")
        self.attach(mine, "cv", "cv_v2.pdf", replace=True)

        self.assertEqual(
            self.attachment_paths(mine),
            [f"data/pipeline/applications/{mine['slug']}/cv_v2.pdf"],
        )
        self.assertEqual(
            [r for r in self.attachment_rows() if r[1] == theirs["id"]], untouched
        )

    def test_replace_leaves_the_other_kinds_alone(self):
        row = self.add()
        self.attach(row, "cover", "cover.pdf")
        self.attach(row, "cv", "cv_v1.pdf")
        self.attach(row, "cv", "cv_v2.pdf", replace=True)

        self.assertEqual(
            self.attachment_paths(row),
            [
                f"data/pipeline/applications/{row['slug']}/cover.pdf",
                f"data/pipeline/applications/{row['slug']}/cv_v2.pdf",
            ],
        )

    def test_replacing_a_path_with_itself_keeps_the_row_it_found(self):
        # Excluding the target from the DELETE, rather than deleting and
        # inserting again, is what keeps a re-run from rewriting added_at.
        row = self.add()
        stored = self.attach(row, "cv", "cv.pdf")
        before = [
            tuple(r)
            for r in self.conn.execute(
                "SELECT id, added_at FROM attachments WHERE application_id = ?",
                (row["id"],),
            )
        ]

        with self.conn:
            done = tracker.add_attachment(
                self.conn, row["slug"], "cv", stored, replace=True
            )

        self.assertEqual(done.replaced, [])
        after = [
            tuple(r)
            for r in self.conn.execute(
                "SELECT id, added_at FROM attachments WHERE application_id = ?",
                (row["id"],),
            )
        ]
        self.assertEqual(after, before)

    def test_attach_without_replace_still_appends(self):
        # The default is untouched: --replace is a flag, not a new behaviour.
        row = self.add()
        self.attach(row, "cv", "cv_v1.pdf")
        self.attach(row, "cv", "cv_v2.pdf")

        self.assertEqual(len(self.attachment_paths(row)), 2)

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

    def test_an_orphan_card_carries_what_the_board_needs_to_move_it(self):
        # A status that used to be in config and is not any more. The board
        # renders these under "Unknown status" so they can be dragged back into
        # a real column, which needs id and updated_at on the card.
        row = self.add()
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = 'was_renamed' WHERE id = ?",
                (row["id"],),
            )
        data = tracker.board_data(self.conn, self.cfg)
        self.assertEqual([c["id"] for c in data["orphans"]], [row["id"]])
        orphan = data["orphans"][0]
        for key in ("id", "updated_at", "company_name", "role", "status"):
            self.assertIn(key, orphan)
        self.assertIsNone(orphan["stage"])

    def test_an_orphan_can_be_moved_back_to_a_configured_status(self):
        # set_status validates the target, never the status being left behind.
        row = self.add()
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = 'was_renamed' WHERE id = ?",
                (row["id"],),
            )
        with self.conn:
            moved, _ = tracker.set_status(self.conn, self.cfg, row["slug"], "screening")
        self.assertEqual(moved["status"], "screening")
        self.assertEqual(tracker.board_data(self.conn, self.cfg)["orphans"], [])

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

    # -- correcting an event's type ----------------------------------------

    def test_an_event_filed_under_the_wrong_type_can_be_corrected(self):
        # An import maps a Notion label it does not recognise to `other`, and
        # the row then says nothing about how far the application got.
        row = self.add()
        with self.conn:
            ev = tracker.add_event(self.conn, self.cfg, row["slug"], "other",
                                   "2026-08-19")
        with self.conn:
            fixed = tracker.set_event_type(
                self.conn, self.cfg, int(ev["id"]), "rejection", outcome="failed"
            )
        self.assertEqual(fixed["type"], "rejection")
        self.assertEqual(fixed["outcome"], "failed")

    def test_the_outcome_is_left_alone_when_not_given(self):
        row = self.add()
        with self.conn:
            ev = tracker.add_event(self.conn, self.cfg, row["slug"], "other",
                                   "2026-08-19", None, "passed")
        with self.conn:
            fixed = tracker.set_event_type(
                self.conn, self.cfg, int(ev["id"]), "follow_up"
            )
        self.assertEqual(fixed["type"], "follow_up")
        self.assertEqual(fixed["outcome"], "passed")

    def test_an_unknown_type_is_refused(self):
        row = self.add()
        with self.conn:
            ev = tracker.add_event(self.conn, self.cfg, row["slug"], "other",
                                   "2026-08-19")
        with self.assertRaises(TrackerError):
            tracker.set_event_type(self.conn, self.cfg, int(ev["id"]), "not_a_type")

    def test_correcting_an_event_that_is_not_there_is_an_error(self):
        with self.assertRaises(TrackerError):
            tracker.set_event_type(self.conn, self.cfg, 9999, "rejection")

    def test_an_event_recorded_twice_can_be_deleted(self):
        row = self.add()
        with self.conn:
            keep = tracker.add_event(self.conn, self.cfg, row["slug"],
                                     "tech_interview", "2026-08-25T14:00",
                                     outcome="passed")
            drop = tracker.add_event(self.conn, self.cfg, row["slug"],
                                     "tech_interview", "2026-08-25T14:00",
                                     outcome="pending")
        with self.conn:
            gone = tracker.delete_event(self.conn, int(drop["id"]))
        # The whole row comes back, so the caller can show what it removed.
        self.assertEqual(gone["outcome"], "pending")
        left = [
            int(e["id"])
            for e in self.conn.execute(
                "SELECT id FROM events WHERE application_id = ?", (row["id"],)
            )
        ]
        self.assertEqual(left, [int(keep["id"])])

    def test_deleting_an_event_that_is_not_there_is_an_error(self):
        with self.assertRaises(TrackerError):
            tracker.delete_event(self.conn, 9999)

    def test_the_delete_report_keeps_a_page_another_event_still_uses(self):
        # The duplicate case: two rows collapsed onto one Notion page. Telling
        # the user to delete that page would cost them the surviving event.
        row = self.add()
        with self.conn:
            keep = tracker.add_event(self.conn, self.cfg, row["slug"],
                                     "tech_interview", "2026-08-25T14:00")
            drop = tracker.add_event(self.conn, self.cfg, row["slug"],
                                     "tech_interview", "2026-08-25T14:00")
            self.conn.execute(
                "UPDATE events SET notion_page_id = 'shared' WHERE id IN (?, ?)",
                (keep["id"], drop["id"]),
            )
        with self.conn:
            gone = dict(tracker.delete_event(self.conn, int(drop["id"])))
        report = tracker.deleted_event_report(self.conn, self.cfg, gone)
        self.assertIn("stays", report)
        self.assertNotIn("delete that page too", report)

    def test_the_delete_report_says_when_a_notion_page_is_left_orphaned(self):
        row = self.add()
        with self.conn:
            ev = tracker.add_event(self.conn, self.cfg, row["slug"], "other",
                                   "2026-08-19", notes="only copy of this")
            self.conn.execute(
                "UPDATE events SET notion_page_id = 'lonely' WHERE id = ?",
                (ev["id"],),
            )
        with self.conn:
            gone = dict(tracker.delete_event(self.conn, int(ev["id"])))
        report = tracker.deleted_event_report(self.conn, self.cfg, gone)
        self.assertIn("delete that page too", report)
        # And the notes are in the report, since nothing else holds them now.
        self.assertIn("only copy of this", report)

    def test_deleting_an_event_touches_its_application(self):
        row = self.add()
        with self.conn:
            ev = tracker.add_event(self.conn, self.cfg, row["slug"], "other",
                                   "2026-08-19")
            self.conn.execute(
                "UPDATE applications SET updated_at = '2000-01-01 00:00:00' "
                "WHERE id = ?", (row["id"],)
            )
        with self.conn:
            tracker.delete_event(self.conn, int(ev["id"]))
        after = self.conn.execute(
            "SELECT updated_at FROM applications WHERE id = ?", (row["id"],)
        ).fetchone()["updated_at"]
        self.assertNotEqual(after, "2000-01-01 00:00:00")

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

    def test_a_terminal_status_outside_the_rejected_stage_is_never_stale(self):
        # Nothing stops a user from filing "accepted" under `processing` and
        # marking it terminal. Testing the stage alone chased it forever.
        self.cfg.data["statuses"].append(
            {"id": "accepted", "stage": "processing", "terminal": True,
             "labels": {"en": "Accepted"}}
        )
        row = self.add(company="Hooli")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "accepted")
        self.age(row)
        self.assertEqual(self.stale_slugs(), [])

    def test_an_application_whose_status_is_not_in_config_is_not_stale(self):
        # enrich() leaves stage None for these, and None != "rejected" was true,
        # so every orphan read as silent -- renaming the `rejected` status id
        # would have made every closed application permanently "Silent" in
        # /triage, which only ever proposes closing them again.
        row = self.add(company="Vandelay")
        self.age(row)
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = 'gone_from_config' WHERE id = ?",
                (row["id"],),
            )
        self.assertEqual(self.stale_slugs(), [])

    # -- the calendar ------------------------------------------------------

    def event(self, row, type_, when, outcome=None):
        with self.conn:
            return tracker.add_event(
                self.conn, self.cfg, row["slug"], type_, when, None, outcome
            )

    def follow_ups(self, when=None):
        when = when or date.today()
        rows = tracker.list_applications(self.conn, self.cfg)
        return tracker.planned_follow_ups(rows, self.cfg, when)

    @staticmethod
    def days(n):
        """An ISO date n days from today, past or future."""
        return (date.today() + timedelta(days=n)).isoformat()

    def test_a_follow_up_is_planned_after_the_configured_interval(self):
        row = self.add(company="Acme")
        self.event(row, "applied", self.days(-3), "passed")
        due = self.follow_ups()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["slug"], row["slug"])
        self.assertEqual(due[0]["company"], "Acme")
        self.assertEqual(due[0]["due"], self.days(4))
        self.assertFalse(due[0]["overdue"])
        self.assertFalse(due[0]["silent"])

    def test_a_follow_up_past_its_date_is_overdue(self):
        row = self.add(company="Globex")
        self.event(row, "applied", self.days(-10), "passed")
        due = self.follow_ups()
        self.assertEqual(due[0]["due"], self.days(-3))
        self.assertTrue(due[0]["overdue"])
        self.assertFalse(due[0]["silent"])

    def test_a_follow_up_due_today_is_not_overdue(self):
        # Due today means write today, not that the day was missed. Off by one
        # here turns every fresh reminder red on the day it appears.
        row = self.add(company="Initech")
        self.event(row, "applied", self.days(-7), "passed")
        due = self.follow_ups()
        self.assertEqual(due[0]["due"], self.days(0))
        self.assertFalse(due[0]["overdue"])

    def test_an_event_scheduled_ahead_suppresses_the_follow_up(self):
        # The whole point of the rule: an interview on the calendar next week
        # is the answer. Chasing it would be writing to ask about the reply we
        # already have.
        row = self.add(company="Hooli")
        self.event(row, "applied", self.days(-10), "passed")
        self.event(row, "tech_interview", f"{self.days(5)}T14:00", "pending")
        self.assertEqual(self.follow_ups(), [])

    def test_a_closed_application_gets_no_follow_up(self):
        row = self.add(company="Vandelay")
        self.event(row, "applied", self.days(-30), "passed")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, row["slug"], "rejected")
        self.assertEqual(self.follow_ups(), [])

    def test_an_application_whose_status_is_not_in_config_gets_no_follow_up(self):
        # Same rule as staleness: an orphan is a row we cannot read, and
        # proposing an action on it would be guessing.
        row = self.add(company="Soylent")
        self.event(row, "applied", self.days(-30), "passed")
        with self.conn:
            self.conn.execute(
                "UPDATE applications SET status = 'gone_from_config' WHERE id = ?",
                (row["id"],),
            )
        self.assertEqual(self.follow_ups(), [])

    def test_an_application_with_no_events_gets_no_follow_up(self):
        # A draft nobody has sent is not waiting for an answer. Falling back to
        # created_at here would put a reminder on every unfinished draft.
        self.add(company="Umbrella")
        self.assertEqual(self.follow_ups(), [])

    def test_logging_a_follow_up_restarts_the_clock(self):
        row = self.add(company="Cyberdyne")
        self.event(row, "applied", self.days(-20), "passed")
        self.event(row, "follow_up", self.days(-2))
        due = self.follow_ups()
        self.assertEqual(due[0]["due"], self.days(5))
        self.assertEqual(due[0]["last_event_date"], self.days(-2))
        self.assertFalse(due[0]["overdue"])

    def test_a_follow_up_long_overdue_is_also_silent(self):
        row = self.add(company="Stark")
        self.event(row, "applied", self.days(-40), "passed")
        due = self.follow_ups()
        self.assertTrue(due[0]["overdue"])
        self.assertTrue(due[0]["silent"])

    def test_the_follow_up_interval_falls_back_when_the_config_omits_it(self):
        # The key landed after the template was already in people's hands, so
        # an existing config.toml has neither the key nor, in the oldest ones,
        # the table.
        self.cfg.data["tracker"].pop("follow_up_after_days", None)
        self.assertEqual(self.cfg.follow_up_after_days(), 7)
        self.cfg.data.pop("tracker")
        self.assertEqual(self.cfg.follow_up_after_days(), 7)
        self.assertEqual(self.cfg.stale_after_days(), 30)

    def test_the_calendar_carries_labels_and_the_time_flag(self):
        row = self.add(company="Acme", role="Frontend Engineer")
        self.event(row, "tech_interview", f"{self.days(3)}T14:00", "pending")
        self.event(row, "applied", self.days(-3), "passed")
        data = tracker.calendar_data(self.conn, self.cfg)
        by_type = {e["type"]: e for e in data["events"]}
        self.assertEqual(by_type["tech_interview"]["is_datetime"], 1)
        self.assertTrue(by_type["tech_interview"]["date"].endswith("T14:00"))
        self.assertEqual(by_type["tech_interview"]["company"], "Acme")
        self.assertEqual(by_type["tech_interview"]["role"], "Frontend Engineer")
        self.assertEqual(by_type["tech_interview"]["slug"], row["slug"])
        self.assertTrue(by_type["tech_interview"]["type_label"])
        self.assertTrue(by_type["tech_interview"]["outcome_label"])
        self.assertEqual(by_type["applied"]["is_datetime"], 0)
        self.assertEqual(data["follow_up_after_days"], 7)
        self.assertEqual(data["today"], date.today().isoformat())
        self.assertEqual([e["type"] for e in data["events"]],
                         ["applied", "tech_interview"])

    def test_the_calendar_offers_every_application_marking_the_closed_ones(self):
        open_row = self.add(company="Acme")
        closed = self.add(company="Globex")
        with self.conn:
            tracker.set_status(self.conn, self.cfg, closed["slug"], "rejected")
        picks = {a["slug"]: a for a in tracker.calendar_data(self.conn, self.cfg)["applications"]}
        self.assertFalse(picks[open_row["slug"]]["terminal"])
        self.assertTrue(picks[closed["slug"]]["terminal"])

    def test_the_agenda_splits_overdue_today_and_the_days_ahead(self):
        late = self.add(company="Globex")
        self.event(late, "applied", self.days(-20), "passed")
        soon = self.add(company="Hooli")
        self.event(soon, "tech_interview", f"{self.days(3)}T10:30", "pending")
        now = self.add(company="Initech")
        self.event(now, "screening", self.days(0), "passed")
        far = self.add(company="Vandelay")
        self.event(far, "tech_interview", f"{self.days(20)}T09:00", "pending")

        sliced = tracker.agenda_slice(tracker.calendar_data(self.conn, self.cfg), 7)
        self.assertEqual([i["company"] for i in sliced["overdue"]], ["Globex"])
        self.assertEqual([i["company"] for i in sliced["today"]], ["Initech"])
        # Hooli's interview in three days, and the follow-up Initech's event
        # today earns exactly on the horizon -- both kinds share the list.
        self.assertEqual([i["company"] for i in sliced["upcoming"]],
                         ["Hooli", "Initech"])
        self.assertEqual([i["kind"] for i in sliced["upcoming"]],
                         ["event", "follow_up"])
        self.assertEqual(sliced["upcoming"][0]["time"], "10:30")
        self.assertEqual(sliced["today"][0]["time"], "")
        self.assertEqual(sliced["overdue"][0]["kind"], "follow_up")

    def test_the_agenda_keeps_silence_in_a_list_of_its_own(self):
        loud = self.add(company="Hooli")
        self.event(loud, "applied", self.days(-10), "passed")
        quiet = self.add(company="Initech")
        self.event(quiet, "applied", self.days(-40), "passed")

        sliced = tracker.agenda_slice(tracker.calendar_data(self.conn, self.cfg), 7)
        self.assertEqual([i["company"] for i in sliced["overdue"]], ["Hooli"])
        self.assertEqual([i["company"] for i in sliced["silent"]], ["Initech"])

        text = tracker.render_agenda(
            tracker.calendar_data(self.conn, self.cfg), sliced, 7)
        self.assertIn("Silent for over 30 days", text)
        self.assertIn("/triage", text)
        # and the silent row is not also printed among the overdue
        overdue_section = text.split("## Overdue")[1].split("##")[0]
        self.assertIn("Hooli", overdue_section)
        self.assertNotIn("Initech", overdue_section)

    def test_the_agenda_command_keeps_the_date_and_the_buckets_apart(self):
        # --json is the contract an agent reads. Spreading the buckets into the
        # top level put the "today" list on the same key as the date, and the
        # list won -- the output no longer said which day it described.
        row = self.add(company="Globex")
        self.event(row, "applied", self.days(-3), "passed")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tracker.main(["agenda", "--days", "5", "--json"])
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["today"], date.today().isoformat())
        self.assertEqual(payload["days"], 5)
        self.assertEqual(payload["follow_up_after_days"], 7)
        self.assertEqual(
            sorted(payload["agenda"]), ["overdue", "silent", "today", "upcoming"])

    def test_the_agenda_refuses_a_horizon_that_points_backwards(self):
        with self.assertRaises(SystemExit):
            tracker.build_parser().parse_args(["agenda", "--days", "-1"])

    def test_an_event_with_no_date_is_dated_the_local_day(self):
        # datetime.now(timezone.utc).date() files an evening entry west of
        # Greenwich under tomorrow, and a future date tells the calendar
        # something is scheduled -- so it stops proposing a follow-up.
        row = self.add(company="Acme")
        event = self.event(row, "applied", None, "passed")
        self.assertEqual(event["date"], date.today().isoformat())
        self.assertEqual(event["is_datetime"], 0)

    def test_the_agenda_text_names_every_bucket_and_its_rows(self):
        row = self.add(company="Globex")
        self.event(row, "applied", self.days(-20), "passed")
        data = tracker.calendar_data(self.conn, self.cfg)
        text = tracker.render_agenda(data, tracker.agenda_slice(data, 7), 7)
        self.assertIn("Globex", text)
        for heading in ("Overdue", "Today", "Next 7 days"):
            self.assertIn(heading, text)

    # -- migrations --------------------------------------------------------

    def test_fresh_database_records_migrations_without_running_them(self):
        # setUp built this database from schema.sql, which already contains
        # everything the migrations would add. Replaying them would fail on
        # duplicate columns, so they must be recorded as a baseline instead.
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        on_disk = {
            p.name
            for p in tracker.MIGRATIONS_DIR.glob("*")
            if p.suffix in tracker.MIGRATION_SUFFIXES
        }
        self.assertEqual(recorded, on_disk)

    def test_migration_004_moves_attachment_paths_under_data(self):
        # Attachments are stored repo-relative, so rows written before the
        # stage directories moved under data/pipeline/ point at nothing.
        row = self.add()
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", "applications/acme/cv.pdf"),
            )
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cover", "data/pipeline/rejected/acme/cover.pdf"),
            )
        sql = (tracker.MIGRATIONS_DIR / "004_attachments_data_dir.sql").read_text(encoding="utf-8")
        self.conn.executescript(sql)
        paths_ = sorted(r["path"] for r in self.conn.execute("SELECT path FROM attachments"))
        self.assertEqual(paths_, [
            "data/pipeline/applications/acme/cv.pdf",
            "data/pipeline/rejected/acme/cover.pdf",
        ])

    def replay_migration(self, name):
        """Run one migration for real on a database that recorded it baselined."""
        with self.conn:
            self.conn.execute("DELETE FROM migrations WHERE name = ?", (name,))
        return tracker.apply_migrations(self.conn)

    def test_migration_006_repoints_attachments_at_the_folder_on_disk(self):
        # Which stage a slug belongs to is not on the row: the status-to-stage
        # map is the user's config, and the folder can drift from the status
        # anyway. The folder on disk is the ground truth.
        row = self.add()
        (paths.stage_dir("rejected") / row["slug"]).mkdir(parents=True)
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", f"data/pipeline/applications/{row['slug']}/cv.pdf"),
            )

        self.assertEqual(
            self.replay_migration("006_attachment_stage_paths.py"),
            ["006_attachment_stage_paths.py"],
        )
        self.assertEqual(
            self.attachment_paths(row),
            [f"data/pipeline/rejected/{row['slug']}/cv.pdf"],
        )

    def test_migration_006_collapses_the_duplicate_rather_than_leaving_both(self):
        # The shape the live database was in: the folder had moved to rejected/,
        # both documents were attached again there, and the rows written under
        # applications/ stayed behind. Two rows per kind, one live and one dead.
        row = self.add()
        folder = paths.stage_dir("rejected") / row["slug"]
        folder.mkdir(parents=True)
        for kind, name in (("cv", "cv.pdf"), ("cover", "cover.pdf")):
            (folder / name).write_text("x", encoding="utf-8")
            with self.conn:
                for stage in ("applications", "rejected"):
                    self.conn.execute(
                        "INSERT INTO attachments(application_id, kind, path) "
                        "VALUES (?,?,?)",
                        (row["id"], kind, f"data/pipeline/{stage}/{row['slug']}/{name}"),
                    )

        self.replay_migration("006_attachment_stage_paths.py")

        self.assertEqual(
            self.attachment_paths(row),
            [
                f"data/pipeline/rejected/{row['slug']}/cover.pdf",
                f"data/pipeline/rejected/{row['slug']}/cv.pdf",
            ],
        )

    def test_migration_006_replayed_over_its_own_repair_changes_nothing(self):
        # Idempotency on a database that never needed repairing proves little:
        # the interesting replay is the one that walks over rows the first run
        # rewrote, and over the gap where it deleted a duplicate.
        row = self.add()
        folder = paths.stage_dir("rejected") / row["slug"]
        folder.mkdir(parents=True)
        for name in ("cv.pdf", "cover.pdf"):
            (folder / name).write_text("x", encoding="utf-8")
        with self.conn:
            for kind, name, stages in (
                ("cv", "cv.pdf", ("applications",)),
                ("cover", "cover.pdf", ("applications", "rejected")),
            ):
                for stage in stages:
                    self.conn.execute(
                        "INSERT INTO attachments(application_id, kind, path) "
                        "VALUES (?,?,?)",
                        (row["id"], kind, f"data/pipeline/{stage}/{row['slug']}/{name}"),
                    )

        self.replay_migration("006_attachment_stage_paths.py")
        repaired = self.attachment_rows()
        self.assertEqual(
            self.attachment_paths(row),
            [
                f"data/pipeline/rejected/{row['slug']}/cover.pdf",
                f"data/pipeline/rejected/{row['slug']}/cv.pdf",
            ],
        )

        self.replay_migration("006_attachment_stage_paths.py")
        # Ids included, so a row rewritten onto the value it already held would
        # still show up here.
        self.assertEqual(self.attachment_rows(), repaired)

    def test_migration_006_leaves_an_application_with_no_folder_alone(self):
        # Nothing on the row says which stage it belongs to, and with no folder
        # on disk there is no ground truth either. Guessing from the status
        # would be guessing: the two are allowed to disagree, which is what
        # /triage is for.
        row = self.add()
        self.assertIsNone(tracker.folder_for(row["slug"])[0])
        with self.conn:
            self.conn.execute(
                "INSERT INTO attachments(application_id, kind, path) VALUES (?,?,?)",
                (row["id"], "cv", f"data/pipeline/applications/{row['slug']}/cv.pdf"),
            )
        before = self.attachment_rows()

        self.replay_migration("006_attachment_stage_paths.py")
        self.assertEqual(self.attachment_rows(), before)

    def test_migration_006_is_a_no_op_when_the_paths_are_already_right(self):
        row = self.add()
        self.attach(row, "cv", "cv.pdf")
        self.attach(row, "cover", "cover.pdf")
        before = self.attachment_rows()

        self.replay_migration("006_attachment_stage_paths.py")
        self.assertEqual(self.attachment_rows(), before)
        # And again, on the database it has just walked over.
        self.replay_migration("006_attachment_stage_paths.py")
        self.assertEqual(self.attachment_rows(), before)

    def columns(self, table="applications"):
        return {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def use_migrations(self, source, name="900_test.sql"):
        """Point the module at a throwaway migrations directory."""
        d = self.tmp / "migrations"
        d.mkdir(exist_ok=True)
        (d / name).write_text(source, encoding="utf-8")
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

    def test_a_python_migration_runs_and_records_itself(self):
        self.use_migrations(PROBE_MIGRATION, name="900_test.py")
        self.assertEqual(tracker.apply_migrations(self.conn), ["900_test.py"])
        self.assertIn("probe_one", self.columns())
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        self.assertIn("900_test.py", recorded)
        # And it runs exactly once.
        self.assertEqual(tracker.apply_migrations(self.conn), [])

    def test_a_python_migration_that_fails_halfway_leaves_nothing_behind(self):
        # No executescript() here, so the runner's `with conn:` really does own
        # the transaction -- as long as one was opened, which pure DDL does not
        # do by itself.
        self.use_migrations(
            PROBE_MIGRATION + "    raise RuntimeError('halfway')\n",
            name="900_test.py",
        )
        with self.assertRaises(RuntimeError):
            tracker.apply_migrations(self.conn)

        self.assertNotIn("probe_one", self.columns())
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        self.assertNotIn("900_test.py", recorded)

    def test_a_python_migration_without_a_migrate_function_is_refused(self):
        self.use_migrations("answer = 42\n", name="900_test.py")
        with self.assertRaises(TrackerError) as ctx:
            tracker.apply_migrations(self.conn)
        self.assertIn("migrate(conn, tracker)", str(ctx.exception))

    def test_a_python_migration_can_find_itself_in_sys_modules(self):
        self.use_migrations(SELF_LOOKUP_MIGRATION, name="900_test.py")
        self.assertEqual(tracker.apply_migrations(self.conn), ["900_test.py"])
        self.assertIn("probe_one", self.columns())
        # And the name does not outlive the run: it points at a file nothing
        # will import again, and the next migration of that stem is a new module.
        self.assertNotIn("careerforge_migration_900_test", sys.modules)

    def test_migrations_refuse_to_run_inside_someone_else_transaction(self):
        # sqlite3's `with conn:` does not nest, so the COMMIT that ends the
        # first migration would land whatever the caller still had open.
        row = self.add()
        self.use_migrations(PROBE_MIGRATION, name="900_test.py")

        with self.assertRaises(TrackerError) as ctx:
            with self.conn:
                self.conn.execute(
                    "UPDATE applications SET notes = 'half done' WHERE id = ?",
                    (row["id"],),
                )
                tracker.apply_migrations(self.conn)

        self.assertIn("transaction", str(ctx.exception))
        self.assertNotIn("probe_one", self.columns())
        # The caller's half-finished work rolled back with the exception,
        # instead of being committed by a migration that never meant to.
        self.assertIsNone(
            self.conn.execute(
                "SELECT notes FROM applications WHERE id = ?", (row["id"],)
            ).fetchone()["notes"]
        )

    # -- the schema document -----------------------------------------------

    CONSTRAINT_KEYWORDS = {"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "CONSTRAINT"}
    DOCUMENTED_TABLES = ("companies", "applications", "events", "attachments")

    def schema_tables(self):
        text = tracker.SCHEMA_PATH.read_text(encoding="utf-8")
        tables = {}
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", text, re.S
        ):
            columns = []
            for line in match.group(2).splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                # Split on the bracket too: "UNIQUE(a, b)" has no space in it.
                first = re.split(r"[\s(]", line, maxsplit=1)[0]
                if first.upper() in self.CONSTRAINT_KEYWORDS:
                    continue
                columns.append(first)
            tables[match.group(1)] = columns
        return tables

    def test_the_schema_doc_describes_every_table_and_column(self):
        # The doc is what someone reads before opening the database in
        # datasette or DB Browser, so a column missing from it is a column
        # they do not know they have. Migration 002 added seven and none of
        # them were written down.
        doc = (REPO / "docs" / "tracker-schema.md").read_text(encoding="utf-8")
        tables = self.schema_tables()
        self.assertTrue(tables, "no CREATE TABLE statements parsed from schema.sql")

        for table in tables:
            self.assertIn(f"`{table}`", doc, f"table {table} is not in the doc")

        for table in self.DOCUMENTED_TABLES:
            for column in tables[table]:
                self.assertIn(
                    f"`{column}`", doc,
                    f"{table}.{column} is in schema.sql but not in tracker-schema.md",
                )

    def test_the_schema_doc_describes_the_view(self):
        doc = (REPO / "docs" / "tracker-schema.md").read_text(encoding="utf-8")
        text = tracker.SCHEMA_PATH.read_text(encoding="utf-8")
        view = text.split("CREATE VIEW", 1)[1]
        for alias in re.findall(r"\bAS (\w+)\b", view):
            self.assertIn(f"`{alias}`", doc, f"view column {alias} is not in the doc")

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


class SecretTest(unittest.TestCase):
    """One reader for every credential, and it never takes /doctor down."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-secret-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, tracker, "load_dotenv", tracker.load_dotenv)
        tracker.load_dotenv = lambda: None

    def test_the_environment_wins_over_a_file(self):
        from unittest import mock
        import os
        path = self.tmp / "token"
        path.write_text("from-the-file", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CF_TEST_SECRET": "from-the-env"}):
            self.assertEqual(
                tracker.secret(("CF_TEST_SECRET",), hint="x", files=(path,)),
                "from-the-env")

    def test_nothing_there_is_skipped_and_the_next_file_is_read(self):
        good = self.tmp / "good"
        good.write_text("s3cret", encoding="utf-8")
        a_directory = self.tmp / "adir"
        a_directory.mkdir()
        self.assertEqual(
            tracker.secret(("CF_TEST_ABSENT",), hint="x",
                           files=(self.tmp / "missing", a_directory, good)),
            "s3cret")

    def test_a_file_that_exists_and_will_not_read_is_named_not_skipped(self):
        # PowerShell's redirect writes UTF-16, and a wrong ACL is routine on
        # Windows. Passing over it silently tells the user to write down a
        # secret that is sitting in the file we just refused to read.
        utf16 = self.tmp / "utf16"
        utf16.write_bytes(b"\xff\xfes\x003\x00")
        good = self.tmp / "good"
        good.write_text("s3cret", encoding="utf-8")
        with self.assertRaises(tracker.TrackerError) as ctx:
            tracker.secret(("CF_TEST_ABSENT",), hint="put it in .env", files=(utf16, good))
        self.assertIn("utf16", str(ctx.exception))
        self.assertIn("cannot be read", str(ctx.exception))

    def test_a_byte_order_mark_does_not_travel_into_the_credential(self):
        # The same redirect, in its other mood: `utf-8` keeps the BOM, so the
        # token goes out with an invisible first character and the service
        # rejects it without saying why.
        path = self.tmp / "bom"
        path.write_bytes(b"\xef\xbb\xbfs3cret")
        self.assertEqual(tracker.secret(("CF_TEST_ABSENT",), hint="x", files=(path,)), "s3cret")

    def test_every_failure_is_a_trackererror_so_callers_stay_standing(self):
        # /doctor asks this about every integration in turn and catches
        # TrackerError. Anything else escaping -- a decode error, a permission
        # -- ends the whole report, required checks included.
        binary = self.tmp / "binary"
        binary.write_bytes(b"\xff\xfe not utf-8 \x00")
        a_directory = self.tmp / "adir2"
        a_directory.mkdir()
        for path in (binary, a_directory, self.tmp / "missing"):
            with self.subTest(path=path.name):
                with self.assertRaises(tracker.TrackerError):
                    tracker.secret(("CF_TEST_ABSENT",), hint="absent", files=(path,))

    def test_nothing_anywhere_raises_the_caller_s_own_message(self):
        with self.assertRaises(tracker.TrackerError) as ctx:
            tracker.secret(("CF_TEST_ABSENT",), hint="put it in .env, like this")
        self.assertEqual(str(ctx.exception), "put it in .env, like this")


class DotenvTest(unittest.TestCase):
    """.env is where all three secrets live, so it is read the way one is written."""

    def setUp(self) -> None:
        import os
        self.os = os
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-dotenv-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._real_repo = paths.REPO
        self.addCleanup(paths.configure, self._real_repo)
        self.addCleanup(setattr, tracker, "_dotenv_loaded", False)
        paths.configure(self.tmp)
        paths.ENV.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        tracker._dotenv_loaded = False
        tracker.load_dotenv()

    def test_a_byte_order_mark_does_not_become_part_of_the_key_name(self):
        # A shell redirect writes one, plain utf-8 keeps it, and the first key
        # then parses as "\ufeffGEMINI_API_KEY" -- which nothing matches, so
        # the user is told the key is not set while it sits in the file.
        paths.ENV.write_bytes(b"\xef\xbb\xbfCF_TEST_BOM=abc123\n")
        self.addCleanup(self.os.environ.pop, "CF_TEST_BOM", None)
        self.load()
        self.assertEqual(self.os.environ.get("CF_TEST_BOM"), "abc123")

    def test_a_utf16_env_does_not_take_the_caller_down(self):
        # UnicodeDecodeError is not an OSError, so it escaped load_dotenv and
        # ended /doctor's whole report with a traceback.
        paths.ENV.write_bytes(b"\xff\xfeC\x00F\x00=\x00x\x00")
        self.load()  # must not raise


if __name__ == "__main__":
    unittest.main()
