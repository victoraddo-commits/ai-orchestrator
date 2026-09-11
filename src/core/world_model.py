import json
from pathlib import Path
from typing import Dict, List, Optional

from core.proxmox_registry import ProxmoxRegistry
from core.docker_observer import DockerObserver
from ai_provider import registered_providers
from kai_missions import missions
from policy import policies

_MEMORY_DIR = Path("memory")
WORLD_PATH = _MEMORY_DIR / "world_model.json"

class WorldModel:
    def __init__(self):
        self.nodes: Dict[str, Dict] = {}
        self.guests: Dict[str, Dict] = {}
        self.docker_containers: Dict[str, Dict] = {}
        self.services: Dict[str, Dict] = {}
        self.operations: Dict[str, Dict] = {}
        self.workers: Dict[str, Dict] = {}
        self.models: Dict[str, Dict] = {}
        self.teammates: Dict[str, Dict] = {}
        self.teams: Dict[str, Dict] = {}
        self.missions: Dict[str, Dict] = {}
        self.policies: Dict[str, Dict] = {}
        self.desired_state: Dict[str, Dict] = {}
        self.actual_state: Dict[str, Dict] = {}

    def load(self):
        if WORLD_PATH.exists():
            with open(WORLD_PATH, 'r') as f:
                data = json.load(f)
                self.nodes = data.get('nodes', {})
                self.guests = data.get('guests', {})
                self.docker_containers = data.get('docker_containers', {})
                self.services = data.get('services', {})
                self.operations = data.get('operations', {})
                self.workers = data.get('workers', {})
                self.models = data.get('models', {})
                self.teammates = data.get('teammates', {})
                self.teams = data.get('teams', {})
                self.missions = data.get('missions', {})
                self.policies = data.get('policies', {})
                self.desired_state = data.get('desired_state', {})
                self.actual_state = data.get('actual_state', {})

    def save(self):
        data = {
            'nodes': self.nodes,
            'guests': self.guests,
            'docker_containers': self.docker_containers,
            'services': self.services,
            'operations': self.operations,
            'workers': self.workers,
            'models': self.models,
            'teammates': self.teammates,
            'teams': self.teams,
            'missions': self.missions,
            'policies': self.policies,
            'desired_state': self.desired_state,
            'actual_state': self.actual_state
        }
        with open(WORLD_PATH, 'w') as f:
            json.dump(data, f, indent=4)

    def collect_nodes(self):
        # Collect nodes from ProxmoxRegistry
        self.nodes = ProxmoxRegistry.collect_nodes()

    def collect_guests(self):
        # Collect guests from ProxmoxRegistry
        self.guests = ProxmoxRegistry.collect_guests()

    def collect_docker_containers(self):
        # Collect docker containers from DockerObserver
        self.docker_containers = DockerObserver.collect_docker_containers()

    def collect_services(self):
        # Collect services from ProxmoxRegistry
        self.services = ProxmoxRegistry.collect_services()

    def collect_operations(self):
        # Collect operations from ProxmoxRegistry
        self.operations = ProxmoxRegistry.collect_operations()

    def collect_workers(self):
        # Collect workers from ProxmoxRegistry
        self.workers = ProxmoxRegistry.collect_workers()

    def collect_models(self):
        # Collect models from registered_providers
        self.models = {provider.name: provider.get_model() for provider in registered_providers}

    def collect_teammates(self):
        # Collect teammates from teammate_factory
        self.teammates = {}  # Stub implementation

    def collect_teams(self):
        # Collect teams from teammate_factory
        self.teams = {}  # Stub implementation

    def collect_missions(self):
        # Collect missions from kai_missions
        self.missions = {mission.name: mission.get_details() for mission in missions}

    def collect_policies(self):
        # Collect policies from policy.json
        self.policies = {policy.name: policy.get_details() for policy in policies}

    def snapshot(self):
        self.save()

    def impact_of(self, entity_id: str) -> List[str]:
        # Implement impact traversal logic
        pass

    def diff(self, previous_state: Dict[str, Dict]) -> Dict[str, Dict]:
        # Generate diff between current and previous state
        pass
