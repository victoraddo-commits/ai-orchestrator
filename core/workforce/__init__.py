"""core.workforce — formal worker identity/registry layer over the existing
orchestration chain. See docs/superpowers/specs/2026-08-22-kai-workforce-design.md."""
from core.workforce.registry import (  # noqa: F401
    WorkerRecord, register, get, list_workers, update_status,
    record_heartbeat, set_circuit_state, revive, deregister_expired,
)
