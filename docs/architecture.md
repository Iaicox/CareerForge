# Architecture

## The one boundary that matters

CareerForge is two things in one directory: a **framework** that is the same for
everyone, and **your data**, which is not. The whole design follows from keeping
them apart.

```
CareerForge/
├── framework  (committed, never written to during normal work)
│   ├── CLAUDE.md                 rules, lifecycle, verification checklist
│   ├── .claude/commands/         /setup /scrape /rank /apply /interview /track
│   │                             /board /mailsync /triage /expand /doctor
│   ├── .claude/agents/           application-reviewer
│   ├── .claude/skills/           job-application-assistant, job-scraper, application-tracker
│   ├── .claude/hooks/            first-run notice
│   ├── profile.example/          templates that tell /setup what to collect
│   ├── config/config.example.toml
│   ├── templates/                docx reference docs, fonts
│   ├── tools/                    tracker, board, doctor, build, notion adapter
│   └── tests/
│
└── yours  (gitignored, written by /setup and by daily work)
    ├── profile/                  who you are; the source of truth for every claim
    ├── config/config.toml        locale, statuses, page limits
    ├── config/notion.json        Notion database ids, if you mirror
    ├── tracker/careerforge.db    the pipeline
    ├── applications/ processing/ rejected/
    └── job_scraper/              seen postings, run shortlists
```

Two consequences worth stating outright:

- **`/setup` writes only to the second group.** That is why `git status` is
  empty after onboarding, and why pulling a framework update never conflicts
  with your content.
- **A workflow that wants to edit a framework file is a bug**, not a reason to
  edit it. The thing it wants to change belongs in `profile/` or
  `config/config.toml`.

## Configuration is the seam

`config/config.toml` is where anything market-, language- or preference-specific
lives, so the framework can stay neutral:

| Setting | Consumed by |
|---|---|
| `locale` | every status and event label shown to you |
| `[[statuses]]` — id, stage, labels | tracker, board, `/triage`, Notion provisioning |
| `[[work_modes]]`, `[[event_types]]`, `[[outcomes]]` | tracker validation, board dropdowns |
| `[documents]` — filenames, page limits, engine | `build.ps1`, `build.sh` |
| `tracker.stale_after_days` | `tracker.py list --stale`, `/triage` |
| `[salary]` — legal forms, regions | `tools/salary_lookup.py` normalisation |

The status table is the important one. Before, the status-to-folder mapping was
written out in three separate documents that could drift apart. Now
`stage_of(status)` is a function over the config, and the tracker, the board and
`/triage` all call it.

## Status drives the folder

An application's documents live in a folder named by its slug. Which of the
three stage directories that folder sits in is derived from the status — never
set independently.

```
tracker.py set-status <slug> screening
    -> move_folder(slug, stage_of("screening"))    # applications/ -> processing/
    -> UPDATE applications SET status = 'screening'
```

The move happens **before** the database write, so a refused move leaves the
status untouched. A name collision in the target directory is reported and
nothing is changed — overwriting someone's application documents is not a
recoverable mistake.

`/triage` exists because reality still drifts: it compares `folder_in_sync`
across the pipeline and proposes fixes.

## Data flow through /apply

```
posting (URL or text)
   |
   |-- tracker.py find --url ...            duplicate? stop and ask
   v
fit evaluation      references/job-evaluation.md  x  profile/evaluation.md
   |
   |  (user decides: CV only, CV + letter, or stop)
   v
draft               profile/cv_master.md -> applications/<slug>/cv_<name>.md
                    profile/cover_letter_master.md -> cover_letter_<name>.md
                    job.md snapshot
   v
application-reviewer agent                 researches, critiques, cites
   |                                        claims marked verified / unverified
   v
revise              unverified claims re-checked or cut
   v
build.ps1 / build.sh                        pandoc -> docx -> pdf, page limits enforced
   v
tracker.py add                              dedup, company upsert, folder slug
   v
verification checklist (CLAUDE.md)          reported pass/fail
```

## The tracker as shared layer

`tools/tracker.py` is both the CLI and the data-access module. `board.py` and
`notion_sync.py` import it rather than opening the database themselves, so the
rules — deduplication, validation against the configured ids, folder movement,
optimistic locking — hold no matter which surface is used.

```
             tracker.py  (CLI + module)
                  |
      +-----------+-----------+
      |           |           |
  board.py   notion_sync.py  Claude, via the
  (kanban)   (mirror)        application-tracker skill
```

Concurrency: SQLite runs in WAL mode, and status changes take an
`expected_updated_at`. If you drag a card while the agent has just changed that
same application from the chat, the board is told its copy is stale and reloads
instead of silently overwriting.

## The board

`tools/board.py` serves one HTML page and a small JSON API on `127.0.0.1` only.
It writes to disk — a drag moves folders — so it also rejects any request whose
`Host` header is not loopback, which is what stops a web page you happen to have
open from driving it via DNS rebinding.

No build step, no dependencies, no CDN: the page is one file with inline CSS and
JS, and it reads the same columns your config defines.

## Documents

Two scripts, one per platform, both reading their page limits from
`config/config.toml` through `tools/config_get.py` so they cannot drift:

- `build.ps1` — Windows. MS Word via COM, which renders exactly and reports
  exact page counts. Falls back to LibreOffice if Word is not scriptable.
- `build.sh` — macOS and Linux. LibreOffice, with page counts from
  `tools/pagecount.py` (pypdf, then `pdfinfo`, then an approximate fallback that
  says so).

Both protect hand edits: if the `.docx` is newer than the `.md`, only the PDF is
refreshed unless you force a rebuild.

## Watching upstream

CareerForge was forked from
[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search) at
its initial release, and that project has kept moving. It is configured as a
remote:

```bash
git fetch upstream
git log --oneline HEAD..upstream/master
```

**Read it, do not merge it.** The structures have diverged past the point where
a merge means anything: they track applications in a CSV and build documents
with LaTeX, we use SQLite and pandoc. What is worth taking is ideas and bug
reports.

Some of the most useful traffic there is bug fixes to problems this code shares
by descent. One example from the day this was written: they fixed dotted
`A.M.B.A.` suffixes being missed in company-name matching, which is the same
defect as the dotted `S.A.` case fixed here independently a day earlier. When
the same code has two lineages, the other lineage's bug list is worth reading.

The reverse also applies: features borrowed from them are re-implemented
against our own model rather than copied. Their company-research cache is a
directory of JSON files; here it is two columns on the `companies` table,
because we already had that table and a second store would be a second truth.

## Notion is optional

`notion_sync.py` mirrors the tracker for phone access. It is off by default, and
nothing in the workflow depends on it.

`provision` creates the three linked databases from your configured statuses, so
there is no template to duplicate and no ids to copy by hand. `push` writes
local state outward; `import` reads an existing Notion tracker in, which is how
you migrate off one.

SQLite is authoritative in every direction. Notion has API quotas and needs an
account; a job search should not stop because of either.
