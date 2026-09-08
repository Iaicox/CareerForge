"""The board server: the kanban and the postings table, in one process.

The handler is served from a thread inside the test, against a throwaway repo
pointed at with paths.configure(), so nothing here touches the real database
or opens a browser.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import board  # noqa: E402
import paths  # noqa: E402
import tracker  # noqa: E402


class BoardServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="careerforge-board-")).resolve()
        self._real_repo = paths.REPO
        example = paths.CONFIG_EXAMPLE
        paths.configure(self.tmp)
        for stage in paths.STAGES:
            paths.stage_dir(stage).mkdir(parents=True)
        paths.CONFIG_DIR.mkdir(parents=True)
        shutil.copy(example, paths.CONFIG)
        tracker.load_config(force=True)
        tracker.init_db()
        self.cfg = tracker.load_config()
        conn = tracker.connect()
        with conn:
            tracker.upsert_postings(conn, self.cfg, [
                {"title": "Vue Dev", "company": "Acme", "url": "https://acme.example/jobs/1"},
                {"title": "React Dev", "company": "Beta", "url": "https://beta.example/jobs/2",
                 "status": "skipped", "note": "React only"},
            ])
        conn.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), board.BoardHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        paths.configure(self._real_repo)
        tracker.load_config(force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, path: str, body: dict | None = None, host: str | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        req.add_header("Content-Type", "application/json")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            with exc:  # it is a response: leaving it open warns on every run
                return exc.code, exc.read().decode("utf-8")

    def rows(self) -> list[dict]:
        status, body = self.request("/api/postings")
        self.assertEqual(status, 200)
        return json.loads(body)["rows"]

    def test_both_pages_are_served(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn('href="/postings"', html)
        status, html = self.request("/postings")
        self.assertEqual(status, 200)
        self.assertIn("/api/postings", html)

    def test_both_pages_gate_a_link_on_its_scheme(self):
        # The rendering is client-side, so this only guards the source: these
        # URLs come from scraped boards and pasted text, and both pages serve
        # unauthenticated POST endpoints on their own origin, so a javascript:
        # href would be click-to-run script against them. The behaviour itself
        # is checked in a browser -- see safeHref().
        #
        # Both pages, because the fix landed on the postings table first and
        # the kanban kept the hole for two commits.
        for route, raw in (("/postings", 'href="${esc(r.url)}"'),
                           ("/", 'href="${esc(card.url)}"')):
            with self.subTest(route=route):
                status, html = self.request(route)
                self.assertEqual(status, 200)
                self.assertIn("function safeHref", html)
                self.assertIn("/^https?:\\/\\//i", html)
                self.assertIn("safeHref(", html.split("function safeHref", 1)[1],
                              "defined but never called")
                self.assertNotIn(raw, html)

    def test_both_pages_carry_the_salary_column(self):
        # The payload and the pages are edited in different files, so assert on
        # the served source that each page still reads the key the other sends.
        for route, marker in (("/postings", "salaryCell("), ("/", "card.salary")):
            with self.subTest(route=route):
                status, html = self.request(route)
                self.assertEqual(status, 200)
                self.assertIn(marker, html)

    def test_the_table_header_can_be_sorted_and_does_not_cover_the_first_row(self):
        # .wrap must carry no overflow: any value other than visible makes it a
        # scroll container, and the sticky th then resolves its top against
        # .wrap instead of the viewport and parks itself over the rows.
        status, html = self.request("/postings")
        self.assertEqual(status, 200)
        wrap = [ln for ln in html.splitlines() if ln.strip().startswith(".wrap {")]
        self.assertEqual(len(wrap), 1)
        self.assertNotIn("overflow", wrap[0])
        self.assertIn("function toggleSort", html)
        self.assertIn("th[data-key]", html)

    def test_both_pages_render_dates_as_day_month_year(self):
        for route in ("/postings", "/"):
            with self.subTest(route=route):
                status, html = self.request(route)
                self.assertEqual(status, 200)
                self.assertIn("function fmtDate", html)
                self.assertIn("fmtDate(", html.split("function fmtDate", 1)[1],
                              "defined but never called")

    def test_a_workspace_with_no_salary_data_still_serves_both_pages(self):
        # salary_data.json is gitignored user data; this fixture has none, and
        # neither does CI. Both payloads must come back whole regardless.
        for route in ("/api/postings", "/api/board"):
            with self.subTest(route=route):
                status, body = self.request(route)
                self.assertEqual(status, 200)
                data = json.loads(body)
                self.assertIsNone(data["salary_meta"])

    def test_the_table_carries_rows_and_status_options(self):
        status, body = self.request("/api/postings")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual([s["id"] for s in data["statuses"]][:2], ["new", "ranked"])
        self.assertEqual(data["total"], 2)
        by_company = {r["company"]: r for r in data["rows"]}
        self.assertEqual(by_company["Beta"]["status"], "skipped")
        self.assertTrue(by_company["Beta"]["terminal"])
        self.assertEqual(by_company["Acme"]["status_label"], "New")

    def test_changing_a_status_from_the_select_persists(self):
        row = next(r for r in self.rows() if r["company"] == "Acme")
        status, body = self.request(
            "/api/posting/status", {"id": row["id"], "status": "maybe", "updated_at": row["updated_at"]}
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["posting"]["status"], "maybe")
        self.assertEqual(next(r for r in data["table"]["rows"] if r["id"] == row["id"])["status"], "maybe")

    def test_a_stale_row_is_refused_not_overwritten(self):
        row = next(r for r in self.rows() if r["company"] == "Acme")
        status, body = self.request(
            "/api/posting/status", {"id": row["id"], "status": "skipped", "updated_at": "1999-01-01 00:00:00"}
        )
        self.assertEqual(status, 409)
        self.assertIn("changed since you loaded it", json.loads(body)["error"])
        self.assertEqual(next(r for r in self.rows() if r["id"] == row["id"])["status"], "new")

    def test_an_unknown_status_is_refused(self):
        row = self.rows()[0]
        status, _ = self.request("/api/posting/status", {"id": row["id"], "status": "archived"})
        self.assertEqual(status, 409)

    def test_notes_are_edited_in_place(self):
        row = next(r for r in self.rows() if r["company"] == "Acme")
        status, body = self.request("/api/posting/note", {"id": row["id"], "text": "AEM in the stack"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["posting"]["note"], "AEM in the stack")

    def test_only_loopback_hosts_are_served(self):
        status, _ = self.request("/api/postings", host="evil.example")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
