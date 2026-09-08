# The tracker database

`data/state/careerforge.db` — an ordinary SQLite file. Created by
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
| `research_json` | Cached company research, shared by `/apply`'s reviewer and `/interview` so the same company is not researched twice. Carries a `salary` list when the research was asked about a role. Leads only — the verification checklist still applies |
| `researched_at` | When that cache was filled; it expires after 30 days |
| `created_at` | |

### `applications`

One row per application: one CV sent for one role.

| Column | Notes |
|---|---|
| `id` | |
| `company_id` | → `companies` |
| `role` | The title as the posting writes it |
| `slug` | Unique; also the folder name under the stage directory |
| `url` | The posting URL — the primary deduplication key |
| `status` | An id from `[[statuses]]` in `data/config/config.toml` |
| `work_mode` | An id from `[[work_modes]]` |
| `location` | The place as the posting states it |
| `location_verdict` | `pass`, `fail` or `flag`. Separate from `location` because the place is a fact and the verdict is a judgement |
| `office_address` | Hybrid and on-site only |
| `deadline` | ISO date, or null. `ASAP`, `rolling` and free text store as null rather than making the column unsortable |
| `source` | Which board or channel it came from |
| `salary_text` | The pay as the posting states it, verbatim, currency and period and all. Never parsed into a number: the parse would pick a currency, a period and a basis that the posting did not state. Rows written before the column existed are null and fall back to the benchmark in `data/profile/salary_data.json` |
| `fit_score` | 0–100, from `/rank` or `/apply` |
| `fit_strengths`, `fit_gaps` | JSON arrays; read back as lists |
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
| `id` | |
| `application_id` | → `applications`, cascade delete |
| `type` | An id from `[[event_types]]` |
| `date` | ISO-8601; date, or datetime for scheduled calls |
| `is_datetime` | Whether the time part is meaningful |
| `participants` | People on their side |
| `outcome` | An id from `[[outcomes]]` |
| `notes` | |
| `notion_page_id` | The Notion event page this row mirrors, whichever direction it travelled. What keeps a re-run from adding it twice. Empty until it has been in the mirror |
| `created_at` | |

### `attachments`

| Column | Notes |
|---|---|
| `id` | |
| `application_id` | → `applications`, cascade delete |
| `kind` | `cv`, `cover`, `other` |
| `path` | Repo-relative. The bytes stay on disk; only the path is stored |
| `added_at` | |

`UNIQUE(application_id, kind, path)`, so attaching the same file twice is not
an error and not a duplicate row; a different path of the same kind is a second
row, unless `tracker.py attach --replace` collapses the kind to the one being
attached. A row comes back out with `tracker.py detach`, which removes the row
and never the file it names.


### `postings`

Every job posting ever seen — scraped by `/scrape`, scored by `/rank`,
evaluated in `/apply`, applied to, or skipped with a reason. One table, so the
scraper's deduplication and `/apply`'s memory of what was declined are the same
thing. Written through `tools/shortlist.py`; `tracker.py add` marks the posting
an application came from `applied` on its own.

| Column | Notes |
|---|---|
| `id` | |
| `url` | As found |
| `url_key` | `normalize_url(url)` — scheme, `www.`, fragment, tracking parameters and a trailing slash dropped — or `company::title` when there is no URL. Unique: the deduplication key |
| `title`, `company` | As the posting writes them |
| `location`, `source`, `summary` | From the scrape; `source` is `apply` for a posting first seen in `/apply` |
| `deadline` | ISO date or null, like `applications.deadline` |
| `salary_text` | The pay as the posting states it, verbatim, currency and period and all. Never parsed into a number: the parse would pick a currency, a period and a basis that the posting did not state. Rows written before the column existed are null and fall back to the benchmark in `data/profile/salary_data.json` |
| `first_seen` | Date |
| `status` | An id from `[[posting_statuses]]`: `new`, `ranked`, `maybe`, `applied`, `skipped`, `expired` by default |
| `note` | Why it was skipped, or the caveat behind `maybe` |
| `score`, `verdict`, `strengths`, `gaps`, `location_verdict`, `language_verdict`, `reason`, `scored_at` | What `/rank` wrote; `strengths` and `gaps` are JSON arrays |
| `application_id` | → `applications`, set to null if the application is deleted |
| `notion_page_id` | Set if this row is mirrored |
| `created_at`, `updated_at` | |

### `meta` and `migrations`

Bookkeeping, two columns each. `meta` holds `schema_version`; `migrations`
holds the filename of every migration already applied, so each runs once.

### `applications_view`

What every read goes through. Every column of `applications`, plus:

| Column | Notes |
|---|---|
| `company_name`, `company_slug`, `company_website` | Joined from `companies` |
| `last_event_date`, `event_count` | Replacing the rollup field the Notion version relied on |
| `is_expired` | 1 if `deadline` is past. Evaluated per query, so it stays true as the day rolls over rather than needing a nightly sweep |

## What the database does not store

**Display labels.** `status` holds `screening`, never `Screening` or
`📞 Скрининг`. Labels come from `data/config/config.toml` at read time, which is why
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
datasette data/state/careerforge.db         # queryable web UI, pip install datasette
```

[DB Browser for SQLite](https://sqlitebrowser.org/) and the VS Code SQLite
extension both open it directly.

Reading it any way you like is fine. **Writing to it by hand is not** — the
CLI enforces the deduplication, the id validation and the folder movement that
keep the tracker and the filesystem agreeing with each other.

## Migrations

`meta.schema_version` records the version. `tracker.py init` is idempotent:
every statement in `schema.sql` is `IF NOT EXISTS`, so re-running it on an
existing database adds anything new without touching your rows. Changes that
cannot be expressed that way — adding a column to an existing table — go in
`tools/migrations/`, are applied in filename order, and are recorded in the
`migrations` table so each runs exactly once. A database created fresh from
`schema.sql` already has everything they would add, so they are recorded as a
baseline instead of replayed.

Each migration runs in its own transaction together with the row that records
it, so one that fails partway leaves the database exactly as it was and can be
retried once fixed.

A migration is a `.sql` file, or a `.py` file defining `migrate(conn, tracker)`
— for the repairs SQL cannot express, the ones that have to look at the
filesystem or reach the status-to-stage map, which lives in your `config.toml`
and not in the database. The runner hands the tracker module in rather than
letting the file import it.

The transaction is the runner's in both cases, and a migration that opens or
ends one of its own takes that guarantee away: a `.sql` file may not use
`BEGIN`, `COMMIT`, `SAVEPOINT` or `PRAGMA`, and a `.py` file may not call
`executescript()`, `commit()` or `rollback()` — `executescript()` commits
whatever is open before it runs a line. Break that and a migration failing
partway can leave its work applied with no row recording it, which wedges the
next `init`. The runner holds the same rule to itself from the other side: call
it with a transaction already open and it refuses, because the `COMMIT` ending
the first migration would land that transaction too.

Not every migration adds a column. `004_attachments_data_dir.sql` rewrites the
rows of `attachments`: their paths are repo-relative, and the stage directories
moved from the repo root to `data/pipeline/`. `005_postings.sql` creates the
`postings` table on an existing database; `tracker.py init` then gives every
application its `applied` row, and `shortlist.py import-json` brings the old
`seen_jobs.json` in. `006_attachment_stage_paths.py` repairs that same column
once more: `set_status` used to move an application's folder between stage
directories without taking its attachment paths along, so the migration
repoints each row at the stage its folder is actually in — and where the
document was attached a second time at the right path, collapses the pair onto
the row that is already correct. `007_salary.sql` adds `salary_text` to both
`applications` and `postings`, and adds nothing to the rows already there —
nothing recorded the figure before, so there is nothing to recover it from. Those
rows keep showing the market benchmark, which is what they showed anyway.

## Backups

Copy the file. If the board or a session might be running, copy all three of
`careerforge.db`, `careerforge.db-wal` and `careerforge.db-shm`, or take a
consistent snapshot:

```bash
sqlite3 data/state/careerforge.db ".backup 'backup.db'"
python tools/tracker.py export > backup.json
```
