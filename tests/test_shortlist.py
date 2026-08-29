"""The shortlist CLI over the postings table.

    python -m unittest discover tests

Each test runs against a temporary repo root, so it never touches the real
database. The commands are exercised the way the skills call them -- through
their argument namespaces and stdin -- so a renamed flag fails here first.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import paths  # noqa: E402
import shortlist  # noqa: E402
import tracker  # noqa: E402


def days_from_now(n: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=n)).isoformat()


def show_args(**kw) -> SimpleNamespace:
    base = dict(status=None, all=False, include_expired=False, unscored=False,
                min_score=None, limit=None, json=True)
    base.update(kw)
    return SimpleNamespace(**base)


def mark_args(**kw) -> SimpleNamespace:
    base = dict(url=None, id=None, status="skipped", note=None, company=None,
                title=None, location=None, source=None)
    base.update(kw)
    return SimpleNamespace(**base)


class ShortlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-shortlist-")).resolve()
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

    # -- helpers -------------------------------------------------------------

    def run_cmd(self, fn, args, stdin: str | None = None) -> str:
        out = io.StringIO()
        original_stdin = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out):
                fn(args, self.conn, self.cfg)
        finally:
            sys.stdin = original_stdin
        return out.getvalue()

    def add(self, postings: list[dict]) -> str:
        return self.run_cmd(shortlist.cmd_add, SimpleNamespace(file=None), json.dumps(postings))

    def rows(self, **kw) -> list[dict]:
        return json.loads(self.run_cmd(shortlist.cmd_show, show_args(**kw)))

    # -- adding --------------------------------------------------------------

    def test_adding_is_idempotent_on_the_posting_url_however_it_was_copied(self):
        posting = {"title": "Dev", "company": "Acme", "url": "https://example.com/1", "deadline": "rolling"}
        self.assertIn("added 1", self.add([posting]))
        self.assertIn("1 already known", self.add([{**posting, "url": "http://www.example.com/1/?utm_source=x"}]))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["deadline"])
        self.assertEqual(rows[0]["url_key"], "example.com/1")

    def test_a_posting_can_arrive_already_skipped(self):
        self.add([{"title": "Dev", "company": "Acme", "url": "https://example.com/1",
                   "status": "skipped", "note": "USA only"}])
        self.assertEqual(self.rows(), [])
        row = self.rows(all=True)[0]
        self.assertEqual((row["status"], row["note"]), ("skipped", "USA only"))

    # -- checking ------------------------------------------------------------

    def test_check_tells_new_from_known_and_reports_the_verdict(self):
        self.add([{"title": "Dev", "company": "Acme", "url": "https://example.com/1",
                   "status": "skipped", "note": "USA only"}])
        out = json.loads(self.run_cmd(
            shortlist.cmd_check,
            SimpleNamespace(urls=["https://www.example.com/1/?trk=x", "https://example.com/2"], json=True),
        ))
        self.assertTrue(out[0]["known"])
        self.assertEqual((out[0]["status"], out[0]["note"]), ("skipped", "USA only"))
        self.assertFalse(out[1]["known"])
        text = self.run_cmd(shortlist.cmd_check, SimpleNamespace(urls=["https://example.com/2"], json=False))
        self.assertTrue(text.startswith("new"))

    # -- scoring -------------------------------------------------------------

    def test_scores_persist_and_unknown_ids_are_reported(self):
        self.add([{"title": "Dev", "company": "Acme", "url": "https://example.com/1"}])
        pid = self.rows()[0]["id"]
        out = self.run_cmd(shortlist.cmd_merge_scores, SimpleNamespace(file=None), json.dumps({"results": [
            {"id": pid, "score": 80, "verdict": "strong", "strengths": ["Vue"], "gaps": ["k8s"], "deadline": "2099-01-01"},
            {"id": 999, "score": 99, "verdict": "strong"},
        ]}))
        self.assertIn("scored 1", out)
        self.assertIn("1 result(s) referenced ids not in the shortlist", out)
        row = self.rows()[0]
        self.assertEqual((row["score"], row["strengths"], row["deadline"], row["status"]), (80, ["Vue"], "2099-01-01", "ranked"))

    def test_free_text_deadlines_do_not_reach_the_shortlist(self):
        self.add([{"title": "Dev", "company": "Acme", "url": "https://example.com/1"}])
        pid = self.rows()[0]["id"]
        self.run_cmd(shortlist.cmd_merge_scores, SimpleNamespace(file=None),
                     json.dumps({"results": [{"id": pid, "deadline": "ASAP"}]}))
        self.assertIsNone(self.rows()[0]["deadline"])

    # -- expiry --------------------------------------------------------------

    def test_sweep_marks_only_newly_expired_open_entries(self):
        self.add([
            {"title": "A", "company": "Old", "url": "https://x.example/past", "deadline": days_from_now(-5)},
            {"title": "B", "company": "New", "url": "https://x.example/future", "deadline": days_from_now(5)},
            {"title": "C", "company": "Done", "url": "https://x.example/done", "deadline": days_from_now(-5),
             "status": "applied"},
        ])
        out = self.run_cmd(shortlist.cmd_sweep, None)
        self.assertIn("marked 1 posting(s) expired", out)
        by_company = {r["company"]: r["status"] for r in self.rows(all=True)}
        self.assertEqual(by_company, {"Old": "expired", "New": "new", "Done": "applied"})
        self.assertIn("nothing has expired", self.run_cmd(shortlist.cmd_sweep, None))

    # -- verdicts ------------------------------------------------------------

    def test_mark_records_a_url_nobody_scraped_with_its_reason(self):
        out = self.run_cmd(shortlist.cmd_mark, mark_args(
            url="https://jobs.example/9?ref=li", status="skipped", note="Needs Turkish",
            company="Beta", title="React Dev",
        ))
        self.assertTrue(out.startswith("skipped"))
        row = self.rows(status="skipped")[0]
        self.assertEqual((row["company"], row["title"], row["note"], row["url_key"]),
                         ("Beta", "React Dev", "Needs Turkish", "jobs.example/9"))

    def test_mark_without_a_stub_on_an_unknown_url_is_an_error(self):
        with self.assertRaises(tracker.TrackerError):
            self.run_cmd(shortlist.cmd_mark, mark_args(url="https://jobs.example/9", status="skipped"))

    def test_mark_by_id_changes_status_and_keeps_the_rest(self):
        self.add([{"title": "Dev", "company": "Acme", "url": "https://example.com/1", "location": "Lisbon"}])
        pid = self.rows()[0]["id"]
        self.run_cmd(shortlist.cmd_mark, mark_args(id=pid, status="maybe", note="AEM in the stack"))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["note"], row["location"]), ("maybe", "AEM in the stack", "Lisbon"))

    # -- the old JSON file ---------------------------------------------------

    def legacy(self) -> Path:
        path = self.tmp / "seen_jobs.json"
        path.write_text(json.dumps({"seen": {
            "lever_smart_working_hr106": {
                "title": "HR Tool Dev", "company": "Smart Working",
                "url": "https://jobs.lever.co/smart/9b762e77", "first_seen": "2026-06-01",
                "fit": "medium", "status": "skipped", "note": "Requires Turkish - deal-breaker",
            },
            "remotive_reddit_senior_frontend": {
                "title": "Senior Frontend", "company": "Reddit",
                "url": "https://remotive.com/job/123?utm_source=x", "first_seen": "2026-06-02",
                "fit": "high", "status": "new",
            },
            "https://example.com/scored": {
                "title": "Scored", "company": "Acme", "url": "https://example.com/scored",
                "first_seen": "2026-07-01", "status": "new", "score": 71, "verdict": "good",
                "strengths": ["Vue"], "gaps": [], "scored_at": "2026-07-02",
            },
            "junk": {"title": "No way to key this"},
            "weird": {"title": "Odd", "company": "Odd Co", "url": "https://odd.example/1",
                      "status": "paused", "note": "on hold"},
        }}), encoding="utf-8")
        return path

    def test_import_rekeys_legacy_entries_and_keeps_their_verdicts(self):
        out = self.run_cmd(shortlist.cmd_import_json, SimpleNamespace(path=str(self.legacy())))
        self.assertIn("imported 4 posting(s)", out)
        self.assertIn("1 entr(ies) had neither a URL", out)
        self.assertIn("paused", out)
        by_key = {r["url_key"]: r for r in self.rows(all=True)}
        smart = by_key["jobs.lever.co/smart/9b762e77"]
        self.assertEqual((smart["status"], smart["note"], smart["verdict"], smart["score"]),
                         ("skipped", "Requires Turkish - deal-breaker", "moderate", None))
        self.assertEqual(by_key["remotive.com/job/123"]["verdict"], "good")
        self.assertEqual(by_key["example.com/scored"]["score"], 71)
        self.assertEqual(by_key["example.com/scored"]["first_seen"], "2026-07-01")
        odd = by_key["odd.example/1"]
        self.assertEqual((odd["status"], odd["note"]), ("new", "[paused] on hold"))

    def test_import_never_downgrades_an_applied_row_and_fills_in_an_empty_one(self):
        with self.conn:
            tracker.add_application(self.conn, self.cfg, company="Smart Working", role="HR Tool Dev",
                                    url="https://jobs.lever.co/smart/9b762e77")
        self.add([{"title": "Senior Frontend", "company": "Reddit", "url": "https://remotive.com/job/123"}])
        out = self.run_cmd(shortlist.cmd_import_json, SimpleNamespace(path=str(self.legacy())))
        self.assertIn("2 already present", out)
        by_key = {r["url_key"]: r for r in self.rows(all=True)}
        self.assertEqual(by_key["jobs.lever.co/smart/9b762e77"]["status"], "applied")
        self.assertEqual(by_key["jobs.lever.co/smart/9b762e77"]["note"], "Requires Turkish - deal-breaker")
        self.assertEqual(by_key["remotive.com/job/123"]["verdict"], "good")
        # Running it again changes nothing.
        out = self.run_cmd(shortlist.cmd_import_json, SimpleNamespace(path=str(self.legacy())))
        self.assertIn("imported 0 posting(s)", out)

    # -- legacy ratings ------------------------------------------------------

    def test_legacy_fit_maps_to_a_verdict_but_never_to_a_score(self):
        self.assertEqual(shortlist.verdict_of({"fit": "high"}), "good")
        self.assertEqual(shortlist.verdict_of({"fit": "low", "verdict": "strong"}), "strong")
        self.assertIsNone(shortlist.verdict_of({"fit": "excellent"}))
        self.assertIsNone(shortlist.verdict_of({}))

    # -- listing -------------------------------------------------------------

    def test_show_hides_terminal_rows_unless_asked(self):
        self.add([
            {"title": "A", "company": "Open", "url": "https://x.example/a"},
            {"title": "B", "company": "Done", "url": "https://x.example/b", "status": "applied"},
        ])
        self.assertEqual([r["company"] for r in self.rows()], ["Open"])
        self.assertEqual(len(self.rows(all=True)), 2)
        self.assertEqual(len(self.rows(include_expired=True)), 2, "--include-expired still means everything")
        table = self.run_cmd(shortlist.cmd_show, show_args(json=False, all=True))
        self.assertIn("Done", table)
        self.assertIn("applied", table)


if __name__ == "__main__":
    unittest.main()
