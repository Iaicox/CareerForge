#!/usr/bin/env python3
"""Optional Notion mirror for the CareerForge tracker.

SQLite is the source of truth. Notion is a mirror you can look at on your
phone. Nothing in CareerForge requires it.

    python tools/notion_sync.py provision --parent-page <page-id-or-url>
    python tools/notion_sync.py import --dry-run
    python tools/notion_sync.py import
    python tools/notion_sync.py push [--slug acme] [--files] [--no-events]

Auth: NOTION_KEY in .env, or NOTION_TOKEN in the environment, or a
.notion_token file in the repo root (all gitignored). Create the integration
at notion.so/my-integrations with Read, Update and Insert content, then share
the parent page with it.

Database ids live in data/state/notion.json (gitignored), written by `provision`
or by hand. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tracker  # noqa: E402
import paths  # noqa: E402
from tracker import TrackerError  # noqa: E402

API = "https://api.notion.com/v1"
API_VERSION = "2025-09-03"
PACE_SECONDS = 0.35  # Notion averages 3 requests/second
MAX_UPLOAD = 20 * 1024 * 1024

# Property names in the mirrored databases. Change them here and in an existing
# workspace together, or provision a fresh one.
P_ROLE = "Role"
P_COMPANY = "Company"
P_STATUS = "Status"
P_URL = "Posting URL"
P_MODE = "Work mode"
P_HR = "Contact"
P_HR_EMAIL = "Contact email"
P_CV = "CV"
P_COVER = "Cover letter"

# The Postings database: every posting seen, mirrored from the `postings` table.
PP_NAME = "Name"
PP_COMPANY = "Company"
PP_URL = "URL"
PP_STATUS = "Status"
PP_SCORE = "Score"
PP_VERDICT = "Verdict"
PP_DEADLINE = "Deadline"
PP_SOURCE = "Source"
PP_FIRST_SEEN = "First seen"
PP_NOTE = "Note"
PP_APPLICATION = "Application"

# What a hand-kept database may already call these, for `adopt`. The title,
# the one url and the one select are claimed by type first; these by name.
ADOPT_ALIASES = {
    PP_COMPANY: ("Company", "Компания"),
    PP_NOTE: ("Note", "Notes", "Ньюансы", "Нюансы", "Заметки", "Комментарий"),
    PP_SOURCE: ("Source", "Источник"),
    PP_SCORE: ("Score", "Оценка"),
    PP_DEADLINE: ("Deadline", "Дедлайн"),
    PP_FIRST_SEEN: ("First seen", "Найдена"),
}

_last_call = 0.0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def token() -> str:
    tracker.load_dotenv()
    # NOTION_KEY is the name the user's .env uses; NOTION_TOKEN is kept for
    # compatibility, and the legacy token file still works as a last resort.
    for name in ("NOTION_KEY", "NOTION_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    path = paths.REPO / ".notion_token"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise TrackerError(
        "no Notion token. Put NOTION_KEY=<token> in .env (or set NOTION_TOKEN). "
        "Create one at notion.so/my-integrations (Read + Update + Insert "
        "content), then share the tracker page with it."
    )


def pace() -> None:
    global _last_call
    wait = PACE_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def request(
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    raw_url: str | None = None,
    retries: int = 4,
) -> dict:
    url = raw_url or f"{API}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token()}",
        "Notion-Version": API_VERSION,
        "Content-Type": "application/json",
    }
    for attempt in range(retries + 1):
        pace()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code in (429, 502, 503, 529) and attempt < retries:
                delay = float(exc.headers.get("Retry-After") or (2 ** attempt))
                time.sleep(min(delay, 30))
                continue
            raise TrackerError(f"Notion {method} {path} -> HTTP {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise TrackerError(f"Notion {method} {path} -> {exc.reason}")
    raise TrackerError("unreachable")


def paginate(path: str, payload: dict) -> Iterable[dict]:
    cursor = None
    while True:
        body = dict(payload)
        if cursor:
            body["start_cursor"] = cursor
        data = request("POST", path, body)
        yield from data.get("results", [])
        if not data.get("has_more"):
            return
        cursor = data.get("next_cursor")


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


# A dashed id is matched in full 8-4-4-4-12 shape rather than as "36 characters
# of hex and dashes": the loose form happily matched a run spanning a title
# slug, e.g. "e-Cafe-1234567890abcdef1234567890abc" out of ".../My-Page-Name-
# Cafe-<id>", swallowing part of the real id on its way past.
DASHED_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
BARE_ID_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")


def normalise_id(value: str) -> str:
    """Accept a bare id, a dashed id, or any Notion URL containing one.

    A Notion database URL carries the view id in the query --
    .../Job-Tracker-<page id>?v=<view id> -- and taking the last id in the whole
    string returned that one, so `provision --parent-page` created the three
    databases under a parent that is not a page, or failed with an opaque 404.

    But the query is not all noise. A page opened as a side peek from a database
    reads .../<db slug>-<db id>?v=<view>&p=<page id>&pm=s: there the path holds
    the *database*, and the only page id in the URL is `p=`. So `p=` is read
    first, and only when it is absent does the path decide.
    """
    parts = urllib.parse.urlsplit(value or "")
    page_param = urllib.parse.parse_qs(parts.query).get("p", [""])[0]
    text = page_param or parts.path
    for candidate in DASHED_ID_RE.findall(text) + BARE_ID_RE.findall(text):
        try:
            return str(uuid.UUID(candidate.replace("-", "")))
        except ValueError:
            continue
    raise TrackerError(f"could not find a Notion id in {value!r}")


def load_ids() -> dict:
    if not paths.NOTION_IDS.exists():
        raise TrackerError(
            f"{tracker.rel(paths.NOTION_IDS)} not found. Run: "
            "python tools/notion_sync.py provision --parent-page <page url>"
        )
    return json.loads(paths.NOTION_IDS.read_text(encoding="utf-8"))


def save_ids(ids: dict) -> None:
    paths.NOTION_IDS.parent.mkdir(parents=True, exist_ok=True)
    paths.NOTION_IDS.write_text(
        json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def query_source(entry: dict) -> Iterable[dict]:
    """Query a database, whichever API shape this workspace exposes."""
    if entry.get("data_source_id"):
        try:
            yield from paginate(f"/data_sources/{entry['data_source_id']}/query", {})
            return
        except TrackerError:
            pass  # fall back to the classic endpoint below
    yield from paginate(f"/databases/{entry['database_id']}/query", {})


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def rich_text(value: str | None) -> list[dict]:
    if not value:
        return []
    # Notion rejects rich_text items over 2000 characters.
    return [
        {"type": "text", "text": {"content": value[i : i + 2000]}}
        for i in range(0, min(len(value), 2000 * 100), 2000)
    ]


def plain(prop: dict | None) -> str:
    if not prop:
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in prop.get(kind, []))
    if kind == "url":
        return prop.get("url") or ""
    if kind == "email":
        return prop.get("email") or ""
    if kind == "select":
        return (prop.get("select") or {}).get("name") or ""
    if kind == "status":
        return (prop.get("status") or {}).get("name") or ""
    if kind == "date":
        return (prop.get("date") or {}).get("start") or ""
    return ""


def number_of(prop: dict | None) -> float | None:
    if not prop or prop.get("type") != "number":
        return None
    return prop.get("number")


def first_relation(prop: dict | None) -> str | None:
    if not prop or prop.get("type") != "relation":
        return None
    rel = prop.get("relation") or []
    return rel[0]["id"] if rel else None


def fold_label(value: str) -> str:
    """A label without its leading emoji or punctuation, case-folded.

    Config labels carry an emoji ("📞 Скрининг"); a select typed by hand in
    Notion usually does not ("Скрининг"). They are the same status.
    """
    return re.sub(r"^[^\w]+", "", (value or "").strip()).casefold()


def match_by_label(cfg: tracker.Config, kind: str, label: str) -> str | None:
    """Map a Notion select label back to a configured id."""
    if not label:
        return None
    label = label.strip()
    wanted = fold_label(label)
    for item in cfg.items(kind):
        if item["id"] == label:
            return item["id"]
        for value in (item.get("labels") or {}).values():
            if value.strip() == label or fold_label(value) == wanted:
                return item["id"]
    return None


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


def status_options(cfg: tracker.Config, kind: str) -> list[dict]:
    return [{"name": cfg.label(kind, item["id"])} for item in cfg.items(kind)]


def relation_to(entry: dict) -> dict:
    """A relation's target, in whichever API shape this workspace exposes.

    The data_sources endpoint wants a data_source_id and rejects a
    database_id; the classic endpoint is the other way round.
    """
    if entry.get("data_source_id"):
        return {"data_source_id": entry["data_source_id"], "single_property": {}}
    return {"database_id": entry["database_id"], "single_property": {}}


def postings_props(cfg: tracker.Config, applications: dict | None = None) -> dict:
    """The Postings database schema; the relation only once Applications exists.

    `applications` is its notion.json entry ({database_id, data_source_id}).
    """
    props: dict[str, Any] = {
        PP_NAME: {"title": {}},
        PP_COMPANY: {"rich_text": {}},
        PP_URL: {"url": {}},
        PP_STATUS: {"select": {"options": status_options(cfg, "posting_statuses")}},
        PP_SCORE: {"number": {}},
        PP_VERDICT: {"select": {"options": [{"name": v} for v in tracker.VERDICT_ORDER]}},
        PP_DEADLINE: {"date": {}},
        PP_SOURCE: {"rich_text": {}},
        PP_FIRST_SEEN: {"date": {}},
        PP_NOTE: {"rich_text": {}},
    }
    if applications:
        props[PP_APPLICATION] = {"relation": relation_to(applications)}
    return props


# The select properties provision() fills from config, as
# (notion.json key, Notion property name, config table).
CONFIG_SELECTS = (
    ("applications", P_STATUS, "statuses"),
    ("applications", P_MODE, "work_modes"),
    ("events", "Type", "event_types"),
    ("events", "Outcome", "outcomes"),
    ("postings", PP_STATUS, "posting_statuses"),
)


def schema_path(entry: dict) -> str:
    """Where a database's own schema lives, in whichever API shape this has."""
    if entry.get("data_source_id"):
        return f"/data_sources/{entry['data_source_id']}"
    return f"/databases/{entry['database_id']}"


def do_sync_options(dry_run: bool) -> int:
    """Bring the mirror's select options back in step with config.

    provision() writes them once. Add a status, event type, work mode or
    outcome to data/config/config.toml afterwards and Notion never hears about it:
    the select keeps the options it was created with, and the new value has
    nowhere to land.

    Only ever adds. An option Notion has and config does not is reported and
    left alone -- a page may be using it, and dropping the option would clear
    that page's value with nothing to say so.
    """
    cfg = tracker.load_config()
    ids = load_ids()
    added = 0
    for key, prop, table in CONFIG_SELECTS:
        entry = ids.get(key)
        if not entry:
            continue
        path = schema_path(entry)
        current = request("GET", path).get("properties", {}).get(prop)
        if not current or current.get("type") != "select":
            print(f"{prop}: not a select in Notion, skipped")
            continue

        options = current["select"]["options"]
        have = {o["name"] for o in options}
        want = [o["name"] for o in status_options(cfg, table)]
        missing = [name for name in want if name not in have]
        extra = sorted(have - set(want))

        if missing:
            added += len(missing)
            verb = "would add" if dry_run else "adding"
            print(f"{prop}: {verb} {', '.join(missing)}")
            if not dry_run:
                request("PATCH", path, {"properties": {prop: {"select": {
                    # The existing options go back verbatim, ids and all, or
                    # Notion reads their absence as a request to remove them.
                    "options": options + [{"name": n} for n in missing],
                }}}})
        if extra:
            print(f"{prop}: in Notion but not in config, left alone: "
                  f"{', '.join(extra)}")
        if not missing and not extra:
            print(f"{prop}: in step")

    verb = "would add" if dry_run else "added"
    print(f"{verb}: {added} option(s)")
    return 0


def provision(parent_page: str, dry_run: bool) -> dict:
    cfg = tracker.load_config()
    parent = {"type": "page_id", "page_id": normalise_id(parent_page)}

    companies_props = {
        "Name": {"title": {}},
        "What they do": {"rich_text": {}},
        "Website": {"url": {}},
    }
    applications_props = {
        P_ROLE: {"title": {}},
        P_STATUS: {"select": {"options": status_options(cfg, "statuses")}},
        P_URL: {"url": {}},
        P_MODE: {"select": {"options": status_options(cfg, "work_modes")}},
        P_HR: {"rich_text": {}},
        P_HR_EMAIL: {"email": {}},
        P_CV: {"files": {}},
        P_COVER: {"files": {}},
    }
    events_props = {
        "Name": {"title": {}},
        "Type": {"select": {"options": status_options(cfg, "event_types")}},
        "Date": {"date": {}},
        "Participants": {"rich_text": {}},
        "Outcome": {"select": {"options": status_options(cfg, "outcomes")}},
    }

    plan = [
        ("companies", "Companies", companies_props),
        ("applications", "Applications", applications_props),
        ("events", "Events", events_props),
        ("postings", "Postings", postings_props(cfg)),
    ]
    if dry_run:
        print("would create under page", parent["page_id"])
        for key, title, props in plan:
            print(f"  {title}: {', '.join(props)}")
        print(f"  then link {P_COMPANY} (Applications -> Companies), "
              f"Application (Events -> Applications) and {PP_APPLICATION} "
              "(Postings -> Applications)")
        return {}

    ids: dict[str, Any] = {"parent_page_id": parent["page_id"], "api_version": API_VERSION}
    created: dict[str, str] = {}
    for key, title, props in plan:
        payload = {
            "parent": parent,
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": props,
        }
        db = request("POST", "/databases", payload)
        entry = {"database_id": db["id"]}
        sources = db.get("data_sources") or []
        if sources:
            entry["data_source_id"] = sources[0]["id"]
        ids[key] = entry
        created[key] = db["id"]
        print(f"created {title}: {db['id']}")

    # Relations need the targets to exist, so they are added in a second pass.
    request(
        "PATCH",
        f"/databases/{created['applications']}",
        {"properties": {P_COMPANY: {"relation": {
            "database_id": created["companies"], "single_property": {}}}}},
    )
    request(
        "PATCH",
        f"/databases/{created['events']}",
        {"properties": {"Application": {"relation": {
            "database_id": created["applications"], "single_property": {}}}}},
    )
    request(
        "PATCH",
        f"/databases/{created['postings']}",
        {"properties": {PP_APPLICATION: {"relation": {
            "database_id": created["applications"], "single_property": {}}}}},
    )
    print("linked relations")

    save_ids(ids)
    print(f"wrote {tracker.rel(paths.NOTION_IDS)}")
    print("Set notion.enabled = true in data/config/config.toml to start mirroring.")
    return ids


# ---------------------------------------------------------------------------
# adopt: an existing database becomes part of the mirror
# ---------------------------------------------------------------------------


def adopt_plan(cfg: tracker.Config, existing: dict, applications: dict | None
               ) -> tuple[dict[str, str], dict[str, dict]]:
    """How a hand-kept database becomes the Postings mirror, without losing a row.

    Returns (renames {old name: new name}, additions {name: schema}). Properties
    are renamed, never replaced, so what is in them stays. The title, the one
    url and the one select are claimed by type; a second url or select is
    refused rather than guessed at.
    """
    wanted = postings_props(cfg, applications)
    types = {name: p.get("type") for name, p in existing.items()}
    renames: dict[str, str] = {}
    claimed: set[str] = set(name for name in existing if name in wanted)

    def by_type(kind: str, target: str) -> None:
        if target in existing:
            return
        candidates = [n for n, t in types.items() if t == kind and n not in claimed]
        if len(candidates) > 1:
            raise TrackerError(
                f"{len(candidates)} {kind} properties ({', '.join(candidates)}); "
                f"rename all but the one that is the {target} and retry"
            )
        if candidates:
            renames[candidates[0]] = target
            claimed.add(candidates[0])

    by_type("title", PP_NAME)
    by_type("url", PP_URL)
    by_type("select", PP_STATUS)
    for target, aliases in ADOPT_ALIASES.items():
        if target in existing:
            continue
        expected = next(iter(wanted[target]))
        for alias in aliases:
            if alias in existing and alias not in claimed and types[alias] == expected:
                renames[alias] = target
                claimed.add(alias)
                break

    for name, schema in wanted.items():
        expected = next(iter(schema))
        if name in existing:
            if types[name] != expected:
                raise TrackerError(f"{name} is a {types[name]} in Notion; expected {expected}")
    for old, new in renames.items():
        expected = next(iter(wanted[new]))
        if types[old] != expected:
            raise TrackerError(f"{old} is a {types[old]} in Notion; {new} must be {expected}")

    additions = {
        name: schema for name, schema in wanted.items()
        if name not in existing and name not in renames.values()
    }
    return renames, additions


def do_adopt(kind: str, database: str, dry_run: bool) -> int:
    if kind != "postings":
        raise TrackerError("only the postings database can be adopted")
    cfg = tracker.load_config()
    ids = load_ids() if paths.NOTION_IDS.exists() else {"api_version": API_VERSION}
    db = request("GET", f"/databases/{normalise_id(database)}")
    entry: dict[str, str] = {"database_id": db["id"]}
    sources = db.get("data_sources") or []
    if sources:
        entry["data_source_id"] = sources[0]["id"]
    existing = request("GET", schema_path(entry)).get("properties", {})
    apps = ids.get("applications") or None
    renames, additions = adopt_plan(cfg, existing, apps)

    title = "".join(t.get("plain_text", "") for t in db.get("title", [])) or db["id"]
    print(f"adopting {title} as the Postings mirror")
    for old, new in renames.items():
        print(f"  rename  {old} -> {new}")
    for name, schema in additions.items():
        print(f"  add     {name} ({next(iter(schema))})")
    if not renames and not additions:
        print("  schema already matches")
    if not apps:
        print(f"  {PP_APPLICATION} relation skipped: no applications database in "
              f"{tracker.rel(paths.NOTION_IDS)}")
    if dry_run:
        print("dry run: nothing written")
        return 0

    payload: dict[str, Any] = {old: {"name": new} for old, new in renames.items()}
    payload.update(additions)
    if payload:
        request("PATCH", schema_path(entry), {"properties": payload})
    ids["postings"] = entry
    save_ids(ids)
    print(f"wrote {tracker.rel(paths.NOTION_IDS)}")
    print("Next: `notion_sync.py sync-options` (adds the status options the select "
          "is missing), then `import --postings-only`, then `push`.")
    return 0


# ---------------------------------------------------------------------------
# import  (Notion -> SQLite)
# ---------------------------------------------------------------------------


def import_postings(conn, cfg: tracker.Config, ids: dict, dry_run: bool,
                    mapping: dict[str, str] | None = None) -> None:
    """Bring the Postings database in. Identity: page id, then the URL key.

    A row already here keeps a terminal status and adopts the page's note where
    it had none. A page whose URL matches an application is `applied` whatever
    its label says. A label neither config nor --map knows lands as `new` with
    the label kept at the front of the note, and is reported.
    """
    mapping = {k.strip(): v.strip() for k, v in (mapping or {}).items()}
    pages = list(query_source(ids["postings"]))
    print(f"postings in Notion: {len(pages)}")
    added = present = unusable = 0
    unmapped: set[str] = set()
    for page in pages:
        props = page.get("properties", {})
        url = plain(props.get(PP_URL) or props.get("Ссылка")) or None
        title = plain(props.get(PP_NAME) or props.get("Название")) or None
        company = plain(props.get(PP_COMPANY) or props.get("Компания")) or None
        note = plain(props.get(PP_NOTE) or props.get("Ньюансы") or props.get("Нюансы")) or None
        label = plain(props.get(PP_STATUS) or props.get("Статус")).strip()
        try:
            key = tracker.posting_key(url, company, title)
        except TrackerError:
            unusable += 1
            continue

        status = mapping.get(label) if label else None
        if status is None and label:
            status = match_by_label(cfg, "posting_statuses", label)
        apps = tracker.find_applications(conn, url=url) if url else []
        application_id = int(apps[0]["id"]) if apps else None
        if application_id:
            status = "applied"
        if status is None:
            if label:
                unmapped.add(label)
                note = f"[{label}] {note}" if note else f"[{label}]"
            status = "new"
        try:
            cfg.posting_status(status)
        except TrackerError:
            unmapped.add(status)
            status = "new"

        row = conn.execute(
            "SELECT * FROM postings WHERE notion_page_id = ?", (page["id"],)
        ).fetchone() or tracker.posting_by_key(conn, key)
        if row is not None:
            present += 1
            if dry_run:
                continue
            sets: list[str] = []
            params: list[Any] = []
            if not row["notion_page_id"]:
                sets.append("notion_page_id = ?"); params.append(page["id"])
            if not row["note"] and note:
                sets.append("note = ?"); params.append(note)
            if not cfg.posting_is_terminal(row["status"]) and status != "new":
                sets.append("status = ?"); params.append(status)
            if application_id and not row["application_id"]:
                sets.append("application_id = ?"); params.append(application_id)
            if sets:
                sets.append("updated_at = ?"); params.append(tracker.now())
                params.append(row["id"])
                with conn:
                    conn.execute(f"UPDATE postings SET {', '.join(sets)} WHERE id = ?", params)
            continue

        added += 1
        if dry_run:
            continue
        with conn:
            tracker.insert_posting(conn, cfg, {
                "url": url, "title": title, "company": company, "note": note,
                "status": status, "application_id": application_id,
                "notion_page_id": page["id"],
                "first_seen": (page.get("created_time") or "")[:10] or None,
                "score": number_of(props.get(PP_SCORE)),
                "verdict": plain(props.get(PP_VERDICT)) or None,
                "deadline": plain(props.get(PP_DEADLINE)) or None,
                "source": plain(props.get(PP_SOURCE)) or None,
            })

    verb = "would import" if dry_run else "imported"
    print(f"{verb}: {added} posting(s); already present: {present}")
    if unusable:
        print(f"  {unusable} page(s) had neither a URL nor a company and title, skipped")
    if unmapped:
        print("posting statuses with no match in [[posting_statuses]] or --map "
              "(stored as 'new', label kept in the note):")
        for s in sorted(unmapped):
            print(f"  - {s}")


def page_body_text(page_id: str) -> str:
    """Flatten a page's blocks to text; used to recover posting/letter bodies."""
    out: list[str] = []
    cursor = None
    while True:
        suffix = f"?start_cursor={cursor}" if cursor else ""
        data = request("GET", f"/blocks/{page_id}/children{suffix}")
        for block in data.get("results", []):
            kind = block.get("type")
            payload = block.get(kind) or {}
            text = "".join(p.get("plain_text", "") for p in payload.get("rich_text", []))
            if kind and kind.startswith("heading"):
                out.append(f"\n## {text}")
            elif text:
                out.append(text)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return "\n".join(out).strip()


def split_body(body: str) -> tuple[str, str, str]:
    """Split the legacy 'posting / cover letter / notes' page body."""
    sections: dict[str, list[str]] = {"posting": [], "cover": [], "notes": []}
    current = "posting"
    for line in body.splitlines():
        low = line.strip().lower().lstrip("# ").strip()
        if low.startswith(("текст вакансии", "job posting", "posting")):
            current = "posting"
            continue
        if low.startswith(("сопроводительное", "cover letter")):
            current = "cover"
            continue
        if low.startswith(("заметки", "notes")):
            current = "notes"
            continue
        sections[current].append(line)
    return tuple("\n".join(v).strip() for v in sections.values())  # type: ignore[return-value]


def norm_when(value: str | None) -> str:
    """A date string trimmed to the precision both sides agree on.

    Notion hands back a full offset datetime ("2026-08-25T14:00:00.000+01:00");
    an event typed through tracker.py stores what was passed ("2026-08-25T14:00").
    Comparing the first 16 characters is what makes those the same instant.
    """
    return (value or "").strip().replace(" ", "T")[:16]


# The SQL half of norm_when(). Kept in the same shape and the same order as
# the Python: strip, then space-to-T, then cut to minutes. Written the other
# way round they agree on every value seen so far and would not stay that way.
SQL_NORM_WHEN = "SUBSTR(REPLACE(TRIM(date), ' ', 'T'), 1, 16)"


def existing_event_id(
    conn: sqlite3.Connection,
    page_id: str,
    app_id: int,
    type_id: str,
    when: str,
    outcome: str | None,
    claimed: set[int] | None = None,
) -> int | None:
    """The local event this Notion page already is, or None if it is new.

    Identity is the Notion page id. Events that came across before that column
    existed carry none, so they are matched once on their fields and then adopt
    the page id, after which every later run is an exact lookup.

    The fallback deliberately keeps `outcome` in the key: one application can
    hold two events of the same type on the same day that differ only by it --
    two follow-up emails sent the same evening, one answered and one not.

    `claimed` stands in for the page ids a dry run would have written: without
    it two Notion pages matching one local event both read as already present,
    while a real run adopts on the first and imports the second.
    """
    row = conn.execute(
        "SELECT id FROM events WHERE notion_page_id = ?", (page_id,)
    ).fetchone()
    if row:
        return int(row["id"])
    rows = conn.execute(
        f"""SELECT id FROM events
            WHERE application_id = ?
              AND type = ?
              AND {SQL_NORM_WHEN} = ?
              AND IFNULL(outcome, '') = IFNULL(?, '')
              AND notion_page_id IS NULL
            ORDER BY id""",
        (app_id, type_id, norm_when(when), outcome),
    ).fetchall()
    for row in rows:
        if not claimed or int(row["id"]) not in claimed:
            return int(row["id"])
    return None


def do_import(dry_run: bool, with_bodies: bool, with_postings: bool = True,
              postings_only: bool = False, mapping: dict[str, str] | None = None) -> int:
    cfg = tracker.load_config()
    ids = load_ids()
    conn = tracker.connect()

    if postings_only:
        if "postings" not in ids:
            conn.close()
            raise TrackerError(f"no postings database in {tracker.rel(paths.NOTION_IDS)}; "
                               "run `adopt postings <url>` or `provision` first")
        import_postings(conn, cfg, ids, dry_run, mapping)
        conn.close()
        return 0

    companies: dict[str, dict] = {}
    for page in query_source(ids["companies"]):
        props = page.get("properties", {})
        name = plain(props.get("Name") or props.get("Название"))
        if not name:
            continue
        companies[page["id"]] = {
            "name": name,
            "website": plain(props.get("Website") or props.get("Сайт")),
            "description": plain(props.get("What they do") or props.get("Чем занимается")),
        }
    print(f"companies in Notion: {len(companies)}")

    app_pages = list(query_source(ids["applications"]))
    print(f"applications in Notion: {len(app_pages)}")

    imported = skipped = 0
    by_notion_id: dict[str, int] = {}
    unmapped_statuses: set[str] = set()

    for page in app_pages:
        props = page.get("properties", {})
        role = plain(props.get(P_ROLE) or props.get("Вакансия"))
        url = plain(props.get(P_URL) or props.get("Ссылка на вакансию")) or None
        status_label = plain(props.get(P_STATUS) or props.get("Статус"))
        mode_label = plain(props.get(P_MODE) or props.get("Формат работы"))
        company_ref = first_relation(props.get(P_COMPANY) or props.get("Компания"))
        company = companies.get(company_ref or "", {"name": "Unknown", "website": "", "description": ""})

        status = match_by_label(cfg, "statuses", status_label)
        if status is None:
            unmapped_statuses.add(status_label or "(empty)")
            status = "draft"
        mode = match_by_label(cfg, "work_modes", mode_label)

        existing = tracker.find_applications(conn, url=url, company=company["name"], role=role)
        if existing:
            by_notion_id[page["id"]] = int(existing[0]["id"])
            skipped += 1
            continue

        if dry_run:
            # Register the page anyway, so the event pass below can tell how
            # many events would come across rather than reporting zero.
            by_notion_id[page["id"]] = -1
            imported += 1
            continue

        posting = cover = notes = None
        if with_bodies:
            posting, cover, notes = split_body(page_body_text(page["id"]))

        with conn:
            row = tracker.add_application(
                conn, cfg,
                company=company["name"], role=role or "(unknown role)", url=url,
                status=status, work_mode=mode,
                website=company["website"] or None,
                company_description=company["description"] or None,
                hr_name=plain(props.get(P_HR) or props.get("HR")) or None,
                hr_email=plain(props.get(P_HR_EMAIL) or props.get("Почта HR")) or None,
                posting_text=posting or None,
                cover_letter_text=cover or None,
                notes=notes or None,
                force=True,
            )
            conn.execute(
                "UPDATE applications SET notion_page_id = ? WHERE id = ?",
                (page["id"], row["id"]),
            )
        by_notion_id[page["id"]] = int(row["id"])
        imported += 1

    events = 0
    events_present = 0
    # A dry run writes no page ids, so it has to remember the rows it would
    # have claimed; otherwise it reports a second page matching the same local
    # event as already present, and undercounts what the real run will import.
    claimed: set[int] = set()
    if "events" in ids:
        for page in query_source(ids["events"]):
            props = page.get("properties", {})
            app_ref = first_relation(props.get("Application") or props.get("Отклик"))
            app_id = by_notion_id.get(app_ref or "")
            if not app_id:
                continue
            type_id = match_by_label(cfg, "event_types", plain(props.get("Type") or props.get("Тип")))
            when = plain(props.get("Date") or props.get("Дата"))
            if not type_id or not when:
                continue
            outcome = match_by_label(cfg, "outcomes", plain(props.get("Outcome") or props.get("Итог")))

            # An event already here must not come across a second time. This is
            # what makes a re-run idempotent rather than doubling the history.
            already = existing_event_id(
                conn, page["id"], app_id, type_id, when, outcome, claimed
            )
            if already is not None:
                if dry_run:
                    claimed.add(already)
                else:
                    with conn:
                        conn.execute(
                            "UPDATE events SET notion_page_id = ? "
                            "WHERE id = ? AND notion_page_id IS NULL",
                            (page["id"], already),
                        )
                events_present += 1
                continue

            if dry_run:
                events += 1
                continue
            with conn:
                row = tracker.add_event(
                    conn, cfg, str(app_id), type_id, when,
                    plain(props.get("Participants") or props.get("Участники")) or None,
                    outcome,
                )
                conn.execute(
                    "UPDATE events SET notion_page_id = ? WHERE id = ?",
                    (page["id"], row["id"]),
                )
            events += 1

    verb = "would import" if dry_run else "imported"
    print(f"{verb}: {imported} application(s), {events} event(s)")
    print(f"already present: {skipped} application(s), {events_present} event(s)")
    if unmapped_statuses:
        print("statuses with no match in data/config/config.toml (stored as 'draft'):")
        for s in sorted(unmapped_statuses):
            print(f"  - {s}")
    if with_postings and "postings" in ids:
        import_postings(conn, cfg, ids, dry_run, mapping)
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# push  (SQLite -> Notion)
# ---------------------------------------------------------------------------


def upload_file(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_UPLOAD:
        raise TrackerError(f"{path.name} is {size} bytes; the single-part limit is 20 MiB")
    created = request("POST", "/file_uploads", {"filename": path.name})
    upload_url = created["upload_url"]

    boundary = f"----CareerForge{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    pace()
    req = urllib.request.Request(
        upload_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token()}",
            "Notion-Version": API_VERSION,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TrackerError(
            f"upload of {path.name} failed: HTTP {exc.code} "
            f"{exc.read().decode('utf-8', 'replace')}"
        )
    if result.get("status") != "uploaded":
        raise TrackerError(f"upload of {path.name} reported status {result.get('status')!r}")
    return created["id"]


def source_parent(entry: dict) -> dict:
    """Where a new page goes, in whichever API shape this workspace exposes."""
    if entry.get("data_source_id"):
        return {"type": "data_source_id", "data_source_id": entry["data_source_id"]}
    return {"type": "database_id", "database_id": entry["database_id"]}


def event_key(app_page_id: str, type_label: str, when: str,
              outcome_label: str) -> tuple[str, str, str, str]:
    """The identity of an event page: which application, what, when, how it went.

    The same four fields existing_event_id() matches a local row on, so the two
    directions of the mirror agree on what "the same event" means. Leaving
    outcome out gave one application's two follow-ups on the same evening -- one
    answered, one not -- a single key: the second PATCHed the page the first had
    just created, both local rows took the same notion_page_id, and one event
    disappeared from the mirror without a word.
    """
    return (
        app_page_id,
        (type_label or "").strip(),
        norm_when(when),
        (outcome_label or "").strip(),
    )


def event_index(ids: dict) -> dict[tuple[str, str, str, str], str]:
    """Every event page already in Notion, keyed by event_key.

    Built once per push. An event typed locally may already have been written
    into Notion by hand -- that was the only way to get one there until now --
    and creating a second page for it is exactly the drift this is meant to end.
    """
    index: dict[tuple[str, str, str], str] = {}
    for page in query_source(ids["events"]):
        props = page.get("properties", {})
        app_ref = first_relation(props.get("Application") or props.get("Отклик"))
        if not app_ref:
            continue
        key = event_key(
            app_ref,
            plain(props.get("Type") or props.get("Тип")),
            plain(props.get("Date") or props.get("Дата")),
            plain(props.get("Outcome") or props.get("Итог")),
        )
        index.setdefault(key, page["id"])
    return index


def notion_date(value: str) -> str:
    """An event date in the shape Notion's date property accepts."""
    when = (value or "").strip().replace(" ", "T")
    if "T" in when and len(when) == 16:
        when += ":00"  # Notion wants seconds on a datetime, not just minutes
    return when


def event_props(detail: dict, ev: dict, app_page_id: str,
                with_title: bool) -> dict:
    props: dict[str, Any] = {
        "Type": {"select": {"name": ev["type_label"]}},
        "Date": {"date": {"start": notion_date(ev["date"])}},
        "Participants": {"rich_text": rich_text(ev.get("participants"))},
        "Application": {"relation": [{"id": app_page_id}]},
    }
    if with_title:
        # Only on create, and derived, because the tracker has no title field:
        # there is nothing here that could be more informative than what is
        # already on an existing page. On this tracker 54 of 135 titles said
        # something the Type select did not -- "Отказ после Code Review"
        # against a type of "Другое" -- so overwriting them lost the record.
        props["Name"] = {"title": [{"type": "text", "text": {
            "content": f"{detail['company_name']} - {ev['type_label']}"}}]}
    if ev.get("outcome"):
        # Written when there is one, never cleared. Sending {"select": None} for
        # an empty local outcome would wipe one typed into Notion by hand, which
        # is the same information loss the title rule above exists to prevent --
        # and unlike Type or Date, an absent outcome is more often "not recorded
        # yet" than "there was none".
        props["Outcome"] = {"select": {"name": ev["outcome_label"]}}
    return props


def push_events(conn, ids: dict, detail: dict, app_page_id: str,
                index: dict, dry_run: bool) -> tuple[int, int]:
    """Mirror one application's events. Returns (created, updated)."""
    created = updated = 0
    # The pages this push has already spoken for, the way do_import() carries
    # `claimed`: two local events alike in every mirrored field share a key, and
    # without this the second would overwrite the page the first just took
    # instead of getting one of its own.
    used: set[str] = set()
    for ev in detail["events"]:
        key = event_key(
            app_page_id, ev["type_label"], ev["date"], ev["outcome_label"]
        )
        page_id = ev.get("notion_page_id")
        if page_id and page_id in used:
            # The damage the three-field key already did, on a tracker that was
            # pushed before it was fixed. Repairing it means deciding which of
            # the two rows is the real event, which is not this tool's call --
            # so say it out loud instead of overwriting one with the other in
            # silence, as every push until now has done.
            print(f"  warning: event #{ev['id']} shares Notion page {page_id} "
                  "with an earlier event of this application; only one of them "
                  "can survive there. Delete the duplicate event, or clear its "
                  "notion_page_id to give it a page of its own.")
        if not page_id:
            candidate = index.get(key)
            if candidate and candidate not in used:
                page_id = candidate
        if page_id:
            used.add(page_id)
        if dry_run:
            if page_id:
                updated += 1
            else:
                created += 1
            continue

        props = event_props(detail, ev, app_page_id, with_title=not page_id)
        if page_id:
            request("PATCH", f"/pages/{page_id}", {"properties": props})
            updated += 1
        else:
            page = request("POST", "/pages",
                           {"parent": source_parent(ids["events"]), "properties": props})
            page_id = page["id"]
            used.add(page_id)
            created += 1

        if ev.get("notion_page_id") != page_id:
            with conn:
                conn.execute(
                    "UPDATE events SET notion_page_id = ? WHERE id = ?",
                    (page_id, ev["id"]),
                )
        index[key] = page_id
    return created, updated


def posting_props(cfg: tracker.Config, row: dict, app_page_id: str | None,
                  with_title: bool) -> dict:
    """One posting as Notion properties.

    Status, company, URL, source and score are the tracker's to overwrite. Note
    is written when the tracker has one and never cleared, and Name is set only
    on create -- the same reasons as an event's Outcome and title: a page typed
    by hand says something the tracker cannot reproduce.
    """
    props: dict[str, Any] = {
        PP_STATUS: {"select": {"name": cfg.label("posting_statuses", row["status"])}},
        PP_COMPANY: {"rich_text": rich_text(row.get("company"))},
        PP_URL: {"url": row.get("url") or None},
        PP_SOURCE: {"rich_text": rich_text(row.get("source"))},
        PP_SCORE: {"number": row.get("score")},
    }
    if row.get("first_seen"):
        props[PP_FIRST_SEEN] = {"date": {"start": row["first_seen"]}}
    if row.get("deadline"):
        props[PP_DEADLINE] = {"date": {"start": row["deadline"]}}
    if row.get("verdict"):
        props[PP_VERDICT] = {"select": {"name": row["verdict"]}}
    if row.get("note"):
        props[PP_NOTE] = {"rich_text": rich_text(row["note"])}
    if app_page_id:
        props[PP_APPLICATION] = {"relation": [{"id": app_page_id}]}
    if with_title:
        props[PP_NAME] = {"title": [{"type": "text", "text": {
            "content": row.get("title") or row.get("url") or "(untitled)"}}]}
    return props


def posting_index(ids: dict) -> dict[str, str]:
    """Every page already in the Postings database, keyed like the table.

    A hand-kept list holds pages the tracker never wrote; keyed on the same
    normalised URL they are adopted, not duplicated.
    """
    index: dict[str, str] = {}
    for page in query_source(ids["postings"]):
        props = page.get("properties", {})
        url = plain(props.get(PP_URL) or props.get("Ссылка"))
        title = plain(props.get(PP_NAME) or props.get("Название"))
        company = plain(props.get(PP_COMPANY) or props.get("Компания"))
        try:
            key = tracker.posting_key(url, company, title)
        except TrackerError:
            continue
        index.setdefault(key, page["id"])
    return index


def push_postings(conn, cfg: tracker.Config, ids: dict, dry_run: bool) -> tuple[int, int]:
    """Mirror the postings table. Returns (created, updated)."""
    rows = tracker.list_postings(conn, cfg, all=True)
    if not rows:
        return 0, 0
    app_pages = {
        r["id"]: r["notion_page_id"]
        for r in conn.execute("SELECT id, notion_page_id FROM applications")
    }
    index = posting_index(ids)
    used: set[str] = set()
    created = updated = 0
    for row in rows:
        page_id = row.get("notion_page_id")
        if page_id and page_id in used:
            print(f"  warning: posting #{row['id']} shares Notion page {page_id} with an "
                  "earlier posting; only one of them can survive there. Clear one "
                  "notion_page_id to give it a page of its own.")
        if not page_id:
            candidate = index.get(row["url_key"])
            if candidate and candidate not in used:
                page_id = candidate
        if page_id:
            used.add(page_id)
        if dry_run:
            if page_id:
                updated += 1
            else:
                created += 1
            continue

        app_page = app_pages.get(row["application_id"]) if row.get("application_id") else None
        props = posting_props(cfg, row, app_page, with_title=not page_id)
        if page_id:
            request("PATCH", f"/pages/{page_id}", {"properties": props})
            updated += 1
        else:
            page = request("POST", "/pages",
                           {"parent": source_parent(ids["postings"]), "properties": props})
            page_id = page["id"]
            used.add(page_id)
            created += 1
        if row.get("notion_page_id") != page_id:
            with conn:
                conn.execute(
                    "UPDATE postings SET notion_page_id = ?, updated_at = updated_at WHERE id = ?",
                    (page_id, row["id"]),
                )
        index[row["url_key"]] = page_id
    return created, updated


def company_page(conn, ids: dict, cache: dict, name: str, website: str | None,
                 description: str | None) -> str:
    if name in cache:
        return cache[name]
    entry = ids["companies"]
    parent = source_parent(entry)
    for page in query_source(entry):
        if plain(page["properties"].get("Name") or page["properties"].get("Название")) == name:
            cache[name] = page["id"]
            return page["id"]
    page = request("POST", "/pages", {
        "parent": parent,
        "properties": {
            "Name": {"title": [{"type": "text", "text": {"content": name}}]},
            "Website": {"url": website or None},
            "What they do": {"rich_text": rich_text(description)},
        },
    })
    cache[name] = page["id"]
    return page["id"]


def do_push(slug: str | None, with_files: bool, dry_run: bool,
            with_events: bool = True, with_postings: bool = True) -> int:
    cfg = tracker.load_config()
    ids = load_ids()
    conn = tracker.connect()
    parent = source_parent(ids["applications"])

    rows = ([tracker.application_detail(conn, cfg, slug)] if slug
            else tracker.list_applications(conn, cfg))
    cache: dict[str, str] = {}
    pushed = 0

    # A workspace provisioned before this could push events has an Events
    # database; one whose notion.json predates it does not.
    mirror_events = with_events and "events" in ids
    index: dict[tuple[str, str, str, str], str] = {}
    index_loaded = False
    ev_created = ev_updated = 0

    def mirror(detail: dict, app_page_id: str) -> None:
        nonlocal index, index_loaded, ev_created, ev_updated
        if not mirror_events or not detail["events"]:
            return
        if not index_loaded:
            index = event_index(ids)
            index_loaded = True
        created, updated = push_events(
            conn, ids, detail, app_page_id, index, dry_run
        )
        ev_created += created
        ev_updated += updated

    for row in rows:
        detail = row if "attachments" in row else tracker.application_detail(conn, cfg, str(row["id"]))
        props: dict[str, Any] = {
            P_ROLE: {"title": [{"type": "text", "text": {"content": detail["role"]}}]},
            P_STATUS: {"select": {"name": cfg.label("statuses", detail["status"])}},
            P_URL: {"url": detail["url"] or None},
            P_HR: {"rich_text": rich_text(detail["hr_name"])},
        }
        if detail["work_mode"]:
            props[P_MODE] = {"select": {"name": cfg.label("work_modes", detail["work_mode"])}}
        if detail["hr_email"]:
            props[P_HR_EMAIL] = {"email": detail["hr_email"]}

        if dry_run:
            print(f"would push #{detail['id']} {detail['company_name']} - {detail['role']}")
            pushed += 1
            # With no page yet there is nothing for an event to relate to, so
            # its events read as new -- which is what the real run will do.
            mirror(detail, detail["notion_page_id"] or "")
            continue

        props[P_COMPANY] = {"relation": [{"id": company_page(
            conn, ids, cache, detail["company_name"],
            detail.get("company_website"), None)}]}

        if with_files:
            for att in detail["attachments"]:
                path = paths.REPO / att["path"]
                if not path.exists():
                    print(f"  skipped missing file {att['path']}")
                    continue
                prop = P_CV if att["kind"] == "cv" else P_COVER
                upload_id = upload_file(path)
                props[prop] = {"files": [{
                    "type": "file_upload",
                    "file_upload": {"id": upload_id},
                    "name": path.name,
                }]}

        if detail["notion_page_id"]:
            app_page_id = detail["notion_page_id"]
            request("PATCH", f"/pages/{app_page_id}", {"properties": props})
        else:
            page = request("POST", "/pages", {"parent": parent, "properties": props})
            app_page_id = page["id"]
            with conn:
                conn.execute(
                    "UPDATE applications SET notion_page_id = ? WHERE id = ?",
                    (app_page_id, detail["id"]),
                )
        pushed += 1
        print(f"pushed #{detail['id']} {detail['company_name']} - {detail['role']}")
        before = (ev_created, ev_updated)
        mirror(detail, app_page_id)
        if (ev_created, ev_updated) != before:
            print(f"  events: {ev_created - before[0]} created, "
                  f"{ev_updated - before[1]} updated")

    print(f"{'would push' if dry_run else 'pushed'}: {pushed} application(s)")
    if mirror_events:
        print(f"events: {'would create' if dry_run else 'created'} {ev_created}, "
              f"{'would update' if dry_run else 'updated'} {ev_updated}")
    elif with_events:
        print(f"events: skipped -- no Events database in {tracker.rel(paths.NOTION_IDS)}. "
              "Run `notion_sync.py provision` to create one.")

    # Postings go across after the applications, so a posting can point at the
    # page its application just got. One application (--slug) leaves them alone.
    if with_postings and not slug:
        if "postings" in ids:
            p_created, p_updated = push_postings(conn, cfg, ids, dry_run)
            print(f"postings: {'would create' if dry_run else 'created'} {p_created}, "
                  f"{'would update' if dry_run else 'updated'} {p_updated}")
        else:
            print(f"postings: skipped -- no Postings database in {tracker.rel(paths.NOTION_IDS)}. "
                  "Run `notion_sync.py provision`, or `adopt postings <url>` for an existing one.")
    conn.close()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_map(value: str | None) -> dict[str, str]:
    """`--map "Label=status,Other=status"` -> {label: status}."""
    mapping: dict[str, str] = {}
    for item in (value or "").split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise TrackerError(f"--map entries look like Label=status; got {item.strip()!r}")
        label, status = item.split("=", 1)
        mapping[label.strip()] = status.strip()
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description="Notion mirror for the CareerForge tracker")
    sub = ap.add_subparsers(dest="command", required=True)

    pv = sub.add_parser("provision", help="create the mirror databases")
    pv.add_argument("--parent-page", required=True, help="page id or URL to create them under")
    pv.add_argument("--dry-run", action="store_true")

    so = sub.add_parser(
        "sync-options",
        help="bring the mirror's select options back in step with config")
    so.add_argument("--dry-run", action="store_true")

    ad = sub.add_parser("adopt", help="make an existing Notion database part of the mirror")
    ad.add_argument("kind", choices=["postings"])
    ad.add_argument("database", help="database id or URL")
    ad.add_argument("--dry-run", action="store_true")

    im = sub.add_parser("import", help="copy Notion into the local tracker")
    im.add_argument("--dry-run", action="store_true")
    im.add_argument("--no-bodies", action="store_true",
                    help="skip page bodies (much faster, loses posting/letter text)")
    im.add_argument("--postings-only", action="store_true",
                    help="only the Postings database")
    im.add_argument("--no-postings", action="store_true",
                    help="leave the Postings database alone")
    im.add_argument("--map", help='posting status labels config does not know, '
                                  'as "Label=status,Other label=status"')

    pu = sub.add_parser("push", help="copy the local tracker into Notion")
    pu.add_argument("--slug", help="one application instead of all")
    pu.add_argument("--files", action="store_true", help="also upload the built PDFs")
    pu.add_argument("--no-events", action="store_true",
                    help="applications and companies only, leave event pages alone")
    pu.add_argument("--no-postings", action="store_true",
                    help="leave the Postings database alone")
    pu.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.command == "provision":
        provision(args.parent_page, args.dry_run)
        return 0
    if args.command == "sync-options":
        return do_sync_options(args.dry_run)
    if args.command == "adopt":
        return do_adopt(args.kind, args.database, args.dry_run)
    if args.command == "import":
        mapping = parse_map(args.map)
        return do_import(args.dry_run, not args.no_bodies, not args.no_postings,
                         args.postings_only, mapping)
    return do_push(args.slug, args.files, args.dry_run, not args.no_events,
                   not args.no_postings)


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
