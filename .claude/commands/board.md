---
description: Open the application pipeline as a kanban board in the browser
argument-hint: "[--port N]"
---

# /board

Start the local kanban over the tracker database:

```bash
python tools/board.py $ARGUMENTS
```

It serves `http://127.0.0.1:8765/` (loopback only) and opens a browser. Columns
are the statuses from `data/config/config.toml`; dragging a card changes its status
**and** moves its folder between `data/pipeline/applications/`, `data/pipeline/processing/` and `data/pipeline/rejected/`.

The server runs until stopped, so start it in the background and tell the user
the URL rather than blocking the session on it.

If the user only wants a quick look, this is cheaper:

```bash
python tools/tracker.py report --board
```
