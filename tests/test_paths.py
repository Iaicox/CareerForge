"""tools/paths.py is the one place that knows where the user's data lives.

Before it existed, ten modules each spelled out REPO / "profile" and friends,
config.toml was resolved in three separate places and the database path was
duplicated in doctor.py. Moving a directory meant finding every copy.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))

import paths  # noqa: E402

# Constants that are deliberately not under data/: the repo root itself, the
# per-machine secrets file, and the published manual (a build product of
# framework docs, not user data).
OUTSIDE_DATA = {"REPO", "ENV", "MANUAL_ARTIFACT"}


def path_constants() -> dict[str, Path]:
    return {
        name: value
        for name, value in vars(paths).items()
        if name.isupper() and isinstance(value, Path)
    }


class LayoutTest(unittest.TestCase):
    def test_every_data_path_sits_under_data(self):
        consts = path_constants()
        self.assertGreater(len(consts), 10)
        for name, value in consts.items():
            if name in OUTSIDE_DATA:
                continue
            with self.subTest(name=name):
                self.assertTrue(
                    value == paths.DATA or paths.DATA in value.parents,
                    f"{name} = {value} is not under {paths.DATA}",
                )

    def test_the_named_places(self):
        self.assertEqual(paths.DATA, paths.REPO / "data")
        self.assertEqual(paths.CONFIG, paths.DATA / "config" / "config.toml")
        self.assertEqual(paths.CONFIG_EXAMPLE, paths.DATA / "config" / "config.example.toml")
        self.assertEqual(paths.PROFILE_EXAMPLE, paths.DATA / "profile.example")
        self.assertEqual(paths.DB, paths.DATA / "state" / "careerforge.db")
        self.assertEqual(paths.NOTION_IDS, paths.DATA / "state" / "notion.json")
        self.assertEqual(paths.MANUAL_ARTIFACT, paths.REPO / "docs" / "manual-artifact.html")
        self.assertEqual(paths.ENV, paths.REPO / ".env")

    def test_stage_dirs_live_under_pipeline(self):
        self.assertEqual(paths.STAGES, ("applications", "processing", "rejected"))
        for stage in paths.STAGES:
            self.assertEqual(paths.stage_dir(stage), paths.DATA / "pipeline" / stage)

    def test_an_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            paths.stage_dir("archive")


class ConfigureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.real_repo = paths.REPO
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-paths-"))
        self.addCleanup(paths.configure, self.real_repo)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_configure_moves_everything_at_once(self):
        # Tests point the tools at a throwaway repo; every constant has to
        # follow, or a test writes into the real profile by mistake.
        paths.configure(self.tmp)
        root = self.tmp.resolve()
        for name, value in path_constants().items():
            with self.subTest(name=name):
                self.assertTrue(
                    value == root or root in value.parents,
                    f"{name} = {value} still points outside {root}",
                )
        self.assertEqual(paths.stage_dir("rejected"), root / "data" / "pipeline" / "rejected")

    def test_configure_back_restores_the_real_layout(self):
        paths.configure(self.tmp)
        paths.configure(self.real_repo)
        self.assertEqual(paths.DB, self.real_repo / "data" / "state" / "careerforge.db")


class NoOtherToolKnowsTheLayoutTest(unittest.TestCase):
    # The drift this module exists to stop: a tool re-deriving a data path
    # from the repo root on its own, which paths.configure() cannot follow.
    PATTERN = re.compile(
        r'(?:REPO|repo)\s*/\s*"(?:data|profile|config|tracker|state|job_scraper|'
        r'documents|applications|processing|rejected|pipeline)"'
    )

    def test_only_paths_py_builds_a_data_path_from_the_repo_root(self):
        offenders = []
        for script in sorted(TOOLS.glob("*.py")):
            if script.name == "paths.py":
                continue
            for lineno, line in enumerate(
                script.read_text(encoding="utf-8").splitlines(), 1
            ):
                if self.PATTERN.search(line):
                    offenders.append(f"{script.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_tool_imports_a_path_out_of_the_module(self):
        # paths.py's own docstring: "Tools read paths.X at call time -- never
        # `from paths import X`". A name bound that way is a copy taken at
        # import, so configure() cannot reach it -- and neither the pattern
        # above nor the one below would see it, because the text says
        # `STATE / "x"` rather than `paths.STATE` or `REPO / "state"`.
        offenders = []
        for script in sorted(TOOLS.glob("*.py")):
            if script.name == "paths.py":
                continue
            for lineno, line in enumerate(
                script.read_text(encoding="utf-8").splitlines(), 1
            ):
                if re.match(r"^\s*from paths import\b", line):
                    offenders.append(f"{script.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_tool_binds_a_root_dependent_path_at_import_time(self):
        # The other half of the same drift: a module-level constant is
        # computed once, at import, and outlives paths.configure(). The tools
        # that did this could not be redirected at all, so their tests
        # monkeypatched the constant and never confirmed where the file lands.
        # Names that do not depend on the root -- STAGES -- are fine to bind.
        rooted = "|".join(sorted(paths._layout(REPO)))
        pattern = re.compile(r"^[A-Z_][A-Z0-9_]*\s*=\s*paths\.(?:%s)\b" % rooted)
        offenders = []
        for script in sorted(TOOLS.glob("*.py")):
            if script.name == "paths.py":
                continue
            for lineno, line in enumerate(
                script.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.match(line):
                    offenders.append(f"{script.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
