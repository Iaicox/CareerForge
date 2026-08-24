"""The ATS text-layer checks.

Exercised against extracted text directly, so the suite needs no PDF, no
LibreOffice and no Word -- and so the failure cases can be constructed exactly
rather than hoped for.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import atscheck  # noqa: E402

GOOD = """Jane Doe
Lisbon, Portugal | jane.doe@example.com | +351 900 000 000

Profile
Senior Frontend Developer with eight years building design systems.

Experience
Senior Frontend Developer, Acme
Built a component library used across four products.

Education
BSc Computer Science

Skills
Vue 3, TypeScript, design systems
"""


def checks(text: str, source: Path | None = None, keywords=()) -> dict[str, list]:
    found = atscheck.run_checks(text, source, list(keywords))
    out: dict[str, list] = {}
    for f in found:
        out.setdefault(f.check, []).append(f)
    return out


class TextLayerTest(unittest.TestCase):
    def test_a_clean_document_reports_nothing(self):
        self.assertEqual(atscheck.run_checks(GOOD, None, []), [])

    def test_an_empty_text_layer_is_a_failure(self):
        found = atscheck.run_checks("   \n  ", None, [])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].check, "text layer")
        self.assertEqual(found[0].severity, "fail")

    def test_replacement_characters_are_a_failure(self):
        found = checks(GOOD.replace("design", "de" + chr(0xFFFD) + "ign"))
        self.assertIn("encoding", found)
        self.assertEqual(found["encoding"][0].severity, "fail")

    def test_ligatures_are_flagged(self):
        # U+FB01 renders as "fi" and extracts as one glyph most parsers drop.
        found = checks(GOOD.replace("Profile", "Pro" + chr(0xFB01) + "le"))
        self.assertIn("ligatures", found)

    def test_private_use_glyphs_are_flagged(self):
        found = checks(GOOD + chr(0xE001))
        self.assertIn("glyphs", found)

    def test_missing_email_is_a_failure(self):
        found = checks(GOOD.replace("jane.doe@example.com", "contact me"))
        self.assertIn("contact", found)
        self.assertTrue(any(f.severity == "fail" for f in found["contact"]))

    def test_missing_phone_is_only_a_warning(self):
        found = checks(GOOD.replace("+351 900 000 000", ""))
        self.assertIn("contact", found)
        self.assertTrue(all(f.severity == "warn" for f in found["contact"]))


class SourceComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-ats-"))
        self.source = self.tmp / "cv.md"
        self.source.write_text(
            "# Jane Doe\n\n## Profile\n\n## Experience\n\n## Education\n\n## Skills\n",
            encoding="utf-8",
        )

    def test_all_headings_present_passes(self):
        self.assertEqual(checks(GOOD, self.source), {})

    def test_a_dropped_section_is_reported(self):
        found = checks(GOOD.replace("Education", ""), self.source)
        self.assertIn("structure", found)
        self.assertIn("Education", found["structure"][0].detail)

    def test_heading_match_survives_layout_and_case(self):
        # pdftotext -layout inserts runs of spaces; that must not read as a
        # missing section.
        spaced = GOOD.replace("Experience", "EXPERIENCE   ")
        self.assertNotIn("structure", checks(spaced, self.source))

    def test_pandoc_attributes_are_stripped_from_headings(self):
        self.source.write_text(
            '# Jane Doe\n\n## Profile {custom-style="SectionHead"}\n',
            encoding="utf-8",
        )
        self.assertNotIn("structure", checks(GOOD, self.source))


class KeywordTest(unittest.TestCase):
    def test_present_keywords_pass(self):
        self.assertEqual(checks(GOOD, None, ["Vue 3", "TypeScript"]), {})

    def test_absent_keywords_are_reported(self):
        found = checks(GOOD, None, ["Kubernetes"])
        self.assertIn("keywords", found)
        self.assertIn("Kubernetes", found["keywords"][0].detail)

    def test_keyword_matching_ignores_case_and_spacing(self):
        self.assertEqual(checks(GOOD, None, ["DESIGN   SYSTEMS"]), {})


if __name__ == "__main__":
    unittest.main()
