<h1 align="center">CareerForge</h1>

<p align="center">
  A job-application workspace for <a href="https://claude.com/claude-code">Claude Code</a>.<br>
  Find postings, judge fit honestly, tailor a CV, write the letter, track the pipeline, prep the interview.
</p>

---

## What it is

You describe your career once. After that, a job posting goes in and a tailored,
fact-checked CV and cover letter come out — built to PDF, recorded in a local
tracker, with interview prep waiting when the reply arrives.

```
 /setup            /scrape                 /apply <url>              /board
    |                 |                        |                       |
    v                 v                        v                       v
 your profile     search the boards       evaluate fit             kanban of
 and config       dedup vs tracker        draft CV (+ letter)      everything
    |                 |                        |                   you have sent
    v                 v                        v
 data/profile/    ranked matches          reviewer agent critiques
 data/config/     with fit ratings        -> revise -> build PDF -> track
```

The framework is language- and country-agnostic. Your profile, your statuses and
your search queries are yours; nothing about a particular market is baked in.

## Why the output is trustworthy

The failure mode of an AI writing your CV is a confident invention that you then
have to defend in an interview. CareerForge is built against that:

- **One source of truth.** Everything claimed in a document must be traceable to
  `data/profile/candidate.md`. Nothing else counts as evidence.
- **Honesty notes in the profile.** Each role can carry explicit boundaries —
  what you did *not* do, phrasings to avoid, which claims would be a stretch.
  They never appear in a document; they stop one from being written.
- **A reviewer that has to cite.** The `application-reviewer` agent researches
  the company and marks each claim `verified` with a source, or `unverified`.
  Unverified claims get re-checked before they go anywhere near a document.
- **A checklist that runs every time**, in `CLAUDE.md`, reported pass/fail.

## Requirements

| | |
|---|---|
| **Claude Code** | the CLI — this is a workspace for it |
| **Python 3.11+** | tracker, board and tooling; standard library only, no pip install |
| **pandoc** | Markdown to DOCX |
| **MS Word** *or* **LibreOffice** | DOCX to PDF. Word gives exact page counts; LibreOffice works everywhere |

Optional: `pypdf` or poppler's `pdfinfo` for exact page counts on the
LibreOffice path; a Notion account if you want your pipeline mirrored to your
phone.

Run `/doctor` at any time and it will tell you exactly what is missing and how
to install it.

## Quick start

```bash
git clone <your-fork-url> careerforge
cd careerforge
claude
```

Then, inside Claude Code:

```
/setup
```

It checks your toolchain, then either reads your existing CV or interviews you,
and writes your profile, your configuration and your tracker database. Nothing
in the repository itself is modified — see [How your data is kept
separate](#how-your-data-is-kept-separate).

```
/scrape                                   # find matching postings
/apply https://example.com/jobs/senior-fe # the full pipeline on one of them
/board                                    # see your pipeline as a kanban
/board postings                           # every posting seen, as a table
```

## Commands

| Command | What it does |
|---|---|
| `/setup` | Build your profile, configure the tracker, check the toolchain |
| `/scrape` | Search job boards, dedup against everything you have already seen |
| `/rank` | Score what the scraper found, so `/apply` is spent only on what is worth it |
| `/apply <url or text>` | Evaluate, draft, review, revise, build, check, track |
| `/interview <company> [stage]` | Prepare for a specific round; `--mock` to rehearse |
| `/track` | Look at or update the tracker |
| `/board` | Open the pipeline as a drag-and-drop kanban at `127.0.0.1:8765`; `/board postings` for the table of every posting seen |
| `/mailsync` | Find employer replies in your mailbox and reconcile the pipeline |
| `/triage` | Find folders out of sync and applications gone silent |
| `/expand` | Grow the profile from new documents and public sources |
| `/doctor` | Check the toolchain |

**[Read the manual](docs/manual.md)** for the whole thing end to end.

## The tracker

A local SQLite database at `data/state/careerforge.db`. No account, no service, no
quota, works on a plane.

```bash
python tools/tracker.py add --company Acme --role "Senior Frontend Developer" \
                            --url https://acme.example/jobs/1
python tools/tracker.py set-status acme screening   # also moves the folder
python tools/tracker.py list --stale                # gone quiet past your cutoff
python tools/tracker.py report --board              # kanban in the terminal
python tools/board.py                               # kanban in the browser
python tools/shortlist.py check <url>               # seen this posting before? why was it dropped?
```

The same database holds every posting you have seen — scraped, scored,
declined in `/apply` with the reason, applied to — keyed on the URL with
tracking parameters stripped. `/scrape` never surfaces one twice.

**Status drives the folder.** Each status maps to a stage directory, and
changing the status moves the application's documents with it:

| Directory | Meaning |
|---|---|
| `data/pipeline/applications/` | Applied, waiting for an answer |
| `data/pipeline/processing/` | They replied; interviews scheduled or underway |
| `data/pipeline/rejected/` | Closed: rejection, silence, or withdrawn |

Statuses, their labels and their stage mapping all live in `data/config/config.toml`.
Rename them, add them, translate them — the framework reads whatever you define.

It is an ordinary SQLite file, so you can also open it in DB Browser for SQLite,
the VS Code SQLite extension, or `datasette data/state/careerforge.db`.

### Optional: mirror to Notion

If you want your pipeline on your phone:

```bash
python tools/notion_sync.py provision --parent-page <page URL>   # creates the databases
python tools/notion_sync.py push --files                         # local -> Notion
```

`provision` builds the four linked databases — companies, applications,
events, and every posting seen — from your own configured statuses, so there
is no template to duplicate by hand. SQLite stays the source of truth; Notion
is a mirror. `import` runs the other way, for moving an existing Notion tracker
in; `adopt postings <database URL>` turns a list of postings you already keep
in Notion into the mirror in place.

## How your data is kept separate

Everything about you lives under one directory, `data/`:

```
data/
  config/config.toml   your locale, statuses, page limits   (what you write)
  profile/             your CV master, experience, STAR stories, search queries
  documents/           the raw material you dropped in
  pipeline/            applications/ processing/ rejected/ — one folder per application
  state/               the database, Notion ids, logs       (what the tools write)
```

Everything else is the framework. `/setup` and daily work never write outside
`data/` — so `git status` stays clean, and pulling an update never conflicts
with your own content. The only tracked files in there are the templates,
kept beside the files they are templates for: `data/config/config.example.toml`
and `data/profile.example/`. Edit those if you want `/setup` to ask for
something different.

## Making it yours

| To change | Edit |
|---|---|
| What the setup interview asks for | `data/profile.example/*.md` |
| Statuses, labels, page limits, locale | `data/config/config.toml` |
| How postings are scored | `.claude/skills/job-application-assistant/references/job-evaluation.md` |
| Tone and phrasing rules | `.claude/skills/job-application-assistant/references/writing-style.md` |
| Document design | `templates/reference_cv.docx` / `reference_cover.docx` in Word, or `tools/make_reference.ps1` |
| What the reviewer looks for | `.claude/agents/application-reviewer.md` |

## Tests

```bash
python -m unittest discover -s tests
```

Covers deduplication, the folder lifecycle including collision refusal,
optimistic locking, and company-name normalisation.

## Documentation

- **[docs/manual.md](docs/manual.md)** — the full manual: install, first run,
  finding work, applying, interviews, configuration, troubleshooting
- [SETUP.md](SETUP.md) — installation in detail, per platform
- [docs/architecture.md](docs/architecture.md) — how the pieces fit
- [docs/tracker-schema.md](docs/tracker-schema.md) — the database, and how to browse it
- [docs/notion-mirror.md](docs/notion-mirror.md) — the optional Notion mirror

## Optional: delegate the bulk work

With the [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed,
company research, posting extraction, batch ranking and long-document
summarising move off Claude's context. Judgement about what is honest to claim
does not move — that stays in one place, against your profile.

It is never a dependency: if Gemini is missing or unauthenticated, the work is
done in Claude instead and you are told which route was taken. See
[the manual](docs/manual.md#11-delegating-to-gemini).

## Acknowledgements

Forked from [MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search),
then substantially rebuilt: local tracker and kanban in place of a hardcoded
Notion workspace, a profile layer separated from the framework, a wired-up
reviewer agent, and a cross-platform document pipeline.

Built with [Claude Code](https://claude.com/claude-code).

## License

MIT — see [LICENSE](LICENSE).
