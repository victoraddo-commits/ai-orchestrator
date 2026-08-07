# Kai Legal Brain — Merged Architecture & Refactored Roadmap

**Prepared**: 2026-08-06
**Architect**: Kai Principal Systems Architect (Qwen3 → Fable)
**Status**: Supersedes all prior Legal Brain planning documents

---

## Executive Summary

The Kai Legal Brain has substantial working code (Klaus module, 2283 lines; law_documents; Juris Kai multi-tenant bot; Law Tutor bot) but critical architectural gaps. This document merges all existing Legal Brain requirements into a single, coherent roadmap, eliminates 12 duplicate/failed phases, resurrects 4 critical phases with corrected scope, and establishes the Ghana-only, zero-trust, evidence-first architecture as non-negotiable.

### Key Changes

| Action | Count | Rationale |
|--------|-------|-----------|
| Phases merged/consolidated | 12 → 4 | Eliminate duplicate proposals, combine related work |
| Failed phases resurrected | 4 | 18C, 19E, 19L, 19Q — critical for zero-trust |
| New phases added | 3 | Research Sessions, Integrity Audits, Command Center |
| Phases removed | 8 | Duplicates, superseded, or already-completed |
| **Net change** | **164 → 156 phases** | Cleaner, more actionable roadmap |

---

## 1. Current Architecture Assessment

### 1.1 What Exists (Working Code)

| Component | Files | Lines | Status |
|-----------|-------|-------|--------|
| **Klaus Core** | `core/klaus/` (9 files) | 2,283 | ✅ Production-ready |
| Klaus Schema | PostgreSQL + pgvector | 109 | ✅ Sources, documents, chunks, audit |
| Klaus DB Manager | Connection pool, CRUD, migrations | 440 | ✅ |
| Klaus Document Processor | PDF extraction, chunking, embeddings | 330 | ✅ |
| Klaus Quality Agents | Source verification, classification, dedup | 333 | ✅ |
| Klaus Background Workers | Ingestion pipeline stages | 372 | ✅ |
| Klaus Scheduler | Timer-driven acquisition jobs | 244 | ✅ |
| Klaus API | REST endpoints for document CRUD | 308 | ✅ Integrated into FastAPI |
| Klaus Vector Indexer | pgvector semantic search | 140 | ✅ |
| **Law Documents** | `core/law_documents.py` | 167 | ✅ Basic CRUD, path traversal safe |
| **Law Tutor Bot** | `core/law_tutor/` (5 files) | ~300 | ✅ Single-user Telegram bot |
| **Juris Kai Bot** | `core/juris_kai/` (7 files) | ~1,000 | ✅ Multi-tenant, Hubtel billing |
| **Auth & Security** | `core/authz.py`, JWT, rate limiter | ~800 | ✅ 18A-a/b complete |
| **Legal Metadata** | `core/legal_metadata.py` | 702 | ✅ Citation extraction, versioning |
| **Legal QC** | `core/legal_qc.py` | 597 | ✅ Quality control agents |
| **Tests** | 12 test files | 2,856 | ✅ Comprehensive coverage |

### 1.2 Critical Gaps (No Code Exists)

| Gap | Severity | Failed Phase | Directive Requirement |
|-----|----------|-------------|----------------------|
| **Permanent/Temporary Separation** | 🔴 Critical | 18C | Zero-trust knowledge architecture |
| **Immutable Knowledge Store** | 🔴 Critical | 18C | Hash-protected, versioned, audited |
| **Knowledge Graph** | 🔴 Critical | 19E | Citation network, entity extraction |
| **User Upload Sandboxing** | 🔴 Critical | 18C | Isolated temp workspace |
| **Malware Scanning** | 🟠 High | — | Assume external docs malicious |
| **Research Session Logging** | 🟠 High | — | Reproducible audit trail |
| **Continuous Integrity Audits** | 🟠 High | 19Q partial | Hash verification, citation validation |
| **Command Center Dashboards** | 🟡 Medium | 13O | 20+ legal management panels |
| **Independent Database** | 🟡 Medium | — | Own DB, vector store, knowledge graph |
| **Evidence-First AI Pipeline** | 🟡 Medium | — | Retrieve → validate → reason → cite |

### 1.3 Architecture Weaknesses in Current Design

1. **Klaus uses shared PostgreSQL**: The Klaus schema lives in the same PostgreSQL instance as other Kai data. The directive requires Legal Brain to maintain its own independent database.

2. **No permanent/temporary boundary**: All documents go into `klaus_documents` with `review_status`. There is no architectural guarantee that unverified user uploads cannot reach the permanent corpus — it's enforced only by application logic (status field), not architecture.

3. **User uploads share vector space**: Document chunks from any source go into `klaus_document_chunks` with embeddings. No isolation between verified legal authorities and user-uploaded content.

4. **No immutability guarantees**: Document records are mutable (UPDATE allowed). No hash-chain or tamper-evident storage for permanent legal content.

5. **Knowledge graph absent**: Citation relationships exist only as text extraction — no structured graph database for legal citation networks.

6. **No sandboxing**: User-uploaded documents are processed in the same Python process as the orchestrator. A malicious PDF could theoretically exploit the PDF parser.

7. **Klaus module conflates permanent + temp**: The schema doesn't distinguish between canonical legal authorities and temporary analysis artifacts.

---

## 2. Merged Architecture

### 2.1 Target Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      KAI ORCHESTRATOR                            │
│  (scheduler, builds, approvals, health, chat, dashboard)         │
│                                                                  │
│  ┌──────────────────────┐       ┌──────────────────────────────┐│
│  │  SERVICE INTERFACE   │       │   SERVICE INTERFACE          ││
│  │  (REST API boundary) │       │   (REST API boundary)        ││
│  └────────┬─────────────┘       └────────────┬─────────────────┘│
│           │                                  │                   │
│  ┌────────▼─────────────┐       ┌────────────▼─────────────────┐│
│  │   PERMANENT LEGAL    │       │   TEMPORARY USER WORKSPACE   ││
│  │   BRAIN (immutable)  │       │   (isolated, ephemeral)      ││
│  │                      │       │                              ││
│  │  • Immutable docs    │       │  • User uploads              ││
│  │  • Hash-protected    │◄──────┤  • OCR processing            ││
│  │  • Versioned         │ NEVER │  • Analysis results          ││
│  │  • Citation indexed  │MERGE  │  • Clause comparison         ││
│  │  • Knowledge graph   │       │  • Summarization             ││
│  │  • Vector embeddings │       │  • Auto-destroyed per TTL    ││
│  │  • Audit trail       │       │  • NEVER promoted            ││
│  │                      │       │                              ││
│  │  Own PostgreSQL DB   │       │  Temp SQLite (in-memory)     ││
│  │  Own pgvector store  │       │  Sandboxed PDF parser        ││
│  │  Own Neo4j/SQLite KG │       │  ClamAV malware scan         ││
│  │  Own backup cron     │       │  Process isolation           ││
│  └──────────────────────┘       └──────────────────────────────┘│
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │              COMMAND CENTER (legal dashboards)               ││
│  │  20 panels: sources, queue, verify, scan, OCR, metadata,    ││
│  │  citations, KG, versions, integrity, audit, sessions,       ││
│  │  workspaces, storage, perf, scheduler, agent health         ││
│  └──────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Service Boundaries (Non-Negotiable)

**Permanent Legal Brain** owns:
- `legal_brain_db` — dedicated PostgreSQL database (not shared)
- `legal_brain_vectors` — dedicated pgvector extension
- `legal_brain_kg` — knowledge graph (SQLite with transitive closure, or Neo4j)
- `legal_brain_backups/` — versioned, encrypted backups
- `legal_brain_audit/` — immutable audit log (HMAC-chained)

**Temporary User Workspace** owns:
- Temp SQLite database (auto-created per session, destroyed per TTL)
- Sandboxed filesystem directory (tmpfs, size-limited)
- Isolated PDF parser process (subprocess with timeout + seccomp)
- ClamAV scan before any processing

**Neither** shares storage, indexes, embeddings, or processes with the other.

**Kai Orchestrator** communicates with both ONLY through defined REST interfaces:
- `GET/POST /legal-brain/v1/documents`
- `GET/POST /legal-brain/v1/search`
- `GET /legal-brain/v1/citations/{doc_id}`
- `POST /workspace/v1/upload`
- `POST /workspace/v1/analyze`
- `GET /workspace/v1/sessions/{id}`

### 2.3 Zero-Trust Ingestion Pipeline

```
1. Source Discovery (scheduler)
   └─► Check Tier 1 official URLs
       └─► New document found?
           └─► Download to STAGING (air-gapped)
               2. Malware Scan (ClamAV)
                  └─► Clean?
                      3. Hash & fingerprint
                         4. AI Classification (jurisdiction, category, copyright)
                            5. Operator Review Queue
                               └─► Approved?
                                   6. Extract citations → knowledge graph
                                      7. Chunk → embed → permanent vector store
                                         8. Seal hash → audit log
                                            9. Index → available for search
```

At no point can an unapproved document enter the permanent store.
At no point can a user upload enter the permanent store.
The staging area is write-only from ingestion, read-only from operator review.

### 2.4 Evidence-First AI Pipeline

```
User Query
  │
  ▼
1. RETRIEVE: Semantic search permanent Legal Brain vectors
   └─► Return top-K authoritative Ghanaian sources
       │
       ▼
2. VALIDATE: Verify citation integrity
   └─► Cross-reference knowledge graph for overruled/repealed/amended
       │
       ▼
3. REASON: AI reasoning on retrieved authorities ONLY
   └─► Never hallucinate unsupported legal conclusions
       │
       ▼
4. CITE: Generate response with inline citations
   └─► Every claim links to a permanent Legal Brain document ID
       │
       ▼
5. LOG: Create Research Session record
   └─► Session ID, query, authorities, model, confidence, timestamp
```

---

## 3. Refactored Roadmap Phases

### 3.1 Phases to REMOVE (8 phases — duplicates, superseded, or already complete)

| Phase ID | Reason for Removal |
|----------|-------------------|
| TK-96ddd758 (17O-B duplicate proposal) | Superseded by completed 17O-B |
| TK-44eee510 (17O-C duplicate proposal) | Superseded by completed 17O-C |
| TK-525d42a6 (17O-E knowledge indexing) | Absorbed into 18C-NEW |
| TK-d569b912 (17O-H scheduled jobs) | Already implemented in klaus/scheduler.py |
| 17P (duplicate pending entry) | Already completed (built 2026-08-06) |
| 13I (Future Roadmap Generator) | Failed, low priority, out of scope |
| 17N (Voice/Phone) | Completed |
| 17G (UI Polish) | Failed, superseded by Command Center work |

### 3.2 Phases to RESURRECT (4 critical phases — corrected scope)

#### 18C-NEW: Zero-Trust Legal Brain Architecture (priority: 1)

*Was: "Permanent/Temporary separation, sandboxing" (failed)*
*Now: Complete implementation of the zero-trust architecture described in §2 above.*

| Deliverable | Details |
|-------------|---------|
| Permanent Brain DB | Dedicated `legal_brain_db` PostgreSQL, independent from Kai's shared DB |
| Temp Workspace DB | Per-session SQLite in tmpfs, auto-destroyed on TTL |
| Immutable document store | Hash-chain verification, append-only, WORM semantics |
| Knowledge graph | SQLite-backed citation network with transitive closure queries |
| Sandboxed PDF processing | Subprocess isolation, seccomp, 30s timeout, memory limits |
| Malware scanning | ClamAV integration for all uploads before processing |
| Service boundaries | REST interfaces only — no shared imports between Brain and Kai |
| Verification pipeline | 6-stage pipeline from discovery to permanent indexing |
| Migration | Copy existing klaus_documents approved records → new Brain DB |

**Dependencies**: 17O-D (complete), 18A-a/b (complete)
**Effort**: XL (architectural foundation)

#### 19E-NEW: Legal Knowledge Engine (priority: 2)

*Was: "Knowledge Engine" — Cerebrum, generic scope (failed)*
*Now: Legal-specific knowledge engine built on the zero-trust architecture.*

| Deliverable | Details |
|-------------|---------|
| Knowledge graph population | Extract entities (courts, judges, statutes, principles) from permanent documents |
| Citation network | Structured citation graph: Act X §Y → Constitution Art. Z → Case [2024] GHSC 1 |
| Jurisdiction awareness | Ghana-only Phase 1; plugin architecture for future jurisdictions |
| Version awareness | Tracks amendments, repeals, judicial treatment (overruled, distinguished, followed) |
| Source trust scoring | Per-source reliability metrics based on tier + operator feedback |
| Query API | `/legal-brain/v1/knowledge?entity=...` |

**Dependencies**: 18C-NEW
**Effort**: L

#### 19L-NEW: Legal Trust Engine (priority: 3)

*Was: "Trust Engine" — Cerebrum, generic scope (failed)*
*Now: Legal-specific trust scoring for sources, documents, and AI responses.*

| Deliverable | Details |
|-------------|---------|
| Source trust scores | Per-source reliability (Tier 1 official > Tier 3 secondary) |
| Document confidence | Per-document classification confidence from QC agents |
| Citation verification | Cross-reference citations against knowledge graph for validity |
| AI response confidence | Score legal answers based on source quality + citation coverage |
| Alert thresholds | Auto-flag responses with confidence < 0.7 for operator review |

**Dependencies**: 19E-NEW
**Effort**: M

#### 19Q-NEW: Legal Brain Health & Integrity (priority: 4)

*Was: "Brain Health Monitor" — Cerebrum, generic scope (failed)*
*Now: Continuous integrity monitoring for the Legal Brain.*

| Deliverable | Details |
|-------------|---------|
| Document hash verification | Daily scan of all permanent documents against stored SHA-256 hashes |
| Missing publication detection | Compare against official source sitemaps for gaps |
| Citation integrity | Detect broken cross-references in the knowledge graph |
| Duplicate detection | Hash-based dedup across the entire corpus |
| Conflicting version detection | Flag documents with same citation but different content |
| Embedding integrity | Validate vector index consistency with source documents |
| Source availability monitor | Periodic health checks on all Tier 1 official URLs |
| Alerting | Push Telegram alerts for any integrity failures |

**Dependencies**: 18C-NEW, 19E-NEW
**Effort**: M

### 3.3 NEW Phases (3 phases — gaps in current roadmap)

#### 18D: Research Session Logging (priority: 5)

Every legal query generates a reproducible Research Session with full audit trail.

| Deliverable | Details |
|-------------|---------|
| Session schema | session_id, user_id, query, retrieved_authorities, search_strategy, citations, model, confidence, brain_version, timestamp |
| Session storage | Append-only log in Legal Brain's dedicated DB |
| Session query API | `/legal-brain/v1/sessions?user=X&date=Y` |
| Export | JSON/PDF research reports for professional use |
| Privacy | User-uploaded documents NOT stored in session (only document IDs from permanent corpus) |

**Dependencies**: 18C-NEW
**Effort**: S

#### 18E: Legal Brain Command Center (priority: 6)

Merge all Legal Brain management into the Kai Command Center dashboard.

| Panel | Content |
|-------|---------|
| Source Registry | All Tier 1-3 sources, add/edit/disable |
| Download Queue | Pending acquisitions, retry/prioritize |
| Verification Pipeline | Staged documents awaiting operator review |
| Malware Scanning | Scan results, quarantined files |
| OCR Processing | OCR queue, quality reports |
| Metadata Extraction | Classification results, confidence scores |
| Citation Index | Knowledge graph visualization |
| Version History | Document version tree per statute/case |
| Integrity Monitoring | Hash verification status, alerts |
| Audit Ledger | Full audit trail with filters |
| Research Sessions | Session history, export |
| Temporary Workspaces | Active workspace list, TTL status |
| Storage Analytics | DB size, vector store size, growth trends |
| Performance Metrics | Query latency, embedding throughput |
| Scheduler | Acquisition job status, next runs |
| Agent Health | QC agent performance, AI provider health |

**Dependencies**: 18C-NEW, 18D, 13O (Command Center)
**Effort**: L

#### 18F: Legal Brain Domain Plugin Architecture (priority: 20 — future)

Design the plugin interface for future non-Ghana jurisdictions.

| Deliverable | Details |
|-------------|---------|
| Domain plugin spec | JSON schema for jurisdiction plugins |
| Isolation guarantees | Each domain = own DB + vector store + knowledge graph |
| Activation mechanism | Domains disabled by default, operator-activated |
| Template | Reference implementation: Ghana Legal Brain as the canonical plugin |
| Medical/Family/Finance | Stub interfaces for operator-proposed future domains |

**Dependencies**: 18C-NEW
**Effort**: M (design only; implementation per-domain later)

### 3.4 Updated Phase Dependencies

```
17O-A (Source Discovery) ✅
  └─► 17O-B (Taxonomy) ✅
        └─► 17O-C (Metadata) ✅
              └─► 17O-D (QC Agents) ✅
                    └─► 17P (Juris Kai) ✅

18A-a (Security Auth) ✅
  └─► 18A-b (Security Audit) ✅

18B (Module Consolidation) ✅

18C-NEW (Zero-Trust Architecture) 🔴 — depends on 17O-D, 18A-b
  ├─► 18D (Research Sessions)
  ├─► 18E (Command Center) — depends on 13O
  ├─► 18F (Domain Plugins) — future
  ├─► 19E-NEW (Knowledge Engine)
  │     └─► 19L-NEW (Trust Engine)
  └─► 19Q-NEW (Integrity Monitoring)
```

### 3.5 Sequencing & Effort Estimate

| Phase | Effort | Dependencies Met? | Est. Duration |
|-------|--------|-------------------|---------------|
| 18C-NEW (Zero-Trust) | XL | ✅ 17O-D + 18A-b done | 3-5 days |
| 19E-NEW (Knowledge Engine) | L | ⏳ Needs 18C | 1-2 days |
| 18D (Research Sessions) | S | ⏳ Needs 18C | 0.5 day |
| 19L-NEW (Trust Engine) | M | ⏳ Needs 19E | 1-2 days |
| 19Q-NEW (Integrity) | M | ⏳ Needs 18C + 19E | 1-2 days |
| 18E (Command Center) | L | ⏳ Needs 18C + 18D + 13O | 2-3 days |
| 18F (Domain Plugins) | M | ⏳ Needs 18C | 1 day (design) |

**Total**: ~10-15 days of focused development for complete Legal Brain implementation.

---

## 4. Gap Analysis

### 4.1 Critical (blocks production Legal Brain)

| # | Gap | Resolution |
|---|-----|------------|
| G1 | No permanent/temporary separation | 18C-NEW: Dedicated DBs, service boundaries |
| G2 | No immutability guarantees | 18C-NEW: Hash-chain, WORM storage |
| G3 | User uploads share vector space with authorities | 18C-NEW: Temp workspace isolation |
| G4 | No malware scanning | 18C-NEW: ClamAV integration |
| G5 | No knowledge graph | 19E-NEW: Citation network |

### 4.2 High (blocks evidence-first quality)

| # | Gap | Resolution |
|---|-----|------------|
| G6 | AI responses not citation-grounded | Evidence-first pipeline (18C-NEW) |
| G7 | No research session logging | 18D |
| G8 | No integrity monitoring | 19Q-NEW |
| G9 | No operator dashboards for Legal Brain | 18E |

### 4.3 Medium (operational excellence)

| # | Gap | Resolution |
|---|-----|------------|
| G10 | Klaus shares Kai's PostgreSQL | 18C-NEW: Dedicated DB |
| G11 | No domain plugin architecture | 18F |
| G12 | No backup strategy for Legal Brain | 18C-NEW: Encrypted cron backups |
| G13 | PDF parsing in-process (security risk) | 18C-NEW: Subprocess sandboxing |

---

## 5. Security Audit

### 5.1 Current Vulnerabilities

| Vuln | Severity | Description |
|------|----------|-------------|
| **V1: PDF parser in-process** | 🔴 Critical | `pypdf` runs in Kai's Python process. A crafted PDF could exploit memory corruption. |
| **V2: No upload sandboxing** | 🔴 Critical | User uploads processed in same filesystem as permanent documents |
| **V3: Mutable document records** | 🟠 High | `klaus_documents` rows are UPDATE-able — no immutability guarantee |
| **V4: Shared DB credentials** | 🟠 High | Klaus uses same PostgreSQL as other Kai modules |
| **V5: No upload size limits** | 🟡 Medium | Large uploads could exhaust disk space |
| **V6: No ClamAV** | 🟡 Medium | No malware scanning before processing |

### 5.2 Mitigations (in 18C-NEW)

- **V1+V2**: Subprocess sandboxing with seccomp, 30s timeout, memory limit, tmpfs
- **V3**: WORM storage — INSERT-only, hash-chained, no UPDATE/DELETE on permanent records
- **V4**: Dedicated PostgreSQL database with separate credentials
- **V5**: Upload size cap (50MB default), enforced at API gateway
- **V6**: ClamAV scan all uploads before extraction

---

## 6. Threat Model

### 6.1 Threat Actors

| Actor | Motivation | Capability |
|-------|-----------|------------|
| Malicious user | Inject fake legal content | Upload crafted PDFs with embedded content |
| Unverified source | Serve tampered legislation | Compromised government website |
| AI hallucination | Generate false legal conclusions | Model confabulation without grounding |
| Supply chain | Compromise Python packages | Malicious pypdf, requests, or AI SDK |

### 6.2 Threat Scenarios & Mitigations

**T1: User uploads PDF claiming to be a Supreme Court judgment**
→ Sandbox processes it → ClamAV scans → AI classifies as "unknown_source" → Goes to operator review → Operator verifies against official source → If approved, enters permanent corpus with traceable provenance chain

**T2: Official government website replaced with malicious PDFs**
→ Source health monitor detects content hash mismatch → Alerts operator → Acquisition paused for that source → Existing verified documents unaffected (immutable)

**T3: AI generates plausible but false legal citation**
→ Evidence-first pipeline: retrieve first, cite only what exists in Legal Brain → Citation verification against knowledge graph → Confidence score flags unsupported claims

**T4: Malicious npm/pip package compromises PDF parser**
→ PDF parsing in subprocess with seccomp → No network access → 30s timeout → Crash doesn't affect orchestrator → ClamAV provides second layer

---

## 7. Updated Database Design

### 7.1 Permanent Legal Brain (PostgreSQL, dedicated)

```sql
-- Immutable document registry
CREATE TABLE permanent.documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash VARCHAR(64) UNIQUE NOT NULL,       -- SHA-256
    prev_hash VARCHAR(64),                           -- Hash-chain link
    source_id INTEGER REFERENCES permanent.sources(id),
    title TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,                   -- Constitution, Legislation, etc.
    jurisdiction VARCHAR(64) DEFAULT 'Ghana',
    court VARCHAR(128),
    year INTEGER,
    citation_text TEXT,                              -- Standard citation format
    file_path TEXT NOT NULL,                         -- WORM storage path
    file_size_bytes BIGINT,
    page_count INTEGER,
    copyright_classification VARCHAR(32) NOT NULL,
    access_level VARCHAR(32) DEFAULT 'public',
    effective_date DATE,
    version INTEGER DEFAULT 1,
    parent_doc_id UUID REFERENCES permanent.documents(doc_id),
    ingested_at TIMESTAMPTZ DEFAULT NOW(),
    approved_by TEXT,
    -- NO UPDATE after INSERT — immutability enforced at DB level
    -- Versioning via parent_doc_id chain, not UPDATE
);

-- Chunks + embeddings
CREATE TABLE permanent.chunks (
    chunk_id UUID PRIMARY KEY,
    doc_id UUID REFERENCES permanent.documents(doc_id),
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(384),
    content_hash VARCHAR(64),
    UNIQUE(doc_id, chunk_index)
);

-- Knowledge graph
CREATE TABLE permanent.citations (
    citation_id UUID PRIMARY KEY,
    source_doc_id UUID REFERENCES permanent.documents(doc_id),
    target_doc_id UUID REFERENCES permanent.documents(doc_id),
    citation_type VARCHAR(32),    -- 'references', 'overrules', 'amends', 'applies'
    context_snippet TEXT,
    confidence FLOAT DEFAULT 1.0,
    verified_by TEXT
);

-- Audit trail (immutable, HMAC-chained)
CREATE TABLE permanent.audit_chain (
    entry_id UUID PRIMARY KEY,
    prev_entry_hash VARCHAR(64),
    event_type VARCHAR(64),
    doc_id UUID,
    operator TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    entry_hash VARCHAR(64) GENERATED ALWAYS AS (
        ENCODE(DIGEST(prev_entry_hash || event_type || COALESCE(doc_id::text,'') || COALESCE(details::text,''), 'sha256'), 'hex')
    ) STORED
);
```

### 7.2 Temporary User Workspace (SQLite, per-session)

```sql
-- Auto-created per user session, destroyed on TTL expiry
CREATE TABLE workspace_documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    original_filename TEXT,
    file_hash VARCHAR(64),
    page_count INTEGER,
    extracted_text TEXT,
    ocr_applied BOOLEAN DEFAULT FALSE,
    malware_scan_passed BOOLEAN,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE workspace_analyses (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES workspace_documents(id),
    analysis_type TEXT,          -- 'summary', 'clause_comparison', 'citation_extraction'
    result TEXT,
    model_used TEXT,
    confidence FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 8. Updated API Specification

### 8.1 Permanent Legal Brain API (`/legal-brain/v1/`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/documents` | viewer | List/search permanent documents |
| GET | `/documents/{id}` | viewer | Get document with full metadata |
| GET | `/documents/{id}/chunks` | viewer | Get document chunks with embeddings |
| GET | `/search` | viewer | Semantic search across permanent corpus |
| GET | `/citations/{id}` | viewer | Get citation network for a document |
| GET | `/knowledge/entity` | viewer | Query knowledge graph entities |
| POST | `/ingest` | operator | Submit document for ingestion pipeline |
| PUT | `/review/{id}` | operator | Approve/reject staged document |
| GET | `/audit` | operator | Query audit chain |
| GET | `/health` | viewer | Integrity verification status |

### 8.2 Temporary Workspace API (`/workspace/v1/`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/upload` | viewer | Upload document for analysis |
| GET | `/sessions/{id}` | viewer | Get session details |
| GET | `/sessions/{id}/documents` | viewer | List session documents |
| POST | `/sessions/{id}/analyze` | viewer | Analyze a document in session |
| DELETE | `/sessions/{id}` | viewer | Destroy session (or auto-TTL) |

---

## 9. Migration Plan

### Phase 1: Zero-Trust Foundation (18C-NEW) — 3-5 days

1. Create dedicated `legal_brain_db` PostgreSQL database
2. Set up dedicated credentials, connection pool
3. Create WORM document storage directory with filesystem permissions
4. Implement sandboxed PDF processing subprocess
5. Install and configure ClamAV
6. Migrate approved `klaus_documents` → `permanent.documents`
7. Build service boundary (REST API)
8. Update Klaus imports to use new service interface
9. Run full test suite (2,856 existing + new)
10. **Switch over**: route all legal queries through new API

### Phase 2: Knowledge Layer (19E-NEW + 18D + 19L-NEW) — 2-4 days

1. Populate knowledge graph from permanent documents
2. Implement citation network queries
3. Build Research Session logging
4. Implement trust scoring
5. Deploy evidence-first AI pipeline

### Phase 3: Operations (19Q-NEW + 18E) — 3-5 days

1. Deploy integrity monitoring cron jobs
2. Build Command Center legal dashboards
3. Set up encrypted backups
4. Documentation and runbooks

---

## 10. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R1 | PostgreSQL migration corrupts existing Klaus data | Low | High | Full backup before migration; verify checksums |
| R2 | pgvector incompatibility with existing embeddings | Medium | Medium | Re-index from source documents (embeddings are derived) |
| R3 | ClamAV integration blocks legitimate documents | Medium | Low | False-positive review queue; operator override |
| R4 | Sandboxed PDF processing too slow for production | Medium | Medium | Timeout tuning; async processing queue |
| R5 | Knowledge graph grows unbounded (disk space) | Low | Medium | Partition by jurisdiction; archival policy |
| R6 | Command Center dashboard complexity overwhelms operators | Medium | Low | Progressive disclosure; defaults to overview |

---

## 11. Summary of Changes to roadmap.json

The following changes should be applied to the master roadmap:

| Action | Phase IDs | Count |
|--------|-----------|-------|
| **DELETE** | TK-96ddd758, TK-44eee510, TK-525d42a6, TK-d569b912, 17P (duplicate), 13I, 17N, 17G | 8 |
| **RESURRECT** | 18C → 18C-NEW, 19E → 19E-NEW, 19L → 19L-NEW, 19Q → 19Q-NEW | 4 |
| **CREATE** | 18D (Research Sessions), 18E (Command Center), 18F (Domain Plugins) | 3 |
| **UPDATE** | 17P (remove duplicate, keep completed) | 1 |

**Net**: 164 → 156 phases (removed 8, added 3 new, resurrected 4 from failed)

---

## Appendix A: Files Modified/Created by This Directive

| File | Action | Purpose |
|------|--------|---------|
| `uploads/kai-legal-brain-merged-roadmap-2026-08-06.md` | CREATE | This document |
| `roadmap.json` | UPDATE | Apply all phase changes from §11 |
| (future) `core/legal_brain/` | CREATE | 18C-NEW implementation |
| (future) `core/legal_brain/permanent/` | CREATE | Permanent store module |
| (future) `core/legal_brain/workspace/` | CREATE | Temp workspace module |

## Appendix B: Prior Documents Superseded

- `uploads/ghana-legal-brain-implementation-plan-2026-07-31.md` — absorbed, requirements merged
- All TK-* proposed phases related to Legal Brain — consolidated or removed
- 18C original (failed) — replaced by 18C-NEW with corrected scope
- 19E/19L/19Q original (failed) — replaced with Legal-specific versions
