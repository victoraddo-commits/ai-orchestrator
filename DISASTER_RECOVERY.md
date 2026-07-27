# Disaster Recovery

## Memory file corruption

Every write to `memory/*.json` goes through `core/memory_manager.py`:
- writes are atomic (temp file + `os.replace`), so a crash mid-write cannot
  leave a torn/partially-written file;
- a rolling `.bak` snapshot of the previous version is kept alongside every
  file, before each write;
- on read, if the primary file fails to parse as JSON, `memory_manager.read()`
  automatically falls back to the `.bak` file; if that's also unreadable, it
  returns the caller's default (`{}` or `[]`) rather than raising.

**Manual recovery**, if you ever need to intervene by hand:

```bash
# inspect what's there
cat memory/incidents.json.bak

# restore a specific file from its backup
cp memory/incidents.json.bak memory/incidents.json
```

Every file is wrapped as `{"schema_version": 1, "records": [...]}`. If
you're editing one by hand, preserve that envelope — `memory_manager` will
transparently accept an unwrapped bare list/dict too (it upgrades the file
automatically on next write), but writing a malformed envelope yourself
(right keys, wrong types) will not be auto-corrected.

## Remediation rollback

See `ARCHITECTURE.md` for the full mechanism. In short: if a remediation's
verification comes back `unresolved`, `attempt_rollback()` runs
automatically. Check `memory/remediation.json` for a `"rollback"` field on
the relevant record — `{"attempted": true, "available": false, ...}` means
there was no registered inverse action (expected for `restart_container`
today, not a failure of the mechanism), while `{"attempted": true,
"available": true, "result": {...}}` means a real rollback executed.

## Service crash / restart behavior

The systemd unit (`/etc/systemd/system/ai-orchestrator.service` +
`override.conf`) is configured with:

```
Restart=always
RestartSec=30
WatchdogSec=600
```

If the scheduler process dies outright, systemd restarts it after 30
seconds automatically — no manual intervention needed. Within the process,
`core/scheduler.py`'s loop also catches any exception from `run_cycle()`
and logs `scheduler error: ...` rather than crashing the process, so a
single bad cycle doesn't take the whole service down (see `OPERATIONS.md`
for what this means day to day, and why a recurring `scheduler error` still
needs investigating even though the service stays "active").

`WatchdogSec=600` relies on the scheduler calling `notify("WATCHDOG=1")`
after each successful cycle (only present if the `systemd` Python bindings
are available — see the `try/except ImportError` at the top of
`scheduler.py`). If the process hangs without crashing, systemd's watchdog
will restart it after 10 minutes of silence.

## Rebuilding state from scratch

Every `memory/*.json` file is derived, re-creatable state, not a system of
record for anything outside this box:
- `incidents.json`, `decisions.json`, `approval_queue.json`,
  `remediation.json`, `verification_history.json`,
  `remediation_history.json`, `execution_audit.json` can all be safely
  deleted (or moved aside) and will be recreated empty (`[]`) the next
  time anything writes to them. You lose incident/decision history, not
  any real infrastructure state.
- `last_scan.json` and `system_state.json` are fully rebuilt on the very
  next scheduler cycle from a live Proxmox/Docker scan.
- `policy.json` is currently unused by the live path (see
  `ARCHITECTURE.md`) — safe to leave alone either way.

There is no database migration to run. If you delete everything under
`memory/`, the next cycle recreates whatever it needs.

## What this does *not* protect against

This system has no write access to Proxmox VM/LXC state beyond restarting
a Docker container (see `SECURITY.md`). A genuine Proxmox host failure,
disk failure on the hypervisor itself, or loss of the LXC this runs in is
outside what `ai-orchestrator` can detect or recover from — it can only
report on such a failure if it's still running somewhere with API access
to observe it.
