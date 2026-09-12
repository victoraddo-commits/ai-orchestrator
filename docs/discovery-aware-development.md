# Discovery-Aware Development Workflow (OpenCode Phase 26S)

When modifying Kai, OpenCode MUST follow this workflow. Never code from
assumptions.

## Required steps (in order)

1. **Identify the affected subsystem** — determine which piece of the Kai
   ecosystem a change touches (orchestrator, network fabric, model fabric,
   FreeLLMAPI, gateway, VMs, storage, security, command center, mobile).
2. **Read authoritative architecture docs** — ARCHITECTURE.md,
   OPERATIONS.md, SECURITY.md, DISASTER_RECOVERY.md, CLAUDE.md, AGENTS.md,
   docs/kai-ai-gateway.md. Use the correct one for the subsystem.
3. **Inspect the actual current implementation** — read the real code/config
   (not memory); verify service state on the live box.
4. **Inspect dependencies** — requirements.txt / npm deps / docker-compose
   shown in the repo.
5. **Check existing skills** — kai-skills catalog + ~/.config/opencode/skills.
6. **Check existing agents** — ~/.config/opencode/agents.
7. **Check existing tests** — tests/ dir, `.venv/bin/python -m pytest` config.
8. **Check current service state** — systemctl, docker ps, protocol status.
9. **Propose implementation** — describe what will change, why, minimal diff.
10. **Implement minimally** — no scope creep, no invented architecture.
11. **Test** — run the relevant tests; show output.
12. **Security review** — use kai-security agent rules; can REJECT.
13. **Review own changes** — kai-reviewer discipline: verify claims.
14. **Fix failures** — iterate until claims are backed by evidence.
15. **Re-test** — run full test suite before declaring done.
16. **Report exactly what changed** — files, commands, tests run, outcomes.

## Guardrails

- Read before write, never modify Proxmox A/Claude Code on A/OmniRoute/
  Ollama/AirLLM/P40/VM104/112 or existing Kai services, DBs, creds.
- Preserve what works; build only what is missing; verify everything.
- Evidence over assertion: every completion claim must cite a run command
  and its output.
- If a subsystem is unclear, say so and inspect before proposing.