"""The Postings database in the Notion mirror: provision, adopt, push, import.

No network. `notion_sync.request` and `notion_sync.query_source` are swapped
for fakes; assertions are on the (method, path, payload) calls and on SQLite.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))

from test_notion_sync import MirrorTestCase  # noqa: E402

import notion_sync  # noqa: E402
import paths  # noqa: E402
import tracker  # noqa: E402
from tracker import TrackerError  # noqa: E402

IDS = {
    "applications": {"database_id": "db-apps", "data_source_id": "ds-apps"},
    "companies": {"database_id": "db-companies"},
    "events": {"database_id": "db-events", "data_source_id": "ds-events"},
    "postings": {"database_id": "db-postings", "data_source_id": "ds-postings"},
}


def text_prop(kind: str, value: str) -> dict:
    return {"type": kind, kind: [{"plain_text": value}]}


def page(page_id: str, **props) -> dict:
    """A Notion page in the hand-kept «Вакансии» shape, or the provisioned one."""
    out = {"id": page_id, "created_time": "2026-07-01T10:00:00.000Z", "properties": {}}
    for name, (kind, value) in props.items():
        if kind in ("title", "rich_text"):
            out["properties"][name] = text_prop(kind, value)
        elif kind == "url":
            out["properties"][name] = {"type": "url", "url": value}
        elif kind == "select":
            out["properties"][name] = {"type": "select", "select": {"name": value}}
        elif kind == "number":
            out["properties"][name] = {"type": "number", "number": value}
    return out


class NotionPostingsCase(MirrorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.calls: list[tuple[str, str, dict]] = []
        self.pages: dict[str, list[dict]] = {}
        self.addCleanup(setattr, notion_sync, "request", notion_sync.request)
        self.addCleanup(setattr, notion_sync, "query_source", notion_sync.query_source)
        self.addCleanup(setattr, notion_sync, "load_ids", notion_sync.load_ids)
        self.addCleanup(setattr, notion_sync, "save_ids", notion_sync.save_ids)
        notion_sync.request = self.fake_request
        notion_sync.query_source = lambda entry: iter(self.pages.get(entry["database_id"], []))
        notion_sync.load_ids = lambda: dict(IDS)
        self.saved_ids: dict | None = None
        notion_sync.save_ids = lambda ids: setattr(self, "saved_ids", ids)
        self.responses: dict[tuple[str, str], dict] = {}
        # MirrorTestCase's application brought a posting row with it; these
        # tests count postings, so start from none.
        with self.conn:
            self.conn.execute("DELETE FROM postings")

    def fake_request(self, method, path, payload=None):
        self.calls.append((method, path, payload or {}))
        if (method, path) in self.responses:
            return self.responses[(method, path)]
        return {"id": f"created-{len(self.calls)}"}

    def posting_rows(self):
        return tracker.list_postings(self.conn, self.cfg, all=True)

    def capture(self, fn, *args, **kw) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(*args, **kw)
        return out.getvalue()


class ProvisionTest(NotionPostingsCase):
    def test_provision_creates_postings_and_links_it_to_applications(self):
        self.responses[("POST", "/databases")] = {"id": "db-new", "data_sources": [{"id": "ds-new"}]}
        out = self.capture(notion_sync.provision, "3a09e8e2b680813da335fbd91fb27f0c", False)
        created = [c for c in self.calls if c[0] == "POST" and c[1] == "/databases"]
        titles = [c[2]["title"][0]["text"]["content"] for c in created]
        self.assertEqual(titles, ["Companies", "Applications", "Events", "Postings"])
        postings_props = created[3][2]["properties"]
        self.assertEqual(next(iter(postings_props["Status"])), "select")
        self.assertEqual(
            [o["name"] for o in postings_props["Status"]["select"]["options"]],
            [self.cfg.label("posting_statuses", s["id"]) for s in self.cfg.posting_statuses],
        )
        relations = [c for c in self.calls if c[0] == "PATCH" and "Application" in c[2].get("properties", {})]
        self.assertEqual(len(relations), 2, "events and postings both relate to applications")
        self.assertIn("postings", self.saved_ids)
        self.assertIn("created Postings", out)

    def test_provision_dry_run_names_postings_and_writes_nothing(self):
        out = self.capture(notion_sync.provision, "3a09e8e2b680813da335fbd91fb27f0c", True)
        self.assertIn("Postings:", out)
        self.assertEqual(self.calls, [])

    def test_sync_options_covers_the_postings_status(self):
        self.assertIn(("postings", "Status", "posting_statuses"), notion_sync.CONFIG_SELECTS)


class AdoptTest(NotionPostingsCase):
    RUSSIAN = {
        "Название": {"type": "title"},
        "Компания": {"type": "rich_text"},
        "Ссылка": {"type": "url"},
        "Статус": {"type": "select", "select": {"options": [{"name": "Откликнулся"}]}},
        "Ньюансы": {"type": "rich_text"},
    }

    def test_the_plan_renames_by_type_and_alias_and_adds_the_rest(self):
        renames, additions = notion_sync.adopt_plan(self.cfg, self.RUSSIAN, IDS["applications"])
        self.assertEqual(renames, {
            "Название": "Name", "Ссылка": "URL", "Статус": "Status",
            "Компания": "Company", "Ньюансы": "Note",
        })
        self.assertEqual(
            sorted(additions), ["Application", "Deadline", "First seen", "Score", "Source", "Verdict"]
        )
        self.assertEqual(additions["Application"]["relation"], {"data_source_id": "ds-apps", "single_property": {}})

    def test_a_database_that_already_matches_needs_nothing(self):
        existing = {name: {"type": next(iter(schema))}
                    for name, schema in notion_sync.postings_props(self.cfg, IDS["applications"]).items()}
        renames, additions = notion_sync.adopt_plan(self.cfg, existing, IDS["applications"])
        self.assertEqual((renames, additions), ({}, {}))

    def test_two_url_properties_are_refused_not_guessed(self):
        existing = dict(self.RUSSIAN, **{"Ещё ссылка": {"type": "url"}})
        with self.assertRaises(TrackerError):
            notion_sync.adopt_plan(self.cfg, existing, IDS["applications"])

    def test_a_name_with_the_wrong_type_is_refused(self):
        existing = dict(self.RUSSIAN, **{"Score": {"type": "rich_text"}})
        with self.assertRaises(TrackerError):
            notion_sync.adopt_plan(self.cfg, existing, IDS["applications"])

    def test_adopt_patches_the_schema_and_records_the_ids(self):
        self.responses[("GET", "/databases/3a69e8e2-b680-8004-bab0-fd6ff294489e")] = {
            "id": "3a69e8e2-b680-8004-bab0-fd6ff294489e",
            "title": [{"plain_text": "Вакансии"}],
            "data_sources": [{"id": "ds-vac"}],
        }
        self.responses[("GET", "/data_sources/ds-vac")] = {"properties": self.RUSSIAN}
        out = self.capture(notion_sync.do_adopt, "postings",
                           "https://app.notion.com/p/3a69e8e2b6808004bab0fd6ff294489e", False)
        patch = next(c for c in self.calls if c[0] == "PATCH")
        self.assertEqual(patch[1], "/data_sources/ds-vac")
        props = patch[2]["properties"]
        self.assertEqual(props["Ссылка"], {"name": "URL"})
        self.assertEqual(props["Ньюансы"], {"name": "Note"})
        self.assertEqual(next(iter(props["Score"])), "number")
        self.assertEqual(self.saved_ids["postings"],
                         {"database_id": "3a69e8e2-b680-8004-bab0-fd6ff294489e", "data_source_id": "ds-vac"})
        self.assertIn("adopting Вакансии", out)

    def test_adopt_dry_run_writes_nothing(self):
        self.responses[("GET", "/databases/3a69e8e2-b680-8004-bab0-fd6ff294489e")] = {
            "id": "3a69e8e2-b680-8004-bab0-fd6ff294489e", "title": [], "data_sources": [{"id": "ds-vac"}],
        }
        self.responses[("GET", "/data_sources/ds-vac")] = {"properties": self.RUSSIAN}
        self.capture(notion_sync.do_adopt, "postings", "3a69e8e2b6808004bab0fd6ff294489e", True)
        self.assertEqual([c[0] for c in self.calls], ["GET", "GET"])
        self.assertIsNone(self.saved_ids)


class PushPostingsTest(NotionPostingsCase):
    def seed(self, **kw) -> int:
        p = {"title": "Vue Dev", "company": "Acme", "url": "https://acme.example/jobs/1"}
        p.update(kw)
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [p])
        return next(r["id"] for r in self.posting_rows() if r["url_key"] == tracker.normalize_url(p["url"]))

    def push(self, dry_run=False):
        return notion_sync.push_postings(self.conn, self.cfg, IDS, dry_run)

    def page_ids(self):
        return {r["id"]: r["notion_page_id"] for r in self.posting_rows()}

    def test_props_carry_the_tracker_side_and_only_a_note_that_exists(self):
        row = {"status": "skipped", "company": "Acme", "url": "https://acme.example/1",
               "source": "linkedin", "score": 42, "verdict": "moderate", "deadline": "2026-09-01",
               "first_seen": "2026-08-01", "note": None, "title": "Vue Dev"}
        props = notion_sync.posting_props(self.cfg, row, None, with_title=False)
        self.assertEqual(props["Status"]["select"]["name"], self.cfg.label("posting_statuses", "skipped"))
        self.assertEqual(props["Score"], {"number": 42})
        self.assertEqual(props["Verdict"]["select"]["name"], "moderate")
        self.assertNotIn("Note", props, "an empty note never clears what was typed in Notion")
        self.assertNotIn("Name", props, "the title is set only on create")
        self.assertNotIn("Application", props)
        props = notion_sync.posting_props(self.cfg, dict(row, note="React only"), "app-page-1", with_title=True)
        self.assertEqual(props["Note"]["rich_text"][0]["text"]["content"], "React only")
        self.assertEqual(props["Name"]["title"][0]["text"]["content"], "Vue Dev")
        self.assertEqual(props["Application"]["relation"], [{"id": "app-page-1"}])

    def test_a_posting_notion_has_never_seen_is_created_then_updated(self):
        pid = self.seed()
        self.assertEqual(self.push(), (1, 0))
        post = [c for c in self.calls if c[0] == "POST"]
        self.assertEqual(post[0][1], "/pages")
        self.assertEqual(post[0][2]["parent"], {"type": "data_source_id", "data_source_id": "ds-postings"})
        self.assertEqual(self.page_ids()[pid], "created-1")
        self.calls.clear()
        self.assertEqual(self.push(), (0, 1))
        self.assertEqual([c[0] for c in self.calls], ["PATCH"])
        self.assertEqual(self.calls[0][1], "/pages/created-1")
        self.assertNotIn("Name", self.calls[0][2]["properties"])

    def test_a_hand_made_page_is_adopted_by_url_not_duplicated(self):
        pid = self.seed(url="https://www.linkedin.com/jobs/view/4441450970/")
        self.pages["db-postings"] = [page(
            "hand-page",
            Название=("title", "Vue Developer — streamways"),
            Компания=("rich_text", "streamways GmbH"),
            Ссылка=("url", "https://www.linkedin.com/jobs/view/4441450970/?refId=abc"),
            Статус=("select", "Не подходит"),
        )]
        self.assertEqual(self.push(), (0, 1))
        self.assertEqual([c[0] for c in self.calls], ["PATCH"])
        self.assertEqual(self.calls[0][1], "/pages/hand-page")
        self.assertEqual(self.page_ids()[pid], "hand-page")

    def test_two_local_rows_cannot_claim_one_page(self):
        a = self.seed(url="https://acme.example/jobs/1")
        b = self.seed(url="https://acme.example/jobs/2")
        with self.conn:
            self.conn.execute("UPDATE postings SET notion_page_id = 'same' WHERE id IN (?, ?)", (a, b))
        out = self.capture(self.push)
        self.assertIn("shares Notion page same", out)

    def test_the_application_relation_points_at_the_application_page(self):
        with self.conn:
            self.conn.execute("UPDATE applications SET notion_page_id = 'app-page-7' WHERE id = ?", (self.app["id"],))
            tracker.link_posting(self.conn, self.cfg, tracker.resolve(self.conn, str(self.app["id"])))
        self.push()
        post = next(c for c in self.calls if c[0] == "POST")
        self.assertEqual(post[2]["properties"]["Application"]["relation"], [{"id": "app-page-7"}])
        self.assertEqual(post[2]["properties"]["Status"]["select"]["name"],
                         self.cfg.label("posting_statuses", "applied"))

    def test_dry_run_counts_and_writes_nothing(self):
        pid = self.seed()
        self.assertEqual(self.push(dry_run=True), (1, 0))
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.page_ids()[pid])

    def test_do_push_reports_postings_after_applications(self):
        self.seed()
        out = self.capture(notion_sync.do_push, None, False, True, True, True)
        self.assertIn("postings: would create 1, would update 0", out)
        out = self.capture(notion_sync.do_push, None, False, True, True, False)
        self.assertNotIn("postings:", out)


class ImportPostingsTest(NotionPostingsCase):
    def vacancies(self):
        self.pages["db-postings"] = [
            page("p-applied", Название=("title", "Frontend Engineer"), Компания=("rich_text", "Acme"),
                 Ссылка=("url", "https://acme.example/jobs/7?utm_source=x"), Статус=("select", "Откликнулся"),
                 Ньюансы=("rich_text", "Тир 1")),
            page("p-skip", Название=("title", "React Dev"), Компания=("rich_text", "Beta"),
                 Ссылка=("url", "https://beta.example/jobs/2"), Статус=("select", "Не подходит"),
                 Ньюансы=("rich_text", "React only")),
            page("p-maybe", Название=("title", "Full-stack"), Компания=("rich_text", "Gamma"),
                 Ссылка=("url", "https://gamma.example/jobs/3"), Статус=("select", "Есть ньюанс"),
                 Ньюансы=("rich_text", "Golang in the core")),
            page("p-dup", Название=("title", "Dup"), Компания=("rich_text", "Delta"),
                 Ссылка=("url", "https://delta.example/jobs/4"), Статус=("select", "Дубль"),
                 Ньюансы=("rich_text", "duplicate of #12")),
            page("p-odd", Название=("title", "Odd"), Компания=("rich_text", "Eps"),
                 Ссылка=("url", "https://eps.example/jobs/5"), Статус=("select", "Пауза"),
                 Ньюансы=("rich_text", "on hold")),
            page("p-junk", Название=("title", "No key")),
        ]

    def run_import(self, dry_run=False, mapping=None) -> str:
        return self.capture(notion_sync.import_postings, self.conn, self.cfg, IDS, dry_run,
                            mapping or {"Есть ньюанс": "maybe", "Дубль": "skipped", "Отказ": "applied"})

    def test_labels_map_through_config_and_map_and_urls_link_applications(self):
        with self.conn:
            app = tracker.add_application(self.conn, self.cfg, company="Acme", role="Vue Lead",
                                          url="https://acme.example/jobs/7")
        self.vacancies()
        out = self.run_import()
        self.assertIn("imported: 4 posting(s); already present: 1", out)
        self.assertIn("1 page(s) had neither a URL", out)
        self.assertIn("Пауза", out)
        by_key = {r["url_key"]: r for r in self.posting_rows()}
        acme = by_key["acme.example/jobs/7"]
        self.assertEqual((acme["status"], acme["application_id"], acme["notion_page_id"], acme["note"]),
                         ("applied", app["id"], "p-applied", "Тир 1"))
        self.assertEqual((by_key["beta.example/jobs/2"]["status"], by_key["beta.example/jobs/2"]["note"]),
                         ("skipped", "React only"))
        self.assertEqual(by_key["gamma.example/jobs/3"]["status"], "maybe")
        self.assertEqual(by_key["delta.example/jobs/4"]["status"], "skipped")
        odd = by_key["eps.example/jobs/5"]
        self.assertEqual((odd["status"], odd["note"]), ("new", "[Пауза] on hold"))
        self.assertEqual(odd["first_seen"], "2026-07-01")

    def test_a_second_import_finds_everything_present(self):
        self.vacancies()
        self.run_import()
        out = self.run_import()
        self.assertIn("imported: 0 posting(s); already present: 5", out)

    def test_dry_run_counts_without_writing(self):
        self.vacancies()
        out = self.run_import(dry_run=True)
        self.assertIn("would import: 5 posting(s)", out)
        self.assertEqual(self.posting_rows(), [])

    def test_a_terminal_local_status_is_never_downgraded_by_a_page(self):
        with self.conn:
            tracker.upsert_postings(self.conn, self.cfg, [
                {"title": "React Dev", "company": "Beta", "url": "https://beta.example/jobs/2",
                 "status": "applied"},
            ])
        self.vacancies()
        self.run_import()
        row = next(r for r in self.posting_rows() if r["url_key"] == "beta.example/jobs/2")
        self.assertEqual((row["status"], row["note"], row["notion_page_id"]), ("applied", "React only", "p-skip"))

    def test_a_label_without_its_emoji_still_matches_config(self):
        # Config says "⏭ Не подходит"; a select typed by hand says "Не подходит".
        self.assertEqual(notion_sync.match_by_label(self.cfg, "posting_statuses", "Не подходит"), "skipped")
        self.assertEqual(notion_sync.match_by_label(self.cfg, "posting_statuses", "откликнулся "), "applied")
        self.assertEqual(notion_sync.match_by_label(self.cfg, "statuses", "Скрининг"), "screening")
        self.assertIsNone(notion_sync.match_by_label(self.cfg, "posting_statuses", "Пауза"))

    def test_parse_map(self):
        self.assertEqual(notion_sync.parse_map("A=maybe, B = skipped,"), {"A": "maybe", "B": "skipped"})
        self.assertEqual(notion_sync.parse_map(None), {})
        with self.assertRaises(TrackerError):
            notion_sync.parse_map("nonsense")


if __name__ == "__main__":
    unittest.main()
