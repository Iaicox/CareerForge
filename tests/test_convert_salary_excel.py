"""Column detection for the salary Excel converter.

The converter has to tell a headcount column from a salary-index one using
nothing but the header text, in a spreadsheet written in whatever language the
union that published it uses. Getting that wrong does not fail loudly: it
produces a dataset that salary_lookup then renders as a benchmark table.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "convert_salary_excel", REPO / "tools" / "convert_salary_excel.py"
)
convert = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(convert)
sys.modules.setdefault("convert_salary_excel", convert)


class Cell:
    def __init__(self, value):
        self.value = value


class FakeSheet:
    """The slice of openpyxl's worksheet API that parse_sheet actually uses."""

    def __init__(self, rows, title="Sheet1"):
        self.title = title
        self._rows = rows

    def iter_rows(self, min_row=1, max_row=None, values_only=False):
        end = len(self._rows) if max_row is None else min(max_row, len(self._rows))
        for row in self._rows[min_row - 1:end]:
            yield tuple(row) if values_only else tuple(Cell(v) for v in row)

    def __getitem__(self, row_idx):
        return [Cell(v) for v in self._rows[row_idx - 1]]


class ColumnTypeTest(unittest.TestCase):
    def test_an_index_header_is_not_a_count(self):
        # Every one of these contains the letter "n", which used to be enough
        # to classify it as a count and leave the pairing below unreachable.
        for header in ("Index", "Indeks", "Median", "Gennemsnit", "Løn"):
            self.assertEqual(convert.detect_column_type(header), "index", header)

    def test_count_headers_are_counts(self):
        for header in ("Antal", "Count", "Employees", "Medarbejdere"):
            self.assertEqual(convert.detect_column_type(header), "count", header)

    def test_n_still_counts_as_a_whole_word(self):
        # Dropping "n" from the patterns entirely would lose a real header.
        self.assertEqual(convert.detect_column_type("N"), "count")
        self.assertEqual(convert.detect_column_type("Number of employees"), "count")

    def test_a_plain_category_header_is_neither(self):
        for header in ("Engineering", "Region", "Sales"):
            self.assertIsNone(convert.detect_column_type(header), header)

    def test_the_marker_word_decides_a_compound_header(self):
        self.assertEqual(convert.detect_column_type("Antal Engineering"), "count")
        self.assertEqual(convert.detect_column_type("Indeks Engineering"), "index")

    def test_a_marker_welded_into_one_word_still_decides(self):
        # Danish and German write these as one word, which is why the pattern
        # lists are Danish in the first place. Whole-word matching alone read
        # every one of them as neither, and an unclassified column is stored as
        # a salary index.
        self.assertEqual(convert.detect_column_type("Lønindeks"), "index")
        self.assertEqual(convert.detect_column_type("Gennemsnitsløn"), "index")
        self.assertEqual(convert.detect_column_type("Medarbejderantal"), "count")

    def test_the_head_of_a_compound_wins(self):
        # Scandinavian and German compounds are head-final: the last part says
        # what the column is, the earlier parts qualify it.
        self.assertEqual(convert.detect_column_type("Lønantal"), "count")
        self.assertEqual(convert.detect_column_type("Antalindeks"), "index")

    def test_a_one_letter_marker_is_still_whole_word_only(self):
        # "n" as a substring is what classified every column as a count.
        self.assertEqual(convert.detect_column_type("N"), "count")
        self.assertIsNone(convert.detect_column_type("Engineering"))


class CategoryNameTest(unittest.TestCase):
    def test_only_the_marker_word_is_removed(self):
        # Substring removal took the letters with it: "atal egieerig".
        self.assertEqual(
            convert.category_name("Antal Engineering", convert.COUNT_PATTERNS),
            "engineering",
        )

    def test_punctuation_becomes_an_underscore(self):
        self.assertEqual(
            convert.category_name("Indeks IT-Support", convert.INDEX_PATTERNS),
            "it_support",
        )

    def test_a_header_that_is_only_a_marker_leaves_nothing(self):
        # parse_sheet falls back to "category_N" on an empty name.
        self.assertEqual(convert.category_name("Antal", convert.COUNT_PATTERNS), "")


class PairingTest(unittest.TestCase):
    def test_a_count_index_pair_becomes_one_category(self):
        sheet = FakeSheet([
            ["Firma", "By", "Antal Engineering", "Indeks Engineering"],
            ["Acme", "Lisboa", 12, 104.5],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(len(entries), 1)
        self.assertEqual(
            entries[0]["categories"],
            {"engineering": {"count": 12, "index": 104.5}},
        )

    def test_the_pair_survives_in_either_order(self):
        sheet = FakeSheet([
            ["Firma", "Indeks Engineering", "Antal Engineering"],
            ["Acme", 104.5, 12],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(
            entries[0]["categories"],
            {"engineering": {"count": 12, "index": 104.5}},
        )

    def test_an_unpaired_column_stays_standalone(self):
        sheet = FakeSheet([
            ["Firma", "Indeks"],
            ["Acme", 104.5],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(entries[0]["categories"], {"indeks": {"index": 104.5}})

    def test_a_standalone_count_is_not_filed_as_a_salary(self):
        # salary_lookup reads "index" and renders it against the baseline, so a
        # headcount stored there is shown to the user as a salary figure.
        sheet = FakeSheet([
            ["Firma", "Medarbejderantal"],
            ["Acme", 240],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(
            entries[0]["categories"], {"medarbejderantal": {"count": 240}}
        )

    def test_a_column_nobody_could_classify_says_so(self):
        # It still lands in "index" -- there is nowhere else to put it -- but
        # not silently, which is how a headcount got rendered as a salary.
        sheet = FakeSheet([
            ["Firma", "Omsætning"],
            ["Acme", 12.0],
        ])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            entries = convert.parse_sheet(sheet)
        self.assertEqual(entries[0]["categories"], {"omsætning": {"index": 12.0}})
        self.assertIn("Omsætning", err.getvalue())


class LanguageCoverageTest(unittest.TestCase):
    """The header languages these surveys are actually published in.

    A survey read in Lisbon is written in Portuguese, and the column that says
    what a company pays is called "Salário" rather than "Løn". An unrecognised
    header is not an error -- the column is stored as an index, which is how a
    headcount becomes a benchmark.
    """

    def test_a_salary_column_is_an_index_in_every_language(self):
        for header in (
            "Salário", "Salario", "Mediana", "Média", "Promedio",
            "Remuneração", "Remuneración", "Índice", "Indice", "Vencimento",
            "Зарплата", "Оклад", "Медиана", "Индекс",
        ):
            self.assertEqual(convert.detect_column_type(header), "index", header)

    def test_a_headcount_column_is_a_count_in_every_language(self):
        for header in (
            "Quantidade", "Cantidad", "Empregados", "Empleados",
            "Trabalhadores", "Respondentes", "Headcount",
            "Количество", "Число", "Сотрудников",
        ):
            self.assertEqual(convert.detect_column_type(header), "count", header)

    def test_a_category_in_those_languages_is_still_neither(self):
        for header in ("Engineering", "Departamento", "Sector", "Отдел"):
            self.assertIsNone(convert.detect_column_type(header), header)

    def test_no_marker_of_one_kind_hides_inside_the_other(self):
        # detect_column_type falls back to substring matching for markers of
        # MIN_SUBSTRING_MARKER letters or more, and the marker ending last
        # wins. A count word living inside an index word (or the reverse) would
        # therefore decide columns by spelling accident. This guards the lists
        # against the next language somebody adds.
        for count in convert.COUNT_PATTERNS:
            for index in convert.INDEX_PATTERNS:
                if len(count) >= convert.MIN_SUBSTRING_MARKER:
                    self.assertNotIn(count, index, f"{count!r} hides inside {index!r}")
                if len(index) >= convert.MIN_SUBSTRING_MARKER:
                    self.assertNotIn(index, count, f"{index!r} hides inside {count!r}")

    def test_a_portuguese_sheet_parses_end_to_end(self):
        sheet = FakeSheet([
            ["Empresa", "Cidade", "Quantidade Engineering", "Salário Engineering"],
            ["Acme", "Lisboa", 12, 104.5],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(entries[0]["company"], "Acme")
        self.assertEqual(entries[0]["city"], "Lisboa")
        self.assertEqual(
            entries[0]["categories"], {"engineering": {"count": 12, "index": 104.5}}
        )

    def test_a_russian_sheet_parses_end_to_end(self):
        sheet = FakeSheet([
            ["Компания", "Город", "Количество", "Зарплата"],
            ["Акме", "Москва", 8, 97.0],
        ])
        entries = convert.parse_sheet(sheet)
        self.assertEqual(entries[0]["company"], "Акме")
        self.assertEqual(entries[0]["city"], "Москва")
        self.assertEqual(
            list(entries[0]["categories"].values()), [{"count": 8, "index": 97.0}]
        )


if __name__ == "__main__":
    unittest.main()
