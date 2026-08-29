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


class BulkInputTest(unittest.TestCase):
    """Bulk input must travel over stdin, not in the command line.

    A command line has a hard size limit -- about 32 KB on Windows -- and a
    single job posting can exceed it, so putting the payload in argv fails with
    WinError 206 on exactly the inputs the tool exists for.
    """

    def setUp(self) -> None:
        self.captured: dict = {}
        self._real_run = gemini.run_cli
        self._real_settings = gemini.settings
        self._real_binary = gemini.binary
        self._real_log = gemini.log_call

        class Result:
            returncode = 0
            stdout = json.dumps({"response": "ok"})
            stderr = ""

        def fake_run(cmd, **kwargs):
            self.captured["cmd"] = cmd
            self.captured["input"] = kwargs.get("payload")
            self.captured["cwd"] = kwargs.get("cwd")
            return Result()

        gemini.run_cli = fake_run
        gemini.binary = lambda: "gemini"
        gemini.log_call = lambda *a, **k: None
        gemini.settings = lambda: {
            "enabled": True, "model": "m", "timeout_seconds": 10,
            "cache_days": 30, "tasks": ["research"], "log": False,
        }

    def tearDown(self) -> None:
        gemini.run_cli = self._real_run
        gemini.settings = self._real_settings
        gemini.binary = self._real_binary
        gemini.log_call = self._real_log

    def test_payload_goes_to_stdin_and_not_argv(self):
        bulk = "x" * 200_000
        gemini.call("instruction", payload=bulk)
        self.assertEqual(self.captured["input"], bulk)
        self.assertNotIn(bulk, self.captured["cmd"])

    def test_the_instruction_still_travels_in_argv(self):
        gemini.call("instruction", payload="data")
        self.assertIn("instruction", self.captured["cmd"])

    def test_gemini_runs_in_an_empty_room_not_the_repo(self):
        # Gemini is itself an agent: run in the repo it discovers CLAUDE.md and
        # answers as the workspace assistant instead of doing the task. Seen
        # live: a digest request came back as "run /setup".
        gemini.call("instruction", payload="data")
        cwd = self.captured.get("cwd")
        self.assertIsNotNone(cwd)
        self.assertIn("gemini-cwd", str(cwd))

    def test_read_only_mode_is_always_requested(self):
        gemini.call("instruction")
        cmd = self.captured["cmd"]
        self.assertIn("--approval-mode", cmd)
        self.assertEqual(cmd[cmd.index("--approval-mode") + 1], "plan")
        # Without this the approval mode is silently downgraded in an untrusted
        # folder, which would hand it write tools.
        self.assertIn("--skip-trust", cmd)

    def test_a_command_line_length_failure_is_explained(self):
        def raise_206(cmd, **kwargs):
            exc = OSError("[WinError 206] The filename or extension is too long")
            exc.winerror = 206
            raise exc

        gemini.run_cli = raise_206
        with self.assertRaises(gemini.GeminiUnavailable) as ctx:
            gemini.call("instruction", payload="x")
        self.assertIn("stdin", str(ctx.exception))

    def test_a_timeout_kills_the_whole_process_tree(self):
        # The Gemini launcher starts a second node process that inherits the
        # stdout pipe. subprocess.run() kills only the child on a timeout and
        # then blocks reading the pipe until the grandchild exits by itself --
        # seen live as a 120-second limit that hung for eight minutes.
        import subprocess
        import time
        child = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); "
            "time.sleep(20)"
        )
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self._real_run([sys.executable, "-c", child], payload=None, timeout=1,
                           env=None, cwd=None)
        self.assertLess(time.monotonic() - start, 8, "the grandchild kept the pipe open")


if __name__ == "__main__":
    unittest.main()
