#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Catown 代码检查和诊断脚本
"""
import os
import sys

print("=" * 60)
print("Catown 代码检查")
print("=" * 60)

# 1. 检查目录结构
print("\n1. 检查目录结构...")
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
    print(f"   {'✓' if exists else '✗'} {d}/")

# 2. 检查关键文件
print("\n2. 检查关键文件...")
required_files = [
    'main.py',
    'requirements.txt',
    'agents/__init__.py',
    'agents/core.py',
    'agents/config_models.py',
    'agents/config_manager.py',
    'agents/registry.py',
    'models/database.py',
    'routes/api.py',
    'routes/websocket.py',
    'configs/agents.json'
]

missing_files = []
for f in required_files:
    exists = os.path.exists(f)
    if not exists:
        missing_files.append(f)
    print(f"   {'✓' if exists else '✗'} {f}")

# 3. 创建缺失的 __init__.py 文件
print("\n3. 创建缺失的 __init__.py 文件...")
init_dirs = ['agents', 'chatrooms', 'llm', 'models', 'routes', 'tools', 'examples', 'tests']
for d in init_dirs:
    init_file = os.path.join(d, '__init__.py')
    if not os.path.exists(init_file):
        if os.path.exists(d):
            with open(init_file, 'w') as f:
                f.write(f'"""{d} module"""\n')
            print(f"   ✓ 创建 {init_file}")
        else:
            print(f"   ✗ 目录不存在: {d}/")

# 4. 检查导入
print("\n4. 检查模块导入...")
import_tests = [
    ('models.database', 'init_database'),
    ('agents.core', 'Agent'),
    ('agents.config_models', 'AgentConfigV2'),
    ('agents.config_manager', 'AgentConfigManager'),
    ('routes.websocket', 'websocket_manager'),
]

for module, attr in import_tests:
    try:
        mod = __import__(module, fromlist=[attr])
        getattr(mod, attr)
        print(f"   ✓ {module}.{attr}")
    except Exception as e:
        print(f"   ✗ {module}.{attr}: {str(e)[:50]}")

# 5. 检查配置文件
print("\n5. 检查配置文件...")
try:
    import json
    with open('configs/agents.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    agent_count = len(config.get('agents', {}))
    print(f"   ✓ configs/agents.json (包含 {agent_count} 个 Agent)")
except Exception as e:
    print(f"   ✗ configs/agents.json: {e}")

# 6. 检查环境变量
print("\n6. 检查环境配置...")
from pathlib import Path
env_file = Path('.env')
if env_file.exists():
    print(f"   ✓ .env 文件存在")
    with open('.env', 'r') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
        print(f"   - 配置项数量: {len(lines)}")
else:
    print(f"   ✗ .env 文件不存在")

# 7. 代码统计
print("\n7. 代码统计...")
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

print(f"   - Python 文件: {len(py_files)}")
print(f"   - 总代码行数: {total_lines}")

# 8. 总结
print("\n" + "=" * 60)
print("检查总结")
print("=" * 60)

if missing_files:
    print(f"\n⚠ 缺失文件 ({len(missing_files)}):")
    for f in missing_files:
        print(f"   - {f}")
else:
    print("\n✓ 所有关键文件都存在")

print("\n下一步:")
print("1. 运行: pip install -r requirements.txt")
print("2. 配置: 编辑 .env 文件设置 API Key")
print("3. 启动: uvicorn main:app --reload")
print("4. 访问: http://localhost:8000/docs")

print("\n" + "=" * 60)
