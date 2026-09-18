-- KAI Media Revenue Factory — profitability.status (migration 004)
-- The profitability snapshot is a first-class auditable record, so it carries
-- the standard status vocabulary like every other table.
ALTER TABLE profitability
    ADD COLUMN IF NOT EXISTS status media_status NOT NULL DEFAULT 'UNVERIFIED';
