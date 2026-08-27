-- CareerForge tracker schema.
-- Status / work-mode / event-type values are ids defined in config/config.toml,
-- so the database never stores a display label.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    description TEXT,
    website     TEXT,
    -- Company research cache, shared by /apply's reviewer and /interview so the
    -- same company is not researched twice. Leads only: the verification
    -- checklist still applies before any of it reaches a document.
    research_json TEXT,
    researched_at TEXT,
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
    -- The place as the posting states it, kept separate from the verdict about
    -- it: the place is a fact, the verdict is a judgement, and merging them
    -- loses the fact.
    location           TEXT,
    location_verdict   TEXT,      -- pass | fail | flag
    office_address     TEXT,
    -- ISO date, or NULL. "ASAP", "rolling" and free text store as NULL rather
    -- than corrupting the column with unsortable values.
    deadline           TEXT,
    source             TEXT,      -- which board or channel it came from
    -- The /apply evaluation, kept instead of scrolling away with the session.
    fit_score          INTEGER,
    fit_strengths      TEXT,      -- JSON array
    fit_gaps           TEXT,      -- JSON array
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
    notion_page_id TEXT,                  -- set only for events imported from Notion
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
    (SELECT COUNT(*)    FROM events e WHERE e.application_id = a.id) AS event_count,
    -- Evaluated per query, so it stays true as the day rolls over.
    CASE WHEN a.deadline IS NOT NULL AND a.deadline < date('now')
         THEN 1 ELSE 0 END AS is_expired
FROM applications a
JOIN companies c ON c.id = a.company_id;
