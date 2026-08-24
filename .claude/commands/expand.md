---
description: Grow the profile from documents you have added and from public sources
argument-hint: "[--documents | --public | --github <user> | --site <url>]"
---

# /expand — fill gaps in the profile

`/setup` builds the profile once. This goes back over it when there is more
material: a document you dropped into `documents/`, a repository you shipped, a
certificate you earned.

`$ARGUMENTS` narrows the source: `--documents`, `--public`, `--github <user>`,
`--site <url>`. With none, do both, documents first.

---

## The rule

**Everything found here is a proposal, not a fact.** Present it, let the user
confirm or correct it, and only then write to `profile/`.

An inferred skill is exactly the claim that has to be defended in an interview.
"You have Terraform in three repositories" is a reasonable thing to notice and a
terrible thing to write into a CV unprompted.

---

## Step 0: know what is already there

Read `profile/candidate.md`. If it does not exist, this is the wrong command —
offer `/setup`.

Note what is thin: roles with one bullet, skills with no context, years with
nothing in them.

## Step 1: documents (`--documents`)

List `documents/`, then read anything not yet reflected in the profile.

```bash
ls documents/
```

For each file, extract what the profile is missing:

- **Older CVs** — the detail a later CV cut for space. This is the richest
  source in the folder and the most often skipped
- **Performance reviews and reference letters** — how others describe their
  work, which is usually more concrete and more usable than self-description
- **Certificates** — name, issuer, date, verification link
- **Project write-ups** — what was built, the stack, the outcome

Long PDFs are worth delegating:

```bash
python tools/gemini.py summarize --file documents/<file> \
  --question "List every role, project, technology and measurable outcome mentioned. Quote the wording used."
```

Exit code 3 means Gemini is unavailable — read it yourself.

## Step 2: public sources (`--public`)

Only sources the user names or that are already listed in
`profile/candidate.md`. Do not go looking for them by name across the web.

- **GitHub** — public repositories, primary languages, what each project does,
  activity. Distinguish a maintained project from a one-weekend experiment;
  presenting the second as the first is the kind of claim that unravels
- **Personal site or portfolio** — case studies and project descriptions
- **Published packages** — npm, PyPI: downloads, last release, whether it works
- **Google Scholar or ORCID** — publications and citations, if relevant

For each finding, record what it proves and what it does not. A repository with
no tests and no users is evidence of interest, not of production experience.

## Step 3: present

Group by what it changes:

**New material** — roles, projects, skills or certificates the profile does not
mention yet.

**Better evidence** — something already claimed, now with a number, a date or a
source behind it. This is often the most valuable category and the easiest to
overlook.

**Contradictions** — where a document disagrees with the profile. Dates,
titles, what a project actually did. Flag every one; do not quietly pick a side.

**Suggested honesty notes** — where a finding could be overstated later, propose
the boundary now, in the profile's own `*italic note*` form: what not to claim,
which phrasing to avoid.

## Step 4: write what was confirmed

Only the confirmed items, into `profile/candidate.md`, `profile/behavioral.md`
or `profile/interview-prep.md` as appropriate. Keep the existing structure.

If new material suggests search terms the user is not currently searching for,
say so and offer `/setup --section search`.

Finish with `git status --short` — it must be empty. Anything else means a
framework file was edited, which is a bug.
