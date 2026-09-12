import os
import json
from unittest.mock import patch
from ai_orchestrator.discovery_service import verify_kai_context

def test_verify_kai_context():
    # Mock the project root directory
    project_root = 'path/to/project'
    
    # Mock the discovery service to return a mock manifest
    mock_manifest = {
        'files': {
            'CLAUDE.md': {'path': 'path/to/CLAUDE.md', 'size': 1024, 'last_modified': '2023-04-01T12:00:00'},
            'AGENTS.md': {'path': 'path/to/AGENTS.md', 'size': 2048, 'last_modified': '2023-04-02T12:00:00'}
        },
        'directories': {
            'skills': {
                'claude': {'path': 'path/to/skills/claude'},
                'opencode': {'path': 'path/to/skills/opencode'},
                'kai': {'path': 'path/to/skills/kai'}
            },
            'superpowers': {'path': 'path/to/superpowers'}
        }
    }
    
    with patch('ai_orchestrator.discovery_service.os.listdir') as mock_listdir:
        mock_listdir.return_value = ['skills', 'superpowers', 'CLAUDE.md', 'AGENTS.md']
        
        with patch('ai_orchestrator.discovery_service.os.path.isdir') as mock_isdir:
            mock_isdir.side_effect = [True, True, False, False]
            
            with patch('ai_orchestrator.discovery_service.os.path.getsize') as mock_getsize:
                mock_getsize.side_effect = [1024, 2048]
                
                with patch('ai_orchestrator.discovery_service.os.path.getmtime') as mock_getmtime:
                    mock_getmtime.side_effect = ['2023-04-01T12:00:00', '2023-04-02T12:00:00']
                    
                    with patch('ai_orchestrator.discovery_service.os.path.join') as mock_join:
                        mock_join.side_effect = [
                            'path/to/project/skills',
                            'path/to/project/superpowers',
                            'path/to/project/CLAUDE.md',
                            'path/to/project/AGENTS.md'
                        ]
                        
                        manifest = verify_kai_context(project_root)
                        assert manifest == mock_manifest
