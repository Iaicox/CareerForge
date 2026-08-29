---
name: job-application-assistant
description: Evaluate a job posting against the user's profile, tailor a CV, draft a cover letter, build DOCX/PDF, and prepare for interviews. Use whenever the user shares a job posting, asks about job fit, asks for a CV or cover letter, or asks to prepare for an interview. Keywords - job posting, vacancy, application, apply, CV, resume, cover letter, interview prep, job fit.
allowed-tools: Read, Glob, Grep, WebFetch, WebSearch, Edit, Write, Bash, AskUserQuestion, Agent
---

# Job application assistant

The `/apply` command orchestrates the full pipeline. This skill is what runs
when the user asks for a piece of it directly.

## Before anything

If `data/profile/candidate.md` does not exist, the workspace is not set up. Say so
and offer `/setup`. Do not invent a profile, and do not proceed with a partial one.

`data/profile/` is the single source of truth about the user. Every claim in every
document must be traceable to it.

## Workflow

### 1. Evaluate fit

- Fetch the posting (WebFetch for a URL; use the text as given if pasted).
- Score it with `references/job-evaluation.md`, using the user's own match
  areas, goals and deal-breakers from `data/profile/evaluation.md`.
- Check for a duplicate before doing any work:
  `python tools/tracker.py find --url <url> --company "<Company>" --role "<Role>"`
- Present the evaluation table and verdict, then ask whether to proceed and
  whether a cover letter is wanted.

### 2. Tailor the CV

- Copy `data/profile/cv_master.md` into the application folder. **Never edit the master.**
- Follow `references/cv-format.md`; keep the pandoc markup conventions exactly.
- Tailor the profile statement, reorder and reframe bullets against the posting.
- Write the `job.md` snapshot alongside it.

### 3. Draft the cover letter (optional)

- Copy `data/profile/cover_letter_master.md`; replace every placeholder.
- Follow `references/writing-style.md` and `references/cover-letter-format.md`.
- Match the posting's language unless `data/config/config.toml` overrides it.

### 4. Build

```
tools/build.ps1 -Path <stage>/<slug>          # Windows
tools/build.sh <stage>/<slug>                 # macOS / Linux
```

Enforce the page limits from `data/config/config.toml`. Over budget means cutting
content and rebuilding, not shrinking the font.

### 5. Record in the tracker

Use the `application-tracker` skill. In short: dedup, add the application,
attach the built PDFs once the user confirms it was actually sent.

### 6. Interview preparation

An interview invitation means the folder belongs in `data/pipeline/processing/` — change the
status and the folder follows:

```
python tools/tracker.py set-status <slug> screening
```

Write prep files into the folder as `interview_prep.md`, and
`interview_prep_<round>.md` for later rounds. Ground every STAR answer in
`data/profile/interview-prep.md`; never invent an anecdote.

## Reference files

| File | Purpose |
|---|---|
| `references/job-evaluation.md` | Scoring dimensions, weights, thresholds |
| `references/writing-style.md` | Tone, structure, things never to write |
| `references/cv-format.md` | CV structure and pandoc markup conventions |
| `references/cover-letter-format.md` | Cover letter structure and rules |

User data lives outside this skill, in `data/profile/`: `candidate.md`,
`behavioral.md`, `evaluation.md`, `interview-prep.md`, `cv_master.md`,
`cover_letter_master.md`.

## Individual requests

- "Evaluate this posting" — step 1 only
- "Write a CV for X" — step 2 + build
- "Write a cover letter for X" — step 3 + build
- "Prepare me for the interview at X" — step 6
- "I sent the application to X" — set status `applied`, add an `applied` event,
  attach the PDFs
