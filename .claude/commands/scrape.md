---
description: Search job boards for new postings matching your profile
argument-hint: "[broad | <focus area>]"
---

# /scrape

Run the **job-scraper** skill (`.claude/skills/job-scraper/SKILL.md`) with
`$ARGUMENTS` as the mode:

- no arguments — the top three priority categories from `profile/search-queries.md`
- `broad` — every category
- anything else (`fintech`, `tech lead`) — prioritise that category

Follow the skill's steps exactly: load state (`seen_jobs.json` plus
`tracker.py list --json` for dedup), search, fetch and parse, quick fit rating,
record everything seen, save the shortlist to `job_scraper/runs/`, present the
table, and offer to run `/apply` on the user's picks.

If `profile/search-queries.md` does not exist, stop and offer
`/setup --section search`.
