---
description: Open the application pipeline as a kanban board in the browser, or the postings table
argument-hint: "[postings] [--port N]"
---

# /board

Start the local board over the tracker database:

```bash
python tools/board.py                   # the applications kanban
python tools/board.py --view postings   # the postings table
```

`/board postings` means the second form; pass any other `$ARGUMENTS` through.

It serves `http://127.0.0.1:8765/` (loopback only) and opens a browser. Two
pages, linked from each other's header:

- `/` — the kanban. Columns are the statuses from `data/config/config.toml`;
  dragging a card changes its status **and** moves its folder between
  `data/pipeline/applications/`, `processing/` and `rejected/`.
- `/postings` — every posting ever seen, as a table: company, title, **status
  as a select** (change it there), score and verdict from `/rank`, deadline,
  source, a note that edits in place, and the application it became, if any.
  Filters: status (open by default), minimum score, a search box.

The server runs until stopped, so start it in the background and tell the user
the URL rather than blocking the session on it.

If the user only wants a quick look, this is cheaper:

```bash
python tools/tracker.py report --board     # the kanban, as text
python tools/shortlist.py show             # the postings, best fit first
```
