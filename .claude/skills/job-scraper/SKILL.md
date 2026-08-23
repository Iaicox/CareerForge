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

1. `job_scraper/seen_jobs.json` — create as `{"seen": {}}` if missing.
2. `profile/search-queries.md` — the query set, geography tiers and exclusions.
3. Everything already tracked, as the second dedup source:
   ```
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

Skip it if the URL, or the company + title pair, is already in `seen_jobs.json`
or already in the tracker. Pre-filter on titles and snippets — do not fetch
every search result.

## Step 3: quick fit

A rough signal only, not the full `job-evaluation.md` scoring:

- **High** — the role is built on the user's core skills
- **Medium** — adjacent to their experience
- **Low** — needs skills they do not have

## Step 4: record

Add every job fetched, presented or skipped, to `job_scraper/seen_jobs.json`:

```json
{"seen": {"<url>": {"title": "…", "company": "…", "url": "…",
                    "first_seen": "YYYY-MM-DD", "fit": "high",
                    "status": "new|skipped|evaluated"}}}
```

Present only what is new.

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
