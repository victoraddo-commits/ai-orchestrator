# Kai — OpenCode Project Reference (AGENTS.md)

OpenCode-readable knowledge reference for the Kai AI infrastructure platform.
This is an **index + snapshot** that references authoritative docs; it is not a
competing source of truth. Per the OpenCode directive (Phase 15 / 26P): extend
and reference, never duplicate.

## Authoritative docs (read these before changing anything)

| Doc | Location | Covers |
|---|---|---|
| ARCHITECTURE.md | `ARCHITECTURE.md` (repo root) | Live pipeline, 5 lifecycle objects, memory layer, risk, rollback |
| OPERATIONS.md | `OPERATIONS.md` | Day-to-day ops, approvals, deploy, troubleshooting |
| SECURITY.md | `SECURITY.md` | Action allowlist, dangerous-command blocking, audit, threat model |
| DISASTER_RECOVERY.md | `DISASTER_RECOVERY.md` | Memory corruption, rollback, crash behavior |
| CLAUDE.md | `CLAUDE.md` | Kai identity, providers, roadmap state, memory files, ops quick-ref |
| AI Gateway md | `docs/kai-ai-gateway.md` | 18A-ai gateway: /v1/*, key mgmt (59 tests green) |

## What Kai is

Autonomous infrastructure-ops + application-builder platform. Single
`ai-orchestrator` systemd service polling every 300s:
`scheduler → orchestrator_cycle → state scan → health → incidents → decisions
→ approvals (human-gated) → remediation → verification → rollback → learning`.
Autonomy level 5 (auto roadmap), but **every action requires human approval**
today (`AUTONOMOUS_MODE` is `False`). Only wired real action: `docker restart`.

## System topology (verified 2026-09-12)

- **Master repo (code/roadmap)**: `claude-code` host `/project/ai-orchestrator`.
- **Runner (executes)**: LXC 111 `kai-orchestrator` `/opt/ai-orchestrator`.
- **This box**: LXC 113 `kai-opencode` on 192.168.1.110; runs OpenCode +
  FreeLLMAPI (docker `:3001`).
- **Proxmox A** (pve, 192.168.99.2, Tailscale 100.116.165.100) — hosts LXC 100–113,
  VMs; reached via claude-code ssh tunnel `:8008`. Do NOT modify.
- **Proxmox B** (192.168.1.109) — offline/no ARP; direct Tailscale
  100.83.4.27. Do NOT touch DD-WRT-side config.
- **VM 104** `kai-gpu-benchmark` (192.168.1.241, Tesla P40) — ollama,
  `kai_coder` = Qwen2.5-Coder-7B. **VM 112** `kai-cpu` (192.168.1.242,
  koboldcpp `:5001`/`:5002`) — CPU coding pool.
- **Ports**: 8006 PVE API; `:8007`→orchestrator host 8006; `:8008`→Proxmox A;
  `:3001` FreeLLMAPI; `:20128` OmniRoute; `:8140` kai-command gateway;
  `:11434–11436` Ollama. ZeroTier 10.250.0.2/24; Tailscale `tail82a9ca.ts.net`.

## Where things live (key paths — see CLAUDE.md for the full table)

- Roadmap (source of truth for status): `roadmap.json` at repo root.
- Runtime memory: `memory/*.json` (gitignored, schema v1, atomic writes, `.bak`).
- Providers/keys: `config/providers.yaml`, `core/ai/secrets.py`
  (`provider_secrets.json`, encrypted 0600, audit-logged).
- AI routing: `core/ai/ai_router.py` `ROLE_PROVIDERS`; circuit breaker
  `core/ai/circuit_breaker.py` (threshold 3, cooldown 300s).
- App builder: `core/build_manager.py`. Plugin: `/project/src/ai-orchestrator-plugin`.
- FreeLLMAPI (OpenCode dev gateway, NOT a Kai provider): LXC 113
  `/root/freellmapi`, unified key in `settings.unified_api_key`.

## Providers (current chain — source: CLAUDE.md + live checks)

`coding`: kai_coder → koboldcpp_cpu_a → koboldcpp_cpu_b → local.
RunPod pods **retired 2026-09-12**. Cloud (omniroute, gemini, groq,
deepseek_native, gpuai_minimax) remain as fallback only; `claude (direct)` out
of credit. Always consult `ROLE_PROVIDERS` in code + `provider_quota.json` —
do not assume the table above is current.

## Critical paths & boundaries

- Incident → decision → approval → remediation → verification →
  `remtrace`-stamped lifecycle objects (see ARCHITECTURE.md).
- **Security boundary**: every action gated in `core/security.py`; no
  autonomous exec; secrets never logged. See SECURITY.md.
- **Do-not-modify list** (OpenCode directive): Proxmox A, Claude Code on A,
  OmniRoute, Kai Vault (LXC 107), Ollama/AirLLM/P40, VM 104/112, existing
  Kai services + DBs + creds.

## Working with the repo

- Tests: `.venv/bin/python -m pytest` (run before deploy; must be green).
- Deploy runner: `systemctl restart ai-orchestrator` (LXC 111) after tests.
- Approvals: `python -m core.approval_cli list` / `approve <id> --yes`.
- Editing roadmap.json: back it up first (`cp roadmap.json ...bak-<ts>`),
  set `status`/`updated_at`/`completed_at`, re-read to verify. Never invent.
- Phases: 26-series = OpenCode directive (26H–26Z). Current work: 26O skills
  done, 26P this file, 26K models audit complete (blocker: FreeLLMAPI's
  OpenRouter key 401).

## Discovery-aware dev workflow (mandatory)

Before any change to Kai: identify subsystem → read authoritative doc →
inspect current impl → inspect deps → check skills/agents → check service
state → propose → implement minimally → test → security-review → re-test →
report. Full 16-step workflow: `docs/discovery-aware-development.md`.
Never code from assumptions; never create competing sources of truth.