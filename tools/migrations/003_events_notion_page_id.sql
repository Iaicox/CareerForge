-- Identity for events that came from the Notion mirror.
--
-- Without it `notion_sync.py import` had no way to tell an event it had already
-- brought across from a new one, so every re-run appended the whole Notion event
-- history again -- despite docs/notion-mirror.md promising the import is
-- idempotent. Applications were always keyed on their URL; events were not
-- keyed on anything.
--
-- Only for databases created before this column existed. A database created
-- from the current schema.sql already has it, and the runner records this
-- migration as applied without executing it.
--
-- Deliberately no UNIQUE index: init_db() runs schema.sql before the migrations,
-- so an index in schema.sql naming this column would fail on an existing
-- database, where the column does not exist yet. The import enforces the key by
-- looking the page id up before inserting.

ALTER TABLE events ADD COLUMN notion_page_id TEXT;
