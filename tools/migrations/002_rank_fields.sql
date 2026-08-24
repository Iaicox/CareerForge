-- Fields that /rank, /apply and the company-research cache need.
--
-- Only for databases created before these columns existed. A database created
-- from the current schema.sql already has them, and the runner records this
-- migration as applied without executing it.

ALTER TABLE applications ADD COLUMN location         TEXT;
ALTER TABLE applications ADD COLUMN location_verdict TEXT;
ALTER TABLE applications ADD COLUMN deadline         TEXT;
ALTER TABLE applications ADD COLUMN source           TEXT;
ALTER TABLE applications ADD COLUMN fit_score        INTEGER;
ALTER TABLE applications ADD COLUMN fit_strengths    TEXT;
ALTER TABLE applications ADD COLUMN fit_gaps         TEXT;

ALTER TABLE companies ADD COLUMN research_json TEXT;
ALTER TABLE companies ADD COLUMN researched_at TEXT;
