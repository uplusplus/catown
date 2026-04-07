"""
Pytest 共享 fixtures
"""
import pytest
import os
import sys

# 确保 backend 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """所有测试自动设置环境变量"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_URL", db_path)
