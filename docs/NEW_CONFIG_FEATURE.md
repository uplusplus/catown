# 🎉 新功能：灵活的 Agent 配置格式

Catown 现在支持更灵活的 Agent 配置格式！

## ✨ 新特性

### 1. 多模型支持
一个 Agent 可以配置多个可用模型，根据任务需求动态切换：

```json
{
  "models": [
    {
      "id": "Qwen-V3.5-256K",
      "input": ["text", "image"],
      "contextWindow": 256000
    },
    {
      "id": "GLM-V5-128K",
      "input": ["text"],
      "contextWindow": 128000
    }
  ]
}
```

### 2. 模型能力标识
明确标识每个模型支持的输入类型：

- `["text"]` - 仅支持文本
- `["text", "image"]` - 支持文本和图片

### 3. 动态模型切换
在运行时切换使用的模型：

```python
agent.set_model("GLM-V5-128K")
```

### 4. 上下文窗口配置
为每个模型配置独立的上下文窗口大小，优化资源使用。

### 5. 成本配置
配置每个模型的成本信息，用于成本优化和计费。

## 📝 配置示例

完整的配置文件示例（`backend/configs/agents.json`）：

```json
{
  "agents": {
    "assistant": {
      "role": "通用助手",
      "system_prompt": "You are a helpful assistant.",
      "tools": ["web_search", "retrieve_memory"],
      "default_model": "GLM-V5-128K",
      "provider": {
        "baseUrl": "http://localhost:3008/opencode/assistant-agent-s/opencode/v1",
        "apiKey": "__OPENCLAW_REDACTED__",
        "auth": "api-key",
        "api": "openai-completions",
        "models": [
          {
            "id": "Qwen-V3.5-256K",
            "name": "Qwen-V3.5-256K",
            "api": "openai-completions",
            "reasoning": false,
            "input": ["text", "image"],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 256000,
            "maxTokens": 32768
          },
          {
            "id": "GLM-V5-128K",
            "name": "GLM-V5-128K",
            "api": "openai-completions",
            "reasoning": false,
            "input": ["text"],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 128000,
            "maxTokens": 16384
          }
        ]
      }
    }
  }
}
```

## 🚀 使用方法

### 1. 从配置文件加载

```python
from agents.config_manager import load_agent_configs

# 加载配置
configs = load_agent_configs("configs/agents.json")

# 获取特定 Agent
assistant_config = configs["assistant"]
```

### 2. 动态创建配置

```python
from agents.config_models import create_agent_config_from_provider

config = create_agent_config_from_provider(
    agent_name="my_agent",
    role="自定义Agent",
    system_prompt="You are a helpful assistant.",
    provider_config={
        "baseUrl": "http://localhost:3008/...",
        "apiKey": "your-api-key",
        "models": [...]
    },
    default_model="GLM-V5-128K"
)
```

### 3. 使用 Agent

```python
from agents.core import Agent

# 创建 Agent
agent = Agent(config)

# 查看可用模型
print(agent.get_available_models())
# ['Qwen-V3.5-256K', 'GLM-V5-128K']

# 切换模型
agent.set_model("GLM-V5-128K")

# 获取模型信息
info = agent.get_model_info()
print(info)
```

## 🎯 模型选择策略

### 根据能力选择

```python
# 获取支持图片的模型
multimodal_models = config.provider.get_models_by_capability("image")

# 获取支持文本的模型
text_models = config.provider.get_models_by_capability("text")
```

### 根据上下文选择

```python
# 选择长上下文模型
long_context_models = [
    m for m in config.provider.models 
    if m.contextWindow >= 200000
]
```

### 根据成本选择

```python
# 选择成本最低的模型
cheapest_model = min(
    config.provider.models,
    key=lambda m: m.cost.input + m.cost.output
)
```

## 📚 相关文件

- **配置文件**: `backend/configs/agents.json`
- **配置模型**: `backend/agents/config_models.py`
- **配置管理器**: `backend/agents/config_manager.py`
- **示例代码**: `backend/examples/new_config_example.py`
- **详细文档**: `AGENT_CONFIG.md`

## 🔄 兼容性

- ✅ 完全兼容旧配置格式
- ✅ 支持环境变量配置
- ✅ 支持动态配置加载

旧格式仍然可用：

```python
{
    "name": "assistant",
    "llm_base_url": "...",
    "llm_api_key": "...",
    "llm_model": "gpt-4"
}
```

## 🏃 快速开始

1. 复制示例配置：
```bash
cp backend/configs/agents.json backend/configs/agents.json.bak
```

2. 修改配置文件，填入你的 API Key

3. 重启服务

4. 运行示例：
```bash
cd backend/examples
python new_config_example.py
```

## 💡 提示

- 使用环境变量管理敏感信息
- 为不同任务配置不同的模型
- 根据成本和性能需求选择合适的模型
- 定期更新配置以使用最新的模型

---

**🎉 享受灵活的多模型配置能力！**
