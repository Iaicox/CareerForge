<!--
  Published as an artifact: https://claude.ai/code/artifact/2b68ae96-7ba7-4e71-8942-451c949f6ea5
  After editing this file, regenerate and republish:
    python tools/publish_manual.py
  then publish docs/manual-artifact.html with the Artifact tool, passing
  that url so the existing page updates rather than a second one appearing.
-->

# CareerForge — the manual

Everything from installing this to sending your first application, and what to
do with it afterwards.

Read it once, then use the contents to come back to a part.

**Contents**

1. [What this is, and what it is not](#1-what-this-is-and-what-it-is-not)
2. [Install](#2-install)
3. [First run](#3-first-run)
4. [Your profile is the product](#4-your-profile-is-the-product)
5. [Finding work](#5-finding-work)
6. [Applying](#6-applying)
7. [After you apply](#7-after-you-apply)
8. [Interviews](#8-interviews)
9. [Growing the profile](#9-growing-the-profile)
10. [Configuration reference](#10-configuration-reference)
11. [Delegating to Gemini](#11-delegating-to-gemini)
12. [Troubleshooting](#12-troubleshooting)
13. [Extending it](#13-extending-it)

---

## 1. What this is, and what it is not

CareerForge is a workspace for [Claude Code](https://claude.com/claude-code).
You describe your career once; after that a job posting goes in, and a tailored
CV and cover letter come out — built to PDF, checked, and recorded in a local
tracker.

**What it does well**

- Turns one master CV into an application aimed at a specific posting, without
  you rewriting it each time
- Judges fit honestly enough to be worth reading, including when the answer is
  "do not apply"
- Keeps a pipeline you can actually see, including which folder holds which
  application and what stage it is at
- Prepares for a specific interview round rather than interviews in general

**What it does not do**

- **It does not submit applications.** Every posting is submitted by you, on
  the employer's site. Nothing here logs into a job board.
- **It does not invent experience.** If you have never used Kubernetes, no
  amount of prompting will produce a CV that says you have. This is the
  central design constraint, not a limitation to work around.
- **It does not replace judgement about your own career.** It scores fit from
  what you told it. It does not know that you would take a pay cut for that one
  company.
- **It is not a spray-and-pray machine.** Each application takes real time, most
  of it yours. The value is in the tailoring; a hundred generic applications
  need a different tool.

**The honesty machinery**, because it is the part that makes the output usable:

- Everything claimed must be traceable to `data/profile/candidate.md`
- Your profile can carry explicit *honesty notes* — what you did **not** do,
  phrasings to avoid, claims that would be a stretch. They never appear in a
  document; they stop one from being written
- A reviewer agent researches the company and must mark each claim `verified`
  with a source or `unverified`. Unverified claims are re-checked or cut
- A verification checklist runs on every document and reports pass/fail

---

## 2. Install

Run `/doctor` at any time; it tells you what is missing and the command to fix
it. This section is the long version.

### Required

| | Why | How |
|---|---|---|
| **Claude Code** | This is a workspace for it | `npm install -g @anthropic-ai/claude-code` |
| **Python 3.11+** | The tooling; standard library only, no `pip install` needed | [python.org](https://www.python.org/downloads/) |
| **pandoc** | Markdown → DOCX | Windows `winget install JohnMacFarlane.Pandoc` · macOS `brew install pandoc` · Debian `sudo apt install pandoc` |
| **MS Word _or_ LibreOffice** | DOCX → PDF | Word: any Office 2016+ desktop install. LibreOffice: `winget install TheDocumentFoundation.LibreOffice` · `brew install --cask libreoffice` · `sudo apt install libreoffice` |

3.11 is the floor because the tooling reads its configuration with `tomllib`,
which entered the standard library there.

Word is preferred where it exists: it renders exactly and reports exact page
counts. LibreOffice works everywhere and is the only path on macOS and Linux.

### Worth having

| | Why |
|---|---|
| **pypdf** (`pip install pypdf`) or **poppler** (`pdftotext`) | Exact page counts and the ATS text check on the LibreOffice path. Without either, page counts are approximate and say so |
| **A Gemini API key** (`GEMINI_API_KEY` in `.env`) | Moves bulk research and ranking off Claude's context. See [§11](#11-delegating-to-gemini) |

### Fonts

The document design uses Roboto and Roboto Light, shipped in `templates/fonts/`.

- **Windows** — installed per-user automatically the first time `build.ps1` runs
- **macOS** — open the files in Font Book
- **Linux** — `cp templates/fonts/*.ttf ~/.local/share/fonts/ && fc-cache -f`

Without them documents render with substitutes and the layout shifts.

### Get the repository

```bash
git clone <your-fork-url> careerforge
cd careerforge
claude
```

---

## 3. First run

Claude will tell you the workspace is not set up. Run:

```
/setup
```

### What happens

**Step one is `/doctor`** — you see what is missing before spending time on an
interview.

**Then it offers two routes.**

**Route A — your documents.** Drop everything you have into `data/documents/`: CVs
(including old ones), certificates, reference letters, performance reviews,
project write-ups. Any readable format. Claude reads them and asks about gaps.

This is the better route, and the reason is not convenience. An old CV usually
describes work a later CV had to cut for space — and that cut material is
exactly what makes a *tailored* application possible later.

**Route B — an interview.** Nine sections, conversational: identity, education,
experience, skills, projects, publications, behavioural profile, goals, and
search configuration.

Either way, section 9 configures what `/scrape` searches for, and proactively
suggests role types your history supports that you may not have considered.

### Where things land

| Path | What |
|---|---|
| `data/profile/candidate.md` | Identity, education, experience, projects, skills |
| `data/profile/behavioral.md` | How you work, strengths, ideal environment |
| `data/profile/evaluation.md` | Match areas, goals, location rules, salary floor, sector filter |
| `data/profile/interview-prep.md` | STAR examples from real experience |
| `data/profile/cv_master.md` | Your master CV |
| `data/profile/cover_letter_master.md` | The letter skeleton |
| `data/profile/search-queries.md` | What `/scrape` looks for |
| `data/config/config.toml` | Locale, statuses, page limits, engine |
| `data/state/careerforge.db` | The tracker |

**All of it is gitignored.** After `/setup`, `git status` should be empty. If it
is not, something wrote to a framework file, which is a bug — say so.

That separation is the reason you can pull an update to CareerForge without it
ever conflicting with your own content.

### Re-running parts

```
/setup --section search        # re-tune searches as priorities change
/setup --section experience
/setup --section skills
```

---

## 4. Your profile is the product

This is the section that decides whether the output is worth sending.

The system can only recombine what you gave it. A profile built from job titles
produces applications built from job titles.

### Thin versus thick

A thin entry:

```markdown
- **Senior Frontend Developer** (2022–2025) — Acme
  - Worked on the main product
  - Used Vue and TypeScript
```

There is nothing here to tailor. Every application drawn from it says the same
thing.

The same role, written usefully:

```markdown
- **Senior Frontend Developer** (Nov 2022–Apr 2025) — **Acme** (Lisbon)
  - Migrated the catalogue to Astro + Solid.js, halving content load time
  - Built the micro-frontend split: extracted shared components into a
    standalone library with independent release cycles
  - Built the branching-video player solo — creator-side scenario editor and
    player-side preloading. Ran ~6 months in production; engagement stayed low
    and it was deliberately not ported during the redesign
    *Frontend only — the scenario storage and API were the team's. Web player
    only, no native VR claim. No usage numbers survive; never invent any.*
  - Replaced scroll listeners with Intersection Observer; fixed a setInterval
    memory leak
```

Now an application for a performance role leads with the load-time and memory
work; one for an architecture role leads with the micro-frontend split; and the
branching-video story is available for "tell me about a product experiment that
did not work" — a question most candidates answer badly.

### Honesty notes

The italic note in that example is the most valuable line in it.

Write, per role or project, what must **not** be claimed: work that was
somebody else's, a system you integrated with rather than built, a project
without users, a number nobody can substantiate any more.

They never appear in a document. They exist so a plausible overstatement is
never written in the first place — which matters because you are the one who
has to defend it in the interview.

### What to include

- **Every role**, with what you actually did — not the job description
- **Numbers**, wherever they are defensible. "Cut build time 90%" beats
  "improved build performance". If the number is not defensible, leave it out
- **Projects that failed**, and why. They are strong interview material and
  most candidates have none prepared
- **Skills in context.** Not "Python", but where and for what
- **What energised you, and what drained you.** This directly shapes how fit is
  scored, and it is the part people skip

---

## 5. Finding work

### `/scrape` — search the boards

```
/scrape                 # top three priority categories
/scrape broad           # every category
/scrape fintech         # prioritise one category
```

Searches the boards in `data/profile/search-queries.md`, drops anything already
seen — scraped before, scored, declined in `/apply`, applied to — and records
what is new. Everything seen lives in one table in the tracker, keyed on the
posting URL with tracking parameters stripped, so the same link copied two
ways is one posting.

Scraping is deliberately cheap. It does not score anything.

### `/rank` — score what it found

```
/rank                   # score the unscored
/rank --all             # re-score everything, including expired
/rank --min-score 60    # show only these
```

Scores each posting and **writes the result back** into the table: strengths,
gaps, verdict, deadline, and the status moves from `new` to `ranked`. A finding
that only reached the console is one you pay for again next week.

Two things are gates rather than deductions:

- **Location** — a posting breaking the rules in `data/profile/evaluation.md` fails,
  whatever else it offers
- **Language** — a posting requiring a language your profile does not claim
  fails

A failed gate caps the verdict at `weak`. Scoring a role 85 and then mentioning
in passing that it requires relocation wastes your attention.

Postings past their deadline are swept out automatically.

You can also read the shortlist directly, or record a verdict yourself:

```bash
python tools/shortlist.py show --min-score 60
python tools/shortlist.py show --unscored
python tools/shortlist.py check <url>                  # seen before? which status? why?
python tools/shortlist.py mark --id 12 --status skipped --note "React only"
```

A verdict without a score is a coarse rating from before `/rank` existed; the
posting still counts as unscored. `/board postings` shows the same table in
the browser, with the status as a select.

---

## 6. Applying

```
/apply https://example.com/careers/senior-frontend
/apply --no-cover <url>          # skip the cover letter
/apply --cover <url>             # draft it without asking
```

Some portals block automated fetching. Paste the posting text instead — that
works identically.

### What happens, step by step

**0 — Deduplicate.** Before anything else, it checks whether you have already
applied here, or already looked at this posting and declined it. An existing
application is shown rather than started twice; a declined posting is shown
with the reason you gave, and you are asked whether to reconsider.

**1 — Evaluate fit.** Skills, experience, behavioural fit, location, career
alignment, weighted into a score with a verdict. If salary data is configured,
a benchmark too.

Then it asks whether to proceed, and whether you want a cover letter. There is
an option for *extra context* — a referral, someone you know on the team, past
work with the company. That goes into the letter, the job snapshot and the
tracker note.

**A weak verdict is a real answer.** Not applying is the correct outcome for
most postings, and the time saved is the point. Declining records the posting as
`skipped` with the deciding gap in one line, so `/scrape` never brings it back
and `/apply` never evaluates it twice.

**2 — Draft.** Creates `data/pipeline/applications/<slug>/` with:

- `job.md` — the posting as captured, plus the fit evaluation. Your safety copy
  when the posting is taken down
- `cv_<you>.md` — from your master, tailored
- `cover_letter_<you>.md` — if you asked for one, in the posting's language

**3 — Research and review.** Company research is fetched (cached for 30 days,
so the second application to a company is free) — including what the company
pays for this role, if anything is published. A figure for the posting's own
location is recorded in your salary benchmark with its source; a figure for
some other market is not, and the company is marked *unknown* for this
location instead, with the lead kept in a note. Then an `application-reviewer`
agent critiques the drafts: missed requirements, weak phrasing, company angles,
and a verification checklist.

It must mark each company claim `verified` with a source or `unverified`.

**4 — Revise.** Suggestions are applied in the reviewer's priority order, within
the page budget. Anything that would fabricate experience is rejected. Anything
`unverified` is confirmed independently or cut.

**5 — Build.** Markdown → DOCX → PDF, with page limits enforced. Over the limit
means cutting content and rebuilding — never shrinking the template to fit.

Then the **ATS check** reads the text layer back out of the PDF: replacement
characters, ligatures that destroy extraction, a surviving email and phone,
every section heading, and the posting keywords. This catches a document that
looks perfect and parses as nothing.

Findings are warnings, not errors — you may knowingly ship a design a parser
dislikes.

**6 — Record.** Added to the tracker with status `draft`. Attachments stay empty
until you confirm it was actually sent, so the tracker holds the version the
employer received rather than a draft you later edited.

**7 — Present.** The verification checklist pass/fail, the key tailoring
decisions, the files with page counts.

### Then you send it

Open the PDF, read it, submit it on the employer's site. Come back and say so:

```
I submitted the application to Acme
```

Status moves to `applied`, the event is recorded, the PDFs are attached.

### Editing by hand

Every document exists as `.md` (source), `.docx` (editable), `.pdf` (what you
send). Edit the `.docx` in Word, then rebuild:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build.ps1 -Path data\pipeline\applications\<slug>
```
```bash
tools/build.sh data/pipeline/applications/<slug>
```

The build sees the DOCX is newer than the Markdown and refreshes **only the
PDF**, so your edits survive. `-Force` / `--force` rebuilds from Markdown and
discards them — you will be asked before that happens.

---

## 7. After you apply

### Status drives the folder

Every application's documents live in a folder. Which directory holds it is
decided by its status, never set separately:

| Directory | Meaning |
|---|---|
| `data/pipeline/applications/` | Applied, waiting |
| `data/pipeline/processing/` | They replied; interviews under way |
| `data/pipeline/rejected/` | Closed: rejection, silence, or withdrawn |

```bash
python tools/tracker.py set-status acme screening
```

changes the status **and moves the folder**, in one operation. Never move a
folder by hand.

If the target name is taken, the move is refused and nothing changes.
Overwriting an application's documents is not a recoverable mistake, so it is
not done silently.

Statuses are yours: `python tools/tracker.py statuses` lists what is configured,
and `data/config/config.toml` is where you rename, add or translate them.

### `/board` — the kanban, and the postings table

```
/board
/board postings
```

Opens `http://127.0.0.1:8765` — columns by status, drag a card to change it.
Dragging moves the folder too.

`/board postings` opens the second page on the same server: every posting ever
seen, as a table. Company, title, the status as a select you change in place,
score and verdict from `/rank`, deadline, source, a note that edits in place,
and the application it became. Filters for status (open postings by default),
minimum score, and a search box.

Loopback only, and it refuses requests whose `Host` header is not loopback,
because it writes to disk.

If the tracker changed elsewhere while the tab was open, the board says so and
reloads rather than overwriting.

### `/track` — from the chat

```
/track                  # the board, as text
/track acme             # one application in full
/track acme screening   # change status, with confirmation
/track stale            # gone quiet past your cutoff
```

### `/mailsync` — find replies you missed

```
/mailsync
/mailsync --since 2026-07-01
```

Reads the mailbox you apply from over IMAP and matches messages to open
applications. Configure it under `[mail]` in `data/config/config.toml`, with an app
password in `MAIL_PASSWORD` or `.mail_password`.

**Read-only, by construction.** The mailbox is opened read-only and messages are
fetched with `BODY.PEEK`: nothing is marked read, moved, flagged or deleted.

It proposes and never decides. Classification from subject lines is a heuristic
— a "we regret" can be a rejection for one role inside an offer for another — so
every finding comes with the phrase that triggered it, and you confirm each one.

The most valuable thing it finds: an application still marked `draft` that the
employer has already replied to. That is one you sent and never recorded.

### `/triage` — reconcile

```
/triage
```

Finds folders in the wrong stage directory, tracker rows with no folder, folders
with no row, and applications silent past your cutoff (30 days by default).
Proposes fixes; changes nothing without confirmation.

---

## 8. Interviews

```
/interview acme                    # infers the stage
/interview acme system-design
/interview acme tech --mock
```

Stages: `screening`, `tech`, `system-design`, `manager`, `final`. Each needs
different preparation, which is the point — a screening call and a design round
have almost nothing in common.

If the stage is inferred it says so, and you can correct it. Guessing wrong
wastes the whole preparation.

Each run produces likely questions drawn from **this** posting and **this**
company's research, an answer for each grounded in `data/profile/interview-prep.md`,
questions worth asking them, and your known weak points with an honest answer
for each.

**It will not invent a story.** If nothing in your profile fits a likely
question, it says so and marks it as a gap to prepare. A fabricated anecdote
collapses the moment it is probed.

`--mock` runs it as an interview: one question, your answer, a direct critique.
It is meant to be uncomfortable. A mock interview that praises everything builds
confidence in an answer that will not survive contact.

Prep files are written into the application folder as `interview_prep_<stage>.md`
and never overwrite an earlier round.

**Afterwards, tell it how it went.** What was actually asked gets appended, which
is what makes the next round — and the next company — cheaper to prepare for.

---

## 9. Growing the profile

The profile is not written once.

```
/expand                    # documents and public sources
/expand --documents
/expand --github yourname
```

Drop something new into `data/documents/` and run `/expand`. It reads what is not yet
reflected in the profile and proposes additions in three groups: new material,
better evidence for something already claimed, and contradictions between a
document and the profile.

**Everything is a proposal you confirm.** An inferred skill is exactly the claim
you would have to defend later.

For public sources it distinguishes what a finding proves from what it does not.
A repository with no tests and no users is evidence of interest, not of
production experience — and it will say so rather than write it up as the
latter.

---

## 10. Configuration reference

`data/config/config.toml`, gitignored. Start from `data/config/config.example.toml`, which
documents every key inline.

| Key | Read by | Notes |
|---|---|---|
| `locale` | everything that shows a status | Picks which label from the `[[statuses]]` tables |
| `cover_letter_language` | `/apply` | `"posting"` matches each posting's language |
| `documents.cv_filename`, `cover_filename` | `/apply`, build scripts | `{slug}` is `documents.name_slug` |
| `documents.name_slug` | the above | Your name in filenames: `cv_jane_doe.md` |
| `documents.cv_max_pages`, `cover_max_pages` | both build scripts | One source of truth, so they cannot drift |
| `documents.engine` | build scripts | `auto` prefers Word, falls back to LibreOffice |
| `tracker.stale_after_days` | `/triage`, `list --stale` | When silence counts as dead |
| `[[statuses]]` | tracker, board, `/triage`, Notion provisioning | `id`, `stage`, per-locale `labels`, optional `terminal` |
| `[[work_modes]]`, `[[event_types]]`, `[[outcomes]]` | tracker validation, board dropdowns, Notion provisioning | Same shape. Edit one after provisioning the mirror and run `notion_sync.py sync-options` |
| `[mail]` | `/mailsync` | `host`, `port`, `user`, `mailbox`, `ssl`. Password never goes here |
| `[gemini]` | `tools/gemini.py` | See [§11](#11-delegating-to-gemini) |
| `[notion]` | `tools/notion_sync.py` | Optional mirror; off by default |
| `[salary]` | `tools/salary_lookup.py` | Legal forms and region words stripped when matching company names |

### Statuses are yours

The default funnel suits most people, but nothing here is fixed:

```toml
[[statuses]]
id = "screening"
stage = "processing"
labels = { en = "Screening", ru = "📞 Скрининг" }
```

`id` is what the database stores. `stage` decides which folder the application
lives in. `labels` is what you see. Rename, translate, add, remove — the tracker,
the board and `/triage` all read the same table.

### Secrets

One `.env` file in the repo root holds them all. The tools load it themselves —
a real environment variable always wins over the file.

```
GEMINI_API_KEY=...        # the Gemini API (research, extraction, ranking)
NOTION_KEY=...            # the Notion mirror (NOTION_TOKEN also accepted)
MAIL_PASSWORD=...         # IMAP, an app password
```

`.env` is gitignored, and `/doctor` shows which keys it holds — names only,
never values. The legacy `.notion_token` and `.mail_password` files still work
as fallbacks.

Use app passwords, never account passwords: they can be revoked on their own.

---

## 11. Delegating to Gemini

Optional. Everything works without it.

Research, extraction and batch ranking are high-volume and low-judgement — the
work that should not be spending Claude's context. With a Gemini API key,
they go there instead — straight to the Gemini API, no CLI in between.

### Setup

Get a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
and put it in `.env`:

```
GEMINI_API_KEY=...
```

Then in `data/config/config.toml`:

```toml
[gemini]
enabled = true
model = "gemini-2.5-flash"
timeout_seconds = 300
tasks = ["research", "extract", "rank", "summarize"]
```

The model is called exactly as named. Company research attaches Google Search
as a tool, which needs a key whose project has that quota; the free tier's
per-model daily limits are small, and a quota refusal is reported once, not
retried into a hang.

Check it:

```bash
python tools/gemini.py check
```

### What moves, and what does not

| To Gemini | Stays with Claude |
|---|---|
| Company research and news, with sources | Writing and tailoring your CV and letter |
| Job posting → structured fields | The final fit judgement |
| Batch-scoring scraped postings | The verification checklist |
| Summarising long documents | Deciding what is honest to claim |
| Mining old sessions | Interview answers grounded in real stories |

The line is not cost. Gemini **gathers and compresses**; the judgement about
what may honestly be said about you stays in one place, against `data/profile/` and
the rules in `CLAUDE.md`.

### What it sees

Whatever the task needs, including your profile — you configured it that way.
Every prompt is logged under `data/state/gemini-log/` with the payload size and its
opening, so what left your machine stays inspectable.

To narrow it, drop a task from `tasks`. To stop entirely, `enabled = false`.

### It is never a dependency

If Gemini is missing, unauthenticated, timing out or returning nonsense, every
tool exits with code 3 and a reason, and the work is done in Claude instead —
and you are told which route was taken. A job search does not stop because a
side tool is down.

---

## 12. Troubleshooting

**`This CareerForge workspace is not set up yet`**
Expected on a fresh clone. Run `/setup`.

**`pandoc not found`**
Install it, then restart the terminal so `PATH` refreshes.

**`No PDF engine available`**
Neither Word nor LibreOffice was found. Install one. On Windows, Word must open
normally — a pending licence dialog or a stuck modal blocks automation.

**Page count looks wrong**
On the LibreOffice path, install `pypdf` or poppler's `pdfinfo`. The fallback
warns you when it is guessing.

**`no PDF text extractor available`**
The ATS check needs `pypdf` or `pdftotext`. It is skipped without them; the
build still succeeds.

**ATS check reports ligatures**
Your source contains typographic ligatures (`ﬁ`, `ﬂ`) that render fine and
destroy text extraction. Replace them with plain letters in the Markdown.

**`cannot move applications/acme -> rejected/acme: target already exists`**
Deliberate: nothing is overwritten. Merge or rename the two folders by hand,
then re-run the status change.

**`tracker database not found`**
`python tools/tracker.py init`

**`no configuration found`**
Copy `data/config/config.example.toml` to `data/config/config.toml`, or run `/setup`.

**Board says the application changed since you loaded it**
The tracker was updated elsewhere while the tab sat open. It reloads; retry.

**`not authenticated` from Gemini**
Set `GEMINI_API_KEY` in `.env`. Everything keeps working meanwhile.

**`quota exhausted` or `overloaded` from Gemini**
The key's project is over its quota for that model (free tiers are per model
and per day — check [ai.dev/rate-limit](https://ai.dev/rate-limit)), or the
model is under heavy demand. The tool gives up immediately rather than
retrying for minutes; the work is done in Claude instead.

**`the prompt is too long for a command line`**
A bug: bulk input should travel over stdin. Report it with the command you ran.

**Mail sync: `login failed`**
Gmail and most providers need an **app password**, not your account password.

**Mail sync matched the wrong application**
Company-name matching is the weakest of its three signals. Correct it — it
proposes, you decide.

**`git status` is not empty after `/setup`**
A bug. Something wrote to a framework file. Revert it and say so; the change
belongs in `data/profile/` or `data/config/config.toml`.

---

## 13. Extending it

| To change | Edit |
|---|---|
| What `/setup` asks for | `data/profile.example/*.md` |
| Statuses, labels, page limits, locale | `data/config/config.toml` |
| How postings are scored | `.claude/skills/job-application-assistant/references/job-evaluation.md` |
| Tone and phrasing rules | `.claude/skills/job-application-assistant/references/writing-style.md` |
| CV structure and markup | `.claude/skills/job-application-assistant/references/cv-format.md` |
| What the reviewer looks for | `.claude/agents/application-reviewer.md` |
| Document design | `templates/reference_cv.docx` in Word, or `tools/make_reference.ps1` |

### The one rule when extending

Framework files are committed; your data is not. A workflow that wants to write
to a framework file is a bug, not a reason to write to one.

Anything user-specific belongs in `data/profile/` or `data/config/config.toml`. That is
what keeps `git pull` from ever conflicting with your content.

### The tools

Commands orchestrate; these do the work. Each runs standalone and each takes
`--help`.

| Tool | What it does |
|---|---|
| `tracker.py` | The tracker: applications, companies, events, attachments. Also the shared data layer the board and the Notion adapter import |
| `board.py` | The kanban server |
| `shortlist.py` | The shortlist: every posting seen, what `/rank` scored, what was decided and why |
| `research.py` | Cached company research, with or without Gemini |
| `atscheck.py` | Reads a built PDF's text layer back out and checks it survives |
| `pagecount.py` | Page counts without Word: pypdf, then `pdfinfo`, then an approximate fallback that says so |
| `build.ps1` / `build.sh` | Markdown → DOCX → PDF, per platform |
| `config_get.py` | Lets the build scripts read `config.toml` without a TOML parser of their own |
| `console.py` | Forces output to UTF-8, so a redirected run does not die on an emoji in a status label |
| `doctor.py` | The environment check |
| `gemini.py` | The single wrapper around the Gemini API |
| `mailsync.py` | IMAP reconciliation |
| `notion_sync.py` | The optional Notion mirror: provision, adopt, sync-options, import, push |
| `salary_lookup.py`, `convert_salary_excel.py` | Salary benchmarking against data you supply — and data research finds (`add`) |
| `session_digest.py` | Mines old Claude Code transcripts into `data/profile/history.md` |

### Carrying history over from another workspace

If you have been doing this in a different Claude Code project, the reasoning
in those sessions is worth keeping — the standing rules, the positioning
decisions, the things you decided never to claim.

```bash
python tools/session_digest.py extract --from C--Projects-old-workspace
python tools/session_digest.py digest
python tools/session_digest.py merge
```

`extract` filters transcripts down to human turns and assistant prose, dropping
tool calls and their results — typically about 3% of the raw bytes survive, and
that 3% is all of the reasoning. `digest` summarises each session; `merge`
combines them into `data/profile/history.md`, organised by theme.

The raw transcripts stay where they are. They are full of absolute paths and
filenames that no longer exist, so resuming one in a new workspace is more
confusing than useful.

Without Gemini, each step tells you where its input is so you can do that step
in Claude instead.

### Tests

```bash
python -m unittest discover -s tests
```

Covers deduplication, the folder lifecycle including collision refusal,
optimistic locking, deadline parsing, the research cache, the ATS checks, mail
classification, company-name normalisation and the salary table's arithmetic.

One of them is a contract rather than a unit test: it runs every tool with
`--help` and fails if any of them does not answer. That promise had already
quietly stopped being true in three of them.

### Upstream

CareerForge is a fork of
[MadsLorentzen/ai-job-search](https://github.com/MadsLorentzen/ai-job-search),
which is still actively developed:

```bash
git fetch upstream
git log --oneline HEAD..upstream/master
```

Read it; do not merge it. The structures have diverged. Their bug fixes are
often worth taking, because the same defect frequently exists here by descent.

### More

- [architecture.md](architecture.md) — how the pieces fit and why
- [tracker-schema.md](tracker-schema.md) — the database, and how to browse it
- [notion-mirror.md](notion-mirror.md) — the optional Notion mirror
