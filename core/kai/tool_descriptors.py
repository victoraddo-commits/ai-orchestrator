# Source Generated with Decompyle++
# File: tool_descriptors.cpython-312.pyc (Python 3.12)

'''
Pre-built MCP-style tool descriptors for all KAI capabilities.

Each descriptor has:
  - name: unique tool identifier
  - description: human-readable summary
  - input_schema: JSON Schema for tool parameters
  - output_schema: JSON Schema for tool output
  - risk_level: "low" | "medium" | "high" | "critical"
  - capabilities_required: list of capability names needed to use this tool
'''
TOOL_DESCRIPTORS: list[dict] = [
    {
        'name': 'inventory.get',
        'description': 'Get full inventory of all known infrastructure entities (servers, containers, services, networks)',
        'input_schema': {
            'type': 'object',
            'properties': { },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'servers': {
                    'type': 'array' },
                'containers': {
                    'type': 'array' },
                'services': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.inventory.read'] },
    {
        'name': 'server.inspect',
        'description': 'Inspect a specific server for detailed information',
        'input_schema': {
            'type': 'object',
            'properties': {
                'server_id': {
                    'type': 'string',
                    'description': 'Server hostname or ID' } },
            'required': [
                'server_id'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'server': {
                    'type': 'object' },
                'status': {
                    'type': 'string' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.inventory.read'] },
    {
        'name': 'vault.request',
        'description': 'Request secrets from Kai Vault (API keys, credentials)',
        'input_schema': {
            'type': 'object',
            'properties': {
                'secret_path': {
                    'type': 'string',
                    'description': 'Vault path for the secret' },
                'purpose': {
                    'type': 'string',
                    'description': 'Purpose/justification for the request' } },
            'required': [
                'secret_path'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'value': {
                    'type': 'string' } } },
        'risk_level': 'critical',
        'capabilities_required': [
            'kai.vault.request'] },
    {
        'name': 'notify.send',
        'description': 'Send a notification to operators via Telegram',
        'input_schema': {
            'type': 'object',
            'properties': {
                'message': {
                    'type': 'string' },
                'severity': {
                    'type': 'string',
                    'enum': [
                        'CRITICAL',
                        'IMPORTANT',
                        'INFO'] } },
            'required': [
                'message'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'sent': {
                    'type': 'boolean' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.notify.send'] },
    {
        'name': 'workforce.list',
        'description': 'List all registered workers in the workforce system',
        'input_schema': {
            'type': 'object',
            'properties': {
                'capability': {
                    'type': 'string',
                    'description': 'Filter by capability' } },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'workers': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.workforce.read'] },
    {
        'name': 'workforce.register',
        'description': 'Register a new worker in the workforce system',
        'input_schema': {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string' },
                'capabilities': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } },
                'model': {
                    'type': 'string' } },
            'required': [
                'name',
                'capabilities'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'worker_id': {
                    'type': 'string' } } },
        'risk_level': 'medium',
        'capabilities_required': [
            'kai.workforce.write'] },
    {
        'name': 'docker.ps',
        'description': 'List all Docker containers across all hosts',
        'input_schema': {
            'type': 'object',
            'properties': {
                'host': {
                    'type': 'string',
                    'description': 'Specific host to query (optional)' } },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'containers': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.docker.read'] },
    {
        'name': 'docker.start',
        'description': 'Start a Docker container',
        'input_schema': {
            'type': 'object',
            'properties': {
                'container_id': {
                    'type': 'string' },
                'host': {
                    'type': 'string' } },
            'required': [
                'container_id'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'started': {
                    'type': 'boolean' } } },
        'risk_level': 'medium',
        'capabilities_required': [
            'kai.docker.write'] },
    {
        'name': 'docker.stop',
        'description': 'Stop a Docker container',
        'input_schema': {
            'type': 'object',
            'properties': {
                'container_id': {
                    'type': 'string' },
                'host': {
                    'type': 'string' } },
            'required': [
                'container_id'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'stopped': {
                    'type': 'boolean' } } },
        'risk_level': 'medium',
        'capabilities_required': [
            'kai.docker.write'] },
    {
        'name': 'proxmox.vm.list',
        'description': 'List all Proxmox VMs and containers',
        'input_schema': {
            'type': 'object',
            'properties': { },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'vms': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.proxmox.read'] },
    {
        'name': 'proxmox.vm.start',
        'description': 'Start a Proxmox VM or container',
        'input_schema': {
            'type': 'object',
            'properties': {
                'vmid': {
                    'type': 'string' },
                'node': {
                    'type': 'string' } },
            'required': [
                'vmid'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'started': {
                    'type': 'boolean' } } },
        'risk_level': 'medium',
        'capabilities_required': [
            'kai.proxmox.write'] },
    {
        'name': 'mission.create',
        'description': 'Create a new background mission with an objective',
        'input_schema': {
            'type': 'object',
            'properties': {
                'objective': {
                    'type': 'string' },
                'models': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } },
                'tools': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } },
                'constraints': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } },
                'success_criteria': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } },
                'risk': {
                    'type': 'string',
                    'enum': [
                        'low',
                        'medium',
                        'high'] } },
            'required': [
                'objective'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'mission_id': {
                    'type': 'string' },
                'status': {
                    'type': 'string' } } },
        'risk_level': 'medium',
        'capabilities_required': [
            'kai.mission.create'] },
    {
        'name': 'mission.list',
        'description': 'List missions, optionally filtered by status',
        'input_schema': {
            'type': 'object',
            'properties': {
                'status': {
                    'type': 'string' },
                'limit': {
                    'type': 'integer' } },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'missions': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.mission.read'] },
    {
        'name': 'mission.get',
        'description': 'Get full detail of a specific mission',
        'input_schema': {
            'type': 'object',
            'properties': {
                'mission_id': {
                    'type': 'string' } },
            'required': [
                'mission_id'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'mission': {
                    'type': 'object' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.mission.read'] },
    {
        'name': 'mission.steer',
        'description': 'Apply a steering action to a mission (pause, resume, stop, redirect, replan)',
        'input_schema': {
            'type': 'object',
            'properties': {
                'mission_id': {
                    'type': 'string' },
                'action': {
                    'type': 'string',
                    'enum': [
                        'pause',
                        'resume',
                        'stop',
                        'redirect',
                        'replan'] },
                'note': {
                    'type': 'string' },
                'new_objective': {
                    'type': 'string' },
                'new_plan': {
                    'type': 'array',
                    'items': {
                        'type': 'string' } } },
            'required': [
                'mission_id',
                'action'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'mission': {
                    'type': 'object' } } },
        'risk_level': 'high',
        'capabilities_required': [
            'kai.mission.write'] },
    {
        'name': 'health.get',
        'description': 'Get current health observatory data',
        'input_schema': {
            'type': 'object',
            'properties': { },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'health': {
                    'type': 'object' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.health.read'] },
    {
        'name': 'approval.create',
        'description': 'Create a new approval request for a human-gated action',
        'input_schema': {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string' },
                'reason': {
                    'type': 'string' },
                'risk_level': {
                    'type': 'string' },
                'channel': {
                    'type': 'string' } },
            'required': [
                'action',
                'reason'] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'approval_id': {
                    'type': 'string' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.approval.create'] },
    {
        'name': 'approval.list',
        'description': 'List pending approval requests',
        'input_schema': {
            'type': 'object',
            'properties': {
                'status': {
                    'type': 'string',
                    'enum': [
                        'pending',
                        'approved',
                        'rejected'] } },
            'required': [] },
        'output_schema': {
            'type': 'object',
            'properties': {
                'approvals': {
                    'type': 'array' } } },
        'risk_level': 'low',
        'capabilities_required': [
            'kai.approval.read'] }]
