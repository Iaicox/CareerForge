# The optional Notion mirror

CareerForge tracks everything locally in SQLite. This is for one thing only:
seeing your pipeline on a device that is not the machine your repository is on.

It is off by default (`notion.enabled = false`), and nothing in the workflow
depends on it.

## What it costs you

Be clear about the trade before turning it on.

| | Local SQLite | Notion mirror |
|---|---|---|
| Account needed | no | yes |
| Works offline | yes | no |
| API quotas | none | yes, per workspace |
| Readable on your phone | no | yes |
| Source of truth | **always** | never |

If you only ever look at your pipeline from the machine you apply from, skip
this page. `python tools/board.py` already gives you a kanban.

## Setup

**1. Create an integration.** [notion.so/my-integrations](https://www.notion.so/my-integrations)
→ New integration, with **Read content**, **Update content** and **Insert
content**.

**2. Store the token.** `NOTION_KEY=<token>` in `.env` in the repo root.
`NOTION_TOKEN` in your environment and a `.notion_token` file both still work
as fallbacks. All three are gitignored; none is ever logged.

**3. Pick a parent page** in Notion to hold the tracker, and connect the
integration to it: `...` → Connections → your integration. The databases created
underneath inherit that access.

**4. Provision.** Dry-run first — it prints what it would create and touches
nothing:

```bash
python tools/notion_sync.py provision --parent-page <page URL> --dry-run
python tools/notion_sync.py provision --parent-page <page URL>
```

This creates four linked databases — Companies, Applications, Events,
Postings — **with the statuses, work modes, event types, outcomes and posting
statuses from your own `data/config/config.toml`**. There is no template to duplicate and no ids to
transcribe; the ids it gets back are written to `data/state/notion.json`
(gitignored).

**5. Enable it.** Set `notion.enabled = true` in `data/config/config.toml`.

## After you edit data/config/config.toml

`provision` writes the select options once. Add a status, work mode, event type
or outcome to `data/config/config.toml` afterwards and Notion has never heard of it —
the select still holds the options it was created with, and the new value has
nowhere to land.

```bash
python tools/notion_sync.py sync-options --dry-run
python tools/notion_sync.py sync-options
```

It only ever **adds**. An option Notion has and your config does not is
reported and left where it is: a page may be using it, and removing the option
would clear that page's value with nothing to say so. Renaming a label is
therefore two options, not one — delete the old one in Notion yourself, once
you are sure nothing uses it.

## Daily use

```bash
python tools/notion_sync.py sync-options       # after editing data/config/config.toml
python tools/notion_sync.py push               # applications, companies, events, postings
python tools/notion_sync.py push --files       # also upload the built PDFs
python tools/notion_sync.py push --slug acme   # just one application (postings left alone)
python tools/notion_sync.py push --no-events   # leave event pages alone
python tools/notion_sync.py push --no-postings # leave the Postings database alone
python tools/notion_sync.py push --dry-run     # counts only, writes nothing
```

`push` creates a page the first time and updates it afterwards, keyed by
`notion_page_id` on the local row. A files property is replaced wholesale on
each push, so re-pushing swaps the PDF rather than adding a second copy.

**Events go across too**, keyed the same way — `events.notion_page_id`, and for
an event that has none, a match on application, type, date and outcome. So an
event you typed into Notion by hand before pushing is adopted rather than
duplicated, and a second push updates the page it made the first time. Outcome
is in that key because one application can hold two events of the same type on
the same day that differ only by it — two follow-up emails sent the same
evening, one answered and one not. Both keep their own page.

The tracker is the source of truth for the fields it holds, so a push
**overwrites** Type, Date and Participants.

**Outcome is written but never cleared.** It is set when the tracker has one and
left alone when it does not, for the same reason a title is left alone: an
outcome you typed in Notion says something the tracker has no way to reproduce,
and a push that "kept the tracker authoritative" would erase it. Clear an
outcome in Notion by clearing it in Notion.

If a push warns that two events **share a Notion page**, that is damage from
before outcome joined the key: two local events were mirrored onto one page, and
each push writes one over the other. Pick which row is the real event — delete
the duplicate, or clear its `notion_page_id` so the next push gives it a page of
its own.

**It never rewrites a title.** `Name` is set once, when the page is created,
and derived — `Acme - Screening`. After that it is yours: the tracker has no
title field, so it has nothing to say there that could be better than what is
already on the page. That matters more than it sounds. On a real mirror, 54 of
135 titles carried something the `Type` select did not — `Отказ после Code
Review` on a page typed `Другое` — and a push that "kept the tracker
authoritative" would have thrown all of it away.

Event `notes` stay local: the Events database `provision` creates has no field
for them, so there is nowhere to put them.

## The postings list

Every posting the tracker has seen — scraped, scored, declined, applied to —
goes across too, as the Postings database: title, company, URL, status, score
and verdict from `/rank`, deadline, source, first seen, the note, and a
relation to the application it became. One page per row, keyed by
`postings.notion_page_id` and, for a page that has none, by the posting URL
with tracking parameters stripped — so a page you typed by hand is adopted,
not duplicated.

The tracker owns status, company, URL, source, score, verdict and the dates,
and overwrites them. Two things follow the events' rules: **the title is set
once, on create**, and **the note is written when the tracker has one and
never cleared** — a nuance typed on the phone says something the tracker
cannot reproduce. Status is edited locally, in `/board postings` or with
`shortlist.py mark`; the phone reads.

A full push sends every row, one request each at Notion's pace — about a
minute per two hundred postings. `--slug` pushes never touch postings.

### Adopting a list you already keep

If you already have a Notion database of postings — a title, a company, a
link, a status select, a notes column — it becomes the mirror in place rather
than being replaced:

```bash
python tools/notion_sync.py adopt postings <database URL> --dry-run
python tools/notion_sync.py adopt postings <database URL>
python tools/notion_sync.py sync-options
python tools/notion_sync.py import --postings-only --map "Есть ньюанс=maybe,Дубль=skipped" --dry-run
python tools/notion_sync.py import --postings-only --map "Есть ньюанс=maybe,Дубль=skipped"
python tools/notion_sync.py push
```

`adopt` **renames** the properties it recognises — the title, the one url, the
one select, and a text column called Company/Компания or Note/Ньюансы — to the
mirror's names, keeping every row, and **adds** the ones that are missing. A
database with two url or two select properties is refused rather than guessed
at. `sync-options` then adds the status options the select lacks; your old
options stay, and are reported.

`import --postings-only` brings the rows in. A label your config knows — with
or without its emoji — maps to that status; a label it does not is mapped with
`--map`, or lands as `new` with the label kept at the front of the note. A page
whose URL matches an application is `applied` whatever its label says. A row
already in the tracker keeps a terminal status it has and adopts the page's
note where it had none.

`--dry-run` reports what it would create and update without writing, and it
accounts for the pages it would have claimed, so its numbers match the real run.

Upload limits: 20 MiB per file in a single request. A CV is tens of kilobytes.

## Migrating an existing Notion tracker in

If you already track applications in Notion and want to move to CareerForge:

**1. Point at your databases.** Write `data/state/notion.json` by hand:

```json
{
  "applications": { "database_id": "…", "data_source_id": "…" },
  "companies":    { "database_id": "…" },
  "events":       { "database_id": "…" },
  "postings":     { "database_id": "…", "data_source_id": "…" }
}
```

`postings` is optional; without it, postings are skipped with a message.

`data_source_id` is optional — the adapter falls back to the classic
`/databases/{id}/query` endpoint when it is absent or rejected.

**2. Dry-run.** Nothing is written; you get counts and a list of any status
labels it could not map.

```bash
python tools/notion_sync.py import --dry-run
```

**3. Map your statuses.** Any Notion status label the importer cannot match is
reported and would land as `draft`. Fix that by adding the label to the right
`[[statuses]]` entry in `data/config/config.toml` — the importer matches on any
locale's label, so a Russian or German status string maps cleanly once it is
listed there.

**4. Import.**

```bash
python tools/notion_sync.py import
```

Idempotent: re-running skips what is already there. Applications are keyed on
the posting URL, events on the Notion page id, which the import records in
`events.notion_page_id` the first time it sees them. Events that came across
before that column existed are matched once on application, type, date and
outcome, and adopt their page id then; every later run is an exact lookup.

The summary separates the two, so a re-run reads honestly:

```
imported: 0 application(s), 2 event(s)
already present: 80 application(s), 130 event(s)
```

Page bodies are read too, so posting snapshots and cover letter text come
across. `--no-bodies` is much faster if you do not need them.

## What does not come across on import

- **Attachments.** Files stay in Notion. Your local PDFs are already on disk;
  `tracker.py attach` records them.
- **Rollups and formulas.** `last_event_date` is computed by the local view
  instead.
- **Page comments and anything outside the four databases.**

## After migrating

Reconcile what came in against what is on disk:

```bash
python tools/tracker.py list --json | python -c "import json,sys; print(len(json.load(sys.stdin)))"
ls data/pipeline/applications data/pipeline/processing data/pipeline/rejected
python tools/tracker.py list --json    # look at folder_in_sync
```

`/triage` finds tracker rows with no folder and folders with no tracker row, and
proposes fixes.
