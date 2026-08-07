"""Honest roadmap cleanup: only mark actually-implemented phases complete."""
import json

with open('roadmap.json') as f:
    data = json.load(f)

today = '2026-08-07'

# ── TRULY COMPLETED (code exists and works) ────────────────────────────────
completed = {
    # Earlier session
    'AI-1': 'Provider consolidation + qwen3->qwen4 rename. 3 dead providers removed. Commit 1ddd2fb.',
    '13Y': 'Fixed 2026-07-29 (commit 1d4f8d8). _plan_needs_clarification resolved.',
    '13S': 'Fixed 2026-07-29 (commit 4c69637). Tool-call markup detection.',
    'IT-2': '@anthropic-ai/sdk upgraded to 0.115.0 in commit cf4b034.',
    '16D': 'Budget monitor implemented in core/monitoring/budget_monitor.py, runs in orchestrator_cycle.',
    # This session
    'AI-4': 'core/ai_serverless/ with handler.py + vercel_handler.py. OpenAI-compatible serverless wrappers.',
    'AI-5': 'core/ai/cost_tracker.py with cost summary/provider detail/monthly/daily endpoints.',
    'AI-7': 'core/ai/credential_vault.py with AES-256-GCM encryption, 90-day rotation, 7 API endpoints.',
    # 4 unlock points (verified code exists and works)
    '15A': 'BCrypt+JWT auth, operator/viewer roles, capability checks, brute-force protection, login/logout/status. core/authz.py + api.py.',
    '18C': '16 acquisition tiers seeded, 22-field Legal Authority Record, tier_id FK, Ghana jurisdiction CHECK, TierClassificationAgent, 88 docs. core/klaus/ complete.',
    'SUSU-2': 'MTN MoMo + Telecel Cash API clients, PaymentGateway collect/disburse/check_status, phone validation. /project/src/susu/core/payments.py (667 lines).',
    '13B': 'Phrase-based command matching, 10+ patterns, dispatch() with health/roadmap/worker/task handlers. core/kai/commands.py (358+ lines).',
    # SUSU-5: Kai AI assistant (verified code exists)
    'SUSU-5': 'KaiClient + ContextBuilder with SQLite, local fallback for balance/payout/group queries. /project/src/susu/core/kai.py (418 lines).',
    # 13K: already implemented in commands.py
    '13K': 'Voice/Text workforce commands in commands.py (_handle_list_workers, _handle_provider_ranking, _handle_available_providers).',
}

# ── OBSOLETE / CANCELLED ───────────────────────────────────────────────────
cancelled = {
    '17S': 'Obsolete: OpenCode Zen dedicated keys. Claude Fable 5 already works. RunPod decommissioned.',
    '17U': 'Obsolete: Provider config editor. Landscape simplified after RunPod decommissioning.',
    '17Z': 'Obsolete: Qwen3-Coder RunPod provider. Both RunPod pods decommissioned.',
    '17X': 'Obsolete: Automated Resiliency for 13E. Superseded by provider migration.',
    '13L': 'Obsolete: Provider Performance-Weighted Routing. Simplified provider landscape.',
    '13L-1': 'Obsolete: Duplicate of 13L.',
    '13U': 'Obsolete: Register DeepSeek. deepseek_native_flash/pro already primary.',
    'AI-6': 'Obsolete: RunPod deployment templates. RunPod pods decommissioned.',
    '17Q': 'Deferred indefinitely per user directive 2026-07-31.',
}

# ── DEFERRED (R&D stretch goals) ───────────────────────────────────────────
deferred = {
    '19R': 'Deferred: Application Registry. Visionary R&D.',
    '19S': 'Deferred: Repository Registry. R&D.',
    '19U': 'Deferred: Dependency Graph. R&D.',
    '19V': 'Deferred: Software Factory. R&D.',
    '19W': 'Deferred: Templates. R&D.',
    '19X': 'Deferred: Lifecycle Management. R&D.',
    '19H': 'Deferred: Reflection Cortex. R&D.',
    '19Y': 'Deferred: AI Workforce Integration. R&D.',
    '19I': 'Deferred: Learning Cortex. R&D.',
    '19Z': 'Deferred: Command Center Extensions. R&D.',
    '19AA': 'Deferred: Security Model. R&D.',
    '19K': 'Deferred: Simulation Engine. R&D.',
    '19BB': 'Deferred: Integration. R&D.',
    '19M': 'Deferred: Self Improvement. R&D.',
    '19O': 'Deferred: Command Center. R&D.',
    '19P': 'Deferred: Self Explanation. R&D.',
    'JK-4': 'Deferred: Juris Kai Knowledge Management. Stretch goal.',
    'JK-5': 'Deferred: Juris Kai Analytics. Stretch goal.',
    'JK-6': 'Deferred: Juris Kai Monetization. Stretch goal.',
    '14A': 'Deferred: Stuck-phase detection. R&D.',
    '17G': 'Deferred: UI/UX polish. Stretch goal.',
    '17I': 'Deferred: App portfolio awareness. R&D.',
}

# ── UNBLOCKED (moved from failed to pending — blocker resolved) ────────────
unblocked = {
    'SUSU-3a': 'SUSU-2 completed — ready to build.',
    'SUSU-7': 'SUSU-2 completed — ready to build.',
    'SUSU-8': 'SUSU-2+SUSU-5 completed — ready to build.',
    '19E': '18C completed — ready to build.',
    '19L': '19E unblocked — ready to build.',
    '19Q': '18C+19E unblocked — ready to build.',
    '18E': '18C completed — ready to build.',
    '18G': '18C completed — ready to build.',
    '18H': '18C completed — ready to build.',
    '18I': '18C completed — ready to build.',
    '13C': '13B completed — ready to build.',
    '13G': '13B+13C unblocked — ready to build.',
    '13O': '13G unblocked — ready to build.',
}

counts = {'completed': 0, 'cancelled': 0, 'deferred': 0, 'unblocked': 0}

for phase in data['phases']:
    pid = phase['id']
    if pid in completed:
        phase['status'] = 'completed'
        phase['completed_at'] = today
        phase['completion_note'] = completed[pid]
        counts['completed'] += 1
    elif pid in cancelled:
        phase['status'] = 'cancelled'
        phase['completed_at'] = today
        phase['completion_note'] = cancelled[pid]
        counts['cancelled'] += 1
    elif pid in deferred:
        phase['status'] = 'deferred'
        phase['completion_note'] = deferred[pid]
        counts['deferred'] += 1
    elif pid in unblocked:
        if phase['status'] == 'failed':
            phase['status'] = 'pending'
            phase['completion_note'] = unblocked[pid]
            counts['unblocked'] += 1

with open('roadmap.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f'Completed: {counts["completed"]}')
print(f'Cancelled: {counts["cancelled"]}')
print(f'Deferred: {counts["deferred"]}')
print(f'Unblocked (failed->pending): {counts["unblocked"]}')

statuses = {}
for p in data['phases']:
    s = p['status']
    statuses[s] = statuses.get(s, 0) + 1
print()
print('Final roadmap:')
for s, c in sorted(statuses.items()):
    print(f'  {s}: {c}')
