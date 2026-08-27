# The tracker database

`tracker/careerforge.db` — an ordinary SQLite file. Created by
`python tools/tracker.py init` from `tools/schema.sql`.

## Tables

### `companies`

One row per company, forever. A second application to the same company links to
the same row.

| Column | Notes |
|---|---|
| `id` | |
| `name` | As the company writes it, without legal forms |
| `slug` | Normalised; the deduplication key |
| `description` | One or two sentences: product, industry, size |
| `website` | |

### `applications`

One row per application: one CV sent for one role.

| Column | Notes |
|---|---|
| `id` | |
| `company_id` | → `companies` |
| `role` | The title as the posting writes it |
| `slug` | Unique; also the folder name under the stage directory |
| `url` | The posting URL — the primary deduplication key |
| `status` | An id from `[[statuses]]` in `config/config.toml` |
| `work_mode` | An id from `[[work_modes]]` |
| `office_address` | Hybrid and on-site only |
| `hr_name`, `hr_email`, `other_contacts` | |
| `posting_text` | Snapshot, in case the posting is taken down |
| `cover_letter_text` | The letter as sent |
| `notes` | |
| `notion_page_id` | Set if this row is mirrored |
| `created_at`, `updated_at` | UTC, matching SQLite's own defaults |

### `events`

One row per stage you went through. Status says where you are; events say how
you got there.

| Column | Notes |
|---|---|
| `application_id` | → `applications`, cascade delete |
| `type` | An id from `[[event_types]]` |
| `date` | ISO-8601; date, or datetime for scheduled calls |
| `is_datetime` | Whether the time part is meaningful |
| `participants` | People on their side |
| `outcome` | An id from `[[outcomes]]` |
| `notes` | |
| `notion_page_id` | Set only for events the Notion import brought across; what keeps a re-run from adding them twice. Empty for anything typed locally |

### `attachments`

| Column | Notes |
|---|---|
| `application_id` | → `applications`, cascade delete |
| `kind` | `cv`, `cover`, `other` |
| `path` | Repo-relative. The bytes stay on disk; only the path is stored |

### `applications_view`

What every read goes through. Joins the company, and computes
`last_event_date` and `event_count` — replacing the rollup field the Notion
version relied on.

## What the database does not store

**Display labels.** `status` holds `screening`, never `Screening` or
`📞 Скрининг`. Labels come from `config/config.toml` at read time, which is why
you can change your locale, rename a status or translate the whole funnel
without migrating anything.

**File contents.** Attachments are paths.

**Which folder an application is in.** That is derived from the status through
the configured stage mapping, and checked against the filesystem on read
(`folder_in_sync`). Storing it would create a second source of truth to drift.

## Browsing it yourself

It is a plain SQLite file, so:

```bash
python tools/board.py                    # kanban, drag to change status
python tools/tracker.py report --board   # kanban in the terminal
python tools/tracker.py export           # everything as JSON
datasette tracker/careerforge.db         # queryable web UI, pip install datasette
```

[DB Browser for SQLite](https://sqlitebrowser.org/) and the VS Code SQLite
extension both open it directly.

Reading it any way you like is fine. **Writing to it by hand is not** — the
CLI enforces the deduplication, the id validation and the folder movement that
keep the tracker and the filesystem agreeing with each other.

## Migrations

`meta.schema_version` records the version. `tracker.py init` is idempotent:
every statement in `schema.sql` is `IF NOT EXISTS`, so re-running it on an
existing database adds anything new without touching your rows. Future changes
that cannot be expressed that way go in `tools/migrations/`.

## Backups

Copy the file. If the board or a session might be running, copy all three of
`careerforge.db`, `careerforge.db-wal` and `careerforge.db-shm`, or take a
consistent snapshot:

```bash
sqlite3 tracker/careerforge.db ".backup 'backup.db'"
python tools/tracker.py export > backup.json
```
