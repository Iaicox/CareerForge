"""The company-research cache: freshness, fallbacks, and never calling out
when it does not have to.

    python -m unittest discover -s tests
"""

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

import gemini  # noqa: E402
import research  # noqa: E402
import tracker  # noqa: E402


class ResearchCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-research-"))
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
        self.conn = tracker.connect()

        # Nothing in these tests may reach the network.
        self._real_fetch = gemini.research_company
        self._real_enabled = gemini.task_enabled
        self.calls: list[tuple] = []

        def fake_fetch(name, url=None):
            self.calls.append((name, url))
            return {"name": name, "what_they_do": "fetched", "recent_news": []}

        gemini.research_company = fake_fetch
        gemini.task_enabled = lambda task: True

    def tearDown(self) -> None:
        gemini.research_company = self._real_fetch
        gemini.task_enabled = self._real_enabled
        self.conn.close()
        tracker.REPO = self._real_repo
        tracker.DB_PATH = self._real_db
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def age_cache(self, company: str, days: float) -> None:
        when = datetime.now(timezone.utc) - timedelta(days=days)
        with self.conn:
            self.conn.execute(
                "UPDATE companies SET researched_at = ? WHERE slug = ?",
                (when.strftime("%Y-%m-%d %H:%M:%S"), tracker.slugify(company)),
            )

    # -- freshness ---------------------------------------------------------

    def test_first_call_fetches_and_second_is_a_cache_hit(self):
        _, origin = research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(origin, "gemini")

        data, origin = research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(origin, "cache")
        self.assertIn("cache_age_days", data)
        # The point of the cache is that the second call costs nothing.
        self.assertEqual(len(self.calls), 1)

    def test_expired_cache_is_refetched(self):
        research.get(self.conn, "Acme", None, force=False)
        self.age_cache("Acme", research.cache_days() + 1)

        _, origin = research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(origin, "gemini")
        self.assertEqual(len(self.calls), 2)

    def test_cache_just_inside_the_window_still_hits(self):
        research.get(self.conn, "Acme", None, force=False)
        self.age_cache("Acme", research.cache_days() - 1)

        _, origin = research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(origin, "cache")
        self.assertEqual(len(self.calls), 1)

    def test_force_ignores_a_fresh_cache(self):
        research.get(self.conn, "Acme", None, force=False)
        _, origin = research.get(self.conn, "Acme", None, force=True)
        self.assertEqual(origin, "gemini")
        self.assertEqual(len(self.calls), 2)

    # -- fallbacks ---------------------------------------------------------

    def test_without_delegation_the_caller_is_told_to_do_it(self):
        gemini.task_enabled = lambda task: False
        with self.assertRaises(research.GeminiPass):
            research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(self.calls, [])

    def test_research_stored_by_hand_is_a_cache_hit(self):
        # The cache has to work with Gemini switched off entirely: Claude
        # researches, stores, and the next consumer pays nothing.
        gemini.task_enabled = lambda task: False
        with self.conn:
            research.write_cache(
                self.conn, "Acme", {"name": "Acme", "what_they_do": "by hand"}
            )
        data, origin = research.get(self.conn, "Acme", None, force=False)
        self.assertEqual(origin, "cache")
        self.assertEqual(data["what_they_do"], "by hand")

    # -- storage -----------------------------------------------------------

    def test_writing_research_does_not_clobber_a_known_website(self):
        with self.conn:
            tracker.company_upsert(self.conn, "Acme", "https://acme.example")
            research.write_cache(self.conn, "Acme", {"x": 1}, "https://wrong.example")
        row = research.find_company(self.conn, "Acme")
        self.assertEqual(row["website"], "https://acme.example")

    def test_corrupt_cache_reads_as_a_miss(self):
        with self.conn:
            tracker.company_upsert(self.conn, "Acme")
            self.conn.execute(
                "UPDATE companies SET research_json = ?, researched_at = ? "
                "WHERE slug = 'acme'",
                ("{not json", tracker.now()),
            )
        cached, age = research.read_cache(self.conn, "Acme")
        self.assertIsNone(cached)

    def test_company_name_variants_share_one_cache_entry(self):
        research.get(self.conn, "Acme Corp", None, force=False)
        _, origin = research.get(self.conn, "acme corp", None, force=False)
        self.assertEqual(origin, "cache")


class ExtractJsonTest(unittest.TestCase):
    """Gemini does not always return bare JSON, whatever it is asked."""

    def test_bare(self):
        self.assertEqual(gemini.extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(
            gemini.extract_json('```json\n{"a": 1}\n```'), {"a": 1}
        )

    def test_wrapped_in_prose(self):
        self.assertEqual(
            gemini.extract_json('Here you go:\n{"a": 1}\nHope that helps!'), {"a": 1}
        )

    def test_list_payload(self):
        self.assertEqual(gemini.extract_json("Results: [1, 2, 3]"), [1, 2, 3])

    def test_nothing_parseable(self):
        self.assertIsNone(gemini.extract_json("sorry, I cannot help with that"))
        self.assertIsNone(gemini.extract_json(""))


if __name__ == "__main__":
    unittest.main()
