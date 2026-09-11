import json
from core.world_model import WorldModel, _MEMORY_DIR, WORLD_PATH

def test_collect_entities():
    world_model = WorldModel()
    world_model.collect_entities()
    assert 'models' in world_model.entities
    assert 'teammates' in world_model.entities
    assert 'teams' in world_model.entities
    assert 'missions' in world_model.entities
    assert 'policies' in world_model.entities

def test_snapshot():
    world_model = WorldModel()
    world_model.collect_entities()
    snapshot = world_model.snapshot()
    assert isinstance(snapshot, dict)
    assert 'models' in snapshot
    assert 'teammates' in snapshot
    assert 'teams' in snapshot
    assert 'missions' in snapshot
    assert 'policies' in snapshot

def test_impact_of():
    world_model = WorldModel()
    world_model.collect_entities()
    impact = world_model.impact_of('models')
    assert isinstance(impact, dict)
    assert 'models' in impact

def test_diff():
    world_model = WorldModel()
    world_model.collect_entities()
    snapshot1 = world_model.snapshot()
    world_model.collect_entities()
    snapshot2 = world_model.snapshot()
    diff = world_model.diff(snapshot1, snapshot2)
    assert isinstance(diff, dict)
    assert 'models' in diff
    assert 'teammates' in diff
    assert 'teams' in diff
    assert 'missions' in diff
    assert 'policies' in diff
