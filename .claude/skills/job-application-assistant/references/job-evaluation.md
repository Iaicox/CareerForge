# Job evaluation framework

The scoring machinery. The user's own match areas, goals, location rules and
sector filter live in `data/profile/evaluation.md` — read both before scoring anything.

## Scoring dimensions

### 1. Technical skills match (0–100)

How well the required and preferred skills line up with what the candidate can do.

| Score | Meaning |
|---|---|
| 80–100 | Core requirements are their primary skills |
| 60–79 | Most requirements match; one or two learnable gaps |
| 40–59 | Partial match; significant upskilling needed |
| 0–39 | Fundamental mismatch |

Use the strong / moderate / weak match areas from `data/profile/evaluation.md`.

### 2. Experience match (0–100)

Does the work history match what they are hiring for?

| Score | Meaning |
|---|---|
| 80–100 | Direct experience in the same domain and role type |
| 60–79 | Related experience; the transfer is obvious |
| 40–59 | Adjacent experience; the case has to be argued |
| 0–39 | Unrelated |

Seniority counts here. A posting one level above the candidate's history is not
an automatic 40 — but it is not an 80 either.

### 3. Behavioural and culture fit (0–100)

| Score | Meaning |
|---|---|
| 80–100 | Culture strongly matches how they work best |
| 60–79 | Mixed signals, mostly compatible |
| 40–59 | Real friction in places |
| 0–39 | Serious mismatch |

Check reviews, LinkedIn and press for team stability, leadership style,
restructuring, and how engineering is treated. Weigh against
`data/profile/behavioral.md`.

### 4. Location and logistics (pass/fail)

Apply the rules in `data/profile/evaluation.md` exactly. Location is a gate, not a
score: a posting that fails it is not worth scoring further, however good the
rest looks. Say so plainly rather than quietly discounting it.

### 5. Career alignment and motivation (0–100)

| Score | Meaning |
|---|---|
| 80–100 | Strongly aligned; a clear growth path |
| 60–79 | Good role, partially aligned |
| 40–59 | A decent job that builds toward nothing they want |
| 0–39 | A dead end or a step backwards |

Score against their stated goals, the tasks that energise them and the tasks
that drain them, from `data/profile/evaluation.md`.

### 6. Salary benchmark (optional)

If `data/profile/salary_data.json` exists:

```bash
python tools/salary_lookup.py "<Company>" --city "<the posting's location>" --json
```

Present it as a small table. If the tool is not configured, omit the section
entirely rather than noting its absence. A record marked unknown says the
company was researched for this location and nothing was found — show the
note, do not research it again.

**Recording what research found** (step 3 of `/apply`, after
`research.py get --role --location`):

- **The target location is the location the posting itself states** — a city
  and country, or "remote — <region>". Not the home market: a US posting is
  benchmarked against US figures, a Lisbon posting against Lisbon figures.
- **A figure counts only if it is for the target location.** However good a
  figure for another location is, it does not become this posting's
  benchmark: record `--unknown` for this company at this location, with the
  other-market figure in the note as a lead, so it is neither presented as
  evidence nor researched again.
- **A matching figure is recorded in the unit the source states**, encoded in
  the category name the way the file already does it
  (`senior_frontend_eur_gross_annual`, `senior_frontend_usd_gross_annual`):
  annual gross as stated, or monthly gross × the payments per year the source
  states. Never converted between currencies, never guessed from net. `count`
  is the number of independent sources.
- Categories in another unit than the file's baseline are shown without the
  "vs Baseline" column, so a USD row is never read as a percentage of the EUR
  median.

## Weighting

| Dimension | Weight |
|---|---|
| Technical skills | 30% |
| Experience match | 25% |
| Behavioural fit | 15% |
| Career alignment | 30% |

Location is pass/fail and is not weighted.

## Thresholds

| Score | Verdict | Action |
|---|---|---|
| 75+ | Strong fit | Apply; tailor everything |
| 60–74 | Good fit | Apply; address the gaps in the letter |
| 45–59 | Moderate fit | Worth a conversation before spending the effort |
| 30–44 | Weak fit | Skip unless there is a strategic reason |
| <30 | Poor fit | Skip |

## Output format

```
## Job fit: [Role] at [Company]

| Dimension | Score | Notes |
|---|---|---|
| Technical skills | XX/100 | … |
| Experience match | XX/100 | … |
| Behavioural fit | XX/100 | … |
| Location | PASS/FAIL | … |
| Career alignment | XX/100 | … |

**Overall: XX/100**

### Verdict: [Strong / Good / Moderate / Weak / Poor fit]

### Strengths for this role
### Gaps to address
### Recommendation
[1–2 sentences: apply, skip, or apply with caveats]

### Research checklist
- [ ] Company site: what they sell, mission, recent news
- [ ] Review sites
- [ ] LinkedIn: team size, recent hires, shared connections
- [ ] Press: restructuring, growth, workplace issues
- [ ] Anyone in the candidate's network who knows the team
```

## Before applying: calling the employer

Worth suggesting **only when there are substantive questions** — never as a way
to "be remembered".

Suggest a call when the requirements are ambiguous, when it is unclear which
competencies are essential versus nice-to-have, when the day-to-day is vague, or
when a named contact invites questions.

Good questions: what the main challenges are, how time splits across the listed
responsibilities, which competencies decide success, what success looks like in
the first six to twelve months.

Rules: prepare a short pitch in case they ask, but the call is for gathering
information, not delivering one. Take notes, use what you learn to tailor the
application, and reference the conversation naturally in the letter.
