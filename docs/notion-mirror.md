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

This creates three linked databases — Companies, Applications, Events —
**with the statuses, work modes, event types and outcomes from your own
`config/config.toml`**. There is no template to duplicate and no ids to
transcribe; the ids it gets back are written to `config/notion.json`
(gitignored).

**5. Enable it.** Set `notion.enabled = true` in `config/config.toml`.

## Daily use

```bash
python tools/notion_sync.py push               # applications, companies, events
python tools/notion_sync.py push --files       # also upload the built PDFs
python tools/notion_sync.py push --slug acme   # just one application
python tools/notion_sync.py push --no-events   # leave event pages alone
python tools/notion_sync.py push --dry-run     # counts only, writes nothing
```

`push` creates a page the first time and updates it afterwards, keyed by
`notion_page_id` on the local row. A files property is replaced wholesale on
each push, so re-pushing swaps the PDF rather than adding a second copy.

**Events go across too**, keyed the same way — `events.notion_page_id`, and for
an event that has none, a match on application, type and date. So an event you
typed into Notion by hand before pushing is adopted rather than duplicated, and
a second push updates the page it made the first time.

The tracker is the source of truth for the fields it holds, so a push
**overwrites** Type, Date, Participants, Outcome and the page title. The title
is derived — `Acme - Screening` — because the tracker has no title field of its
own. If you have been writing richer titles in Notion by hand, `--no-events`
keeps them; there is no local field for that text to survive in otherwise.

Event `notes` stay local: the Events database `provision` creates has no field
for them, so there is nowhere to put them.

`--dry-run` reports what it would create and update without writing, and it
accounts for the pages it would have claimed, so its numbers match the real run.

Upload limits: 20 MiB per file in a single request. A CV is tens of kilobytes.

## Migrating an existing Notion tracker in

If you already track applications in Notion and want to move to CareerForge:

**1. Point at your databases.** Write `config/notion.json` by hand:

```json
{
  "applications": { "database_id": "…", "data_source_id": "…" },
  "companies":    { "database_id": "…" },
  "events":       { "database_id": "…" }
}
```

`data_source_id` is optional — the adapter falls back to the classic
`/databases/{id}/query` endpoint when it is absent or rejected.

**2. Dry-run.** Nothing is written; you get counts and a list of any status
labels it could not map.

```bash
python tools/notion_sync.py import --dry-run
```

**3. Map your statuses.** Any Notion status label the importer cannot match is
reported and would land as `draft`. Fix that by adding the label to the right
`[[statuses]]` entry in `config/config.toml` — the importer matches on any
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
- **Page comments and anything outside the three databases.**

## After migrating

Reconcile what came in against what is on disk:

```bash
python tools/tracker.py list --json | python -c "import json,sys; print(len(json.load(sys.stdin)))"
ls applications processing rejected
python tools/tracker.py list --json    # look at folder_in_sync
```

`/triage` finds tracker rows with no folder and folders with no tracker row, and
proposes fixes.
