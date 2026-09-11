import json
from core.proxmox_registry import ProxmoxRegistry
from core.docker_observer import DockerObserver
from memory.workers import _MEMORY_DIR, WORLD_PATH
from ai_provider import registered_providers
from kai_missions import missions
from policy import policies

class WorldModel:
    def __init__(self):
        self.entities = {
            'nodes': {},
            'guests': {},
            'docker_containers': {},
            'services': {},
            'operations': {},
            'workers': {},
            'models': {},
            'teammates': {},
            'teams': {},
            'missions': {},
            'policies': {}
        }

    def collect_entities(self):
        self.collect_nodes()
        self.collect_guests()
        self.collect_docker_containers()
        self.collect_services()
        self.collect_operations()
        self.collect_workers()
        self.collect_models()
        self.collect_teammates()
        self.collect_teams()
        self.collect_missions()
        self.collect_policies()

    def collect_nodes(self):
        # Existing implementation
        pass

    def collect_guests(self):
        # Existing implementation
        pass

    def collect_docker_containers(self):
        # Existing implementation
        pass

    def collect_services(self):
        # Existing implementation
        pass

    def collect_operations(self):
        # Existing implementation
        pass

    def collect_workers(self):
        # Existing implementation
        pass

    def collect_models(self):
        for provider in registered_providers:
            for model in provider.get_models():
                self.entities['models'][model.id] = model

    def collect_teammates(self):
        # Stub implementation
        pass

    def collect_teams(self):
        # Stub implementation
        pass

    def collect_missions(self):
        for mission in missions:
            self.entities['missions'][mission.id] = mission

    def collect_policies(self):
        for policy in policies:
            self.entities['policies'][policy.id] = policy

    def snapshot(self):
        with open(WORLD_PATH, 'r') as f:
            return json.load(f)

    def impact_of(self, entity_type):
        # Existing implementation
        pass

    def diff(self, snapshot1, snapshot2):
        diff = {}
        for entity_type in self.entities:
            if entity_type in snapshot1 and entity_type in snapshot2:
                diff[entity_type] = {
                    'added': set(snapshot2[entity_type].keys()) - set(snapshot1[entity_type].keys()),
                    'removed': set(snapshot1[entity_type].keys()) - set(snapshot2[entity_type].keys()),
                    'changed': {
                        key: (snapshot1[entity_type][key], snapshot2[entity_type][key])
                        for key in set(snapshot1[entity_type].keys()) & set(snapshot2[entity_type].keys())
                        if snapshot1[entity_type][key] != snapshot2[entity_type][key]
                    }
                }
        return diff
