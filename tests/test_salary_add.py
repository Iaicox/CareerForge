"""salary_lookup.py add: a figure found during research lands in the benchmark.

One record per company and city. A figure for another location is never this
location's benchmark; --unknown records that nothing was found, with the lead
in the note. No unit conversion, ever.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
spec = importlib.util.spec_from_file_location("salary_lookup", REPO / "tools" / "salary_lookup.py")
salary = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(salary)

FILE = {
    "metadata": {
        "source": "Personal research",
        "index_baseline": 62166,
        "index_label": "EUR gross/year",
        "baseline_description": "62,166 = Lisbon senior median",
    },
    "companies": [
        {"company": "Example Corp Lda", "city": "Lisboa",
         "categories": {"senior_frontend_eur_gross_annual": {"count": 4, "index": 72000}}},
        {"company": "Beta SA", "city": "Lisboa",
         "categories": {"senior_fullstack_eur_gross_annual": {"count": 1, "index": 60000}}},
    ],
}


class AddTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-salary-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, salary, "DATA_FILE", salary.DATA_FILE)
        salary.DATA_FILE = self.tmp / "salary_data.json"
        salary.DATA_FILE.write_text(json.dumps(FILE), encoding="utf-8")

    def data(self) -> dict:
        return json.loads(salary.DATA_FILE.read_text(encoding="utf-8"))

    def add(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = salary.main_add(list(argv))
        return code, out.getvalue(), err.getvalue()

    def lookup(self, *argv) -> tuple[str, int]:
        out = io.StringIO()
        real_argv = sys.argv
        sys.argv = ["salary_lookup.py", *argv]
        try:
            with contextlib.redirect_stdout(out):
                try:
                    salary.main()
                    code = 0
                except SystemExit as exc:
                    code = int(exc.code or 0)
        finally:
            sys.argv = real_argv
        return out.getvalue(), code

    # -- recording a figure -------------------------------------------------

    def test_a_figure_is_recorded_with_its_source_and_date(self):
        code, out, _ = self.add("--company", "Acme Lda", "--city", "Lisboa",
                                "--category", "senior_frontend_eur_gross_annual",
                                "--index", "65000", "--count", "2",
                                "--source", "https://glassdoor.example/acme", "--as-of", "2026-08")
        self.assertEqual(code, 0, out)
        self.assertIn("recorded Acme Lda (Lisboa)", out)
        rec = next(e for e in self.data()["companies"] if e["company"] == "Acme Lda")
        self.assertEqual(rec["categories"]["senior_frontend_eur_gross_annual"], {"count": 2, "index": 65000.0})
        self.assertEqual((rec["source"], rec["as_of"], rec["origin"]),
                         ("https://glassdoor.example/acme", "2026-08", "research"))
        self.assertEqual(self.data()["metadata"], FILE["metadata"], "metadata survives a write")

    def test_the_lookup_shows_the_provenance(self):
        self.add("--company", "Acme Lda", "--city", "Lisboa", "--category", "senior_frontend_eur_gross_annual",
                 "--index", "65000", "--source", "https://glassdoor.example/acme", "--as-of", "2026-08")
        text, _ = self.lookup("Acme")
        self.assertIn("Source: https://glassdoor.example/acme", text)
        self.assertIn("As of: 2026-08", text)
        self.assertIn("+4.6%", text)
        rows, _ = self.lookup("Acme", "--json")
        self.assertEqual(json.loads(rows)[0]["as_of"], "2026-08")

    def test_an_existing_figure_is_not_overwritten_without_force(self):
        code, _, err = self.add("--company", "Example Corp", "--city", "Lisboa",
                                "--category", "senior_frontend_eur_gross_annual", "--index", "80000")
        self.assertEqual(code, 1)
        self.assertIn("already has senior_frontend_eur_gross_annual = 72,000", err)
        self.assertIn("80,000", err)
        self.assertEqual(self.data()["companies"][0]["categories"]["senior_frontend_eur_gross_annual"]["index"], 72000)
        code, out, _ = self.add("--company", "Example Corp", "--city", "Lisboa",
                                "--category", "senior_frontend_eur_gross_annual", "--index", "80000", "--force")
        self.assertEqual(code, 0)
        self.assertIn("updated Example Corp Lda (Lisboa)", out)
        self.assertEqual(self.data()["companies"][0]["categories"]["senior_frontend_eur_gross_annual"]["index"], 80000)

    def test_a_category_the_file_has_never_seen_needs_new_category(self):
        code, _, err = self.add("--company", "Acme", "--city", "Austin",
                                "--category", "senior_frontend_usd_gross_annual", "--index", "170000")
        self.assertEqual(code, 1)
        self.assertIn("not in the file", err)
        self.assertIn("--new-category", err)
        code, _, _ = self.add("--company", "Acme", "--city", "Austin",
                              "--category", "senior_frontend_usd_gross_annual", "--index", "170000", "--new-category")
        self.assertEqual(code, 0)

    def test_records_are_one_company_in_one_city(self):
        self.add("--company", "Acme", "--city", "Austin", "--category", "senior_frontend_usd_gross_annual",
                 "--index", "170000", "--new-category")
        code, out, _ = self.add("--company", "Acme", "--city", "Lisboa", "--unknown",
                                "--note", "US only: $170k (levels.fyi, 2026-05)", "--as-of", "2026-08")
        self.assertEqual(code, 0, out)
        acme = [e for e in self.data()["companies"] if e["company"] == "Acme"]
        self.assertEqual({e["city"] for e in acme}, {"Austin", "Lisboa"})
        lisbon = next(e for e in acme if e["city"] == "Lisboa")
        self.assertEqual((lisbon["categories"], lisbon["note"], lisbon["origin"]),
                         ({}, "US only: $170k (levels.fyi, 2026-05)", "research"))

    def test_unknown_refuses_a_record_that_already_has_a_figure(self):
        code, _, err = self.add("--company", "Example Corp", "--city", "Lisboa", "--unknown", "--note", "x")
        self.assertEqual(code, 1)
        self.assertIn("already carries a figure", err)

    def test_unknown_and_a_figure_are_mutually_exclusive(self):
        code, _, err = self.add("--company", "Acme", "--city", "Lisboa", "--unknown",
                                "--category", "senior_frontend_eur_gross_annual", "--index", "1")
        self.assertEqual(code, 1)
        self.assertIn("--unknown", err)

    def test_a_figure_needs_a_category_and_an_index(self):
        code, _, err = self.add("--company", "Acme", "--city", "Lisboa", "--category", "senior_frontend_eur_gross_annual")
        self.assertEqual(code, 1)
        self.assertIn("--index", err)

    # -- the lookup side ----------------------------------------------------

    def test_an_unknown_record_reads_as_unknown_in_the_lookup(self):
        self.add("--company", "Acme", "--city", "Lisboa", "--unknown", "--note", "US only: $170k", "--as-of", "2026-08")
        text, code = self.lookup("Acme", "--city", "Lisboa")
        self.assertEqual(code, 0)
        self.assertIn("Note: US only: $170k", text)
        self.assertIn("No figure for this location", text)

    def test_another_unit_is_shown_without_a_baseline_comparison(self):
        self.add("--company", "Acme", "--city", "Austin", "--category", "senior_frontend_usd_gross_annual",
                 "--index", "170000", "--new-category")
        text, _ = self.lookup("Acme")
        self.assertIn("170,000", text)
        self.assertNotIn("%", text, "USD is not a percentage of a EUR median")
        text, _ = self.lookup("Example")
        self.assertIn("+15.8%", text, "the file's own unit still compares")
        self.assertEqual(self.data()["metadata"]["baseline_unit"], "eur_gross_annual",
                         "the first foreign category pins the file's unit down in writing")

    def test_the_baseline_unit_is_read_from_the_file(self):
        self.assertEqual(salary.baseline_unit(FILE), "eur_gross_annual")
        stated = dict(FILE, metadata=dict(FILE["metadata"], baseline_unit="eur_gross_annual"))
        self.assertEqual(salary.baseline_unit(stated), "eur_gross_annual")
        self.assertIsNone(salary.baseline_unit({"metadata": {}, "companies": []}))
        mixed = {"companies": [{"company": "A", "categories": {"x_eur": {}}}, {"company": "B", "categories": {"y_usd": {}}}]}
        self.assertIsNone(salary.baseline_unit(mixed))
        self.assertTrue(salary.compares_to_baseline("senior_frontend_eur_gross_annual", "eur_gross_annual"))
        self.assertFalse(salary.compares_to_baseline("senior_frontend_usd_gross_annual", "eur_gross_annual"))
        self.assertTrue(salary.compares_to_baseline("anything", None))


if __name__ == "__main__":
    unittest.main()
