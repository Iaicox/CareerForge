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

REAL_API_KEY = gemini.api_key
import research  # noqa: E402
import paths  # noqa: E402
import tracker  # noqa: E402


class ResearchCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-research-")).resolve()
        self._real_repo = paths.REPO
        example = paths.CONFIG_EXAMPLE
        paths.configure(self.tmp)
        for stage in paths.STAGES:
            paths.stage_dir(stage).mkdir(parents=True)
        paths.CONFIG_DIR.mkdir(parents=True)
        shutil.copy(example, paths.CONFIG)
        tracker.load_config(force=True)
        tracker.init_db()
        self.conn = tracker.connect()

        # Nothing in these tests may reach the network.
        self._real_fetch = gemini.research_company
        self._real_enabled = gemini.task_enabled
        self.calls: list[tuple] = []

        def fake_fetch(name, url=None, role=None, location=None):
            self.calls.append((name, url) if role is None and location is None
                              else (name, url, role, location))
            return {"name": name, "what_they_do": "fetched", "recent_news": []}

        gemini.research_company = fake_fetch
        gemini.task_enabled = lambda task: True

    def tearDown(self) -> None:
        gemini.research_company = self._real_fetch
        gemini.task_enabled = self._real_enabled
        self.conn.close()
        paths.configure(self._real_repo)
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

    # -- salary ------------------------------------------------------------

    def test_a_role_and_location_reach_the_research_call(self):
        research.get(self.conn, "Acme", "https://acme.example", False,
                     role="Senior Frontend Developer", location="Lisbon")
        self.assertEqual(self.calls, [("Acme", "https://acme.example", "Senior Frontend Developer", "Lisbon")])

    def test_a_cache_from_before_salary_existed_is_refreshed_once(self):
        # The first fetch stored no `salary` key (the fake returns none). With a
        # role asked for, that entry is not good enough -- once.
        research.get(self.conn, "Acme", None, False)
        self.assertEqual(len(self.calls), 1)
        _, origin = research.get(self.conn, "Acme", None, False, role="Dev", location="Lisbon")
        self.assertEqual((origin, len(self.calls)), ("gemini", 2))
        with self.conn:
            research.write_cache(self.conn, "Acme", {"name": "Acme", "salary": []})
        _, origin = research.get(self.conn, "Acme", None, False, role="Dev", location="Lisbon")
        self.assertEqual((origin, len(self.calls)), ("cache", 2))
        # Without a role, an old entry is still a hit.
        with self.conn:
            research.write_cache(self.conn, "Acme", {"name": "Acme"})
        _, origin = research.get(self.conn, "Acme", None, False)
        self.assertEqual(origin, "cache")

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


class RestTransportTest(unittest.TestCase):
    """Gemini is called over its REST API: the configured model, bounded retries.

    The CLI route was dropped after it began rewriting every model whose name
    ends in "flash" to gemini-3.5-flash under a remote flag, ran web search on
    that model whatever was asked for, and retried a quota error without end.
    """

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict, int]] = []
        self.responses: list = []
        self.sleeps: list[float] = []
        for name in ("http_post", "settings", "log_call", "api_key", "sleep"):
            self.addCleanup(setattr, gemini, name, getattr(gemini, name))
        gemini.settings = lambda: {
            "enabled": True, "model": "gemini-3.7-flash", "timeout_seconds": 10,
            "cache_days": 30, "tasks": ["research"], "log": False,
        }
        gemini.log_call = lambda *a, **k: None
        gemini.api_key = lambda: "test-key"
        gemini.sleep = lambda seconds: self.sleeps.append(seconds)

        def fake_post(url, body, timeout):
            self.calls.append((url, body, timeout))
            item = self.responses.pop(0) if self.responses else (200, self.ok("OK"))
            if isinstance(item, BaseException):
                raise item
            return item

        gemini.http_post = fake_post

    @staticmethod
    def ok(text: str) -> dict:
        return {
            "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"totalTokenCount": 3},
            "modelVersion": "gemini-3.7-flash-08-2026",
        }

    @staticmethod
    def quota(delay: str | None) -> tuple[int, dict]:
        err = {"code": 429, "status": "RESOURCE_EXHAUSTED",
               "message": "You exceeded your current quota, please check your plan and billing details."}
        if delay:
            err["details"] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]
        return 429, {"error": err}

    def test_the_configured_model_is_the_one_called(self):
        gemini.call("instruction")
        url = self.calls[0][0]
        self.assertTrue(url.endswith("/models/gemini-3.7-flash:generateContent"), url)
        self.assertEqual(self.calls[0][2], 10)

    def test_the_payload_precedes_the_instruction_in_one_message(self):
        gemini.call("instruction", payload="x" * 200_000)
        text = self.calls[0][1]["contents"][0]["parts"][0]["text"]
        self.assertTrue(text.startswith("x" * 200_000))
        self.assertTrue(text.endswith("\n\ninstruction"))

    def test_research_attaches_web_search_and_extraction_asks_for_json(self):
        self.responses = [(200, self.ok('{"name": "Acme", "what_they_do": "x"}'))]
        gemini.research_company("Acme")
        body = self.calls[0][1]
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertNotIn("responseMimeType", body["generationConfig"], "JSON mode cannot ride with a tool")
        self.assertEqual(len(self.calls), 1, "no role, no salary call")

        self.responses = [(200, self.ok('{"role": "Dev", "company": "Acme"}'))]
        gemini.extract_posting("posting text")
        body = self.calls[1][1]
        self.assertNotIn("tools", body)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")

    def test_a_role_makes_salary_its_own_searched_call(self):
        # Asked alongside news and culture, the model returned an empty salary
        # list for companies whose Glassdoor page it finds in seconds when
        # asked only that. Two calls, and the second cannot sink the first.
        self.responses = [
            (200, self.ok('{"name": "Acme", "what_they_do": "x"}')),
            (200, self.ok('{"salary": [{"role": "Dev", "location": "Lisbon", "currency": "EUR", '
                          '"amount_min": 60000, "amount_max": 70000, "period": "year", "basis": "gross", '
                          '"payments_per_year": 14, "source_url": "https://g.example", "date": "2026-07"}]}')),
        ]
        data = gemini.research_company("Acme", role="Dev", location="Lisbon")
        self.assertEqual(len(self.calls), 2)
        salary_prompt = self.calls[1][1]["contents"][0]["parts"][0]["text"]
        self.assertIn("Dev", salary_prompt)
        self.assertIn("Lisbon", salary_prompt)
        self.assertEqual(self.calls[1][1]["tools"], [{"google_search": {}}])
        self.assertEqual(data["salary"][0]["amount_max"], 70000)
        self.assertNotIn("salary_error", data)

    def test_a_refused_salary_call_keeps_the_research(self):
        self.responses = [(200, self.ok('{"name": "Acme", "what_they_do": "x"}')), self.quota("52418s")]
        data = gemini.research_company("Acme", role="Dev", location="Lisbon")
        self.assertEqual(data["what_they_do"], "x")
        self.assertEqual(data["salary"], [])
        self.assertIn("quota", data["salary_error"])

    def test_a_daily_quota_is_not_retried(self):
        self.responses = [self.quota("52418s")]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("quota", str(ctx.exception))
        self.assertIn("gemini-3.7-flash", str(ctx.exception))
        self.assertNotIn("grounding", str(ctx.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_a_quota_refusal_on_a_search_call_names_grounding(self):
        # Seen live: gemini-3.7-flash answers a plain prompt and refuses the
        # same key with google_search attached -- the free tier has no
        # grounding quota for that model, and the message has to say so.
        self.responses = [self.quota(None)]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction", search=True)
        self.assertIn("Google Search grounding", str(ctx.exception))

    def test_a_short_rate_limit_is_waited_out_once(self):
        self.responses = [self.quota("5s"), (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.sleeps, [5.0])
        self.assertEqual(len(self.calls), 2)
        self.responses = [self.quota("5s"), self.quota("5s"), (200, self.ok("OK"))]
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.call("instruction")

    def test_high_demand_is_retried_twice_then_given_up(self):
        overloaded = (503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                      "message": "This model is currently experiencing high demand."}})
        self.responses = [overloaded, overloaded, overloaded]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("overloaded", str(ctx.exception))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.sleeps, [5, 15])
        self.responses = [overloaded, (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")

    def test_a_timeout_is_a_timeout(self):
        self.responses = [TimeoutError("timed out")]
        with self.assertRaises(gemini.GeminiTimeout):
            gemini.call("instruction")

    def test_an_unknown_model_and_a_bad_key_are_explained(self):
        self.responses = [(404, {"error": {"code": 404, "message": "models/x is not found"}})]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("not available to this key", str(ctx.exception))
        self.responses = [(403, {"error": {"code": 403, "message": "API key not valid"}})]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("not authenticated", str(ctx.exception))

    def test_an_empty_answer_is_bad_output_with_the_reason(self):
        self.responses = [(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})]
        with self.assertRaises(gemini.GeminiBadOutput) as ctx:
            gemini.call("instruction")
        self.assertIn("SAFETY", str(ctx.exception))

    def test_a_missing_key_says_where_to_put_it(self):
        import os
        from unittest import mock
        gemini.api_key = REAL_API_KEY
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                mock.patch.object(gemini, "load_dotenv", lambda: None):
            with self.assertRaises(gemini.GeminiUnavailable) as ctx:
                gemini.api_key()
        self.assertIn(".env", str(ctx.exception))

    def test_rank_unwraps_the_results_list(self):
        self.responses = [(200, self.ok('{"results": [{"id": 1, "score": 80}]}'))]
        self.assertEqual(gemini.rank_postings([{"id": 1, "title": "Dev"}], "criteria"), [{"id": 1, "score": 80}])
        text = self.calls[0][1]["contents"][0]["parts"][0]["text"]
        self.assertTrue(text.startswith("CRITERIA:"))

    def test_the_command_line_length_limit_no_longer_exists(self):
        # 200 KB of posting text went over stdin to the CLI because argv has a
        # ~32 KB limit on Windows; over HTTP it is simply the request body.
        gemini.call("instruction", payload="x" * 300_000)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
