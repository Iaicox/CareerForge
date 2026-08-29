---
description: Search job boards for new postings matching your profile
argument-hint: "[broad | <focus area>]"
---

# /scrape

Run the **job-scraper** skill (`.claude/skills/job-scraper/SKILL.md`) with
`$ARGUMENTS` as the mode:

- no arguments — the top three priority categories from `data/profile/search-queries.md`
- `broad` — every category
- anything else (`fintech`, `tech lead`) — prioritise that category

If `data/profile/search-queries.md` does not exist, stop and offer
`/setup --section search`.

## Deduplicate against both sources

Before presenting anything, drop what is already known:

```bash
python tools/shortlist.py show --include-expired --json   # already seen
python tools/tracker.py list --json                       # already applied to
```

## Record what was found

Do not hand-edit `seen_jobs.json`. Feed the postings in:

```bash
python tools/shortlist.py add --file <postings.json>
```

Each posting: `title`, `company`, `url`, `location`, `source`, `deadline`,
`summary`. Free-text deadlines (`ASAP`, `rolling`) are stored as none rather
than as unsortable text; the wording belongs in the summary if it matters.

Extraction from a fetched page is worth delegating:

```bash
python tools/gemini.py extract-posting --file <page.html>
```

Exit code 3 means Gemini is unavailable — parse it yourself instead, and say so.

## Then hand over

Report how many are new, then suggest `/rank` to score them. Scoring is a
separate step so a scrape stays cheap and the shortlist keeps its findings
between runs.

Never present a posting that WebSearch and WebFetch did not actually return.
