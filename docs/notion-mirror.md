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

**2. Store the token.** Either `NOTION_TOKEN` in your environment, or a
`.notion_token` file in the repo root. Both are gitignored; neither is ever
logged.

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
python tools/notion_sync.py push              # properties only
python tools/notion_sync.py push --files      # also upload the built PDFs
python tools/notion_sync.py push --slug acme  # just one application
```

`push` creates a page the first time and updates it afterwards, keyed by
`notion_page_id` on the local row. A files property is replaced wholesale on
each push, so re-pushing swaps the PDF rather than adding a second copy.

**`push` writes applications only.** It does not create or update event pages —
events travel the other way, through `import`. So a funnel event added with
`tracker.py event add` stays local until you put it in Notion yourself, and the
two sides can drift apart in both directions. Comparing event counts will not
tell you: diff on application, type, date and outcome instead.

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

## What does not come across

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
