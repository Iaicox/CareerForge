"""Company-name normalisation for the salary benchmark lookup.

The tool matches a company name from a job posting against a user-supplied
dataset, so normalisation has to survive legal forms, diacritics and
punctuation in any Latin-script market.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("salary_lookup", REPO / "salary_lookup.py")
salary = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(salary)
sys.modules.setdefault("salary_lookup", salary)


class NormalisationTest(unittest.TestCase):
    def assertNormalises(self, raw: str, expected: str) -> None:
        self.assertEqual(salary.normalize(raw), expected, f"input: {raw!r}")

    def test_strips_legal_forms(self):
        self.assertNormalises("Acme A/S", "acme")
        self.assertNormalises("Sixt GmbH", "sixt")
        self.assertNormalises("Acme Lda", "acme")
        self.assertNormalises("Acme Pte", "acme")

    def test_sees_through_periods_in_legal_forms(self):
        # "S.A." has to reduce to "sa" before the word-boundary patterns run.
        self.assertNormalises("Nestle S.A.", "nestle")

    def test_strips_region_noise(self):
        self.assertNormalises("Acme Nordic", "acme")
        self.assertNormalises("Acme EMEA", "acme")

    def test_drops_everything_after_a_comma(self):
        self.assertNormalises("Sonae, SGPS", "sonae")

    def test_does_not_cut_a_legal_form_out_of_a_word(self):
        # "co" is a legal form; "Cocacola" must survive it intact.
        self.assertNormalises("Cocacola", "cocacola")
        self.assertNormalises("Incorp", "incorp")

    def test_never_normalises_to_an_empty_key(self):
        # An empty key would match every entry in the dataset.
        for name in ("Company Ltd", "Group Holding", "Global"):
            with self.subTest(name=name):
                self.assertTrue(salary.normalize(name), f"{name!r} normalised to nothing")

    def test_folds_diacritics(self):
        self.assertNormalises("Nestl" + chr(233), "nestle")          # Nestlé
        self.assertNormalises("M" + chr(248) + "ller", "moller")     # Møller
        self.assertNormalises("Bl" + chr(228) + "ck", "black")       # Bläck
        self.assertNormalises("Stra" + chr(223) + "e", "strasse")    # Straße

    def test_core_words_ignore_noise(self):
        self.assertEqual(salary.extract_core_words("Acme Nordic A/S"), ["acme"])


if __name__ == "__main__":
    unittest.main()
