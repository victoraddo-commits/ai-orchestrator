# Kai — AI Orchestrator

Kai is an autonomous infrastructure operations + application builder platform that observes, reasons, remediates, and learns. It runs on a single systemd service polling every 300s, with human-approval gates on every action.

## Identity

- **Name**: Kai (Knowledgeable AI Infrastructure)
- **Created**: 2026-07-26 (Phase 13A)
- **Autonomy level**: 5 (auto roadmap — highest)
- **Mission**: Operate the homelab infrastructure autonomously while keeping human operators as final authority on every action, and execute the roadmap until all phases are complete.

## Architecture (condensed)

Entry point: `core/scheduler.py` → `core/orchestrator_cycle.py::run_cycle()`

```
scheduler (300s loop) → state scan → health analyze → incidents → decisions → approvals (human-gated) → remediation → verification → rollback (if needed) → learning
```

Five lifecycle objects all share a common schema from `core/lifecycle.py::new_object()`: Incident, Decision, Approval, Remediation, Verification — each with `trace_id` chaining back to root cause.

**Memory layer**: `core/memory.py` — atomic writes (temp file + `os.replace`), `.bak` backups, schema versioning, production/test isolation.

**AI provider system**: `core/ai/ai_router.py` — role-based delegation with fallback chains, quota/health tracking, rotating front groups for coding.

**API**: `core/api.py` (FastAPI, port 8000) — read-only observability, plus build/approval/chat endpoints.

**Plugin**: `/project/src/ai-orchestrator-plugin/` — CloudCLI TypeScript plugin (9 tabs) + standalone dashboard (`/dashboard`).

## Key files

| File | Purpose |
|------|---------|
| `roadmap.json` | Machine-readable phase tracking — the source of truth for what's done/next |
| `core/scheduler.py` | Main loop entry (systemd) |
| `core/ai/ai_router.py` | Provider routing with fallback chains |
| `core/ai/secrets.py` | Secure provider API key storage (never exposed) |
| `core/ai_provider.py` | Provider registry (register_provider) |
| `core/ai/agent_registry.py` | Agent registry (model + GPU + cost + benchmarks per provider) |
| `core/ai/provider_health.py` | Quota, health tracking |
| `core/ai/circuit_breaker.py` | Per-provider circuit breaker (threshold=3, cooldown=300s, half-open probe) |
| `core/api.py` | FastAPI HTTP endpoint |
| `core/build_manager.py` | Application builder lifecycle |
| `core/memory.py` | Memory save/load API |
| `core/lifecycle.py` | State machine primitives |
| `core/kai/commands.py` | Command interface |
| `core/kai/planner.py` | Strategic planning |
| `core/llm_clients.py` | Raw API clients for each AI provider |
| `config/providers.yaml` | Provider config (mainly docker/proxmox tools) |
| `memory/` | Runtime state (json files — gitignored) |

## Provider routing (as of 2026-09-12)

**RunPod GPU pods retired 2026-09-12.** All coding + text-task roles now
route to local providers on our own hardware. Cloud providers remain as
fallback only. See `core/ai/ai_router.py::ROLE_PROVIDERS` for the live
chain per role.

### Coding providers (agentic tool-use, build/self-modifying work)

```
ROLE_PROVIDERS["coding"] = ["kai_coder", "koboldcpp_cpu_a",
                            "koboldcpp_cpu_b", "local"]
ROLE_PROVIDERS["coding_cpu_pool"] = ["koboldcpp_cpu_a", "koboldcpp_cpu_b"]
```

- `kai_coder` — Qwen2.5-Coder-7B-Instruct on VM 104 (kai-gpu-benchmark),
  served by ollama. Primary coder.
- `koboldcpp_cpu_a` — Qwen2.5-Coder-7B-Instruct-Q4_K_M on VM 112
  (kai-cpu, 192.168.1.242:5001), koboldcpp, 7 threads / 14G RAM.
- `koboldcpp_cpu_b` — same model, port 5002, second instance for parallel
  CPU inference.
- `koboldcpp_cpu` — alias → koboldcpp_cpu_a (backwards compat).
- `local` — always-on fallback (ollama on this host).
- All RunPod providers (`qwen4_coding`, `qwen4_text`, `qwen4_pod_b`,
  `qwen4Z`) — REMOVED 2026-09-12.
- `omniroute*`, `gpuai_minimax`, `gemini*`, `groq`, `deepseek_*`,
  `claude`, `openai` — remain registered as cloud fallback, subject to
  circuit breaker and quota.

### Text-task providers (chat/completion, no tool use)

All text roles now head with `kai_coder` (or `kai_brain` where a heavier
model is warranted). Consult `core/ai/ai_router.py::ROLE_PROVIDERS` for
the current chain per role; the previous per-role table listed RunPod
pods that no longer exist.

### Provider status

| Provider | Type | Status | Billing |
|----------|------|--------|---------|
| kai_coder (VM 104 ollama) | both | ✅ primary | free (local GPU) |
| koboldcpp_cpu_a (VM 112:5001) | coding | ✅ CPU fallback | free (local CPU) |
| koboldcpp_cpu_b (VM 112:5002) | coding | ✅ CPU fallback | free (local CPU) |
| local (this host ollama) | both | ✅ always-on fallback | free |
| gpuai_minimax (GPU.ai) | both | ✅ cloud fallback | GPU.ai serverless (paid) |
| omniroute | both | ✅ cloud fallback | self-hosted |
| omniroute_deepseek_flash | text | ✅ cloud fallback | self-hosted |
| omniroute_sonnet | coding | ✅ legal module | self-hosted |
| gemini | text | ✅ cloud fallback | Google billing |
| geminix (2nd account) | text | ✅ cloud fallback | Google billing |
| groq | text | ✅ cloud fallback | free tier |
| deepseek_native_flash | text | ✅ cloud fallback | native api.deepseek.com |
| deepseek_native_pro | text | ✅ cloud fallback | native api.deepseek.com |
| claude (direct) | both | ⚠️ out of credit | Anthropic subscription |
| RunPod pods (qwen4_*) | — | ❌ RETIRED 2026-09-12 | — |

## Roadmap state

### Completed (49 phases)
1-11 Foundation, 12A-12L (Application Builder pipeline), 13A-13H (Kai identity through autonomy), K1-K4 (Command interface + router fixes), 13J (Workforce dashboard), 13M-13Z (OpenRouter fallback through Telegram bridge), 14B (Dual-repo rollback safety), 16A (Standalone dashboard), 17A-17D (Parallel builds + dashboard), 17H (Plugin rebuild), 17J (Chat-triggered builds), 17K (Telegram chat parity)

### In Progress (9 phases)

| ID | Name | Priority | Dependencies | Key criteria |
|----|------|----------|-------------|---------------|
| **13K** | Workforce Voice/Text Commands | 29 | 13B, 13X | New command patterns dispatch to existing dashboard functions |
| **13O** | Kai Command Center | 25 | 13G | Expanded ops dashboard with 10+ panels, no duplicate business logic |
| **15A** | Platform Auth Foundation | 50 | 13H | Local accounts + capability-based permissions (operator/viewer) |
| **15D** | Real-time Update Infrastructure | 53 | 13G | SSE push for dashboard state changes |
| **16D** | Cost Budget Limits | 28 | 13W, 13Z | Configurable budget ceiling + Telegram alert |
| **17M** | Free-tier Provider Expansion | 30 | none | Add free providers as fallback, never displace top-priority |
| **17N** | Voice/Phone AI (MiniMax) | 60 | none | Design proposal only — no code yet |
| **17V** | Kai Conversation Memory | 33 | 13X | Session envelopes, long-term operator store, guarded compression |
| **17Z** | Qwen4 RunPod Provider | 45 | none | Proper text provider registration with qwen4_text/qwen4_pod_b |**
| ~~**18A-ai**~~ | ~~AI Gateway~~ | ~~40~~ | ~~none~~ | ✅ COMPLETE 2026-09-03 — `/v1/chat/completions`, `/v1/models`, `/v1/providers`, `/v1/usage`, key management, 59 tests green |

### Failed (10 phases)
13I (Future roadmap generator), 13L (Performance-weighted routing), 14A (Stuck-phase detection), 15G (AI Workforce Center), 17E (Multi-node Proxmox), 17G (UI/UX polish), 17I (App portfolio awareness), 17R (AI routing resilience), 17S (OpenCode Zen dedicated keys), 17U (Provider config editor), 17W (Telegram native UX), 17X (Automated resiliency)

### Pending/Proposed
15B-C, 15E-H, 16B-C, 17F, 17L, 17O-Q, 17T, 17Y, 18A

## Memory system

All runtime state lives in `memory/` (gitignored). Each file is `{"schema_version": 1, "records": [...]}` with atomic writes and `.bak` backups. Key files:

| File | Contents |
|------|----------|
| `builds.json` | All build records (28MB+) |
| `approval_queue.json` | Pending/approved/rejected actions |
| `incidents.json` | Infrastructure incidents |
| `decisions.json` | Decision engine output |
| `provider_quota.json` | Provider health/rate-limit tracking |
| `ai_usage_history.json` | Every ai_router delegate() call |
| `learning_lessons.json` | Action classification (trusted/observe/avoid) |
| `kai_chat_history.json` | 40-message rolling chat history |
| `system_state.json` | Host/Docker/Proxmox snapshot |
| `autonomy_level.json` | Current autonomy level (0-5) |
| `improvement_proposals.json` | Kai's strategic proposals |
| `remediation.json` | Remediation records |
| `verification_history.json` | Verification outcomes |
| `api_keys.json` | AI Gateway consumer keys (hashed) |
| `agents.json` | AI Agent registry (model, GPU, cost, benchmark, fallback data) |
| `gateway_audit.json` | AI Gateway request audit trail |
| `provider_secrets.json` | Provider API keys (encrypted, 0600 perms, never exposed) |
| `secret_access_audit.json` | Secrets access audit log |
| `provider_state.json` | Per-provider enable/disable toggles |

## Operations quick reference

```bash
# Service
systemctl status ai-orchestrator
journalctl -u ai-orchestrator -f

# Approvals
python -m core.approval_cli list
python -m core.approval_cli approve <id> --yes

# Tests (always run before deploy)
.venv/bin/python -m pytest

# Deploy
systemctl restart ai-orchestrator  # after tests pass!

# API (run manually)
.venv/bin/uvicorn core.api:app --host 127.0.0.1 --port 8000
```

## How to resume work

When a new AI provider picks up Kai's work:

1. **Read this file** first — it's the provider-agnostic entry point
2. **Check `roadmap.json`** for current phase status
3. **Check `memory/builds.json`** for any WAITING_FOR_USER_INPUT or stuck builds
4. **Prioritize in-progress phases** by priority score (lower = more urgent)
5. **Run tests** before any code change: `.venv/bin/python -m pytest`
6. **After deploy**: watch one full scheduler cycle (`journalctl -u ai-orchestrator -f`)

### Current active task: P23 Consolidation

**Context:**
- 7 duplicates identified: `talent.db` ×2, `vault_master_key` ×2, NetBird ×2, kai-notify ×2
- Formal consolidation not yet performed
- See `/project/uploads/kai-disaster-recovery-2026-09-03.md` for full service inventory

**AI Gateway (18A-ai) — COMPLETE as of 2026-09-03:**
- `POST /v1/chat/completions` — OpenAI-compatible chat
- `POST /v1/chat/completions/stream` — SSE streaming
- `GET /v1/models` — list available models
- `GET /v1/providers` — provider health/status
- `GET /v1/usage` — consumer usage tracking
- Key management: create/revoke/list API keys
- 59 tests green, all endpoints live at https://localhost:8000

**Agent Registry routes:** (all write-gated)

**AI Gateway routes:**
- `POST /v1/chat/completions` — OpenAI-compatible chat
- `POST /v1/chat/completions/stream` — SSE streaming (simulated)
- `GET /v1/models` — list available models
- `GET /v1/providers` — provider health/status

**Agent Registry routes:**
- `GET /kai/agents` — list all agents (optional `?status=active|disabled` filter)
- `GET /kai/agents/{id}` — get single agent
- `POST /kai/agents` — register/update agent (write-gated)
- `POST /kai/agents/{id}/enable` — enable agent (write-gated)
- `POST /kai/agents/{id}/disable` — disable agent (write-gated)
- `POST /kai/agents/{id}/test` — quick health test against provider
- `GET /kai/agents/{id}/stats` — aggregate success rate, avg latency, total cost
- `GET /kai/agents/{id}/costs` — recent cost history entries
- `GET /kai/agents/{id}/performance` — recent performance data points
- `POST /kai/agents/{id}/benchmarks` — record benchmark results (write-gated)
- `POST /kai/agents/bootstrap` — seed registry from existing providers (write-gated)

**Circuit Breaker routes:**
- `GET /kai/circuit-breakers` — list all breaker states with cooldown remaining
- `GET /kai/circuit-breakers/{provider}` — get single breaker snapshot
- `POST /kai/circuit-breakers/{provider}/reset` — reset a breaker (write-gated)
- `POST /kai/circuit-breakers/reset-all` — clear all breakers (write-gated)
- `POST /kai/circuit-breakers/{provider}/trip` — force-trip a breaker (write-gated)
- `PUT /kai/circuit-breakers/{provider}/config` — set threshold + cooldown (write-gated)

### Other in-progress work

- **15A** (priority 50): Auth foundation — blocks 15B-H, 15F. Two roles (operator/viewer), local accounts, capability checks on write endpoints.
- **17V** (priority 33): Conversation memory restructuring — session envelopes, long-term operator store, guarded compression.
- **13K/13O**: Command/dashboard UI work in the plugin.
- **16D/17M**: Cost alerts and free-tier expansion — lower urgency.
- **17N**: Design proposal only — no implementation yet.
