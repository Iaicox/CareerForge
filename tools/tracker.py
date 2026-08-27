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

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "tracker" / "careerforge.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SCHEMA_VERSION = "3"
STAGES = ("applications", "processing", "rejected")


class TrackerError(Exception):
    """Anything the user should see as a plain message, not a traceback."""


_dotenv_loaded = False


def load_dotenv() -> None:
    """Load REPO/.env into os.environ, once, without overriding anything.

    The standard library does not read .env files, and the secrets consumers
    (Gemini key, Notion token, mail password) all live there now. Real
    environment variables always win over the file: a value set for one shell
    session must not be silently shadowed by an old line in .env.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    path = REPO / ".env"
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
        return list(self.data.get(key, []))

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
        REPO / "config" / "config.toml",
        REPO / "config" / "config.example.toml",
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
    if not DB_PATH.exists() and not create:
        raise TrackerError(
            f"tracker database not found at {rel(DB_PATH)}; "
            "run: python tools/tracker.py init"
        )
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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
    fresh = not DB_PATH.exists()
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
        return str(path.relative_to(REPO)).replace("\\", "/")
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
    if url:
        rows = conn.execute(
            "SELECT * FROM applications_view WHERE url = ?", (url.strip(),)
        ).fetchall()
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
    return resolve(conn, str(cur.lastrowid))


# ---------------------------------------------------------------------------
# Folder lifecycle
# ---------------------------------------------------------------------------


def folder_for(slug: str) -> tuple[Path | None, str | None]:
    """Where the application's documents currently live."""
    for stage in STAGES:
        candidate = REPO / stage / slug
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

    target = REPO / target_stage / slug
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
    abs_path = p if p.is_absolute() else (REPO / p)
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
        print(f"tracker database {what}: {rel(DB_PATH)}")
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
                f"event #{d['id']} {cfg.label('event_types', d['type'])} on {d['date']}",
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
