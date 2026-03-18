"""
示例脚本 - 演示新的 Agent 配置格式
"""
from agents.config_models import (
    AgentConfigV2,
    AgentProviderConfig,
    ModelConfig,
    ModelCost,
    create_agent_config_from_provider,
    parse_agent_config
)
from agents.core import Agent


def example_new_config_format():
    """演示新的配置格式"""
    print("=" * 60)
    print("新的 Agent 配置格式示例")
    print("=" * 60)
    
    # 1. 直接使用字典配置
    print("\n1. 使用字典配置创建 Agent:")
    
    config_dict = {
        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
        "apiKey": "__OPENCLAW_REDACTED__",
        "auth": "api-key",
        "api": "openai-completions",
        "models": [
            {
                "id": "Qwen-V3.5-256K",
                "name": "Qwen-V3.5-256K",
                "api": "openai-completions",
                "reasoning": False,
                "input": ["text", "image"],
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
                "contextWindow": 256000,
                "maxTokens": 32768,
            },
            {
                "id": "GLM-V5-128K",
                "name": "GLM-V5-128K",
                "api": "openai-completions",
                "reasoning": False,
                "input": ["text"],
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
                "contextWindow": 128000,
                "maxTokens": 16384,
            },
        ],
    }
    
    # 解析配置
    provider_config = parse_agent_config(config_dict)
    print(f"   Provider URL: {provider_config.baseUrl}")
    print(f"   可用模型: {[m.id for m in provider_config.models]}")
    
    # 创建 Agent 配置
    agent_config = create_agent_config_from_provider(
        agent_name="my_agent",
        role="自定义Agent",
        system_prompt="You are a helpful assistant.",
        provider_config=config_dict,
        tools=["web_search"],
        default_model="GLM-V5-128K"
    )
    
    print(f"   Agent 名称: {agent_config.name}")
    print(f"   默认模型: {agent_config.get_effective_model()}")
    print(f"   Base URL: {agent_config.get_effective_base_url()}")
    
    # 2. 使用 Pydantic 模型
    print("\n2. 使用 Pydantic 模型创建配置:")
    
    models = [
        ModelConfig(
            id="Qwen-V3.5-256K",
            name="Qwen-V3.5-256K",
            input=["text", "image"],
            contextWindow=256000,
            maxTokens=32768,
            cost=ModelCost()
        ),
        ModelConfig(
            id="GLM-V5-128K",
            name="GLM-V5-128K",
            input=["text"],
            contextWindow=128000,
            maxTokens=16384,
            cost=ModelCost()
        )
    ]
    
    provider = AgentProviderConfig(
        baseUrl="http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
        apiKey="__OPENCLAW_REDACTED__",
        models=models
    )
    
    config = AgentConfigV2(
        name="expert_agent",
        role="专家Agent",
        system_prompt="You are an expert in your field.",
        provider=provider,
        default_model="Qwen-V3.5-256K"
    )
    
    print(f"   Agent 名称: {config.name}")
    print(f"   默认模型: {config.get_effective_model()}")
    print(f"   模型数量: {len(config.provider.models)}")
    
    # 3. 模型信息查询
    print("\n3. 查询模型信息:")
    
    model_info = config.get_model_config("GLM-V5-128K")
    if model_info:
        print(f"   模型 ID: {model_info.id}")
        print(f"   上下文窗口: {model_info.contextWindow}")
        print(f"   最大 Token: {model_info.maxTokens}")
        print(f"   输入能力: {model_info.input}")
    
    # 查询支持图片的模型
    multimodal_models = config.provider.get_models_by_capability("image")
    print(f"   支持图片的模型: {[m.id for m in multimodal_models]}")
    
    # 4. 创建 Agent 实例
    print("\n4. 创建 Agent 实例并切换模型:")
    
    agent = Agent(config)
    
    print(f"   可用模型: {agent.get_available_models()}")
    print(f"   当前模型信息: {agent.get_model_info()}")
    
    # 切换模型
    agent.set_model("GLM-V5-128K")
    print(f"   切换后模型: {agent.get_model_info()}")
    
    print("\n" + "=" * 60)
    print("示例完成！")
    print("=" * 60)


def example_load_from_file():
    """演示从文件加载配置"""
    print("\n" + "=" * 60)
    print("从文件加载配置示例")
    print("=" * 60)
    
    from agents.config_manager import AgentConfigManager, load_agent_configs
    
    # 1. 使用便捷函数
    print("\n1. 使用 load_agent_configs():")
    try:
        configs = load_agent_configs("configs/agents.json")
        print(f"   加载了 {len(configs)} 个 Agent 配置")
        
        for name, config in configs.items():
            print(f"   - {name}: {config.role}")
            print(f"     模型: {config.get_effective_model()}")
    except Exception as e:
        print(f"   错误: {e}")
    
    # 2. 使用配置管理器
    print("\n2. 使用 AgentConfigManager:")
    
    manager = AgentConfigManager()
    
    try:
        configs = manager.load_from_json("configs/agents.json")
        print(f"   加载了 {len(configs)} 个配置")
        
        # 列出所有配置
        print(f"   配置列表: {manager.list_configs()}")
        
        # 获取特定配置
        assistant_config = manager.get_config("assistant")
        if assistant_config:
            print(f"   Assistant 的模型: {assistant_config.get_effective_model()}")
    except Exception as e:
        print(f"   错误: {e}")
    
    print("\n" + "=" * 60)


def example_model_selection():
    """演示模型选择策略"""
    print("\n" + "=" * 60)
    print("模型选择策略示例")
    print("=" * 60)
    
    provider_config = {
        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
        "apiKey": "__OPENCLAW_REDACTED__",
        "models": [
            {
                "id": "Qwen-V3.5-256K",
                "name": "Qwen-V3.5-256K",
                "input": ["text", "image"],
                "contextWindow": 256000,
                "maxTokens": 32768,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
            },
            {
                "id": "GLM-V5-128K",
                "name": "GLM-V5-128K",
                "input": ["text"],
                "contextWindow": 128000,
                "maxTokens": 16384,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
            },
            {
                "id": "Qwen-V3.5-27B-128K",
                "name": "Qwen-V3.5-27B-128K",
                "input": ["text"],
                "contextWindow": 128000,
                "maxTokens": 16384,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
            }
        ]
    }
    
    config = create_agent_config_from_provider(
        agent_name="smart_agent",
        role="智能Agent",
        system_prompt="You are a smart agent with model selection.",
        provider_config=provider_config
    )
    
    print("\n1. 默认模型选择:")
    print(f"   默认模型: {config.get_effective_model()}")
    
    print("\n2. 根据能力选择模型:")
    
    # 需要处理图片的场景
    multimodal_models = config.provider.get_models_by_capability("image")
    print(f"   支持图片的模型: {[m.id for m in multimodal_models]}")
    
    # 纯文本场景
    text_models = config.provider.get_models_by_capability("text")
    print(f"   支持文本的模型: {[m.id for m in text_models]}")
    
    print("\n3. 根据上下文长度选择:")
    
    # 需要长上下文的场景
    long_context_models = [
        m for m in config.provider.models 
        if m.contextWindow >= 200000
    ]
    print(f"   长上下文模型 (>=200K): {[m.id for m in long_context_models]}")
    
    # 普通场景
    normal_models = [
        m for m in config.provider.models 
        if m.contextWindow < 200000
    ]
    print(f"   普通模型 (<200K): {[m.id for m in normal_models]}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    # 运行所有示例
    example_new_config_format()
    example_load_from_file()
    example_model_selection()
    
    print("\n" + "=" * 60)
    print("所有示例运行完成！")
    print("=" * 60)
    print("\n关键特性:")
    print("✓ 支持多模型配置")
    print("✓ 支持模型能力查询（文本/图片）")
    print("✓ 支持动态模型切换")
    print("✓ 支持上下文窗口配置")
    print("✓ 支持成本配置")
    print("✓ 兼容旧配置格式")
    print("✓ 支持从 JSON/YAML 文件加载")
