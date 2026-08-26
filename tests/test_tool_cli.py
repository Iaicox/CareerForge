"""Every tool answers --help, because the manual promises that it does.

The tools are not uniform underneath: most use argparse and get --help for
free, four parse argv by hand, and one needs an optional dependency it
should not demand merely to describe itself. Nothing else checks that the
promise holds across all of them, and it had already stopped being true --
config_get read `--help` as a config key, pagecount read it as a filename,
and publish_manual ignored it and rewrote the published page instead.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"

# console.py is a helper module, not a command.
SCRIPTS = sorted(p for p in TOOLS.glob("*.py") if p.name != "console.py")


class HelpTest(unittest.TestCase):
    def test_the_tools_directory_was_found(self):
        self.assertTrue(SCRIPTS, f"no tools found under {TOOLS}")

    def test_every_tool_answers_help(self):
        for script in SCRIPTS:
            with self.subTest(tool=script.name):
                result = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=60,
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"{script.name} --help exited {result.returncode}: "
                    f"{result.stderr.strip()[:300]}",
                )
                self.assertTrue(
                    result.stdout.strip(),
                    f"{script.name} --help printed nothing to stdout",
                )


if __name__ == "__main__":
    unittest.main()
