"""Module auto-registry for Kai Command Center.

Phase 19A: every new module self-registers at startup via a simple JSON
descriptor dropped into config/modules/. No manual wiring needed --
modules appear automatically in the Command Center workforce panel.

Follows the existing ai_provider.py dynamic-discovery pattern: modules
are entirely decoupled, never hardcoded, and register themselves simply
by placing a JSON descriptor in the designated directory.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODULES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "config", "modules"
)

_REQUIRED_FIELDS = frozenset(["name", "version", "description"])
_OPTIONAL_FIELDS = frozenset(["endpoints", "capabilities", "dependencies", "cc"])


class ModuleRegistry:
    def __init__(self, config_dir=None):
        self._config_dir = config_dir or DEFAULT_MODULES_DIR
        self._modules = {}

    def load_modules(self, config_dir=None):
        config_dir = config_dir or self._config_dir
        config_path = Path(config_dir)

        if not config_path.is_dir():
            logger.warning("Module config directory not found: %s -- no modules registered", config_dir)
            self._modules = {}
            return self._modules

        loaded = {}
        for entry in sorted(config_path.iterdir()):
            if not entry.is_file() or entry.suffix != ".json":
                continue

            try:
                raw = entry.read_text()
            except OSError as exc:
                logger.warning("Cannot read module descriptor %s: %s", entry.name, exc)
                continue

            try:
                descriptor = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid JSON in module descriptor %s: %s", entry.name, exc)
                continue

            if not isinstance(descriptor, dict):
                logger.warning("Module descriptor %s is not a JSON object -- skipping", entry.name)
                continue

            missing = _REQUIRED_FIELDS - descriptor.keys()
            if missing:
                logger.warning(
                    "Module descriptor %s missing required fields: %s -- skipping",
                    entry.name, ", ".join(sorted(missing)),
                )
                continue

            name = descriptor["name"]
            version = descriptor["version"]
            description = descriptor["description"]

            if not isinstance(name, str) or not name.strip():
                logger.warning("Module descriptor %s has invalid 'name' field -- skipping", entry.name)
                continue

            if name in loaded:
                logger.warning(
                    "Duplicate module name %r (descriptor %s) -- keeping first, skipping second",
                    name, entry.name,
                )
                continue

            cc = descriptor.get("cc")
            if not isinstance(cc, dict):
                cc = {}
            module = {
                "name": name.strip(),
                "version": str(version),
                "description": str(description),
                "endpoints": descriptor.get("endpoints", []),
                "capabilities": descriptor.get("capabilities", []),
                "dependencies": descriptor.get("dependencies", []),
                "cc": cc,
                "source_file": entry.name,
            }
            loaded[name] = module

        self._modules = loaded
        logger.info("Loaded %d module(s) from %s", len(loaded), config_dir)
        return self._modules

    def get_registered_modules(self):
        if not self._modules:
            self.load_modules()
        return dict(self._modules)

    def get_cc_modules(self):
        """Modules that declare a ``cc`` block and therefore own a CC tab."""
        out = []
        for name, module in self.get_registered_modules().items():
            cc = module.get("cc") or {}
            tab = cc.get("tab")
            if not tab:
                continue
            out.append({
                "name": name,
                "tab": tab,
                "title": cc.get("title") or name,
                "status": cc.get("status", "live"),
                "health": cc.get("health"),
                "description": module.get("description", ""),
            })
        return out

    def register_module(self, name, version, description,
                        endpoints=None, capabilities=None, dependencies=None,
                        cc=None):
        module = {
            "name": name,
            "version": version,
            "description": description,
            "endpoints": endpoints or [],
            "capabilities": capabilities or [],
            "dependencies": dependencies or [],
            "cc": cc or {},
        }
        self._modules[name] = module
        return module


_module_registry = ModuleRegistry()


def load_modules(config_dir=None):
    return _module_registry.load_modules(config_dir)


def get_registered_modules():
    return _module_registry.get_registered_modules()


def get_cc_modules():
    return _module_registry.get_cc_modules()


def register_module(name, version, description,
                    endpoints=None, capabilities=None, dependencies=None,
                    cc=None):
    return _module_registry.register_module(
        name, version, description,
        endpoints=endpoints, capabilities=capabilities, dependencies=dependencies,
        cc=cc,
    )


def reset():
    _module_registry._modules = {}
