---
name: job-scraper
description: Search job boards for new postings matching the user's profile, deduplicate against the tracker and previous runs, and present them with a quick fit rating. Use when the user asks to find, search or scrape for jobs, or asks whether there are new positions. Keywords - find jobs, search jobs, scrape jobs, new positions, job hunt.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, WebFetch, WebSearch, Agent, AskUserQuestion
---

# Job scraper

Searches the boards configured in `profile/search-queries.md`, drops anything
already seen or already in the tracker, and presents what is left.

## Before anything

Requires `profile/search-queries.md`. If it is missing, say so and offer
`/setup --section search`. Never invent search criteria for the user.

## Step 0: load state

`job_scraper/seen_jobs.json` is owned by `tools/shortlist.py`. Read it through
the tool and write it only through the tool — never by hand. It carries the
scores `/rank` produced, and hand-editing loses them.

1. The shortlist, as the first dedup source:
   ```bash
   python tools/shortlist.py show --include-expired --json
   ```
   The tool creates the file if it does not exist yet.
2. `profile/search-queries.md` — the query set, geography tiers and exclusions.
3. Everything already tracked, as the second dedup source:
   ```bash
   python tools/tracker.py list --json
   ```
   Collect the `url` and `company_name` + `role` pairs.

## Step 1: search

Run the queries from `search-queries.md`. Default to the top three priority
categories; run all of them if the user said "broad"; prioritise a named
category if the user gave one (`/scrape fintech`).

Postings from the last 14 days, within the configured geography tiers.

## Step 2: fetch and parse

For each promising hit, WebFetch the posting and extract title, company,
location, posting date, URL, key requirements and any deadline.

Skip it if the URL, or the company + title pair, is already in the shortlist
from step 0 or already in the tracker. Pre-filter on titles and snippets — do
not fetch every search result.

## Step 3: quick fit

A rough signal for the table below, so the user can see what is worth reading
first. Not the full `job-evaluation.md` scoring:

- **High** — the role is built on the user's core skills
- **Medium** — adjacent to their experience
- **Low** — needs skills they do not have

**This rating is for the presentation only — never write it to the shortlist.**
Scoring is `/rank`'s job, and it is the only thing that writes a score. The old
`fit` field is read for entries that predate `/rank` and is never written back.

## Step 4: record

Add every job fetched, presented or skipped. Write a JSON list and feed it in:

```bash
python tools/shortlist.py add --file <postings.json>
```

Each posting: `title`, `company`, `url`, `location`, `source`, `deadline`,
`summary`. The last four are not optional in practice — `/rank` scores on
`location` and `summary`, and `shortlist.py sweep` can only expire a posting
that has a `deadline`. Free-text deadlines (`ASAP`, `rolling`) are stored as
none rather than as unsortable text; put the wording in the summary if it
matters.

`add` keys on the URL, sets `first_seen` and `status`, and skips what is
already there, so it is safe to pass the whole batch. Present only what it
reports as new.

## Step 5: present

Save the shortlist to `job_scraper/runs/YYYY-MM-DD.md` (suffix `-2` if today's
file exists), then show the same table:

```
## New matches — YYYY-MM-DD
Found X new positions (Y high, Z medium, W low).

| # | Fit | Title | Company | Location | Deadline | URL |
```

For each high-match role add two or three bullets: why it matches, what to
check, any red flag. Then ask which ones to evaluate in detail, and run
`/apply` on the user's picks.

## Rules

1. **Never fabricate a posting.** Only what WebSearch and WebFetch actually returned.
2. **Always dedup against both sources** before presenting anything.
3. **Respect the configured geography and the sector exclusions** in `search-queries.md`.
4. **Open positions only** — skip expired or closed listings.
5. **Be frugal with WebFetch**; parallel searches are fine, mass fetching is not.
6. **Never hand-edit `seen_jobs.json`.** It goes through `tools/shortlist.py`,
   which owns the key, the deadline parsing and the scores `/rank` wrote.
