-- KAI Media Revenue Factory — initial schema (migration 001)
-- Normalized + auditable. Every table carries id / created_at / updated_at.
-- `media_audit_events` is the append-only ledger; `audit_events` is a read
-- view over it (append-only by construction).

-- ── Enums ──────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE media_status AS ENUM (
        'VERIFIED', 'PARTIALLY_VERIFIED', 'UNVERIFIED', 'MISSING',
        'BLOCKED', 'DEGRADED', 'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE publish_mode AS ENUM ('dry_run', 'test', 'canary', 'live');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE pattern_state AS ENUM (
        'HYPOTHESIS', 'EMERGING', 'VALIDATED', 'STRATEGIC', 'DECLINED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE content_state AS ENUM (
        'IDEA', 'RESEARCH', 'APPROVED_CONCEPT', 'SCRIPTING',
        'ASSET_GENERATION', 'ASSEMBLY', 'RENDERING', 'QC', 'RIGHTS_CHECK',
        'POLICY_CHECK', 'READY', 'SCHEDULED', 'PUBLISHING', 'PUBLISHED',
        'ANALYTICS_PENDING', 'ANALYZING', 'WINNER', 'REPAIR', 'RETEST',
        'DECLINING', 'RETIRED', 'QUARANTINED', 'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── updated_at trigger ─────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION media_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── Reference / infrastructure ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platforms (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'video',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS accounts (
    id           BIGSERIAL PRIMARY KEY,
    platform_id  BIGINT REFERENCES platforms(id) ON DELETE SET NULL,
    handle       TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    token_ref    TEXT,
    health       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform_id, handle)
);

CREATE TABLE IF NOT EXISTS factories (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'content',
    description  TEXT NOT NULL DEFAULT '',
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workers (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'worker',
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    last_seen_at TIMESTAMPTZ,
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS models (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    provider     TEXT NOT NULL DEFAULT 'ollama',
    endpoint     TEXT,
    role         TEXT NOT NULL DEFAULT 'content',
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS skills (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    origin       TEXT NOT NULL DEFAULT 'local',
    path         TEXT,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    meta         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker_id    BIGINT REFERENCES workers(id) ON DELETE SET NULL,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Trend → opportunity ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trends (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL,
    external_id  TEXT,
    title        TEXT NOT NULL,
    query        TEXT,
    geo          TEXT,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalized   JSONB NOT NULL DEFAULT '{}'::jsonb,
    momentum     NUMERIC(8,5),
    competition  NUMERIC(8,5),
    shelf_life   NUMERIC(8,5),
    score        NUMERIC(8,5),
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    fetched_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, title, geo)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id              BIGSERIAL PRIMARY KEY,
    trend_id        BIGINT REFERENCES trends(id) ON DELETE SET NULL,
    factory_key     TEXT NOT NULL REFERENCES factories(key) ON DELETE CASCADE,
    angle           TEXT NOT NULL,
    effort          NUMERIC(10,5),
    expected_payoff NUMERIC(12,5),
    score           NUMERIC(12,5),
    formula         TEXT,
    status          media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Content ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content (
    id            BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT REFERENCES opportunities(id) ON DELETE SET NULL,
    factory_key   TEXT NOT NULL REFERENCES factories(key) ON DELETE CASCADE,
    account_id    BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'short',
    state         content_state NOT NULL DEFAULT 'IDEA',
    status        media_status NOT NULL DEFAULT 'UNVERIFIED',
    duration_s    NUMERIC(12,3),
    published_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_dna (
    id          BIGSERIAL PRIMARY KEY,
    content_id  BIGINT NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    hook        TEXT,
    format      TEXT,
    dna         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status      media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ideas (
    id          BIGSERIAL PRIMARY KEY,
    content_id  BIGINT REFERENCES content(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    premise     TEXT,
    score       NUMERIC(8,5),
    status      media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scripts (
    id           BIGSERIAL PRIMARY KEY,
    content_id   BIGINT REFERENCES content(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL DEFAULT 1,
    body         TEXT,
    model        TEXT,
    model_status media_status NOT NULL DEFAULT 'UNVERIFIED',
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (content_id, version)
);

-- ── Universe / character / episode / canon memory (Factory B) ──────────────
CREATE TABLE IF NOT EXISTS universes (
    id            BIGSERIAL PRIMARY KEY,
    key           TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    premise       TEXT,
    canon_summary TEXT,
    status        media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS characters (
    id           BIGSERIAL PRIMARY KEY,
    universe_id  BIGINT NOT NULL REFERENCES universes(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'supporting',
    traits       JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, key)
);

CREATE TABLE IF NOT EXISTS episodes (
    id           BIGSERIAL PRIMARY KEY,
    universe_id  BIGINT NOT NULL REFERENCES universes(id) ON DELETE CASCADE,
    content_id   BIGINT REFERENCES content(id) ON DELETE SET NULL,
    number       INTEGER,
    title        TEXT NOT NULL,
    synopsis     TEXT,
    state        content_state NOT NULL DEFAULT 'IDEA',
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS canon (
    id           BIGSERIAL PRIMARY KEY,
    universe_id  BIGINT NOT NULL REFERENCES universes(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    statement    TEXT NOT NULL,
    evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
    immutable    BOOLEAN NOT NULL DEFAULT TRUE,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (universe_id, key)
);

-- ── Assets + lineage ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS assets (
    id              BIGSERIAL PRIMARY KEY,
    content_id      BIGINT REFERENCES content(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    uri             TEXT,
    sha256          TEXT,
    bytes           BIGINT,
    meta            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          media_status NOT NULL DEFAULT 'UNVERIFIED',
    blocked_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS asset_lineage (
    id               BIGSERIAL PRIMARY KEY,
    asset_id         BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    parent_asset_id  BIGINT REFERENCES assets(id) ON DELETE SET NULL,
    relation         TEXT NOT NULL DEFAULT 'derived_from',
    transform        TEXT,
    status           media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Rights / provenance / policy ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rights (
    id            BIGSERIAL PRIMARY KEY,
    asset_id      BIGINT REFERENCES assets(id) ON DELETE CASCADE,
    content_id    BIGINT REFERENCES content(id) ON DELETE SET NULL,
    source        TEXT,
    title         TEXT,
    creator       TEXT,
    work_date     TEXT,
    jurisdiction  TEXT,
    license       TEXT,
    public_domain BOOLEAN NOT NULL DEFAULT FALSE,
    rights_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    confidence    NUMERIC(5,4),
    restrictions  TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status        media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provenance (
    id            BIGSERIAL PRIMARY KEY,
    subject_type  TEXT NOT NULL,
    subject_id    BIGINT NOT NULL,
    source        TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
    status        media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policies (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'platform',
    rule        JSONB NOT NULL DEFAULT '{}'::jsonb,
    severity    TEXT NOT NULL DEFAULT 'warn',
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    status      media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Publishing / analytics ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS publishing_jobs (
    id               BIGSERIAL PRIMARY KEY,
    content_id       BIGINT REFERENCES content(id) ON DELETE CASCADE,
    account_id       BIGINT REFERENCES accounts(id) ON DELETE SET NULL,
    platform_id      BIGINT REFERENCES platforms(id) ON DELETE SET NULL,
    mode             publish_mode NOT NULL DEFAULT 'dry_run',
    status           media_status NOT NULL DEFAULT 'UNVERIFIED',
    idempotency_key  TEXT UNIQUE,
    scheduled_at     TIMESTAMPTZ,
    published_at     TIMESTAMPTZ,
    attempt          INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    last_error       TEXT,
    blocked_reason   TEXT,
    response         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics (
    id           BIGSERIAL PRIMARY KEY,
    content_id   BIGINT REFERENCES content(id) ON DELETE CASCADE,
    platform_id  BIGINT REFERENCES platforms(id) ON DELETE SET NULL,
    source       TEXT NOT NULL DEFAULT 'manual',
    metric       TEXT NOT NULL,
    value        NUMERIC(20,5),
    observed_at  TIMESTAMPTZ,
    raw          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Experiments / patterns / strategy ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS experiments (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    hypothesis   TEXT,
    metric       TEXT,
    variant_a    TEXT,
    variant_b    TEXT,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    started_at   TIMESTAMPTZ,
    ended_at     TIMESTAMPTZ,
    readout      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patterns (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    state       pattern_state NOT NULL DEFAULT 'HYPOTHESIS',
    score       NUMERIC(10,5),
    evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status      media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pattern_lifecycle (
    id          BIGSERIAL PRIMARY KEY,
    pattern_id  BIGINT NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    from_state  pattern_state,
    to_state    pattern_state NOT NULL,
    reason      TEXT,
    evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor       TEXT NOT NULL DEFAULT 'system',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id             BIGSERIAL PRIMARY KEY,
    version        INTEGER NOT NULL UNIQUE,
    parent_version INTEGER,
    strategy       JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence       JSONB NOT NULL DEFAULT '{}'::jsonb,
    status         media_status NOT NULL DEFAULT 'UNVERIFIED',
    active         BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Failures / repairs / decisions ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS failures (
    id           BIGSERIAL PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id   BIGINT,
    category     TEXT NOT NULL DEFAULT 'unknown',
    message      TEXT NOT NULL,
    root_cause   TEXT,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repairs (
    id          BIGSERIAL PRIMARY KEY,
    failure_id  BIGINT REFERENCES failures(id) ON DELETE SET NULL,
    strategy    TEXT NOT NULL,
    outcome     TEXT,
    evidence    JSONB NOT NULL DEFAULT '{}'::jsonb,
    status      media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decisions (
    id           BIGSERIAL PRIMARY KEY,
    scope_type   TEXT NOT NULL DEFAULT 'content',
    scope_id     BIGINT,
    decision     TEXT NOT NULL,
    rationale    TEXT,
    evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
    reversible   BOOLEAN NOT NULL DEFAULT TRUE,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Money ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS revenue (
    id                BIGSERIAL PRIMARY KEY,
    content_id        BIGINT REFERENCES content(id) ON DELETE SET NULL,
    pattern_id        BIGINT REFERENCES patterns(id) ON DELETE SET NULL,
    strategy_version  INTEGER REFERENCES strategy_versions(version) ON DELETE SET NULL,
    platform_id       BIGINT REFERENCES platforms(id) ON DELETE SET NULL,
    source            TEXT NOT NULL DEFAULT 'manual',
    amount            NUMERIC(20,5) NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'USD',
    occurred_at       TIMESTAMPTZ,
    evidence          JSONB NOT NULL DEFAULT '{}'::jsonb,
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    status            media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS costs (
    id           BIGSERIAL PRIMARY KEY,
    content_id   BIGINT REFERENCES content(id) ON DELETE SET NULL,
    category     TEXT NOT NULL DEFAULT 'compute',
    amount       NUMERIC(20,5) NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'USD',
    occurred_at  TIMESTAMPTZ,
    evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
    status       media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS profitability (
    id            BIGSERIAL PRIMARY KEY,
    scope_type    TEXT NOT NULL,
    scope_id      BIGINT,
    revenue       NUMERIC(20,5) NOT NULL DEFAULT 0,
    cost          NUMERIC(20,5) NOT NULL DEFAULT 0,
    profit        NUMERIC(20,5) NOT NULL DEFAULT 0,
    currency      TEXT NOT NULL DEFAULT 'USD',
    period_start  TIMESTAMPTZ,
    period_end    TIMESTAMPTZ,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── IP portfolio ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ip_portfolio (
    id             BIGSERIAL PRIMARY KEY,
    universe_id    BIGINT REFERENCES universes(id) ON DELETE SET NULL,
    content_id     BIGINT REFERENCES content(id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'universe',
    value_estimate NUMERIC(20,5),
    status         media_status NOT NULL DEFAULT 'UNVERIFIED',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Pipeline events + append-only audit ledger ─────────────────────────────
CREATE TABLE IF NOT EXISTS media_events (
    id         BIGSERIAL PRIMARY KEY,
    cycle_id   TEXT,
    stage      TEXT NOT NULL,
    status     media_status NOT NULL DEFAULT 'UNVERIFIED',
    detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_audit_events (
    id          BIGSERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    entity_type TEXT,
    entity_id   BIGINT,
    actor       TEXT NOT NULL DEFAULT 'system',
    source      TEXT NOT NULL DEFAULT 'media_factory',
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION media_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'media_audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_media_audit_immutable ON media_audit_events;
CREATE TRIGGER trg_media_audit_immutable
    BEFORE UPDATE OR DELETE ON media_audit_events
    FOR EACH ROW EXECUTE FUNCTION media_block_mutation();

-- Read surface for the audit API (append-only by construction).
CREATE OR REPLACE VIEW audit_events AS
    SELECT id, event_type, entity_type, entity_id, actor, source, payload, created_at
    FROM media_audit_events;

-- ── Attach updated_at triggers to every mutable table ──────────────────────
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'platforms','accounts','factories','workers','models','skills','jobs',
        'trends','opportunities','content','content_dna','ideas','scripts',
        'universes','characters','episodes','canon','assets','asset_lineage',
        'rights','provenance','policies','publishing_jobs','analytics',
        'experiments','patterns','pattern_lifecycle','strategy_versions',
        'failures','repairs','decisions','revenue','costs','profitability',
        'ip_portfolio'
    ] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%s_updated ON %I;', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION media_set_updated_at();', t, t);
    END LOOP;
END $$;
