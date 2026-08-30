# Changelog

## Unreleased

### Changed

- **Gemini has two pools of models and sets a refused one aside.**
  `[gemini] models` (everything without search) and `search_models`
  (company research and salary figures, with Google Search attached) in
  `config.toml`, tried in order. A 429 puts the model on cooldown and the next
  one is asked at once: a per-minute limit for the API's retry delay or a
  minute, a daily quota until the retry delay it names or the next midnight
  in Los Angeles; a 404 skips the model for the rest of the run. Cooldowns
  live in `data/state/gemini-cooldowns.json` and `gemini.py check` lists
  them. Defaults: 3.7 → 3.6 → 3.5 → 3 → 2.5 Flash, and 2.5 Flash → 2.5 Flash
  Lite for search; a single `model` in an older config goes first.

- **Gemini is called over its REST API; the CLI is gone.** `tools/gemini.py`
  used to shell out to `gemini`. Version 0.56/0.57 of that CLI rewrites every
  model whose name ends in `flash` — `gemini-2.5-flash`, `gemini-3.7-flash` —
  to `gemini-3.5-flash` under a remote flag, runs its web-search tool on that
  model whatever `-m` says, and retries a quota error with backoff for as
  long as the timeout allows; the free tier of 3.5 Flash is 20 requests a
  day, so research never returned. Now the request goes straight to
  `generativelanguage.googleapis.com` with `GEMINI_API_KEY`: the model is
  exactly the one configured, Google Search is a tool on the research
  request, JSON mode is asked for where no tool is involved, a daily quota is
  reported once, a short rate limit is waited out once, "high demand" is
  retried twice, and the socket timeout is the timeout. No CLI to install,
  no `gemini-cwd` scratch directory, no 32 KB command-line limit.

- **Company research looks for what the company pays, and records it.**
  `research.py get --role --location` asks for salary figures for the role,
  each with its own location, currency, period, basis and source; a cache
  entry from before the question existed is refreshed once. In `/apply`'s
  research step, a figure for **the location the posting states** goes into
  the salary benchmark with its source and date; a figure for any other
  location does not — the company is recorded as *unknown* for this location,
  with the other-market figure kept as a lead, so it is neither presented as
  evidence nor researched again.

  `salary_lookup.py add` is the write path: one record per company and city;
  no unit conversion; a category the file has never seen needs
  `--new-category`, and the first foreign one pins `metadata.baseline_unit`
  so figures in another unit are shown without the `vs Baseline` column. A
  figure already there is not overwritten without `--force`. Records carry
  `source`, `as_of`, `note` and `origin`, and the lookup shows them. The
  allow-listed `salary_lookup.py` therefore now writes — only to
  `data/profile/salary_data.json`.

- **The Notion mirror carries postings too.** A fourth database, Postings,
  next to Companies, Applications and Events: title, company, URL, status,
  score and verdict, deadline, source, first seen, the note, and a relation to
  the application. Keyed by `postings.notion_page_id`, then by the posting URL,
  so a page typed by hand is adopted rather than duplicated; the title is set
  once and the note is never cleared, on the events' reasoning. `push` sends
  postings after applications (`--no-postings` to skip; `--slug` never touches
  them), `import` brings them in (`--postings-only`, `--map` for labels config
  does not know), and `sync-options` covers the new select. Labels match with
  or without their emoji, for postings and applications alike.

  New `adopt postings <database URL>`: a list you already keep in Notion becomes
  the mirror in place — its title, url, select and Company/Note columns are
  renamed, the missing properties added, every row kept.

- **Every posting ever seen is a row in the tracker.** The scraper kept its
  own JSON file, keyed on the raw URL; `/apply` never wrote to it, and the
  tracker compared URLs with its own bare `strip()` — so a LinkedIn link with a
  different `?trk=` was a new posting, and a role declined in `/apply` came
  back on the next scrape at full price. Now a `postings` table (migration
  `005`) holds scraped, ranked, evaluated and applied-to postings alike, keyed
  on a normalised URL (scheme, `www.`, fragment, tracking parameters and the
  trailing slash dropped). `tracker.py add` marks the posting an application
  came from `applied`; `/apply` records a decline as `skipped` with the reason;
  `/scrape` asks `shortlist.py check` before fetching anything. Statuses are
  configuration — `[[posting_statuses]]`, with `maybe` for a posting kept in
  view with a caveat — and fall back to the defaults when the block is absent.

  `tools/shortlist.py` keeps its verbs (`show`, `add`, `merge-scores`, `sweep`)
  over the table and gains `check`, `mark` and a one-time `import-json` for the
  old file, which re-keys its hand-made slugs on the URL and keeps every
  `skipped` note. `data/job_scraper/` is gone with it.

  `/board postings` opens a second page on the board server: the table with the
  status as a select, the note editable in place, filters for status, score
  and text. The kanban links to it.

- **Everything yours now lives under `data/`.** Seven user directories used to
  sit at the repo root between the framework's own; finding an application
  meant scanning three of them, and `.gitignore` needed seven blocks to draw the
  line. Now: `data/config/` (what you write), `data/profile/`, `data/documents/`,
  `data/pipeline/{applications,processing,rejected}/` and `data/state/` (what
  the tools write — the database, `notion.json`, Gemini logs, session digests).
  A framework template sits beside the file it is a template for, so
  `config.example.toml` and `profile.example/` moved in too; `data/.gitignore`
  ignores everything else. The published manual, a build product of framework
  docs, moved out to `docs/manual-artifact.html`.

  `tools/paths.py` is now the one place that knows the layout; the tools that
  each derived `REPO / "profile"` on their own read it instead, and the tests
  point it at a throwaway root with `paths.configure()`. Migration `004`
  prefixes stored attachment paths, which are repo-relative, with
  `data/pipeline/`.

  Moving an existing workspace: move the contents of the old directories into
  their new homes (`tracker/*` → `data/state/`, `config/notion.json` →
  `data/state/`, the rest by name), then `python tools/tracker.py init`.

### Fixed

- **A Gemini timeout no longer hangs, and `timeout_seconds` bounds the whole
  call.** The CLI was a launcher whose grandchild held the stdout pipe, so
  `subprocess.run()` killed only the child on a timeout and then blocked
  reading the pipe until the grandchild exited by itself — a 120-second limit
  that ran for eight minutes. That route is gone (above): over REST the socket
  timeout ends the request, and `call()` holds one deadline for the whole pool,
  so a stalled API costs the configured budget once rather than once per model.

- **A quota refusal benches one quota, not both.** Cooldowns were keyed by
  model name while Google meters grounded and ungrounded requests separately,
  so a batch `rank` that spent its plain quota took company research down with
  it, and one grounding refusal took down everything else. They are now keyed
  by model and mode. An inconclusive 429 is read as a per-minute limit rather
  than a daily one — the wrong guess then costs one retry instead of a day —
  and `gemini.py check` reports a refusal without benching anything, so running
  `/doctor` can no longer disable what it is checking.

- **A 400 no longer kills the pool.** Only an unusable key stops a call now;
  any other per-model error moves to the next model, so a model that will not
  take the search tool no longer takes company research down with it.

- **Research no longer caches a salary answer it never got.** `research_company`
  wrote `salary: []` on every call, including role-less ones and refused ones,
  which is exactly the marker `research.py` reads to decide an entry is worth
  refreshing. A `/interview` lookup could therefore leave the later `/apply`
  with an empty salary block for `cache_days`. The key is written only when the
  question was answered.

- **Two postings no longer collapse into one.** `position`, `source` and `src`
  were stripped from posting URLs as tracking parameters, but boards use them
  to name the posting itself, so the second of two postings was silently
  dropped as already known.

- **The Notion import no longer duplicates events.** `notion_sync.py import`
  deduplicated applications on their posting URL but keyed events on nothing, so
  every re-run appended the whole Notion event history again — against a
  tracker holding 133 events that meant ~132 silent duplicates per run, while
  the documentation promised the import was idempotent. Events now carry
  `notion_page_id` (migration `003`), and the import matches on it. Events that
  arrived before the column existed are matched once on application, type, date
  and outcome, and adopt their page id then.

  The identity keeps `outcome`, because one application can legitimately hold
  two events of the same type on the same day that differ only by it — two
  follow-up emails the same evening, one answered and one not. A `UNIQUE` index
  on `(application_id, type, date)` would have destroyed those, and could not
  live in `schema.sql` anyway: `init_db()` runs the schema before the
  migrations, so an index naming a column the migration has yet to add fails on
  every existing database.

- **`--dry-run` no longer flatters itself.** It counted every event it would
  process, so "would import: 132 event(s)" read as 132 new ones. New and
  already-present are now reported on separate lines, for applications and
  events alike.

- **The Notion mirror survives a slow connection.** `notion_sync.request`
  retried a refused or dropped connection four times but not a read timeout: a
  socket timeout is not a `URLError`, so it escaped the retry loop and came out
  as a bare `TimeoutError` in the middle of a push. Gemini and the mirror now
  share one transport, which classifies a timeout the same way for both; each
  keeps its own retry policy, pacing and error type on top of it.

- **A state file is written whole, or not at all.** `data/state/notion.json`
  and `gemini-cooldowns.json` were written straight over the target, so an
  interrupted write left a parse error rather than a shorter file. The
  cooldowns file recovers by itself — an unreadable one reads as no cooldowns —
  but `notion.json` holds the database ids and costs a re-provision to rebuild.
  Both now write beside the target and move it into place.

- **`/doctor` names what is wrong with Gemini, and gives advice that fits.**
  The check recovered the cause by matching `NOT USABLE` in the probe's console
  output, cut it at 80 characters, and printed one fixed remedy — "set
  `GEMINI_API_KEY` in `.env`" — whatever had happened. With the plain pool
  answering and the search pool out of its daily grounding quota, that came out
  as a sentence truncated before the time the quota returned, no mention that
  the plain pool was healthy, and an instruction to replace a key that was
  working. Every failure now carries the kind the branch that produced it knew
  — quota, cooling, unknown model, overload, timeout, auth — `gemini.py check
  --json` reports it per pool together with a remedy derived from those kinds,
  and doctor forwards both without forming an opinion of its own. In that
  remedy a configuration error outranks a quota, because a quota comes back by
  itself at a time the report can name.

### Documentation

- `docs/notion-mirror.md` states what `push` actually does: applications only,
  never events. The two sides can drift in both directions, and equal event
  counts do not mean they agree.

## 0.2.0 — 2026-08-24

Catching up with what upstream learned since the fork, moving bulk work off
Claude's context, and finally writing the manual.

### From upstream

[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search)
reached v1.6.0 while this fork sat on its initial release. It is now configured
as a remote, read rather than merged — the structures have diverged past the
point where merging means anything, but their bug fixes often apply here by
descent. One from the day before this release fixed dotted `A.M.B.A.` suffixes
in company-name matching: the same defect as the dotted `S.A.` case fixed here
independently a day earlier.

- **Schema gaps closed.** The tracker had no `deadline` and no `location` at
  all — only `office_address`, and only for hybrid roles. `location` and
  `location_verdict` are separate columns, because the place is a fact and the
  verdict is a judgement, and merging them loses the fact. Also `source`, and
  the fit score with its strengths and gaps, which until now scrolled away with
  the session.
- **Deadlines are parsed defensively.** Postings say `ASAP`, `rolling` and
  `until filled`. Storing those as text would make the column unsortable and
  every deadline comparison a lie, so anything that is not a real date becomes
  null.
- **Migrations.** `schema.sql` is the current shape; `tools/migrations/*.sql`
  carries an older database forward, recorded so each runs once. A fresh
  database records them as a baseline rather than replaying them.
- **`/rank`** scores what the scraper found so `/apply` is only spent on what
  is worth it. Location and language are gates, not deductions: a failed gate
  caps the verdict at weak, because scoring a role 85 and then mentioning in
  passing that it requires relocation wastes the reader's attention.
- **`/interview`** prepares for a specific round — screening, tech, system
  design, manager, final — inferring the stage from the tracker and saying so.
  `--mock` rehearses one question at a time and critiques directly; a mock
  interview that praises everything builds confidence in an answer that will
  not survive contact.
- **`documents/` and `/expand`.** A folder to drop CVs, certificates and
  reference letters into, which `/setup` reads instead of asking for a paste.
  Old CVs are the richest thing in there: they describe work a later CV had to
  cut for space, which is exactly the material tailoring needs.
- **Company research is cached** for 30 days in the `companies` table, shared
  between `/apply`'s reviewer and `/interview`. It works with Gemini switched
  off — `research.py put` stores what Claude gathered. Stale research with
  Gemini down is returned labelled with its age, which beats returning nothing.
- **ATS text-layer check.** A PDF can look perfect and extract as mojibake, so
  the parser reads nothing and no human sees the application. Checks
  replacement characters, ligatures, a surviving email and phone, every section
  heading, and posting keywords. Warnings, not build errors.

### Gemini delegation

Optional, off by default, and never a dependency: every failure exits 3 with a
reason and the work is done in Claude instead, saying which route it took.

Gemini gathers and compresses — research, extraction, batch ranking, long
documents. Judgement about what is honest to claim does not move.

Two traps found building it: the CLI exits 0 on an auth failure and reports it
only inside its JSON, and with `--skip-trust` that JSON goes to stderr. Both
streams are parsed and the error key decides, not the exit code. And bulk input
now travels over stdin — a 190 KB transcript in argv fails with WinError 206,
which broke exactly the inputs the delegation exists for.

### Mail

**`/mailsync`** reads the mailbox you actually apply from over IMAP, which is
rarely the one wired to a chat connector. Standard library only, so any
provider works.

Read-only by construction: opened `readonly=True`, fetched with `BODY.PEEK`.
Nothing is marked read, moved or flagged.

It proposes and never decides — a "we regret" can be a rejection for one role
inside an offer for another, so every finding carries the phrase that triggered
it. Company names match on word boundaries, so "Meta" does not match
"metadata".

### Sessions

`tools/session_digest.py` mines old Claude Code transcripts for standing rules,
positioning decisions and honesty boundaries. Filtering to human turns and
assistant prose takes the real corpus from 70.9 MB to 1.8 MB — 2.5% — which is
what makes it affordable. Raw transcripts stay where they are; they are full of
paths that no longer exist.

### Documentation

`docs/manual.md`: install to first sent application, and everything after.
Every command documented, every tool named, every internal link checked.

### Fixed

- Timestamps mixed UTC column defaults with local-time Python helpers, so one
  row could show `created_at` an hour after `updated_at`.
- `applications_view` never picked up schema changes, because
  `CREATE VIEW IF NOT EXISTS` leaves an old definition in place. Views are now
  dropped and recreated on every init.

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
