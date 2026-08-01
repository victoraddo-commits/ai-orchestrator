# Kai Infrastructure Audit & Roadmap Enhancement
## Chief AI Architect Review — Transforming Kai into an "AI Operating System"

Generated 2026-08-01. Grounded against the live `ai-orchestrator` codebase, `roadmap.json`, and the dedicated GPU infrastructure review delivered earlier the same day. Per the operator's instruction, this audit does **not** propose rebuilding Kai from scratch and does **not** duplicate features that already exist — it inventories what is real, what is roadmap-intent-only, and what specifically needs to be added.

### Table of Contents
1. [System Audit: Capability Inventory](#system-audit-capability-inventory)
2. [Control Plane, Intelligent Routing, Auto-Switching, Quality Scoring](#control-plane-intelligent-routing-auto-switching-quality-scoring)
3. [Conversational Intelligence, Conversation Engine, Memory System](#conversational-intelligence-conversation-engine-memory-system)
4. [Agent Workforce, Document Intelligence, Legal Knowledge Brain, Controlled Learning](#agent-workforce-document-intelligence-legal-knowledge-brain-controlled-learning)
5. [Security & Enterprise, Scalability, Telegram/Web UX, GPU Migration Cross-Reference](#security--enterprise-scalability-telegramweb-ux-gpu-migration-cross-reference)
6. [Final Synthesis: Updated Architecture, Roadmap Changes, Migration Plan](#final-synthesis-updated-architecture-roadmap-changes-migration-plan)

---

### Editorial Note

Each section below was generated independently against a shared, hand-verified facts file (live codebase inspection, `roadmap.json`, and the same-day GPU review) and then spot-checked before assembly. All phase IDs referenced (15A, 17O, 17P) are real, existing roadmap phases — verified against `roadmap.json` directly, not invented. No new phase IDs are proposed in this audit; it recommends *expanding the scope* of existing phases rather than adding new ones, which is the more conservative reading of "add only the missing improvements."

---

---

## System Audit: Capability Inventory

### Kai System Architecture & Capability Audit

| Feature Area | Sub-Feature / Capability | Status | Evidence (Grounded in Live Codebase / Infrastructure) |
| :--- | :--- | :--- | :--- |
| **AI Providers** | `claude` (CloudCLI/Anthropic) | (Completed) | Registered and working via `core.ai.ai_router` and `core/ai_provider.py`. |
| | `gemini`, `openai`, `groq`, `openrouter` variants | (Completed) | Registered and active in `ROLE_PROVIDERS` configuration. |
| | `deepseek` (Dedicated OpenRouter key) | (Completed) | Registered with dedicated routing configuration in `core/ai_provider.py`. |
| | `minimax` (Direct API) | (Needs improvement) | Deliberately paused/excluded from all text roles per past evidence-based reviews. |
| **API Integrations** | `POST /kai/chat` | (Completed) | Real, working chat endpoint with persistent history in `kai_chat_history.json` (`core/api.py`). |
| | Telegram Bot Integration | (Partially implemented) | Handled via dedicated poller (`core.telegram_poller`, `@KaiEnzo_bot`), with parity handlers in progress. |
| | API Authentication | (Partially implemented) | Uses narrow bridge-token validation (`require_bridge_token` dependency) rather than unified auth. |
| **Agent System** | `core.ai.agent_roles.py` Wrappers | (Partially implemented) | Thin wrappers (`architecture_agent`, etc.) pointing directly to `delegate()` without distinct states. |
| | General Agent Framework | (Missing) | Historical bytecode remains in `core/agents/__pycache__/`, but `.py` source files are completely gone. |
| | Multi-Agent Coordination | (Missing) | No autonomous multi-agent orchestration, tool use, or inter-agent messaging layers exist. |
| **Memory System** | Conversation History (`kai_chat_history.json`) | (Partially implemented) | Flat, growing JSON file storing raw logs without short/long-term separation or compression. |
| | Context Summarization / Compression | (Missing) | Full chat history is retained and replayed without automated contextual truncation or summarization. |
| **Conversation System** | Multi-Turn Context Handler | (Completed) | Working API chat layer retaining session states via shared persistence files. |
| **User Management** | Unified User Accounts & Auth | (Missing) | Roadmap phase 15A is pending; no multi-tenant accounts, user databases, or RBAC systems exist. |
| | Per-User Data Isolation | (Missing) | Relies on narrow Telegram chat-id allowlists, lacking multi-tenant data boundaries. |
| **Document Processing** | Operator Document Upload & Storage | (Completed) | `core.law_documents` supports basic PDF text extraction (via `pypdf`) for the Kai Dashboard Library. |
| | Document Classification & Chunking | (Missing) | Zero automated structural indexing, chunking, or metadata extraction beyond filename/time. |
| **OCR & Vision** | Optical Character Recognition (OCR) | (Missing) | No OCR libraries (`Tesseract`, `PaddleOCR`, etc.) are installed or listed in `requirements.txt`. |
| | Vision-Capable AI Integration | (Missing) | While Gemini/Claude vision providers exist in text configs, no code paths send images to them. |
| **Legal Knowledge** | "Ghana Legal Brain" (Roadmap Phase 17O) | (Missing) | Zero application code written; architecture specifies a strict primary-sources-only 7-category taxonomy. |
| | Controlled Operator-Approval Pipeline | (Missing) | Formal review workflows for shared knowledge ingestion exist as design specifications only. |
| **Vector Database** | Vector Storage & Embeddings | (Missing) | Verified completely absent from `requirements.txt` (no Chroma, Qdrant, Pinecone, or FAISS). |
| **Security** | Unified Platform Security Layer | (Missing) | No centralized authentication, encryption-at-rest policies, or unified audit-logging frameworks. |
| | Execution & Approval Audits | (Partially implemented) | `core.approval` and `core.execution_audit` track self-build pipeline infrastructure changes. |
| **Monitoring** | Health & Incident Management | (Completed) | Working automation (`core.health`, `core.incident_manager`, `core.decision_engine`) running via systemd. |
| | Approval Watchdog | (Completed) | `core.approval_watchdog` actively reminds the operator via Telegram if builds stall. |
| | Infrastructure Dashboards (`proxdash`) | (Completed) | Live Node backend and static frontend tracking Proxmox and WireGuard states. |
| **Cost Management** | Provider Cost Tiers & History | (Partially implemented) | `cost_tier` tracking and raw usage logs (`memory/ai_usage_history.json`) capture execution expenses. |
| | Quota & Health Tracking | (Completed) | Dynamic provider skipping via `core.ai.provider_health` reacts to real-time API limits. |
| **Infrastructure** | Proxmox Site A & Site B Topology | (Completed) | Deployed across Site A (i5-9500, 24.9GB RAM) and Site B (i5-6500T, 15GB RAM), linked via WireGuard. |
| | Self-Build and Deployment Pipeline | (Completed) | Automated testing, isolated cloning, and systemd-managed services operate continuously. |
| **Future GPU Migration** | Cloud/Local Compute Strategy | (Partially implemented) | A completed separate GPU infrastructure review concludes that no local GPUs exist and any cloud GPU must integrate as a standard `ai_router` provider, keeping real-time paths isolated from the 210ms inter-site link. |

---

### Audit Summary

Kai’s core infrastructure, self-build pipelines, health monitors, and multi-provider AI routing layer are genuinely mature, stable, and production-ready for a solo-operator environment. However, nearly everything concerning document intelligence, vector retrieval, legal knowledge bases, multi-agent frameworks, and user management is still pre-implementation roadmap intent rather than working software. Bridging the gap from an advanced solo-operator self-building orchestrator to a multi-tenant "AI operating system" or commercial legal platform requires treating these missing layers as greenfield development targets rather than extensions of existing code.

---

## Control Plane, Intelligent Routing, Auto-Switching, Quality Scoring

## AI Provider Orchestration Control Plane

What already exists and must be preserved:
- `core.ai.ai_router.delegate()` is the single execution bottleneck and entry point.
- `core.ai.provider_health` tracks real-time signals (`quota_exceeded`, raw errors, header-based limits) and actively skips dead or exhausted providers.
- `core.ai.ai_router.get_usage_history()` and `memory/ai_usage_history.json` record every raw call (provider, task_type, success, duration, error, cost).

What is genuinely missing to form a true **Control Plane**:
1. **The Aggregation Layer**: `ai_usage_history.json` is currently a raw, growing flat list of individual invocation logs. There is no background aggregator translating these flat logs into a rolling health profile (e.g., rolling success rate over the last 50 calls, average latency per provider over 1 hour).
2. **The Control Plane Registry**: A structured state registry (backed by a new lightweight config or memory file, e.g., `memory/model_control_plane.json`) that explicitly defines the operator's metadata schema for every registered provider and model:
   - `Provider` / `Model` identifier
   - `Purpose` (e.g., general text, complex legal reasoning, high-volume ingestion)
   - `Strengths` / `Weaknesses` (e.g., fast token generation, weak multi-step logic)
   - `Cost Tier` (`free_or_low_cost` / `paid`)
   - `Speed Profile` (rolling average latency ms)
   - `Reliability Score` (rolling success percentage)
   - `Current Availability` (live flags derived from `provider_health`)
   - `Usage Limits` (known rate-limit ceilings)

### Concrete Design for the Extension
Instead of building a heavy dashboard or separate service (which violates the solo-operator zero-cloud-budget and resource constraints), extend `core.ai.provider_health` to maintain a materialized summary state:

1. **Materialized Health Metrics**: When `delegate()` finishes a call (success or failure), it invokes a lightweight internal update function:
   ```python
   # Conceptual extension inside core/ai/provider_health.py
   def update_provider_metrics(provider_name: str, success: bool, duration_ms: float, cost: float, error_type: str = None):
       # Load rolling window or update exponential moving averages (EMA)
       # Store state in memory/provider_control_state.json
   ```
2. **Exposing the Control Plane Record**: Create a clean inspection utility (`core.ai.provider_health.get_control_plane_status()`) that stitches together:
   - The static metadata definitions (Model, Purpose, Strengths, Cost Tier).
   - The dynamic runtime state from `provider_health` (Is it currently skipped? Is quota exceeded?).
   - The computed telemetry from `ai_usage_history.json` (rolling P95 latency, actual success rate, tracked spend).

This leverages existing plumbing (`delegate`, `provider_health`, usage logs) while turning passive logs into an active, queryable model control plane.

---

## Intelligent Model Routing

What already exists and must be preserved:
- `ROLE_PROVIDERS` maps `task_type` -> ordered provider candidate list.
- `FIXED_ORDER_TASK_TYPES` locks down high-stakes legal roles (architecture, planning, law_document, law_case_analysis, etc.) to prevent rotation.
- Rotation indexes handle load-spreading for generic task types.

Evaluating the operator's desire for a pre-request classification step (simple vs. complex vs. high-risk vs. vision vs. coding):

### Reality Check & Risk Assessment
Running an upfront AI classification step on *every single request* introduces:
- **Added latency**: At least 200–500ms added to every turn.
- **Added cost**: Burning tokens just to decide *which* model should burn tokens.
- **Redundancy**: The existing `task_type` parameter passed to `delegate()` *already* acts as an explicit human- or code-level classification (e.g., a caller calling `task_type="law_case_analysis"` has already declared the request's complexity).

### The Concrete Extension: Targeted Complexity Guardrails
Finer classification is **only** worth doing for ambiguous or high-volume catch-all task types (e.g., `task_type="general_chat"` or `law_chat`), where simple queries mix with complex multi-step questions. 

Do *not* add a separate LLM classification call. Instead, implement a **Rule-Based Pre-Classifier** in `core.ai.ai_router` that inspects the prompt string instantly with zero network overhead:
1. **Length & Structure Heuristics**: Character count, presence of code blocks, legal citation patterns (e.g., section numbers, "v.", "Plaintiff").
2. **Dynamic Tier Upgrading**: 
   - If `task_type` is generic (`law_chat` / `general_reasoning`) but the heuristic detects high complexity markers (e.g., >2000 characters, legal terminology flags), temporarily override the candidate list to prepend a high-reasoning model (e.g., `openrouter_claude_opus` or `deepseek` pro tier).
   - If the request is a simple greeting or short question (<100 characters), route directly to a fast, low-cost tier (`groq` or `deepseek-flash`).
3. **Vision / Document routing**: If the prompt or payload includes image paths or raw document bytes, intercept and force-route to a vision-capable provider (e.g., `gemini` or `anthropic`) regardless of the default `task_type` mapping.

This integrates directly into the top of `delegate(prompt, task_type, ...)` before candidate selection occurs, preserving the exact same function signature while upgrading how the candidate list is initialized.

---

## Automatic Model Switching

What already exists and must be preserved:
- Automatic fallback *on failure* (if provider A errors or times out, `delegate()` catches it and tries provider B).
- Quota-exceeded skipping (if `provider_health` flags a provider as exhausted, it is skipped entirely).

What is genuinely missing:
- **Pre-emptive switching** based on degraded health metrics (e.g., rising latency, intermittent 5xx errors, or creeping error rates) *before* a hard failure or total quota block occurs.

### Concrete Design for Pre-Emptive Switching
Extend `core.ai.provider_health` to track a **Degraded State** alongside `quota_exceeded`:
1. **Health Thresholds**: If a provider experiences 3 consecutive timeouts or >20% error rate over a 15-minute rolling window (computed from `ai_usage_history.json` summaries), flag its state as `degraded`.
2. **Pre-Emptive Deprioritization**: When `delegate()` builds its candidate list, active `degraded` providers are automatically moved to the *very end* of the fallback queue, behind healthy providers.
3. **Cost-Aware Pre-Emptive Switching**: If a low-cost provider becomes degraded or exhibits high latency (>5000ms), pre-emptively shift traffic to a reliable paid/higher-tier fallback *before* the user experiences a hanging request or failure.

This builds directly on top of the existing `provider_health` and fallback mechanisms without requiring any new infrastructure components.

---

## Model Quality Scoring Engine

What already exists and must be preserved:
- `memory/ai_usage_history.json` logs every call's success, duration, and cost.
- `core.ai.ai_router` manages provider selection order.

What is genuinely missing:
- A scoring formula and an aggregation script that turns raw historical logs into actionable **Quality, Speed, Cost-efficiency, and Reliability scores**.
- The specific routing split for DeepSeek (distinguishing high-volume/low-cost "DeepSeek Flash" from complex-reasoning "DeepSeek Pro").

### 1. Fixing the DeepSeek Prerequisite First
Today, `deepseek` is registered as a *single* provider entry in `ai_router` and `ai_provider.py`. To fulfill the operator's requirement for DeepSeek Flash vs. DeepSeek Pro:
- Split the single `deepseek` registration into two distinct provider keys in the router config: `deepseek_flash` (pointing to the high-volume, low-cost endpoint/model) and `deepseek_pro` (pointing to the heavy reasoning model).
- Only once these are distinct entities can the scoring engine evaluate and route between them independently.

### 2. Concrete Scoring Formula & Aggregator
Implement a lightweight scoring module (`core.ai.model_scoring`) that runs periodically (or on-demand when loading candidates) to parse `ai_usage_history.json` and compute a normalized score (0 to 100) for each provider/model:

- **Reliability (Weight: 40%)**: 
  $$\text{Reliability} = \left( \frac{\text{Successful Calls}}{\text{Total Calls}} \right) \times 100$$
- **Speed (Weight: 30%)**: 
  Normalized inverse of rolling P95 duration (e.g., mapping 500ms = 100 points, 10000ms = 0 points).
- **Cost-Efficiency (Weight: 20%)**: 
  Derived from the model's cost tier and actual billed cost per 1k tokens from usage history (`free_or_low_cost` scores highest; expensive paid models score lower unless justified by complexity).
- **Task Suitability (Weight: 10%)**: 
  A static bonus modifier based on how well a model matches specific `task_type` categories (e.g., Claude for legal drafting, DeepSeek for code/logic).

### 3. Dynamic Selection Integration
Feed the output of this scoring engine into `delegate()`'s candidate sorting logic: instead of relying *only* on static `ROLE_PROVIDERS` order, dynamic candidate ordering sorts providers within their assigned tier by their **Composite Quality Score**. 

This creates a self-healing loop: if a provider's latency spikes or error rate climbs, its computed score drops, it sinks in the candidate priority, and traffic shifts automatically to healthier alternatives—all while respecting the solo operator's zero-budget constraint by heavily favoring cost-efficient models that maintain high reliability scores.

---

## Conversational Intelligence, Conversation Engine, Memory System

## Claude-Level Conversational Intelligence

The operator’s goal is fluid, context-aware, multi-turn conversation rather than isolated query-response loops. Achieving this requires a structured approach to memory that respects the realities of the current codebase.

### Current Reality Check
- **What exists:** `POST /kai/chat` (`core/api.py`) backed by `kai_chat_history.json` storing a flat array of message objects. 
- **What does not exist:** No short-term/long-term memory separation, no automatic context compaction, and crucially, **no user account system** (Phase 15A is pending). 

### Short-Term vs. Long-Term Memory Design

#### 1. Short-Term Memory (Session Context)
* **What it is:** The immediate thread of conversation, active goals, recent turns, and working context for the current session or task.
* **Buildable Now:** Extend the current flat `kai_chat_history.json` array into a structured JSON session object. Instead of an array of raw message dictionaries, store a session envelope:
  ```json
  {
    "session_id": "uuid-or-timestamp",
    "active_goal": "Reviewing Ghana Land Title Registration Act",
    "recent_messages": [...],
    "ephemeral_context": {
      "last_referenced_document": "lib_id_402.pdf",
      "pending_clarification": null
    }
  }
  ```
* **Why it works now:** It requires zero user account scaffolding; it relies strictly on the existing single-operator JSON storage model.

#### 2. Long-Term Memory (Operator Preferences, Projects, and Approved Knowledge)
* **What it is:** Persistent facts, stylistic preferences, ongoing project scopes, and explicit architectural/legal decisions that must survive across separate terminal sessions or days.
* **The Blocker:** True *per-user* long-term memory requires an identity system to scope whose memory belongs to whom. Because Phase 15A (local accounts + permissions) is pending, we cannot build a multi-tenant long-term memory architecture.
* **Buildable Now (Single-Operator Scope):** Build a static, operator-scoped long-term memory store (`memory/operator_long_term.json`) governed by an **explicit-control principle**—nothing auto-writes to long-term memory. 
  - **Write Path:** The operator explicitly commands Kai: *"Remember that for all Ghana land cases, we prioritize the 1992 Constitution provisions over customary law where they conflict."* Kai calls a dedicated internal tool/handler to append this to `memory/operator_long_term.json`.
  - **Read Path:** Every call to `ai_router.delegate()` for chat or legal tasks automatically injects the contents of `memory/operator_long_term.json` into the system prompt prefix.

---

## Conversation Engine & Context Handling

The operator wants Kai to naturally handle continuity, corrections, and contextual cross-references (e.g., *"I don't think that is correct,"* *"continue from yesterday,"* *"use the previous document,"* *"compare this with the earlier case"*). 

### Engineering vs. Prompting Breakdown

| Operator Request / Statement Class | Implementation Requirement | Status in Current Codebase |
| :--- | :--- | :--- |
| **"I don't think that is correct"** / **"Continue from yesterday"** | **Prompting & History Window.** Since `kai_chat_history.json` retains past turns, the LLM already receives the conversational context. Handling corrections relies on prompt engineering instructing the LLM to acknowledge the correction, adjust its stance, and update its working hypothesis without breaking persona. | **Buildable Now** (leveraging existing chat history). |
| **"Use the previous document"** | **State Tracking.** Requires short-term memory to track `last_referenced_document` in the session envelope so pronoun resolution ("the document") maps to a specific file ID or name. | **Buildable Now** (with minor session envelope additions). |
| **"Compare this with the earlier case"** | **Legal Knowledge Base & Document Pipeline.** This requires fetching structured text from external documents or historical case repositories. | **Blocked** on Phase 17O ("Ghana Legal Brain") and document chunking/embedding infrastructure, which **do not exist yet**. |

#### How to Handle "Continue from Yesterday" Today
Without a database, "yesterday" means looking back at historical session files or timestamped entries in `kai_chat_history.json`. When the operator says *"continue from yesterday,"* the chat handler:
1. Loads the most recent session file or historical log from the previous calendar day.
2. Extracts the `active_goal` and last unresolved question.
3. Automatically injects a context priming block into the current prompt: `[Context Restoration from Yesterday: Goal was X, left off at Y]`.

---

## Conversation Memory System & Compression

Because `kai_chat_history.json` currently performs a full load-append-save cycle on every message and retains infinite history, long conversations will eventually bloat the context window, increase latency, and drive up token costs.

### Concrete Compression Design

#### 1. Trigger Condition
* **Threshold:** When a conversation history exceeds **30 messages** or estimated token usage crosses **12,000 tokens** (tracked via light word-count/token estimation in `core/api.py`), compaction triggers automatically before the next AI call.

#### 2. The Summarization Mechanism
Instead of discarding old turns or building a new vector store, use the existing `ai_router.delegate()` to perform a structured distillation. Kai summarizes the older turns into a standardized JSON template:

```json
{
  "summary_period": "Messages 1-25",
  "user_issue": "Investigating registration procedures for stool lands in Ashanti Region.",
  "established_facts": [
    "Stool lands require formal concurrence from the Lands Commission.",
    "Customary grants must be registered within the statutory timeframe."
  ],
  "legal_questions_raised": [
    "Does Article 267 of the 1992 Constitution override prior customary leaseholds?"
  ],
  "next_action_pending": "Analyze specific Supreme Court precedents on stool land alienation."
}
```

#### 3. Execution Flow
1. **Old Turns Compacted:** Messages 1 through $N-10$ are replaced in `kai_chat_history.json` by the structured summary JSON block.
2. **Recent Turns Preserved:** Messages $N-9$ through $N$ (the last 10 turns) are kept verbatim to maintain conversational nuance, tone, and immediate context.
3. **Prompt Injection:** The system prompt receives both the structured summary block and the recent verbatim messages.

### The Legal Risk: Lossy Compression in Legal Contexts
* **The Danger:** Automated LLM summarization is inherently lossy. In a legal or architectural analysis context, dropping a specific statutory reference, a temporal detail (e.g., *"not the 1962 Act, the 2020 Amendment"*), or a precise caveat given by the operator could invalidate subsequent legal reasoning.
* **The Guardrail (What *Never* to Summarize):**
  1. **Exact Statutory Citations & Act Numbers:** If an act number, article, section, or specific case name was mentioned, it must be extracted into a permanent `key_entities` list rather than generalized away.
  2. **Operator Directives & Constraints:** Statements beginning with "always," "never," "ensure that," or "do not" must be copied verbatim into the long-term preferences file (`memory/operator_long_term.json`) rather than compressed into a summary.
  3. **Explicit Operator Corrections:** Any turn where the operator corrected Kai (*"I don't think that is correct"*) must retain its full interactive trace so future reasoning avoids repeating the corrected error.

---

## Agent Workforce, Document Intelligence, Legal Knowledge Brain, Controlled Learning

## AI Agent Workforce

The operator's architectural goal includes a multi-agent workforce: Conversation Manager, Legal Research, Document, Analysis, Writing, Citation, Review, and Infrastructure agents. 

### What Actually Exists Today
- **`core.ai.agent_roles.py`**: A thin module providing 4 preset wrappers (`architecture_agent`, `research_agent`, `fast_analysis_agent`, `general_reasoning_agent`) that map directly to specific `task_type` parameters calling the single underlying `core.ai.ai_router.delegate()` function.
- **Specialized Pipeline Roles**: `core.ai.chief_architect` and `core.decision_engine` handle build-plan generation and incident response specifically within the self-build infrastructure pipeline—not general-purpose conversational or legal tasks.
- **Abandoned Precedent**: The stale bytecode files found in `core/agents/__pycache__/` (`base_agent`, `analyst_agent`, `observer_agent`, `orchestrator_agent`, `planner_agent`, `supervisor_agent`, `context_manager`, `execution_guard`) confirm that an ambitious multi-agent object hierarchy was attempted previously and abandoned. No corresponding source code remains.

### Reality Check for a Solo Operator
Building 8 distinct, standing microservice agents with independent state machines, background loops, and inter-agent message queues is unnecessary overhead for a solo operator on a modest 2-site Proxmox layout without a paid cloud infrastructure budget. 

Instead, recognize that **most of these "agents" do not need separate runtime processes or complex orchestration frameworks.** They are distinct **cognitive modes**—combinations of a specialized prompt, a designated model preference via `ai_router`'s `ROLE_PROVIDERS`, and a structured output schema.

### Recommended Lean Design: Task-Type Specialization Over Microservice Sprawl
Rather than implementing 8 standing background agents, map the requested workforce onto **specialized task types** routed through the existing `delegate()` function:

1. **Conversation Manager (`law_chat`)**: Handles conversational state, session continuity, and user-facing dialogue via the shared chat handling pipeline (`handle_kai_chat()`).
2. **Legal Research & Citation Agent (`law_case_analysis` / `law_document`)**: Dedicated to parsing primary legal sources, applying the 7-category taxonomy, and extracting strict, verifiable citations (source, section, case, page).
3. **Document & Analysis Agent (`fast_analysis_agent` / text transformation)**: Handles raw text extraction cleaning, structural classification, and preliminary summarization.
4. **Writing & Synthesis Agent (`law_teaching` / `law_exam`)**: Generates structured legal briefs, tutorials, or educational summaries.
5. **Review & Governance Gate (`approval_watchdog` / human-in-the-loop)**: Enforces the mandatory operator-approval barrier before any new legal knowledge enters the shared corpus.
6. **Infrastructure & Incident Agent (`core.decision_engine` / `remediation_runner`)**: Already exists and runs continuously via the 60s scheduler cycle to handle self-build and system healing.

**Implementation Rule**: Do not build persistent agent state machines or inter-agent communication buses. Treat the "agents" as prompt-and-routing configurations built on top of the robust, already-working `ai_router.delegate()` infrastructure.

---

## Document Intelligence Platform

The document intelligence platform bridges raw operator uploads to structured, searchable knowledge. The current reality is limited: `core.law_documents` handles basic PDF text extraction via `pypdf` for the Kai Dashboard Library tab, with zero OCR, zero vector storage, and zero document classification.

### The Target Ingestion Pipeline
```
Upload -> Analyzer -> [OCR / Vision Engine] -> Text Extraction -> Cleaning -> Classification -> Metadata Tagging -> Embedding Generation -> Vector DB -> AI Reasoning
```

### OCR Engine Evaluation for Site B (CPU-Only, 4 Cores, ~15GB RAM)
With no discrete GPU and strict solo-operator constraints, we evaluate open-source OCR options:

| Tool | Hardware Profile | Accuracy on Legal Scans | Integration Complexity | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Tesseract OCR** | Extremely lightweight; pure C++ binary with Python bindings (`pytesseract`). Low RAM overhead. | Moderate on clean text; poor on degraded historical legal scans or multi-column layouts without pre-processing. | Low. Easily installed via apt package (`tesseract-ocr`) with zero GPU requirements. | **Recommended baseline** for text-heavy, clean PDFs where speed and resource economy matter most. |
| **PaddleOCR** | Optimized for CPU inference via PaddlePaddle, but heavier dependency footprint. Good layout analysis. | High on complex tables and multi-column documents. | Medium. Requires additional Python wheel dependencies and model weight downloads. | **Strong secondary candidate** if layout preservation is prioritized over raw speed. |
| **EasyOCR** | PyTorch-based. High accuracy on diverse fonts and handwriting. | High. | **Prohibitive.** PyTorch installation footprint and heavy CPU memory bandwidth usage risk starving Site B's 15GB RAM limit. | **Rejected** due to heavy resource footprint on CPU. |

**Practical Recommendation**: Start with **Tesseract OCR** integrated into a lightweight Python subprocess step within `core.law_documents`, falling back to direct text extraction (`pypdf`) when an uploaded PDF already contains an embedded text layer.

### Wiring Vision-Capable Providers (`ai_provider.py`)
While `gemini`, `claude`, and `openai` are already registered as text providers in `ai_provider.py`, sending scanned image pages or multi-page PDF renders requires extending the provider interface:
- **Payload Extension**: Modify provider client wrappers to accept multi-modal payload structures (base64 image data or file handles alongside prompt text) for Gemini Vision, Claude 3/3.5 Vision, or OpenAI GPT-4o.
- **Routing Integration**: Add a dedicated `document_vision` or `ocr_fallback` task type to `ROLE_PROVIDERS` so that when Tesseract confidence scores fall below a strict threshold, the document page is automatically routed to a vision-capable provider (e.g., Gemini Flash via OpenRouter) for intelligent transcription.

---

## Legal Knowledge Brain (Roadmap Phase 17O)

Phase 17O ("Ghana Legal Brain") specifies a professional legal RAG system adhering to strict structural and governance mandates.

### Core Architecture & Already-Decided Constraints
- **7-Category Taxonomy**: Every ingested document must be classified into one of 7 canonical legal categories (e.g., Constitution, Acts, Subsidiary Legislation, Case Law/Precedents, Practice Directions, Approved Treatises/Books, Administrative Directives).
- **Primary-Sources-Only Policy**: The shared knowledge base accepts *only* verified primary legal authorities and approved texts. User-uploaded files in private sessions remain strictly isolated and **never** pollute the shared corpus.
- **Mandatory Operator Approval**: Zero automated ingestion. Every parsed, classified document sits in an administrative staging queue until explicitly approved by the operator.
- **Strict Citation Preservation**: Every generated answer must explicitly trace back to its source, section, case name, document title, and page number.

### Vector DB, Embedding Model, and Storage Choice for Site B
Given zero cloud budget, modest hardware (Site B: 4 cores, 15GB RAM, no GPU), and the need for local data sovereignty:

- **Vector Database**: **Chroma** (embedded mode, running locally on Site B backed by persistent SQLite storage) or **FAISS** (flat index stored on disk). Chroma is recommended because it natively handles metadata filtering (essential for querying strictly by the 7-category taxonomy and document version) with a minimal Python footprint and zero external database server process to manage.
- **Embedding Generation**: Use a lightweight, high-performance open-source embedding model via an API provider or local CPU-friendly inference (e.g., `text-embedding-3-small` via OpenRouter/OpenAI for pennies, or a small local sentence-transformer model like `all-MiniLM-L6-v2` if completely offline execution is required). Given OpenRouter's low cost for embeddings and Site B's CPU constraints, utilizing an efficient cloud API endpoint for embedding generation avoids unnecessary CPU compute load during ingestion batches.

---

## Controlled Learning System

The controlled learning system operationalizes the principle that **nothing enters the shared legal knowledge base without explicit operator review**. This extends the existing Kai Dashboard Library tab into a full review and promotion workflow.

### Concrete Workflow & State Machine
1. **Submission Phase**: 
   - Operator or authorized pipeline submits a document (PDF, statute, case judgment) via the Kai Dashboard Library interface or designated ingestion endpoint.
   - The document is assigned a unique UUID and placed in a `PENDING_REVIEW` state.
2. **Automated Extraction & Analysis**:
   - Background worker extracts text (via `pypdf` or Tesseract OCR), cleans whitespace, strips artifacts, and runs an AI-assisted analysis via `ai_router.delegate()` to propose:
     - Document title and publication date.
     - Taxonomic category (one of the 7 official Ghana Legal Brain categories).
     - Summary abstract and key legal principles.
3. **Admin Approval Gate (Kai Dashboard)**:
   - The Kai Dashboard presents the document, extracted text preview, and AI-proposed metadata in an **Approval Queue** view (similar in spirit to the self-build pipeline's `core.approval` review interface).
   - The operator can inspect, edit metadata, reject, or click **"Approve & Commit"**.
4. **Knowledge DB Update & Embedding Pipeline**:
   - Upon operator approval, the state transitions to `APPROVED`.
   - The text is chunked into semantic segments (e.g., 500-token chunks with 50-token overlap, preserving section headers).
   - Embeddings are generated and written to the local Chroma vector database alongside rich metadata tags (category, source authority, version, approval timestamp).
   - The document is officially indexed and made available to the `law_case_analysis` and RAG query pipelines.

---

## Security & Enterprise, Scalability, Telegram/Web UX, GPU Migration Cross-Reference

## Security and Enterprise Features

The current state of security and user management across the Kai ecosystem is highly fragmented. While individual components have narrow access controls—such as single-allowlist Telegram models for Law Tutor and bridge-token validation in `core.api`—there is **no unified authentication, authorization, or per-user data isolation layer**. Roadmap phase 15A remains pending and represents a hard prerequisite for multi-tenant products.

### Minimum Viable Auth (MVA) vs. Post-Launch Features

Given Juris Kai's design requirements (independent paying users, strict per-user data isolation, group-chat billing, and per-document fees), the current single-allowlist model used by Law Tutor is completely insufficient. Law Tutor can safely retain its isolated single-user Telegram allowlist since it serves only one private operator. Juris Kai, however, requires a distinct security baseline before accepting external traffic or user data.

**Minimum Viable Auth (MVA) for Juris Kai Launch:**
1. **Local Account Identity (`core.auth` / Phase 15A):** Secure password hashing (Argon2id or bcrypt), unique user IDs, and active/inactive state tracking in a lightweight SQLite database.
2. **Capability-Based Permissions:** A simple role mapping (`user`, `pro_user`, `admin`) to gate endpoint access.
3. **Session Management:** Secure, signed HTTP-only cookies or short-lived JWTs for the web dashboard, alongside a secure Telegram account-linking mechanism (mapping Telegram `chat_id` to an internal user UUID).
4. **Strict Per-User Data Isolation:** Enforcing database and file-system query filters using the authenticated user's UUID. A user's uploaded documents (`core.law_documents`) and chat histories (`kai_chat_history.json` migration to SQLite/partitioned storage) must *never* bleed into global context or other users' sessions.

**What Can Wait Post-Launch:**
- Enterprise SAML/OIDC/SSO integrations.
- Granular, custom role-based access control (RBAC) UI builders.
- Automated compliance reporting frameworks.

### Data Protection, Auditing, and Infrastructure Safety

- **Encryption-at-Rest:** Sensitive credentials (API keys, database strings) must move strictly out of plain text configuration files and into encrypted environment stores or an encrypted SQLite database using standard symmetric encryption (e.g., Fernet / AES-GCM) keyed via an environment variable restricted to root.
- **Audit Logs:** Generalize `core.execution_audit` beyond infrastructure changes to record user-facing security events: failed login attempts, unauthorized document access attempts, permission modifications, and administrative overrides.
- **Backups:** Given that no backup automation currently exists, a simple cron-driven backup script (`pg_dump`/SQLite copy + compressed archive of user storage) must be implemented to push daily encrypted snapshots to a secondary off-site location or local secondary drive, respecting the zero-cloud-budget constraint.

---

## Scalability

Target scale: **200 initial users generating ~4,000 questions per day**. 

### Mathematical Reality Check
- 4,000 questions per day equals an average of **~2.8 requests per minute**. 
- Peak hours (e.g., business hours) might compress this into 10–15 requests per minute.
- **Conclusion:** This traffic load is trivial for a standard Python/FastAPI backend running on modest Proxmox hardware (Site A / Site B i5 nodes). The infrastructure bottleneck is **not compute**, but rather the missing document/legal-knowledge pipeline (Phase 17O), provider rate limits, and synchronous I/O blocks.

### Scalability Strategy for a Solo Operator (No Paid Cloud Budget)

1. **Queue System & Background Workers:** 
   - Avoid Redis/Celery bloat if simplicity is required, but implement a lightweight local job queue (e.g., using `arq` with Redis or an embedded SQLite-backed task queue) to handle asynchronous document uploads, OCR tasks, and heavy AI completions without blocking HTTP worker threads.
2. **Rate Limiting:**
   - Implement sliding-window rate limiting via FastAPI middleware (using `slowapi` or custom decorators) mapped to user UUIDs or Telegram `chat_id`s to prevent abuse and manage external AI provider rate limits (`core.ai.provider_health`).
3. **Database Optimization:**
   - Migrate from growing flat JSON files (`kai_chat_history.json`, `ai_usage_history.json`) to a local SQLite instance with proper indexing on `user_id`, `timestamp`, and `task_type`. This ensures O(1) or O(log N) lookups as chat histories grow.
4. **Load Management on Current Hardware:**
   - Utilize Uvicorn with multiple worker processes managed via Systemd on Site A.
   - Ensure real-time paths (chat interactions) bypass any heavy background processing (like document parsing or embedding generation), which must execute strictly asynchronously in background workers.

---

## Telegram and Web Chat Experience

To match a Claude-like conversational standard, both Telegram and Web interfaces must evolve past simple text-in, text-out request loops.

### Telegram Interface Design (Leveraging Native Bot API Features)
- **Typing Indicators:** Send persistent `sendChatAction(action="typing")` calls during long-running `ai_router.delegate()` calls to maintain user engagement.
- **Inline Keyboards:** Use native Telegram inline buttons for interactive workflows (e.g., confirming document upload scopes, selecting legal citation styles, or approving admin actions via `core.approval_watchdog`).
- **File and Photo Uploads:** Fully utilize Telegram’s document and photo handlers to accept user PDFs and images, feeding them directly into the processing pipeline.
- **Voice Messages:** Implement a lightweight transcription step (e.g., routing incoming `.ogg` voice notes through a low-cost transcription provider or local Whisper instance if compute permits) before passing text to `handle_kai_chat()`.

### Web Dashboard Experience
- **Streaming Responses:** Implement Server-Sent Events (SSE) or WebSockets on FastAPI endpoints to stream token output from `ai_router.delegate()` for instant visual feedback.
- **Rich Markdown & Citations:** Client-side rendering supporting sanitized Markdown, syntax-highlighted code blocks, and interactive footnote/citation chips linking directly to primary legal sources (anticipating Phase 17O).
- **File Viewer & Artifacts:** A dedicated split-pane interface allowing users to view uploaded PDFs alongside the active conversation thread.
- **Saved Conversations, Projects & Folders:** Requires the foundational conversation/memory storage layer (migrating away from flat JSON histories) to group chat threads by user-defined projects or legal matters.

---

## Future GPU Server Migration

*(Cross-referenced with the dedicated GPU infrastructure review completed on 2026-08-01).*

- **Headline Conclusion:** Neither Proxmox site (Site A: i5-9500 / Site B: i5-6500T) possesses a discrete GPU, and zero budget exists for paid cloud infrastructure. Therefore, any future GPU workloads must integrate directly via API as custom `ai_router` providers (utilizing external pay-as-you-go or spot GPU compute *only* when revenue supports it), avoiding any local Site-B-centric GPU Manager services. Furthermore, **real-time interactive paths must never route through the measured 210–220ms Site A <-> Site B link**.
- **What Moves Local Eventually (As Hardware/Budget Permits):** Heavy background tasks like document OCR, text chunking, local vector embeddings generation, and execution of small open-weight models (e.g., quantized local embedding or small reasoning models for offline tasks).
- **What Stays Cloud / API-Based:** Advanced reasoning, expert legal document synthesis, complex multi-step legal analysis, and high-tier model inference routed via `core.ai.ai_router` (Claude, DeepSeek, OpenAI, OpenRouter).

---

## Final Synthesis: Updated Architecture, Roadmap Changes, Migration Plan

## 1. Current Kai Audit

Kai’s foundational infrastructure is mature, stable, and production-ready for a solo-operator environment. The self-build and deployment pipelines, WireGuard-linked Proxmox Site A/Site B topology, active health monitors (`core.health`, `core.incident_manager`), Telegram watchdog (`core.approval_watchdog`), and multi-provider AI routing layer (`core.ai.ai_router`) are fully operational and robust. 

Conversely, layers involving document intelligence, optical character recognition, vector retrieval, the Ghana Legal Brain knowledge base, multi-agent orchestrations, and multi-tenant user management are pre-implementation roadmap intents rather than working software. Bridging this gap requires treating these missing layers as greenfield development targets built atop existing core routing and API primitives.

---

## 2. Existing Capabilities

* **Multi-Provider AI Routing**: `core.ai.ai_router.delegate()` handles multi-model routing across Claude, Gemini, OpenAI, Groq, OpenRouter, and DeepSeek, backed by real-time failure fallback and quota-exceeded provider health tracking (`core.ai.provider_health`).
* **Operational Telemetry & Cost Logging**: `memory/ai_usage_history.json` and `core.ai.ai_router.get_usage_history()` record every raw invocation (provider, task type, success, duration, cost).
* **Core API & Chat Endpoint**: `POST /kai/chat` (`core.api`) provides a working chat endpoint with session persistence in `kai_chat_history.json`.
* **Proxmox Infrastructure & Telephony**: Fully deployed Site A and Site B architecture linked via WireGuard, managed by automated systemd services and proxdash dashboards.
* **Basic Document Processing**: `core.law_documents` supports basic PDF text extraction via `pypdf` for the Kai Dashboard Library.
* **Self-Building Infrastructure**: Automated testing, isolated cloning, approval gating (`core.approval`), and execution auditing (`core.execution_audit`) operate continuously.

---

## 3. Missing Capabilities

In priority order (most foundational/blocking first):
1. **Unified Identity & User Management (Phase 15A)**: Local account database, user IDs, and permissions required for multi-tenant data isolation.
2. **Document OCR & Vision Pipeline**: Tesseract/PaddleOCR integration and vision-capable provider wrappers for scanned legal pages.
3. **Structured Vector Storage & Embeddings**: Local vector DB (Chroma) and embedding generation for semantic document search.
4. **Ghana Legal Brain Knowledge Base (Phase 17O)**: Primary-sources-only 7-category taxonomy and operator-approval staging queue.
5. **Short/Long-Term Memory Architecture**: Session envelope separation and explicit operator preference storage.
6. **Provider Control Plane & Scoring**: Aggregated usage metrics, pre-emptive health degradation switching, and dynamic quality scoring.
7. **Autonomous Multi-Agent Coordination**: General-purpose multi-agent worker frameworks (which are superseded by specialized prompt task-types in practice).

---

## 4. Improvements Required

* **Materialized Provider Health Control Plane**: Extend `core.ai.provider_health` to aggregate raw `ai_usage_history.json` logs into rolling P95 latency and success-rate profiles stored in `memory/provider_control_state.json`.
* **Rule-Based Complexity Pre-Classifier**: Add a zero-latency heuristic pre-classifier inside `delegate()` to inspect prompt length, structure, and legal patterns, routing ambiguous catch-all tasks to appropriate tiers without adding LLM classification overhead.
* **Pre-Emptive Health Switching**: Extend provider health monitoring to flag degraded providers (high latency or creeping error rates) and shift traffic pre-emptively *before* total quota exhaustion occurs.
* **Explicit Long-Term Memory & Compression**: Implement structured JSON session envelopes, context summarization with strict guardrails preserving exact legal citations, and an explicit write path for operator preferences (`memory/operator_long_term.json`).
* **Centralized Security & Encryption**: Migrate sensitive secrets out of plain-text configs into encrypted environment stores and generalize `core.execution_audit` to track security events.

---

## 5. Updated Architecture

```
[ Operator / External User ]
         │
         ▼
[ FastAPI / API Gateway ] ──(Auth & Rate Limits: Phase 15A)
         │
         ├─────────────────────────────────────────┐
         ▼                                         ▼
[ Conversation Engine ]                   [ Document Ingestion Pipeline ]
  - Structured Session Envelope             - PDF / OCR (Tesseract / pypdf)
  - Short/Long-Term Memory                  - AI-Assisted Classification
  - Summarization & Guardrails              - Operator Approval Staging
         │                                         │
         │                                         ▼
         └─────────────┐                 [ Chroma Vector Database ]
                       │                 (7-Category Taxonomy / Primary Sources)
                       ▼                           │
              [ AI Router Control Plane ] ─────────┘
                - Rule-Based Complexity Pre-Classifier
                - Dynamic Scoring & Pre-Emptive Switching
                - `core.ai.ai_router.delegate()`
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
[ Cloud AI Providers ]      [ Self-Build Pipeline ]
(Claude, Gemini, DeepSeek)  (core.approval / proxdash)
```

The system operates as a unified cognitive loop: User requests enter via FastAPI, pass through MVA authentication and rate-limiting, and bind to structured session contexts. Complex or legal queries pull semantic context from the Chroma Vector Database (governed by the 7-category taxonomy and operator approval gate). The refined prompt passes to the AI Router Control Plane, which evaluates complexity, sorts candidates via dynamic quality scoring, and invokes `delegate()`. Telemetry loops back into usage logs and provider health metrics, while self-build and incident monitors operate continuously in parallel.

---

## 6. Updated Roadmap

* **Phase 15A (Identity & Security)**: Accelerated to top priority. Must be implemented before any external multi-tenant user data ingestion or Juris Kai public deployment.
* **Phase 17O (Ghana Legal Brain)**: Expanded to include local Chroma vector storage, Tesseract OCR integration, and the operator-approval staging queue.
* **Phase 17P (Provider Intelligence)**: Formalized to include materialized provider health metrics, pre-emptive degradation switching, and dynamic quality-score sorting inside `delegate()`.

---

## 7. Implementation Priorities

### Phase 1: Critical Improvements
* Implement materialized health metrics aggregation in `core.ai.provider_health` and dynamic candidate scoring in `ai_router`.
* Build rule-based prompt complexity heuristics into `delegate()`.
* Restructure flat `kai_chat_history.json` into structured JSON session envelopes with context summarization and strict legal citation preservation guards.
* Implement Minimum Viable Auth (MVA / Phase 15A) with SQLite user persistence, secure password hashing, and per-user data isolation.

### Phase 2: Production Readiness
* Integrate Tesseract OCR and extend provider wrappers (`ai_provider.py`) to support multi-modal payloads (vision/scans).
* Deploy local Chroma vector database on Site B with the 7-category legal taxonomy schema.
* Build the operator-approval staging queue in the Kai Dashboard for legal document promotion.
* Implement sliding-window rate limiting, FastAPI background worker queues, and encrypted secrets management.

### Phase 3: Advanced AI Capabilities
* Implement explicit long-term operator preference storage (`memory/operator_long_term.json`).
* Enhance Telegram bot integration with persistent typing indicators, inline approval keyboards, and file/document handlers.
* Enable streaming responses (SSE/WebSockets) and split-pane file/artifact viewers on the web dashboard.
* Expand execution auditing to track authentication failures and data access attempts.

### Phase 4: GPU Infrastructure Migration
* *(Cross-referenced with standalone GPU review)*: Maintain zero local GPU dependency on Site A/Site B.
* Integrate future external GPU compute strictly as remote API endpoints via `ai_router` when budget allows.
* Offload heavy background batch embedding generation or local fallback quantization to external pay-as-you-go GPU instances, preserving the 210ms inter-site link integrity.

---

## 8. Technology Recommendations

* **OCR Library**: **Tesseract OCR** (pure C++ binary with Python `pytesseract` bindings) for lightweight, low-RAM text extraction on CPU-constrained Site B.
* **Vector Database**: **Chroma** (embedded mode, local SQLite backing) for zero external server process overhead and robust metadata filtering.
* **Embedding Approach**: **API-driven embeddings** (e.g., `text-embedding-3-small` via OpenRouter/OpenAI) to avoid local CPU compute bottlenecks during document ingestion batches.
* **Task Queue**: **Lightweight local job queue** (e.g., SQLite-backed task runner or `arq`) for asynchronous document processing without blocking FastAPI HTTP workers.
* **Authentication**: **Argon2id hashing with HTTP-only signed cookies / JWTs** for robust local user session management.

---

## 9. Cost Optimization Strategy

Juris Kai maintains a strict **zero-cloud-budget** constraint. 
* **Model Tiering**: High-volume, simple queries route exclusively to low-cost or free-tier providers (Groq, DeepSeek Flash, Gemini Flash) via dynamic router sorting. Premium reasoning models (Claude Opus, DeepSeek Pro) are restricted strictly to high-complexity legal analysis tasks flagged by the pre-classifier.
* **Infrastructure Economy**: Running entirely on local Proxmox hardware (Site A and Site B) with embedded Chroma SQLite storage eliminates all cloud infrastructure licensing and hosted database fees.
* **Embedding Efficiency**: Using inexpensive cloud embedding APIs (`text-embedding-3-small`) avoids burning local CPU resources and electricity on batch vectorization while costing fractions of a cent.

---

## 10. Migration Plan

1. **Step 1 (Immediate)**: Implement the materialized health metrics update loop and rule-based complexity pre-classifier in `core.ai.ai_router` and `provider_health.py`.
2. **Step 2**: Refactor chat session storage into structured JSON session envelopes with basic summarization guards.
3. **Step 3**: Deploy Phase 15A (Local SQLite MVA authentication and per-user isolation boundaries).
4. **Step 4**: Integrate Tesseract OCR and deploy the local Chroma vector database on Site B.
5. **Step 5**: Build the Kai Dashboard operator-approval staging queue for Phase 17O (Ghana Legal Brain ingestion).

---

Kai's foundational infrastructure—its multi-provider routing, self-building pipelines, and Proxmox node topology—is exceptionally strong and fully capable of supporting this larger "AI operating system" vision. Nothing foundational needs to be torn down; rather, the missing cognitive layers (MVA auth, vector retrieval, document OCR, and structured memory) must be systematically constructed and plugged directly into the existing `core.ai.ai_router.delegate()` foundation.
