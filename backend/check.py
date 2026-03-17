#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Catown Code Check Script
"""
import os
import sys

print("=" * 60)
print("Catown Code Check")
print("=" * 60)

# 1. Check directory structure
print("\n1. Checking directory structure...")
required_dirs = [
    'agents',
    'chatrooms',
    'llm',
    'models',
    'routes',
    'configs',
    'examples',
    'tests'
]

for d in required_dirs:
    exists = os.path.exists(d)
    status = "[OK]" if exists else "[MISSING]"
    print("   {} {}/".format(status, d))

# 2. Check key files
print("\n2. Checking key files...")
required_files = [
    'main.py',
    'requirements.txt',
    'agents/core.py',
    'agents/config_models.py',
    'agents/config_manager.py',
    'models/database.py',
    'routes/api.py',
    'configs/agents.json'
]

missing_files = []
for f in required_files:
    exists = os.path.exists(f)
    if not exists:
        missing_files.append(f)
    status = "[OK]" if exists else "[MISSING]"
    print("   {} {}".format(status, f))

# 3. Create __init__.py files
print("\n3. Creating __init__.py files...")
init_dirs = ['agents', 'chatrooms', 'llm', 'models', 'routes', 'tools', 'examples', 'tests']
for d in init_dirs:
    init_file = os.path.join(d, '__init__.py')
    if not os.path.exists(init_file):
        if os.path.exists(d):
            with open(init_file, 'w') as f:
                f.write('"""{} module"""\n'.format(d))
            print("   [CREATED] {}".format(init_file))
        else:
            print("   [SKIP] Directory not found: {}/".format(d))

# 4. Check config file
print("\n4. Checking config file...")
try:
    import json
    with open('configs/agents.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    agent_count = len(config.get('agents', {}))
    print("   [OK] configs/agents.json ({} agents)".format(agent_count))
except Exception as e:
    print("   [ERROR] configs/agents.json: {}".format(str(e)[:50]))

# 5. Check .env file
print("\n5. Checking environment...")
from pathlib import Path
env_file = Path('.env')
if env_file.exists():
    print("   [OK] .env file exists")
    with open('.env', 'r', encoding='utf-8') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
        print("   - Config items: {}".format(len(lines)))
else:
    print("   [WARNING] .env file not found")

# 6. Code statistics
print("\n6. Code statistics...")
import glob

py_files = glob.glob('**/*.py', recursive=True)
py_files = [f for f in py_files if 'node_modules' not in f and '__pycache__' not in f]

total_lines = 0
for f in py_files:
    try:
        with open(f, 'r', encoding='utf-8') as file:
            lines = len(file.readlines())
            total_lines += lines
    except:
        pass

print("   - Python files: {}".format(len(py_files)))
print("   - Total lines: {}".format(total_lines))

# 7. Summary
print("\n" + "=" * 60)
print("Summary")
print("=" * 60)

if missing_files:
    print("\nMissing files ({}):".format(len(missing_files)))
    for f in missing_files:
        print("   - {}".format(f))
else:
    print("\nAll key files exist!")

print("\nNext steps:")
print("1. Install: pip install -r requirements.txt")
print("2. Configure: Edit .env file with your API key")
print("3. Start: uvicorn main:app --reload --host 0.0.0.0 --port 8000")
print("4. Access: http://localhost:8000/docs")

print("\n" + "=" * 60)
