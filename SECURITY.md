# Security

## Threat model

This runs as root in a single-operator home-lab LXC container, reached via
SSH/console. Anyone with shell access to this box already has root — that
is the actual trust boundary. Design decisions below are scoped to that
reality, not to a multi-tenant or network-exposed deployment. If this ever
changes (shared operators, network-remote approval, exposure beyond this
host), revisit every decision in this document.

## Action allowlist and risk tiers (`core/action_policy.py`)

| Tier | Actions (today) | Approval required? |
|---|---|---|
| Low | `restart_monitoring_agent`, `clear_temp_files`, `restart_container` | Only skippable if `AUTONOMOUS_MODE=True` |
| Medium | `resize_resources`, `restart_production_service` | Always |
| High | `migrate_vm`, `shutdown_node`, `modify_networking`, `delete_resources` | Always |
| Unclassified | anything not in the above lists | Always, and blocked outright by `core/security.py` |

`core/docker_actions.py::execute_action()` calls
`core.security.enforce_action_is_safe(action, command)` before doing
anything. An action not present in one of the tiers above raises
`SecurityViolation` rather than silently no-op'ing — this makes it
structurally impossible to wire in a new action type without it passing
through the policy first.

**Only `restart_container` is wired to real execution today.** The other
tier entries are classified but have no implementation — they exist so the
policy is ready when a real action for them is built.

## Dangerous command blocking (`core/security.py`)

Independent of the allowlist, every constructed command string is checked
against a pattern list before execution: `rm -rf /`, `mkfs.*`, `dd
if=...of=/dev/*`, fork bombs, `iptables -F`/`ufw disable`, `useradd`,
`passwd`, `chmod 777 /`, and reads/writes of `/etc/shadow`, `id_rsa`,
`~/.ssh/authorized_keys`. A match raises `SecurityViolation` regardless of
what action requested it. See `tests/test_security.py` for the full list
and `tests/test_docker_actions_security.py` for it wired into the real
execution path.

Per the roadmap: `rm -rf`, disk destruction, firewall lockout, and
credential changes are never allowed without explicit approval — in
practice this means those patterns are blocked outright, since nothing in
this system currently has a legitimate reason to run them at all.

## Audit log (`core/execution_audit.py`)

Every execution (success or failure) appends an entry to
`memory/execution_audit.json`: operator, action, service, command, result,
linked `request_id`/`remediation_id`, and a timestamp. This is append-only
and separate from `remediation_history.json` (which is optimized for
success-rate queries, not audit trail).

## Operator identity and "approval authentication"

`core/approval.py::approve()`/`reject()` record who acted (`approved_by`/
`rejected_by`). `approval_cli.py` captures this automatically (`SUDO_USER`
env var, falling back to the OS user) and requires typing `yes` to confirm
before approving/rejecting interactively (or `--yes` for scripted use).
`decision_engine.py`'s autonomous-mode auto-approval records
`operator="system(autonomous)"` so the audit trail can tell human approvals
apart from policy-driven ones.

**Scoping decision**: this is operator identity + a confirmation step, not
a username/password or API-key authentication system. Given the threat
model above, a login prompt in front of a CLI that anyone with shell access
could bypass by calling the Python function directly wouldn't add real
security — only friction. What actually matters at this scale is knowing
who approved what (now recorded) and not fat-fingering an approval (now
requires an explicit "yes"). If this system is ever reachable by more than
one trusted operator, or over a network channel that isn't already
gated by SSH, add real authentication before relying on this document's
reasoning.

## `AUTONOMOUS_MODE`

Lives in `core/config.py`, defaults to `False`. See `OPERATIONS.md` for
what changes (and, more importantly, what doesn't) when it's turned on.
