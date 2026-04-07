# -*- coding: utf-8 -*-
"""
统一配置模块

LLM 配置已迁移至 agents.json（per-Agent 独立配置）。
本模块仅保留基础设施配置。
"""
import os
from pathlib import Path


class Settings:
    """全局配置（基础设施级别，不含 LLM）"""
    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "data/catown.db")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Agent 配置文件路径
    AGENT_CONFIG_FILE: str = os.getenv("AGENT_CONFIG_FILE", "configs/agents.json")


settings = Settings()
