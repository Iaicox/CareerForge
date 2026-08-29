---
name: application-tracker
description: Record and update job applications in the local tracker - add an application, change its status, log funnel events, attach built documents, and keep application folders in the right stage directory. Use when the user reports sending an application, getting a reply, an interview, a rejection or an offer, or asks about the state of their pipeline. Keywords - tracker, pipeline, status, applied, rejected, interview scheduled, offer.
allowed-tools: Read, Bash, AskUserQuestion
---

# Application tracker

A local SQLite database at `data/state/careerforge.db`. Everything goes through
`tools/tracker.py` — never open the database directly, and never edit an
application folder's location by hand.

## The one rule

**Status drives the folder, never the reverse.** Each status in
`data/config/config.toml` carries a `stage` id, and the stage names the
directory under `data/pipeline/`:

| Stage | Meaning |
|---|---|
| `data/pipeline/applications/` | Applied, waiting for an answer |
| `data/pipeline/processing/` | The company replied; interviews scheduled or underway |
| `data/pipeline/rejected/` | Closed: rejection at any stage, silence, or withdrawn |

`tracker.py set-status` changes the status **and** moves the folder in one
step. If it reports a collision, stop and tell the user — do not resolve it by
moving files yourself.

Run `python tools/tracker.py statuses` to see the configured statuses; they are
the user's, not fixed by this framework.

## Recording a new application

```bash
# 1. Dedup first, always.
python tools/tracker.py find --url "<posting url>" --company "<Company>" --role "<Role>" --json

# 2. If nothing came back, add it.
python tools/tracker.py add \
  --company "<Company>" --role "<Role>" --url "<url>" \
  --status draft --work-mode remote \
  --website "<company site>" --company-description "<one or two sentences>" \
  --posting-file <stage>/<slug>/job.md
```

`add` prints the slug it assigned — that is the folder name to create under
`data/pipeline/applications/`. It refuses duplicates on its own; `--force` exists for the
genuine second application to the same company, and needs the user to say so.

Leave attachments empty at this point. The PDFs go up when the application is
actually sent, so the tracker holds the version the employer received.

## When the user says they sent it

```bash
python tools/tracker.py set-status <slug> applied
python tools/tracker.py event add <slug> --type applied --date YYYY-MM-DD --outcome passed
python tools/tracker.py attach <slug> --kind cv    --path <stage>/<slug>/cv_<name>.pdf
python tools/tracker.py attach <slug> --kind cover --path <stage>/<slug>/cover_letter_<name>.pdf
```

## When something happens

Every stage of the funnel is a status change **and** an event:

```bash
python tools/tracker.py set-status <slug> screening
python tools/tracker.py event add <slug> --type screening \
  --date 2026-09-01T14:00 --participants "Anna (HR)" --outcome pending
```

Use `--outcome pending` for anything scheduled in the future. Add new names to
the record with `note --append`, never overwriting what is there.

## Correcting the record

An event is a record of what happened, so it does not get rewritten. Two things
can still be wrong about it:

```bash
python tools/tracker.py event set-type <event-id> --type tech_interview
python tools/tracker.py event delete <event-id>
```

`set-type` fixes a classification — an import that could not map a label files
the event as `other`, and the row then says nothing about how far the
application got. `delete` is for a row that records nothing that happened: the
same interview logged twice, once when it was scheduled and again with its
outcome, or a meeting that moved and left its old slot behind. Both take the
event id from `show <slug>`.

Do not use `delete` to erase history. An interview that went badly happened.

`delete` prints everything the row held, and says whether the event's Notion
page is now orphaned — if it is, delete that page too, or the next
`notion_sync.py import` brings the event back.

## Never

- Do not write to the database with anything but `tools/tracker.py`.
- Do not invent data. No placeholder emails, no guessed addresses. A field with
  no value stays empty.
- Do not move an application backwards through the funnel, except into a
  terminal status.
- Do not create a second record for a company that already has one — link the
  new role to the same company by using the same `--company` name.

## Reviewing the pipeline

```bash
python tools/tracker.py list --stage processing     # what is live
python tools/tracker.py list --stale                # silent past the cutoff
python tools/tracker.py report --board              # kanban in the terminal
python tools/tracker.py show <slug>                 # one application in full
```

The `/triage` command uses `--stale` and the `folder_in_sync` flag to propose
fixes. `/board` opens the same data as a drag-and-drop kanban in the browser.

## Optional: mirroring to Notion

Only when `data/config/config.toml` has `notion.enabled = true`:

```bash
python tools/notion_sync.py push          # local -> Notion, including PDFs
python tools/notion_sync.py import        # Notion -> local (one-time migration)
```

SQLite stays the source of truth. If the two disagree, local wins.
