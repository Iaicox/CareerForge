-- Attachment paths are stored relative to the repo root (tracker.rel), and the
-- three stage directories moved from the root to data/pipeline/. A row written
-- before the move points at a folder that no longer exists; prefix it once.
--
-- Fresh databases get this recorded as a baseline without running it, like
-- every migration; on an old database it is a no-op for rows already under
-- data/, so re-running by hand is harmless.

UPDATE attachments
   SET path = 'data/pipeline/' || path
 WHERE path LIKE 'applications/%'
    OR path LIKE 'processing/%'
    OR path LIKE 'rejected/%';
