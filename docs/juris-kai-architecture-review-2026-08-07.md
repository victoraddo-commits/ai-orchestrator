# Juris Kai Architecture Review & Recommendations

**Date**: 2026-08-07
**Reviewer**: Kai AI Orchestrator

---

## Current Architecture

```
┌──────────────────────────────────────────────────────┐
│                   Juris Kai Module                    │
│  core/juris_kai/                                      │
│  ├── bot.py          (Telegram polling + menu routing) │
│  ├── menus.py        (Reply/inline keyboards)          │
│  ├── accounts.py     (SQLite multi-tenant accounts)    │
│  ├── commands.py     (Business logic handlers)          │
│  ├── prompt.py       (AI prompt templates)              │
│  ├── payments.py     (Hubtel Mobile Money)              │
│  ├── dashboard.py    (Admin API endpoints)              │
│  └── session.py      (User session + history)           │
├── core/ai/ai_router.py  (6 juris_* task types)         │
├── core/ai/secrets.py    (API key storage — shared)      │
└── memory/                (Shared runtime state dir)      │
    ├── juris_kai_accounts.db                             │
    └── juris_kai_sessions.json                           │
```

---

## Current State Assessment

### What's Shared (with Kai core)

| Resource | Shared? | Risk | Recommendation |
|----------|---------|------|-----------------|
| **Database** | Own SQLite DB (`juris_kai_accounts.db`) | ✅ Low | Already isolated — no action needed |
| **Vector DB namespace** | ❌ Not integrated yet | — | See recommendation below |
| **API keys** | Via `core/ai/secrets.py` (read-only) | ✅ Low | Provider keys shared; appropriate |
| **AI routing** | Via `core/ai/ai_router.py` | ✅ Low | Own task types; provider pool shared |
| **Memory directory** | Same `memory/` dir as Kai core | 🟡 Medium | Consider `memory/juris_kai/` subdir |
| **Logging** | Same Python logging | ✅ Low | Logger prefix "juris_kai" ensures separation |
| **Deployment** | Same Python venv + systemd | 🟡 Medium | Separate service; could be separate venv |

### What's Fully Isolated

- **Operational capabilities**: Zero cross-imports (verified by tests)
- **Build system**: Cannot import `core.build_manager`
- **Approval system**: Cannot import `core.approval`
- **Deployment**: Cannot import `core.deployment_manager`
- **User accounts**: Own SQLite DB with separate schema
- **Admin gating**: `JURIS_KAI_ADMIN_IDS` whitelist

---

## Recommendations

### 1. Separate Database — NOT RECOMMENDED right now

Juris Kai already uses its own SQLite DB (`memory/juris_kai_accounts.db`). The `memory/` directory is shared with Kai core, but this is acceptable since:
- Table names are namespaced (`juris_accounts`, `juris_payments`, etc.)
- WAL mode prevents lock conflicts
- SQLite is per-connection, not per-process

**Recommendation**: Keep current setup. If scaling to 100+ users, consider `memory/juris_kai/` subfolder for clean separation.

### 2. Vector Database Namespace — NEEDS INTEGRATION

Current state: Juris Kai does NOT use the legal knowledge base (`core/legal_brain/`) or vector search. This is the biggest gap.

**Recommendation**: Integrate as read-only query interface:
- Use `core/legal/search.py` for legal document search
- Add a "Search Knowledge Base" menu option
- Results annotated with source and confidence level
- NEVER write back — read-only boundary preserved

### 3. Separate API Keys — NOT RECOMMENDED

Both Kai core and Juris Kai use the same AI providers (qwen4_text, claude, gemini, etc.). Duplicating provider keys would be wasteful and harder to maintain.

**Recommendation**: Keep sharing via `core/ai/secrets.py`. The secrets module already has audit logging and permission control.

### 4. Separate AI Routing — ALREADY DONE

Juris Kai has its own task types (`juris_legal_teaching`, `juris_case_analysis`, etc.) with separate provider chains from Kai core. This is already correct.

**Recommendation**: No changes needed.

### 5. Separate Monitoring — PARTIALLY DONE

Juris Kai has its own dashboard stats (`get_dashboard_stats()`), usage logs, and security logs. But it lacks:
- Telegram-specific health metrics (response time, error rate)
- Cost per user tracking
- Uptime monitoring

**Recommendation**: Add basic health endpoint for monitoring. Already partially in place via `/v1/providers` in the gateway.

### 6. Separate Deployment Lifecycle — MOSTLY DONE

Juris Kai now has its own systemd service (`ai-orchestrator-juris-kai.service`) separate from the main orchestrator. This is correct.

**Recommendation**: Consider separate `requirements.txt` or venv if dependencies diverge. Not needed now.

---

## Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Token exhaustion from shared provider pool | 🟡 Medium | Rate limits + tier limits already in place |
| DB corruption from shared `memory/` dir | 🟢 Low | WAL mode + namespaced tables |
| User document leak to KB | 🟢 Low | Session-only design enforced |
| Admin bypass via Telegram ID spoofing | 🟢 Low | Telegram IDs are immutable per account |
| AI prompt injection via user text | 🟡 Medium | Prompts are sandboxed in `prompt.py` with prefix patterns |

---

## Summary

The current architecture is **sound for production**. The key areas to address in future iterations:

1. **Integrate legal knowledge base** (highest value-add)
2. **Add response quality monitoring**
3. **Consider `memory/juris_kai/` subfolder** for cleaner separation
4. **Document analysis pipeline** (currently a stub — needs actual file parsing)
