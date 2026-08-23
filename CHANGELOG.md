# Changelog

## 0.1.0 — 2026-08-23

First CareerForge release. Forked from
[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
and rebuilt around two goals: someone else can actually use it, and nothing it
writes about you is invented.

### Reusable by someone other than the author

- **Profile split out of the framework.** The candidate profile used to live
  inside `CLAUDE.md` and the skill files. It now lives in `profile/`, which is
  gitignored, alongside `config/config.toml`. `/setup` writes only there, so
  onboarding leaves `git status` clean and a framework update never conflicts
  with your content.
- **`profile.example/`** carries the templates `/setup` fills in. Editing them
  changes what the interview asks for.
- **Everything market-specific moved into configuration.** Statuses, their
  labels and their stage mapping, work modes, event types, outcomes, page
  limits, document filenames and company-name normalisation are all in
  `config/config.toml`. The status-to-folder mapping in particular used to be
  duplicated in three documents that could drift apart; it is now one table that
  the tracker, the board and `/triage` all read.
- **`/doctor`** reports what the toolchain is missing and how to install it.
- **A first-run notice** — a `SessionStart` hook and a rule at the top of
  `CLAUDE.md` — stops a fresh clone from drafting documents against a profile
  that does not exist yet.

### The tracker moved from Notion to local SQLite

- **`tools/tracker.py`** — companies, applications, events and attachments in
  `tracker/careerforge.db`. Standard library only. Deduplication by posting URL
  or company plus role, validation against your configured ids, and a
  `--stale` query for applications gone quiet.
- **`tools/board.py`** — a kanban at `127.0.0.1:8765`, no dependencies and no
  CDN. Dragging a card changes the status *and* moves the application folder.
  Loopback-only with a `Host` check, since it writes to disk.
- **Status drives the folder, in one operation.** The move runs before the
  database write, so a refused move leaves the status untouched, and a name
  collision in the target directory is reported rather than resolved by
  overwriting.
- **Optimistic locking.** Status changes carry the `updated_at` they were read
  at, so the board and the agent cannot silently overwrite each other.
- **Notion became optional** (`notion.enabled = false`). `notion_sync.py
  provision` creates the three linked databases from your own configured
  statuses — no template to duplicate by hand — and `import` migrates an
  existing Notion tracker in. SQLite is the source of truth in both directions.

### Fixed

- **`gemini-research-expert` was dead code.** Nothing in the repository
  referenced it, and it required an uninstalled `gemini` CLI. Replaced with
  `application-reviewer`, which `/apply` now spawns by name; the 60-line prompt
  moved out of `apply.md` into the agent definition where it belongs.
- **Neither skill was being registered.** `SKILL.md` files declared `name` and
  `description` as bold Markdown instead of YAML frontmatter, so Claude Code
  never picked them up — including the one named in the permission allowlist.
  Both now have proper frontmatter, joined by a third, `application-tracker`.
- **The document pipeline was Windows-only.** `build.ps1` keeps MS Word as the
  preferred engine and falls back to LibreOffice; `build.sh` covers macOS and
  Linux. Both read their page limits from `config/config.toml` so they cannot
  drift. `tools/pagecount.py` counts pages without Word, and says so when its
  answer is approximate.
- **`salary_lookup.py` was Denmark-specific**, stripping `ø/æ/å` and Danish
  legal forms. Now folds diacritics generally and strips legal forms from a
  configurable list. Two bugs found while generalising it: `S.A.` was invisible
  to the word-boundary patterns because of its periods, and a company literally
  named "Company Ltd" normalised to an empty key that would have matched every
  entry in the dataset.
- **`.gitignore` blocked every image in the repository** with a bare `*.png`,
  and missed `job_scraper/runs/` where the scraper writes its shortlists.
- **Timestamps mixed clocks.** SQLite column defaults are UTC; the Python
  helpers used local time, so a single row could show `created_at` an hour after
  `updated_at`. Everything is UTC now.

### Removed

- The Danish job-board skills left over from the fork.
- `notion_upload.ps1`, superseded by the cross-platform `notion_sync.py push`.
- The Notion tracker document, which hardcoded a personal workspace, page and
  data-source ids.

### Tests

`python -m unittest discover -s tests` — 21 tests over deduplication, the folder
lifecycle including collision refusal, optimistic locking, status and stage
mapping, and company-name normalisation.
