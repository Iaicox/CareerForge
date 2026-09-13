---
description: Look at or update the application tracker
argument-hint: "[status | <company> <new status> | stale | agenda]"
---

# /track

Use the **application-tracker** skill. Interpret `$ARGUMENTS`:

- empty → `python tools/tracker.py report --board`
- `stale` → `python tools/tracker.py list --stale`
- `agenda` → `python tools/tracker.py agenda` — late follow-ups, today, the
  week ahead; the calendar's three lists as text
- a company slug → `python tools/tracker.py show <slug>`
- a company slug plus a status → confirm with the user, then
  `python tools/tracker.py set-status <slug> <status>`, and add the matching
  event with `tracker.py event add`

A status change moves the application folder as well, and the recorded document
paths move with it. Say which folder moved where, so the user is never surprised
by files relocating.

Run `python tools/tracker.py statuses` if you need the configured status ids.
