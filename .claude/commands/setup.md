---
description: Set up this workspace - build your profile, configure the tracker, check the toolchain
argument-hint: "[--section profile|skills|experience|search|tracker|notion]"
---

# /setup — onboarding

Collect the user's professional information and configure the workspace so
`/scrape` and `/apply` work immediately.

## The rule that matters

**Write only to `profile/`, `config/config.toml`, `config/notion.json` and
`tracker/`.** All four are gitignored. Never modify `CLAUDE.md`, the files under
`.claude/`, `templates/`, `tools/` or `profile.example/` — those are the
framework, and a user who pulls an update must not get a merge conflict because
setup edited them.

If `$ARGUMENTS` contains `--section <name>`, run only that section and stop.

---

## Step 0: check the toolchain

```bash
python tools/doctor.py
```

Show the user what is missing and how to fix it. Missing pandoc or a PDF engine
does not block the interview — profile-building works without them — but say
plainly that documents cannot be built until they are installed.

---

## Step 1: choose a path

First, look in `documents/`:

```bash
ls documents/
```

If it holds anything besides `README.md`, say what you found and start there —
that is the cheapest good start available, and it beats asking the user to
paste a CV into a chat window.

> **Welcome to CareerForge.**
>
> I'll build your professional profile so I can evaluate postings, tailor CVs,
> write cover letters and prep you for interviews.
>
> **Path A — read your documents (recommended).** Drop CVs, certificates,
> reference letters and project write-ups into `documents/` and I'll read them.
> Old CVs are especially useful: they usually describe work a later CV had to
> cut for space. You can also share a file with `@` or paste text.
>
> **Path B — interview.** I'll walk you through it section by section.
>
> Which would you prefer?

### Path A: import

Read everything available, and extract identity, contact, education,
experience, skills, projects, publications and awards. Show the user what you
extracted and ask about the gaps — behavioural profile, career goals,
deal-breakers, salary floor.

A long PDF is worth delegating rather than reading into context:

```bash
python tools/gemini.py summarize --file documents/<file> \
  --question "List every role, project, technology and measurable outcome mentioned. Quote the wording used."
```

Exit code 3 means Gemini is unavailable — read it yourself.

### Path B: interview

A conversation, not a form. One topic at a time; let the user answer in their
own words and you do the structuring. Sections:

1. **Identity** — name, location, contact, languages, work-authorisation and
   commute constraints, current status
2. **Education** — degrees, institutions, years, certifications
3. **Experience** — per role: title, company, dates, location, what they
   actually did, achievements with numbers, stack. Push for detail here; this is
   the single biggest lever on output quality
4. **Skills** — languages, frameworks, domains, tools. Ask *where* each was
   used, not just whether it was
5. **Independent projects** — side projects, open source, freelance, and for
   each: is it shipped, is it public, does it have users? Honest framing now
   prevents an inflated claim later
6. **Publications and awards** — skip if not applicable
7. **Behavioural profile** — a formal assessment if they have one, otherwise:
   what environments they thrive in, what drains them, how they decide, how they
   communicate
8. **Goals and preferences** — target roles and sectors, what excites them,
   deal-breakers, salary floor and target, sectors to avoid
9. **Search configuration** — role titles to search for (3–8), the skills most
   likely to appear in postings (3–5), target companies, geography tiers (ideal
   / acceptable / borderline / too far), and which boards to use

In section 9, **also suggest role types they have not raised**, based on what
their history actually shows. Someone with deep domain knowledge plus coding may
not have considered solutions engineering; someone who has led migrations may
not have considered a platform role. This is where latent options surface.

---

## Step 2: write the profile

Each file starts from its counterpart in `profile.example/`, which carries the
expected structure and a note on what belongs in it.

| Write to | Contents |
|---|---|
| `profile/candidate.md` | Identity, contact, education, experience, projects, skills, certifications |
| `profile/behavioral.md` | Behavioural profile, strengths, ideal environment, growth areas |
| `profile/evaluation.md` | Strong/moderate/weak match areas, career goals, motivation filters, location rules, salary floor, sector filter |
| `profile/interview-prep.md` | 3–4 STAR examples drawn from real experience, plus tough-question notes |
| `profile/cv_master.md` | The master CV, with real details, keeping the pandoc markup conventions from `references/cv-format.md` |
| `profile/cover_letter_master.md` | The cover letter skeleton |
| `profile/search-queries.md` | Search queries, geography tiers and sector exclusions from section 9 |

Write what the user actually told you. An empty section is better than a
plausible invention — every later document is checked against these files, so a
fabrication here propagates into a CV.

---

## Step 3: configure

Copy `config/config.example.toml` to `config/config.toml`, then adjust:

- `locale` — the language for status labels
- `documents.name_slug` — used in document filenames (`cv_jane_doe.md`)
- `documents.cv_max_pages` / `cover_max_pages` — most markets expect 2 and 1
- `documents.engine` — leave `auto` unless doctor found a reason not to
- `[[statuses]]` — the default funnel suits most people; ask only if they say
  they track things differently
- `tracker.stale_after_days` — when silence counts as dead

Then create the database:

```bash
python tools/tracker.py init
```

---

## Step 4: optional extras

**Salary benchmarking.** If the user has salary data (a survey, a union
dataset, their own research), point them at `tools/README_SALARY_TOOL.md` for
the format and `tools/convert_salary_excel.py` for the Excel path. Skipping this
just omits the benchmark from `/apply`.

**Notion mirror.** Ask whether they want their pipeline mirrored to Notion for
phone access. Only if yes:

```bash
python tools/notion_sync.py provision --parent-page <page URL> --dry-run
python tools/notion_sync.py provision --parent-page <page URL>
```

They need an integration token first (notion.so/my-integrations, with Read,
Update and Insert content), stored in `NOTION_TOKEN` or `.notion_token`, and the
parent page shared with it. Then set `notion.enabled = true`.

---

## Step 5: confirm

Re-run `python tools/doctor.py` and show the result. Then:

> **Setup complete.** Written: `profile/…`, `config/config.toml`,
> `tracker/careerforge.db`.
>
> - `/scrape` — search job boards now, then `/rank` to score what it finds
> - `/apply <url>` — run the full application workflow on a posting
> - `/board` — open your pipeline as a kanban
> - `/expand` — grow the profile as you add documents
> - `/setup --section search` — re-tune your searches as priorities change

Finally, confirm that nothing outside the ignored paths changed:

```bash
git status --short
```

Empty output is the expected result. If anything shows up, you edited a
framework file — revert it and put the change in `profile/` or
`config/config.toml` instead.
