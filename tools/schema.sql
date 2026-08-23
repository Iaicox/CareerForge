-- CareerForge tracker schema.
-- Status / work-mode / event-type values are ids defined in config/config.toml,
-- so the database never stores a display label.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    website     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    id                 INTEGER PRIMARY KEY,
    company_id         INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    role               TEXT NOT NULL,
    slug               TEXT NOT NULL UNIQUE,   -- folder name under the stage dir
    url                TEXT,
    status             TEXT NOT NULL,
    work_mode          TEXT,
    office_address     TEXT,
    hr_name            TEXT,
    hr_email           TEXT,
    other_contacts     TEXT,
    posting_text       TEXT,
    cover_letter_text  TEXT,
    notes              TEXT,
    notion_page_id     TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_applications_url    ON applications(url);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company_id);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    type           TEXT NOT NULL,
    date           TEXT NOT NULL,        -- ISO-8601, date or datetime
    is_datetime    INTEGER NOT NULL DEFAULT 0,
    participants   TEXT,
    outcome        TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_application ON events(application_id);

CREATE TABLE IF NOT EXISTS attachments (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,        -- cv | cover | other
    path           TEXT NOT NULL,        -- repo-relative
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(application_id, kind, path)
);

-- Replaces the Notion "last event date" rollup.
CREATE VIEW IF NOT EXISTS applications_view AS
SELECT
    a.*,
    c.name AS company_name,
    c.slug AS company_slug,
    c.website AS company_website,
    (SELECT MAX(e.date) FROM events e WHERE e.application_id = a.id) AS last_event_date,
    (SELECT COUNT(*)    FROM events e WHERE e.application_id = a.id) AS event_count
FROM applications a
JOIN companies c ON c.id = a.company_id;
