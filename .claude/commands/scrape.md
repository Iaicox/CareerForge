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

## Deduplicate

Every posting ever seen — scraped, ranked, declined in `/apply`, applied to —
is in the tracker's `postings` table. Before fetching or presenting anything:

```bash
python tools/shortlist.py check <url> [<url> ...]
```

Anything it reports as known is dropped; a `skipped` one comes with the reason.

## Record what was found

Never write to the table directly. Feed the postings in:

```bash
python tools/shortlist.py add --file <postings.json>
```

Each posting: `title`, `company`, `url`, `location`, `source`, `deadline`,
`summary`; a posting you filtered out yourself carries `"status": "skipped"`
and a `"note"` saying why. Free-text deadlines (`ASAP`, `rolling`) are stored
as none rather than as unsortable text; the wording belongs in the summary if
it matters.

Extraction from a fetched page is worth delegating:

```bash
python tools/gemini.py extract-posting --file <page.html>
```

Exit code 3 means Gemini is unavailable — parse it yourself instead, and say so.

## Then hand over

Report how many are new, then suggest `/rank` to score them. Scoring is a
separate step so a scrape stays cheap and the shortlist keeps its findings
between runs. `/board postings` shows the whole table in the browser.

Never present a posting that WebSearch and WebFetch did not actually return.
