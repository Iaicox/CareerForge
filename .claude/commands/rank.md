---
description: Score everything the scraper found, so /apply is only spent on what is worth applying to
argument-hint: "[--all] [--min-score N]"
---

# /rank — score the shortlist

`/scrape` finds postings. This decides which of them deserve the hour that
`/apply` costs.

Scores persist into `data/job_scraper/seen_jobs.json`. A triage finding that only
ever reached the console is one you pay for again next week.

`$ARGUMENTS`:

- `--all` — also re-score entries already scored, and expired ones
- `--min-score N` — only show results at or above N

---

## Step 0: check the workspace

`data/profile/evaluation.md` must exist. If it does not, stop and offer `/setup`.

## Step 1: sweep expired postings

```bash
python tools/shortlist.py sweep
```

Anything past its deadline is marked expired and drops out of the shortlist.
Report how many, then leave them alone unless `--all` was passed.

## Step 2: take what needs scoring

Write the selection to a file — step 3 scores that file and nothing else.

```bash
python tools/shortlist.py show --unscored --json        > /tmp/to_rank.json  # default
python tools/shortlist.py show --include-expired --json > /tmp/to_rank.json  # with --all
```

If the file is an empty list, say so and stop. Do not invent work.

Entries marked `verdict` with a trailing `*` in the table carry a coarse rating
inherited from before this command existed — they still count as unscored.

## Step 3: score

**Delegate to Gemini when it is available.** Thirty postings scored is exactly
the bulk work that should not sit in Claude's context:

```bash
python tools/gemini.py rank \
  --input /tmp/to_rank.json \
  --criteria data/profile/evaluation.md > /tmp/ranking.json
```

**Score the file from step 2, never `data/job_scraper/seen_jobs.json`.** That file
is every posting ever seen: passing it re-scores the whole history on every
run, expired entries included, and `merge-scores` then overwrites scores that
were already good. Step 1 exists to take expired postings out of the run.

Exit code 3 means Gemini is unavailable — not an error. Score them yourself
instead, using `.claude/skills/job-application-assistant/references/job-evaluation.md`
and `data/profile/evaluation.md`, and produce the same JSON shape. Say which route
you took, so the user knows whether a score came from Gemini or from you.

Either way, each result is:

```json
{"id": "...", "score": 0-100, "verdict": "strong|good|moderate|weak|poor",
 "strengths": ["..."], "gaps": ["..."],
 "location_verdict": "pass|fail|flag", "language_verdict": "pass|fail|flag",
 "deadline": "YYYY-MM-DD|null", "reason": "one sentence"}
```

### Hard gates come before the score

Two things are not deductions, they are failures:

- **Location.** A posting that breaks the rules in `data/profile/evaluation.md` is
  `location_verdict: fail`, whatever else it offers.
- **Language.** A posting requiring a language the profile does not claim at
  working level is `language_verdict: fail`.

A failed gate caps the verdict at `weak`. Scoring a role 85 and then noting in
passing that it requires relocation wastes the user's attention.

## Step 4: merge and show

```bash
python tools/shortlist.py merge-scores --file /tmp/ranking.json
python tools/shortlist.py show --min-score 60
```

`merge-scores` reports any result whose id is not in the shortlist rather than
silently dropping it — that mismatch usually means the ids were rewritten
somewhere.

## Step 5: present

Show the ranked table. For everything at `good` or better, add two or three
lines: why it matches, what the gap is, and anything that would need checking
before applying.

Then ask which to run `/apply` on. Offer the numbers from the table.

Say plainly how many were scored, how many expired, and how many are still
unscored because Gemini returned nothing for them. **A silently truncated
shortlist reads as "that is everything" when it is not.**
