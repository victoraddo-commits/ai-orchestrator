"""
KLAUS Legal Knowledge Acquisition System - Database Schema

SQL schema matching the approved Phase 17O implementation plan:
- Source tiering (1=official gov, 2=recognized institution, 3=secondary)
- Copyright classification before ingestion
- Document versioning with parent references
- pgvector embeddings for semantic search
- Audit trail for all operations
"""

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgvector";

-- Source catalog: recursive seed discovery + reliability tracking
CREATE TABLE IF NOT EXISTS klaus_sources (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    tier INTEGER CHECK (tier BETWEEN 1 AND 3),
    jurisdiction VARCHAR(64) DEFAULT 'Ghana',
    last_discovered TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(32) DEFAULT 'active',
    reliability_score FLOAT DEFAULT 1.0
);

-- Document storage: ingested legal materials with versioning
CREATE TABLE IF NOT EXISTS klaus_documents (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES klaus_sources(id),
    title TEXT NOT NULL,
    file_hash VARCHAR(64) UNIQUE NOT NULL,
    file_path TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,
    jurisdiction VARCHAR(64) NOT NULL,
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

-- Document chunks with vector embeddings for semantic search
CREATE TABLE IF NOT EXISTS klaus_document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES klaus_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(384),
    metadata JSONB
);

-- Audit and failure log for every document lifecycle event
CREATE TABLE IF NOT EXISTS klaus_audit_logs (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES klaus_documents(id),
    event_type VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_klaus_sources_domain ON klaus_sources(domain);
CREATE INDEX IF NOT EXISTS idx_klaus_sources_status ON klaus_sources(status);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_file_hash ON klaus_documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_review_status ON klaus_documents(review_status);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_category ON klaus_documents(category);
CREATE INDEX IF NOT EXISTS idx_klaus_docs_source_id ON klaus_documents(source_id);
CREATE INDEX IF NOT EXISTS idx_klaus_chunks_doc_id ON klaus_document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_klaus_audit_doc_id ON klaus_audit_logs(document_id);
CREATE INDEX IF NOT EXISTS idx_klaus_audit_event_type ON klaus_audit_logs(event_type);
"""

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
