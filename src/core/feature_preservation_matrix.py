import sys
import os
import json
import inspect
import glob
import re
from typing import List, Dict, Any

class FeatureDiscoveryEngine:
    def __init__(self, project_root: str = "."):
        self.project_root = project_root
        self.features = []
        self.classified_features = []

    def run_discovery(self) -> List[Dict]:
        """Orchestrates the scanning and classification pipeline."""
        self._scan_ai_providers()
        self._scan_registries()
        self._scan_memory_state()
        self._scan_external_integrations()
        
        # Deduplication logic before classification
        self._deduplicate_features()
        
        self.classified_features = self._classify_features(self.features)
        return self.classified_features

    def _scan_ai_providers(self):
        """Extracts AI providers from core/ai_provider.py."""
        ai_provider_path = os.path.join(self.project_root, 'core', 'ai_provider.py')
        with open(ai_provider_path, 'r') as file:
            ai_provider_module = inspect.getmodule(inspect.getsourcefile(ai_provider_path))
            providers = [name for name, obj in inspect.getmembers(ai_provider_module) if inspect.isclass(obj) and issubclass(obj, ai_provider_module.Provider)]
            for provider in providers:
                provider_instance = provider()
                self.features.append({
                    'name': provider.__name__,
                    'category': 'AI Provider',
                    'status': 'VERIFIED' if provider_instance.is_active else 'BROKEN',
                    'action': 'PRESERVE' if provider_instance.is_active else 'RECOVER',
                    'source_files': [ai_provider_path],
                    'last_seen': '2023-10-27'
                })

    def _scan_registries(self):
        """Extracts items from Capability, Service, and Module registries."""
        registries = [
            ('core', 'capability_registry.py', 'Capability'),
            ('core', 'service_registry.py', 'Service'),
            ('core', 'module_registry.py', 'Module')
        ]
        for registry_dir, registry_file, category in registries:
            registry_path = os.path.join(self.project_root, registry_dir, registry_file)
            with open(registry_path, 'r') as file:
                registry_module = inspect.getmodule(inspect.getsourcefile(registry_path))
                registry_instance = registry_module.Registry()
                for key, value in registry_instance.get_all().items():
                    self.features.append({
                        'name': key,
                        'category': category,
                        'status': 'VERIFIED',
                        'action': 'PRESERVE',
                        'source_files': [registry_path],
                        'last_seen': '2023-10-27'
                    })

    def _scan_memory_state(self):
        """Extracts entities from memory/*.json files."""
        memory_path = os.path.join(self.project_root, 'memory')
        json_files = glob.glob(os.path.join(memory_path, '*.json'))
        for json_file in json_files:
            with open(json_file, 'r') as file:
                data = json.load(file)
                for key in data:
                    self.features.append({
                        'name': key,
                        'category': 'State Entity',
                        'status': 'VERIFIED',
                        'action': 'PRESERVE',
                        'source_files': [json_file],
                        'last_seen': '2023-10-27'
                    })

    def _scan_external_integrations(self):
        """Detects Telegram bots and API endpoints."""
        core_path = os.path.join(self.project_root, 'core')
        python_files = glob.glob(os.path.join(core_path, '*.py'))
        for python_file in python_files:
            with open(python_file, 'r') as file:
                content = file.read()
                if 'import telegram' in content or re.search(r'@bot\.command', content):
                    self.features.append({
                        'name': os.path.basename(python_file),
                        'category': 'Telegram Integration',
                        'status': 'VERIFIED',
                        'action': 'PRESERVE',
                        'source_files': [python_file],
                        'last_seen': '2023-10-27'
                    })
                if re.search(r'@app\.get|@app\.post|@router\.get', content):
                    self.features.append({
                        'name': os.path.basename(python_file),
                        'category': 'API Endpoint',
                        'status': 'VERIFIED',
                        'action': 'PRESERVE',
                        'source_files': [python_file],
                        'last_seen': '2023-10-27'
                    })

    def _deduplicate_features(self):
        """Identifies features appearing in multiple sources."""
        feature_dict = {}
        for feature in self.features:
            if feature['name'] in feature_dict:
                feature_dict[feature['name']].append(feature)
            else:
                feature_dict[feature['name']] = [feature]
        self.features = [features[0] for features in feature_dict.values()]

    def _classify_features(self, raw_features: List[Dict]) -> List[Dict]:
        """Applies Status and Action logic."""
        classified_features = []
        for feature in raw_features:
            if feature['status'] == 'VERIFIED' and len(feature_dict[feature['name']]) == 1:
                feature['action'] = 'PRESERVE'
            elif feature['status'] == 'BROKEN':
                feature['action'] = 'RECOVER'
            elif feature['status'] == 'DECLARED' and len(feature_dict[feature['name']]) == 1:
                feature['action'] = 'DEPRECATE'
            elif feature['status'] == 'MISSING':
                feature['action'] = 'COMPLETE'
            elif feature['status'] == 'DUPLICATE':
                feature['action'] = 'DEPRECATE'
            classified_features.append(feature)
        return classified_features

    def save_matrix(self):
        """Writes the final matrix to memory/feature_matrix.json."""
        matrix_path = os.path.join(self.project_root, 'memory', 'feature_matrix.json')
        with open(matrix_path, 'w') as file:
            json.dump(self.classified_features, file, indent=4)
