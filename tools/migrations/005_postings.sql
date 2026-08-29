-- Every posting ever seen, in the database instead of job_scraper/seen_jobs.json.
-- The scraper, /rank and /apply all read and write one table, keyed on a
-- normalised URL, so a posting declined in /apply never resurfaces in /scrape.
--
-- DDL only: the rows come from tracker.backfill_postings() (one `applied` row
-- per existing application, which needs Python for the URL key) and from
-- `shortlist.py import-json` for the old JSON file. Fresh databases already
-- have the table from schema.sql and record this as a baseline.

CREATE TABLE IF NOT EXISTS postings (
    id               INTEGER PRIMARY KEY,
    url              TEXT,
    url_key          TEXT NOT NULL UNIQUE, -- normalize_url(url); company::title when there is no URL
    title            TEXT,
    company          TEXT,
    location         TEXT,
    source           TEXT,                -- board or channel; "apply" when first seen in /apply
    summary          TEXT,
    deadline         TEXT,                -- ISO date or NULL, via parse_deadline
    first_seen       TEXT NOT NULL,
    status           TEXT NOT NULL,       -- id from [[posting_statuses]]
    note             TEXT,                -- why it was skipped, or anything else worth keeping
    score            INTEGER,             -- from /rank
    verdict          TEXT,
    strengths        TEXT,                -- JSON array
    gaps             TEXT,                -- JSON array
    location_verdict TEXT,
    language_verdict TEXT,
    reason           TEXT,
    scored_at        TEXT,
    application_id   INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    notion_page_id   TEXT,                -- set if this row is mirrored
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_postings_status      ON postings(status);
CREATE INDEX IF NOT EXISTS idx_postings_application ON postings(application_id);
