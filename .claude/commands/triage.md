---
description: Sweep the pipeline for folders out of sync with their status and applications gone silent
argument-hint: "[company slug ...]"
---

# /triage — keep the pipeline honest

Two things drift: an application folder can sit in the wrong stage directory,
and an application can go quiet without anyone noticing. This sweeps for both,
proposes fixes, and applies them only after the user confirms.

`$ARGUMENTS` may name specific slugs; with no arguments, check everything.

## 1. Gather

```bash
python tools/tracker.py list --json          # everything, with folder_in_sync
python tools/tracker.py list --stale --json  # open and silent past the cutoff
python tools/tracker.py statuses --json      # status -> stage map
```

`folder_in_sync: false` means the folder is in a different stage directory than
its status says it should be. `folder: null` means no folder exists — normal for
an application tracked without documents, not an error.

Also list the directories themselves, so you can spot the reverse problem:

```bash
ls applications processing rejected
```

Ignore anything starting with `_` — those are not applications.

## 2. Propose

Show one table before touching anything:

| Company | Role | Status | Folder now | Should be | Why |

Then two lists with no action attached:

- **Unmatched** — folders with no tracker entry, and tracker entries whose
  folder is missing.
- **Unknown status** — rows whose `stage` is `null` in `list --json`: their
  status is not in `data/config/config.toml`, usually because a status id was
  renamed or removed. Name the status each one carries and stop there. These
  are deliberately absent from `--stale`, because the only thing this command
  offers a silent row is to close it, and a row whose status cannot be read is
  not a row to make guesses about. Fixing it means editing the config or
  re-setting the status, and that is the user's call.
- **Silent** — from `--stale`. Propose the `frozen` status for each; that moves
  the folder to `data/pipeline/rejected/`. This is the only status change /triage ever
  proposes.

Report the count of everything already correct. Do not list it.

**Change nothing until the user confirms.** Let them exclude individual rows.

## 3. Apply

For each approved row:

```bash
python tools/tracker.py set-status <slug> <status>
```

That changes the status and moves the folder in one step. For a row that only
needs the folder moved, re-set the status it already has — the move happens, the
status is unchanged.

If a move is refused because the target name is taken, stop on that row, report
it, and carry on with the others. Never resolve a collision by renaming or
deleting anything.

## 4. Report

How many folders moved where, which statuses changed, what is still unmatched,
and confirm the total application count is unchanged from step 1.
