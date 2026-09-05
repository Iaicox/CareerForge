"""Attachment paths left behind by a stage move.

set_status() moved an application's folder between stage directories without
rewriting attachments.path, which stores the stage directory inside the string.
Every row written before such a move points at a file that is no longer there:
tracker.py show prints the dead path, and notion_sync quietly stops carrying
the CV because the file it wants is missing. Re-attaching the right path did
not help either -- add_attachment is INSERT OR IGNORE against
UNIQUE(application_id, kind, path), so it appended a second row and left the
stale one sitting beside it.

Which stage a slug belongs to cannot be read off the row. The status-to-stage
map lives in the user's config.toml, not in the database, and the folder can
sit out of step with the status anyway -- that is what /triage is for. So this
reads the folder off disk, the same ground truth folder_for() gives set_status.

Fresh databases have nothing to repair and record this as a baseline without
running it, like every migration. On an existing one a second run is a no-op:
a row already under its own folder's stage is under no other stage's prefix, so
nothing matches. A slug with no folder on disk is left alone rather than
guessed at.
"""

from __future__ import annotations


def migrate(conn, tracker):
    for app in conn.execute("SELECT id, slug FROM applications").fetchall():
        _, stage = tracker.folder_for(app["slug"])
        if stage is None:
            continue
        for other in tracker.STAGES:
            if other != stage:
                tracker.retarget_attachments(conn, app["id"], app["slug"], other, stage)
