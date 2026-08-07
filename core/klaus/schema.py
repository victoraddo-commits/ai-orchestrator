"""
KLAUS Legal Knowledge Acquisition System - Database Schema

SQL schema matching the approved Phase 17O implementation plan + Phase 18C
16-tier Ghana Legal Brain Corpus Acquisition & Knowledge Foundation Layer:

- 16-tier acquisition priority registry (klaus_acquisition_tiers)
- 22-field Legal Authority Record (klaus_legal_authority_records)
- Source tiering expanded with trust-level classification
- pgvector embeddings for semantic search
- Ghana-only jurisdiction enforcement at DB level
- Audit trail for all operations
"""

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 16-Tier Acquisition Priority Registry ──────────────────────────────
-- Tier 1 (Constitutional) = highest priority → Tier 16 (Official Publications) = lowest
CREATE TABLE IF NOT EXISTS klaus_acquisition_tiers (
    id SERIAL PRIMARY KEY,
    tier_number INTEGER CHECK (tier_number BETWEEN 1 AND 16) UNIQUE NOT NULL,
    tier_name VARCHAR(128) NOT NULL,
    tier_category VARCHAR(64) NOT NULL,
    description TEXT,
    priority_weight FLOAT DEFAULT 1.0,
    coverage_target INTEGER DEFAULT 0,
    acquisition_current INTEGER DEFAULT 0,
    status VARCHAR(32) DEFAULT 'active',
    last_acquired_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ── Source catalog: recursive seed discovery + reliability tracking ────
-- Expanded with tier assignment and trust-level classification per Phase 18C directive
CREATE TABLE IF NOT EXISTS klaus_sources (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    tier INTEGER CHECK (tier BETWEEN 1 AND 3),
    tier_id INTEGER REFERENCES klaus_acquisition_tiers(id),
    jurisdiction VARCHAR(64) DEFAULT 'Ghana' CHECK (jurisdiction = 'Ghana'),
    source_type VARCHAR(64),
    trust_level VARCHAR(32) DEFAULT 'unverified',
    last_discovered TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_validated_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(32) DEFAULT 'active',
    reliability_score FLOAT DEFAULT 1.0
);

-- ── Document storage: ingested legal materials with versioning ─────────
-- Expanded with tier_id FK per Phase 18C directive
CREATE TABLE IF NOT EXISTS klaus_documents (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES klaus_sources(id),
    tier_id INTEGER REFERENCES klaus_acquisition_tiers(id),
    title TEXT NOT NULL,
    file_hash VARCHAR(64) UNIQUE NOT NULL,
    file_path TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,
    jurisdiction VARCHAR(64) NOT NULL CHECK (jurisdiction = 'Ghana'),
    court VARCHAR(128),
    year INTEGER,
    legislation_number VARCHAR(64),
    copyright_classification VARCHAR(32) NOT NULL,
    access_level VARCHAR(32) NOT NULL,
    review_status VARCHAR(32) DEFAULT 'pending',
    effective_date DATE,
    version INTEGER DEFAULT 1,
    parent_document_id INTEGER REFERENCES klaus_documents(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ── 22-Field Legal Authority Record ────────────────────────────────────
-- Structured legal metadata per the Ghana Legal Brain Corpus directive.
-- One record per document; linked 1:1 to klaus_documents.
CREATE TABLE IF NOT EXISTS klaus_legal_authority_records (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES klaus_documents(id) ON DELETE CASCADE UNIQUE NOT NULL,
    authority_type VARCHAR(32) NOT NULL,
    citation_text TEXT,
    neutral_citation VARCHAR(128),
    court_identifier VARCHAR(128),
    judge_names TEXT[],
    parties TEXT,
    case_number VARCHAR(64),
    docket_number VARCHAR(64),
    date_argued DATE,
    date_decided DATE,
    status VARCHAR(32) DEFAULT 'current',
    ratio_decidendi TEXT,
    obiter_dicta TEXT,
    headnotes TEXT,
    legislation_history JSONB,
    amendment_chain INTEGER[],
    repeal_status VARCHAR(32),
    gazette_number VARCHAR(64),
    gazette_date DATE,
    consolidation_date DATE,
    authoritative_version BOOLEAN DEFAULT false,
    language VARCHAR(32) DEFAULT 'en',
    source_trust_level VARCHAR(32) DEFAULT 'unverified',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ── Document chunks with vector embeddings for semantic search ─────────
CREATE TABLE IF NOT EXISTS klaus_document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES klaus_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(384),
    metadata JSONB
);

-- ── Audit and failure log for every document lifecycle event ───────────
CREATE TABLE IF NOT EXISTS klaus_audit_logs (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES klaus_documents(id),
    event_type VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ── Indexes ────────────────────────────────────────────────────────────
-- Tier indexes (new)
CREATE INDEX IF NOT EXISTS idx_klaus_tiers_number ON klaus_acquisition_tiers(tier_number);
CREATE INDEX IF NOT EXISTS idx_klaus_tiers_status ON klaus_acquisition_tiers(status);

-- Source indexes
CREATE INDEX IF NOT EXISTS idx_klaus_sources_domain ON klaus_sources(domain);
CREATE INDEX IF NOT EXISTS idx_klaus_sources_status ON klaus_sources(status);

-- Document indexes
CREATE INDEX IF NOT EXISTS idx_klaus_docs_file_hash ON klaus_documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_review_status ON klaus_documents(review_status);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_category ON klaus_documents(category);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_source_id ON klaus_documents(source_id);

-- Authority record indexes (new)
CREATE INDEX IF NOT EXISTS idx_klaus_authority_type ON klaus_legal_authority_records(authority_type);
CREATE INDEX IF NOT EXISTS idx_klaus_authority_status ON klaus_legal_authority_records(status);
CREATE INDEX IF NOT EXISTS idx_klaus_authority_court ON klaus_legal_authority_records(court_identifier);

-- Chunk and audit indexes
CREATE INDEX IF NOT EXISTS idx_klaus_chunks_doc_id ON klaus_document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_klaus_audit_doc_id ON klaus_audit_logs(document_id);
CREATE INDEX IF NOT EXISTS idx_klaus_audit_event_type ON klaus_audit_logs(event_type);
"""

# ── Migration: add new columns and indexes to existing tables ─────────────
# These ALTER statements safely add columns that don't exist yet.
# IF NOT EXISTS (PostgreSQL 9.6+) avoids errors on re-run.
# Indexes for new columns are created here (not in SCHEMA_SQL) because the
# columns don't exist in existing tables until after the migration runs.

MIGRATION_SQL = """
-- Expand klaus_sources with tier assignment and trust classification
ALTER TABLE klaus_sources ADD COLUMN IF NOT EXISTS tier_id INTEGER REFERENCES klaus_acquisition_tiers(id);
ALTER TABLE klaus_sources ADD COLUMN IF NOT EXISTS source_type VARCHAR(64);
ALTER TABLE klaus_sources ADD COLUMN IF NOT EXISTS trust_level VARCHAR(32) DEFAULT 'unverified';
ALTER TABLE klaus_sources ADD COLUMN IF NOT EXISTS last_validated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE klaus_sources DROP CONSTRAINT IF EXISTS klaus_sources_jurisdiction_check;
ALTER TABLE klaus_sources ADD CONSTRAINT klaus_sources_jurisdiction_check CHECK (jurisdiction = 'Ghana');

-- Expand klaus_documents with tier assignment and jurisdiction enforcement
ALTER TABLE klaus_documents ADD COLUMN IF NOT EXISTS tier_id INTEGER REFERENCES klaus_acquisition_tiers(id);
ALTER TABLE klaus_documents DROP CONSTRAINT IF EXISTS klaus_documents_jurisdiction_check;
ALTER TABLE klaus_documents ADD CONSTRAINT klaus_documents_jurisdiction_check CHECK (jurisdiction = 'Ghana');

-- Indexes for new columns (safe to run multiple times)
CREATE INDEX IF NOT EXISTS idx_klaus_sources_tier_id ON klaus_sources(tier_id);
CREATE INDEX IF NOT EXISTS idx_klaus_sources_trust_level ON klaus_sources(trust_level);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_tier_id ON klaus_documents(tier_id);
"""

# ── Schema Constants ────────────────────────────────────────────────────

COPYRIGHT_CLASSIFICATIONS = (
    "public_domain",
    "open_license",
    "official_public_access",
    "copyright_protected",
    "unknown",
)

KNOWLEDGE_CATEGORIES = (
    "Constitutional Law",
    "Legislation",
    "Judiciary",
    "Legal Procedure",
    "International Law",
    "Legal Scholarship",
)

EVENT_TYPES = (
    "discovery",
    "download",
    "verification",
    "classification",
    "failure",
    "review",
)

SEVERITY_LEVELS = ("info", "warning", "error", "critical")

SOURCE_TIERS = {1: "official_government", 2: "recognized_institution", 3: "secondary"}

# ── 16-Tier Acquisition Priority System ─────────────────────────────────
# Per the Ghana Legal Brain Corpus Acquisition & Knowledge Foundation Layer directive.
# Each tier has: tier_number, name, category, priority weight, coverage target.

ACQUISITION_TIERS = {
    1:  {"name": "Constitutional Law",           "category": "Constitutional Law",     "weight": 1.00, "target": 5},
    2:  {"name": "Primary Legislation (Acts)",   "category": "Legislation",            "weight": 0.95, "target": 50},
    3:  {"name": "Subsidiary Legislation",       "category": "Legislation",            "weight": 0.90, "target": 30},
    4:  {"name": "Judicial Precedents",          "category": "Judiciary",              "weight": 0.85, "target": 50},
    5:  {"name": "Criminal Law",                 "category": "Legal Procedure",        "weight": 0.80, "target": 15},
    6:  {"name": "Commercial & Contract Law",    "category": "Legislation",            "weight": 0.75, "target": 20},
    7:  {"name": "Employment & Labour Law",      "category": "Legislation",            "weight": 0.70, "target": 15},
    8:  {"name": "Tax & Revenue Law",            "category": "Legislation",            "weight": 0.65, "target": 20},
    9:  {"name": "Property & Land Law",          "category": "Legislation",            "weight": 0.60, "target": 15},
    10: {"name": "Family & Succession Law",      "category": "Legislation",            "weight": 0.55, "target": 10},
    11: {"name": "Intellectual Property Law",    "category": "Legal Scholarship",      "weight": 0.50, "target": 10},
    12: {"name": "Technology & Data Law",        "category": "Legislation",            "weight": 0.45, "target": 10},
    13: {"name": "Banking & Finance Law",        "category": "Legislation",            "weight": 0.40, "target": 15},
    14: {"name": "Government & Administrative",  "category": "Legal Procedure",        "weight": 0.35, "target": 15},
    15: {"name": "Case Law & Digests",           "category": "Judiciary",              "weight": 0.30, "target": 30},
    16: {"name": "Official Publications",        "category": "Legal Scholarship",      "weight": 0.25, "target": 30},
}

# Authority types for the Legal Authority Record
AUTHORITY_TYPES = (
    "constitution",
    "statute",
    "regulation",
    "case",
    "treaty",
    "circular",
    "gazette",
    "report",
    "form",
    "bill",
)


def get_tier_priority_band(tier_number: int) -> str:
    """Return the acquisition priority band for a given tier.

    Band 1 (daily):      T1-T4   — Constitutional, Acts, Subsidiary, Precedents
    Band 2 (every 3 days): T5-T8  — Criminal, Commercial, Employment, Tax
    Band 3 (weekly):     T9-T12  — Property, Family, IP, Technology
    Band 4 (monthly):    T13-T16 — Banking, Government, Case Law, Publications
    """
    if 1 <= tier_number <= 4:
        return "daily"
    elif 5 <= tier_number <= 8:
        return "every_3_days"
    elif 9 <= tier_number <= 12:
        return "weekly"
    else:
        return "monthly"
