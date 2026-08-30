"""tracker.http_request: the one transport under Gemini and the Notion mirror.

Both used to carry their own urllib client, and the two disagreed about what a
socket timeout was -- a TimeoutError to one, a retryable URLError to the other.
The suites covering those modules swap the whole request function out, so this
is the only place the wire behaviour is checked at all.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import notion_sync  # noqa: E402
import tracker  # noqa: E402


class Routes(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.answer()

    def do_POST(self) -> None:  # noqa: N802
        self.answer()

    def answer(self) -> None:
        sent = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, body, extra = 200, b"", {}
        if self.path == "/json":
            body = json.dumps({"ok": True, "echo": sent.decode()}).encode()
        elif self.path == "/empty":
            status = 204
        elif self.path == "/text":
            body = b"not json at all"
        elif self.path == "/429":
            status, body, extra = 429, b'{"message": "slow down"}', {"Retry-After": "7"}
        elif self.path == "/500":
            status, body = 500, b"<html>boom</html>"
        elif self.path == "/hang":
            time.sleep(3)
            body = b"{}"
        else:
            status, body = 404, b'{"error": "no such route"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in extra.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # the suite's output is not a web server log


class HttpRequestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Routes)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_a_json_body_comes_back_parsed_beside_its_status(self):
        res = tracker.http_request(self.base + "/json", method="POST", data=b"hello")
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data, {"ok": True, "echo": "hello"})

    def test_an_empty_body_is_not_a_parse_error(self):
        res = tracker.http_request(self.base + "/empty")
        self.assertEqual((res.status, res.text, res.data), (204, "", None))

    def test_a_body_that_is_not_json_keeps_its_text(self):
        res = tracker.http_request(self.base + "/text")
        self.assertEqual((res.status, res.data), (200, None))
        self.assertEqual(res.text, "not json at all")

    def test_an_error_status_is_returned_rather_than_raised(self):
        # Google names the quota it exhausted in the body of a 429 and Notion
        # names the field it rejected in a 400. Raising would throw that away
        # before the caller that knows what to do with it ever sees it.
        res = tracker.http_request(self.base + "/429")
        self.assertEqual(res.status, 429)
        self.assertEqual(res.data, {"message": "slow down"})
        self.assertEqual(res.headers.get("Retry-After"), "7")

    def test_a_5xx_with_an_html_body_still_carries_its_text(self):
        res = tracker.http_request(self.base + "/500")
        self.assertEqual((res.status, res.data), (500, None))
        self.assertIn("boom", res.text)

    def test_a_silent_socket_is_a_timeout_not_a_transport_error(self):
        # gemini.call() clamps each attempt against its deadline and only a
        # TimeoutError tells it the model was slow rather than broken.
        with self.assertRaises(TimeoutError):
            tracker.http_request(self.base + "/hang", timeout=1)

    def test_a_refused_connection_is_a_transport_error(self):
        with self.assertRaises(tracker.TransportError):
            tracker.http_request("http://127.0.0.1:1/nothing", timeout=5)


class NotionRetryPolicyTest(unittest.TestCase):
    """The policy stays with the caller: the transport neither retries nor paces."""

    def setUp(self) -> None:
        self.slept: list[float] = []

    def ask(self, *answers, retries=4):
        queue = list(answers)

        def fake_request(url, **kwargs):
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        with mock.patch.object(tracker, "http_request", fake_request), \
                mock.patch.object(notion_sync, "pace", lambda: None), \
                mock.patch.object(notion_sync, "token", lambda: "test-token"), \
                mock.patch.object(time, "sleep", self.slept.append):
            return notion_sync.request("GET", "/v1/x", retries=retries)

    @staticmethod
    def reply(status, data=None, headers=None, text=""):
        return tracker.Response(status, headers or {}, text or json.dumps(data or {}), data)

    def test_a_429_waits_the_delay_the_server_named(self):
        out = self.ask(self.reply(429, {"m": "slow"}, {"Retry-After": "7"}),
                       self.reply(200, {"ok": True}))
        self.assertEqual(out, {"ok": True})
        self.assertEqual(self.slept, [7.0])

    def test_an_absurd_retry_after_is_capped(self):
        self.ask(self.reply(503, {}, {"Retry-After": "99999"}), self.reply(200, {"ok": 1}))
        self.assertEqual(self.slept, [30])

    def test_a_broken_connection_is_retried_and_then_reported(self):
        out = self.ask(tracker.TransportError("connection reset"), self.reply(200, {"ok": 1}))
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(self.slept, [1])

        self.slept.clear()
        with self.assertRaises(tracker.TrackerError) as ctx:
            self.ask(tracker.TransportError("connection reset"), retries=0)
        self.assertIn("connection reset", str(ctx.exception))

    def test_a_socket_timeout_is_retried_too(self):
        # It was not, before: a read timeout is not a URLError, so it escaped
        # the retry loop and the caller saw a bare TimeoutError.
        out = self.ask(TimeoutError("timed out"), self.reply(200, {"ok": 1}))
        self.assertEqual(out, {"ok": 1})

    def test_a_4xx_is_not_retried_and_carries_the_body(self):
        with self.assertRaises(tracker.TrackerError) as ctx:
            self.ask(self.reply(400, text="body is not valid for property Deadline"))
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertIn("property Deadline", str(ctx.exception))
        self.assertEqual(self.slept, [])

    def test_a_success_with_no_body_is_an_empty_dict(self):
        self.assertEqual(self.ask(self.reply(204, None, text="")), {})


if __name__ == "__main__":
    unittest.main()
