"""The postings table: every job posting ever seen, in one place.

Before it existed the scraper kept a JSON file keyed on the raw URL, /apply
never wrote to it, and the tracker compared URLs with its own bare strip() --
so a link with a different ?trk= parameter was a new posting, and a role the
user had declined came back on the next scrape at full price.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import paths  # noqa: E402
import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402


class PostingsFixture(unittest.TestCase):
    """A throwaway repo with an empty tracker, laid out like the real one."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-postings-")).resolve()
        self._real_repo = paths.REPO
        example = paths.CONFIG_EXAMPLE
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
        return tracker.add_application(self.conn, self.cfg, company=company, role=role, url=url, **kw)


class NormalizeUrlTest(unittest.TestCase):
    def test_variants_of_one_posting_share_a_key(self):
        base = tracker.normalize_url("https://www.linkedin.com/jobs/view/4441450970/")
        for variant in (
            "http://linkedin.com/jobs/view/4441450970",
            "https://www.linkedin.com/jobs/view/4441450970/?refId=abc&trackingId=xyz",
            "https://www.linkedin.com/jobs/view/4441450970?utm_source=share&utm_medium=x",
            "https://LinkedIn.com/jobs/view/4441450970/#top",
            "www.linkedin.com/jobs/view/4441450970",
        ):
            with self.subTest(variant=variant):
                self.assertEqual(tracker.normalize_url(variant), base)
        self.assertEqual(base, "linkedin.com/jobs/view/4441450970")

    def test_parameters_that_name_the_posting_are_kept(self):
        self.assertEqual(
            tracker.normalize_url("https://boards.greenhouse.io/acme/jobs/123?gh_jid=123&gh_src=li"),
            "boards.greenhouse.io/acme/jobs/123?gh_jid=123",
        )

    def test_different_postings_stay_different(self):
        self.assertNotEqual(
            tracker.normalize_url("https://acme.example/jobs/1"),
            tracker.normalize_url("https://acme.example/jobs/2"),
        )

    def test_empty_is_empty(self):
        self.assertEqual(tracker.normalize_url(None), "")
        self.assertEqual(tracker.normalize_url("   "), "")

    def test_posting_key_falls_back_to_company_and_title(self):
        self.assertEqual(tracker.posting_key(None, "Acme GmbH", " Senior Dev "), "acme-gmbh::senior dev")
        with self.assertRaises(TrackerError):
            tracker.posting_key(None, "Acme", None)


class PostingStatusConfigTest(PostingsFixture):
    def test_defaults_apply_when_the_config_has_no_table(self):
        # The example config carries the block; a config written before
        # postings existed does not, and must keep working unchanged.
        cfg = tracker.Config({k: v for k, v in self.cfg.data.items() if k != "posting_statuses"}, self.cfg.source)
        self.assertEqual(
            [s["id"] for s in cfg.posting_statuses],
            ["new", "ranked", "maybe", "applied", "skipped", "expired"],
        )
        self.assertEqual(cfg.label("posting_statuses", "skipped"), "Skipped")
        self.assertTrue(cfg.posting_is_terminal("applied"))
        self.assertFalse(cfg.posting_is_terminal("maybe"))
        with self.assertRaises(TrackerError):
            cfg.posting_status("archived")

    def test_the_example_config_carries_the_same_ids(self):
        self.assertEqual(
            [s["id"] for s in self.cfg.posting_statuses],
            [s["id"] for s in tracker.DEFAULT_POSTING_STATUSES],
        )

    def test_migration_005_is_on_disk(self):
        self.assertTrue((tracker.MIGRATIONS_DIR / "005_postings.sql").exists())


class PostingsTest(PostingsFixture):
    def posting(self, **kw) -> dict:
        p = {"title": "Frontend Developer", "company": "Acme", "url": "https://acme.example/jobs/1"}
        p.update(kw)
        return p

    def rows(self, **kw):
        return tracker.list_postings(self.conn, self.cfg, **kw)

    # -- recording -----------------------------------------------------------

    def test_add_dedups_url_variants_and_keeps_the_first(self):
        with self.conn:
            added, known = tracker.upsert_postings(self.conn, self.cfg, [
                self.posting(),
                self.posting(url="https://www.acme.example/jobs/1/?utm_source=x", title="Other title"),
            ])
        self.assertEqual((added, known), (1, 1))
        self.assertEqual(self.rows()[0]["title"], "Frontend Developer")

    def test_add_honours_a_verdict_given_with_the_posting(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [
                self.posting(status="skipped", note="Requires Turkish"),
            ])
        row = self.rows(all=True)[0]
        self.assertEqual((row["status"], row["note"]), ("skipped", "Requires Turkish"))
        self.assertEqual(self.rows(), [], "a terminal row is not part of the open shortlist")

    def test_add_refuses_an_unknown_status(self):
        with self.assertRaises(TrackerError):
            tracker.upsert_postings(self.conn, self.cfg, [self.posting(status="archived")])

    def test_free_text_deadlines_are_stored_as_none(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting(deadline="rolling")])
        self.assertIsNone(self.rows()[0]["deadline"])

    # -- verdicts ------------------------------------------------------------

    def test_mark_on_an_unseen_url_records_it_when_given_a_stub(self):
        with self.conn:
            row = tracker.mark_posting(
                self.conn, self.cfg, "https://acme.example/jobs/9?trk=x", "skipped",
                note="React only", stub={"title": "React Dev", "company": "Acme"},
            )
        self.assertEqual(row["url_key"], "acme.example/jobs/9")
        self.assertEqual((row["status"], row["note"], row["title"]), ("skipped", "React only", "React Dev"))

    def test_mark_on_an_unseen_url_without_a_stub_is_refused(self):
        with self.assertRaises(TrackerError):
            tracker.mark_posting(self.conn, self.cfg, "https://acme.example/jobs/9", "skipped")

    def test_mark_keeps_what_rank_wrote_and_changes_only_the_verdict(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
            tracker.merge_scores(self.conn, self.cfg, [
                {"id": self.rows()[0]["id"], "score": 80, "verdict": "good", "strengths": ["Vue"]},
            ])
            row = tracker.mark_posting(self.conn, self.cfg, "https://acme.example/jobs/1", "maybe", note="AEM in the stack")
        self.assertEqual((row["status"], row["score"], row["note"]), ("maybe", 80, "AEM in the stack"))
        self.assertEqual(self.rows()[0]["strengths"], ["Vue"])

    def test_mark_by_id_with_a_stale_timestamp_is_refused(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
        row = self.rows()[0]
        with self.assertRaises(TrackerError):
            tracker.mark_posting(self.conn, self.cfg, row["id"], "skipped", expected_updated_at="1999-01-01 00:00:00")
        with self.conn:
            tracker.mark_posting(self.conn, self.cfg, row["id"], "skipped", expected_updated_at=row["updated_at"])
        self.assertEqual(self.rows(all=True)[0]["status"], "skipped")

    # -- scores --------------------------------------------------------------

    def test_scores_persist_ranked_follows_and_unknown_ids_are_reported(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
            pid = self.rows()[0]["id"]
            merged, unknown = tracker.merge_scores(self.conn, self.cfg, [
                {"id": pid, "score": 80, "verdict": "strong", "strengths": ["Vue"],
                 "gaps": ["k8s"], "deadline": "2099-01-01"},
                {"id": 999, "score": 99},
                {"id": "https://nowhere.example/x", "score": 1},
            ])
        self.assertEqual((merged, unknown), (1, 2))
        row = self.rows()[0]
        self.assertEqual(row["status"], "ranked")
        self.assertEqual((row["score"], row["strengths"], row["gaps"], row["deadline"]), (80, ["Vue"], ["k8s"], "2099-01-01"))
        self.assertIsNotNone(row["scored_at"])

    def test_scores_match_on_the_url_too(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
            merged, _ = tracker.merge_scores(self.conn, self.cfg, [
                {"id": "https://www.acme.example/jobs/1/", "score": 50, "verdict": "moderate"},
            ])
        self.assertEqual(merged, 1)
        self.assertEqual(self.rows()[0]["score"], 50)

    def test_a_free_text_deadline_from_rank_does_not_reach_the_table(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
            tracker.merge_scores(self.conn, self.cfg, [{"id": self.rows()[0]["id"], "deadline": "ASAP"}])
        self.assertIsNone(self.rows()[0]["deadline"])

    # -- expiry --------------------------------------------------------------

    def test_sweep_expires_open_rows_only(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [
                self.posting(url="https://acme.example/past", deadline="2000-01-01"),
                self.posting(url="https://acme.example/future", deadline="2099-01-01"),
                self.posting(url="https://acme.example/skipped", deadline="2000-01-01", status="skipped", note="no"),
                self.posting(url="https://acme.example/maybe", deadline="2000-01-01", status="maybe"),
            ])
            expired = tracker.sweep_postings(self.conn, self.cfg)
        self.assertEqual(len(expired), 2)
        by_key = {r["url_key"]: r["status"] for r in self.rows(all=True)}
        self.assertEqual(by_key["acme.example/past"], "expired")
        self.assertEqual(by_key["acme.example/maybe"], "expired")
        self.assertEqual(by_key["acme.example/future"], "new")
        self.assertEqual(by_key["acme.example/skipped"], "skipped")

    # -- listing -------------------------------------------------------------

    def test_open_rows_by_default_everything_with_all_and_scored_first(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [
                self.posting(url="https://acme.example/a", company="No score"),
                self.posting(url="https://acme.example/b", company="Low"),
                self.posting(url="https://acme.example/c", company="High"),
                self.posting(url="https://acme.example/d", company="Gone", status="skipped"),
            ])
            ids = {r["company"]: r["id"] for r in self.rows()}
            tracker.merge_scores(self.conn, self.cfg, [
                {"id": ids["Low"], "score": 40}, {"id": ids["High"], "score": 90},
            ])
        self.assertEqual([r["company"] for r in self.rows()], ["High", "Low", "No score"])
        self.assertEqual(len(self.rows(all=True)), 4)
        self.assertEqual([r["company"] for r in self.rows(status="skipped")], ["Gone"])
        self.assertEqual([r["company"] for r in self.rows(unscored=True)], ["No score"])
        self.assertEqual([r["company"] for r in self.rows(min_score=50)], ["High"])
        self.assertEqual(len(self.rows(limit=1)), 1)

    def test_table_data_carries_the_status_options_in_config_order(self):
        data = tracker.postings_table_data(self.conn, self.cfg)
        self.assertEqual([s["id"] for s in data["statuses"]], ["new", "ranked", "maybe", "applied", "skipped", "expired"])
        self.assertTrue(data["statuses"][3]["terminal"])
        self.assertEqual(data["rows"], [])

    # -- the application side --------------------------------------------------

    def test_recording_an_application_marks_its_posting_applied(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
            row = self.add(company="Acme", role="Frontend Developer", url="https://www.acme.example/jobs/1/?trk=share")
        posting = self.rows(all=True)[0]
        self.assertEqual((posting["status"], posting["application_id"], posting["application_slug"]), ("applied", row["id"], row["slug"]))
        self.assertEqual(len(self.rows(all=True)), 1, "the application did not create a second posting")

    def test_an_application_nobody_scraped_still_gets_a_posting(self):
        with self.conn:
            row = self.add(company="Beta", role="Vue Engineer", url="https://beta.example/jobs/7")
        posting = self.rows(all=True)[0]
        self.assertEqual((posting["status"], posting["source"], posting["title"], posting["application_id"]), ("applied", "apply", "Vue Engineer", row["id"]))

    def test_an_application_without_a_url_is_keyed_on_company_and_role(self):
        with self.conn:
            self.add(company="Gamma", role="Senior Dev", url=None)
        self.assertEqual(self.rows(all=True)[0]["url_key"], "gamma::senior dev")

    def test_find_matches_a_tracked_url_that_differs_only_by_tracking_parameters(self):
        with self.conn:
            self.add(company="Acme", role="Dev", url="https://acme.example/jobs/1")
        hits = tracker.find_applications(self.conn, url="https://www.acme.example/jobs/1/?utm_source=linkedin")
        self.assertEqual(len(hits), 1)
        with self.assertRaises(TrackerError):
            with self.conn:
                self.add(company="Acme", role="Dev", url="http://acme.example/jobs/1#x")

    def test_backfill_is_idempotent(self):
        with self.conn:
            self.add(company="Acme", role="Dev", url="https://acme.example/jobs/1")
            self.add(company="Beta", role="Dev", url=None)
            self.conn.execute("DELETE FROM postings")
        with self.conn:
            first = tracker.backfill_postings(self.conn, self.cfg)
            second = tracker.backfill_postings(self.conn, self.cfg)
        self.assertEqual((first, second), (2, 0))
        self.assertEqual({r["status"] for r in self.rows(all=True)}, {"applied"})

    def test_export_carries_postings(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [self.posting()])
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tracker.main(["export"])
        import json
        self.assertEqual(len(json.loads(out.getvalue())["postings"]), 1)


if __name__ == "__main__":
    unittest.main()
