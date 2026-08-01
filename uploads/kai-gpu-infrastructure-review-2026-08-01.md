# Kai Infrastructure Architecture Review: GPU Integration Study

**Prepared 2026-08-01. Critical review, not an endorsement -- ground truth verified live against both Proxmox hosts before any analysis.**

## Editorial Note on the RAM Figure (read this first)

This review was generated in 4 sequential parts. Mid-way through, the operator confirmed Site B's RAM had genuinely been upgraded (verified live via SSH: 15GiB, was 8GB). Because of the timing:

- **Parts 1-2** (Executive Summary through Cost Analysis) were written **before** the update and cite Site B's RAM as **8GB**.
- **Part 3** (Capacity/Scalability/Risks) was written **during** the update sequence and correctly notes the upgrade as **planned within a month**, not yet confirmed.
- **Part 4** (Improvements, Roadmap, Final Recommendation) was written **after** live verification and correctly uses **15GiB (already confirmed)**.

None of this changes the core conclusions -- no discrete GPU exists on either site regardless of RAM, the 210-220ms site latency is unaffected by RAM, and the recommended corrected architecture (direct Site-A-to-cloud-GPU path, bypassing Site B for real-time compute) holds either way. The RAM upgrade meaningfully **eases** the "Site B resource starvation" concern raised in Parts 1-3, but does not reverse the headline recommendation. Read RAM-specific numbers in Parts 1-3 as historical/superseded by Part 4's 15GiB figure.

One more correction made after generation: the report's proposed new roadmap phases were originally labeled 17M/17N/17Q, which collide with real, different, already-existing phases (17M = free-tier AI provider expansion, 17N = voice/phone capability, 17Q = Kai Operations Appliance). Relabeled to 17R/17S/17T below, the next genuinely free IDs.

## Table of Contents

1. Executive Summary
2. Compatibility with Current Roadmap
3. Hardware Compatibility Analysis
4. Architecture Review
5. GPU Integration Plan
6. GPU Manager Design
7. Cost Analysis
8. Capacity Planning
9. Scalability Analysis
10. Risks
11. Suggested Improvements
12. Recommended Roadmap Changes
13. Recommended New Development Phases
14. Final Recommendation

---

## 1. Executive Summary

The proposal to run a GPU-backed legal AI architecture by placing a GPU Manager on Site B and relying on Site B for vector databases, embedding pipelines, and RAG operations **does not hold up against the verified physical and network facts of the 2026-08-01 deployment.** 

The single biggest problem with the proposal as written is a severe mismatch between topological assumptions and physical reality: it treats Site B as a robust local server environment when it is actually an 8GB RAM low-power desktop (Intel i5-6500T) separated from Site A by a harsh **210–220ms WAN latency tunnel**. Pushing compute, vector workloads, and cloud GPU orchestration onto Site B introduces catastrophic latency loops, resource starvation (given Site B’s strict 8GB memory ceiling), and unnecessary architectural fragility.

**Headline Recommendation:** **Reject the proposed Site-B-centric GPU architecture.** Do not colocate vector databases or cloud GPU orchestration on Site B. Instead, keep core orchestration and user touchpoints on Site A, keep heavy vector/embedding indexing entirely off-premise or tied directly to cloud API providers, and design any future cloud GPU integration (e.g., RunPod/Vast.ai) as an independent, direct-to-cloud service invoked straight from Site A—bypassing Site B entirely unless Site B is strictly acting as a thin data staging proxy. Furthermore, because no vector database, embedding pipeline, or OCR tooling currently exists and Roadmap Phase 17O is entirely unbuilt, **no infrastructure hardware decisions should be finalized until the specific embedding models and database footprints of Phase 17O are explicitly defined.**

---

## 2. Compatibility with Current Roadmap

The proposal does not directly conflict with any currently implemented software, but it **violates the logical sequencing of the existing unbuilt roadmap**.

- **Phase Alignment:** Roadmap Phase 17O (*Ghana Legal Brain: authoritative legal knowledge base*) and Phase 17P (*Juris Kai: multi-tenant paid legal-assistant Telegram bot*) are strictly pending and have zero application code written today. The `core.law_documents` module on Site A is merely a simple file storage and text extraction utility, not a RAG pipeline. 
- **The Sequencing Trap:** The proposal attempts to design an infrastructure deployment (cloud GPUs + GPU Manager service on Site B) before the software architecture of Phase 17O has even chosen its core mechanics (e.g., embedding model dimensions, vector store footprint, chunking strategies). 
- **Implications:** This is premature infrastructure optimization. The requirements for a vector database and embedding pipeline dictate the GPU and RAM needs—not the other way around. Phase 17O must define its data ingestion, storage, and retrieval parameters *first*. If Phase 17O utilizes lightweight or API-driven embedding strategies (or matches the existing philosophy of `core.ai.ai_router.delegate()`), a dedicated cloud GPU cluster might be completely unnecessary, rendering a "GPU Manager" service dead weight.

---

## 3. Hardware Compatibility Analysis

A direct confrontation between the proposal and the verified live hardware/network facts exposes critical bottlenecks:

### Site-to-Site Latency Bottleneck (~210–220ms WAN)
The WireGuard link between Site A and Site B is operating at high WAN latency (210–220ms round trip). The proposed request path—`User -> Kai Core (Site A) -> Kai Legal Brain (Site B) -> GPU Manager (Site B) -> Cloud GPU -> Site B -> Site A`—creates a multi-hop ping-pong effect. A single user interaction would traverse the 210ms tunnel *at least twice* just for internal routing before even factoring in the external cloud GPU inference time. This guarantees sluggish, unacceptable response times for interactive legal queries.

### Site B Resource Starvation (8GB Hard Ceiling)
Site B runs an Intel i5-6500T with **only 8GB of total RAM**. Currently, it hosts only `kai-legal-brain` (2GB allocated, empty). The proposed stack on Site B—PostgreSQL, a vector database, an embedding database, a legal document repository, indexing services, and a GPU Manager abstraction layer—will easily exhaust 8GB of RAM under even minimal concurrent load. Swapping will cause severe performance degradation, and an out-of-memory (OOM) kernel panic is practically guaranteed if vector indexing or bulk OCR runs concurrently.

### Site A Overcommitment
Site A is running on a 6-core/6-thread i5-9500 with **24.9GB RAM**. Current allocations are already heavy: `claude-code` takes 16.9GB and OPNsense takes 9.7GB. Total allocation (26.6GB) technically exceeds physical RAM, relying on swap (1.9GB of 8GB swap in use). While Site A has more headroom than Site B, it is already heavily burdened by the operator's local development environment and nested Docker containers (`it-manager`, `proxdash`, `airdrop-hunter`, etc.). It cannot absorb additional heavy local AI microservices.

### Single Point of Failure & Zero GPU Reality
Neither site possesses a discrete GPU; both rely entirely on integrated graphics (Intel UHD 630 on Site A, Intel HD 530 on Site B) with zero CUDA capability. Therefore, *any* meaningful GPU compute must happen in the cloud (e.g., RunPod). Forcing Site B to act as the intermediary gatekeeper for cloud compute introduces an unnecessary Single Point of Failure (SPOF) on an under-resourced low-power desktop node.

---

## 4. Architecture Review

1. **Does this design fit the existing roadmap?**
   No. It leaps ahead of Phase 17O (Ghana Legal Brain) by assuming infrastructure requirements (cloud GPUs and local GPU management services) before the knowledge-base architecture, vector database selection, or embedding models have even been designed or implemented.

2. **Does it conflict with any existing planned phases?**
   It doesn't formally conflict with text-based roadmap text, but it subverts the logical progression of Phase 17O and 17P by imposing a heavy, complex hardware-management layer before the core application logic exists.

3. **Will it introduce unnecessary complexity?**
   Extremely high complexity. Building a custom "GPU Manager" service on Site B to abstract cloud GPUs, coupled with a cross-site container deployment, introduces maintenance overhead, credential management, and state synchronization across a slow WAN link for no justifiable gain.

4. **Does it simplify future development?**
   No. It decouples components across a high-latency, resource-constrained boundary, making debugging, logging, state management, and local testing significantly harder.

5. **Are there better architectural patterns?**
   Yes. Given the **210–220ms Site A <-> Site B latency**, routing traffic through Site B as a mandatory hop is structurally flawed. If cloud GPUs are required, the orchestration layer (Kai Core or a lightweight API client) should communicate *directly* with the cloud provider (or a direct cloud-hosted orchestrator) from Site A, or handle legal document storage on cloud object stores rather than forcing Site B to act as an un-resourced on-premise data center. The GPU Manager should never live on an 8GB remote node; it should either be a direct cloud API integration or reside alongside the primary router on Site A.

6. **Are there components already implemented that duplicate these ideas?**
   **None.** As verified, this is strictly greenfield territory. There are no vector databases, OCR pipelines, embedding engines, or GPU wrappers currently in the codebase. `core.ai.ai_router.delegate()` handles text multi-provider routing (Gemini, Claude, Groq, DeepSeek), but it does not touch GPUs or vector storage.

7. **Should any responsibilities remain on Site A instead of moving to Site B?**
   Yes. Core orchestration (`ai_router`), user-facing APIs, and primary application logic should remain anchored on Site A (or standard cloud infrastructure), because Site B’s 8GB RAM limit and 210ms WAN latency make it utterly unsuited for high-throughput coordination or rapid state queries.

8. **Are there better boundaries between Kai Core and Kai Legal Brain?**
   Yes. Kai Legal Brain should be treated as a stateless domain logic package or microservice interface, not a physical hardware bucket tied to Site B. Tying a logical product domain ("Legal Brain") to a specific weak physical machine ("Site B hardware") creates an artificial hardware bottleneck.

9. **Should GPU orchestration become part of Kai Core instead of a separate Site B service?**
   If cloud GPUs are managed at all, their abstraction should extend naturally from the existing `core.ai.ai_router` pattern rather than spawning an entirely separate microservice on a remote, resource-starved node. However, given the operator's strict **no-budget / no-paid-cloud-infrastructure** standing constraint, introducing metered cloud GPU orchestration (like RunPod) contradicts the financial reality of the project.

10. **Is there a more scalable design?**
    Yes: **API-first, serverless, or zero-GPU reliance.** Given that the project operates under a strict zero-budget constraint for paid cloud infrastructure, the scalable path forward for Phase 17O is to leverage existing free-tier or low-cost API providers for embeddings and text generation (leveraging or extending `core.ai.ai_router.delegate()`), utilize lightweight vector solutions that fit within modest RAM footprints without heavy GPU acceleration, and avoid dedicated rented cloud GPU infrastructure entirely until revenue or concrete funding justifies the burn.

---

## 5. GPU Integration Plan

A GPU integration plan for this architecture cannot be evaluated using conventional cloud assumptions. We must design strictly around the verified constraints: **Site A has zero GPU**, **Site B has an i5-6500T with 8GB RAM**, the **Site-to-Site WireGuard link has a 210–220ms round-trip latency**, and the operator is a **solo developer with a strict "no paid cloud budget" constraint**.

### The Fatal Flaw in the Proposed Request Path
The hypothetical request path—*User (Telegram/Web) -> Site A (claude-code) -> Site B (WireGuard 210ms) -> GPU Manager -> Cloud GPU (RunPod/Vast) -> back to Site B (210ms) -> back to Site A -> User*—is structurally flawed and introduces unmitigated latency bottlenecks:
1. **Double-Hopping the WAN:** Forcing data to cross the 210ms WireGuard tunnel twice per inference cycle adds nearly **half a second of pure network latency** (420ms–440ms) *before computation even begins*.
2. **Site B Resource Exhaustion:** Site B has only **8GB of total RAM** and runs an LXC with 2 cores and 2GB RAM. It cannot act as a heavy processing proxy or buffer large data payloads coming back from a cloud GPU without risking OOM crashes.

### The Corrected Path
Because Site A houses the active application entrypoints (`ai-orchestrator`, `claude-code`, API gateways) and Site B is hardware-constrained and isolated by a 210ms WAN link, **Site A must directly orchestrate cloud GPU instances**, bypassing Site B entirely for heavy compute calls.

* **Corrected Data Flow:** `User -> Site A (ai-orchestrator / core.ai) -> Cloud GPU API (RunPod/Vast.ai) -> Direct payload return to Site A`.
* **Role of Site B:** Site B remains strictly reserved for its local domain (the future *Kai Legal Brain* knowledge base, PostgreSQL, vector storage, and local RAG when running fully offline/small-model fallback). It should not touch external cloud GPU orchestration traffic.

---

## 6. GPU Manager Design

Building a "GPU Manager" service for a solo operator must ruthlessly separate *v1 necessities* from *enterprise-fantasy over-engineering*. 

### Where Should It Run?
Given the corrected path and Site A's superior capacity (24.9GB RAM, 100GB+ storage, primary application hub), the **GPU Manager service must run on Site A** as a lightweight FastAPI microservice or a module integrated directly into `core.ai`. Running it on Site B would compound the 210ms latency penalty and choke Site B's 8GB RAM limit.

### Component-by-Component v1 Implementation Breakdown

| Responsibility | v1 Implementation for Solo Operator | Status for v1 |
| :--- | :--- | :--- |
| **Provider Abstraction** | A simple Python class wrapping the RunPod GraphQL/REST API. No abstract factory patterns or multi-provider drivers needed yet. | **Must-Have** |
| **GPU-Needed Decision** | Static rule-based tagging in `core.ai.ai_router.delegate()`: if task == `heavy_embedding_generation` or `local_llm_finetuning`, route to GPU Manager. Standard chat/RAG uses existing APIs (Gemini, Claude CLI, Groq, DeepSeek). | **Must-Have** |
| **Instance Lifecycle** | API calls to provision a pod via RunPod template, poll until `STATUS == RUNNING`, execute workload, and trigger termination. | **Must-Have** |
| **Job Queue / Priority** | In-memory Python `asyncio.Queue` on Site A. Since there is a solo operator, multi-tenant priority queues are completely unnecessary. | **Must-Have (Simplified)** |
| **Storage Mounting** | Use cloud-native network volumes (e.g., RunPod Network Volumes) or pull models directly from S3/Hugging Face on container startup via an initialization script. Do not sync over the 210ms WireGuard link. | **Must-Have** |
| **Model Deployment** | Use pre-baked Docker images (e.g., vLLM or Ollama pre-configured containers) rather than building images on the fly. | **Must-Have** |
| **Health Monitoring** | Simple HTTP ping loop from Site A to the cloud GPU instance's exposed port. | **Must-Have** |
| **Retry Failures** | Standard Python `try/except` with exponential backoff (max 3 retries) for API provisioning failures. | **Must-Have** |
| **Idle Auto-Termination** | A background `asyncio` task checking last-active timestamp; if idle for >10 minutes, fire API call to terminate pod. Prevents runaway billing. | **Critical Safety** |
| **Usage Stats & Cost Estimation** | Log start time, stop time, and GPU hourly rate to a local SQLite table on Site A. Display a running estimated cost counter. *(Note: treated strictly as hypothetical modeling/estimates).* | **Must-Have** |
| **Multi-Provider Failover** | **OVER-ENGINEERING.** Do not build Vast.ai + RunPod failover for v1. Pick one provider (RunPod) and stick to it until stability demands otherwise. | **Deferred / Skip** |
| **Model / Embedding Caching** | Rely on container layer caching and fast Hugging Face downloads on cloud nodes rather than building a custom caching layer on Site A/B. | **Deferred / Skip** |

---

## 7. Cost Analysis: RunPod vs. Vast.ai

*Note on Cost/Usage Projections: All financial figures discussed here are **estimates and hypothetical modeling exercises** based on known platform pricing structures, subject to change. Given the operator's strict "no paid cloud budget" constraint, any cloud GPU usage represents a deliberate policy exception.*

### Comparative Evaluation

| Dimension | RunPod | Vast.ai |
| :--- | :--- | :--- |
| **Stability & Reliability** | **High.** Enterprise-grade cloud data centers; dedicated pods rarely experience sudden host evictions. | **Variable.** P2P marketplace model; hosts can reclaim hardware or experience abrupt reboots. |
| **API Quality & Docs** | **Clean & Consistent.** Well-documented GraphQL and REST APIs, highly automatable for a solo dev. | **Fragmented.** Functional CLI and API, but documentation and UX vary widely by host reliability. |
| **Persistent Storage** | **Robust.** First-class Network Volume support attached directly to data centers. | **Host-dependent.** Storage persistence relies on the specific host machine's disk reliability. |
| **Automation-Friendliness** | **Excellent.** Designed for programmatic spin-up/spin-down workflows via API. | **Moderate.** Requires handling bid-market nuances, interruptions, and variable host setups. |

### Pricing Model Differences
* **RunPod:** Operates on a **fixed hourly rate** model tied to hardware tiers (e.g., specific rates for RTX 3090/4090 or A10G instances). Pricing is predictable, consistent, and transparent, making cost forecasting straightforward.
* **Vast.ai:** Operates on a **spot/auction marketplace** model where individual host operators set prices. It is frequently cheaper on paper, but prone to availability friction, sudden termination by host owners, and variable network performance.

### Which Fits the Solo Operator Constraints Better?
Given the strict **no paid cloud budget** constraint and the realities of a solo operator maintaining the system:
1. **RunPod wins for v1 automation and peace of mind.** Even if its baseline hourly rate is marginally higher than the absolute cheapest Vast.ai spot listing, a solo developer cannot afford the debugging overhead of a P2P host abruptly pulling an instance mid-job or corrupting a vector indexing run.
2. **When would the answer flip?** The choice would flip to Vast.ai *only* if the project transitions into a high-volume, batch-processing regime (e.g., continuous overnight embedding of millions of legal pages) where cost minimization outweighs interruption risk, and the GPU Manager is refactored to gracefully handle spot-instance evictions with robust checkpointing. 

*Actionable Recommendation for v1:* Do not commit capital or provision standing cloud instances. If heavy compute (like Phase 17O embedding generation) becomes mandatory and cannot be handled by free API tiers or local CPU processing on Site A, implement RunPod strictly on an **on-demand, spin-up/compute/terminate** lifecycle with a hard-coded 10-minute idle auto-termination safeguard. Verify live rates directly on RunPod's dashboard before running any billable job.

---

## 8. Capacity Planning

*Disclaimer: The following growth and usage numbers are **hypothetical modeling exercises** requested by the operator to stress-test architecture boundaries. As verified live, Kai IT Manager is currently a small, single-business production app with low traffic, and Kai Legal Brain (roadmap phase 17O) has zero users and zero application code built as of August 2026. Treat these figures strictly as capacity-planning projections.*

---

### Scenario A: Kai IT Manager at ~200 hypothetical active users

#### Workload Estimation & Resource Profile
* **Active User Base:** ~200 concurrent or semi-concurrent users (typical of a mid-sized payroll/HR deployment across a few business units).
* **Average AI Requests / Hour:** Assuming each active user triggers light AI assistance (e.g., policy checks, timecard anomaly detection, or text-completion prompts) roughly 2 times per hour = **~400 requests/hour** (~0.11 requests/sec).
* **GPU Utilization:** **0%**. 
  * *Reality Check:* Kai IT Manager is a conventional CRUD payroll and timecard management app. Its existing AI features (such as `AIInsightsCard`) are strictly text-only and route directly through `core.ai.ai_router.delegate()`, leveraging external providers (Gemini, Claude, Groq, DeepSeek). 
  * It has **zero** dependency on local or cloud GPU compute today, and at 200 users, its text summarization and anomaly detection loads remain trivial text-completion tasks. 
  * **Verdict:** Forcing IT Manager onto a GPU would be entirely redundant. The app does not need GPU acceleration at this scale; it will continue to scale comfortably on Site A’s existing LXC allocation using external text APIs.

---

### Scenario B: Kai Legal Brain at ~200 hypothetical active users

Unlike IT Manager, a legal RAG and knowledge retrieval system (phase 17O/17P) introduces heavy text-embedding, OCR, and inference overhead.

#### Workload Estimation (Hypothetical 200 Active Users)
* **Real-Time Legal Searches & RAG Queries / Hour:** 
  * Assuming 200 users each generating an average of 3 queries per hour during peak shifts = **~600 real-time queries/hour** (~0.17 requests/sec).
* **Document Analysis & OCR Frequency:** 
  * Ad-hoc document uploads and OCR parsing: ~20–30 dense legal PDFs per hour during active research windows.
* **Embedding Generation Frequency:** 
  * Incremental chunking and vector embedding generation for new uploads: ~200–500 chunks/hour.
* **Batch Indexing / Background Jobs:** 
  * Periodic bulk re-indexing of the 7-category taxonomy: Scheduled nightly or weekly, processing thousands of chunks in a single run.

#### Real-Time vs. Background Workload Separation
* **Real-Time (User-Facing / Latency-Sensitive):** 
  * Chatbot interactions (Juris Kai Telegram/Web interface) and fast RAG similarity searches. 
  * *The Site A -> Site B -> Cloud GPU Latency Trap:* Because Site A and Site B are bridged via WireGuard with a measured **~210-220ms WAN round-trip latency**, any real-time request path that flows from Site A (OPNsense/App) -> Site B (Local DB/Broker) -> Cloud GPU -> Site B -> Site A will **compound the latency penalty twice** on every hop. 
  * Coupled with cloud GPU cold-starts (which can add 5–30 seconds), running real-time inference on a distant cloud GPU will ruin user experience. Real-time inference *must* rely on fast API providers (via `ai_router.delegate()`) or be cached aggressively.
* **Background Jobs (Batch Indexing, OCR, Bulk Embedding):** 
  * These tasks are **completely latency-tolerant**. A batch embedding job or a heavy OCR pass that takes 45 seconds instead of 5 seconds has zero impact on user experience. 
  * Therefore, if cloud GPU acceleration or heavy batch processing is utilized, it should be strictly quarantined to **asynchronous background workers** managed on Site B, completely bypassing the real-time chat path.

---

## 9. Scalability Analysis

### Growth Trajectory: 500, 1000, and 5000 Users

| Metric | 500 Users | 1000 Users | 5000 Users |
| :--- | :--- | :--- | :--- |
| **Primary Bottleneck** | Site B RAM (8GB limit) | Site B 4-Core CPU Saturation | WireGuard WAN Throughput / External API Rate Limits |
| **Database Load** | PostgreSQL + Vector indexing starts hitting memory limits | Concurrent connection pooling required; read replicas needed | Full dedicated database cluster required off-site |
| **GPU Requirement** | Still optional; external APIs or scheduled batch embedding suffice | Optional for inference; recommended for local batch embedding models | Mandatory self-hosted or dedicated inference tier |

#### 1. When does a second GPU become necessary?
A second GPU is not a hardware scaling requirement until concurrent generation requests exceed single-GPU throughput limits (typically >10–20 concurrent heavy LLM generation streams). For Juris Kai, this threshold will not be crossed until well past **1,000 to 2,000 active legal professionals** actively querying local models simultaneously—a scale that requires a complete infrastructure overhaul far beyond current single-node Proxmox limits.

#### 2. Breakeven Analysis: Cloud Rental vs. Self-Hosting (Reasoning Framework)
* *Note: Exact pricing fluctuates; the following is structural economic reasoning.*
* **Cloud GPU Rental (e.g., Vast.ai / RunPod):** Typically costs $0.20 to $1.50/hour depending on the card (e.g., RTX 3060/4090). 
  * *On-Demand Cost:* If run 24/7, a $0.40/hr instance costs ~$290/month. If run *only* for scheduled batch jobs (e.g., 2 hours/day), it drops to ~$24/month.
* **Self-Hosted Hardware:** Purchasing a dedicated desktop GPU + upgrading power supplies and cooling.
* **The Crossover Point:** Because the operator operates under a strict **zero-budget / no-paid-cloud-infrastructure constraint**, recurring cloud GPU rental is a non-starter. Self-hosting via rented cloud infrastructure breaks the core financial model. Therefore, the economic breakeven is absolute: **Zero cloud spend means all compute must either fit within free API tiers, local CPU/iGPU execution, or existing hardware.**

#### 3. Should Site B eventually get a permanent local GPU?
* **Physical Constraints:** As verified live, Site B runs on an **Intel Core i5-6500T** housed in a low-power, small-form-factor (SFF) chassis with an **8GB total RAM constraint** (slated for 16–32GB RAM upgrades within a month). 
* **The Verdict:** **No.** An i5-6500T SFF chassis typically lacks the physical PCIe slot power delivery (often limited to 35W–75W low-profile slots), physical clearance, and robust Power Supply Unit (PSU) required to house a real discrete desktop GPU (like an NVIDIA RTX series). Upgrading Site B to support a GPU would require replacing the entire chassis, motherboard, and PSU—defeating the purpose of utilizing existing low-power hardware. 
* Instead, Site B's upcoming RAM upgrades (16GB -> 32GB) should be dedicated entirely to expanding PostgreSQL, vector indexing caches, and local container memory, leaving heavy AI inference to external APIs or asynchronous batch pipelines.

---

## 10. Risks & Mitigations

| Risk | Impact | Severity | Mitigation Status |
| :--- | :--- | :--- | :--- |
| **1. WAN Latency Compounding**<br>Site A <-> Site B 210ms round-trip latency combined with cloud GPU hops. | Sluggish real-time chat UX; timeout errors on webhook bridges. | **High** | *Open / Unmitigated.* Must enforce that real-time user queries bypass Site B routing for heavy compute, utilizing direct API delegation (`ai_router.delegate()`) from Site A. |
| **2. Cloud GPU Provider Outages & Pricing Volatility**<br>Reliance on spot instances or third-party renters breaking budget or availability. | Sudden downtime for AI-backed features; unexpected cost overruns. | **Critical** (violates zero-budget constraint) | *Mitigated by Design:* The existing `ai_router.delegate()` architecture already features automatic fallback across multiple providers and quota tracking. No metered cloud GPU should ever be a single point of failure. |
| **3. Site B Hardware Limits (8GB RAM / i5-6500T)**<br>Exhausting memory during heavy vector database indexing or RAG queries. | OOM (Out of Memory) kernel panics crashing the `kai-legal-brain` container. | **High** | *Partially Mitigated:* Operator-confirmed RAM upgrade path to 16GB–32GB within a month relieves the immediate pressure, but CPU/iGPU limits remain rigid. |
| **4. Data Residency & Legal Document Security**<br>Sensitive Ghana legal documents leaving Site B storage to reach third-party cloud GPUs. | Regulatory breach or privacy compromise of proprietary legal knowledge base. | **High** | *Open.* Phase 17O mandates a primary-sources-only policy; any cloud-based embedding or OCR processing must use zero-retention API agreements or be restricted to local CPU execution. |
| **5. Orphaned Idle Instance Costs**<br>Cloud GPU instances left running indefinitely after batch jobs complete. | Rapid depletion of operating funds via idle billing. | **Critical** | *Mitigated:* Any cloud GPU usage must be strictly governed by automated startup/shutdown scripts tied directly to background job completion queues. |

---

## 11. Suggested Improvements

Grounded directly in the verified live facts of the Proxmox nodes (Site A i5-9500 with integrated UHD 630; Site B i5-6500T with 15GiB RAM and integrated HD 530; 210–220ms WireGuard WAN latency), the existing Python codebase (`core.ai.ai_router`), and the zero-budget constraint, several architectural improvements are essential:

*   **Integration with `core.ai.ai_router` (Unified Provider Routing):** GPU-backed inference should not be implemented as a parallel, isolated subsystem. Instead, any self-hosted or cloud GPU inference endpoint (e.g., vLLM on a rented instance or local CPU fallbacks) must be wrapped as just another provider plugin inside `core.ai.ai_router.delegate()`. This allows the existing cost-tier tagging, quota tracking, automatic failover, and usage logging to govern GPU calls seamlessly.
*   **Mitigating 210ms WAN Latency via Edge Caching on Site A:** Because Site A hosts the active applications (`claude-code`, `it-manager`) and sits behind OPNsense, routing every retrieval-augmented generation (RAG) query round-trip through Site B adds a mandatory 420ms networking penalty (Site A $\rightarrow$ Site B $\rightarrow$ Site A) before external API calls even begin. To mitigate this, Site A's local storage pools (`local-lvm`) must cache frequent document embeddings and semantic search index stubs locally. Site B should act as the authoritative storage and embedding generation node, while Site A handles downstream user-facing presentation and caches hot RAG context.
*   **Security & Data Sovereignty for Legal Documents:** Phase 17O ("Ghana Legal Brain") handles confidential legal source material. Transmitting raw legal documents across external networks to cloud GPUs (if used) or even over the unoptimized Site A-to-Site B WireGuard tunnel requires strict controls. PII scrubbing and anonymization must occur *before* text leaves Site A, and any external GPU inference provider must adhere to zero-retention policies. Given the solo operator's strict zero-budget constraint, external rented GPUs are financially unviable anyway; all heavy vector search and indexing must execute locally on Site B's 15GiB RAM host.
*   **Embedding Caching & Batch Ingestion:** Generating embeddings is computationally expensive. The ingestion pipeline defined in Phase 17O must implement content-hash-based caching (e.g., SHA-256 of the source PDF chunk) stored in PostgreSQL on Site B. If a document chunk has not changed, its embedding must never be recalculated, protecting the CPU cycles of Site B's 4-core i5-6500T processor.
*   **Backup & Disaster Recovery for Vector State:** Unlike stateless microservices, a vector database and embedding index represent hours of curated legal taxonomy work. The backup strategy must integrate directly with Proxmox's built-in snapshot and backup capabilities, scheduling daily container-level backups of LXC 100 on Site B to local storage, with an off-site archive step to Site A over the WireGuard tunnel during off-peak hours.

---

## 12. Recommended Roadmap Changes

The existing roadmap requires targeted adjustments to account for hardware realities, network latency, and the absence of local GPU capabilities:

*   **Phase 17O (Ghana Legal Brain):** 
    *   *Modify:* Explicitly define the vector database and embedding model to run on CPU-optimized configurations (e.g., `pgvector` inside PostgreSQL on Site B) rather than assuming GPU acceleration, given Site B's lack of a discrete GPU and the zero-budget constraint.
    *   *Add Requirement:* Integrate the ingestion pipeline with content-hash caching to protect Site B's 4-core i5-6500T CPU during bulk document processing.
*   **Phase 17P (Juris Kai Telegram Bot):** 
    *   *Modify:* Require all retrieval queries originating from Site A to leverage local embedding caches where possible, reducing the impact of the 210–220ms Site-A-to-Site-B inter-site latency on conversational response times.

---

## 13. Recommended New Development Phases

To bridge the gap between current infrastructure and the planned legal brain products, the following new roadmap phases are required in strict dependency order:

*   **Phase 17R (Infrastructure & Network Optimization Layer):** 
    *   *Description:* Implements diagnostic and tuning measures for the Site-A-to-Site-B WireGuard tunnel to minimize the 210–220ms latency overhead. Establishes cross-site health monitoring, automated Proxmox container backup routines for Site B's `kai-legal-brain` LXC, and resource allocation adjustments (resizing LXC 100 on Site B to utilize its newly upgraded 15GiB host RAM).
    *   *Dependencies:* None (Prerequisite for all multi-site legal brain components).
*   **Phase 17S (Core Authentication & Capability Foundation):** 
    *   *Description:* Implements platform-wide local account management, role/capability-based permissions, and secure inter-service token authentication. Replaces fragmented service-level auth (such as isolated Telegram allowlists and API bridge tokens) with a unified access control layer shared across Site A and Site B applications.
    *   *Dependencies:* Phase 17R.
*   **Phase 17T (Vector Storage & Hybrid RAG Engine):** 
    *   *Description:* Provisions and configures `pgvector` inside PostgreSQL on Site B's `kai-legal-brain` LXC, builds the content-hash-backed embedding pipeline, and integrates embedding generation directly into `core.ai.ai_router` as a standard local execution target.
    *   *Dependencies:* Phase 17S, Phase 17R.

---

## 14. Final Recommendation

The original proposal's implicit assumption of cloud GPU utilization and unconstrained cross-site pipelines is fundamentally misaligned with the verified physical reality: a solo operator with **zero cloud budget**, a **210–220ms WAN latency** barrier between Site A and Site B, and **no discrete GPU hardware** on either node. Proposing metered cloud GPUs or complex multi-node orchestration introduces unsustainable operational overhead and cost risks.

**The Superior Design:** A lean, strictly local-first architecture where Site B's upgraded 15GiB RAM host runs PostgreSQL with `pgvector` entirely on CPU, backed by strict content-hash caching to protect the 4-core i5-6500T. All AI routing—whether local CPU-driven embeddings or external zero-cost/low-cost API calls—flows uniformly through the existing `core.ai.ai_router.delegate()` framework. Cross-site traffic is minimized by caching hot RAG artifacts on Site A.

### Single Next Action
**Execute Phase 17R:** Log into Site B, resize the `kai-legal-brain` LXC container's RAM allocation from 2GB to take advantage of the newly verified 15GiB host memory, and run a diagnostic check on the WireGuard tunnel to document baseline packet loss and routing efficiency.
