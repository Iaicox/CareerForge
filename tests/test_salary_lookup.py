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
spec = importlib.util.spec_from_file_location("salary_lookup", REPO / "tools" / "salary_lookup.py")
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

    def test_the_example_config_carries_every_built_in_pattern(self):
        # config.example.toml is copied to config.toml by /setup, and a
        # configured list REPLACES the built-in one rather than extending it.
        # So anything missing from the example is silently lost the moment a
        # user has a config at all -- "group" and "holding" were.
        import tomllib

        with (REPO / "data" / "config" / "config.example.toml").open("rb") as fh:
            example = tomllib.load(fh)["salary"]
        self.assertEqual(
            set(salary.DEFAULT_LEGAL_FORMS) - set(example["strip_legal_forms"]),
            set(),
            "built-in legal forms missing from config.example.toml",
        )
        self.assertEqual(
            set(salary.DEFAULT_REGIONS) - set(example["strip_regions"]),
            set(),
            "built-in region words missing from config.example.toml",
        )
        self.assertEqual(
            set(example["strip_legal_forms"]) - set(salary.DEFAULT_LEGAL_FORMS),
            set(),
            "config.example.toml legal forms missing from the built-in list",
        )
        self.assertEqual(
            set(example["strip_regions"]) - set(salary.DEFAULT_REGIONS),
            set(),
            "config.example.toml region words missing from the built-in list",
        )

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


class FormattingTest(unittest.TestCase):
    """The table has to read correctly for an index and for absolute money."""

    EUR = {
        "index_label": "EUR gross/year",
        "index_baseline": 62166,
        "baseline_description": "62,166 = Lisbon senior median",
    }

    def test_difference_from_an_index_baseline(self):
        self.assertEqual(salary.fmt_difference(112.5, 100), "+12.5%")
        self.assertEqual(salary.fmt_difference(93, 100), "-7.0%")

    def test_difference_from_a_money_baseline_is_not_the_subtraction(self):
        # 105000 - 62166 = 42834, which is a sum of euros, not "+42834%".
        self.assertEqual(salary.fmt_difference(105000, 62166), "+68.9%")

    def test_no_baseline_means_no_comparison(self):
        for baseline in (0, None):
            with self.subTest(baseline=baseline):
                self.assertEqual(salary.fmt_difference(105000, baseline), "")

    def test_numbers_keep_only_the_precision_they_carry(self):
        self.assertEqual(salary.fmt_number(105000), "105,000")
        self.assertEqual(salary.fmt_number(112.5), "112.5")

    def test_label_casing_belongs_to_the_dataset(self):
        # .title() would turn the unit into "Eur".
        self.assertEqual(salary.fmt_label("senior_frontend_EUR"), "Senior frontend EUR")

    def test_columns_line_up_whatever_the_label_length(self):
        entry = {
            "company": "Acme",
            "categories": {
                "senior_software_engineer_frontend_eur_gross_annual": {
                    "count": 6, "index": 105000,
                },
                "junior": {"count": 12, "index": 40000},
            },
        }
        rendered = salary.format_entry(entry, self.EUR).splitlines()
        rows = [line for line in rendered if "105,000" in line or "40,000" in line]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), len(rows[1]), "columns do not line up")

    def test_the_footnote_only_appears_when_a_value_is_missing(self):
        present = {"company": "Acme", "categories": {"all": {"count": 5, "index": 70000}}}
        self.assertNotIn("N/A", salary.format_entry(present, self.EUR))

        missing = {"company": "Acme", "categories": {"all": {"count": 5, "index": None}}}
        self.assertIn("N/A", salary.format_entry(missing, self.EUR))

    def test_a_short_entry_inside_a_longer_query_still_scores(self):
        # The dataset holds "Novo"; the posting says "Novo Nordisk Pharma".
        # This is the branch that looks redundant next to the word-coverage
        # fallback below and is not: without it these score in the 40s and
        # fall behind worse matches.
        self.assertEqual(salary.match_score("Novo Nordisk Pharma", "Novo"), 75)
        self.assertEqual(salary.match_score("Ostergaard Cafe", "Cafe"), 75)

    def test_a_short_name_inside_a_longer_one_needs_a_shared_word(self):
        # "abcd" sits inside "Abcdefgh Systems" without being any part of it.
        # Both directions have to refuse it.
        self.assertEqual(salary.match_score("Abcd", "Abcdefgh Systems"), 0)
        self.assertEqual(salary.match_score("Abcdefgh Systems", "Abcd"), 0)

    def test_containment_scores_above_word_overlap(self):
        # A name the dataset spells out in full beats one that merely shares
        # words with it.
        self.assertEqual(salary.match_score("Vestas", "Vestas Wind Systems"), 83)
        self.assertEqual(salary.match_score("Novo Nordisk", "Nordisk Pharma"), 50)

    def test_a_count_of_zero_is_not_a_dash(self):
        entry = {"company": "Acme", "categories": {"all": {"count": 0, "index": 70000}}}
        row = [
            line for line in salary.format_entry(entry, self.EUR).splitlines()
            if "70,000" in line
        ][0]
        self.assertIn("0", row.split())


if __name__ == "__main__":
    unittest.main()
