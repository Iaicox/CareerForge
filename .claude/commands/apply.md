---
description: Evaluate a job posting, draft a tailored CV and cover letter, review, build and track it
argument-hint: "[--cover|--no-cover] <posting URL or pasted text>"
---

# /apply — drafter/reviewer application workflow

`$ARGUMENTS` holds the job posting (a URL or pasted text), optionally preceded by:

- `--cover` — draft the cover letter without asking
- `--no-cover` — skip the cover letter without asking

Follow the steps in order. Do not skip any.

---

## Step 0: check the workspace, parse the input

If `data/profile/candidate.md` is missing, stop and offer `/setup`. Everything below
depends on it.

Read `data/config/config.toml` for the document filename patterns and page limits.

- URL → `WebFetch` it. If the fetch is blocked, ask the user to paste the text
  rather than guessing at the content.
- Extract: **company**, **role**, **department**, **location**, **work mode**,
  **posting language**.
- **Deduplicate before doing any work:**
  ```
  python tools/shortlist.py check "<url>"
  python tools/tracker.py find --url "<url>" --company "<Company>" --role "<Role>" --json
  ```
  `find` matching means this application already exists: show it to the user
  and ask whether to update it instead of starting over. `check` reporting
  `skipped` means this posting was evaluated before and declined — show the
  note and ask whether to reconsider before spending anything on it.

---

## Step 1: evaluate fit

Read `.claude/skills/job-application-assistant/references/job-evaluation.md`
for the framework and `data/profile/evaluation.md` for the user's own match areas,
goals, location rules and deal-breakers.

If `data/profile/salary_data.json` exists, add a benchmark:
`python tools/salary_lookup.py "<Company>" --json` (add `--city` when the posting
names one). Skip the benchmark silently if the tool is not configured.

Present: skills match, experience match, behavioural fit, salary benchmark,
overall score and a recommendation.

Then gate on the user:

- With `--cover` / `--no-cover`: ask a plain "proceed with drafting?"
- Otherwise **AskUserQuestion** with: CV + cover letter / CV + cover letter with
  extra context the user will supply (a referral, a contact on the team, past
  collaboration) / CV only / don't proceed.

**If the user declines, record why and stop** — the reason is what saves the
next `/scrape` from surfacing this posting again, and you from evaluating it
twice:

```
python tools/shortlist.py mark --url "<url>" --status skipped --note "<one line: the deciding gap>" \
  --company "<Company>" --title "<Role>" --salary "<the pay as the posting states it, verbatim>"
```

Drop `--salary` when the posting named no figure. It is worth passing even on a
posting being declined: the row stays in the shortlist, and the next time this
company comes up the table shows what they last advertised.

Use `--status maybe` for a posting the user wants to keep in view with a caveat
rather than drop.

Extra context goes into the letter, into `job.md` notes, and into the tracker
note. Facts the user states about their own relationships need no external
verification — but write only what they actually said.

---

## Step 2: draft

Create the folder `data/pipeline/applications/<slug>/`, where `<slug>` is what
`tracker.py add` assigns in step 6 — derive it the same way (company slug;
`<company>_<role>` if that company already has an application).

**`job.md`** first; it is the local safety copy and the source for the tracker:

```markdown
# <Role> — <Company>

- **URL:** <url or "pasted text">
- **Location / work mode:** …
- **Language:** …
- **Captured:** YYYY-MM-DD

## Full posting text
…
## Fit evaluation
<the assessment from step 1>
## Notes
<salary as the posting states it, stack, red flags, source, contacts>
```

**CV** — copy `data/profile/cv_master.md` to the filename from `config.toml`
(`documents.cv_filename`). Never edit the master. Follow
`references/cv-format.md`; keep the pandoc markup exactly as it is in the
master. Tailor the profile statement and bullet order to the posting.

**Cover letter** (if chosen) — copy `data/profile/cover_letter_master.md`, replace
every placeholder, follow `references/cover-letter-format.md` and
`references/writing-style.md`. Match the posting's language unless
`cover_letter_language` in the config says otherwise. Address a named person if
the posting gives one.

**Then run the `humanizer` skill on the cover letter**, before the reviewer sees
it — a letter that reads as machine-written is rejected before anyone weighs
what it says. Apply the rewrite, keeping the pandoc markup and the page limit
intact. **Not on the CV**: the skill refuses one, and its rewrite rules would
break the master's structure.

---

## Step 3: research, then review

**First, get the company research.** It is cached, so a second application to
the same company — or a later interview — costs nothing. Name the role and
the posting's location, so the research also looks for what this company pays:

```bash
python tools/research.py get "<Company>" --url "<company site>" \
  --role "<Role>" --location "<the location the posting states>"
```

Exit code 3 means nothing is cached and Gemini is unavailable. Research the
company yourself — including its salary figures for this role, each with its
own location, currency, period, basis and source — then store it so the next
consumer gets it free:

```bash
python tools/research.py put "<Company>" --file <json>
```

Research is **leads, not evidence**. Everything in it still has to be verified
before it reaches a document — a cached claim is not a checked one.

**Then record what the research says about pay**, if `data/profile/salary_data.json`
exists. The rule is in `references/job-evaluation.md` §6; in short:

- The target location is **the one the posting states**. A figure in the
  research counts only if it is for that location.
- A matching figure goes in as stated, in the unit its category name carries
  (`senior_frontend_eur_gross_annual`; a US posting gets a
  `..._usd_gross_annual` category, with `--new-category`), with its source
  and date. Never converted between currencies, never guessed from net.
  ```bash
  python tools/salary_lookup.py add --company "<Company>" --city "<target location>" \
    --category <category> --index <figure> --count <sources> \
    --source "<url>" --as-of "<YYYY-MM>"
  ```
- Only other locations, or nothing at all → the record for this company at
  this location is **unknown**, with the other-market figure kept as a lead:
  ```bash
  python tools/salary_lookup.py add --company "<Company>" --city "<target location>" \
    --unknown --note "<market>: <figures> (<source>, <date>)" --as-of "<YYYY-MM>"
  ```

The tool refuses to overwrite a figure already there (`--force` if the new one
is better sourced). Say what was recorded in the step's summary, and put the
finding in `job.md`'s Notes line.

**Then spawn the `application-reviewer`** agent with the Agent tool. Give it:

- the company slug and the application folder path
- the full job posting text
- the paths of the drafted documents
- **the research from above**, so it spends its turn on critique rather than
  on repeating a search someone already paid for

Its instructions live in `.claude/agents/application-reviewer.md` — do not
restate them here.

---

## Step 4: revise

1. Read the critique and the drafts again.
2. Apply the suggestions that genuinely improve the application, in the
   reviewer's priority order, within the page budget.
3. Edit in place; do not recreate the files.
4. **Reject anything that would fabricate experience.**
5. Any company claim the reviewer marked `unverified` must be independently
   confirmed with WebFetch/WebSearch before it goes into a document. If it
   cannot be confirmed, cut it.
6. Re-run the `humanizer` scan on the revised cover letter. The reviewer's
   suggestions arrive as prose and reintroduce AI texture more often than the
   first draft had. Reject any humanizer suggestion that would add a claim the
   profile does not support: the honesty rules outrank the voice rules.

---

## Step 5: build

```
tools\build.ps1 -Path data\pipeline\applications\<slug>          # Windows
tools/build.sh data/pipeline/applications/<slug>                 # macOS / Linux
```

Over the page limit means cutting the least relevant content and rebuilding.
Repeat until the script reports OK. Never solve it by changing the template.

---

## Step 6: record in the tracker

Follow the `application-tracker` skill.

```bash
python tools/tracker.py add \
  --company "<Company>" --role "<Role>" --url "<url>" \
  --status draft --work-mode <remote|hybrid|onsite> \
  --salary "<the pay as the posting states it, verbatim>" \
  --website "<site>" --company-description "<1-2 sentences>" \
  --posting-file data/pipeline/applications/<slug>/job.md \
  --cover-file data/pipeline/applications/<slug>/<cover filename>.md
```

`--salary` is the figure from the posting itself — the same words that went
into the job.md notes in step 2, copied over, not rounded and not converted.
Leave the flag off when the posting named none; the board then shows the market
benchmark instead, and a benchmark written in as a stated figure would be
exactly the invented number the honesty rules forbid.

If the slug it assigns differs from the folder you created, rename the folder
to match. The posting itself is marked `applied` in the `postings` table by
`add` — nothing to do there.

Attachments stay empty for now. The PDFs go up when the user confirms the
application was actually sent, so the tracker holds the version the employer
received — the user often hand-edits the `.docx` afterwards.

---

## Step 7: present

**Verification checklist** — run the one in `CLAUDE.md` and report pass/fail per
line. Include the `humanizer` scores for the cover letter (AI-Likeness,
Authenticity, Clarity, Appropriate Tone) and anything it flagged that was left
in on purpose.

**Key tailoring decisions** — three to five: what was emphasised and why, which
company angles were used, the most useful reviewer suggestion, which gaps were
acknowledged.

**Files and tracker** — list the produced files with page counts, and the
tracker id.

Close with: "Documents are ready. Edit the `.docx` in Word if you want manual
tweaks, then re-run the build to refresh the PDF. Tell me when you've submitted
and I'll update the tracker and attach the PDFs."
