"""doctor's Gemini check reports the tool's verdict, not its own reading of it.

The remedy line used to be a constant -- "set GEMINI_API_KEY in .env" -- printed
whatever had gone wrong, so a working key whose daily grounding quota had run
out was reported as a key to replace. The reason was recovered by matching
`NOT USABLE` in the console output and cut at 80 characters, which landed
exactly before the part saying when the quota came back.

Nothing covered this check before.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import doctor  # noqa: E402
import gemini  # noqa: E402
import paths  # noqa: E402


class CheckDotenvTest(unittest.TestCase):
    """.env is read before every check that needs a secret."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-dotenv-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._real_repo = paths.REPO
        self.addCleanup(paths.configure, self._real_repo)
        paths.configure(self.tmp)
        paths.ENV.parent.mkdir(parents=True, exist_ok=True)

    def test_a_byte_order_mark_does_not_become_part_of_a_key_name(self):
        paths.ENV.write_bytes(b"\xef\xbb\xbfGEMINI_API_KEY=abc\n")
        self.assertEqual(doctor.check_dotenv().detail, "keys: GEMINI_API_KEY")

    def test_an_unreadable_env_is_a_warning_not_the_end_of_the_report(self):
        paths.ENV.write_bytes(b"\xff\xfeG\x00=\x00x\x00")
        check = doctor.check_dotenv()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("unreadable", check.detail)
        self.assertIn("UTF-8", check.fix)


class CheckGeminiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(setattr, gemini, "settings", gemini.settings)
        self.enabled = True
        self.timeout = 30
        self.spawned: list = []
        gemini.settings = lambda: {
            "enabled": self.enabled, "models": ["m"], "search_models": ["m"],
            "timeout_seconds": self.timeout, "cache_days": 30, "tasks": [], "log": False,
        }

    def check(self, *, stdout: str = "", timeout: bool = False) -> doctor.Check:
        def fake_run(*args, **kwargs):
            self.spawned.append(kwargs.get("timeout"))
            if timeout:
                raise subprocess.TimeoutExpired(cmd="gemini.py", timeout=90, output=stdout)
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        with mock.patch.object(doctor.subprocess, "run", fake_run):
            return doctor.check_gemini()

    @staticmethod
    def verdict(**over) -> str:
        report = {
            "enabled": True,
            "key": True,
            "pools": {"plain": {"ok": True, "model": "a-model"},
                      "search": {"ok": True, "model": "a-model"}},
            "remedy": "",
            "exit_code": 0,
        }
        report.update(over)
        return json.dumps(report)

    def test_both_pools_answering_is_ok_and_names_them(self):
        check = self.check(stdout=self.verdict())
        self.assertEqual(check.status, doctor.OK)
        self.assertIn("plain ok via a-model", check.detail)
        self.assertIn("search ok via a-model", check.detail)

    def test_one_failing_pool_warns_without_condemning_the_other(self):
        check = self.check(stdout=self.verdict(
            pools={"plain": {"ok": True, "model": "a-model"},
                   "search": {"ok": False, "summary": "daily quota until 01:00 UTC",
                              "kinds": ["quota_daily"]}},
            remedy="nothing to do: the quota resets at 2026-08-30 01:00 UTC",
            exit_code=3,
        ))
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("plain ok via a-model", check.detail)
        self.assertIn("daily quota until 01:00 UTC", check.detail)
        self.assertEqual(check.fix, "nothing to do: the quota resets at 2026-08-30 01:00 UTC")
        self.assertNotIn("GEMINI_API_KEY", check.fix, "the key is not what is wrong here")

    def test_the_reason_is_not_truncated_into_uselessness(self):
        # The old [:80] cut this exactly at "daily quota (retry".
        long = ("no model in the search pool could answer -- gemini-2.5-flash: daily quota "
                "(retryDelay), back at 2026-08-30 01:00 UTC; gemini-2.5-flash-lite: unknown "
                "to this key (404)")
        check = self.check(stdout=self.verdict(
            pools={"search": {"ok": False, "summary": "a model this key does not know",
                              "reason": long}},
            remedy="this key does not know gemini-2.5-flash-lite -- remove it",
            exit_code=3,
        ))
        self.assertIn("a model this key does not know", check.detail)
        self.assertIn("gemini-2.5-flash-lite", check.fix)

    def test_a_missing_key_forwards_the_remedy_that_does_name_the_key(self):
        check = self.check(stdout=self.verdict(
            key=False, pools={},
            remedy="GEMINI_API_KEY is not set -- put it in .env",
        ))
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("GEMINI_API_KEY", check.fix)

    def test_a_killed_probe_blames_the_timeout_and_not_the_key(self):
        check = self.check(timeout=True)
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("did not finish", check.detail)
        self.assertIn("timeout_seconds", check.fix)
        self.assertNotIn("GEMINI_API_KEY", check.fix)

    def test_a_killed_probe_still_uses_what_it_managed_to_print(self):
        check = self.check(timeout=True, stdout=self.verdict(
            pools={"plain": {"ok": False, "summary": "no answer in time"}},
            remedy="no answer within timeout_seconds (30s) -- raise it",
            exit_code=4,
        ))
        self.assertIn("no answer in time", check.detail)
        self.assertIn("raise it", check.fix)

    def test_unreadable_output_falls_back_to_running_it_by_hand(self):
        check = self.check(stdout="this is not json")
        self.assertEqual(check.status, doctor.WARN)
        self.assertEqual(check.fix, "python tools/gemini.py check")

    def test_the_probe_budget_covers_both_pools(self):
        # It was `timeout + 30` for a check that probes two pools, so doctor
        # timed out setups that worked.
        self.check(stdout=self.verdict())
        self.assertEqual(self.spawned, [2 * 30 + 30])

    def test_a_large_working_timeout_does_not_become_the_health_check_s(self):
        # The half the first version of this test could not see: with
        # timeout_seconds below PROBE_TIMEOUT the capped and uncapped formulas
        # agree, so removing the cap changed nothing and every test stayed
        # green. A research call may take ten minutes; /doctor may not.
        self.timeout = 600
        self.check(stdout=self.verdict())
        self.assertEqual(self.spawned, [2 * gemini.PROBE_TIMEOUT + 30])

    def test_a_killed_probe_quotes_the_time_it_was_actually_given(self):
        # Also untested: reverting either call site that feeds remedy_for the
        # applied limit left the whole suite green.
        self.timeout = 600
        check = self.check(timeout=True)
        budget = 2 * gemini.PROBE_TIMEOUT + 30
        self.assertIn(f"{budget}s", check.detail)
        self.assertIn(f"within {budget}s", check.fix)
        self.assertNotIn("600", check.fix, "not the configured timeout, which did not apply")

    def test_settings_that_will_not_read_are_not_reported_as_disabled(self):
        # "disabled (optional)" is green, and /setup reads it as the toolchain
        # being fine. A config that cannot be parsed, or a tool that will not
        # import, is not the same as switched off.
        gemini.settings = lambda: (_ for _ in ()).throw(ValueError("bad TOML"))
        check = self.check()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("cannot read the [gemini] settings", check.detail)
        self.assertIn("config.toml", check.fix)

    def test_disabled_is_ok_and_spawns_nothing(self):
        self.enabled = False
        calls = []
        with mock.patch.object(doctor.subprocess, "run",
                               lambda *a, **k: calls.append(a) or SimpleNamespace()):
            check = doctor.check_gemini()
        self.assertEqual(check.status, doctor.OK)
        self.assertEqual(calls, [], "a disabled integration is not probed")


if __name__ == "__main__":
    unittest.main()
