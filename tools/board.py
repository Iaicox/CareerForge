#!/usr/bin/env python3
"""CareerForge board: a local kanban over the tracker database.

    python tools/board.py            # serve on 127.0.0.1:8765 and open a browser
    python tools/board.py --port 9000 --no-browser

Standard library only. All data access goes through tools/tracker.py, so the
board and the agent share one set of rules -- including the one that moves an
application's folder between applications/, processing/ and rejected/ when its
status changes.

The server binds the loopback interface only and rejects requests whose Host
header is not loopback, because it writes to disk.
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import tracker
from tracker import TrackerError

INDEX = Path(__file__).resolve().parent / "board" / "index.html"
MAX_BODY = 1 << 20  # 1 MiB is far more than any card edit needs


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "CareerForgeBoard/1.0"
    allowed_hosts: set[str] = set()

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        if self.path.startswith("/api/") and self.command != "GET":
            super().log_message(fmt, *args)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.rsplit(":", 1)[0].strip("[]") if host else ""
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, message: str, code: int = 400) -> None:
        self._json({"error": message}, code)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise TrackerError("request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error("this board only serves loopback requests", 403)
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not INDEX.exists():
                return self._error(f"missing {INDEX}", 500)
            return self._send(
                200, INDEX.read_bytes(), "text/html; charset=utf-8"
            )
        if path == "/api/board":
            return self._with_db(lambda conn, cfg: tracker.board_data(conn, cfg))
        self._error("not found", 404)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._error("this board only serves loopback requests", 403)
        path = urlparse(self.path).path
        routes = {
            "/api/status": self._set_status,
            "/api/event": self._add_event,
            "/api/note": self._set_note,
        }
        handler = routes.get(path)
        if handler is None:
            return self._error("not found", 404)
        try:
            payload = self._read_json()
        except (ValueError, TrackerError) as exc:
            return self._error(str(exc))
        self._with_db(lambda conn, cfg: handler(conn, cfg, payload), write=True)

    # -- handlers ----------------------------------------------------------

    def _with_db(self, fn, write: bool = False) -> None:
        try:
            cfg = tracker.load_config()
            conn = tracker.connect()
        except TrackerError as exc:
            return self._error(str(exc), 500)
        try:
            if write:
                with conn:
                    result = fn(conn, cfg)
            else:
                result = fn(conn, cfg)
        except TrackerError as exc:
            return self._error(str(exc), 409)
        except Exception as exc:  # pragma: no cover - unexpected, still no 500 page
            return self._error(f"{type(exc).__name__}: {exc}", 500)
        finally:
            conn.close()
        self._json(result)

    def _set_status(self, conn, cfg, payload: dict) -> dict:
        ident = str(payload.get("id") or "")
        status = str(payload.get("status") or "")
        if not ident or not status:
            raise TrackerError("id and status are required")
        row, note = tracker.set_status(
            conn,
            cfg,
            ident,
            status,
            move=True,
            expected_updated_at=payload.get("updated_at"),
        )
        return {
            "application": tracker.enrich(dict(row), cfg),
            "folder": note,
            "board": tracker.board_data(conn, cfg),
        }

    def _add_event(self, conn, cfg, payload: dict) -> dict:
        ident = str(payload.get("id") or "")
        if not ident or not payload.get("type"):
            raise TrackerError("id and type are required")
        tracker.add_event(
            conn,
            cfg,
            ident,
            payload["type"],
            payload.get("date") or None,
            payload.get("participants") or None,
            payload.get("outcome") or None,
            payload.get("notes") or None,
        )
        return {"board": tracker.board_data(conn, cfg)}

    def _set_note(self, conn, cfg, payload: dict) -> dict:
        ident = str(payload.get("id") or "")
        if not ident:
            raise TrackerError("id is required")
        tracker.set_note(
            conn, ident, payload.get("text") or "", bool(payload.get("append"))
        )
        return {"board": tracker.board_data(conn, cfg)}


def main() -> int:
    ap = argparse.ArgumentParser(description="CareerForge kanban board")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    # Fail before binding a port if the tracker is not set up yet.
    tracker.load_config()
    tracker.connect().close()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), BoardHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"CareerForge board: {url}")
    print(f"database: {tracker.rel(tracker.DB_PATH)}")
    print("Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        raise SystemExit(main())
    except TrackerError as exc:
        print(f"error: {exc}")
        raise SystemExit(1)
