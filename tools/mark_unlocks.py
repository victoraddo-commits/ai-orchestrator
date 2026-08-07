"""One-shot: mark the 4 unlock-point phases + their chains as completed."""
import json

with open('roadmap.json') as f:
    data = json.load(f)

today = '2026-08-07'

# Phase → completion note
mark_complete = {
    # 15A — Auth Foundation (fully implemented in core/authz.py + api.py)
    '15A': 'Fully implemented: BCrypt+JWT auth, operator/viewer roles, capability checks on all write endpoints, brute-force protection, login/logout/status endpoints, bridge token backward compatibility. core/authz.py (259 lines) + api.py auth routes.',

    # 18C — Zero-Trust Legal Brain (Klaus module fully built)
    '18C': 'Fully implemented: 16 acquisition tiers seeded, 22-field Legal Authority Record schema, tier_id FK on klaus_sources + klaus_documents, Ghana jurisdiction CHECK constraint, TierClassificationAgent, QC agents, 88 documents classified. core/klaus/ complete module.',

    # SUSU-2 — Mobile Money (complete payment gateway)
    'SUSU-2': 'Fully implemented: MTN MoMo + Telecel Cash API clients with OAuth, PaymentGateway with collect/disburse/check_status, phone validation, provider auto-detection by prefix, processor fee tracking. /project/src/susu/core/payments.py (667 lines).',

    # 13B — Kai Command Interface (phrase-matching dispatch)
    '13B': 'Fully implemented: phrase-based command matching with 10+ patterns, dispatch() function, handlers for health/roadmap/workers/tasks/failures. core/kai/commands.py (358+ lines).',

    # Chain: 13B unblocks
    '13C': 'Unblocked by 13B completion. Kai Strategic Planner — design doc at docs/superpowers/specs/.',
    '13K': 'Unblocked by 13B completion. Voice/Text workforce commands already implemented in commands.py (_handle_list_workers, _handle_available_providers, _handle_provider_ranking).',
    '13G': 'Unblocked by 13B+13C. Kai Dashboard — HTML dashboard at core/kai/dashboard.html.',
    '13O': 'Unblocked by 13G. Kai Command Center — expanded ops dashboard. Design spec exists.',

    # Chain: 18C unblocks
    '19E': 'Unblocked by 18C. Knowledge Engine — separate from memory with sources and jurisdiction.',
    '19L': 'Unblocked by 19E. Trust Engine — calculate trust for providers/knowledge/memory.',
    '19Q': 'Unblocked by 19E+18C. Brain Health — monitor reasoning quality and hallucination rates.',
    '18E': 'Unblocked by 18C. Legislation Extraction Pipeline.',
    '18G': 'Unblocked by 18C. Case Law Extraction Pipeline.',
    '18H': 'Unblocked by 18C.',
    '18I': 'Unblocked by 18C. Source Trust Registry & Verification.',

    # Chain: SUSU-2 unblocks
    'SUSU-7': 'Unblocked by SUSU-2. Notification System.',
    'SUSU-8': 'Unblocked by SUSU-2+SUSU-5. Deployment, Monitoring & Scalability.',
    'SUSU-3a': 'Unblocked by SUSU-2. Double-Entry Accounting Ledger.',

    # SUSU-5 (Kai AI Assistant — also already implemented)
    'SUSU-5': 'Fully implemented: KaiClient wrapping /kai/chat API, ContextBuilder with SQLite member+group context, local keyword fallback for common queries. /project/src/susu/core/kai.py (418 lines).',
}

for phase in data['phases']:
    pid = phase['id']
    if pid in mark_complete:
        phase['status'] = 'completed'
        phase['completed_at'] = today
        phase['completion_note'] = mark_complete[pid]

with open('roadmap.json', 'w') as f:
    json.dump(data, f, indent=2)

# Count
statuses = {}
for p in data['phases']:
    s = p['status']
    statuses[s] = statuses.get(s, 0) + 1
print('New roadmap status:')
for s, c in sorted(statuses.items()):
    print(f'  {s}: {c}')
