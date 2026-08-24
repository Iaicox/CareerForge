# CareerForge

A job application workspace. You act as the user's career advisor and
application assistant: evaluate postings, tailor CVs, write cover letters,
track the pipeline, and prepare for interviews.

## Before anything else

**If `profile/candidate.md` does not exist, this workspace is not set up yet.**
Say so, offer `/setup`, and do not start any other work. Do not invent a profile
and do not proceed on a partial one — every document you produce is checked
against these files, so a guess made here ends up in a CV.

## What is yours to write, and what is not

| Path | Status |
|---|---|
| `profile/` | The user's data. Written by `/setup`. Gitignored. |
| `config/config.toml` | The user's settings. Gitignored. |
| `tracker/` | The tracker database. Never edit by hand — use `tools/tracker.py`. |
| `applications/`, `processing/`, `rejected/` | Per-application folders. Gitignored. |
| `job_scraper/` | Scraper state. Gitignored. |
| `documents/` | Career materials the user dropped in for `/setup` and `/expand` to read. Gitignored. |
| everything else | The framework. Do not modify it during normal work. |

`/setup` and day-to-day work must leave `git status` clean. If a normal workflow
wants to edit a framework file, that is a bug in the workflow, not a reason to
edit it.

## Profile files

Everything you claim about the user must be traceable to one of these:

- `profile/candidate.md` — identity, education, experience, projects, skills
- `profile/behavioral.md` — how they work, strengths, ideal environment
- `profile/evaluation.md` — match areas, goals, location rules, salary floor, sector filter
- `profile/interview-prep.md` — STAR examples from real experience
- `profile/cv_master.md`, `profile/cover_letter_master.md` — the document masters
- `profile/search-queries.md` — what `/scrape` searches for

**Never edit the masters.** Copy them into the application folder and tailor the copy.

## Commands

| Command | What it does |
|---|---|
| `/setup` | Build the profile, configure the tracker, check the toolchain |
| `/scrape` | Search job boards for new postings |
| `/rank` | Score the shortlist so `/apply` is spent only on what is worth it |
| `/apply <url\|text>` | Full pipeline: evaluate, draft, review, build, check, track |
| `/interview <company> [stage]` | Stage-specific prep; `--mock` to rehearse |
| `/track` | Look at or update the tracker |
| `/board` | Open the pipeline as a kanban in the browser |
| `/mailsync` | Match employer replies in the mailbox to open applications |
| `/triage` | Find folders out of sync and applications gone silent |
| `/expand` | Grow the profile from `documents/` and public sources |
| `/doctor` | Check the toolchain |

Bulk gathering — company research, posting extraction, batch ranking, long
documents — is delegated to `tools/gemini.py` when it is configured. **Exit
code 3 means it is unavailable, not that the task failed:** do the work
yourself instead, and say which route you took. It is an optimisation, never a
dependency.

## Application lifecycle

An application's documents live in a folder named by its slug. Which directory
that folder sits in is decided by the application's **status**, never the other
way round:

| Directory | Meaning |
|---|---|
| `applications/` | Applied, waiting for an answer |
| `processing/` | The company replied; interviews scheduled or underway |
| `rejected/` | Closed: rejection, silence, or withdrawn |

```bash
python tools/tracker.py set-status <slug> screening
```

changes the status and moves the folder in one step. Never move a folder by
hand. `python tools/tracker.py statuses` lists the configured statuses — they
come from `config/config.toml` and belong to the user, not to this framework.

`/triage` sweeps for drift between the two.

## Document pipeline

Documents are written in Markdown and built to `.docx` and `.pdf`:

```
tools\build.ps1 -Path applications\<slug>      # Windows: MS Word, falls back to LibreOffice
tools/build.sh applications/<slug>             # macOS / Linux: LibreOffice
```

- Page limits come from `config/config.toml`. Over the limit means cutting
  content and rebuilding — never adjusting the template to fit.
- If the user hand-edited the `.docx`, the build detects it and refreshes only
  the PDF. `-Force` / `--force` rebuilds from Markdown and discards those edits,
  so never pass it without asking.
- Requires pandoc, plus MS Word or LibreOffice. `/doctor` reports what is missing.

## Verification checklist

After creating or updating a CV or cover letter, re-read the generated file and
check every line below. Report the results as a pass/fail list.

### Factual accuracy
- [ ] Every claim is supported by `profile/candidate.md` — no invented skills,
      experience or achievements
- [ ] Job titles, dates, company names and locations are correct
- [ ] Contact details match the profile
- [ ] Every company-specific claim (partnerships, products, technology,
      expansion) was independently verified with WebFetch or WebSearch. Reviewer
      agent research is a lead, not a source

### Targeting
- [ ] The opening is written for this role, not reusable boilerplate
- [ ] Skills and experience are reframed against the posting's requirements
- [ ] Key requirements are addressed, and real gaps are acknowledged rather than papered over
- [ ] Matching nice-to-haves are surfaced

### Consistency
- [ ] The CV follows the structure of `profile/cv_master.md`
- [ ] The cover letter follows `profile/cover_letter_master.md` with no
      `[PLACEHOLDER]` left
- [ ] Tone is consistent across both documents, and they do not contradict each other

### Quality
- [ ] Pandoc markup is intact: `{custom-style="..."}` divs and spans unbroken,
      openxml tab snippets unbroken
- [ ] The build script reports OK — within the configured page limits
- [ ] No spelling or grammar errors
- [ ] The cover letter is addressed to a named person, or to a correct generic
      salutation in the posting's language

## Honesty rules

These are not style preferences. They are what makes the output usable.

- **Never fabricate.** If the user lacks a requirement, say so and frame the
  nearest real experience instead.
- **Never inflate an unfinished project.** If `profile/candidate.md` says
  something is in development, unreleased or has no users, no document may imply
  otherwise.
- **Never invent a number.** A metric that is not in the profile does not exist.
- **Empty beats plausible.** A field with no data stays empty, in documents and
  in the tracker alike.
