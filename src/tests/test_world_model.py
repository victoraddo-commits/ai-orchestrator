import json
from pathlib import Path
from core.world_model import WorldModel

def test_world_model_load_save():
    wm = WorldModel()
    wm.load()
    assert wm.nodes == {}
    assert wm.guests == {}
    assert wm.docker_containers == {}
    assert wm.services == {}
    assert wm.operations == {}
    assert wm.workers == {}
    assert wm.models == {}
    assert wm.teammates == {}
    assert wm.teams == {}
    assert wm.missions == {}
    assert wm.policies == {}
    assert wm.desired_state == {}
    assert wm.actual_state == {}

    wm.nodes = {'node1': {'type': 'proxmox'}}
    wm.save()
    wm.load()
    assert wm.nodes == {'node1': {'type': 'proxmox'}}

def test_world_model_collect_entities():
    wm = WorldModel()
    wm.collect_nodes()
    assert wm.nodes != {}

    wm.collect_guests()
    assert wm.guests != {}

    wm.collect_docker_containers()
    assert wm.docker_containers != {}

    wm.collect_services()
    assert wm.services != {}

    wm.collect_operations()
    assert wm.operations != {}

    wm.collect_workers()
    assert wm.workers != {}

    wm.collect_models()
    assert wm.models != {}

    wm.collect_teammates()
    assert wm.teammates != {}

    wm.collect_teams()
    assert wm.teams != {}

    wm.collect_missions()
    assert wm.missions != {}

    wm.collect_policies()
    assert wm.policies != {}
