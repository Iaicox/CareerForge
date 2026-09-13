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
from datetime import date, timedelta
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

    def test_every_page_is_served_and_links_to_the_others(self):
        # A page nobody can reach from the others is a page nobody opens.
        links = {"/": 'href="/"', "/postings": 'href="/postings"',
                 "/calendar": 'href="/calendar"'}
        for route in links:
            with self.subTest(route=route):
                status, html = self.request(route)
                self.assertEqual(status, 200)
                for other, link in links.items():
                    if other != route:
                        self.assertIn(link, html, f"{route} does not link {other}")
        self.assertIn("/api/postings", self.request("/postings")[1])
        self.assertIn("/api/calendar", self.request("/calendar")[1])

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
        # Measured after the header has its text, not only on load: #meta sits
        # inside <header> and wraps it onto a second line at narrow widths.
        head, _, tail = html.partition("function render()")
        self.assertIn("syncStickyOffset();", tail.split("\nfunction ", 1)[0])

    def test_the_ranked_columns_sort_by_rank_and_not_by_their_label(self):
        # Statuses are the user's own, in the user's language, and several
        # locales prefix them with an emoji: ordering the rendered label sorts
        # the funnel by codepoint. Verdict has a ranking of its own on the
        # server. Both orders have to travel with the payload.
        status, body = self.request("/api/postings")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["verdict_order"][0], "strong")
        self.assertEqual(data["verdict_order"][-1], "poor")

        status, html = self.request("/postings")
        self.assertEqual(status, 200)
        self.assertIn("rankOf(statusRank, r.status)", html)
        self.assertIn("rankOf(verdictRank, r.verdict)", html)
        self.assertNotIn("(r) => r.status_label", html)

    def test_a_benchmark_is_marked_as_one_everywhere_it_is_printed(self):
        # A market figure printed bare reads as what the employer offered.
        # The card marks it; so must the panel behind the card.
        status, html = self.request("/")
        self.assertEqual(status, 200)
        panel = html.split("<dt>Salary</dt>", 1)[1].split("<dt>Contact</dt>", 1)[0]
        self.assertIn('card.salary.kind === "benchmark"', panel)
        self.assertIn("&asymp;", panel)
        self.assertIn("state.salary_meta", html, "no legend for the marking")

    def test_every_page_renders_dates_as_day_month_year(self):
        for route in ("/postings", "/", "/calendar"):
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

    # -- the calendar ------------------------------------------------------

    def application(self, company="Acme", role="Frontend Engineer"):
        conn = tracker.connect()
        with conn:
            row = tracker.add_application(conn, self.cfg, company=company, role=role)
        conn.close()
        return dict(row)

    def calendar(self) -> dict:
        status, body = self.request("/api/calendar")
        self.assertEqual(status, 200)
        return json.loads(body)

    @staticmethod
    def days(n):
        return (date.today() + timedelta(days=n)).isoformat()

    def test_the_calendar_payload_has_its_shape(self):
        data = self.calendar()
        for key in ("today", "events", "follow_ups", "applications",
                    "event_types", "outcomes", "follow_up_after_days",
                    "stale_after_days"):
            self.assertIn(key, data)
        self.assertEqual(data["follow_up_after_days"], 7)
        self.assertRegex(data["today"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(data["events"], [])

    def test_an_event_with_a_time_round_trips_through_the_api(self):
        row = self.application(company="Hooli")
        status, _ = self.request("/api/event", {
            "id": row["id"], "type": "tech_interview",
            "date": f"{self.days(5)}T14:00", "outcome": "pending",
        })
        self.assertEqual(status, 200)
        data = self.calendar()
        self.assertEqual(len(data["events"]), 1)
        event = data["events"][0]
        self.assertEqual(event["is_datetime"], 1)
        self.assertTrue(event["date"].endswith("T14:00"))
        self.assertEqual(event["company"], "Hooli")
        # An interview already booked is the answer; nothing to chase.
        self.assertEqual(data["follow_ups"], [])

    def test_a_follow_up_sent_from_the_calendar_restarts_the_clock(self):
        row = self.application(company="Globex")
        self.request("/api/event", {
            "id": row["id"], "type": "applied",
            "date": self.days(-20), "outcome": "passed",
        })
        self.assertTrue(self.calendar()["follow_ups"][0]["overdue"])

        status, _ = self.request("/api/event", {
            "id": row["id"], "type": "follow_up", "date": self.days(0),
        })
        self.assertEqual(status, 200)
        due = self.calendar()["follow_ups"][0]
        self.assertEqual(due["due"], self.days(7))
        self.assertFalse(due["overdue"])

    def test_the_view_flag_knows_the_calendar(self):
        parsed = board.build_parser().parse_args(["--view", "calendar"])
        self.assertEqual(parsed.view, "calendar")
        self.assertIn("calendar", board.build_parser().format_help())

    def test_the_calendar_never_builds_a_day_out_of_utc(self):
        # Events are naive local wall-clock strings. new Date("2026-09-01") is
        # UTC midnight -- the day before, west of Greenwich -- so a parsed date
        # would land an interview on the wrong square. Both the day and the
        # time are read by slicing the string.
        status, html = self.request("/calendar")
        self.assertEqual(status, 200)
        self.assertNotIn("toISOString", html)
        self.assertNotIn("new Date(ev.", html)
        self.assertNotIn("Date.parse", html)
        self.assertIn(".slice(11, 16)", html)

    def test_the_calendar_reloads_rather_than_reading_the_post_back(self):
        # /api/event answers with the kanban payload, which this page cannot
        # render. It has to fetch its own after a write.
        status, html = self.request("/calendar")
        self.assertEqual(status, 200)
        self.assertIn('"/api/event"', html)
        self.assertNotIn("res.board", html)
        self.assertIn("await load();", html)

    def test_only_loopback_hosts_are_served(self):
        status, _ = self.request("/api/postings", host="evil.example")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
