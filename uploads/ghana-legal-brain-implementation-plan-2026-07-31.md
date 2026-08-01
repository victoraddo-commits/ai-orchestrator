# Ghana Legal Brain — Implementation Plan (Phase 17O)

**Pre-implementation design proposal — requires operator review and approval before any coding begins.**

Prepared 2026-07-31. This document merges the operator's "Kai Legal Knowledge Acquisition & Update System" spec into roadmap phase 17O and satisfies its own requirement: a 10-part plan must precede any acquisition code.

## Table of Contents

1. Current Legal Knowledge Architecture Review
2. Required Services
3. Database Design
4. Storage Requirements
5. Source List
6. Update Schedule
7. Agent Responsibilities
8. Security Considerations
9. Copyright Compliance Approach
10. Implementation Roadmap
11. Risk Summary

---

The "Kai" ecosystem currently possesses foundational pieces across storage, interface, and AI routing:
*   **AI Gateway (`core.ai.ai_router.delegate`)**: A centralized entry point for all AI model interaction across the ecosystem, supporting multi-provider fallback (Gemini, Claude via OpenRouter, Groq, MiniMax, DeepSeek) and usage tracking.
*   **Document Storage (`core.law_documents`)**: A standalone module for uploaded legal document storage and text extraction (using `pypdf`). It includes path-traversal safety, basic CRUD functions (`save_document`, `list_documents`, `get_document`, `get_document_text`, `delete_document`), and configuration via `AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR`. By design, it has zero operational imports.
*   **Interfaces**: 
    *   *Law Tutor*: A private Telegram bot (`core.law_tutor`) for a single law student user, currently isolated from `core.law_documents`.
    *   *Juris Kai*: A second, separate Telegram bot (`@Juriskai_bot`) with a verified bot token, intended as a paid, multi-tenant legal assistant.
    *   *Kai Dashboard*: Contains a built-in "Library" tab that calls `core.law_documents` via newly implemented POST/GET/DELETE `/kai/law-documents` endpoints in `core/api.py`.
*   **Automation & Alerting**: Systemd timers and watchdog patterns exist for background tasks, alongside the `pushTelegramAlert()` Telegram push-notification relay.

### What Is Missing
To transition from basic document storage to an authoritative legal knowledge base for Ghana, several critical components are entirely absent:
*   **Vector Search & Embeddings**: No vector database or embedding pipeline exists anywhere in the stack. Semantic retrieval of case law, legislation, and constitutional articles is currently impossible.
*   **OCR Capabilities**: No OCR tooling exists to process scanned paper judgments, gazettes, or historical legislation often found in Tier 1 and Tier 2 Ghanaian legal sources.
*   **Automated Source Ingestion & Discovery**: No systematic pipeline exists to discover, fetch, or monitor updates from official sources (e.g., Ghana Parliament, Judiciary).
*   **Taxonomy & Tiering Enforcement**: `core.law_documents` stores files flatly. It lacks structural enforcement of the operator's 7-category taxonomy (Constitution, Legislation, Case Law, Procedure, Specialized Law, International Law, Legal Reasoning) and source tiering (Tiers 1–3).
*   **Copyright Classification & Approval Workflows**: No automated or enforced metadata capture for copyright status (Categories 1–5), nor an operator review queue to prevent unverified user uploads from mixing into the shared knowledge base.
*   **Citation & Amendment Tracking**: No mechanism to track legislative amendments, repeals, or judicial treatment (e.g., whether a case has been overruled or distinguished).

### Why `core.law_documents` Alone Is Insufficient
`core.law_documents` was designed solely as a basic document repository with text extraction via `pypdf`. It is insufficient for an authoritative legal knowledge base because:
1.  **Lack of Semantic Context**: It performs raw text extraction without chunking, embedding, or semantic mapping, making precise legal research impossible.
2.  **No Structural Hierarchy**: It does not recognize legal taxonomies, document versions, amendments, or citation networks.
3.  **Boundary Constraints**: By strict design, `core.law_documents` has *zero operational imports* and cannot interact with build managers, approval workflows, or deployment pipelines. It cannot self-update or gate content based on operator reviews.
4.  **Security & Data Integrity Vulnerabilities**: Without an integrated copyright classification filter and operator approval queue, regular user uploads from multi-tenant environments (like Juris Kai) risk contaminating the authoritative shared knowledge base.

---

## 2. Required Services

To build the Ghana Legal Brain without paid cloud services or complex enterprise infrastructure, the system is broken down into lightweight, modular services. Every AI reasoning task (classification, metadata extraction, summarization) must use the existing **`core.ai.ai_router.delegate()`** gateway.

### 1. Discovery Service
*   **Responsibility**: Periodically check known official URLs, public repositories, and RSS/web feeds for new Ghana legislation, Gazette notices, and Supreme Court judgments.
*   **Why it's separate**: Keeps network-fetching logic and scraping logic isolated from core storage and database operations.
*   **Dependencies**: Systemd timer / Python script, standard requests/BeautifulSoup libraries.
*   **v1 Implementation**: A scheduled Python script running via a systemd timer (reusing the established pattern) that queries specific target endpoints, logs found items, and pushes raw files to a staging directory.

### 2. Download / Fetch Service
*   **Responsibility**: Safely download discovered files (PDFs, HTML documents, gazettes) and assign initial tracking IDs.
*   **Why it's separate**: Handles network retries, timeouts, and raw file validation safely away from the processing pipeline.
*   **Dependencies**: Discovery Service output directory.
*   **v1 Implementation**: Simple Python downloader with error handling, writing files to a staging staging directory (`~/.ai-orchestrator/law-staging/`).

### 3. Processing / OCR Service
*   **Responsibility**: Extract text from fetched documents. Uses `pypdf` for clean PDFs; flags scanned documents requiring OCR.
*   **Why it's separate**: Heavy text extraction and potential OCR processing should be decoupled from ingestion to prevent blocking other services.
*   **Dependencies**: `core.law_documents` (for basic text extraction), local system utilities (if lightweight OCR tools like `tesseract` are installed on the host).
*   **v1 Implementation**: Extend `core.law_documents` text extraction calls. If text density is near zero, tag the document as `NEEDS_OCR` for operator review or fallback processing.

### 4. Legal Metadata & Classification Service
*   **Responsibility**: Analyze extracted text to automatically suggest taxonomy category (01–07), source tier (Tier 1–3), copyright classification (1–5), and structural metadata (title, enactment date, case number).
*   **Why it's separate**: Isolates AI analysis and classification logic from raw storage.
*   **Dependencies**: `core.ai.ai_router.delegate()`.
*   **v1 Implementation**: A Python worker that sends text snippets to `core.ai.ai_router.delegate()` with structured prompt templates instructing the AI to return JSON matching the required taxonomy and copyright categories.

### 5. Citation & Amendment Tracker Service
*   **Responsibility**: Extract legal citations (e.g., Act numbers, constitutional articles, case citations like "[2024] GHSC 1") and identify cross-references or amendments.
*   **Why it's separate**: Citation network mapping requires specialized text pattern matching and relationship logging distinct from basic document storage.
*   **Dependencies**: Legal Metadata Service, `core.ai.ai_router.delegate()`.
*   **v1 Implementation**: Regex patterns for standard Ghanaian legal citations combined with `core.ai.ai_router.delegate()` prompts to extract references to other statutes or cases.

### 6. Operator Review Queue Service
*   **Responsibility**: Present ingested, classified documents to the operator via the Kai Dashboard for review, approval, rejection, or copyright tier adjustment.
*   **Why it's separate**: Enforces the non-negotiable Data Integrity Rule that shared canonical knowledge grows *only* through operator review.
*   **Dependencies**: Kai Dashboard (`core/kai/dashboard.html`), SQLite database audit log.
*   **v1 Implementation**: API endpoints in `core/api.py` that list staged documents awaiting approval, allowing the operator to click "Approve" (moving them to the shared knowledge base) or "Reject". Triggers `pushTelegramAlert()` upon new items entering the queue.

### 7. Indexing / Embedding Preparation Service
*   **Responsibility**: Chunk approved text into logical legal segments (sections, articles, headnotes) and prepare payloads for embedding generation.
*   **Why it's separate**: Separates document preparation from vector storage and database mapping.
*   **Dependencies**: Approved documents from the Operator Review Queue.
*   **v1 Implementation**: Python text-splitting utility that breaks documents down by section/article boundaries while preserving citation metadata.

---

## 3. Database Design

Given that SQLite is already used elsewhere in the stack (e.g., `it-manager`), it is fully adequate for the metadata, relational, versioning, and audit log requirements of the Ghana Legal Brain on a single Proxmox host. 

*(Note: Vector storage mechanisms and specific embedding storage formats will be addressed in Part 2 of this design).*

### Concrete Schema (SQLite / Relational)

#### 1. `law_documents` (Core Document Registry)
Tracks master document records, enforcing taxonomy and copyright rules.
```sql
CREATE TABLE law_documents (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    taxonomy_category INTEGER NOT NULL, -- 01 through 07
    source_tier INTEGER NOT NULL, -- 1, 2, or 3
    copyright_classification INTEGER NOT NULL, -- 1 to 5
    status TEXT NOT NULL, -- STAGED, APPROVED, REJECTED, ARCHIVED
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL, -- SHA-256 for integrity
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 2. `law_versions` (Version & Amendment History)
Tracks amendments, repeals, and historical versions of legislation/case law.
```sql
CREATE TABLE law_versions (
    version_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_summary TEXT,
    effective_date TEXT,
    file_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES law_documents(doc_id)
);
```

#### 3. `law_sources` (Source Tracking & Tiering)
Records the provenance of documents to verify Tier 1/2/3 status.
```sql
CREATE TABLE law_sources (
    source_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    source_name TEXT NOT NULL, -- e.g., "Ghana Parliament Official Gazette"
    source_url TEXT,
    retrieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES law_documents(doc_id)
);
```

#### 4. `law_citations` (Citation Network)
Maps legal cross-references (statutes citing statutes, cases citing constitutional articles).
```sql
CREATE TABLE law_citations (
    citation_id TEXT PRIMARY KEY,
    source_doc_id TEXT NOT NULL,
    target_reference TEXT NOT NULL, -- e.g., "Article 125, 1992 Constitution"
    target_doc_id TEXT, -- NULL if external or not yet ingested
    context_snippet TEXT,
    FOREIGN KEY (source_doc_id) REFERENCES law_documents(doc_id)
);
```

#### 5. `law_audit_log` (Operator Approval Audit Trail)
Records every action taken within the approval workflow to maintain strict separation between user uploads and the canonical base.
```sql
CREATE TABLE law_audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    operator_action TEXT NOT NULL, -- APPROVED, REJECTED, TIER_MODIFIED
    notes TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES law_documents(doc_id)
);
```

---

## 4. Storage Requirements & Capacity Analysis

### Concrete Capacity Breakdown (Estimated for 30GB+ Knowledge Base)
An authoritative legal knowledge base for Ghana encompassing the 1992 Constitution, all active Acts, Legislative Instruments, procedure rules, specialized laws, international treaties, and decades of superior court case law scales rapidly:
1.  **Original Document Binaries (PDFs / Scans)**: ~12 GB
2.  **Extracted Text & Structured JSON**: ~4 GB
3.  **Embeddings & Vector Index Structures**: ~8 GB (depending on chunking granularity and dimension size)
4.  **Versions, Amendments, and Historical Revisions**: ~4 GB
5.  **Audit Logs, Database Indexes, and Staging Temp Files**: ~2 GB
*   **Total Projected Storage**: **~30 GB**

### Current Host Constraints vs. Reality
*   **Proxmox A (Current Host)**: 50GB total disk, 33GB used, **15GB free**. It is also shared with unrelated production services (real payroll and employee data in `it-manager`, monitoring via `proxdash`, and other containers).
*   **Assessment**: The current host's 15GB free space is **insufficient** to house a 30GB+ legal knowledge base without risking host instability or impacting critical payroll services.

### Required Pre-Ingestion Action
Before bulk ingestion of the Ghana Legal Brain can begin, a storage location decision must be executed. Because Proxmox B is currently unconfirmed and unreachable (no network path, no IP, no active VPN/WireGuard tunnel), **bulk ingestion cannot rely on Proxmox B at this stage.**

### Operator-Facing Questions to Resolve Before Provisioning Storage:
1.  *Can additional storage be provisioned on Proxmox A* (e.g., attaching an expanded virtual disk or secondary storage pool, provided the underlying hardware pool has physical headroom)?
2.  *If Proxmox A cannot be expanded, is there a secondary local storage medium* (e.g., an attached external drive or network-attached storage on the local office network) that can be mounted securely to host the `AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR`?
3.  *Should staging and bulk vector storage be partitioned separately* from production databases to allow controlled purging of staging cache if disk pressure rises?

---

## 5. Source List

A concrete, realistic starting list of Ghana legal sources to target for each taxonomy category, based on the operator’s Tier 1–3 priority system (official government sources first). **All URLs listed here are candidates requiring human verification**—none have been confirmed as live, authoritative endpoints. The operator must check each one before automating any fetching.

### 01 Constitution
- **Primary document**: The 1992 Constitution of Ghana (including all amendments).  
  - *Tier 1 source*: Parliament of Ghana (likely `parliament.gh`) – official publication of the Constitution as amended.  
  - *Tier 1 source*: Ghana Gazette (published by Ghana Publishing Company) – where constitutional amendments are officially gazetted.  
  - *Tier 2 source*: Judicial Service of Ghana – may host a consolidated electronic copy on its judgment portal.  
- Notes: If an amendment has been enacted, it must be obtained from the Gazette; the consolidated version maintained by a parliamentary or judicial website may lag behind.

### 02 Legislation
Acts of Parliament, Legislative Instruments (LIs), Regulations, Executive Instruments.
- **Tier 1 sources**:
  - Ghana Gazette – the official vehicle for publication of all Acts and statutory instruments. The Ghana Publishing Company’s website (possible domain `ghanapublishingcompany.com`, `gpc.gov.gh`, or a subdirectory of `epublicservices.gov.gh`) typically offers recent gazettes as PDFs.  
  - Parliament of Ghana – may provide a “Bills, Acts & Legislation” section with downloadable PDFs.  
  - DataCenta? – the operator has previously mentioned `datacentagh.com` as a possible aggregator; treat as Tier 2 unless confirmed as an official mirror.
- **Tier 2 sources**:
  - Ghana Law Finder (www.ghanalawfinder.com) – a legal publisher; check licensing terms before full-text use.  
  - University of Ghana Faculty of Law digital library (if publicly accessible).
- Important: Verify whether the Ghana Gazette archive is complete and machine‑readable (text‑based PDF). Many official gazettes are scanned images, which would require OCR before text extraction—a capability not currently available. Only text‑based PDFs should be fetched initially; scanned ones must be flagged for human review and excluded until OCR is implemented.

### 03 Case Law
Supreme Court, Court of Appeal, High Court judgments, plus rulings from specialised tribunals.
- **Tier 1 sources**:
  - Judicial Service of Ghana official judgment repository. The exact domain is uncertain; possible candidates: `judg.gh`, `judicial.gov.gh/judgments`, or `judcom.gov.gh`. The operator must test reachability and confirm authenticity.  
  - Supreme Court of Ghana Annual Reports – may be available in PDF from the Judicial Service website.  
  - Ghana Law Reports (published by the Council for Law Reporting / Ghana Publishing Company) – the authoritative series, though availability of digital copies is unknown.
- **Tier 2 sources**:
  - Denning Law School (public case database) – verify that it mirrors official reports; if not, classify as Tier 3.  
  - `ghanalaw.org` – a community‑run case repository; useful for discovery, but must be cross‑checked against official sources.
- **Uncertainty caveat**: None of the above URLs have been probed from the current host; some may be behind government CAPTCHAs or IP‑based restrictions, requiring manual download.

### 04 Procedure
Civil Procedure Rules, Criminal Procedure Code, Evidence Act, Practice Directions.
- **Tier 1**:
  - Parliament of Ghana – the principal source for the Evidence Act (NRCD 323) and Criminal Procedure Code (Act 30).  
  - Ghana Law Reform Commission (if they publish the latest consolidated rules).  
  - Judicial Service – may host Practice Directions and court forms.
- **Tier 2**:
  - University of Ghana Law Faculty – often keeps public reference copies.  
  - Ghana Bar Association – may have member‑only resources; public sections might contain procedure guides.

### 05 Specialized Law
Company, Labour, Land, Tax, Banking, Data Protection, Intellectual Property, etc.
- **Tier 1 by agency**:
  - Companies Act: Registrar General’s Department (`rgd.gov.gh`).  
  - Labour: Ministry of Employment and Labour Relations (`melr.gov.gh`), National Labour Commission.  
  - Land: Lands Commission (`landscommission.gov.gh`).  
  - Tax: Ghana Revenue Authority (`gra.gov.gh`) – tax legislation and practice notes.  
  - Banking & Financial: Bank of Ghana (`bog.gov.gh`) – Banking Act, Payment Systems Act, anti‑money‑laundering regulations.  
  - Data Protection: Data Protection Commission (`dataprotection.org.gh`).  
  - Intellectual Property: Registrar General’s Department (Copyright, Patents).  
- **Caution**: Agency websites may not always host full legislative texts; some only display summaries. The operator must verify that the site provides the actual Act or Regulation, not just a description.

### 06 International Law
Treaties, conventions, and human‑rights instruments Ghana has ratified.
- **Tier 1**:
  - Ministry of Foreign Affairs and Regional Integration – likely maintains a treaty database or a page listing ratified instruments.  
  - Parliament of Ghana – treaty ratification records (motions, resolutions) may be published on its website.  
  - African Union/ECOWAS official treaty pages – relevant protocols (e.g., African Charter on Human and Peoples’ Rights); verify if these are mirrored on a Ghanaian government site for authority.
- **Tier 2**:
  - United Nations Treaty Collection (`treaties.un.org`) – official but not Ghana‑specific; reference only, not a primary source for local supremacy. Tools like the OASIS treaty status database may help identify ratified instruments.
- **Uncertainty**: No specific Ghanaian treaty portal is known; the operator should contact the Ministry or search for parliamentary archives of ratification instruments.

### 07 Legal Reasoning
Maxims of law, principles of statutory interpretation, judicial reasoning patterns.
- **Tier 2/3**:
  - Judicial Service of Ghana – judicial training manuals, bench books, or published seminar papers (if publicly released).  
  - University of Ghana Faculty of Law – public lectures, working papers, and theses (after confirming open‑access licence).  
  - Ghana Institute of Advanced Legal Studies – possible library material.  
- **Caution**: This category will be built incrementally, initially from case‑law reasoning examples and verified academic publications, not by bulk download. Most sources will be Tier 3 (secondary), so the system must enforce metadata‑only storage where copyright is unknown.

---

## 6. Update Schedule

The cadence-driven fetching of new legal documents is implemented using systemd timers (*.timer + *.service*), reusing the established pattern already in place for other scheduled jobs (e.g., the watchdog timer). The pipeline logic distinguishes between “no new content” and “failure to reach source” via exit codes and log monitoring, so that the operator is only alerted when intervention is truly needed.

All scheduled scripts are placed under `/opt/ai-orchestrator/scripts/ghana_legal_brain/` and operate on the ingestion queue database (SQLite, as defined in Part 1). The curated knowledge base directory (`GHANA_LEGAL_BRAIN_DOCS_DIR`) and staging area are configurable via environment variables.

### 6.1 Timer definitions and fetch behaviour

| Timer name | Cadence | Target sources | What it does |
|------------|---------|----------------|--------------|
| `legal-fetch-gazette.timer` | Daily (Mon–Fri, 07:00 UTC) | Ghana Gazette, Parliament legislation page | Fetches the latest gazette and Acts list from the official Parliament/gazette website, compares publication dates against the ingestion queue, and inserts any new entry as a `pending` queue item with the source URL and metadata. |
| `legal-fetch-caselaw.timer` | Weekly (Sunday 02:00 UTC) | Judicial Service judgment portals (Supreme Court, Court of Appeal, High Court) | Checks for weekly judgment lists or RSS feeds (if available) and downloads new judgment PDFs that are based on text-based OCR (deterministic text extraction via pypdf). Scanned images are logged and skipped. |
| `legal-fetch-agencies.timer` | Monthly (1st of month, 03:00 UTC) | Specialised agency websites (BOG, GRA, Data Protection, Lands Commission, etc.) | Visits a curated list of agency pages, scrapes for new regulatory instruments or guidelines, and enqueues any document not already seen. |
| `legal-fetch-treaties.timer` | Quarterly (Jan 1, Apr 1, Jul 1, Oct 1) | Ministry of Foreign Affairs, Parliament treaty records | Manual-assisted fetch – the timer triggers a script that emails/Telegram‑alerts the operator with a reminder to check official treaty ratification pages; the operator can manually trigger the fetch once new URLs are confirmed. This is a semi‑automated step because no stable treaty database is known. |
| `legal-fetch-reasoning.timer` | Quarterly (offset from treaties) | University repositories, Judicial Service training materials | Similar to treaties: the script sends a reminder to the operator to identify new publicly available reasoning resources. Eventually, when a stable feed exists, it can become fully automatic. |

All timers use a shared locking mechanism (`flock`) to prevent overlapping runs; systemd’s `OnUnitActiveSec` ensures they do not stack.

### 6.2 Differentiating failures from “nothing new”

Each fetch script follows a strict exit‑code convention:

- **Exit 0**: Success. Either
  - New items were found and inserted into the queue (with status `new`), or
  - No new items were detected since the last check, indicating that the source was reachable and the content is up‑to‑date.
- **Exit 1**: Transient failure (e.g., source server timed out, DNS error, HTTP 5xx). The script logs a detailed error. The systemd unit is configured with `Restart=on-failure` and `RestartSec=5min`, allowing up to three automatic retries before escalating.
- **Exit 2**: Permanent/semi‑permanent failure (e.g., source URL is gone, authentication changed, content format unrecognised). The script calls the existing `pushTelegramAlert()` function to notify the operator immediately.

A companion `legal-monitor.timer` (hourly) tail‑parses the logs: if any fetch unit has been in a failed state for more than 24 hours without human acknowledgement, it sends a consolidated alert. This prevents alert fatigue from transient network blips while still surfacing prolonged outages.

### 6.3 Processing pipeline trigger

Fetched items sit in the queue with status `pending`. A separate systemd timer, `legal-ingest-processor.timer` (runs every 15 minutes), invokes the ingestion pipeline script. That script picks **one** pending queue item, advances it through the pipeline stages (see Section 7), and then exits. This pacing avoids overwhelming the AI providers and respects the limited disk I/O on Proxmox A. The timer restarts after a short interval; if no items exist or the last run is still active, it simply exits silently.

---

## 7. Agent Responsibilities

The ingestion pipeline turns a raw, fetched document into a curated entry in the shared canonical knowledge base. Each stage is implemented as either deterministic Python code or a call to `core.ai.ai_router.delegate()`, with the boundary strictly observed so that no AI is used where a simple rule would be cheaper and more reliable. The pipeline state is tracked in the SQLite database defined in Part 1; only documents that have passed all stages and received explicit operator approval become part of the public knowledge base.

### 7.1 Stage 1 – Source Verification *(deterministic)*

**Purpose**: Confirm that the source URL matches a pre‑approved, trusted official source and assign a Tier (1/2/3) and initial copyright classification.

- **Implementation**: A Python lookup table mapping URL patterns (domains, path prefixes) to source metadata. The table is maintained as a YAML file inside the `core/law_documents` module (separate from the upload module’s root, to preserve the boundary).  
- **Behaviour**:
  - If the URL matches a known Tier 1 or Tier 2 entry, the document proceeds automatically with that tier and a default licensing category (e.g., `public_domain` for Acts, `official_public_access` for gazette text‑based PDFs).  
  - If no match is found, or the URL is flagged as “unverified”, the pipeline **halts** and flags the item as `needs_source_verification`. The operator is notified via the Kai Dashboard Library tab; they must manually confirm the source authenticity, assign a tier, and resubmit.  
  - Copyright classification for known sources is also deterministic (categories 1‑3) based on the source table. Category 4 and 5 sources can be flagged at this stage if the operator has pre‑registered them as such.
- **Why deterministic**: No intelligence is needed to match a URL against a whitelist. Using AI here would add latency, cost, and potential miscategorisation.

### 7.2 Stage 2 – Legal Classification *(deterministic + conditional AI)*

**Purpose**: Classify the document into the 7‑category legal taxonomy and determine its exact licensing category (1–5) for storage.

- **2a – Deterministic mapping**: For documents whose source table entry includes a reliable taxonomy mapping (e.g., a Parliament Act → `02 Legislation`, tier 1, category 1), the pipeline applies the mapping directly and skips AI. Over 90 % of Tier 1 documents are expected to fall into this fast path.  
- **2b – Conditional AI classification**: For documents from sources without a pre‑mapped taxonomy, or where the licensing status is ambiguous, the pipeline delegates to the AI Router:
  - **`task_type='law_classification'`**  
  - **`capability='classification'`**  
  - The prompt includes a structured excerpt of the document (first 5000 characters) and asks the model to return a JSON object with `taxonomy_code`, `suggested_tier`, `licensing_category`, and a confidence score. The operator’s jurisdiction rules (Constitution supremacy, etc.) are included as a system note.
  - This task type uses a **FIXED_ORDER** routing configuration that prefers providers known for logical, law‑specific reasoning (Claude via OpenRouter, then Gemini). The fixed order avoids rotating to less‑reliable providers for a legal classification task where consistency matters.
- The combined deterministic + AI result is stored in the queue item as `classification_ready`, awaiting human review in the final stage.

### 7.3 Stage 3 – Citation Extraction *(AI delegate)*

**Purpose**: Identify all formal law citations within the document (e.g., *“Act 1015”*, *“J1/5/2021”*, *“1992 Constitution Article 1”*) and return them in a structured format.

- **Why AI**: Natural language parsing of legal text is needed because Ghanaian citations vary in format; pure regular expressions would be brittle and quickly become outdated.  
- **Implementation**:  
  - Deterministic pre‑check: extract the document’s text using `pypdf` (text only). If extraction returns fewer than 50 words, the document is labelled `insufficient_text` and the pipeline halts with a notice to the operator (likely a scanned PDF needing OCR).  
  - If text is present, call:  
    `core.ai.ai_router.delegate(prompt, task_type='law_citation_extraction', capability='extraction')`  
    The `prompt` consists of the extracted text (truncated to the model’s context window) and instructions to return a JSON array of citation strings, optionally grouped by type (case, act, constitution, article).  
  - `task_type='law_citation_extraction'` is registered with the same FIXED_ORDER as other `law_*` tasks, prioritising models that excel at entity extraction. The operator can tune the provider list later.
- The raw extracted citations are saved in the tracking record. A subsequent deterministic validation step checks that each citation matches at least one category in the taxonomy (e.g., an “Act” citation must point to a real Act number, not a random string). Invalid citations are flagged but do not block the pipeline; they are presented to the operator during approval.

### 7.4 Stage 4 – Extraction Quality Assurance *(optional AI delegate, operator‑toggleable)*

**Purpose**: Catch obvious gaps or errors before the document reaches human review, acting as a second pair of eyes on the machine‑extracted metadata.

- **Deterministic part**: The script verifies that the number of extracted citations is consistent with the document length (e.g., a 100‑page Supreme Court judgment with zero citations is suspicious and is marked `low_quality`).  
- **AI QA step** (enabled by an environment‑variable toggle `LEGAL_BRAIN_QA_ENABLED`):  
  - `core.ai.ai_router.delegate(prompt, task_type='law_qa', capability='analysis')`  
  - The prompt asks the AI to review a short excerpt and the list of extracted citations, then grade completeness on a scale of 1–5. If the score is 1 or 2, the document is flagged for manual review; otherwise, it advances.  
  - This stage uses a **rotating** provider strategy (no FIXED_ORDER) to avoid over‑relying on a single model’s quirks, since QA is less sensitive than classification.
- By default, the QA toggle is off to save costs; the operator can enable it for high‑value Tier 1 batches.

### 7.5 Stage 5 – Operator/Curator Approval *(human‑in‑the‑loop)*

**Purpose**: The final, mandatory gate that enforces the data integrity rule: **only reviewed, approved documents enter the shared canonical knowledge base.**

- **Integration with the Kai Dashboard**:  
  The dashboard’s “Library” tab is extended (as already planned) to show a queue of `pending_approval` items. Each item displays:
  - Source URL and tier
  - Taxonomy classification and confidence
  - Copyright/licensing category (with a clear warning if 4 or 5)
  - Extracted citations preview
  - A “View full text” button (text‑only PDF transcript) so the operator can spot‑check.
- The operator can take one of three actions:
  1. **Approve** → the document is stored in the curated directory structure under the corresponding taxonomy folder. Full text is stored only for categories 1–3; for 4–5, only metadata + source reference are saved. The document’s metadata record is updated to `approved` and a notification is sent (Telegram) for the operator’s records.  
  2. **Reject** → the item is moved to `rejected`, optionally with a reason. The pipeline discards any extracted data.  
  3. **Re‑classify** → the operator overrides the taxonomy or licensing category, then re‑submits for automatic re‑processing of subsequent stages (if needed).
- This approval workflow is entirely deterministic and human‑driven, with no AI involvement. It reuses the existing API endpoints (`POST /kai/law-documents` and the Library‑tab UI) already described in the architecture report.

### 7.6 State machine and sequence

The ingestion tracking DB maintains a `status` column for each queue item:

```
pending → source_verified → classification_ready → citations_extracted → ready_for_qa → pending_approval → approved | rejected
                 ↘           ↙  (if AI needed)          ↘
              needs_source_verification               insufficient_text (halt)
```

The pipeline processing script (triggered by `legal-ingest-processor.timer`) advances the item one stage at a time per invocation, ensuring a steady, cost‑controlled flow. All AI calls are logged by `ai_router` via its existing usage‑history mechanism, giving the operator full visibility into token consumption.

---

*All stages respect the boundary that `core.law_documents` remains an isolated upload module; the ingestion pipeline never calls its functions. The curated knowledge base is a separate tree under `GHANA_LEGAL_BRAIN_DOCS_DIR`, with its own metadata and file‑tier storage rules, keeping the user‑upload surface completely separate from the operator‑curated shared corpus.*

---

## 8. SECURITY CONSIDERATIONS

Operating an automated system that fetches legal documents from external websites on a schedule—and ingests them into a shared canonical knowledge base consumed by paid downstream products like *Juris Kai*—introduces distinct attack surfaces. The design relies on strict boundaries, established patterns in the existing codebase, and explicit human-in-the-loop gates.

### Fetch-Time Risks & Mitigations
*   **Compromised or Malicious Source Sites:** Official Ghanaian government websites (Tier 1) or academic portals (Tier 2/3) could theoretically be compromised, serving malicious payloads, oversized files (DoS vector), or poisoned text containing prompt-injection vectors designed to hijack downstream AI reasoning.
    *   *Mitigation:* The fetcher treats all ingested text as untrusted string data. The pipeline enforces hard file-size limits before parsing (leveraging `pypdf` safely within existing resource constraints). Under no circumstances is raw downloaded text executed, rendered as HTML in administrative interfaces without sanitization, or passed directly to an AI system without explicit delimitation.
*   **SSRF and Source URL Integrity:** If source URLs were dynamically influenced by untrusted user inputs, server-side request forgery (SSRF) could occur.
    *   *Mitigation:* Source URLs are hardcoded into the scheduled ingestion configuration or strictly managed via the authorized operator dashboard (`core/kai/dashboard.html`). Ingestion jobs pull *only* from statically defined, operator-vetted registry lists, never from user-supplied parameters.
*   **Oversized and Malformed Files:** Corrupted PDFs or multi-gigabyte files could exhaust memory or disk space on the host.
    *   *Mitigation:* Strict streaming size caps and validation checks must precede parsing in `core.law_documents`.

### Storage & Path-Traversal Security
*   **Reuse of Existing Patterns:** The codebase already enforces path-traversal safety in `core.law_documents` (where `doc_id` inputs are validated against strict regex patterns and containment-checked against the storage root). This exact pattern must be reused without reinvention for any expanded storage structures.
*   **Storage Isolation:** Because Proxmox A lacks the disk space for the full legal knowledge base, the storage root configured via `AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR` must point to an external storage mount or a properly provisioned alternative path once storage capacity is resolved.

### The Operator-Approval Gate as the Trust Boundary
*   The operator-approval workflow integrated into the Kai Dashboard (`core/kai/dashboard.html`) serves as the ultimate trust boundary. 
*   **No automated script, external scraper, or AI routing mechanism is permitted to commit documents directly to the shared canonical knowledge base.** 
*   Everything fetched or uploaded enters a staging/pending review state. Only an explicit operator sign-off promotes a document to "authoritative status."

### Audit Logging
*   Every addition, modification, status change, and copyright classification decision must generate an immutable audit log entry.
*   The log record must capture:
    *   Timestamp of the action.
    *   Source URL or original upload identifier.
    *   Assigned taxonomy category (01–07).
    *   Copyright classification (1–5).
    *   The exact operator identity who approved the ingestion.

### Module Isolation Boundaries
*   **Preserving `core.law_tutor` Isolation:** The existing architectural constraint—that `core.law_tutor` has zero operational imports to build managers, approval engines, or deployment modules—must be strictly preserved. The tutor reads from the shared knowledge base via read-only interfaces but cannot alter state or trigger builds.
*   **Isolating Juris Kai:** Similarly, future multi-tenant code for *Juris Kai* must interact with the shared knowledge base strictly through defined read-only APIs or parameterized query interfaces. Paid tenant document uploads must remain strictly siloed within individual user profiles and can *never* merge into the shared canonical knowledge base, protecting system integrity against user-uploaded data poisoning.

---

## 9. COPYRIGHT COMPLIANCE APPROACH

To manage intellectual property risks responsibly without administrative paralysis, the system operationalizes the operator's five-tier copyright classification framework directly into the ingestion and storage pipeline.

### Operational Policy by Classification
1.  **Public Domain / Government Publication (Category 1):** Full text is extracted, stored in the shared canonical knowledge base, and fully indexed for retrieval and AI context injection.
2.  **Open License (Category 2):** Full text is extracted and stored, provided the license terms (e.g., Creative Commons attribution) are verified and logged.
3.  **Official Public-Access Document (Category 3):** Full text stored and utilized for research and legal reasoning support.
4.  **Copyright Protected (Category 4) & Unknown Licensing Status (Category 5):** 
    *   **Strict Prohibition:** Full-text storage and full-text redistribution are strictly blocked.
    *   **Permitted Data:** Only metadata (Title, Citation, Source URI, Author, Publication Date) and a concise, non-infringing abstract or reference pointer may be stored. 
    *   **Routing:** Any document with an unknown licensing status (Category 5) must immediately route to human review rather than defaulting to storage.

### The Classification Decision Process
*   **Initial Tagging:** During automated ingestion or manual upload via the Kai Dashboard, the source registry or operator must assign a copyright category (1–5).
*   **Ambiguity Protocol:** **On genuine ambiguity, the system defaults to human review (Category 5 routing).** The AI must never guess or automatically assign a permissive category when legal status is unclear. The operator must make the final determination before any text beyond metadata is retained.

### Plain-Language Legal Note & Risk Disclaimer
> *Disclaimer: The following is an operational policy summary, not formal legal advice.*

In many jurisdictions, including Ghana, official primary legal materials—such as the 1992 Constitution, enacted Acts of Parliament, Legislative Instruments, and reported judgments of superior courts—are treated as public records or official government publications intended for public access and are generally not subject to traditional private copyright restriction in the same manner as commercial literary works. 

However, secondary legal materials, academic commentaries, headnotes created by commercial publishers (such as the Ghana Law Reports digests), and textbooks remain fully protected by copyright. Republishing or storing full texts of protected secondary sources without a license exposes the operator to infringement risk. 

Where jurisdiction, licensing terms, or statutory interpretation is uncertain regarding specific documents (particularly historical or privately compiled legal compendia), **the matter must be referred to qualified legal counsel** rather than assuming public domain status.

---

## 10. IMPLEMENTATION ROADMAP

The following ordered, priority-tiered checklist governs the phased implementation of the Ghana Legal Brain knowledge acquisition system.

### Phase 1: Blocking Prerequisite [CRITICAL]
1.  **Confirm and Provision Storage Location:** 
    *   Resolve the Proxmox A disk constraint (33GB used / 15GB free) by provisioning adequate external or expanded storage dedicated to the legal knowledge base (`AI_ORCHESTRATOR_LAW_DOCUMENTS_DIR`).
    *   Verify read/write permissions and path-traversal safety checks on the new storage root.

### Phase 2: Schema, Metadata, & Approval Workflow [HIGH]
2.  **Database & Taxonomy Schema:** 
    *   Implement the metadata schema supporting the 7-part legal taxonomy (01 Constitution through 07 Legal Reasoning) and the 5 copyright classification categories.
3.  **Dashboard Approval Gate Integration:** 
    *   Extend `core/kai/dashboard.html` and `core/api.py` endpoints to include the operator-approval staging queue. 
    *   Ensure any incoming document sits in `PENDING_REVIEW` until explicitly approved and classified by the operator.
4.  **Audit Logging Hook:** 
    *   Implement immutable logging for all approval and rejection actions, recording operator ID and timestamps.

### Phase 3: Manual Ingestion Pipeline & Testing [MEDIUM]
5.  **Manual Pipeline Test (Small Dataset):** 
    *   Ingest a small, verified test set of Tier 1 documents (e.g., selected chapters of the 1992 Constitution) manually through the dashboard.
    *   Verify text extraction via `pypdf`, metadata tagging, and review queue behavior.
6.  **Read-Only Integration Check:** 
    *   Verify that `core.law_tutor` and the future *Juris Kai* consumer interfaces can query the approved store without violating module isolation boundaries.

### Phase 4: Automated Fetching & Scheduling [LOW]
7.  **Scheduler Integration:** 
    *   Implement the scheduled fetching watchdog using the established systemd timer pattern.
    *   Configure scheduled checks against static, approved Tier 1/Tier 2 source URLs, routing all newly discovered files directly to the dashboard staging queue (never directly to the canonical base).
8.  **Alerting Hook:** 
    *   Wire up `pushTelegramAlert()` to notify the operator via Telegram whenever new documents require review in the dashboard queue.

*Note: Multi-jurisdiction expansion beyond Ghana is explicitly out of scope for v1.*

---

## RISK SUMMARY

1.  **Host Resource Exhaustion (Storage & Compute):** 
    *   *Risk:* Proxmox A's remaining disk space (15GB) is insufficient for a comprehensive legal corpus (~30GB+), risking host instability if unmanaged. Unchecked PDF parsing could also spike CPU/RAM.
    *   *Mitigation:* Making physical storage provisioning a hard blocking prerequisite (Phase 1) before any bulk ingestion occurs. Enforcing strict file-size limits and streaming parsers.
2.  **Source Site Poisoning & Malicious Payloads:** 
    *   *Risk:* External websites (even official ones) could be compromised or spoofed, injecting malformed files, oversized payloads, or prompt-injection text into the pipeline.
    *   *Mitigation:* Treating all fetched text as untrusted string data, enforcing size caps, isolating parsing logic, and requiring mandatory human operator review before any ingested file enters the canonical knowledge base.
3.  **Copyright Infringement & Unauthorized Redistribution:** 
    *   *Risk:* Ingesting copyrighted secondary legal commentary, proprietary law reports digests, or restricted academic works (Categories 4/5) could expose the solo operator to legal liability.
    *   *Mitigation:* Enforcing strict copyright classification (1–5) at ingestion, restricting Category 4/5 storage strictly to metadata and references, defaulting ambiguity to human review, and requiring review by qualified counsel for uncertain sources.
4.  **Data Pollution from Paid Tenant Uploads:** 
    *   *Risk:* Future paying users of *Juris Kai* attempting to push their private documents or pleadings into the shared canonical knowledge base.
    *   *Mitigation:* Architectural enforcement of strict data boundaries. User uploads remain permanently confined to isolated individual user profiles and can never merge into the shared canonical legal brain.
