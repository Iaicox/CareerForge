"""Cross-document promises, checked instead of trusted.

Documentation drifts silently because nothing reads it. These are the places
where two files have to agree about the same fact, and where being out of date
sends a reader looking for something that is not there -- or, worse, leaves a
whole pipeline stage undiscovered.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMMANDS_DIR = REPO / ".claude" / "commands"


def command_names() -> list[str]:
    return sorted(p.stem for p in COMMANDS_DIR.glob("*.md"))


class SlashCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.commands = command_names()
        self.assertTrue(self.commands, f"no commands found under {COMMANDS_DIR}")

    def assertListsEveryCommand(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        missing = [
            name for name in self.commands
            # A boundary, so "/track" is not satisfied by "tools/tracker.py".
            if not re.search(rf"/{re.escape(name)}\b", text)
        ]
        self.assertEqual(
            missing, [],
            f"{path.relative_to(REPO)} does not mention: "
            + ", ".join(f"/{n}" for n in missing),
        )

    def test_the_architecture_sketch_lists_every_command(self):
        # It listed seven of eleven, and the four it omitted included /rank --
        # a whole stage of the pipeline the sketch described without it.
        self.assertListsEveryCommand(REPO / "docs" / "architecture.md")

    def test_claude_md_lists_every_command(self):
        # This is the table an agent reads to know what it can offer.
        self.assertListsEveryCommand(REPO / "CLAUDE.md")

    def test_the_manual_covers_every_command(self):
        self.assertListsEveryCommand(REPO / "docs" / "manual.md")


if __name__ == "__main__":
    unittest.main()
