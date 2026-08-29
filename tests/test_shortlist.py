"""The scraper shortlist: scoring persistence, expiry, and legacy entries."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import paths  # noqa: E402
import shortlist  # noqa: E402
import tracker  # noqa: E402


def days_from_now(n: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=n)).isoformat()


class ShortlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-shortlist-")).resolve()
        self._real_repo = paths.REPO
        paths.configure(self.tmp)
        paths.JOB_SCRAPER.mkdir(parents=True)

    def tearDown(self) -> None:
        paths.configure(self._real_repo)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self, entries: dict) -> None:
        shortlist.save({"seen": entries})

    def read(self) -> dict:
        return json.loads(shortlist.seen_path().read_text(encoding="utf-8"))["seen"]

    # -- reading -----------------------------------------------------------

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(shortlist.load(), {"seen": {}})

    def test_corrupt_file_is_reported_not_silently_reset(self):
        shortlist.seen_path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(tracker.TrackerError):
            shortlist.load()

    # -- scoring -----------------------------------------------------------

    def test_scores_persist_and_unknown_ids_are_ignored(self):
        self.seed({"a": {"company": "Acme", "title": "Dev"}})
        results = [
            {"id": "a", "score": 80, "verdict": "strong",
             "strengths": ["Vue"], "gaps": ["k8s"], "deadline": "2099-01-01"},
            {"id": "nope", "score": 99, "verdict": "strong"},
        ]

        class Args:
            file = None

        payload = json.dumps({"results": results})
        original_stdin, sys.stdin = sys.stdin, __import__("io").StringIO(payload)
        try:
            shortlist.cmd_merge_scores(Args())
        finally:
            sys.stdin = original_stdin

        entry = self.read()["a"]
        self.assertEqual(entry["score"], 80)
        self.assertEqual(entry["strengths"], ["Vue"])
        self.assertEqual(entry["deadline"], "2099-01-01")
        self.assertNotIn("nope", self.read())

    def test_free_text_deadlines_do_not_reach_the_shortlist(self):
        self.seed({"a": {"company": "Acme"}})

        class Args:
            file = None

        payload = json.dumps({"results": [{"id": "a", "deadline": "ASAP"}]})
        original_stdin, sys.stdin = sys.stdin, __import__("io").StringIO(payload)
        try:
            shortlist.cmd_merge_scores(Args())
        finally:
            sys.stdin = original_stdin
        self.assertIsNone(self.read()["a"]["deadline"])

    # -- expiry ------------------------------------------------------------

    def test_expiry_is_decided_by_the_date_not_a_stored_flag(self):
        self.assertTrue(shortlist.is_expired({"deadline": days_from_now(-1)}))
        self.assertFalse(shortlist.is_expired({"deadline": days_from_now(1)}))
        self.assertFalse(shortlist.is_expired({"deadline": None}))
        self.assertFalse(shortlist.is_expired({}))

    def test_sweep_marks_only_newly_expired_entries(self):
        self.seed({
            "past": {"company": "Old", "deadline": days_from_now(-5)},
            "future": {"company": "New", "deadline": days_from_now(5)},
            "already": {"company": "Done", "deadline": days_from_now(-5),
                        "status": "expired"},
        })
        shortlist.cmd_sweep(None)
        seen = self.read()
        self.assertEqual(seen["past"]["status"], "expired")
        self.assertNotIn("status", seen["future"])
        self.assertEqual(seen["already"]["status"], "expired")

    # -- legacy entries ----------------------------------------------------

    def test_legacy_fit_is_shown_but_never_counts_as_a_score(self):
        entry = {"company": "Acme", "fit": "high"}
        self.assertEqual(shortlist.verdict_of(entry), "good")
        self.assertIsNone(entry.get("score"))

    def test_a_real_verdict_wins_over_the_legacy_rating(self):
        self.assertEqual(
            shortlist.verdict_of({"fit": "low", "verdict": "strong"}), "strong"
        )

    def test_unknown_legacy_rating_is_not_invented(self):
        self.assertIsNone(shortlist.verdict_of({"fit": "excellent"}))
        self.assertIsNone(shortlist.verdict_of({}))

    # -- ordering ----------------------------------------------------------

    def test_scored_entries_sort_above_unscored(self):
        rows = [
            {"company": "No score"},
            {"company": "Low", "score": 40},
            {"company": "High", "score": 90},
        ]
        rows.sort(key=shortlist.sort_key)
        self.assertEqual([r["company"] for r in rows], ["High", "Low", "No score"])

    # -- adding ------------------------------------------------------------

    def test_adding_is_idempotent_on_the_posting_url(self):
        class Args:
            file = None

        posting = [{"title": "Dev", "company": "Acme",
                    "url": "https://example.com/1", "deadline": "rolling"}]
        for _ in range(2):
            original_stdin, sys.stdin = sys.stdin, __import__("io").StringIO(
                json.dumps(posting)
            )
            try:
                shortlist.cmd_add(Args())
            finally:
                sys.stdin = original_stdin

        seen = self.read()
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen["https://example.com/1"]["deadline"])


if __name__ == "__main__":
    unittest.main()
