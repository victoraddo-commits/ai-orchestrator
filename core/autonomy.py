"""Phase 13H: Controlled Autonomy Levels.

Replaces the binary Phase 12L flag (memory/autonomous_mode.json's
``enabled: true/false``) with 6 explicit levels. Each level unlocks
strictly more automation than the one below it, and NONE of them
bypasses the ARCHITECTURE_APPROVED / DEPLOY_APPROVAL human gates in
core/build_manager.py -- those exist for exactly the case where the
operator has set the highest level and then wants to still see every
architecture plan and every deploy before it happens.

Level catalog
-------------

0 -- Manual only. Nothing automatic. The autonomous roadmap loop stays
     idle even if someone calls advance_roadmap() by hand.
1 -- Observe + report (the default). Passive analytics and alerts run,
     but no proposals get written, no branches get cut, no builds get
     started. This is what a fresh install / a build with no explicit
     level configured lands on.
2 -- Create proposals. Kai's improvement-proposal generator
     (core/kai/planner.py) is allowed to write to
     memory/improvement_proposals.json. Still no branches, no builds.
3 -- Create branches / builds. advance_roadmap() may cut isolated
     self-build clones and start builds through code review + security
     review, but stops at the WAITING_FOR_DEPLOY_APPROVAL gate.
4 -- Execute through deploy-approval. The exact old ``enabled: true``
     behavior: builds are driven all the way through, but the two
     human-approval gates in build_manager (architecture, deploy) are
     still required and still block.
5 -- Continuously manages roadmap incl. self-generated phases. Level 4
     PLUS the planner is allowed to promote its own proposals into
     new roadmap phases without a per-phase human command. The
     ARCHITECTURE_APPROVED / DEPLOY_APPROVAL gates on each build
     still fire -- the operator remains the sole approver of every
     architecture plan and every deploy.

Backwards compatibility
-----------------------

The old ``is_autonomous_mode_enabled() / enable_autonomous_mode() /
disable_autonomous_mode()`` symbols in core.roadmap_manager are kept as
thin wrappers around this module's level API so that existing test code
and existing callers keep working. Migration mapping:

* Old ``enabled: true`` --> Level 4 (identical behavior).
* Old ``enabled: false`` --> Level 1 (the 13H default; NOT level 0,
  which is fully manual and must be set explicitly).
* Old file missing or malformed --> Level 1.

Locking / atomicity
-------------------

Every write goes through core.memory_manager.write, which already
serializes concurrent writers with fcntl.flock (see memory_manager.py's
``_write_locked``). No new lock discipline is invented here.
"""

from datetime import datetime, timezone

from core.memory import load, save


AUTONOMY_LEVEL_FILE = "autonomy_level.json"

# The pre-13H binary flag. Read at migration time only; never written
# by this module. The old file is kept on disk after migration so a
# rollback can find it -- deleting it is a future phase's job.
LEGACY_AUTONOMOUS_MODE_FILE = "autonomous_mode.json"

# The default level for a fresh install / a system that has no
# autonomy_level.json yet AND no legacy file to migrate from.
# ``1`` per the 13H spec: observe + report.
DEFAULT_LEVEL = 1

# Sentinel written into ``set_by`` when the system itself creates the
# file (e.g. one-time migration on first boot, or the read path healing
# a missing file). Distinct from any real operator identity string so
# an audit log reader can tell "the system chose this default" from
# "an operator set this".
SYSTEM_DEFAULT_IDENTITY = "system-default"

MIN_LEVEL = 0
MAX_LEVEL = 5

VALID_LEVELS = tuple(range(MIN_LEVEL, MAX_LEVEL + 1))


def _now_iso():
    # UTC, second-resolution ISO-8601 with a trailing Z -- matches the
    # timestamp shape the rest of memory/*.json uses (build_manager,
    # approval, etc.).
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_record(level=DEFAULT_LEVEL, set_by=SYSTEM_DEFAULT_IDENTITY):
    return {
        "level": level,
        "set_by": set_by,
        "updated_at": _now_iso(),
    }


def _coerce_level(value):
    """Best-effort coercion of a stored/inbound level to an int in
    [MIN_LEVEL, MAX_LEVEL]. Anything unparseable / out of range / a
    non-integer numeric (e.g. ``1.5``, which ``int()`` would silently
    truncate to 1) returns None -- callers decide whether to fall back
    to a default or raise."""

    # Booleans are an int subclass in Python; treat them as invalid so
    # a stray ``True`` doesn't silently mean "level 1".
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        n = value
    elif isinstance(value, str):
        try:
            n = int(value)
        except ValueError:
            return None
    else:
        return None

    if n < MIN_LEVEL or n > MAX_LEVEL:
        return None

    return n


def _migrate_from_legacy():
    """One-shot migration: derive an autonomy_level record from the
    pre-13H ``autonomous_mode.json`` file. Callers are responsible for
    persisting the returned record; this function itself has no
    side effects.

    Returns ``None`` when there is no legacy file to migrate (so
    callers can fall through to the plain default). Malformed legacy
    data is treated the same as ``enabled: false`` -> Level 1, per the
    spec's decided-up-front migration mapping.
    """

    legacy = load(LEGACY_AUTONOMOUS_MODE_FILE)

    if not isinstance(legacy, dict) or "enabled" not in legacy:
        return None

    level = 4 if legacy.get("enabled") is True else 1

    return _default_record(level=level, set_by=SYSTEM_DEFAULT_IDENTITY)


def get_autonomy_level():
    """Read the current autonomy record.

    Missing / malformed on-disk state is healed transparently: on the
    very first read after upgrade, the legacy binary file (if any) is
    migrated per the 13H mapping and the new autonomy_level.json is
    written out with ``set_by = "system-default"``. On a truly fresh
    install with neither file present, the DEFAULT_LEVEL (1) is
    returned and persisted so the operator can see, in the UI, exactly
    what state the system defaulted to.
    """

    stored = load(AUTONOMY_LEVEL_FILE)

    if isinstance(stored, dict):
        level = _coerce_level(stored.get("level"))
        if level is not None:
            # Preserve existing set_by / updated_at (operator audit
            # trail) rather than smashing them with fresh defaults.
            return {
                "level": level,
                "set_by": stored.get("set_by") or SYSTEM_DEFAULT_IDENTITY,
                "updated_at": stored.get("updated_at") or _now_iso(),
            }

    migrated = _migrate_from_legacy()
    record = migrated if migrated is not None else _default_record()

    # Persist so subsequent reads are cheap and so the operator UI
    # sees a real record instead of a synthesized-in-memory default.
    save(AUTONOMY_LEVEL_FILE, record)

    return record


def set_autonomy_level(level, operator_identity):
    """Persist ``level`` (must be an int in [0, 5]) with the given
    operator identity string in ``set_by``. Returns the written record.

    Raises ``ValueError`` for out-of-range / non-integer levels;
    require_bridge_token / the caller is responsible for ensuring
    ``operator_identity`` is a real, authenticated identity string --
    this module never invents one on behalf of a caller (a synthesized
    identity would defeat the audit-trail purpose of ``set_by``).
    """

    coerced = _coerce_level(level)
    if coerced is None:
        raise ValueError(
            f"autonomy level must be an integer in [{MIN_LEVEL}, {MAX_LEVEL}], "
            f"got {level!r}"
        )

    if not isinstance(operator_identity, str) or not operator_identity:
        raise ValueError("operator_identity must be a non-empty string")

    record = {
        "level": coerced,
        "set_by": operator_identity,
        "updated_at": _now_iso(),
    }

    save(AUTONOMY_LEVEL_FILE, record)

    return record


# ---------------------------------------------------------------------------
# Capability predicates. Every autonomous hook point in the codebase should
# gate on one of these (never on a raw ``level >= N`` inline) so the policy
# is stated once, here, and can't drift phase-by-phase.
# ---------------------------------------------------------------------------


def get_level():
    """Convenience: just the integer level, without the audit envelope."""
    return get_autonomy_level()["level"]


def can_observe_and_report():
    """Level 1+: passive analytics, alerts, health scans. This is what
    a fresh install runs at."""
    return get_level() >= 1


def can_create_proposals():
    """Level 2+: the improvement-proposal generator may write to
    memory/improvement_proposals.json."""
    return get_level() >= 2


def can_create_branches_and_builds():
    """Level 3+: advance_roadmap() may cut branches and start builds
    (still stops at the deploy-approval gate)."""
    return get_level() >= 3


def can_execute_through_deploy_approval():
    """Level 4+: the exact old ``enabled: true`` behavior -- builds
    are driven through code review, security review, and up to the
    deploy-approval gate. ARCHITECTURE_APPROVED / DEPLOY_APPROVAL
    remain human-only regardless."""
    return get_level() >= 4


def can_manage_roadmap_continuously():
    """Level 5 only: the planner may promote its own proposals into
    new roadmap phases without a per-phase human command. Every
    resulting build still hits ARCHITECTURE_APPROVED / DEPLOY_APPROVAL
    -- 13H's non-negotiable invariant."""
    return get_level() >= 5


# ---------------------------------------------------------------------------
# Backwards-compatibility shims for pre-13H callers.
#
# These preserve the exact API surface of the old
# core.roadmap_manager.{is,enable,disable}_autonomous_mode() functions so
# existing tests and existing external callers (e.g. Telegram command
# handlers, if any) keep working without a synchronised world-wide edit.
# roadmap_manager re-exports these under their old names -- see that
# module.
# ---------------------------------------------------------------------------


def is_autonomous_mode_enabled():
    """Old binary flag semantics. True iff level >= 4 (the exact
    behavior the pre-13H flag guarded)."""
    return can_execute_through_deploy_approval()


def enable_autonomous_mode(operator_identity=SYSTEM_DEFAULT_IDENTITY):
    """Old shim: promote to Level 4, the exact behavior the pre-13H
    ``enabled: true`` flag unlocked. Callers that have a real operator
    identity should pass it; those that don't (legacy internal helpers,
    tests) get ``system-default`` recorded, which is honest about the
    fact that no authenticated operator asked for this."""
    return set_autonomy_level(4, operator_identity)


def disable_autonomous_mode(operator_identity=SYSTEM_DEFAULT_IDENTITY):
    """Old shim: return to Level 1 (observe + report), NOT Level 0.
    Level 0 (fully manual) is intentionally reachable only through the
    explicit level-set API -- 'disabling autonomous mode' in pre-13H
    meant 'stop advancing the roadmap', which is exactly Level 1's
    guarantee, not the stricter 'nothing automatic at all' Level 0."""
    return set_autonomy_level(1, operator_identity)
