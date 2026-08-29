#!/usr/bin/env python3
"""CareerForge application tracker.

A local SQLite tracker for job applications: companies, applications, funnel
events and document attachments. Standard library only.

This module is also the shared data-access layer for tools/board.py and
tools/notion_sync.py -- neither of them talks to SQLite directly.

    python tools/tracker.py init
    python tools/tracker.py add --company Acme --role "Senior Frontend Developer" \
                                --url https://acme.example/jobs/1
    python tools/tracker.py list --stage applications
    python tools/tracker.py set-status acme screening
    python tools/tracker.py report --board
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tomllib
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit

import paths
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
# What a file in here may contain. apply_migrations() wraps it in BEGIN/COMMIT
# together with the row that records it, so:
#   - no transaction control of its own -- no BEGIN, COMMIT or SAVEPOINT;
#   - no PRAGMA. SQLite ignores `PRAGMA foreign_keys` inside a transaction,
#     silently, so the documented table-rebuild recipe cannot be written here as
#     it stands: connect() leaves foreign keys ON and the rebuild would drop
#     references without erroring. Such a migration needs the wrapper changed,
#     not a pragma smuggled into the file;
#   - every statement terminated with `;`. The bookkeeping INSERT is appended
#     to the text, so a missing final semicolon glues it onto the last statement
#     and the syntax error points at the INSERT rather than at the file.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_VERSION = "4"
STAGES = paths.STAGES

# Where a posting stands, from the moment it is seen. config.toml may carry a
# [[posting_statuses]] table with the same shape; when it does not, these apply,
# so a config written before postings existed keeps working.
DEFAULT_POSTING_STATUSES: tuple[dict, ...] = (
    {"id": "new", "labels": {"en": "New", "ru": "🆕 Новая"}},
    {"id": "ranked", "labels": {"en": "Ranked", "ru": "📊 Оценена"}},
    {"id": "maybe", "labels": {"en": "Maybe", "ru": "🤔 Есть нюанс"}},
    {"id": "applied", "terminal": True, "labels": {"en": "Applied", "ru": "📤 Откликнулся"}},
    {"id": "skipped", "terminal": True, "labels": {"en": "Skipped", "ru": "⏭ Не подходит"}},
    {"id": "expired", "terminal": True, "labels": {"en": "Expired", "ru": "⌛ Истекла"}},
)


class TrackerError(Exception):
    """Anything the user should see as a plain message, not a traceback."""


_dotenv_loaded = False


def load_dotenv() -> None:
    """Load .env from the repo root into os.environ, once, without overriding anything.

    The standard library does not read .env files, and the secrets consumers
    (Gemini key, Notion token, mail password) all live there now. Real
    environment variables always win over the file: a value set for one shell
    session must not be silently shadowed by an old line in .env.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    path = paths.ENV
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Values may be quoted; the quotes are not part of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class Config:
    def __init__(self, data: dict, source: Path):
        self.data = data
        self.source = source

    @property
    def locale(self) -> str:
        return self.data.get("locale", "en")

    def _table(self, key: str) -> list[dict]:
        rows = list(self.data.get(key, []))
        if not rows and key == "posting_statuses":
            return [
                {**s, "labels": dict(s["labels"])} for s in DEFAULT_POSTING_STATUSES
            ]
        return rows

    def items(self, key: str) -> list[dict]:
        """A config table by name, with the built-in defaults where they apply."""
        return self._table(key)

    @property
    def statuses(self) -> list[dict]:
        return self._table("statuses")

    @property
    def work_modes(self) -> list[dict]:
        return self._table("work_modes")

    @property
    def event_types(self) -> list[dict]:
        return self._table("event_types")

    @property
    def outcomes(self) -> list[dict]:
        return self._table("outcomes")

    @property
    def posting_statuses(self) -> list[dict]:
        return self._table("posting_statuses")

    def posting_status(self, status_id: str) -> dict:
        for s in self.posting_statuses:
            if s["id"] == status_id:
                return s
        raise TrackerError(
            f"unknown posting status {status_id!r}; configured: "
            + ", ".join(s["id"] for s in self.posting_statuses)
        )

    def posting_is_terminal(self, status_id: str | None) -> bool:
        for s in self.posting_statuses:
            if s["id"] == status_id:
                return bool(s.get("terminal"))
        return False

    def status_ids(self) -> list[str]:
        return [s["id"] for s in self.statuses]

    def status(self, status_id: str) -> dict:
        for s in self.statuses:
            if s["id"] == status_id:
                return s
        raise TrackerError(
            f"unknown status {status_id!r}; configured statuses: "
            + ", ".join(self.status_ids())
        )

    def stage_of(self, status_id: str) -> str:
        stage = self.status(status_id).get("stage", "applications")
        if stage not in STAGES:
            raise TrackerError(
                f"status {status_id!r} has stage {stage!r}; "
                f"must be one of {', '.join(STAGES)}"
            )
        return stage

    def is_terminal(self, status_id: str | None) -> bool | None:
        """Is this status closed for good? None when it is not configured at all.

        The `terminal` flag is the answer where it is set; the `rejected` stage
        is the fallback for a status that predates it. Reading the stage alone
        got both ends wrong: a user-defined terminal status filed under
        `processing` -- an accepted/hired column, which nothing forbids -- was
        never treated as closed, and an unconfigured status has no stage at all,
        so it read as open.
        """
        for s in self.statuses:
            if s["id"] == status_id:
                if "terminal" in s:
                    return bool(s["terminal"])
                return s.get("stage") == "rejected"
        return None

    def label(self, kind: str, item_id: str | None) -> str:
        if not item_id:
            return ""
        for item in self._table(kind):
            if item["id"] == item_id:
                labels = item.get("labels", {})
                return labels.get(self.locale) or labels.get("en") or item_id
        return item_id

    def stale_after_days(self) -> int:
        return int(self.data.get("tracker", {}).get("stale_after_days", 30))

    def validate(self, item_id: str | None, kind: str) -> str | None:
        """Reject unknown ids early rather than storing junk."""
        if item_id is None:
            return None
        valid = [i["id"] for i in self._table(kind)]
        if item_id not in valid:
            name = kind[:-1] if kind.endswith("s") else kind
            raise TrackerError(
                f"unknown {name} {item_id!r}; configured: {', '.join(valid)}"
            )
        return item_id


_config: Config | None = None


def load_config(force: bool = False) -> Config:
    global _config
    if _config is not None and not force:
        return _config
    candidates = (
        paths.CONFIG,
        paths.CONFIG_EXAMPLE,
    )
    for candidate in candidates:
        if candidate.exists():
            with candidate.open("rb") as fh:
                _config = Config(tomllib.load(fh), candidate)
            return _config
    raise TrackerError(
        "no configuration found: expected config/config.toml "
        "(copy config/config.example.toml or run /setup)"
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def connect(create: bool = False) -> sqlite3.Connection:
    if not paths.DB.exists() and not create:
        raise TrackerError(
            f"tracker database not found at {rel(paths.DB)}; "
            "run: python tools/tracker.py init"
        )
    paths.DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets the board read while the agent writes.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> tuple[str, list[str]]:
    """Create or upgrade the database. Returns (what happened, migrations run).

    schema.sql describes the current shape and is all a fresh database needs.
    An older database gets there through tools/migrations/*.sql, applied in
    filename order and recorded so they run exactly once.
    """
    fresh = not paths.DB.exists()
    conn = connect(create=True)
    try:
        with conn:
            # Views are derived, so recreating them is free -- and it is the only
            # way a schema.sql change to a view reaches an existing database,
            # since CREATE VIEW IF NOT EXISTS leaves the old definition alone.
            conn.execute("DROP VIEW IF EXISTS applications_view")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SCHEMA_VERSION,),
            )
        applied = apply_migrations(conn, baseline=fresh)
        # Applications recorded before the postings table existed have no
        # posting row; give each one its `applied` row so /scrape's dedup sees
        # the whole history from the first run.
        with conn:
            backfill_postings(conn, load_config())
    finally:
        conn.close()
    return ("created" if fresh else "updated"), applied


def apply_migrations(conn: sqlite3.Connection, baseline: bool = False) -> list[str]:
    """Run pending migrations. With baseline=True, record them without running.

    A database just built from schema.sql already has everything the migrations
    would add, so replaying them would fail on duplicate columns.
    """
    done = {r["name"] for r in conn.execute("SELECT name FROM migrations")}
    pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in done)
    applied: list[str] = []
    for path in pending:
        if baseline:
            with conn:
                conn.execute("INSERT INTO migrations(name) VALUES (?)", (path.name,))
            continue

        # executescript() COMMITs any open transaction before it runs a single
        # statement, so a `with conn:` around it has nothing left to roll back:
        # a migration that failed halfway left its earlier statements committed
        # and no row in `migrations`, and every later run replayed the file and
        # died on a duplicate column. SQLite DDL is transactional, so the script
        # owns its own transaction and records itself inside it -- the columns
        # and the record land together or not at all.
        name = path.name.replace("'", "''")
        script = (
            "BEGIN;\n"
            f"{path.read_text(encoding='utf-8')}\n"
            f"INSERT INTO migrations(name) VALUES ('{name}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except Exception:
            conn.rollback()
            raise
        applied.append(path.name)
    return applied


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(paths.REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


def now() -> str:
    """UTC, to match SQLite's datetime('now') column defaults."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unnamed"


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------


def company_upsert(
    conn: sqlite3.Connection,
    name: str,
    website: str | None = None,
    description: str | None = None,
) -> int:
    slug = slugify(name)
    row = conn.execute("SELECT id FROM companies WHERE slug = ?", (slug,)).fetchone()
    if row:
        # Fill in blanks without clobbering what is already there.
        if website or description:
            conn.execute(
                "UPDATE companies SET website = COALESCE(NULLIF(website,''), ?), "
                "description = COALESCE(NULLIF(description,''), ?) WHERE id = ?",
                (website, description, row["id"]),
            )
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO companies(name, slug, website, description) VALUES (?,?,?,?)",
        (name, slug, website, description),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def resolve(conn: sqlite3.Connection, ident: str) -> sqlite3.Row:
    """Accept a numeric id or a folder slug."""
    if str(ident).isdigit():
        row = conn.execute(
            "SELECT * FROM applications_view WHERE id = ?", (int(ident),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM applications_view WHERE slug = ?", (str(ident),)
        ).fetchone()
    if not row:
        raise TrackerError(f"no application matches {ident!r}")
    return row


def find_applications(
    conn: sqlite3.Connection,
    url: str | None = None,
    company: str | None = None,
    role: str | None = None,
) -> list[sqlite3.Row]:
    """Deduplication lookup used by /apply and /scrape."""
    key = normalize_url(url)
    if key:
        # Compared in Python: the table holds a few hundred rows at most, and a
        # normalised column would be a second copy of the URL to keep in step.
        rows = [
            r
            for r in conn.execute(
                "SELECT * FROM applications_view WHERE url IS NOT NULL AND url != ''"
            ).fetchall()
            if normalize_url(r["url"]) == key
        ]
        if rows:
            return rows
    if company:
        params: list[Any] = [slugify(company)]
        sql = "SELECT * FROM applications_view WHERE company_slug = ?"
        if role:
            sql += " AND lower(role) = lower(?)"
            params.append(role)
        return conn.execute(sql, params).fetchall()
    return []


def parse_deadline(value: str | None) -> str | None:
    """An ISO date, or nothing.

    Postings say "ASAP", "rolling", "until filled" and worse. Storing those as
    text would make the column unsortable and every deadline comparison a lie,
    so anything that is not a real date becomes NULL. The wording, if it
    matters, belongs in the notes.
    """
    if not value:
        return None
    text = value.strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def as_json_list(value) -> str | None:
    """Store strengths/gaps as a JSON array, accepting a list or a string."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value] if value.strip() else []
    items = [str(v).strip() for v in value if str(v).strip()]
    return json.dumps(items, ensure_ascii=False) if items else None


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    slug = base
    n = 2
    while conn.execute("SELECT 1 FROM applications WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def add_application(
    conn: sqlite3.Connection,
    cfg: Config,
    company: str,
    role: str,
    url: str | None = None,
    status: str = "draft",
    work_mode: str | None = None,
    slug: str | None = None,
    website: str | None = None,
    company_description: str | None = None,
    hr_name: str | None = None,
    hr_email: str | None = None,
    office_address: str | None = None,
    location: str | None = None,
    location_verdict: str | None = None,
    deadline: str | None = None,
    source: str | None = None,
    fit_score: int | None = None,
    fit_strengths=None,
    fit_gaps=None,
    posting_text: str | None = None,
    cover_letter_text: str | None = None,
    notes: str | None = None,
    force: bool = False,
) -> sqlite3.Row:
    cfg.status(status)
    cfg.validate(work_mode, "work_modes")
    if location_verdict not in (None, "pass", "fail", "flag"):
        raise TrackerError(
            f"unknown location verdict {location_verdict!r}; expected pass, fail or flag"
        )

    if not force:
        dupes = find_applications(conn, url=url, company=company, role=role)
        if dupes:
            d = dupes[0]
            raise TrackerError(
                f"duplicate: #{d['id']} {d['role']} at {d['company_name']} "
                f"(status {d['status']}, folder {d['slug']}). Update it instead, "
                "or pass --force if this is genuinely a second application."
            )

    company_id = company_upsert(conn, company, website, company_description)
    if slug:
        base = slugify(slug)
    else:
        base = slugify(company)
        taken = conn.execute(
            "SELECT 1 FROM applications WHERE slug = ?", (base,)
        ).fetchone()
        if taken:
            # Second role at the same company: keep the company prefix so the
            # folder still sorts next to the first one.
            base = f"{base}_{slugify(role)}"
    slug = unique_slug(conn, base)

    cur = conn.execute(
        """INSERT INTO applications
           (company_id, role, slug, url, status, work_mode, office_address,
            location, location_verdict, deadline, source,
            fit_score, fit_strengths, fit_gaps,
            hr_name, hr_email, posting_text, cover_letter_text, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            company_id,
            role,
            slug,
            url,
            status,
            work_mode,
            office_address,
            location,
            location_verdict,
            parse_deadline(deadline),
            source,
            fit_score,
            as_json_list(fit_strengths),
            as_json_list(fit_gaps),
            hr_name,
            hr_email,
            posting_text,
            cover_letter_text,
            notes,
        ),
    )
    row = resolve(conn, str(cur.lastrowid))
    link_posting(conn, cfg, row)
    return row


# ---------------------------------------------------------------------------
# Folder lifecycle
# ---------------------------------------------------------------------------


def folder_for(slug: str) -> tuple[Path | None, str | None]:
    """Where the application's documents currently live."""
    for stage in STAGES:
        candidate = paths.stage_dir(stage) / slug
        if candidate.is_dir():
            return candidate, stage
    return None, None


def move_folder(slug: str, target_stage: str) -> str:
    """Move an application folder between stage directories.

    Refuses to overwrite: a name collision is reported, never resolved silently.
    """
    if target_stage not in STAGES:
        raise TrackerError(f"unknown stage {target_stage!r}")
    current, current_stage = folder_for(slug)
    if current is None:
        return f"no folder for {slug} yet (nothing to move)"
    if current_stage == target_stage:
        return f"folder already in {target_stage}/"

    target = paths.stage_dir(target_stage) / slug
    if target.exists():
        raise TrackerError(
            f"cannot move {current_stage}/{slug} -> {target_stage}/{slug}: "
            "target already exists. Resolve it by hand; nothing was changed."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(current), str(target))
    return f"moved {current_stage}/{slug} -> {target_stage}/{slug}"


def set_status(
    conn: sqlite3.Connection,
    cfg: Config,
    ident: str,
    status: str,
    move: bool = True,
    expected_updated_at: str | None = None,
) -> tuple[sqlite3.Row, str]:
    row = resolve(conn, ident)
    cfg.status(status)

    if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
        raise TrackerError(
            "this application changed since you loaded it (now "
            f"{row['updated_at']}, you had {expected_updated_at}). Reload and retry."
        )

    # Move the folder first: if it collides we must not have already claimed
    # the new status in the database.
    note = move_folder(row["slug"], cfg.stage_of(status)) if move else ""
    conn.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
        (status, now(), row["id"]),
    )
    return resolve(conn, str(row["id"])), note


# ---------------------------------------------------------------------------
# Events, attachments, notes
# ---------------------------------------------------------------------------


def set_event_type(
    conn: sqlite3.Connection,
    cfg: Config,
    event_id: int,
    type_: str,
    outcome: str | None = None,
) -> sqlite3.Row:
    """Correct the type of an event that was filed under the wrong one.

    The only edit an event allows, and deliberately so: everything else on an
    event is a record of what happened, and a record is not something to go
    back and change. The type is a classification, and a classification can
    simply be wrong -- an import maps a Notion label it does not recognise to
    `other`, and the row then says nothing about how far the application got.
    """
    cfg.validate(type_, "event_types")
    cfg.validate(outcome, "outcomes")
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise TrackerError(f"no event with id {event_id}")

    if outcome is None:
        conn.execute("UPDATE events SET type = ? WHERE id = ?", (type_, event_id))
    else:
        conn.execute(
            "UPDATE events SET type = ?, outcome = ? WHERE id = ?",
            (type_, outcome, event_id),
        )
    conn.execute(
        "UPDATE applications SET updated_at = ? WHERE id = ?",
        (now(), row["application_id"]),
    )
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def delete_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row:
    """Remove an event that should not be a row at all.

    Not for editing history -- set_event_type() explains why an event is
    otherwise immutable. This is for a row that records nothing that happened:
    the same interview logged twice, once when it was scheduled and again with
    its outcome, or a meeting that moved and left its old slot behind. Until
    now the only way out was to leave it there, which quietly doubles a stage
    in the funnel and puts a date in /triage's way that nobody is waiting for.

    Returns the row it removed, so the caller can show what is gone.
    """
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise TrackerError(f"no event with id {event_id}")
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.execute(
        "UPDATE applications SET updated_at = ? WHERE id = ?",
        (now(), row["application_id"]),
    )
    return row


def deleted_event_report(conn: sqlite3.Connection, cfg: Config, row: dict) -> str:
    """Everything the deleted row held, because after this it is only here."""
    lines = [
        f"deleted event #{row['id']} "
        f"{cfg.label('event_types', row['type'])} on {row['date']}"
    ]
    for field in ("participants", "outcome", "notes"):
        if row.get(field):
            value = (cfg.label("outcomes", row[field])
                     if field == "outcome" else row[field])
            lines.append(f"  {field}: {value}")

    page = row.get("notion_page_id")
    if not page:
        return "\n".join(lines)
    # A page another event still points at is that event's page. That is exactly
    # the case when the row removed was a duplicate the mirror had collapsed
    # onto one page, and telling the user to delete it would cost them the
    # surviving event.
    if conn.execute(
        "SELECT 1 FROM events WHERE notion_page_id = ? LIMIT 1", (page,)
    ).fetchone():
        lines.append(f"  Notion page {page} stays: another event still uses it")
    else:
        lines.append(
            f"  it was mirrored as Notion page {page} -- delete that page too, "
            "or the next `notion_sync.py import` brings the event back"
        )
    return "\n".join(lines)


def add_event(
    conn: sqlite3.Connection,
    cfg: Config,
    ident: str,
    type_: str,
    when: str | None = None,
    participants: str | None = None,
    outcome: str | None = None,
    notes: str | None = None,
) -> sqlite3.Row:
    row = resolve(conn, ident)
    cfg.validate(type_, "event_types")
    cfg.validate(outcome, "outcomes")
    when = (when or datetime.now(timezone.utc).date().isoformat()).strip()
    is_dt = 1 if ("T" in when or " " in when) else 0
    cur = conn.execute(
        """INSERT INTO events
           (application_id, type, date, is_datetime, participants, outcome, notes)
           VALUES (?,?,?,?,?,?,?)""",
        (row["id"], type_, when, is_dt, participants, outcome, notes),
    )
    conn.execute(
        "UPDATE applications SET updated_at = ? WHERE id = ?", (now(), row["id"])
    )
    return conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()


def add_attachment(conn: sqlite3.Connection, ident: str, kind: str, path: str) -> str:
    row = resolve(conn, ident)
    p = Path(path)
    abs_path = p if p.is_absolute() else (paths.REPO / p)
    if not abs_path.exists():
        raise TrackerError(f"file not found: {path}")
    stored = rel(abs_path.resolve())
    conn.execute(
        "INSERT OR IGNORE INTO attachments(application_id, kind, path) VALUES (?,?,?)",
        (row["id"], kind, stored),
    )
    conn.execute(
        "UPDATE applications SET updated_at = ? WHERE id = ?", (now(), row["id"])
    )
    return stored


def set_note(
    conn: sqlite3.Connection, ident: str, text: str, append: bool = False
) -> None:
    row = resolve(conn, ident)
    if append and row["notes"]:
        text = f"{row['notes']}\n{text}"
    conn.execute(
        "UPDATE applications SET notes = ?, updated_at = ? WHERE id = ?",
        (text, now(), row["id"]),
    )


# ---------------------------------------------------------------------------
# Postings: every job posting ever seen
# ---------------------------------------------------------------------------
#
# Scraped, ranked, evaluated, applied to, skipped -- one table, keyed on a
# normalised URL. It is the scraper's dedup source and /apply's memory of what
# was declined and why, so a posting judged once is never judged again.


VERDICT_ORDER = {"strong": 0, "good": 1, "moderate": 2, "weak": 3, "poor": 4}

# Query parameters that identify the click, not the posting. Everything else
# stays: some boards name the posting in a parameter (gh_jid, jobId).
# `position`, `source` and `src` are deliberately absent: boards use them to
# name the posting itself (?position=<id>), and collapsing two postings into one
# url_key loses the second silently.
TRACKING_PARAMS = {
    "ref", "refid", "trk", "trkinfo", "trackingid", "ebp", "pagenum",
    "refresh", "original_referer", "gh_src", "lever-source",
    "lever-origin", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid",
}

POSTING_SCORE_FIELDS = (
    "score", "verdict", "strengths", "gaps", "location_verdict", "language_verdict", "reason",
)


def normalize_url(url: str | None) -> str:
    """One key for one posting, however the link was copied.

    Scheme, `www.`, the fragment, tracking parameters and a trailing slash all
    vary between the scraper's link and the one the user pastes into /apply;
    none of them changes which posting it is.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port not in (80, 443):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
    )
    key = host + path
    if query:
        key += "?" + urlencode(query)
    return key


def posting_key(url: str | None, company: str | None = None, title: str | None = None) -> str:
    key = normalize_url(url)
    if key:
        return key
    if company and title and title.strip():
        return f"{slugify(company)}::{title.strip().lower()}"
    raise TrackerError("a posting needs a URL, or a company and a title")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def posting_by_key(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM postings WHERE url_key = ?", (key,)).fetchone()


def resolve_posting(conn: sqlite3.Connection, ident: str | int) -> sqlite3.Row:
    """Accept a row id or a posting URL."""
    row = None
    if isinstance(ident, int) or str(ident).isdigit():
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (int(ident),)).fetchone()
    else:
        key = normalize_url(str(ident))
        row = posting_by_key(conn, key) if key else None
    if not row:
        raise TrackerError(f"no posting matches {ident!r}")
    return row


def insert_posting(conn: sqlite3.Connection, cfg: Config, p: dict) -> int:
    key = posting_key(p.get("url"), p.get("company"), p.get("title"))
    status = p.get("status") or "new"
    cfg.posting_status(status)
    cur = conn.execute(
        """INSERT INTO postings
           (url, url_key, title, company, location, source, summary, deadline,
            first_seen, status, note, score, verdict, strengths, gaps,
            location_verdict, language_verdict, reason, scored_at,
            application_id, notion_page_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            (p.get("url") or "").strip() or None,
            key,
            p.get("title"),
            p.get("company"),
            p.get("location"),
            p.get("source"),
            p.get("summary"),
            parse_deadline(p.get("deadline")),
            p.get("first_seen") or today(),
            status,
            p.get("note"),
            p.get("score"),
            p.get("verdict"),
            as_json_list(p.get("strengths")),
            as_json_list(p.get("gaps")),
            p.get("location_verdict"),
            p.get("language_verdict"),
            p.get("reason"),
            p.get("scored_at"),
            p.get("application_id"),
            p.get("notion_page_id"),
        ),
    )
    return int(cur.lastrowid)


def upsert_postings(conn: sqlite3.Connection, cfg: Config, postings: Iterable[dict]) -> tuple[int, int]:
    """Record what a scrape found. Returns (added, already known)."""
    added = known = 0
    for p in postings:
        key = posting_key(p.get("url"), p.get("company"), p.get("title"))
        if posting_by_key(conn, key):
            known += 1
            continue
        insert_posting(conn, cfg, p)
        added += 1
    return added, known


def mark_posting(
    conn: sqlite3.Connection,
    cfg: Config,
    ident: str | int,
    status: str,
    note: str | None = None,
    application_id: int | None = None,
    stub: dict | None = None,
    expected_updated_at: str | None = None,
) -> sqlite3.Row:
    """Record a verdict on a posting: skipped with a reason, applied, maybe.

    `ident` is a row id or a URL. A URL nobody has seen yet is recorded on the
    spot when `stub` (title, company, ...) is given -- /apply usually runs on a
    link that was never scraped, and losing the verdict there would lose the
    cases that matter most.
    """
    cfg.posting_status(status)
    if isinstance(ident, int) or str(ident).isdigit():
        row = resolve_posting(conn, ident)
    else:
        key = normalize_url(str(ident))
        if not key:
            raise TrackerError(f"not a URL: {ident!r}")
        row = posting_by_key(conn, key)
        if row is None:
            if stub is None:
                raise TrackerError(
                    f"no posting matches {ident!r}; pass --company and --title to record it"
                )
            pid = insert_posting(
                conn, cfg,
                {**stub, "url": str(ident), "status": status, "note": note,
                 "application_id": application_id},
            )
            return resolve_posting(conn, pid)

    if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
        raise TrackerError(
            "this posting changed since you loaded it (now "
            f"{row['updated_at']}, you had {expected_updated_at}). Reload and retry."
        )
    sets = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, now()]
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if application_id is not None:
        sets.append("application_id = ?")
        params.append(application_id)
    params.append(row["id"])
    conn.execute(f"UPDATE postings SET {', '.join(sets)} WHERE id = ?", params)
    return resolve_posting(conn, int(row["id"]))


def set_posting_note(conn: sqlite3.Connection, ident: str | int, text: str) -> sqlite3.Row:
    row = resolve_posting(conn, ident)
    conn.execute(
        "UPDATE postings SET note = ?, updated_at = ? WHERE id = ?",
        (text or None, now(), row["id"]),
    )
    return resolve_posting(conn, int(row["id"]))


def merge_scores(conn: sqlite3.Connection, cfg: Config, results: Iterable[dict]) -> tuple[int, int]:
    """Write /rank's results back. Returns (merged, ids that matched nothing)."""
    merged = unknown = 0
    for r in results:
        row = None
        ident = r.get("id")
        try:
            if ident is not None and str(ident).strip():
                row = resolve_posting(conn, ident)
        except TrackerError:
            row = None
        if row is None and r.get("url"):
            row = posting_by_key(conn, normalize_url(r["url"]))
        if row is None:
            unknown += 1
            continue
        sets = ["scored_at = ?", "updated_at = ?"]
        params: list[Any] = [today(), now()]
        for field in POSTING_SCORE_FIELDS:
            if r.get(field) is None:
                continue
            value = r[field]
            if field in ("strengths", "gaps"):
                value = as_json_list(value)
            sets.append(f"{field} = ?")
            params.append(value)
        if r.get("deadline"):
            sets.append("deadline = ?")
            params.append(parse_deadline(r["deadline"]))
        if row["status"] == "new":
            cfg.posting_status("ranked")
            sets.append("status = ?")
            params.append("ranked")
        params.append(row["id"])
        conn.execute(f"UPDATE postings SET {', '.join(sets)} WHERE id = ?", params)
        merged += 1
    return merged, unknown


def sweep_postings(conn: sqlite3.Connection, cfg: Config) -> list[str]:
    """Expire open postings whose deadline has passed. Returns what it expired."""
    cfg.posting_status("expired")
    open_ids = [s["id"] for s in cfg.posting_statuses if not s.get("terminal")]
    if not open_ids:
        return []
    rows = conn.execute(
        "SELECT id, company, title FROM postings WHERE deadline IS NOT NULL "
        f"AND deadline < ? AND status IN ({','.join('?' * len(open_ids))}) ORDER BY id",
        [today(), *open_ids],
    ).fetchall()
    for r in rows:
        conn.execute(
            "UPDATE postings SET status = 'expired', updated_at = ? WHERE id = ?",
            (now(), r["id"]),
        )
    return [f"{r['company']} - {r['title']}" for r in rows]


def posting_sort_key(e: dict):
    # Highest score first; unscored last, since they still need a decision.
    score = e.get("score")
    return (
        0 if score is not None else 1,
        -(score or 0),
        VERDICT_ORDER.get(e.get("verdict") or "", 9),
        e.get("company") or "",
        e.get("id") or 0,
    )


def enrich_posting(row: dict, cfg: Config) -> dict:
    for key in ("strengths", "gaps"):
        raw = row.get(key)
        try:
            row[key] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            row[key] = [raw] if raw else []
    row["status_label"] = cfg.label("posting_statuses", row.get("status"))
    row["terminal"] = cfg.posting_is_terminal(row.get("status"))
    row["is_expired"] = bool(row.get("deadline")) and row["deadline"] < today()
    return row


def list_postings(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    status: str | None = None,
    all: bool = False,
    unscored: bool = False,
    min_score: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    """The shortlist: open postings by default, everything with all=True."""
    sql = (
        "SELECT p.*, a.slug AS application_slug FROM postings p "
        "LEFT JOIN applications a ON a.id = p.application_id"
    )
    where: list[str] = []
    params: list[Any] = []
    if status:
        cfg.posting_status(status)
        where.append("p.status = ?")
        params.append(status)
    elif not all:
        terminal = [s["id"] for s in cfg.posting_statuses if s.get("terminal")]
        if terminal:
            where.append(f"p.status NOT IN ({','.join('?' * len(terminal))})")
            params.extend(terminal)
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = [enrich_posting(dict(r), cfg) for r in conn.execute(sql, params).fetchall()]
    if unscored:
        rows = [r for r in rows if r.get("score") is None]
    if min_score is not None:
        rows = [r for r in rows if (r.get("score") or 0) >= min_score]
    rows.sort(key=posting_sort_key)
    if limit:
        rows = rows[:limit]
    return rows


def postings_table_data(conn: sqlite3.Connection, cfg: Config) -> dict:
    """Rows plus the status options, for the browser table. Used by tools/board.py."""
    rows = list_postings(conn, cfg, all=True)
    return {
        "locale": cfg.locale,
        "statuses": [
            {
                "id": s["id"],
                "label": cfg.label("posting_statuses", s["id"]),
                "terminal": bool(s.get("terminal")),
            }
            for s in cfg.posting_statuses
        ],
        "rows": rows,
        "total": len(rows),
    }


def link_posting(
    conn: sqlite3.Connection, cfg: Config, app: sqlite3.Row | dict, first_seen: str | None = None
) -> int | None:
    """An application exists, so the posting it came from is `applied`.

    Creates the posting row when nothing scraped it first, so the dedup table is
    complete whichever way a posting arrived.
    """
    try:
        key = posting_key(app["url"], app["company_name"], app["role"])
    except TrackerError:
        return None
    cfg.posting_status("applied")
    row = posting_by_key(conn, key)
    if row is None:
        return insert_posting(
            conn, cfg,
            {
                "url": app["url"],
                "title": app["role"],
                "company": app["company_name"],
                "location": app["location"],
                "source": app["source"] or "apply",
                "deadline": app["deadline"],
                "first_seen": first_seen or today(),
                "status": "applied",
                "application_id": app["id"],
            },
        )
    if row["status"] != "applied" or row["application_id"] != app["id"]:
        conn.execute(
            "UPDATE postings SET status = 'applied', application_id = ?, updated_at = ? "
            "WHERE id = ?",
            (app["id"], now(), row["id"]),
        )
    return int(row["id"])


def backfill_postings(conn: sqlite3.Connection, cfg: Config) -> int:
    """One `applied` posting per application that has none yet. Idempotent."""
    rows = conn.execute(
        "SELECT * FROM applications_view WHERE id NOT IN "
        "(SELECT application_id FROM postings WHERE application_id IS NOT NULL) "
        "ORDER BY id"
    ).fetchall()
    for r in rows:
        link_posting(conn, cfg, r, first_seen=(r["created_at"] or "")[:10] or None)
    return len(rows)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_applications(
    conn: sqlite3.Connection,
    cfg: Config,
    status: str | None = None,
    stage: str | None = None,
    company: str | None = None,
    stale: bool = False,
    expired: bool = False,
) -> list[dict]:
    sql = "SELECT * FROM applications_view"
    where: list[str] = []
    params: list[Any] = []
    if status:
        cfg.status(status)
        where.append("status = ?")
        params.append(status)
    if stage:
        if stage not in STAGES:
            raise TrackerError(
                f"unknown stage {stage!r}; expected one of {', '.join(STAGES)}"
            )
        ids = [s["id"] for s in cfg.statuses if s.get("stage") == stage]
        if not ids:
            return []
        where.append(f"status IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if company:
        where.append("company_slug = ?")
        params.append(slugify(company))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(last_event_date, created_at) DESC, id DESC"

    rows = [enrich(dict(r), cfg) for r in conn.execute(sql, params).fetchall()]

    if stale:
        today = datetime.now(timezone.utc).date()
        cutoff = (today - timedelta(days=cfg.stale_after_days())).isoformat()
        # Anything still open. Restricting this to the `applications` stage hid
        # every status under `processing` -- screening, assignment, interview,
        # final, offer -- so an interview process that went quiet could not
        # reach /triage's "Silent" section, which reads only from here. That is
        # the silence worth chasing, and the one case the sweep structurally
        # could not see.
        #
        # Open means cfg.is_terminal() says so: False, not None. A status that
        # is not in the config is an orphan, and the only thing /triage offers
        # a silent row is to close it -- which would be guessing about a row we
        # could not even read. /triage lists those separately instead.
        rows = [
            r
            for r in rows
            if cfg.is_terminal(r["status"]) is False
            and (r["last_event_date"] or r["created_at"])[:10] < cutoff
        ]
    if expired:
        # Only worth surfacing while the application is still open; a deadline
        # that passed after a rejection is not news.
        rows = [
            r for r in rows
            if r["is_expired"] and cfg.is_terminal(r["status"]) is False
        ]
    return rows


def enrich(row: dict, cfg: Config) -> dict:
    row["status_label"] = cfg.label("statuses", row.get("status"))
    row["work_mode_label"] = cfg.label("work_modes", row.get("work_mode"))
    for key in ("fit_strengths", "fit_gaps"):
        raw = row.get(key)
        try:
            row[key] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            row[key] = [raw] if raw else []
    try:
        row["stage"] = cfg.stage_of(row["status"]) if row.get("status") else None
    except TrackerError:
        row["stage"] = None  # status not in config; surfaced as an orphan
    folder, folder_stage = folder_for(row["slug"])
    row["folder"] = rel(folder) if folder else None
    row["folder_stage"] = folder_stage
    row["folder_in_sync"] = (folder_stage == row["stage"]) if folder_stage else None
    return row


def application_detail(conn: sqlite3.Connection, cfg: Config, ident: str) -> dict:
    row = enrich(dict(resolve(conn, ident)), cfg)
    events = []
    for e in conn.execute(
        "SELECT * FROM events WHERE application_id = ? ORDER BY date, id", (row["id"],)
    ):
        ev = dict(e)
        ev["type_label"] = cfg.label("event_types", ev["type"])
        ev["outcome_label"] = cfg.label("outcomes", ev["outcome"])
        events.append(ev)
    row["events"] = events
    row["attachments"] = [
        dict(a)
        for a in conn.execute(
            "SELECT * FROM attachments WHERE application_id = ? ORDER BY kind",
            (row["id"],),
        )
    ]
    return row


def board_data(conn: sqlite3.Connection, cfg: Config) -> dict:
    """Columns + cards, in funnel order. Used by tools/board.py."""
    rows = list_applications(conn, cfg)
    known = {s["id"] for s in cfg.statuses}
    by_status: dict[str, list[dict]] = {s["id"]: [] for s in cfg.statuses}
    orphans: list[dict] = []
    for r in rows:
        if r["status"] in known:
            by_status[r["status"]].append(r)
        else:
            orphans.append(r)
    return {
        "locale": cfg.locale,
        "stale_after_days": cfg.stale_after_days(),
        "columns": [
            {
                "id": s["id"],
                "label": cfg.label("statuses", s["id"]),
                "stage": s.get("stage", "applications"),
                "terminal": bool(s.get("terminal")),
                "cards": by_status.get(s["id"], []),
            }
            for s in cfg.statuses
        ],
        "orphans": orphans,
        "event_types": [
            {"id": e["id"], "label": cfg.label("event_types", e["id"])}
            for e in cfg.event_types
        ],
        "outcomes": [
            {"id": o["id"], "label": cfg.label("outcomes", o["id"])}
            for o in cfg.outcomes
        ],
        "total": len(rows),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_table(rows: Iterable[dict]) -> str:
    rows = list(rows)
    if not rows:
        return "(no applications)"
    headers = ["#", "Company", "Role", "Status", "Last activity", "Folder"]
    data = [
        [
            str(r["id"]),
            r["company_name"],
            r["role"][:44],
            r["status_label"],
            (r["last_event_date"] or r["created_at"])[:10],
            (r["folder"] or "-")
            + ("" if r["folder_in_sync"] is not False else "  << out of sync"),
        ]
        for r in rows
    ]
    widths = [max(len(h), *(len(d[i]) for d in data)) for i, h in enumerate(headers)]
    out = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip(),
        "  ".join("-" * w for w in widths).rstrip(),
    ]
    for d in data:
        out.append("  ".join(c.ljust(w) for c, w in zip(d, widths)).rstrip())
    return "\n".join(out)


def render_board(conn: sqlite3.Connection, cfg: Config) -> str:
    data = board_data(conn, cfg)
    out = [f"# Application board ({data['total']} total)", ""]
    for col in data["columns"]:
        if not col["cards"]:
            continue
        out.append(f"## {col['label']}  ({len(col['cards'])})")
        for c in col["cards"]:
            when = (c["last_event_date"] or c["created_at"])[:10]
            flag = "" if c["folder_in_sync"] is not False else "  **folder out of sync**"
            out.append(f"- **{c['company_name']}** - {c['role']}  _{when}_{flag}")
        out.append("")
    if data["orphans"]:
        out.append("## Unknown status (not in config)")
        for c in data["orphans"]:
            out.append(f"- {c['company_name']} - {c['role']}  (status `{c['status']}`)")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def emit(args: argparse.Namespace, payload: Any, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tracker", description="CareerForge application tracker"
    )
    sub = p.add_subparsers(dest="command", required=True)

    def with_json(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        return sp

    sub.add_parser("init", help="create or upgrade the tracker database")

    a = with_json(sub.add_parser("add", help="record a new application"))
    a.add_argument("--company", required=True)
    a.add_argument("--role", required=True)
    a.add_argument("--url")
    a.add_argument("--status", default="draft")
    a.add_argument("--work-mode", dest="work_mode")
    a.add_argument("--location", help="the place as the posting states it")
    a.add_argument("--location-verdict", dest="location_verdict",
                   choices=["pass", "fail", "flag"])
    a.add_argument("--deadline", help="ISO date; free text like 'ASAP' is stored as none")
    a.add_argument("--source", help="which board or channel this came from")
    a.add_argument("--fit-score", dest="fit_score", type=int)
    a.add_argument("--fit-strengths", dest="fit_strengths", nargs="*")
    a.add_argument("--fit-gaps", dest="fit_gaps", nargs="*")
    a.add_argument("--slug", help="folder name; defaults to the company slug")
    a.add_argument("--website")
    a.add_argument("--company-description", dest="company_description")
    a.add_argument("--hr-name", dest="hr_name")
    a.add_argument("--hr-email", dest="hr_email")
    a.add_argument("--office-address", dest="office_address")
    a.add_argument("--posting-file", help="file whose text is the posting snapshot")
    a.add_argument("--cover-file", help="file whose text is the cover letter")
    a.add_argument("--notes")
    a.add_argument("--force", action="store_true", help="skip the duplicate check")

    f = with_json(sub.add_parser("find", help="duplicate lookup by URL or company+role"))
    f.add_argument("--url")
    f.add_argument("--company")
    f.add_argument("--role")

    ls = with_json(sub.add_parser("list", help="list applications"))
    ls.add_argument("--status")
    ls.add_argument("--stage", choices=STAGES)
    ls.add_argument("--company")
    ls.add_argument(
        "--stale", action="store_true", help="open and silent past the configured cutoff"
    )
    ls.add_argument(
        "--expired", action="store_true", help="deadline has passed and the status is still open"
    )

    sh = with_json(sub.add_parser("show", help="full detail for one application"))
    sh.add_argument("application")

    ss = with_json(sub.add_parser("set-status", help="change status and move the folder"))
    ss.add_argument("application")
    ss.add_argument("status")
    ss.add_argument("--no-move", action="store_true", help="do not move the folder")

    ev = sub.add_parser("event", help="funnel events")
    evsub = ev.add_subparsers(dest="event_command", required=True)
    eva = with_json(evsub.add_parser("add", help="record an event"))
    eva.add_argument("application")
    eva.add_argument("--type", dest="type_", required=True)
    eva.add_argument("--date", dest="when", help="ISO-8601; defaults to today")
    eva.add_argument("--participants")
    eva.add_argument("--outcome")
    eva.add_argument("--notes")

    evs = with_json(evsub.add_parser(
        "set-type", help="correct the type an event was filed under"))
    evs.add_argument("event_id", type=int, help="from `show <application>`")
    evs.add_argument("--type", dest="type_", required=True)
    evs.add_argument("--outcome", help="also correct the outcome")

    evd = with_json(evsub.add_parser(
        "delete", help="remove an event that records nothing that happened"))
    evd.add_argument("event_id", type=int, help="from `show <application>`")

    at = with_json(sub.add_parser("attach", help="record a built document"))
    at.add_argument("application")
    at.add_argument("--kind", default="cv", choices=["cv", "cover", "other"])
    at.add_argument("--path", required=True)

    nt = sub.add_parser("note", help="set or append notes")
    nt.add_argument("application")
    nt.add_argument("--text", required=True)
    nt.add_argument("--append", action="store_true")

    r = sub.add_parser("report", help="print a summary")
    r.add_argument("--board", action="store_true", help="markdown kanban, not a table")

    with_json(sub.add_parser("export", help="dump everything as JSON"))
    with_json(sub.add_parser("statuses", help="list configured statuses and stages"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "init":
        what, migrations = init_db()
        cfg = load_config()
        print(f"tracker database {what}: {rel(paths.DB)}")
        for name in migrations:
            print(f"  migration applied: {name}")
        print(
            f"configuration: {rel(cfg.source)} "
            f"(locale {cfg.locale}, {len(cfg.statuses)} statuses)"
        )
        return 0

    cfg = load_config()
    conn = connect()
    try:
        if args.command == "add":
            posting = (
                Path(args.posting_file).read_text(encoding="utf-8")
                if args.posting_file
                else None
            )
            cover = (
                Path(args.cover_file).read_text(encoding="utf-8")
                if args.cover_file
                else None
            )
            with conn:
                row = add_application(
                    conn,
                    cfg,
                    company=args.company,
                    role=args.role,
                    url=args.url,
                    status=args.status,
                    work_mode=args.work_mode,
                    slug=args.slug,
                    website=args.website,
                    company_description=args.company_description,
                    hr_name=args.hr_name,
                    hr_email=args.hr_email,
                    office_address=args.office_address,
                    location=args.location,
                    location_verdict=args.location_verdict,
                    deadline=args.deadline,
                    source=args.source,
                    fit_score=args.fit_score,
                    fit_strengths=args.fit_strengths,
                    fit_gaps=args.fit_gaps,
                    posting_text=posting,
                    cover_letter_text=cover,
                    notes=args.notes,
                    force=args.force,
                )
            d = enrich(dict(row), cfg)
            emit(
                args,
                d,
                f"#{d['id']} {d['role']} at {d['company_name']} "
                f"[{d['status_label']}] -> folder slug '{d['slug']}'",
            )

        elif args.command == "find":
            rows = [
                enrich(dict(r), cfg)
                for r in find_applications(
                    conn, url=args.url, company=args.company, role=args.role
                )
            ]
            emit(args, rows, render_table(rows))

        elif args.command == "list":
            rows = list_applications(
                conn,
                cfg,
                status=args.status,
                stage=args.stage,
                company=args.company,
                stale=args.stale,
                expired=args.expired,
            )
            emit(args, rows, render_table(rows))

        elif args.command == "show":
            d = application_detail(conn, cfg, args.application)
            if args.json:
                emit(args, d, "")
            else:
                print(f"#{d['id']} {d['role']} at {d['company_name']}")
                print(f"  status   {d['status_label']} ({d['status']})")
                print(f"  url      {d['url'] or '-'}")
                print(f"  folder   {d['folder'] or '-'}")
                print(f"  mode     {d['work_mode_label'] or '-'}")
                place = d["location"] or "-"
                if d["location_verdict"]:
                    place += f"  [{d['location_verdict']}]"
                print(f"  location {place}")
                if d["deadline"]:
                    print(f"  deadline {d['deadline']}"
                          + ("  (expired)" if d["is_expired"] else ""))
                if d["source"]:
                    print(f"  source   {d['source']}")
                if d["fit_score"] is not None:
                    print(f"  fit      {d['fit_score']}/100")
                for item in d["fit_strengths"]:
                    print(f"    + {item}")
                for item in d["fit_gaps"]:
                    print(f"    - {item}")
                if d["events"]:
                    print("  events:")
                    for e in d["events"]:
                        # The id is here so `event set-type` has something to
                        # name; it is the only handle an event has.
                        print(
                            f"    #{e['id']:<4} {e['date'][:16]}  "
                            f"{e['type_label']}  {e['outcome_label']}"
                        )
                if d["attachments"]:
                    print("  files:")
                    for at in d["attachments"]:
                        print(f"    {at['kind']}: {at['path']}")

        elif args.command == "set-status":
            with conn:
                row, note = set_status(
                    conn, cfg, args.application, args.status, move=not args.no_move
                )
            d = enrich(dict(row), cfg)
            emit(
                args,
                {"application": d, "folder": note},
                f"#{d['id']} {d['company_name']} -> {d['status_label']}"
                + (f"\n{note}" if note else ""),
            )

        elif args.command == "event":
            if args.event_command == "delete":
                with conn:
                    e = delete_event(conn, args.event_id)
                d = dict(e)
                emit(args, d, deleted_event_report(conn, cfg, d))
            else:
                if args.event_command == "set-type":
                    with conn:
                        e = set_event_type(
                            conn, cfg, args.event_id, args.type_, args.outcome
                        )
                else:
                    with conn:
                        e = add_event(
                            conn,
                            cfg,
                            args.application,
                            args.type_,
                            args.when,
                            args.participants,
                            args.outcome,
                            args.notes,
                        )
                d = dict(e)
                emit(
                    args,
                    d,
                    f"event #{d['id']} "
                    f"{cfg.label('event_types', d['type'])} on {d['date']}",
                )

        elif args.command == "attach":
            with conn:
                stored = add_attachment(conn, args.application, args.kind, args.path)
            emit(
                args,
                {"kind": args.kind, "path": stored},
                f"attached {args.kind}: {stored}",
            )

        elif args.command == "note":
            with conn:
                set_note(conn, args.application, args.text, args.append)
            print("note saved")

        elif args.command == "report":
            if args.board:
                print(render_board(conn, cfg))
            else:
                print(render_table(list_applications(conn, cfg)))

        elif args.command == "export":
            payload = {
                "schema_version": SCHEMA_VERSION,
                "exported_at": now(),
                "companies": [
                    dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY id")
                ],
                "applications": [
                    application_detail(conn, cfg, str(r["id"]))
                    for r in conn.execute("SELECT id FROM applications ORDER BY id")
                ],
                "postings": list_postings(conn, cfg, all=True),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))

        elif args.command == "statuses":
            rows = [
                {
                    "id": s["id"],
                    "stage": s.get("stage"),
                    "label": cfg.label("statuses", s["id"]),
                }
                for s in cfg.statuses
            ]
            emit(
                args,
                rows,
                "\n".join(
                    f"{r['id']:<12} {str(r['stage']):<14} {r['label']}" for r in rows
                ),
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    from console import use_utf8
    use_utf8()
    try:
        sys.exit(main())
    except TrackerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
