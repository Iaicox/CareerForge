"""Tracker behaviour that is easy to break and expensive to get wrong.

    python -m unittest discover tests

Each test runs against a temporary repo root, so it never touches the real
database or the real application folders.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

import paths  # noqa: E402
import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402


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

    # -- migrations --------------------------------------------------------

    def test_fresh_database_records_migrations_without_running_them(self):
        # setUp built this database from schema.sql, which already contains
        # everything the migrations would add. Replaying them would fail on
        # duplicate columns, so they must be recorded as a baseline instead.
        recorded = {r["name"] for r in self.conn.execute("SELECT name FROM migrations")}
        on_disk = {p.name for p in tracker.MIGRATIONS_DIR.glob("*.sql")}
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

    def test_has_secret_stays_total_whatever_the_file_holds(self):
        # /doctor asks this about every integration in turn, so one bad file
        # must never take the whole report down.
        binary = self.tmp / "binary"
        binary.write_bytes(b"\xff\xfe not utf-8 \x00")
        a_directory = self.tmp / "adir2"
        a_directory.mkdir()
        for path in (binary, a_directory, self.tmp / "missing"):
            with self.subTest(path=path.name):
                self.assertFalse(tracker.has_secret(("CF_TEST_ABSENT",), files=(path,)))

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
