# ⚖️ Legal Module Comprehensive Audit
**Date**: 2026-08-07
**Scope**: All 4 subsystems — Telegram bot menus, Cerebrum Command Center, legal acquisition (KLAUS), legal answering pipeline

---

## Executive Summary

The Kai legal ecosystem spans **4 modules** (juris-kai, susu, legal-brain, klaus), **2 Telegram bots**, **23 API endpoints**, **4 module descriptors**, a **16-tier document index**, and **1 continuous acquisition daemon**. The system is structurally sound but heavily dependent on a **near-empty knowledge base** (only Parliament source operational). The answering pipeline has excellent Ghana-only jurisdiction enforcement, but the knowledge base has only 88 documents — all from parliament showPDF patterns.

### Key Findings
- **3 of 5 legal sources are inaccessible**: GhaLII (Cloudflare WAF), eJudgment (login-walled), Ghana Publishing (paywalled—removed per audit)
- **Only 1 active scraper**: parliament.gh via legacy showPDF handler (88 docs, all pre-existing)
- **GhaLII needs AfricanLII API key or IP whitelisting** — robots.txt allows search but WAF blocks it
- **Juris Kai bot is fully operational** with 4 subscription tiers, referral system, and document analysis
- **SUSU bot is separate from legal brain** — savings group bot, not legal knowledge
- **Command Center has full legal admin panel** — search users, ban/unban, grant days, change tiers, generate referrals
- **Answering pipeline has strong jurisdiction gates** — but depends on thin knowledge base

---

## 1. Telegram Bot Menus (Juris Kai)

### 1.1 Bot Identity
- **Bot**: `@Juriskai_bot` (JURIS_KAI_BOT_TOKEN)
- **Purpose**: Multi-tenant Ghana legal assistant with subscription tiers
- **DB**: SQLite (`juris_kai.db`) — `core/juris_kai/accounts.py`
- **Admin gate**: `JURIS_KAI_ADMIN_IDS` environment variable (comma-separated Telegram IDs)
- **Polling**: Long-poll with 25s timeout, 5s error backoff, 10s rate limit window (5 msg/window)

### 1.2 Menu Structure (7 main menus + 5 admin sub-menus)

#### Main Menu (`main_menu()` — `core/juris_kai/menus.py:31`)
```
📚 Learn Law    ⚖️ Cases
📝 Practice     🧠 Study Tools
📄 Documents    🎓 Progress
⚙️ Settings     ❓ Help
```

#### Learn Law (`learn_menu()` — line 45) → 7 topic buttons + Search
```
🇬🇭 Ghana Constitution  ⚖️ Criminal Law
🏛️ Civil Law           📋 Contract Law
🏠 Property Law         👨‍👩‍👧 Family Law
💼 Business Law         🔍 Search Topic
🔙 Back to Menu
```
Each topic queries Legal Brain DB → delegates to AI with juris_legal_teaching task type.

#### Cases (`case_law_menu()` — line 60)
```
📋 Case Summaries    ⚡ Legal Principles
📜 Precedents        🔎 Case Analysis
📂 Source References  🔍 Search Case
🔙 Back to Menu
```
Each maps to juris_case_analysis task type.

#### Practice (`practice_menu()` — line 74)
```
📝 Generate Questions   ⚖️ IRAC Practice
✍️ Essay Practice       📋 Mock Exams
✅ Answer Evaluation    🔙 Back to Menu
```

#### Study Tools (`study_tools_menu()` — line 87)
```
🃏 Flashcards      🧠 Memory Drills
⏱️ Quick Quiz      📝 Revision Notes
🔙 Back to Menu
```

#### Documents (`documents_menu()` — line 100)
```
📤 Upload Document    📋 Summarize
⚖️ Legal Concepts     📌 Key Points
📂 Recent Documents   🔙 Back to Menu
```
Document upload is **session-only** — uploaded documents are NOT auto-ingested into the permanent knowledge base. Privacy-gated.

#### Progress & Settings (`progress_menu()` line 113, `settings_menu()` line 126)
- Progress: Learning History, Completed Topics, Weak Areas, Study Path, Stats
- Settings: Language, Learning Level, Notifications, Account Info, Subscription
- ⚠️ **Language, Learning Level, Notifications**: Stub "will be available in the next update"

#### Admin Menu (`admin_main_menu()` — line 139) — gated by JURIS_KAI_ADMIN_IDS
```
🔧 Bot Health    👥 User Activity
📚 Knowledge     🤖 AI Mgmt
🔐 Security      📊 Stats Dashboard
🔙 User Menu
```
With 4 sub-menus:
- **Bot Health**: System Status, Error Logs, API Status, Provider Status, Cost Monitor
- **Knowledge Mgmt**: Approved Sources, Add Document, Verify Source, Update Database, Source Versions
- **AI Mgmt**: Model Routing, Provider Status, Token Usage, Performance, Failover Control
- **Security**: Permissions, Sessions, Suspicious Activity, Access Logs

**⚠️ FINDING**: Most admin sub-menu items are stubs — menu items map to `None` in `sub_backs` dictionary (menus.py:260-318) and fall through to bot.py's `_handle_menu_action()` handlers, many of which return generic responses. The actual admin functionality is thin — primarily `/api/juris-kai/stats` and account listing.

### 1.3 Message Routing Flow (`bot.py:handle_message()`)
```
Incoming msg → rate limit check → get/create account → new user? (disclaimer)
→ deactivated? → route:
  /start         → welcome + main menu
  /menu          → show menu
  /admin         → admin gate check → admin menu
  menu text      → menu_for_text() → sub-menu keyboard
  menu action    → _handle_menu_action() → execute
  /command       → commands.py handler
  free text      → _handle_free_text() → Legal Brain query → AI delegate
```

### 1.4 Subscription Tiers (`accounts.py:29`)
| Tier | Period | Price (GHS) | Docs/mo | Queries/day | Features |
|------|--------|-------------|---------|-------------|----------|
| Free Trial | 7 days | 0 | 3 | 20 | Basic QA, case lookup |
| Monthly Basic | 30 days | 50 | 15 | 100 | + Doc analysis, research |
| Monthly Pro | 30 days | 150 | 50 | 500 | + Arguments, flashcards, priority |
| Annual Pro | 365 days | 1500 | 50 | 500 | + API access |

Payment: Hubtel Mobile Money integration with per-document billing (GHS 2/page).

### 1.5 Referral System (`accounts.py:556`)
- `generate_invite_code()` → unique referral code
- `accept_invite()` → grants 1 day to inviter, 3-day trial to invitee
- `get_referral_history()` / `get_all_referrals()` for tracking
- Exposed via API and Command Center

### 1.6 Callback Queries
Two inline keyboards:
- `disclaimer_accept_keyboard()` — "✅ I Understand" → `disclaimer_accept`
- `confirm_cancel_keyboard(action)` — "✅ Confirm" / "❌ Cancel"
- `quiz_answer_keyboard(options)` — A/B/C/D 4-option quiz grid

### 1.7 Jurisdiction Enforcement (`prompt.py`)
Every prompt includes `_JURISDICTION_GATE`:
> "You ONLY answer questions about Ghana law (Republic of Ghana). If the user's question is about any other country's laws... respond ONLY with: 'I only handle Ghana legal matters.'"

Plus `_DATABASE_FIRST`:
> "Before answering, check the bot's local knowledge base and database for relevant Ghana legal documents..."

---

## 2. Cerebrum Command Center — Legal Interface

### 2.1 Navigation
Command Center (`core/kai/command_center.html`, 1500 lines) has a dedicated **Legal** navigation item (#legal) in the sidebar.

### 2.2 Modules Panel (`loadModules()`, line 920)
Groups legal modules visually under "Legal Modules" header with separator:
- juris-kai, susu, legal-brain, klaus

Each module card has expandable tabs:
- **Overview**: Live stats from API
- **Settings**: Editable module settings from descriptors
- **Operations**: Action buttons
- **Logs**: Audit trail

### 2.3 Legal Panel (`loadLegal()`, line 1155)
Full-featured admin dashboard with:

**Juris Kai section:**
- Live stats: active/total accounts
- Search input (name, phone, Telegram ID) + tier filter dropdown
- "Find Users" button → inline result cards showing:
  - Full name, Telegram ID, active/banned badge, tier badge
  - Email, phone, subscription end date
  - Dropdowns: Grant Days (1/3/7/14/30), Change Tier (free/monthly/annual)
  - Ban/Unban buttons
- "Generate Referral" button → prompts for account ID, copies code to clipboard

**SUSU section:**
- Stats: user count, active/total groups, total deposits, fees, pending tx

**Legal Brain section:**
- Document count, source count, sessions, jurisdictions

### 2.4 API Endpoints (all in `core/api.py`)

**Juris Kai** (11 endpoints):
| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/juris-kai/stats | Aggregate stats |
| GET | /api/juris-kai/accounts | List/search accounts |
| GET | /api/juris-kai/accounts/{id} | Account detail |
| POST | /api/juris-kai/accounts/{id}/subscription | Change tier |
| POST | /api/juris-kai/accounts/{id}/deactivate | Ban user |
| POST | /api/juris-kai/accounts/{id}/activate | Unban user |
| POST | /api/juris-kai/accounts/{id}/grant-days | Grant free days |
| GET | /api/juris-kai/referrals | List all referrals |
| POST | /api/juris-kai/referrals/generate | Generate invite code |
| GET | /api/juris-kai/payments | Payment history |
| GET | /api/juris-kai/security-log | Security events |

**SUSU** (4 endpoints):
| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/susu/stats | User/group/tx stats |
| GET | /api/susu/users | List users with balances |
| GET | /api/susu/groups | List groups with members |
| GET | /api/susu/transactions | Filter transactions |

**Legal Brain** (1 endpoint):
| Method | Path | Purpose |
|--------|------|---------|
| GET | /api/legal-brain/stats | Doc/source/session counts |

**KLAUS** (self-contained router at `/klaus`):
| Method | Path | Purpose |
|--------|------|---------|
| GET | /klaus/sources | List sources (filterable) |
| POST | /klaus/sources | Add source |
| PUT | /klaus/sources/{id}/status | Update source status |
| GET | /klaus/documents | List documents (filterable) |
| GET | /klaus/documents/flagged | Flagged-for-review docs |
| GET | /klaus/documents/{id} | Get document detail |
| GET | /klaus/documents/{id}/chunks | Get document chunks |
| PUT | /klaus/documents/{id}/review | Review/approve/reject |
| POST | /klaus/ingest | Ingest document (base64) |
| POST | /klaus/documents/{id}/verify | Run quality agents |
| POST | /klaus/documents/{id}/index | Vector index document |
| GET | /klaus/search | Vector similarity search |
| POST | /klaus/scheduler/trigger/{job} | Trigger scheduled job |
| GET | /klaus/monitoring | Dashboard metrics |
| GET | /klaus/audit-logs | Audit trail |
| GET | /klaus/reference/categories | Schema reference |
| GET | /klaus/reference/copyright | Copyright classifications |

KLAUS router mounted at `app.include_router(klaus_router)` — routes are at `/klaus/sources`, `/klaus/documents`, etc. (prefix in api_endpoints.py:53, but there's a possible double-prefix issue — the router has `prefix="/klaus"` AND is mounted via `include_router(klaus_router)` without an additional prefix, so should be correct).

### 2.5 Module Descriptors

| Module | File | Endpoints | Features |
|--------|------|-----------|----------|
| juris-kai | config/modules/juris-kai.json | 5 | Subscription mgmt, payments, referrals, document analysis |
| susu | config/modules/susu.json | 3 | Group savings, mobile money, fees |
| legal-brain | config/modules/legal-brain.json | 3 | Research sessions, knowledge engine |
| klaus | config/modules/klaus.json | 3 | Legal research, doc ingestion, citation, embeddings |

---

## 3. Legal Acquisition System (KLAUS)

### 3.1 Architecture
```
Scheduler (APScheduler) → Discovery Worker (hourly) → Ingestion Worker (5min)
→ Document Processor (pdfplumber → pypdf → OCR) → SHA-256 dedup
→ 6 Quality Agents → Vector Indexer (all-MiniLM-L6-v2, 384-dim)
→ PostgreSQL (klaus_db) → Legal Brain SQLite (WORM store)
```

### 3.2 Current State

**Database**: PostgreSQL `klaus_db` on localhost, user `klaus_user`
- **Documents**: 88 total (87 approved, 1 pending)
- **Chunks**: 1,924 indexed with embeddings
- **Authority Records**: 88 created
- **Sources**: 5 in DB (3 active, 2 deactivated)

### 3.3 Source Status

| # | Source | Domain | Tier | Status | Reason |
|---|--------|--------|------|--------|--------|
| 1 | Parliament Repository | parliament.gh | 1 | ✅ ACTIVE | Legacy showPDF handler — 91 docs found, 88 unique ingested |
| 2 | Constitute Project | constituteproject.org | 2 | ✅ ACTIVE | Generic scraper only — no dedicated handler |
| 3 | GhaLII | ghalii.org | 2 | ⚠️ BLOCKED | Cloudflare WAF 403 on all content paths (search, browse, /judgments/, /content/) |
| 4 | ~~eJudgment~~ | judicial.gov.gh | 1 | ❌ REMOVED | Login-walled — requires judge/lawyer credentials from Judicial Service of Ghana |
| 5 | ~~Ghana Publishing~~ | ghanapublishing.gov.gh | 1 | ❌ REMOVED | Paywalled — gpclonline.com login-walled e-commerce store; all PDFs redirect to login |

### 3.4 Scraper Handlers (`core/klaus/background_workers.py`)

| Handler | Domain | Method | Status |
|---------|--------|--------|--------|
| `_discover_parliament_repository` | parliament.gh | DSpace REST (404'd) → HTML → legacy showPDF | ✅ Working (legacy fallback) |
| `_discover_parliament_gh` | parliament.gh | Regex showPDF('name','title') → /epanel/docs/{name} | ✅ Working |
| `_discover_ghalii` | ghalii.org | Search terms → /search/?q= → follow judgment links | ❌ 403 Cloudflare WAF |
| `_discover_ejudgment_gh` | judicial.gov.gh | Session with SSL verify off → portal pages | ❌ Login-walled |
| `_discover_ghanapublishing_gh` | ghanapublishing.gov.gh | WordPress scraper with browse paths | ❌ gpclonline redirect |
| `_discover_judicial_gh` | judicial.gov.gh | Legacy Joomla scraper | ❌ Login-walled |

### 3.5 Acquisition Pipeline
1. **Discovery Phase**: `_DOMAIN_HANDLERS[domain]()` → returns list of document dicts with metadata
2. **Source Verification**: 6 quality agents verify each document (SourceVerification, LegalClassification, TierClassification, CitationExtraction, QualityAssurance, KnowledgeCurator)
3. **Copyright Check**: .gov.gh domains → `official_public_access`; others classified by type
4. **Deduplication**: SHA-256 content hash → skip if exists (no silent overwrites)
5. **Tier Classification**: 350+ regex patterns in `TIER_SIGNALS` map content to 16 tiers
6. **Vector Indexing**: all-MiniLM-L6-v2 (384-dim) embeddings via sentence-transformers, stored in pgvector

### 3.6 Scheduling
- **Daily** (3am UTC): Legislation check — scan Tier 1 sources
- **Weekly** (Mon 4am UTC): Judgments scan — Tiers 1+2
- **Monthly** (1st 5am UTC): Full refresh — all active sources
- **Quarterly** (Jan/Apr/Jul/Oct 1st 6am UTC): Accuracy verification + document review
- **Continuous**: Discovery worker (every 1h), Ingestion worker (every 5min)

### 3.7 Source Cleanup Action
During this audit, **2 sources were removed**:
1. **ejudgment.judicial.gov.gh** — deactivated. Login-walled portal requiring Judicial Service credentials.
2. **ghanapublishing.gov.gh** — deactivated. All publications redirect to gpclonline.com paywall.

Sources deactivated via `update_source_status(id, 'inactive')`. `TIER_1_SEEDS` in scheduler.py updated to remove both entries:

```python
TIER_1_SEEDS = [
    {"url": "https://repository.parliament.gh/home", "domain": "parliament.gh", "tier": 1, "jurisdiction": "Ghana"},
    {"url": "https://ghalii.org/", "domain": "ghalii.org", "tier": 2, "jurisdiction": "Ghana"},
]
```

---

## 4. Legal Answering Pipeline

### 4.1 Query Flow
```
User query (Telegram text) → _handle_free_text()
  → check_query_limit(account_id) → ❌ if exceeded
  → query_knowledge_base(query) → Legal Brain DB search (keyword LIKE on title + chunks)
  → build_context_preamble(results) → prompt preamble with Ghana legal sources
  → build_prompt(task_type, query) → full prompt with _JURISDICTION_GATE + _DATABASE_FIRST
  → _delegate_with_timeout(prompt, task_type, label) → ai_router.delegate()
  → AI response → record_query(account_id) → return to user with menu keyboard
```

### 4.2 Knowledge Base Search (`core/juris_kai/legal_context.py`)
- Database: `/var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db` (SQLite, read-only, dedicated connection)
- Search: Keyword LIKE on document titles + chunk content, filtered to `jurisdiction='Ghana'` + `review_status='approved'`
- Returns up to 5 chunks (MAX_CONTEXT_CHUNKS), each ≤ 2000 chars
- Results sorted: title matches first → by year DESC
- **⚠️ CRITICAL**: This searches the Legal Brain SQLite, NOT the PostgreSQL klaus_db. Currently the Legal Brain has 0 documents of its own — the KLAUS pipeline ingests to PostgreSQL, not to the Legal Brain WORM store.

### 4.3 AI Routing (`core/ai/ai_router.py`)
| Task Type | Primary | Fallback 1 | Fallback 2 |
|-----------|---------|------------|------------|
| juris_legal_teaching | deepseek_native_pro | gemini | groq |
| juris_case_analysis | deepseek_native_pro | gemini | omniroute_deepseek_flash |
| juris_research | deepseek_native_pro | gemini | geminix → omniroute_deepseek_flash |
| juris_argument_construction | deepseek_native_pro | gemini | groq |
| juris_flashcards | deepseek_native_pro | groq | — |
| juris_chat | deepseek_native_pro | groq | gemini |
| juris_document_vision | gpuai_gemma | — | — |

All juris_* roles pivoted to deepseek_native_pro primary as of 2026-08-07 (was deepseek_native_flash returning empty responses for legal statutes).

Legacy law_* roles also exist (`law_document`, `law_case_analysis`, `law_teaching`, `law_exam`, `law_flashcards`, `law_chat`, `law_document_vision`) — these are from the older law_tutor bot, a separate product.

### 4.4 Conversation Flow States
12 multi-step conversation flows supported:
| Step | Prompt Type | Return Menu |
|------|------------|-------------|
| search_topic | legal_teaching → juris_legal_teaching | Learn Law |
| search_case | legal_case_analysis → juris_case_analysis | Cases |
| gen_questions | legal_teaching → juris_legal_teaching | Practice |
| irac | legal_argument → juris_argument_construction | Practice |
| essay | legal_research → juris_research | Practice |
| mock_exam | legal_teaching → juris_legal_teaching | Practice |
| answer_eval | legal_argument → juris_argument_construction | Practice |
| flashcards | legal_flashcards → juris_flashcards | Study Tools |
| memory | legal_flashcards → juris_flashcards | Study Tools |
| quiz | legal_teaching → juris_legal_teaching | Study Tools |
| revision | legal_teaching → juris_legal_teaching | Study Tools |
| summarize | legal_research → juris_research | Documents |

### 4.5 Timeout Protection
`_delegate_with_timeout()` uses `concurrent.futures.ThreadPoolExecutor` with a **110-second wall-clock timeout**. This prevents a slow AI provider from blocking the bot's entire polling loop. Falls back to a generic error message if timeout expires.

### 4.6 Legacy Commands
Commands from `core/juris_kai/commands.py`:
- /help, /account, /subscribe, /learn, /case, /research, /argument, /flashcards, /progress

All delegate to text AI providers via the same ai_router.

---

## 5. Critical Issues

### 5.1 Knowledge Base Gap — CRITICAL
**The Legal Brain WORM store at `/var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db` appears to have 0 documents.** The `/api/legal-brain/stats` endpoint queries `klaus_documents` and `klaus_sources` tables — but those are in the **PostgreSQL** `klaus_db`, not the Legal Brain SQLite. 

**There may be a data flow gap**: KLAUS ingests to PostgreSQL but the Legal Brain SQLite (which Juris Kai queries via `legal_context.py:LEGAL_BRAIN_DB`) may be empty. This would mean every Juris Kai query searches an empty database, gets 0 results, and the AI answers from its training data alone — defeating the "database-first" principle.

**Action required**: Verify that `legal_context.py:LEGAL_BRAIN_DB` actually contains documents. Run:
```bash
ls -la /var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db
sqlite3 /var/lib/ai-orchestrator/legal_brain/permanent/legal_brain.db "SELECT COUNT(*) FROM documents;"
```

### 5.2 Source Access — HIGH
Only 1 of the 3 active sources can actually be scraped:
- parliament.gh: ✅ Working (legacy showPDF handler)
- ghalii.org: ❌ Blocked by Cloudflare WAF (even Playwright gets 403)
- constituteproject.org: ⚠️ Generic scraper only, no Ghana-specific handler

### 5.3 Admin Menu Stubs — MEDIUM
Most admin sub-menu items in the bot have action handlers that produce generic/placeholder responses. The bot's admin menu structure is richer than its actual functionality.

### 5.4 Settings Stubs — LOW
Language, Learning Level, and Notifications settings are "will be available in the next update" stubs.

### 5.5 Double-Prefix Risk — LOW
`klaus_router` has `prefix="/klaus"` (api_endpoints.py:53) but is included via `app.include_router(klaus_router)` without a second prefix in api.py:122. Currently this is correct (no double prefix), but if someone later adds `prefix="/klaus"` to the include_router call, all routes would become `/klaus/klaus/*`.

### 5.6 No Error Recovery for Background Workers — MEDIUM
The scheduler's background worker thread (`start_background_workers`) starts once. If the discovery or ingestion thread crashes, there's no restart mechanism.

---

## 6. Source Cleanup Summary

| Source | Domain | Issue | Action Taken |
|--------|--------|-------|---------------|
| Ghana Publishing Company | ghanapublishing.gov.gh | Paywalled — gpclonline.com login redirect | ❌ Deactivated, removed from seeds |
| eJudgment Portal | judicial.gov.gh | Login-walled — requires judge/lawyer credentials | ❌ Deactivated, removed from seeds |

**Remaining active sources**: 3 (parliament.gh, ghalii.org, constituteproject.org)
**Operationally usable**: 1 (parliament.gh via showPDF handler)

---

## 7. Recommendations

### Immediate (this week)
1. **Verify Legal Brain SQLite has documents** — this is the single most critical check
2. **Contact AfricanLII for GhaLII API access** — this is the largest untapped source of Ghana judgments
3. **Add constituteproject.org dedicated handler** for Ghana constitution pages
4. **Run KLAUS migration/sync** if Legal Brain SQLite is empty — sync PostgreSQL → SQLite WORM store

### Short-term (2 weeks)
5. **Resolve GhaLII access** — either AfricanLII API key or IP whitelisting request
6. **Implement admin menu actions** — fill in stubs for model routing, token usage, error logs, etc.
7. **Add background worker health check** — restart crashed discovery/ingestion threads

### Medium-term (1 month)
8. **Obtain eJudgment credentials** from Judicial Service of Ghana if possible
9. **Fill settings stubs** — language, learning level, notifications
10. **Historical backfill** — once GhaLII is accessible, run historical acquisition to build up the knowledge base

---

## Appendix A: File Reference

| File | Lines | Purpose |
|------|-------|---------|
| core/juris_kai/bot.py | 1316 | Bot handler, message routing, polling loop |
| core/juris_kai/menus.py | 342 | All menu/keyboard definitions |
| core/juris_kai/accounts.py | ~800 | Account mgmt, subscriptions, referrals, payments |
| core/juris_kai/prompt.py | 95 | Ghana-scoped prompt construction |
| core/juris_kai/legal_context.py | 188 | Legal Brain DB query interface |
| core/juris_kai/commands.py | ~200 | Legacy slash-command handlers |
| core/juris_kai/dashboard.py | ~200 | Admin dashboard data aggregation |
| core/api.py | ~3200 | Main API (legal endpoints at lines 648-918) |
| core/kai/command_center.html | 1500 | Cerebrum dashboard with legal panel |
| core/klaus/scheduler.py | 247 | KLAUS scheduler + background workers |
| core/klaus/background_workers.py | ~2000 | Scraper handlers for all sources |
| core/klaus/db_manager.py | 707 | PostgreSQL database layer |
| core/klaus/document_processor.py | ~400 | PDF extraction, dedup, citation |
| core/klaus/quality_agents.py | ~500 | 6 quality control agents |
| core/klaus/vector_indexer.py | ~200 | pgvector embeddings |
| core/klaus/api_endpoints.py | 309 | KLAUS REST API router |
| core/klaus/schema.py | ~200 | 16-tier DB schema |
| core/ai/ai_router.py | ~400 | Provider routing (juris_* roles) |

## Appendix B: Database Tables

| DB | Table | Records | Purpose |
|----|-------|---------|---------|
| PostgreSQL (klaus_db) | klaus_documents | 88 | Ingested legal documents |
| PostgreSQL (klaus_db) | klaus_document_chunks | 1,924 | Vector-indexed text chunks |
| PostgreSQL (klaus_db) | klaus_sources | 5 (3 active) | Source catalog |
| PostgreSQL (klaus_db) | klaus_acquisition_tiers | 16 | Tier definitions |
| PostgreSQL (klaus_db) | klaus_legal_authority_records | 88 | Authority metadata per doc |
| PostgreSQL (klaus_db) | klaus_audit_log | ongoing | All operations |
| SQLite (juris_kai.db) | juris_accounts | N/A | User accounts |
| SQLite (juris_kai.db) | juris_referrals | N/A | Referral tracking |
| SQLite (legal_brain.db) | documents* | **0?** | WORM legal document store |

*The `/api/legal-brain/stats` endpoint queries the PostgreSQL klaus_db tables, not the Legal Brain SQLite. The actual Legal Brain SQLite may be empty.

---

*Audit prepared by Kai Command Center | 2026-08-07*
