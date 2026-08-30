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

    def test_a_refused_salary_call_waits_for_the_refusal_to_lift(self):
        # Without the marker, every --role lookup re-ran the whole grounded
        # research and spent more of the quota that had just refused.
        soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        with self.conn:
            research.write_cache(self.conn, "Acme", {
                "name": "Acme",
                "salary_error": "no model in the search pool could answer",
                "salary_retry_after": soon,
            })
        _, origin = research.get(self.conn, "Acme", None, False, role="Dev", location="Lisbon")
        self.assertEqual((origin, len(self.calls)), ("cache", 0))

        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
        with self.conn:
            research.write_cache(self.conn, "Acme", {
                "name": "Acme", "salary_error": "...", "salary_retry_after": past})
        _, origin = research.get(self.conn, "Acme", None, False, role="Dev", location="Lisbon")
        self.assertEqual((origin, len(self.calls)), ("gemini", 1),
                         "once it has lifted, ask again")

    def test_an_entry_from_before_the_question_is_still_refreshed_at_once(self):
        # No marker at all means nobody has asked, which is not a refusal.
        with self.conn:
            research.write_cache(self.conn, "Acme", {"name": "Acme"})
        _, origin = research.get(self.conn, "Acme", None, False, role="Dev", location="Lisbon")
        self.assertEqual(origin, "gemini")

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
    """Gemini over its REST API: a pool of models, cooldowns, bounded retries.

    The CLI route was dropped after it began rewriting every model whose name
    ends in "flash" to gemini-3.5-flash under a remote flag, ran web search on
    that model whatever was asked for, and retried a quota error without end.
    """

    POOL = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-2.5-flash"]
    SEARCH_POOL = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    T0 = datetime(2026, 8, 29, 19, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.calls: list[tuple[str, dict, int]] = []
        self.responses: list = []
        self.sleeps: list[float] = []
        self.clock = self.T0
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-gemini-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._real_repo = paths.REPO
        self.addCleanup(paths.configure, self._real_repo)
        paths.configure(self.tmp)
        for name in ("http_post", "settings", "log_call", "api_key", "sleep", "now"):
            self.addCleanup(setattr, gemini, name, getattr(gemini, name))
        self.addCleanup(gemini.UNKNOWN_MODELS.clear)
        gemini.UNKNOWN_MODELS.clear()
        gemini.settings = lambda: {
            "enabled": True, "models": list(self.POOL), "search_models": list(self.SEARCH_POOL),
            "timeout_seconds": 10, "cache_days": 30,
            "tasks": ["research"], "log": False,
        }
        gemini.log_call = lambda *a, **k: None
        gemini.api_key = lambda: "test-key"
        gemini.sleep = lambda seconds: self.sleeps.append(seconds)
        gemini.now = lambda: self.clock

        def fake_post(url, data, timeout):
            # The transport takes encoded bytes; the assertions below are all
            # about what was in them.
            self.calls.append((url, json.loads(data), timeout))
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
        }

    @staticmethod
    def quota(delay: str | None, quota_id: str | None = None) -> tuple[int, dict]:
        err: dict = {"code": 429, "status": "RESOURCE_EXHAUSTED",
                     "message": "You exceeded your current quota, please check your plan and billing details.",
                     "details": []}
        if delay:
            err["details"].append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay})
        if quota_id:
            err["details"].append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": quota_id}]})
        return 429, {"error": err}

    def models_called(self) -> list[str]:
        return [c[0].split("/models/")[1].split(":")[0] for c in self.calls]

    def cooldowns(self) -> dict:
        return json.loads(paths.GEMINI_COOLDOWNS.read_text(encoding="utf-8"))

    # -- pools ---------------------------------------------------------------

    def test_a_plain_call_starts_the_general_pool_and_search_the_search_pool(self):
        gemini.call("instruction")
        gemini.call("instruction", search=True)
        self.assertEqual(self.models_called(), ["gemini-3.7-flash", "gemini-2.5-flash"])
        self.assertEqual(self.calls[1][1]["tools"], [{"google_search": {}}])
        self.assertEqual(self.calls[0][2], 10)
        self.assertEqual(gemini.LAST_MODEL, "gemini-2.5-flash")

    def test_an_explicit_model_is_tried_alone(self):
        self.responses = [self.quota("5s", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")]
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.call("instruction", model="gemini-2.5-flash")
        self.assertEqual(self.models_called(), ["gemini-2.5-flash"])

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
        self.assertEqual(self.models_called()[1], "gemini-3.7-flash", "extraction uses the general pool")

    def test_a_role_makes_salary_its_own_searched_call(self):
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
        self.responses = [(200, self.ok('{"name": "Acme", "what_they_do": "x"}')),
                          self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
                          self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
        data = gemini.research_company("Acme", role="Dev", location="Lisbon")
        self.assertEqual(data["what_they_do"], "x")
        self.assertIn("no model in the search pool", data["salary_error"])
        # No `salary` key: the question was asked and not answered, which is
        # what research.get() reads to know the entry is worth refreshing. An
        # empty list here would freeze a quota blip in for cache_days.
        self.assertNotIn("salary", data)
        # And it carries the moment the refusal lifts, so the retry is not
        # every lookup: both search models are out until Pacific midnight.
        self.assertEqual(data["salary_retry_after"], "2026-08-30T07:00:00+00:00")

    def test_research_without_a_role_claims_nothing_about_pay(self):
        # /interview researches with no --role. Writing salary: [] there would
        # tell the later /apply that pay had been looked into.
        self.responses = [(200, self.ok('{"name": "Acme", "what_they_do": "x"}'))]
        self.assertNotIn("salary", gemini.research_company("Acme"))

    # -- 429: cooldown and move on ------------------------------------------

    def test_a_per_minute_limit_moves_on_at_once_and_cools_for_its_retry_delay(self):
        self.responses = [self.quota("7s", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"),
                          (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash", "gemini-3.6-flash"])
        self.assertEqual(self.sleeps, [], "no waiting on a limited model; the next one is asked")
        cd = self.cooldowns()["gemini-3.7-flash"]
        self.assertEqual(cd["until"], (self.T0 + timedelta(seconds=7)).isoformat(timespec="seconds"))
        self.assertIn("per-minute", cd["reason"])

    def test_a_per_minute_limit_without_a_delay_cools_for_a_minute(self):
        self.responses = [self.quota(None, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")]
        gemini.call("instruction")
        self.assertEqual(self.cooldowns()["gemini-3.7-flash"]["until"],
                         (self.T0 + timedelta(seconds=60)).isoformat(timespec="seconds"))

    def test_a_daily_quota_cools_until_los_angeles_midnight(self):
        self.responses = [self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
        gemini.call("instruction")
        cd = self.cooldowns()["gemini-3.7-flash"]
        # 2026-08-29 19:00 UTC is 12:00 PDT; the next midnight PDT is 07:00 UTC on the 30th.
        self.assertEqual(cd["until"], "2026-08-30T07:00:00+00:00")
        self.assertIn("daily", cd["reason"])

    def test_a_daily_quota_with_a_retry_delay_uses_the_delay(self):
        self.responses = [self.quota("52418s", "GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
        gemini.call("instruction")
        self.assertEqual(self.cooldowns()["gemini-3.7-flash"]["until"],
                         (self.T0 + timedelta(seconds=52418)).isoformat(timespec="seconds"))

    def test_a_cooling_model_is_skipped_and_returns_when_the_time_has_passed(self):
        self.responses = [self.quota("30s", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")]
        gemini.call("instruction")
        self.calls.clear()
        gemini.call("instruction")
        self.assertEqual(self.models_called(), ["gemini-3.6-flash"], "still cooling")
        self.clock = self.T0 + timedelta(seconds=31)
        self.calls.clear()
        gemini.call("instruction")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash"], "back in rotation")

    def test_the_cooldown_file_lands_under_data_state(self):
        # The tool used to expose a cooldowns_path() seam for the tests to
        # redirect, so nothing checked where the file actually goes.
        gemini.cool_down("gemini-3.7-flash", self.T0 + timedelta(hours=1), "daily quota")
        self.assertTrue((self.tmp / "data" / "state" / "gemini-cooldowns.json").exists())

    def test_cooldowns_survive_across_processes(self):
        # The file is the memory: another run sees the same cooldown.
        gemini.cool_down("gemini-3.7-flash", self.T0 + timedelta(hours=1), "daily quota")
        self.assertIsNotNone(gemini.cooling_until("gemini-3.7-flash"))
        gemini.call("instruction")
        self.assertEqual(self.models_called(), ["gemini-3.6-flash"])

    def test_when_the_whole_pool_is_cooling_the_caller_is_told_why(self):
        for m in self.POOL:
            gemini.cool_down(m, self.T0 + timedelta(hours=1), "daily quota")
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("cooling down", str(ctx.exception))
        self.assertEqual(self.calls, [])

    def test_a_search_refusal_hints_at_grounding(self):
        self.responses = [self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
                          self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction", search=True)
        self.assertIn("Google Search grounding", str(ctx.exception))
        self.assertIn("search pool", str(ctx.exception))

    def test_a_plain_refusal_leaves_the_model_free_for_grounded_work(self):
        # gemini-2.5-flash is the general pool's last resort and the search
        # pool's first choice, and the two quotas are separate.
        gemini.cool_down("gemini-2.5-flash", self.T0 + timedelta(hours=1), "daily quota")
        self.assertIsNone(gemini.cooling_until("gemini-2.5-flash", search=True))
        gemini.call("instruction", search=True)
        self.assertEqual(self.models_called(), ["gemini-2.5-flash"])

    def test_a_grounded_refusal_leaves_the_model_free_for_plain_work(self):
        self.responses = [self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
                          (200, self.ok("OK"))]
        gemini.call("instruction", search=True)
        self.assertIsNotNone(gemini.cooling_until("gemini-2.5-flash", search=True))
        self.assertIsNone(gemini.cooling_until("gemini-2.5-flash"),
                          "the grounding quota is not the request quota")
        self.calls.clear()
        gemini.call("instruction")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash"])

    def test_a_probe_reports_a_refusal_without_benching_anything(self):
        # `check` runs this way: a diagnostic that disables what it diagnoses
        # turns three runs of /doctor into a day without delegation.
        self.responses = [self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 3
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.call("instruction", cool=False)
        self.assertEqual(gemini.load_cooldowns(), {})

    # -- errors that belong to one model, not to the pool -------------------

    def test_an_unsupported_request_moves_on_to_the_next_model(self):
        # A model that will not take the search tool answers 400, not 429.
        self.responses = [(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                           "message": "google_search is not supported"}}),
                          (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction", search=True), "OK")
        self.assertEqual(self.models_called(), ["gemini-2.5-flash", "gemini-2.5-flash-lite"])

    def test_a_malformed_key_is_a_400_and_still_stops_the_pool(self):
        self.responses = [(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                           "message": "API key not valid. Please pass a valid API key."}})]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("not authenticated", str(ctx.exception))
        self.assertEqual(len(self.calls), 1, "no other model would fix the key")

    def test_the_timeout_is_the_budget_for_the_pool_not_for_each_model(self):
        # It used to be the socket timeout of every model in turn, so a stalled
        # API cost len(pool) x timeout_seconds -- half an hour under the shipped
        # config -- from a setting that reads like a bound.
        clock = [0.0]
        self.addCleanup(setattr, gemini, "time", gemini.time)
        gemini.time = type("Clock", (), {"monotonic": staticmethod(lambda: clock[0]),
                                         "sleep": staticmethod(lambda s: None)})

        def crawl(url, body, timeout):
            self.calls.append((url, body, timeout))
            clock[0] += timeout  # the socket timeout is spent in full
            raise TimeoutError("timed out")

        gemini.http_post = crawl
        with self.assertRaises(gemini.GeminiTimeout) as ctx:
            gemini.call("instruction")
        self.assertEqual([c[2] for c in self.calls], [10],
                         "the first model spent the budget; the rest are not asked")
        self.assertIn("budget was spent", str(ctx.exception))

    # -- what went wrong, as a fact rather than a sentence ------------------

    def test_every_failure_carries_the_kind_the_branch_knew(self):
        overloaded = (503, {"error": {"code": 503, "message": "high demand"}})
        for kind, responses, tried in (
            ("quota_daily",
             [self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 3, 3),
            ("quota_minute",
             [self.quota("7s", "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")] * 3, 3),
            ("unknown_model", [(404, {"error": {"code": 404, "message": "no such model"}})] * 3, 3),
            # Two models, the same complaint: the third is not asked. See
            # test_the_same_400_from_two_models_is_the_request_s_fault.
            ("http_error",
             [(400, {"error": {"code": 400, "message": "google_search is not supported"}})] * 3, 2),
            ("timeout", [TimeoutError("timed out")] * 3, 3),
            ("no_answer",
             [(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})] * 3, 3),
            ("overloaded", [overloaded] * 9, 3),  # three tries each, then the next model
        ):
            with self.subTest(kind=kind):
                gemini.UNKNOWN_MODELS.clear()
                gemini.save_cooldowns({})
                self.responses = list(responses)
                with self.assertRaises(gemini.GeminiUnavailable) as ctx:
                    gemini.call("instruction")
                self.assertEqual({r.kind for r in ctx.exception.reasons}, {kind})
                self.assertEqual([r.model for r in ctx.exception.reasons], self.POOL[:tried])

    def test_the_same_400_from_two_models_is_the_request_s_fault(self):
        # summarize() sends up to a megabyte, so "payload size exceeds the
        # limit" is reachable -- and re-uploading it to the rest of the pool
        # cannot change any model's mind.
        same = (400, {"error": {"code": 400,
                                "message": "request payload size exceeds the limit"}})
        self.responses = [same] * 3
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction", payload="x" * 5000)
        self.assertEqual(len(ctx.exception.reasons), 2)
        self.assertEqual(len(self.calls), 2, "the third model is never uploaded to")

    def test_two_different_400s_are_each_that_model_s_own_problem(self):
        # The common case: a model that will not take the search tool, behind
        # one that will not take JSON mode, in front of one that answers.
        self.responses = [
            (400, {"error": {"code": 400, "message": "google_search is not supported"}}),
            (400, {"error": {"code": 400, "message": "responseMimeType is not supported"}}),
            (200, self.ok("OK")),
        ]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.models_called(), self.POOL)

    def test_a_quota_says_when_it_comes_back(self):
        # The remedy for a daily quota is "wait", so the time has to survive as
        # a value rather than only inside the sentence.
        self.responses = [self.quota("52418s", "GenerateRequestsPerDayPerProjectPerModel-FreeTier")] * 3
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        first = ctx.exception.reasons[0]
        self.assertEqual(first.until, (self.T0 + timedelta(seconds=52418)).isoformat(timespec="seconds"))

    def test_a_cooling_model_is_its_own_kind(self):
        for m in self.POOL:
            gemini.cool_down(m, self.T0 + timedelta(hours=1), "daily quota")
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertEqual({r.kind for r in ctx.exception.reasons}, {"cooling"})
        self.assertTrue(all(r.until for r in ctx.exception.reasons))

    def test_a_bad_key_is_tagged_auth(self):
        self.responses = [(403, {"error": {"code": 403, "message": "API key not valid"}})]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertEqual(ctx.exception.kind, "auth")

    def test_the_timeout_verdict_follows_the_kinds_not_the_wording(self):
        self.responses = [TimeoutError("timed out")] * 3
        with self.assertRaises(gemini.GeminiTimeout):
            gemini.call("instruction")
        # One refusal among the timeouts and the pool was not merely slow.
        gemini.save_cooldowns({})
        self.responses = [TimeoutError("timed out"), TimeoutError("timed out"),
                          self.quota(None, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertNotIsInstance(ctx.exception, gemini.GeminiTimeout)

    def test_a_cooldown_file_full_of_junk_does_not_stop_the_pool(self):
        paths.GEMINI_COOLDOWNS.parent.mkdir(parents=True, exist_ok=True)
        paths.GEMINI_COOLDOWNS.write_text(json.dumps({
            "gemini-3.7-flash": "not a dict at all",
            "gemini-3.6-flash": {"until": "nonsense"},
            "gemini-2.5-flash": None,
            "stray": [1, 2, 3],
        }), encoding="utf-8")
        self.assertIsNone(gemini.cooling_until("gemini-3.7-flash"))
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash"],
                         "an unreadable cooldown means not cooling")

    # -- 404: unknown until restart ----------------------------------------

    def test_an_unknown_model_is_skipped_for_the_rest_of_the_process(self):
        self.responses = [(404, {"error": {"code": 404, "message": "models/gemini-3.7-flash is not found"}}),
                          (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash", "gemini-3.6-flash"])
        self.calls.clear()
        gemini.call("instruction")
        self.assertEqual(self.models_called(), ["gemini-3.6-flash"], "not asked again")
        self.assertNotIn("gemini-3.7-flash", gemini.load_cooldowns(), "not a cooldown: it ends with the process")

    # -- the rest -------------------------------------------------------------

    def test_high_demand_is_retried_twice_then_the_next_model_is_asked(self):
        base = gemini.settings()
        gemini.settings = lambda: {**base, "timeout_seconds": 120}  # room for 5s + 15s
        overloaded = (503, {"error": {"code": 503, "status": "UNAVAILABLE",
                                      "message": "This model is currently experiencing high demand."}})
        self.responses = [overloaded, overloaded, overloaded, (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.assertEqual(self.models_called(), ["gemini-3.7-flash"] * 3 + ["gemini-3.6-flash"])
        self.assertEqual(self.sleeps, [5, 15])

    def test_a_timeout_moves_on_and_is_reported_when_every_model_times_out(self):
        self.responses = [TimeoutError("timed out"), (200, self.ok("OK"))]
        self.assertEqual(gemini.call("instruction"), "OK")
        self.responses = [TimeoutError("timed out")] * 3
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("timed out", str(ctx.exception))

    def test_a_bad_key_is_an_immediate_stop(self):
        self.responses = [(403, {"error": {"code": 403, "message": "API key not valid"}})]
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("not authenticated", str(ctx.exception))
        self.assertEqual(len(self.calls), 1, "no other model would fix the key")

    def test_an_empty_answer_moves_on_with_the_reason(self):
        self.responses = [(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})] * 3
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction")
        self.assertIn("SAFETY", str(ctx.exception))

    def test_a_missing_key_says_where_to_put_it(self):
        import os
        from unittest import mock
        gemini.api_key = REAL_API_KEY
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                mock.patch.object(tracker, "load_dotenv", lambda: None):
            with self.assertRaises(gemini.GeminiUnavailable) as ctx:
                gemini.api_key()
        self.assertIn(".env", str(ctx.exception))

    def test_rank_unwraps_the_results_list(self):
        self.responses = [(200, self.ok('{"results": [{"id": 1, "score": 80}]}'))]
        self.assertEqual(gemini.rank_postings([{"id": 1, "title": "Dev"}], "criteria"), [{"id": 1, "score": 80}])
        text = self.calls[0][1]["contents"][0]["parts"][0]["text"]
        self.assertTrue(text.startswith("CRITERIA:"))


class PacificMidnightFallbackTest(unittest.TestCase):
    """The no-tz-database path.

    It is dead wherever zoneinfo has data and live wherever it does not, so
    the environment decides which branch a test of pacific_midnight_after()
    exercises. These call the fallback directly.
    """

    def test_it_agrees_with_zoneinfo_across_a_year(self):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo("America/Los_Angeles")
        except Exception:  # no tz database here; the other test still runs
            self.skipTest("no tz database to compare against")
        when = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
        for _ in range(366):
            self.assertEqual(gemini._pacific_midnight_by_rule(when),
                             gemini.pacific_midnight_after(when), when.isoformat())
            when += timedelta(days=1)

    def test_both_offsets_and_the_year_rollover(self):
        for when, expected in (
            # 12:00 PDT, UTC-7
            (datetime(2026, 8, 29, 19, 0, tzinfo=timezone.utc), "2026-08-30T07:00:00+00:00"),
            # 12:00 PST, UTC-8
            (datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc), "2026-01-16T08:00:00+00:00"),
            # the hour after spring forward, and the hour after falling back
            (datetime(2026, 3, 8, 11, 0, tzinfo=timezone.utc), "2026-03-09T07:00:00+00:00"),
            (datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc), "2026-11-02T08:00:00+00:00"),
            # the last day of the year, into the next
            (datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc), "2027-01-01T08:00:00+00:00"),
        ):
            with self.subTest(when=when):
                self.assertEqual(gemini._pacific_midnight_by_rule(when).isoformat(), expected)


class PoolSettingsTest(unittest.TestCase):
    """Where the pools come from: config lists, then a preferred model, then defaults."""

    def setUp(self) -> None:
        self.addCleanup(setattr, gemini, "load_config", gemini.load_config)
        self.cfg: dict = {}
        gemini.load_config = lambda: type("C", (), {"data": {"gemini": self.cfg}})()

    def test_defaults_when_nothing_is_configured(self):
        s = gemini.settings()
        self.assertEqual(s["models"], list(gemini.DEFAULT_MODELS))
        self.assertEqual(s["search_models"], list(gemini.DEFAULT_SEARCH_MODELS))

    def test_a_single_model_in_config_goes_first_with_the_defaults_behind(self):
        self.cfg = {"model": "gemini-2.5-flash"}
        self.assertEqual(gemini.settings()["models"][:2], ["gemini-2.5-flash", "gemini-3.7-flash"])
        self.assertEqual(gemini.settings()["models"].count("gemini-2.5-flash"), 1)

    def test_config_lists_win_over_the_single_model(self):
        self.cfg = {"model": "gemini-2.5-flash", "models": ["a", "b"], "search_models": ["c"]}
        s = gemini.settings()
        self.assertEqual((s["models"], s["search_models"]), (["a", "b"], ["c"]))

    def test_a_comma_separated_string_is_accepted_too(self):
        self.cfg = {"models": " x , y,,x ", "search_models": "z"}
        s = gemini.settings()
        self.assertEqual((s["models"], s["search_models"]), (["x", "y"], ["z"]))

    def test_pacific_midnight(self):
        # PDT: 12:00 PDT on the 29th -> 00:00 PDT on the 30th = 07:00 UTC.
        self.assertEqual(gemini.pacific_midnight_after(datetime(2026, 8, 29, 19, 0, tzinfo=timezone.utc)),
                         datetime(2026, 8, 30, 7, 0, tzinfo=timezone.utc))
        # PST in January: 08:00 UTC is already the 30th in Los Angeles? No: 08:00 UTC = 00:00 PST.
        self.assertEqual(gemini.pacific_midnight_after(datetime(2026, 1, 30, 8, 0, tzinfo=timezone.utc)),
                         datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(gemini.pacific_midnight_after(datetime(2026, 1, 30, 7, 59, tzinfo=timezone.utc)),
                         datetime(2026, 1, 30, 8, 0, tzinfo=timezone.utc))

    def test_quota_kind(self):
        daily = {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                             {"retryDelay": "52s"}]}
        self.assertEqual(gemini.quota_kind(daily), "daily", "the named quota wins over a short retry delay")
        minute = {"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}
        self.assertEqual(gemini.quota_kind(minute), "minute")
        self.assertEqual(gemini.quota_kind({"message": "Please retry in 12s.", "details": []}), "minute")
        # Google states the same limit in prose, with spaces, and sometimes
        # sends no details at all.
        prose = {"message": "Quota exceeded for quota metric "
                            "'Generate Content API requests per minute'", "details": []}
        self.assertEqual(gemini.quota_kind(prose), "minute")
        helpful = {"message": "You exceeded your current quota. See "
                              "https://ai.google.dev/gemini-api/docs/daily-limits", "details": []}
        self.assertEqual(gemini.quota_kind(helpful), "minute",
                         "a word in a help link is not the quota that was hit")
        self.assertEqual(gemini.quota_kind({"message": "You exceeded your current quota", "details": []}),
                         "minute",
                         "an inconclusive 429 costs one retry this way, a day the other way")


class RemedyTest(unittest.TestCase):
    """The advice follows the fact.

    /doctor used to print "set GEMINI_API_KEY in .env" for every Gemini
    failure, including a working key whose daily grounding quota had run out.
    """

    R = gemini.Reason

    def remedy(self, *reasons, timeout_seconds=30):
        return gemini.remedy_for(list(reasons), timeout_seconds=timeout_seconds)

    def test_a_config_error_outranks_a_quota_that_fixes_itself(self):
        text = self.remedy(
            self.R("gemini-2.5-flash", "quota_daily", "daily quota",
                   "2026-08-30T01:00:00+00:00"),
            self.R("gemini-2.5-flash-lite", "unknown_model", "unknown to this key (404)"),
        )
        self.assertIn("does not know gemini-2.5-flash-lite", text)
        self.assertIn("config.toml", text)

    def test_a_daily_quota_says_when_it_returns_and_to_do_nothing(self):
        text = self.remedy(self.R("m", "quota_daily", "daily quota",
                                  "2026-08-30T01:00:00+00:00"))
        self.assertIn("nothing to do", text)
        self.assertIn("2026-08-30 01:00 UTC", text)

    def test_the_one_line_summary_keeps_the_time_a_quota_returns(self):
        # The 80-character truncation in /doctor used to cut exactly here.
        self.assertEqual(
            gemini.pool_summary([self.R("m", "quota_daily", "daily quota",
                                        "2026-08-30T01:00:00+00:00")]),
            "daily quota until 01:00 UTC")
        self.assertEqual(
            gemini.pool_summary([self.R("m", "timeout", "timed out")]),
            "no answer in time")

    def test_a_timeout_reports_the_limit_that_actually_applied(self):
        # The probe is capped at PROBE_TIMEOUT, so quoting the configured
        # timeout_seconds here would advise raising a setting that does not
        # bound what just happened.
        text = self.remedy(self.R("m", "timeout", "timed out"), timeout_seconds=45)
        self.assertIn("within 45s", text)
        self.assertNotIn("timeout_seconds (45s)", text)

    def test_only_a_key_problem_says_anything_about_the_key(self):
        self.assertIn("GEMINI_API_KEY", self.remedy(self.R("", "no_key", "not set")))
        self.assertIn("replace GEMINI_API_KEY", self.remedy(self.R("", "auth", "refused")))
        for kind in ("quota_daily", "quota_minute", "cooling", "unknown_model",
                     "overloaded", "timeout", "http_error", "no_answer"):
            with self.subTest(kind=kind):
                self.assertNotIn("GEMINI_API_KEY", self.remedy(self.R("m", kind, "x")))


class CheckReportTest(unittest.TestCase):
    """`check` answers per pool, so "plain works, search does not" can be said."""

    POOL = ["a-model", "b-model"]

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-check-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._real_repo = paths.REPO
        self.addCleanup(paths.configure, self._real_repo)
        paths.configure(self.tmp)
        for name in ("settings", "api_key", "call"):
            self.addCleanup(setattr, gemini, name, getattr(gemini, name))
        self.enabled = True
        gemini.settings = lambda: {
            "enabled": self.enabled, "models": list(self.POOL),
            "search_models": list(self.POOL), "timeout_seconds": 30,
            "cache_days": 30, "tasks": ["research"], "log": False,
        }
        gemini.api_key = lambda: "test-key"

    def report(self, plain=None, search=None) -> dict:
        """plain/search: None to answer, or the exception the pool raises."""
        def fake_call(prompt, *, search=False, **kw):
            failure = search_exc if search else plain_exc
            if failure:
                raise failure
            gemini.LAST_MODEL = "a-model"
            return "OK"

        plain_exc, search_exc = plain, search
        gemini.call = fake_call
        return gemini.check_report()

    @staticmethod
    def refusal(*reasons):
        text = "no model in the search pool could answer -- " + "; ".join(
            f"{r.model}: {r.detail}" for r in reasons)
        return gemini.GeminiUnavailable(text, reasons=tuple(reasons))

    def test_both_pools_answering_needs_no_remedy(self):
        report = self.report()
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["remedy"], "")
        self.assertTrue(report["pools"]["plain"]["ok"])
        self.assertTrue(report["pools"]["search"]["ok"])

    def test_one_pool_down_is_reported_without_condemning_the_other(self):
        report = self.report(search=self.refusal(
            gemini.Reason("a-model", "quota_daily", "daily quota", "2026-08-30T01:00:00+00:00"),
            gemini.Reason("b-model", "unknown_model", "unknown to this key (404)"),
        ))
        self.assertTrue(report["pools"]["plain"]["ok"], "the plain pool still answers")
        self.assertEqual(report["pools"]["search"]["ok"], False)
        self.assertEqual(report["pools"]["search"]["kinds"], ["quota_daily", "unknown_model"])
        self.assertEqual(report["pools"]["search"]["summary"], "a model this key does not know")
        self.assertEqual(report["pools"]["search"]["until"], "2026-08-30T01:00:00+00:00")
        self.assertIn("does not know b-model", report["remedy"])
        self.assertEqual(report["exit_code"], 3)

    def test_a_missing_key_stops_before_any_request(self):
        gemini.api_key = lambda: (_ for _ in ()).throw(
            gemini.GeminiUnavailable("GEMINI_API_KEY is not set -- put it in .env"))
        report = self.report()
        self.assertFalse(report["key"])
        self.assertEqual(report["pools"], {})
        self.assertIn("GEMINI_API_KEY", report["remedy"])

    def test_disabled_says_which_setting_turns_it_on(self):
        self.enabled = False
        report = self.report()
        self.assertEqual(report["pools"], {})
        self.assertIn("enabled = true", report["remedy"])

    def test_a_junk_cooldown_entry_does_not_break_the_report(self):
        # check_report reads the file to list what is sitting out, and used to
        # call .get() on whatever it found there.
        paths.GEMINI_COOLDOWNS.parent.mkdir(parents=True, exist_ok=True)
        paths.GEMINI_COOLDOWNS.write_text('{"a-model": "not a dict"}', encoding="utf-8")
        report = self.report()
        self.assertEqual(report["cooling"], [])
        self.assertEqual(report["exit_code"], 0)

    def test_a_probe_never_benches_a_model(self):
        seen = {}

        def fake_call(prompt, *, search=False, cool=True, **kw):
            seen[search] = cool
            gemini.LAST_MODEL = "a-model"
            return "OK"

        gemini.call = fake_call
        gemini.check_report()
        self.assertEqual(seen, {False: False, True: False})


if __name__ == "__main__":
    unittest.main()
