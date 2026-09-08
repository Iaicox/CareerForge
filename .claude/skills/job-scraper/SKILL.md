---
name: job-scraper
description: Search job boards for new postings matching the user's profile, deduplicate against the tracker and previous runs, and present them with a quick fit rating. Use when the user asks to find, search or scrape for jobs, or asks whether there are new positions. Keywords - find jobs, search jobs, scrape jobs, new positions, job hunt.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, WebFetch, WebSearch, Agent, AskUserQuestion
---

# Job scraper

Searches the boards configured in `data/profile/search-queries.md`, drops anything
already seen — scraped, ranked, evaluated, applied to or skipped — and presents
what is left.

## Before anything

Requires `data/profile/search-queries.md`. If it is missing, say so and offer
`/setup --section search`. Never invent search criteria for the user.

## Step 0: load state

Every posting ever seen lives in the tracker database, in the `postings`
table, owned by `tools/shortlist.py`. It holds what earlier scrapes found, what
`/rank` scored, what `/apply` evaluated and declined (with the reason), and
every application — so it is the only dedup source there is.

1. `data/profile/search-queries.md` — the query set, geography tiers and exclusions.
2. A look at what is already open, so the presentation can say what is new
   versus what was already waiting:
   ```bash
   python tools/shortlist.py show --json
   ```

## Step 1: search

Run the queries from `search-queries.md`. Default to the top three priority
categories; run all of them if the user said "broad"; prioritise a named
category if the user gave one (`/scrape fintech`).

Postings from the last 14 days, within the configured geography tiers.

## Step 2: fetch and parse

For each promising hit, WebFetch the posting and extract title, company,
location, posting date, URL, key requirements and any deadline.

Before fetching, ask the tool whether the URLs are known — it normalises them,
so a link with a different `?trk=` or `utm_` parameter is still the same
posting:

```bash
python tools/shortlist.py check <url> [<url> ...]
```

Skip everything it reports as known: `applied`, `skipped` (the note says why),
`expired`, or simply already in the shortlist. Pre-filter on titles and
snippets — do not fetch every search result.

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

Add every job fetched, presented or filtered out. Write a JSON list and feed
it in:

```bash
python tools/shortlist.py add --file <postings.json>
```

Each posting: `title`, `company`, `url`, `location`, `source`, `deadline`,
`summary`, and `salary` when the listing names one. `location`, `summary` and
`deadline` are not optional in practice — `/rank` scores on the first two, and
`shortlist.py sweep` can only expire a posting that has a `deadline`. Free-text
deadlines (`ASAP`, `rolling`) are stored as none rather than as unsortable
text; put the wording in the summary if it matters.

`salary` is the pay **as the listing states it**, copied word for word —
`"45.000-60.000 EUR/year, 14 payments"`, `"from £70k"`. Not normalised, not
converted, not a guess: a listing that names no figure has no `salary`, and the
board falls back to the market benchmark for that company. A number is accepted
too, and `0` is read as "not disclosed" rather than as a salary of zero.

Getting it right here is worth the attention: this is the step that sees the
listing. A figure missed now can still be filled in later — a re-scrape fills
an empty one, and `shortlist.py mark --salary` and `tracker.py set-salary` set
one by hand — but none of those re-read the page for you.

A posting you filtered out yourself — wrong country, a required language, a
closed listing — goes in with `"status": "skipped"` and a one-line `"note"`
saying why, so the next scrape does not fetch it again and the reason is there
when it comes up.

`add` keys on the normalised URL, sets `first_seen` and `status`, and skips
what is already there, so it is safe to pass the whole batch. Present only
what it reports as new.

## Step 5: present

The table is the record of the run — `/board postings` shows it in the
browser, `shortlist.py show` in the terminal. Present:

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
2. **Always `check` before fetching and before presenting.** The postings table
   is the one source: applications, verdicts and earlier scrapes alike.
3. **Respect the configured geography and the sector exclusions** in `search-queries.md`.
4. **Open positions only** — skip expired or closed listings.
5. **Be frugal with WebFetch**; parallel searches are fine, mass fetching is not.
6. **Never write to the `postings` table directly.** It goes through
   `tools/shortlist.py`, which owns the URL key, the deadline parsing and the
   scores `/rank` wrote.
