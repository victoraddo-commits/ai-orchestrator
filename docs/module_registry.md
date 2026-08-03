# Module Auto-Registry for Kai Command Center

This document describes the module auto-registration feature implemented for Kai Command Center (Phase 19A).

## Module Descriptor Format

Each module must provide a JSON descriptor file in the `config/modules/` directory. The descriptor must include the following fields:

- `name` (string, required): Unique identifier for the module
- `version` (string, required): Version of the module
- `description` (string, required): Brief description of what the module does
- `endpoints` (array of strings, optional): List of API endpoints the module exposes
- `capabilities` (array of strings, optional): List of capabilities this module provides
- `dependencies` (array of strings, optional): List of module names this module depends on

### Example Module Descriptor

```json
{
  "name": "my-custom-module",
  "version": "1.0.0",
  "description": "A custom module for special functionality",
  "endpoints": [
    "/api/my-module/endpoint1",
    "/api/my-module/endpoint2"
  ],
  "capabilities": [
    "custom-functionality",
    "special-processing"
  ],
  "dependencies": [
    "ai-workforce",
    "approval-center"
  ]
}
```

## Integration Pattern

Modules follow the existing `ai_provider.py` dynamic discovery pattern - they are entirely decoupled and register themselves simply by dropping a JSON descriptor into `config/modules/` without requiring hardcoded imports or manual router wiring in core files.

## API Endpoint

The new `/api/modules` endpoint returns registered modules in the following format:

```json
{
  "modules": {
    "module-name": {
      "name": "module-name",
      "version": "1.0.0",
      "description": "Module description",
      "endpoints": ["/api/endpoint"],
      "capabilities": ["capability1"],
      "dependencies": [],
      "source_file": "module-name.json"
    }
  }
}
```