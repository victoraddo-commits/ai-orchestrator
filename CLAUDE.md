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
| `core/ai_provider.py` | Provider registry (register_provider) |
| `core/ai/provider_health.py` | Quota, health tracking |
| `core/api.py` | FastAPI HTTP endpoint |
| `core/build_manager.py` | Application builder lifecycle |
| `core/memory.py` | Memory save/load API |
| `core/lifecycle.py` | State machine primitives |
| `core/kai/commands.py` | Command interface |
| `core/kai/planner.py` | Strategic planning |
| `core/llm_clients.py` | Raw API clients for each AI provider |
| `config/providers.yaml` | Provider config (mainly docker/proxmox tools) |
| `memory/` | Runtime state (json files — gitignored) |

## Provider routing (as of 2026-08-03)

### Coding providers (agentic tool-use, build/self-modifying work)

```
CODING_ROTATING_FRONT: ["opencode_claude"]  (Fable 5, sole front — credits healthy)
Fixed tail: opencode_claude_sonnet → opencode_claude_opus → qwen3_coding → omniroute → claude → opencode → opencode_minimax
```

- `opencode_claude` — Fable 5 via OpenCode Zen (separate billing, healthy). Primary.
- `qwen3_coding` — Qwen2.5-Coder-32B on RunPod A100 SXM 80GB (tool-calling confirmed live). Primary in CODING_ROTATING_FRONT.
- `claude` — Direct CloudCLI/Anthropic subscription. Out of credit currently, in tail.
- `omniroute` — Self-hosted aggregator gateway on localhost:20128. Always-on fallback.
- `openrouter_claude_opus/sonnet` — DROPPED (OpenRouter account out of credit).

### Text-task providers (chat/completion, no tool use)

The Provider rotation varies by task_type. Key roles:

| Role | Primary | Fallback chain |
|------|---------|----------------|
| planning | gemini | geminix → deepseek_native_flash → opencode_claude → deepseek_native_pro → deepseek → claude |
| architecture | deepseek_native_flash | gemini → geminix → deepseek → openai → claude |
| review | openai* | deepseek_native_flash → deepseek → gemini → geminix → claude |
| classification | groq | deepseek_native_flash → gemini → geminix → deepseek → claude |
| documentation | deepseek_native_flash | groq → deepseek → claude |
| log_analysis | groq | claude |

*Note: The "openai" slot currently points to the self-hosted Qwen3-Coder RunPod (hijacked in `llm_clients.call_openai`). Phase 17Z is registering this properly as its own provider name.

### Provider status

| Provider | Type | Status | Billing |
|----------|------|--------|---------|
| opencode_claude (Fable 5) | coding | ✅ healthy | OpenCode Zen (separate) |
| qwen3_coding (RunPod) | coding | ✅ fallback | $0.99/hr GPU |
| qwen3/openai (RunPod text) | text | ✅ working but needs proper reg | same GPU |
| omniroute | both | ✅ fallback | self-hosted |
| gemini | text | ✅ healthy (credit reloaded) | Google billing |
| geminix (2nd account) | text | ✅ fallback | Google billing |
| groq | text | ✅ healthy | free tier |
| deepseek_native_flash | text | ✅ healthy | native api.deepseek.com |
| deepseek_native_pro | text | ✅ healthy | native api.deepseek.com |
| deepseek (OpenRouter) | text | ✅ healthy | OpenRouter |
| claude (direct) | both | ⚠️ out of credit | Anthropic subscription |
| openrouter_claude_* | coding | ❌ OpenRouter out of credit | OpenRouter |
| minimax | text | ⚠️ excluded (0/4 verified) | |
| opencode_minimax | coding | ⚠️ in tail only | OpenCode Zen |
| local | both | ❌ placeholder | N/A |

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
| **17Z** | Qwen3-Coder RunPod Provider | 45 | none | Proper text provider registration (not the "openai" hijack) |

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
4. **Prioritize in-progress phases** by priority score (lower = more urgent, except 15A at 50 which blocks other work)
5. **17Z is the current active work** — Qwen3-Coder RunPod text provider registration
6. **Run tests** before any code change: `.venv/bin/python -m pytest`
7. **After deploy**: watch one full scheduler cycle (`journalctl -u ai-orchestrator -f`)

### Current active task: 17Z — Qwen3-Coder RunPod text provider

**Context:**
- RunPod pod `c5ib0n2adowifp` running vLLM on A100 SXM 80GB
- Model: `Qwen/Qwen2.5-Coder-32B-Instruct-AWQ` (AWQ quantized)
- Endpoint: `https://c5ib0n2adowifp-8000.proxy.runpod.net/v1` (OpenAI-compatible)
- Auth: API key in `.env` as `VLLM_QWEN3_CODER_API_KEY`
- Confirmed live: 2026-08-02 with real chat completion
- $0.99/hr always-on billing, root filesystem is ephemeral

**What's already done:**
- `qwen3_coding` registered as coding agent (tool-use loop via opencode CLI)
- `call_openai()` in `llm_clients.py` is hijacked to point to this RunPod
- Env vars `VLLM_QWEN3_CODER_BASE_URL`, `VLLM_QWEN3_CODER_MODEL`, `VLLM_QWEN3_CODER_API_KEY` all set

**What 17Z needs:**
1. Proper `call_qwen3_coder_text()` function in `llm_clients.py` (or reuse existing endpoint with own name)
2. Register as `qwen3_coder_text` via `core.ai_provider.register_provider()` (run_text_task only)
3. Add to `ROLE_PROVIDERS` in `ai_router.py` as fallback capacity (after primary, before claude universal fallback)
4. `cost_tier='paid'` (real GPU billing, unlike free-tier providers)
5. `available_fn` checking VLLM_QWEN3_CODER_API_KEY + endpoint responding
6. Tests covering the new provider registration and routing

### Other in-progress work

- **15A** (priority 50): Auth foundation — blocks 15B-H, 15F. Two roles (operator/viewer), local accounts, capability checks on write endpoints.
- **17V** (priority 33): Conversation memory restructuring — session envelopes, long-term operator store, guarded compression.
- **13K/13O**: Command/dashboard UI work in the plugin.
- **16D/17M**: Cost alerts and free-tier expansion — lower urgency.
- **17N**: Design proposal only — no implementation yet.
