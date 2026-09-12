import hashlib
import os
import re

def get_expected_sha256():
    with open('kai-mobile-v31.md', 'r') as file:
        content = file.read()
        match = re.search(r'artifact_sha256: ([a-fA-F0-9]{64})', content)
        if match:
            return match.group(1)
        else:
            raise ValueError('Expected artifact_sha256 not found in kai-mobile-v31.md')

def get_current_sha256():
    apk_path = find_apk_path()
    with open(apk_path, 'rb') as file:
        sha256 = hashlib.sha256(file.read()).hexdigest()
    return sha256

def find_apk_path():
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith('.apk'):
                return os.path.join(root, file)
    raise FileNotFoundError('APK file not found in the project')

def is_deterministic():
    build_files = ['build.gradle', 'Dockerfile', 'pom.xml']
    for file in build_files:
        with open(file, 'r') as f:
            content = f.read()
            if 'latest' in content or '*' in content:
                return False
    return True
