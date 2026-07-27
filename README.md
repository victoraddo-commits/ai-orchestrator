# AI Orchestrator

A self-operating monitoring/remediation system for a home-lab Proxmox + Docker
environment. It observes, reasons about, and (with human approval) remediates
infrastructure problems, verifying the outcome afterward and learning from it.

Runs as a single systemd service (`ai-orchestrator.service`) polling every 300
seconds, plus an optional read-only observability API.

## What it actually does today

- Scans Proxmox (node, LXC containers, VMs, backups, network) and Docker
  containers for problems every 5 minutes.
- Turns findings into deduplicated, lifecycle-tracked incidents.
- Reasons about incidents using severity, recurrence, and historical fix
  success rate, and proposes a remediation action.
- **Every proposed action requires human approval** via `approval_cli.py`
  before anything executes. There is no autonomous execution today —
  `AUTONOMOUS_MODE` in `core/config.py` is `False`. See `SECURITY.md`.
- The only action wired to real execution is `docker restart <container>`.
- Verifies whether the fix actually worked, and attempts an automatic
  rollback if it didn't (see `ARCHITECTURE.md` for the current limits of
  that).
- Tracks which actions historically work, to inform future confidence.

## Quick start

```bash
# service status / logs
systemctl status ai-orchestrator
journalctl -u ai-orchestrator -f

# see what's pending approval
python -m core.approval_cli list

# approve or reject
python -m core.approval_cli approve <id>
python -m core.approval_cli reject <id>

# run the test suite
.venv/bin/python -m pytest

# read-only dashboard API (not started as a service by default --
# run manually when you want it)
.venv/bin/uvicorn core.api:app --host 127.0.0.1 --port 8000
curl localhost:8000/health
```

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) -- how the pieces fit together, the
  five lifecycle objects, the memory layer.
- [`OPERATIONS.md`](OPERATIONS.md) -- day-to-day running, approving actions,
  deploying changes, troubleshooting.
- [`SECURITY.md`](SECURITY.md) -- the action allowlist, dangerous-command
  blocking, audit log, and the threat model this was actually designed
  against.
- [`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md) -- memory corruption
  recovery, rollback, what happens if the service crashes.

## Project status

Foundational lifecycle/safety work (roadmap phases 1-10) is complete and
running in production on the live host. Not yet built: real actions beyond
`restart_container` (resize, migrate VM, etc.), a full authentication system
(deliberately out of scope for a single-operator box -- see `SECURITY.md`),
and a persistent deployment of the observability API.
