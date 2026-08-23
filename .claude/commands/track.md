---
description: Look at or update the application tracker
argument-hint: "[status | <company> <new status> | stale]"
---

# /track

Use the **application-tracker** skill. Interpret `$ARGUMENTS`:

- empty → `python tools/tracker.py report --board`
- `stale` → `python tools/tracker.py list --stale`
- a company slug → `python tools/tracker.py show <slug>`
- a company slug plus a status → confirm with the user, then
  `python tools/tracker.py set-status <slug> <status>`, and add the matching
  event with `tracker.py event add`

A status change moves the application folder as well. Say which folder moved
where, so the user is never surprised by files relocating.

Run `python tools/tracker.py statuses` if you need the configured status ids.
