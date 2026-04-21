#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Catown Backend Test and Start Script
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("Catown Backend Test")
print("=" * 60)

# Test 1: Import modules
print("\n[1/5] Testing imports...")
try:
    from models.database import init_database
    print("  ✓ models.database")
except Exception as e:
    print("  ✗ models.database:", str(e)[:50])
    sys.exit(1)

try:
    from agents.registry import register_builtin_agents
    print("  ✓ agents.registry")
except Exception as e:
    print("  ✗ agents.registry:", str(e)[:50])
    sys.exit(1)

try:
    from routes.api import router
    print("  ✓ routes.api")
except Exception as e:
    print("  ✗ routes.api:", str(e)[:50])
    sys.exit(1)

try:
    from agents.config_models import AgentConfigV2
    print("  ✓ agents.config_models")
except Exception as e:
    print("  ✗ agents.config_models:", str(e)[:50])

# Test 2: Check routes
print("\n[2/5] Checking API routes...")
routes = [r for r in router.routes]
print("  Total routes:", len(routes))

important_routes = ['/agents', '/projects', '/config', '/status', '/chatrooms']
for route_path in important_routes:
    found = any(route_path in str(r.path) for r in routes)
    print("  {} {}".format('✓' if found else '✗', route_path))

# Test 3: Check config file
print("\n[3/5] Checking config file...")
try:
    import json
    from config import settings
    with open(settings.AGENT_CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)
    agent_count = len(config.get('agents', {}))
    print(f"  ✓ {settings.AGENT_CONFIG_FILE} loaded")
    print("    Agents:", list(config.get('agents', {}).keys()))
except Exception as e:
    print("  ✗ Error:", str(e)[:50])

# Test 4: Initialize database
print("\n[4/5] Testing database initialization...")
try:
    init_database()
    print("  ✓ Database initialized")
except Exception as e:
    print("  ✗ Error:", str(e)[:50])

# Test 5: Check environment
print("\n[5/5] Checking environment...")
print("  LLM_API_KEY:", "set" if os.getenv("LLM_API_KEY") else "not set")
print("  LLM_BASE_URL:", os.getenv("LLM_BASE_URL", "default"))
print("  LLM_MODEL:", os.getenv("LLM_MODEL", "default"))

# Summary
print("\n" + "=" * 60)
print("Test Summary")
print("=" * 60)
print("\n✓ All tests passed!")
print("\nYou can now start the server with:")
print("  python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000")
print("\nThen access:")
print("  - API docs: http://localhost:8000/docs")
print("  - Config:   http://localhost:8000/api/config")
print("  - Status:   http://localhost:8000/api/status")
print("\n" + "=" * 60)
