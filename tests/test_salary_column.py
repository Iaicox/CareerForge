"""The salary a posting states, and the benchmark shown when it states none.

Nothing stored a posting's own pay before: gemini.extract_posting() had
returned it verbatim since it was written and every caller dropped it, so the
board could show a market benchmark for the company and never the figure the
posting itself printed. These pin both halves, and the seam between them --
which of the two a row is showing has to be answerable from the payload.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import paths  # noqa: E402
import salary_lookup  # noqa: E402
import tracker  # noqa: E402


DATASET = {
    "metadata": {
        "source": "Test data",
        "index_baseline": 60000,
        "index_label": "EUR gross/year",
        "baseline_unit": "eur_gross_annual",
    },
    "companies": [
        {
            "company": "Cloudflare Portugal",
            "city": "Lisboa",
            "categories": {
                "senior_frontend_eur_gross_annual": {"count": 8, "index": 88000},
                "senior_fullstack_eur_gross_annual": {"count": 3, "index": 94000},
            },
        },
        {
            "company": "Megaport",
            "city": "Remote",
            # The `--unknown` shape: the dataset says it looked and found nothing.
            "categories": {},
            "note": "Lead only: USD figures for another market",
        },
    ],
}


class SalaryFixture(unittest.TestCase):
    """A throwaway repo. `salary_data.json` is written only where a test wants it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-salary-")).resolve()
        self._real_repo = paths.REPO
        example = paths.CONFIG_EXAMPLE
        paths.configure(self.tmp)
        for stage in paths.STAGES:
            paths.stage_dir(stage).mkdir(parents=True)
        paths.CONFIG_DIR.mkdir(parents=True)
        paths.PROFILE.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, paths.CONFIG)
        tracker.load_config(force=True)
        tracker.init_db()
        self.cfg = tracker.load_config()
        self.conn = tracker.connect()
        salary_lookup._CACHE = None
        self.addCleanup(setattr, salary_lookup, "_CACHE", None)

    def tearDown(self) -> None:
        self.conn.close()
        paths.configure(self._real_repo)
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_dataset(self, data=None) -> Path:
        path = paths.PROFILE / "salary_data.json"
        path.write_text(json.dumps(data or DATASET, ensure_ascii=False), encoding="utf-8")
        salary_lookup._CACHE = None
        return path

    def posting(self, **kw):
        row = {"url": kw.pop("url", "https://jobs.example/1"), "title": "Frontend Engineer",
               "company": "Cloudflare Portugal", "status": "new"}
        row.update(kw)
        tracker.upsert_postings(self.conn, self.cfg, [row])
        self.conn.commit()


class StoredFigureTest(SalaryFixture):
    def test_a_posting_keeps_the_salary_it_states(self):
        self.posting(salary="45.000-60.000 EUR/year, 14 payments")
        row = self.conn.execute("SELECT salary_text FROM postings").fetchone()
        self.assertEqual(row["salary_text"], "45.000-60.000 EUR/year, 14 payments")

    def test_an_empty_figure_is_not_a_stated_salary(self):
        self.posting(salary="   ")
        self.assertIsNone(self.conn.execute("SELECT salary_text FROM postings").fetchone()[0])

    def test_an_application_carries_its_salary_to_its_posting(self):
        tracker.add_application(
            self.conn, self.cfg, company="Acme", role="Frontend Engineer",
            url="https://jobs.example/2", salary_text="60k EUR/year",
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/2",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "60k EUR/year")

    def test_a_scraped_posting_gains_the_figure_when_it_is_applied_to(self):
        # The scrape saw it first and recorded no pay; the application knows it.
        self.posting(url="https://jobs.example/3")
        tracker.add_application(
            self.conn, self.cfg, company="Cloudflare Portugal", role="Frontend Engineer",
            url="https://jobs.example/3", salary_text="70k EUR/year",
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/3",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "70k EUR/year")

    def test_the_scrape_figure_is_not_overwritten_by_the_application(self):
        # Fill in, never clobber: the scrape recorded what that page said.
        self.posting(url="https://jobs.example/4", salary="from the posting")
        tracker.add_application(
            self.conn, self.cfg, company="Cloudflare Portugal", role="Frontend Engineer",
            url="https://jobs.example/4", salary_text="typed by hand",
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/4",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "from the posting")


class FigureShapeTest(SalaryFixture):
    """`shortlist.py add --file` takes JSON an agent wrote from a board.

    A board states pay as a number or a pair of them as readily as a sentence,
    and a batch of forty postings must not be lost to the shape of one field.
    """

    def test_a_figure_given_as_a_number_is_recorded_rather_than_refused(self):
        self.posting(url="https://jobs.example/n1", salary=60000)
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/n1",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "60000")

    def test_a_figure_given_as_a_pair_is_joined(self):
        self.posting(url="https://jobs.example/n2", salary=["45000", "60000 EUR"])
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/n2",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "45000, 60000 EUR")

    def test_a_shape_with_no_obvious_wording_is_left_empty(self):
        # Empty beats plausible: the row falls back to the benchmark, which
        # says where its number came from. Inventing a currency here would not.
        self.posting(url="https://jobs.example/n3", salary={"min": 45000, "max": 60000})
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/n3",)
        ).fetchone()
        self.assertIsNone(row["salary_text"])

    def test_a_large_figure_keeps_its_digits(self):
        # %g goes scientific past six significant digits, and an annual gross
        # in HUF or KRW is seven or eight. "1.44e+07" is not verbatim.
        self.posting(url="https://jobs.example/n4", salary=14400000)
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/n4",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "14400000")

    def test_zero_is_not_a_stated_salary(self):
        # Boards write 0 for "not disclosed". Showing "0" as what an employer
        # offered is worse than showing the market benchmark.
        self.posting(url="https://jobs.example/n5", salary=0)
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/n5",)
        ).fetchone()
        self.assertIsNone(row["salary_text"])

    def test_a_batch_survives_one_posting_with_an_odd_figure(self):
        tracker.upsert_postings(self.conn, self.cfg, [
            {"url": "https://jobs.example/b1", "title": "A", "company": "Acme",
             "salary": {"nope": 1}},
            {"url": "https://jobs.example/b2", "title": "B", "company": "Acme",
             "salary": "70k EUR/year"},
        ])
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0], 2)


class RescrapeTest(SalaryFixture):
    def test_a_second_scrape_fills_in_a_figure_the_first_one_missed(self):
        # Rows scraped before the column existed carry nothing, and a search
        # result often hides pay the posting page states.
        self.posting(url="https://jobs.example/r1")
        self.posting(url="https://jobs.example/r1", salary="70k EUR/year")
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/r1",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "70k EUR/year")

    def test_a_second_scrape_does_not_overwrite_the_figure_already_there(self):
        self.posting(url="https://jobs.example/r2", salary="from the first read")
        self.posting(url="https://jobs.example/r2", salary="from the second")
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/r2",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "from the first read")

    def test_a_known_posting_is_still_counted_as_known(self):
        self.posting(url="https://jobs.example/r3")
        with self.conn:
            added, known = tracker.upsert_postings(self.conn, self.cfg, [
                {"url": "https://jobs.example/r3", "title": "Frontend Engineer",
                 "company": "Cloudflare Portugal", "salary": "70k"},
            ])
        self.assertEqual((added, known), (0, 1))


class SetSalaryTest(SalaryFixture):
    """`add --salary` is where a figure normally arrives; it is not the only way.

    A posting read again later often names pay the first pass missed, and the
    alternative to a command is hand-editing a database this workspace forbids
    touching.
    """

    def application(self, **kw):
        row = tracker.add_application(
            self.conn, self.cfg, company="Cloudflare Portugal",
            role="Frontend Engineer", url="https://jobs.example/s1", **kw)
        self.conn.commit()
        return row

    def test_a_figure_missed_at_add_time_can_still_be_recorded(self):
        app = self.application()
        with self.conn:
            tracker.set_salary(self.conn, str(app["id"]), "70k EUR/year")
        row = self.conn.execute(
            "SELECT salary_text FROM applications WHERE id = ?", (app["id"],)).fetchone()
        self.assertEqual(row["salary_text"], "70k EUR/year")

    def test_the_linked_posting_is_filled_in_too(self):
        app = self.application()
        with self.conn:
            tracker.set_salary(self.conn, str(app["id"]), "70k EUR/year")
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE application_id = ?", (app["id"],)
        ).fetchone()
        self.assertEqual(row["salary_text"], "70k EUR/year")

    def test_a_posting_that_already_states_one_keeps_its_own(self):
        self.posting(url="https://jobs.example/s1", salary="from the posting")
        app = self.application()
        with self.conn:
            tracker.set_salary(self.conn, str(app["id"]), "typed by hand")
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE url = ?", ("https://jobs.example/s1",)
        ).fetchone()
        self.assertEqual(row["salary_text"], "from the posting")

    def test_an_empty_string_takes_back_a_figure_recorded_in_error(self):
        app = self.application(salary_text="90k, misread")
        with self.conn:
            tracker.set_salary(self.conn, str(app["id"]), "")
        row = self.conn.execute(
            "SELECT salary_text FROM applications WHERE id = ?", (app["id"],)).fetchone()
        self.assertIsNone(row["salary_text"])

    def test_clearing_reaches_the_posting_as_well(self):
        # A figure withdrawn as wrong that survives on the postings table is
        # the same wrong number, still labelled as what the employer stated,
        # on the page the kanban no longer shows it on.
        app = self.application(salary_text="90k, misread")
        with self.conn:
            tracker.set_salary(self.conn, str(app["id"]), "")
        row = self.conn.execute(
            "SELECT salary_text FROM postings WHERE application_id = ?", (app["id"],)
        ).fetchone()
        self.assertIsNone(row["salary_text"])

    def test_the_command_line_reaches_it(self):
        # The dispatch is the half a unit test on set_salary does not cover,
        # and it is the half a person actually types.
        import contextlib
        import io
        app = self.application()
        self.conn.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tracker.main(["set-salary", app["slug"], "--salary", "70k EUR/year"])
        self.conn = tracker.connect()
        row = self.conn.execute(
            "SELECT salary_text FROM applications WHERE id = ?", (app["id"],)).fetchone()
        self.assertEqual(row["salary_text"], "70k EUR/year")
        self.assertIn("70k EUR/year", out.getvalue())


class BenchmarkTest(SalaryFixture):
    def test_a_missing_dataset_is_not_an_error(self):
        # salary_data.json is gitignored user data. Most workspaces, and CI,
        # have none: the column stays empty and nothing raises.
        self.posting()
        data = tracker.postings_table_data(self.conn, self.cfg)
        self.assertIsNone(data["salary_meta"])
        self.assertTrue(all(r["salary"] is None for r in data["rows"]))

    def test_a_dataset_with_no_metadata_still_gets_a_legend(self):
        # A hand-assembled file need not carry a metadata block, and cell()
        # does not need one. Without a legend the "≈" on those figures is a
        # symbol nobody was told the meaning of.
        self.write_dataset({"companies": [
            {"company": "Cloudflare Portugal", "city": "Lisboa",
             "categories": {"senior_frontend_eur_gross_annual": {"count": 4, "index": 62000}}},
        ]})
        self.posting(url="https://jobs.example/m1")
        data = tracker.postings_table_data(self.conn, self.cfg)
        self.assertEqual(data["rows"][0]["salary"]["kind"], "benchmark")
        self.assertIsNotNone(data["salary_meta"])
        self.assertTrue(data["salary_meta"]["unit"])

    def test_a_posting_without_a_figure_falls_back_to_the_benchmark(self):
        self.write_dataset()
        self.posting()
        cell = tracker.postings_table_data(self.conn, self.cfg)["rows"][0]["salary"]
        self.assertEqual(cell["kind"], "benchmark")
        self.assertIn("88,000", cell["text"])
        self.assertIn("EUR gross/year", cell["text"])
        self.assertEqual(cell["sort"], 88000.0)

    def test_the_salary_a_posting_states_wins_over_the_benchmark(self):
        self.write_dataset()
        self.posting(salary="45.000-60.000 EUR/year")
        cell = tracker.postings_table_data(self.conn, self.cfg)["rows"][0]["salary"]
        self.assertEqual(cell["kind"], "posting")
        self.assertEqual(cell["text"], "45.000-60.000 EUR/year")
        # The benchmark still orders the row, and says so in the tooltip.
        self.assertEqual(cell["sort"], 88000.0)
        self.assertIn("Market:", cell["detail"])

    def test_a_company_the_dataset_does_not_know_has_no_salary(self):
        self.write_dataset()
        self.posting(url="https://jobs.example/9", company="Nonesuch Ltd")
        rows = {r["company"]: r["salary"] for r in
                tracker.postings_table_data(self.conn, self.cfg)["rows"]}
        self.assertIsNone(rows["Nonesuch Ltd"])

    def test_a_record_that_found_nothing_yields_no_cell(self):
        # Empty `categories` is the dataset saying it looked. A note is a lead,
        # not a figure, and must not reach the column as one.
        self.write_dataset()
        bench = salary_lookup.load_benchmarks()
        self.assertIsNone(bench.cell("Megaport"))

    def test_a_location_the_dataset_spells_differently_still_matches(self):
        # search_company's own city filter is a one-directional substring, so
        # "Lisbon, Portugal" against a dataset that says "Lisboa" would drop
        # the company entirely. The city prefers; it must never filter.
        self.write_dataset()
        bench = salary_lookup.load_benchmarks()
        entry = bench.for_company("Cloudflare Portugal", "Lisbon, Portugal")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["company"], "Cloudflare Portugal")

    def test_a_partial_word_overlap_is_not_a_match(self):
        # Nobody reads this match before it reaches the screen. The CLI's
        # threshold would pair "Smart Working Solutions" with "Volkswagen
        # Digital Solutions" on the word they share, and print one company's
        # pay beside another company's name.
        self.write_dataset({
            "metadata": DATASET["metadata"],
            "companies": [{
                "company": "Volkswagen Digital Solutions",
                "city": "Lisboa",
                "categories": {"senior_frontend_eur_gross_annual": {"count": 5, "index": 70000}},
            }],
        })
        bench = salary_lookup.load_benchmarks()
        self.assertIsNone(bench.for_company("Smart Working Solutions"))
        self.assertIsNotNone(bench.for_company("Volkswagen Digital Solutions"))

    def test_the_category_with_the_most_sources_wins(self):
        self.write_dataset()
        bench = salary_lookup.load_benchmarks()
        label, _cat, value = bench.best_category(DATASET["companies"][0])
        self.assertEqual(label, "senior_frontend_eur_gross_annual")
        self.assertEqual(value, 88000)

    def test_a_figure_in_another_unit_is_not_chosen(self):
        # A USD figure and a EUR figure in one cell is two units pretending to
        # be one -- the rule compares_to_baseline() already applies.
        self.write_dataset({
            "metadata": DATASET["metadata"],
            "companies": [{
                "company": "Elsewhere Inc",
                "city": "Remote",
                "categories": {
                    "senior_frontend_usd_gross_annual": {"count": 9, "index": 150000},
                    "senior_frontend_eur_gross_annual": {"count": 2, "index": 65000},
                },
            }],
        })
        bench = salary_lookup.load_benchmarks()
        cell = bench.cell("Elsewhere Inc")
        self.assertEqual(cell["sort"], 65000.0)

    def test_a_category_with_no_figure_is_not_chosen(self):
        self.write_dataset({
            "metadata": DATASET["metadata"],
            "companies": [{
                "company": "Withheld Lda",
                "city": "Lisboa",
                "categories": {
                    "senior_frontend_eur_gross_annual": {"count": 9, "index": None},
                    "senior_fullstack_eur_gross_annual": {"count": 1, "index": 71000},
                },
            }],
        })
        bench = salary_lookup.load_benchmarks()
        self.assertEqual(bench.cell("Withheld Lda")["sort"], 71000.0)

    def test_the_dataset_is_parsed_once_for_the_whole_table(self):
        self.write_dataset()
        calls = []
        real = salary_lookup.load_data_quietly

        def counted():
            calls.append(1)
            return real()

        salary_lookup.load_data_quietly = counted
        self.addCleanup(setattr, salary_lookup, "load_data_quietly", real)
        for i in range(4):
            self.posting(url=f"https://jobs.example/n{i}")
        tracker.postings_table_data(self.conn, self.cfg)
        self.assertEqual(len(calls), 1)

    def test_a_figure_added_while_the_board_runs_is_picked_up(self):
        # /apply writes new figures with `salary_lookup.py add` while the board
        # sits open in a tab, and Refresh has to show them.
        path = self.write_dataset()
        self.assertEqual(salary_lookup.load_benchmarks().cell("Cloudflare Portugal")["sort"], 88000.0)
        raised = json.loads(path.read_text(encoding="utf-8"))
        raised["companies"][0]["categories"]["senior_frontend_eur_gross_annual"]["index"] = 99000
        path.write_text(json.dumps(raised, ensure_ascii=False), encoding="utf-8")
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))
        self.assertEqual(salary_lookup.load_benchmarks().cell("Cloudflare Portugal")["sort"], 99000.0)


class KanbanTest(SalaryFixture):
    def test_the_kanban_card_carries_the_salary(self):
        self.write_dataset()
        tracker.add_application(
            self.conn, self.cfg, company="Cloudflare Portugal", role="Frontend Engineer",
            url="https://jobs.example/k1", salary_text="80k EUR/year",
        )
        self.conn.commit()
        data = tracker.board_data(self.conn, self.cfg)
        cards = [c for col in data["columns"] for c in col["cards"]] + data["orphans"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["salary"]["kind"], "posting")
        self.assertEqual(cards[0]["salary"]["text"], "80k EUR/year")
        self.assertEqual(data["salary_meta"]["unit"], "EUR gross/year")

    def test_an_orphan_card_gets_one_too(self):
        # Orphans are annotated because the rows are the same objects, and they
        # are the column meant to be worked through by hand.
        self.write_dataset()
        tracker.add_application(
            self.conn, self.cfg, company="Cloudflare Portugal", role="Frontend Engineer",
            url="https://jobs.example/k2",
        )
        self.conn.execute("UPDATE applications SET status = 'no-such-status'")
        self.conn.commit()
        data = tracker.board_data(self.conn, self.cfg)
        self.assertEqual(len(data["orphans"]), 1)
        self.assertEqual(data["orphans"][0]["salary"]["kind"], "benchmark")


class MigrationTest(unittest.TestCase):
    def version_of(self, *names) -> str:
        """schema_version() over a directory holding exactly these filenames."""
        tmp = Path(tempfile.mkdtemp(prefix="careerforge-migrations-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in names:
            (tmp / name).write_text("", encoding="utf-8")
        return tracker.schema_version(tmp)

    def test_the_version_is_the_highest_migration_as_a_number(self):
        # "010" sorts below "009" as text, and a version that goes backwards
        # after the tenth migration is the same silent drift as the typed "4".
        self.assertEqual(self.version_of("002_a.sql", "009_b.sql", "010_c.py"), "10")

    def test_a_file_that_is_not_a_migration_does_not_count(self):
        self.assertEqual(
            self.version_of("002_a.sql", "999_notes.txt", "readme.md"), "2")

    def test_nothing_to_read_is_the_shape_before_the_first_migration(self):
        # A renamed or missing directory must not raise out of import; the
        # migrations themselves are tracked by filename, not by this number.
        self.assertEqual(self.version_of(), "1")
        self.assertEqual(tracker.schema_version(Path("no-such-directory")), "1")

    def test_the_database_records_the_version_the_module_derived(self):
        self.assertEqual(tracker.SCHEMA_VERSION, tracker.schema_version())
        self.assertGreaterEqual(int(tracker.SCHEMA_VERSION), 7)

    def test_migration_007_adds_the_salary_columns_to_an_old_database(self):
        import sqlite3
        tmp = Path(tempfile.mkdtemp(prefix="careerforge-mig007-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        conn = sqlite3.connect(tmp / "old.db")
        self.addCleanup(conn.close)
        conn.executescript(
            "CREATE TABLE applications(id INTEGER PRIMARY KEY);"
            "CREATE TABLE postings(id INTEGER PRIMARY KEY);"
        )
        conn.executescript(
            (tracker.MIGRATIONS_DIR / "007_salary.sql").read_text(encoding="utf-8")
        )
        for table in ("applications", "postings"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            self.assertIn("salary_text", cols, table)


if __name__ == "__main__":
    unittest.main()
