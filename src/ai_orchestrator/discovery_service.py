import os
import json

def verify_kai_context(project_root):
    manifest = {
        'files': {},
        'directories': {}
    }
    
    # Check for CLAUDE.md and AGENTS.md
    for file_name in ['CLAUDE.md', 'AGENTS.md']:
        file_path = os.path.join(project_root, file_name)
        if os.path.isfile(file_path):
            manifest['files'][file_name] = {
                'path': file_path,
                'size': os.path.getsize(file_path),
                'last_modified': os.path.getmtime(file_path)
            }
    
    # Scan skills directory
    skills_dir = os.path.join(project_root, 'skills')
    if os.path.isdir(skills_dir):
        for skill_type in ['claude', 'opencode', 'kai']:
            skill_path = os.path.join(skills_dir, skill_type)
            if os.path.isdir(skill_path):
                manifest['directories']['skills'][skill_type] = {
                    'path': skill_path
                }
    
    # Scan superpowers directory
    superpowers_dir = os.path.join(project_root, 'superpowers')
    if os.path.isdir(superpowers_dir):
        manifest['directories']['superpowers'] = {
            'path': superpowers_dir
        }
    
    return manifest
