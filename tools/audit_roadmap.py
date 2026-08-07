"""One-shot: audit all 54 failed roadmap phases and recategorize them."""
import json
from datetime import datetime, timezone

with open('roadmap.json') as f:
    data = json.load(f)

today = '2026-08-07'

# Category 1: ALREADY DONE
completed = {
    'AI-1': 'Provider consolidation + qwen3->qwen4 rename executed. 3 dead OpenRouter providers removed, all qwen4 providers renamed, openai slot fixed. Commit 1ddd2fb.',
    '13Y': 'Fixed 2026-07-29 via direct live fix (commit 1d4f8d8). _plan_needs_clarification false-positive on rhetorical sign-off questions resolved.',
    '13S': 'Fixed 2026-07-29 via direct live fix (commit 4c69637). _looks_like_tool_call_leak rejects empty/near-empty plan text and tool-call markup.',
    'IT-2': 'Dependency upgrade completed. @anthropic-ai/sdk upgraded from 0.100.1 to 0.115.0 in commit cf4b034.',
    '16D': 'Budget monitor implemented. core/monitoring/budget_monitor.py with check_budgets() runs in orchestrator_cycle. Config at config/budget.json. Telegram alerts via 13Z bridge.',
}

# Category 2: OBSOLETE (RunPod decommissioning)
cancelled = {
    '17S': 'Obsolete: OpenCode Zen dedicated keys. Claude Fable 5 already works via existing OpenCode Zen. RunPod decommissioned.',
    '17U': 'Obsolete: Provider config editor. Provider landscape simplified after RunPod decommissioning.',
    '17Z': 'Obsolete: Wire in self-hosted Qwen3-Coder RunPod provider. Both RunPod GPU pods decommissioned 2026-08-06.',
    '17X': 'Obsolete: Automated Resiliency for 13E. Context superseded by provider migration.',
    '13L': 'Obsolete: Provider Performance-Weighted Routing. Superseded by simplified provider landscape.',
    '13L-1': 'Obsolete: Duplicate of 13L. Performance-weighted routing no longer needed.',
    '13U': 'Obsolete: Register DeepSeek as a real provider. Already done -- deepseek_native_flash and deepseek_native_pro are primary providers.',
    'AI-6': 'Obsolete: RunPod deployment templates. RunPod GPU pods decommissioned. Automated pod creation no longer applicable.',
}

# Category 3: DEFERRED (R&D / stretch goals)
deferred = {
    '19R': 'Deferred: Application Registry -- visionary R&D phase, not a failed implementation.',
    '19S': 'Deferred: Repository Registry -- GitHub/GitLab/Gitea integration. R&D proposal.',
    '19U': 'Deferred: Dependency Graph -- cross-project relationship mapping. R&D proposal.',
    '19V': 'Deferred: Software Factory -- full conversation-to-deployment pipeline. R&D proposal.',
    '19W': 'Deferred: Templates -- reusable app templates (CRM, ERP, etc.). R&D proposal.',
    '19X': 'Deferred: Lifecycle Management -- complete history for every app. R&D proposal.',
    '19H': 'Deferred: Reflection Cortex -- automatic reflection after tasks. R&D proposal.',
    '19Y': 'Deferred: AI Workforce Integration -- auto-assign AI workers. R&D proposal.',
    '19I': 'Deferred: Learning Cortex -- continuous learning from failures/successes. R&D proposal.',
    '19Z': 'Deferred: Command Center Extensions -- app dashboards and explorer. R&D proposal.',
    '19AA': 'Deferred: Security Model -- RBAC, tenant isolation for apps. R&D proposal.',
    '19K': 'Deferred: Simulation Engine -- simulate actions before executing. R&D proposal.',
    '19BB': 'Deferred: Integration -- reuse existing modules for software factory. R&D proposal.',
    '19M': 'Deferred: Self Improvement -- Kai evaluates itself. R&D proposal.',
    '19O': 'Deferred: Command Center -- mission control for brain/memory/knowledge. R&D proposal.',
    '19P': 'Deferred: Self Explanation -- Kai answers why questions with evidence. R&D proposal.',
    'JK-4': 'Deferred: Juris Kai Knowledge Management -- legal knowledge base search. Stretch goal.',
    'JK-5': 'Deferred: Juris Kai Analytics -- user engagement metrics. Stretch goal.',
    'JK-6': 'Deferred: Juris Kai Monetization Readiness -- Hubtel API payment integration. Stretch goal.',
    '14A': 'Deferred: Stuck-phase detection and Fable auto-answer. R&D proposal.',
    '17G': 'Deferred: UI/UX polish pass for Proxdash and Kai Dashboards. Stretch goal.',
    '17I': 'Deferred: Application portfolio awareness. R&D proposal.',
}

counts = {'completed': 0, 'cancelled': 0, 'deferred': 0, 'kept': 0}

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

# Count kept as failed (not in any dict above)
for phase in data['phases']:
    if phase['status'] == 'failed':
        counts['kept'] += 1

with open('roadmap.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f'Completed: {counts["completed"]}')
print(f'Cancelled: {counts["cancelled"]}')
print(f'Deferred: {counts["deferred"]}')
print(f'Kept as failed: {counts["kept"]}')

statuses = {}
for p in data['phases']:
    s = p['status']
    statuses[s] = statuses.get(s, 0) + 1
print()
print('New roadmap status:')
for s, c in sorted(statuses.items()):
    print(f'  {s}: {c}')
