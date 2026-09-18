import threading

import pytest

from core.kai_event_bus import KAIEventBus
from core.workforce.registry import WorkerRecord
from core.workforce import registry as workforce_registry

from core.teammate.worker_integration import (
    WorkerAssignment,
    WorkerIntegrator,
    _TOPIC_COMPLETED,
    _TOPIC_FAILED,
    _TOPIC_REASSIGNED,
    _TOPIC_STARTED,
)


def _fresh_bus(tmp_path):
    bus = KAIEventBus.__new__(KAIEventBus)
    bus._subscribers = {}
    bus._recent_events = []
    bus._journal_dir = tmp_path
    bus._journal_dir.mkdir(parents=True, exist_ok=True)
    bus._started = False
    bus._lock = threading.RLock()
    bus._flusher_thread = None
    bus._stop_flusher_event = threading.Event()
    return bus


@pytest.fixture(autouse=True)
def fresh_event_bus(tmp_path, monkeypatch):
    import core.kai_event_bus as keb
    orig = keb.KAIEventBus.get_instance
    keb.KAIEventBus.get_instance = classmethod(lambda cls: _fresh_bus(tmp_path))
    keb.event_bus._journal_dir = tmp_path
    yield
    keb.KAIEventBus.get_instance = orig


@pytest.fixture
def worker_registry():
    return workforce_registry


def _make_worker(worker_id="provider:kai_coder"):
    workforce_registry.register(WorkerRecord(
        worker_id=worker_id,
        kind="provider",
        capabilities=["generate", "coding"],
        permissions={"secrets": [], "network": ["model-apis"], "filesystem": []},
        limits={"max_concurrency": 1, "timeout_seconds": 600},
    ))
    return worker_id


class _TeammateStub:
    """Minimal teammate double for tests that exercise ONLY the worker
    integration layer (real TeammateRegistry is covered in Task 3)."""

    def __init__(self, tid="t1", status="READY"):
        self.id = tid
        self.status = status


class _AssignmentStoreStub:
    """Provides a stable in-memory assignment store so tests can assert
    against returned assignment objects without touching memory files."""

    def __init__(self):
        self.assignments = {}


def test_assignment_repr_has_expected_fields():
    a = WorkerAssignment(teammate_id="t1", worker_id="w1", skill_id="write_code")
    d = a.to_dict()
    assert d["teammate_id"] == "t1"
    assert d["worker_id"] == "w1"
    assert d["skill_id"] == "write_code"
    assert d["status"] == "assigned"
    assert "started_at" in d


def test_assign_publishes_started_when_worker_runs(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(
        te, worker_id=wid, skill_id="write_code", reason="test")
    assert assignment.status == "assigned"
    integrator.mark_running(assignment)

    topics = [t for t, _ in received]
    assert _TOPIC_STARTED in topics
    env = next(envelope for t, envelope in received if t == _TOPIC_STARTED)
    assert env["source"] == "teammate_factory"
    assert env["payload"]["teammate_id"] == "t1"
    assert env["payload"]["worker_id"] == wid


def test_failed_publishes_and_increments_worker_failures(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code",
                                   reason="test")
    integrator.mark_running(assignment)
    integrator.mark_failed(assignment, reason="model timeout")

    assert any(t == _TOPIC_FAILED for t in received)
    worker = workforce_registry.get(wid)
    assert worker.health["consecutive_failures"] == 1


def test_reassign_publishes_and_closes_old(tmp_path):
    _make_worker("provider:a")
    _make_worker("provider:b")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id="provider:a", skill_id="write_code",
                                   reason="test")
    integrator.mark_running(assignment)
    new = integrator.reassign(te, new_worker_id="provider:b", reason="circuit open")

    assert any(t == _TOPIC_REASSIGNED for t in received)
    assigned_end = integrator.get(assignment)
    assert assigned_end.ended_at is not None
    assert new.worker_id == "provider:b"
    assert new.status == "assigned"


def test_completed_publishes_and_keeps_health(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code",
                                   reason="test")
    integrator.mark_running(assignment)
    integrator.mark_completed(assignment, result="ok")

    assert any(t == _TOPIC_COMPLETED for t in received)
    assert integrator.get(assignment).status == "completed"
    worker = workforce_registry.get(wid)
    assert worker.health["consecutive_failures"] == 0


def test_track_health_records_heartbeat_and_reconciles_failed_worker(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code",
                                   reason="test")
    integrator.mark_running(assignment)
    integrator.track_health(te, wid)
    worker = workforce_registry.get(wid)
    assert worker.health["last_heartbeat"] is not None

    workforce_registry.update_status(wid, "dead", reason="crash",
                                     increment_failures=True)
    integrator.track_health(te, wid)
    assert any(t == _TOPIC_FAILED for t in received)
    assert integrator.get(assignment).status == "failed"


def test_assignments_roundtrip_across_new_integrator_instances():
    first = WorkerIntegrator()
    stored = first._put(WorkerAssignment(
        teammate_id="t1", worker_id="provider:kai_coder", skill_id="write_code"))
    second = WorkerIntegrator()  # no store injected → __init__ loads from memory
    loaded = second.get(stored.assignment_id)
    assert loaded is not None
    assert loaded.teammate_id == "t1"
    assert loaded.worker_id == "provider:kai_coder"
    assert loaded.skill_id == "write_code"
    assert loaded.status == "assigned"


def test_double_assign_same_composite_keeps_two_distinct_assignments():
    wid = _make_worker()
    te = _TeammateStub()
    integrator = WorkerIntegrator()
    a1 = integrator.assign(te, worker_id=wid, skill_id="write_code", reason="first")
    a2 = integrator.assign(te, worker_id=wid, skill_id="write_code", reason="second")
    assert a1.assignment_id != a2.assignment_id
    assert len(integrator._assignments) == 2
    assert integrator.get(a1) is a1
    assert integrator.get(a2) is a2
    assert integrator.get(a1.assignment_id) is a1
    assert integrator.get(a2.assignment_id) is a2


def test_worker_failed_payload_includes_reason(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code",
                                   reason="load balancing")
    integrator.mark_running(assignment)
    integrator.mark_failed(assignment, reason="model timeout")

    env = next(e for t, e in received if t == _TOPIC_FAILED)
    assert env["payload"]["reason"] == "model timeout"
    assert integrator.get(assignment).reason == "model timeout"


def test_track_health_circuit_open_marks_assignment_failed(tmp_path):
    wid = _make_worker()
    workforce_registry.set_circuit_state(wid, "open")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.mark_running(assignment)
    integrator.track_health(te, wid)

    assert integrator.get(assignment).status == "failed"
    assert any(t == _TOPIC_FAILED for t in received)


def test_mark_failed_on_unknown_worker_is_safe(tmp_path, monkeypatch):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.mark_running(assignment)

    monkeypatch.setattr(workforce_registry, "get", lambda worker_id: None)
    integrator.mark_failed(assignment, reason="worker vanished")

    current = integrator.get(assignment)
    assert current.status == "failed"
    assert current.ended_at is not None
    assert any(t == _TOPIC_FAILED for t, _ in received)


def test_illegal_transitions_raise(tmp_path):
    wid = _make_worker()
    te = _TeammateStub()
    integrator = WorkerIntegrator()

    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.mark_completed(assignment)
    with pytest.raises(ValueError):
        integrator.mark_failed(assignment, reason="late failure")

    other = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.mark_running(other)
    with pytest.raises(ValueError):
        integrator.mark_running(other)


def test_track_health_preserves_failure_count_on_dead_worker(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.mark_running(assignment)

    workforce_registry.update_status(wid, "dead", reason="crash",
                                     increment_failures=True)
    integrator.track_health(te, wid)
    after_first = workforce_registry.get(wid).health["consecutive_failures"]
    assert after_first >= 1

    integrator.track_health(te, wid)
    after_second = workforce_registry.get(wid).health["consecutive_failures"]
    assert after_second >= 1
    assert after_second >= after_first
    assert integrator.get(assignment).status == "failed"


def test_reassign_sets_old_status_reassigned_and_payload_has_new_worker(tmp_path):
    _make_worker("provider:a")
    _make_worker("provider:b")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id="provider:a", skill_id="write_code",
                                   reason="load")
    integrator.mark_running(assignment)
    new = integrator.reassign(te, new_worker_id="provider:b", reason="circuit open")

    old = integrator.get(assignment)
    assert old.status == "reassigned"
    assert old.ended_at is not None
    env = next(e for t, e in received if t == _TOPIC_REASSIGNED)
    assert env["payload"]["status"] == "reassigned"
    assert env["payload"]["new_worker_id"] == "provider:b"
    assert new.assignment_id != assignment.assignment_id
    assert new.worker_id == "provider:b"
    assert new.attempts == 2


def test_mark_running_and_completed_payloads_include_assignment_id(tmp_path):
    _make_worker("provider:a")
    _make_worker("provider:b")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()

    a = integrator.assign(te, worker_id="provider:a", skill_id="write_code")
    integrator.mark_running(a)
    integrator.mark_completed(a, result="ok")

    b = integrator.assign(te, worker_id="provider:b", skill_id="write_code")
    integrator.mark_running(b)
    c = integrator.reassign(te, new_worker_id="provider:a", reason="move")
    integrator.mark_failed(c, reason="boom")

    for topic in (_TOPIC_STARTED, _TOPIC_FAILED, _TOPIC_REASSIGNED,
                  _TOPIC_COMPLETED):
        envs = [e for t, e in received if t == topic]
        assert envs, topic
        for env in envs:
            assert env["payload"]["assignment_id"] is not None
            assert env["payload"]["assignment_id"] != "", (topic, env)


def test_track_health_promotes_assigned_via_mark_running(tmp_path):
    wid = _make_worker()
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append(t))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id=wid, skill_id="write_code")
    integrator.track_health(te, wid)

    assert integrator.get(assignment).status == "running"
    assert any(t == _TOPIC_STARTED for t in received)
    with pytest.raises(ValueError):
        integrator.mark_running(assignment)


def test_reassign_without_reason_omits_falsy_reason(tmp_path):
    _make_worker("provider:a")
    _make_worker("provider:b")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id="provider:a", skill_id="write_code")
    integrator.mark_running(assignment)
    integrator.reassign(te, new_worker_id="provider:b")

    env = next(e for t, e in received if t == _TOPIC_REASSIGNED)
    assert env["payload"].get("reason") is None


def test_reassigned_payload_contains_new_assignment_id(tmp_path):
    _make_worker("provider:a")
    _make_worker("provider:b")
    received = []
    from core.kai_event_bus import event_bus
    event_bus.subscribe("worker.*", lambda t, e: received.append((t, e)))

    te = _TeammateStub()
    integrator = WorkerIntegrator()
    assignment = integrator.assign(te, worker_id="provider:a", skill_id="write_code")
    integrator.mark_running(assignment)
    new = integrator.reassign(te, new_worker_id="provider:b", reason="move")

    env = next(e for t, e in received if t == _TOPIC_REASSIGNED)
    successor_id = env["payload"]["new_assignment_id"]
    assert successor_id == new.assignment_id
    assert integrator.get(successor_id) is new
    assert integrator.get(successor_id).worker_id == "provider:b"
