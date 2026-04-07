# -*- coding: utf-8 -*-
"""
Pipeline 配置加载

从 configs/pipelines.json 加载 Pipeline 模板定义。
"""
import json
import os
from typing import Dict, List, Optional, Any
from pathlib import Path
from pydantic import BaseModel, Field

from config import settings


class StageConfig(BaseModel):
    """单个阶段的配置"""
    name: str  # 阶段标识符
    display_name: str  # 显示名称
    agent: str  # 执行 Agent 名称
    gate: str = "auto"  # auto / manual / condition
    timeout_minutes: int = 30
    expected_artifacts: List[str] = []  # 预期产出文件
    context_prompt: str = ""  # 阶段指令
    # 打回配置
    rollback_on_blocker: bool = False
    max_rollback_count: int = 3
    rollback_target: Optional[str] = None  # 打回目标阶段名


class PipelineConfig(BaseModel):
    """Pipeline 模板配置"""
    name: str
    description: str = ""
    stages: List[StageConfig] = []


class PipelineConfigManager:
    """Pipeline 配置管理器"""

    def __init__(self, config_file: str = None):
        self.config_file = config_file or os.path.join(
            os.path.dirname(settings.AGENT_CONFIG_FILE),
            "pipelines.json"
        )
        self.configs: Dict[str, PipelineConfig] = {}

    def load(self) -> Dict[str, PipelineConfig]:
        """加载所有 Pipeline 模板"""
        if not os.path.exists(self.config_file):
            return {}

        with open(self.config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for name, config_data in data.items():
            stages = [StageConfig(**s) for s in config_data.get("stages", [])]
            self.configs[name] = PipelineConfig(
                name=config_data.get("name", name),
                description=config_data.get("description", ""),
                stages=stages
            )

        return self.configs

    def get(self, name: str) -> Optional[PipelineConfig]:
        """获取指定 Pipeline 模板"""
        if not self.configs:
            self.load()
        return self.configs.get(name)

    def list_templates(self) -> List[str]:
        """列出所有可用模板名"""
        if not self.configs:
            self.load()
        return list(self.configs.keys())

    def get_stage(self, pipeline_name: str, stage_name: str) -> Optional[StageConfig]:
        """获取指定阶段配置"""
        config = self.get(pipeline_name)
        if not config:
            return None
        for stage in config.stages:
            if stage.name == stage_name:
                return stage
        return None

    def get_next_stage(self, pipeline_name: str, current_stage_name: str) -> Optional[StageConfig]:
        """获取下一阶段配置"""
        config = self.get(pipeline_name)
        if not config:
            return None
        for i, stage in enumerate(config.stages):
            if stage.name == current_stage_name and i + 1 < len(config.stages):
                return config.stages[i + 1]
        return None

    def get_rollback_target(self, pipeline_name: str, current_stage_name: str) -> Optional[StageConfig]:
        """获取打回目标阶段配置"""
        config = self.get(pipeline_name)
        if not config:
            return None
        for stage in config.stages:
            if stage.name == current_stage_name:
                if stage.rollback_target:
                    return self.get_stage(pipeline_name, stage.rollback_target)
                # 默认打回到上一个阶段
                idx = config.stages.index(stage)
                if idx > 0:
                    return config.stages[idx - 1]
        return None


# 全局实例
pipeline_config_manager = PipelineConfigManager()
