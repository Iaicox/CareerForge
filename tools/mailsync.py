#!/usr/bin/env python3
"""Find employer replies in a mailbox and match them to applications.

    python tools/mailsync.py check
    python tools/mailsync.py scan
    python tools/mailsync.py scan --since 2026-07-01 --json

Reads any IMAP mailbox -- the address you actually apply from, which is rarely
the one wired to a chat connector. Standard library only: imaplib and email.

**Read-only, deliberately.** The mailbox is opened with readonly=True and
messages are fetched with BODY.PEEK, so nothing is marked read, moved,
flagged or deleted. Running this must never change what the user sees in their
mail client.

**It proposes, it does not decide.** Classification from subject lines is a
heuristic; a "we regret" can be a rejection for one role inside an offer for
another. Every match is output with its evidence for a human, or Claude, to
confirm before any tracker status changes.

Credentials: MAIL_PASSWORD, or a .mail_password file in the repo root (both
gitignored). Use an app password, never your account password.
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import json
import os
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracker  # noqa: E402
from tracker import REPO, TrackerError  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_UNCONFIGURED = 0, 1, 3
PASSWORD_FILE = REPO / ".mail_password"
MAX_BODY_CHARS = 4000

# Ordered: the first pattern that matches wins, so an interview invitation is
# not read as a plain reply, and an offer outranks both.
CLASSIFIERS = [
    ("offer", re.compile(
        r"\b(offer letter|we(?:'| a)re (?:delighted|pleased) to offer|job offer|"
        r"offer of employment|contract attached)\b", re.I)),
    ("rejection", re.compile(
        r"\b(unfortunately|we regret|not (?:be )?(?:moving|proceeding|progressing)|"
        r"decided (?:not|to proceed with other)|unsuccessful|"
        r"another candidate|will not be taking your application)\b", re.I)),
    ("interview", re.compile(
        r"\b(interview|schedule a call|book a (?:time|slot)|meet the team|"
        r"technical (?:round|screen)|availability (?:for|next)|calendly|"
        r"would you be available)\b", re.I)),
    ("assignment", re.compile(
        r"\b(take[- ]home|coding (?:task|challenge|test)|assignment|"
        r"technical (?:task|exercise))\b", re.I)),
    ("acknowledgement", re.compile(
        r"\b(we(?:'| ha)ve received|thank you for (?:your )?appl|"
        r"application received|is under review)\b", re.I)),
]

# Mail from these is about the application but never from the employer.
NOISE_DOMAINS = {"linkedin.com", "indeed.com", "glassdoor.com", "google.com",
                 "notifications.google.com", "no-reply.accounts.google.com"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def mail_settings() -> dict:
    cfg = load_mail_config()
    if not cfg.get("host") or not cfg.get("user"):
        raise TrackerError(
            "no mailbox configured. Add a [mail] section to config/config.toml:\n"
            '  [mail]\n'
            '  host = "imap.gmail.com"\n'
            '  port = 993\n'
            '  user = "you@example.com"\n'
            '  mailbox = "INBOX"\n'
            "Then put an app password in MAIL_PASSWORD or .mail_password."
        )
    return cfg


def load_mail_config() -> dict:
    try:
        cfg = dict(tracker.load_config().data.get("mail", {}))
    except TrackerError:
        cfg = {}
    cfg.setdefault("port", 993)
    cfg.setdefault("mailbox", "INBOX")
    cfg.setdefault("ssl", True)
    return cfg


def password() -> str:
    tracker.load_dotenv()
    env = (os.environ.get("MAIL_PASSWORD") or "").strip()
    if env:
        return env
    if PASSWORD_FILE.exists():
        value = PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise TrackerError(
        f"no mail password. Put MAIL_PASSWORD=<app password> in .env, or in "
        f"{tracker.rel(PASSWORD_FILE)} (both gitignored).\n"
        "Gmail: myaccount.google.com/apppasswords -- never your account password."
    )


# ---------------------------------------------------------------------------
# IMAP
# ---------------------------------------------------------------------------


def connect_mailbox(cfg: dict) -> imaplib.IMAP4:
    try:
        if cfg.get("ssl", True):
            conn = imaplib.IMAP4_SSL(
                cfg["host"], int(cfg["port"]), ssl_context=ssl.create_default_context()
            )
        else:
            conn = imaplib.IMAP4(cfg["host"], int(cfg["port"]))
            conn.starttls(ssl.create_default_context())
    except (OSError, imaplib.IMAP4.error, ssl.SSLError) as exc:
        raise TrackerError(f"could not reach {cfg['host']}:{cfg['port']} -- {exc}")

    try:
        conn.login(cfg["user"], password())
    except imaplib.IMAP4.error as exc:
        raise TrackerError(
            f"login failed for {cfg['user']}: {exc}\n"
            "If this is Gmail, it needs an app password, not the account password."
        )
    # readonly=True is the guarantee that a scan never marks anything read.
    typ, _ = conn.select(cfg["mailbox"], readonly=True)
    if typ != "OK":
        conn.logout()
        raise TrackerError(f"cannot open mailbox {cfg['mailbox']!r}")
    return conn


def header_text(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, UnicodeDecodeError):
        return str(value)


def body_text(message: email.message.Message) -> str:
    parts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() != "text":
            continue
        if part.get_content_subtype() not in ("plain", "html"):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if part.get_content_subtype() == "html":
            text = re.sub(r"<[^>]+>", " ", text)
        parts.append(text)
        if sum(len(p) for p in parts) > MAX_BODY_CHARS * 4:
            break
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fetch_messages(conn: imaplib.IMAP4, since: datetime) -> list[dict]:
    criterion = since.strftime("%d-%b-%Y")
    typ, data = conn.search(None, "SINCE", criterion)
    if typ != "OK":
        raise TrackerError("mailbox search failed")
    ids = (data[0] or b"").split()

    messages: list[dict] = []
    for num in ids:
        # BODY.PEEK, so the \Seen flag is never set even if the server ignores
        # the read-only select.
        typ, payload = conn.fetch(num, "(BODY.PEEK[])")
        if typ != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        msg = email.message_from_bytes(payload[0][1])
        sender = header_text(msg.get("From"))
        addr = email.utils.parseaddr(sender)[1].lower()
        domain = addr.split("@")[-1] if "@" in addr else ""
        if any(domain.endswith(d) for d in NOISE_DOMAINS):
            continue
        messages.append({
            "uid": num.decode(),
            "from": sender,
            "from_address": addr,
            "domain": domain,
            "subject": header_text(msg.get("Subject")),
            "date": header_text(msg.get("Date")),
            "body": body_text(msg)[:MAX_BODY_CHARS],
        })
    return messages


# ---------------------------------------------------------------------------
# Matching and classification
# ---------------------------------------------------------------------------


def domain_of(url: str | None) -> str:
    if not url:
        return ""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1).lower() if m else ""


def match_application(message: dict, applications: list[dict]) -> tuple[dict | None, str]:
    """Returns (application, why). Strongest evidence first."""
    haystack = f"{message['subject']} {message['body']}".lower()

    for app in applications:
        if app.get("url") and app["url"].lower() in haystack:
            return app, "posting URL in the message"

    for app in applications:
        site = domain_of(app.get("company_website"))
        if site and message["domain"] and (
            message["domain"].endswith(site) or site.endswith(message["domain"])
        ):
            return app, f"sender domain matches {site}"

    for app in applications:
        name = (app.get("company_name") or "").strip()
        # Word boundaries, not a substring: "Meta" must not match "metadata",
        # and a three-letter name is too weak to match on at all.
        if len(name) >= 4 and re.search(rf"\b{re.escape(name.lower())}\b", haystack):
            return app, "company name in the message"

    return None, ""


def classify(message: dict) -> tuple[str, str]:
    """Returns (kind, the phrase that decided it)."""
    text = f"{message['subject']} {message['body']}"
    for kind, pattern in CLASSIFIERS:
        m = pattern.search(text)
        if m:
            return kind, m.group(0)
    return "unknown", ""


SUGGESTED_STATUS = {
    "offer": "offer",
    "rejection": "rejected",
    "interview": "screening",
    "assignment": "assignment",
    "acknowledgement": None,
    "unknown": None,
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_check(args) -> int:
    cfg = load_mail_config()
    if not cfg.get("host") or not cfg.get("user"):
        try:
            mail_settings()
        except TrackerError as exc:
            print(exc, file=sys.stderr)
        return EXIT_UNCONFIGURED
    print(f"  host     {cfg['host']}:{cfg['port']}")
    print(f"  user     {cfg['user']}")
    print(f"  mailbox  {cfg['mailbox']}")
    try:
        conn = connect_mailbox(cfg)
    except TrackerError as exc:
        print(f"\n  NOT USABLE: {exc}", file=sys.stderr)
        return EXIT_UNCONFIGURED
    try:
        typ, data = conn.search(None, "ALL")
        count = len((data[0] or b"").split()) if typ == "OK" else 0
        print(f"  status   connected, {count} message(s) visible, opened read-only")
    finally:
        conn.logout()
    return EXIT_OK


def cmd_scan(args) -> int:
    cfg = mail_settings()
    conn_db = tracker.connect()
    try:
        config = tracker.load_config()
        applications = [
            a for a in tracker.list_applications(conn_db, config)
            if a["stage"] != "rejected"
        ]
    finally:
        conn_db.close()

    if not applications:
        print("no open applications to match against")
        return EXIT_OK

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        oldest = min(
            (a["created_at"] or "")[:10] for a in applications if a.get("created_at")
        )
        since = datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc)
        since -= timedelta(days=1)

    conn = connect_mailbox(cfg)
    try:
        messages = fetch_messages(conn, since)
    finally:
        conn.logout()

    findings = []
    for message in messages:
        app, why = match_application(message, applications)
        if not app:
            continue
        kind, phrase = classify(message)
        findings.append({
            "application": {
                "id": app["id"], "slug": app["slug"],
                "company": app["company_name"], "role": app["role"],
                "status": app["status"], "status_label": app["status_label"],
            },
            "match_reason": why,
            "kind": kind,
            "matched_phrase": phrase,
            "suggested_status": SUGGESTED_STATUS.get(kind),
            "from": message["from"],
            "subject": message["subject"],
            "date": message["date"],
            "snippet": message["body"][:300],
        })

    # An application still marked as a draft, with a reply already sitting in
    # the mailbox, is how a forgotten submission surfaces.
    forgotten = [
        f for f in findings
        if f["application"]["status"] == "draft" and f["kind"] != "unknown"
    ]

    if args.json:
        print(json.dumps({
            "scanned_since": since.date().isoformat(),
            "messages_examined": len(messages),
            "findings": findings,
            "possibly_forgotten": forgotten,
        }, ensure_ascii=False, indent=2))
        return EXIT_OK

    print(f"scanned {len(messages)} message(s) since {since.date().isoformat()}")
    if not findings:
        print("nothing matched an open application")
        return EXIT_OK

    print(f"{len(findings)} message(s) matched:\n")
    for f in findings:
        app = f["application"]
        arrow = ""
        if f["suggested_status"] and f["suggested_status"] != app["status"]:
            arrow = f"  ->  propose status '{f['suggested_status']}'"
        print(f"  {app['company']} - {app['role']}  [{app['status_label']}]{arrow}")
        print(f"    kind     {f['kind']}"
              + (f"  (matched on \"{f['matched_phrase']}\")" if f["matched_phrase"] else ""))
        print(f"    matched  {f['match_reason']}")
        print(f"    from     {f['from']}")
        print(f"    subject  {f['subject'][:90]}")
        print()

    if forgotten:
        print("Still marked as drafts, but the employer has already replied:")
        for f in forgotten:
            print(f"  {f['application']['company']} - {f['application']['role']}")
        print()

    print("Nothing was changed. Confirm each one before updating the tracker.")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description="Match employer replies to applications")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="can the mailbox be reached?")

    s = sub.add_parser("scan", help="find replies to open applications")
    s.add_argument("--since", help="ISO date; defaults to the oldest open application")
    s.add_argument("--json", action="store_true")

    args = ap.parse_args()
    return {"check": cmd_check, "scan": cmd_scan}[args.command](args)


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_UNCONFIGURED)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)
