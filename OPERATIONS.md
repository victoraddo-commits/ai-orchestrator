# Operations

## Checking the service

```bash
systemctl status ai-orchestrator
journalctl -u ai-orchestrator -f
journalctl -u ai-orchestrator --since "10 minutes ago"
```

A healthy cycle logs `=== orchestrator cycle started ===` followed within a
couple seconds by `=== orchestrator cycle completed ===` and a
`cycle completed findings=N incidents=N decisions=N` summary line, every 300
seconds. `scheduler error: ...` means the cycle raised and was swallowed —
the loop keeps running (see `DISASTER_RECOVERY.md`), but nothing was
processed that cycle. Treat any recurring `scheduler error` as a real bug,
not noise.

## Viewing current state

Directly from the memory files (each is `{"schema_version": 1, "records":
[...]}`):

```bash
python -c "from core.memory import load; import json; print(json.dumps(load('incidents.json'), indent=2))"
python -c "from core.incident_manager import get_active_incidents; print(get_active_incidents())"
```

Or via the read-only API (start it manually — see `README.md`):

```bash
curl localhost:8000/health
curl localhost:8000/incidents
curl localhost:8000/approvals
curl localhost:8000/learning
```

## Approving or rejecting a proposed action

Every proposed remediation sits in `approval_queue.json` as `"pending"`
until a human acts on it:

```bash
python -m core.approval_cli list
python -m core.approval_cli approve <id>     # prompts "type 'yes' to confirm"
python -m core.approval_cli approve <id> --yes   # skip the interactive prompt
python -m core.approval_cli reject <id>
```

The CLI records who approved it (`SUDO_USER` env var, falling back to the
current OS user) on the request, and it shows up in the execution audit log
(`memory/execution_audit.json`) once the action actually runs.

Approved requests are picked up and executed on the **next** scheduler
cycle, not immediately — there's no separate trigger.

## Deploying a code change

The scheduler is a long-running Python process; it does **not** hot-reload.
A code change has zero effect until the service is restarted:

```bash
# 1. run the full test suite first, every time, no exceptions
.venv/bin/python -m pytest

# 2. confirm production memory is untouched by the test run
md5sum memory/*.json > /tmp/before.md5
.venv/bin/python -m pytest -q
md5sum memory/*.json > /tmp/after.md5
diff /tmp/before.md5 /tmp/after.md5

# 3. restart
systemctl restart ai-orchestrator

# 4. watch at least one full cycle before walking away
journalctl -u ai-orchestrator -f
```

This project has already caught one real crash this way (a legacy data
shape that only showed up against real production `memory/incidents.json`,
not the test fixtures) — always watch the first live cycle after a restart,
don't just check that the process is "active".

## Autonomous mode

`core/config.py::AUTONOMOUS_MODE` is `False`. With it `False`, every
proposed action requires human approval regardless of its risk
classification. Turning it `True` only changes behavior for actions
classified `low` risk in `core/action_policy.py` (currently just
`restart_container`) — medium/high risk actions and anything unclassified
always require approval no matter what this flag is set to. Flip it
deliberately, not as a side effect of something else, and watch a cycle
afterward the same way you would for any other deploy.

## Common troubleshooting

- **Nothing in `incidents.json` matches what I see in Proxmox/Docker**:
  check `memory/last_scan.json` was actually refreshed recently
  (`state.last_scan` field) — a scan failure upstream means `health.analyze()`
  has stale data to work from.
- **An approved action never executes**: confirm the request's `status` is
  actually `"approved"` in `approval_queue.json` (not still `"pending"`) and
  that a cycle has run since you approved it.
- **A remediation's `rollback` shows `"available": false`**: expected for
  `restart_container` — there's no registered rollback strategy for it (see
  `ARCHITECTURE.md`). This is not a bug.
