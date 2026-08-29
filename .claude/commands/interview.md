---
description: Prepare for a specific interview stage, or run a mock interview
argument-hint: "<company> [screening|tech|system-design|manager|final] [--mock]"
---

# /interview — stage-specific preparation

A screening call and a system-design round need different preparation. This
command asks which one you are walking into, and prepares for that.

`$ARGUMENTS`: a company slug, optionally a stage, optionally `--mock`.

---

## Step 0: locate the application

```bash
python tools/tracker.py show <slug>
```

If the slug does not resolve, list the live pipeline and ask which one:

```bash
python tools/tracker.py list --stage processing
```

**Work out the stage** when it was not given: take the most recent event and
the current status. `screening` status means the screening call is next;
a completed `screening` event means the next one is probably `tech`. State
what you inferred and let the user correct it — guessing wrong here wastes
the whole preparation.

If the application is still in `data/pipeline/applications/`, an interview invitation means
it belongs in `data/pipeline/processing/`. Move it by changing the status; the folder
follows:

```bash
python tools/tracker.py set-status <slug> screening
```

## Step 1: gather

**The company**, from cache — this is why the cache exists:

```bash
python tools/research.py get "<Company>"
```

Exit code 3 means nothing is cached and Gemini is unavailable. Research it
yourself, then store it so `/apply` and any later round get it free:

```bash
python tools/research.py put "<Company>" --file <json>
```

**The role**: `job.md` in the application folder holds the posting as captured.

**The candidate**: `data/profile/interview-prep.md` for STAR material,
`data/profile/candidate.md` for the facts, `data/profile/behavioral.md` for how they work.

## Step 2: prepare for this stage

| Stage | What matters |
|---|---|
| `screening` | A 90-second story of who they are. Salary expectation — the research's `salary` block and `salary_lookup.py "<Company>" --city` say what this company pays, if anything is known — notice period, why this company. Recruiters screen out on logistics, not on depth |
| `tech` | The stack in the posting, matched against real work. Where the gaps are and how to answer honestly when one is probed |
| `system-design` | One or two architectures from their actual history, with the trade-offs they can defend. Scale, failure modes, what they would do differently |
| `manager` | Ownership, conflict, delivery under pressure, why they left each role |
| `final` | Motivation, the questions worth asking, compensation (from the same salary sources as screening), the close |

For every stage produce:

1. **Likely questions**, drawn from the posting and the research — not a
   generic list. Delegating the first draft is worthwhile:
   ```bash
   python tools/gemini.py summarize --file <stage>/<slug>/job.md \
     --question "What interview questions would a hiring manager ask for this role? List 15."
   ```
   Then cut the ones that do not fit and add what the research suggests.
2. **The answer**, per question, grounded in `data/profile/interview-prep.md`.
   **Never invent a story.** If nothing in the profile fits a likely question,
   say so plainly and mark it as a gap to prepare — a fabricated anecdote
   collapses the moment it is probed.
3. **Questions to ask them**, specific enough to prove the research was done.
4. **The known weak points** and an honest answer for each.

## Step 3: mock interview (`--mock`)

Ask one question. Wait. Critique the answer against what a real interviewer
would take from it: did it answer the question, was it concrete, was it the
right length, did it invite the follow-up they want.

Be direct. A mock interview that praises everything is worse than none, because
it builds confidence in an answer that will not survive contact.

After each answer, note what to fix. At the end, summarise the three weakest
points and record them in the prep file.

## Step 4: write it down

Write to the application folder:

```
<stage-dir>/<slug>/interview_prep_<stage>.md
```

Never rename or overwrite prep files from earlier rounds — the history of what
was asked is worth keeping.

Record the event:

```bash
python tools/tracker.py event add <slug> --type <event-type> \
  --date <ISO date, with time for a scheduled call> --outcome pending
```

Use `python tools/tracker.py statuses` if you need the configured ids.

## Step 5: after the interview

When the user reports how it went, add the outcome and move the status:

```bash
python tools/tracker.py event add <slug> --type <type> --outcome passed
python tools/tracker.py set-status <slug> <next status>
```

Then append to the prep file what was actually asked. That is what makes the
next round, and the next company, cheaper to prepare for.
