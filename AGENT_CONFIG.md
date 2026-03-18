# Agent 配置格式说明

## 新配置格式特性

Catown 现在支持更灵活的 Agent 配置格式，主要特性包括：

- ✅ **多模型支持**: 一个 Agent 可以配置多个可用模型
- ✅ **模型能力标识**: 标识模型支持的输入类型（文本、图片等）
- ✅ **上下文窗口配置**: 为每个模型配置独立的上下文窗口大小
- ✅ **成本配置**: 配置每个模型的成本信息
- ✅ **动态模型切换**: Agent 可以在运行时切换使用的模型
- ✅ **自动模型选择**: 根据任务需求自动选择合适的模型

## 配置格式

### 完整配置示例

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

### 配置字段说明

#### Agent 级别配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `role` | string | 是 | Agent 角色描述 |
| `system_prompt` | string | 是 | 系统提示词 |
| `tools` | array | 否 | 可用工具列表 |
| `default_model` | string | 否 | 默认使用的模型 ID |
| `provider` | object | 是 | Provider 配置 |

#### Provider 配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `baseUrl` | string | 是 | API 基础 URL |
| `apiKey` | string | 是 | API 密钥 |
| `auth` | string | 否 | 认证方式（默认 "api-key"） |
| `api` | string | 否 | API 类型（默认 "openai-completions"） |
| `models` | array | 是 | 可用模型列表 |

#### Model 配置

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 模型唯一标识符 |
| `name` | string | 是 | 模型显示名称 |
| `api` | string | 否 | API 类型 |
| `reasoning` | boolean | 否 | 是否支持推理 |
| `input` | array | 否 | 支持的输入类型（["text"] 或 ["text", "image"]） |
| `cost` | object | 否 | 成本配置 |
| `contextWindow` | number | 否 | 上下文窗口大小 |
| `maxTokens` | number | 否 | 最大输出 Token 数 |

#### Cost 配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `input` | number | 输入 Token 成本 |
| `output` | number | 输出 Token 成本 |
| `cacheRead` | number | 缓存读取成本 |
| `cacheWrite` | number | 缓存写入成本 |

## 使用方法

### 1. 从配置文件加载

```python
from agents.config_manager import load_agent_configs

# 加载配置
configs = load_agent_configs("configs/agents.json")

# 获取特定 Agent 配置
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
        "models": [
            {
                "id": "model-1",
                "name": "Model 1",
                "input": ["text"],
                "contextWindow": 128000,
                "maxTokens": 16384,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
            }
        ]
    },
    tools=["web_search"],
    default_model="model-1"
)
```

### 3. 创建 Agent 实例

```python
from agents.core import Agent

# 创建 Agent
agent = Agent(config)

# 查看可用模型
print(agent.get_available_models())
# ['Qwen-V3.5-256K', 'GLM-V5-128K']

# 切换模型
agent.set_model("GLM-V5-128K")

# 获取当前模型信息
model_info = agent.get_model_info()
print(model_info)
# {
#   "id": "GLM-V5-128K",
#   "name": "GLM-V5-128K",
#   "context_window": 128000,
#   "max_tokens": 16384,
#   "capabilities": ["text"],
#   "reasoning": false
# }
```

### 4. 根据能力选择模型

```python
# 获取支持图片的模型
multimodal_models = config.provider.get_models_by_capability("image")

# 获取支持文本的模型
text_models = config.provider.get_models_by_capability("text")

# 根据上下文长度选择
long_context_models = [
    m for m in config.provider.models 
    if m.contextWindow >= 200000
]
```

## 模型选择策略

### 自动选择逻辑

1. **优先使用指定模型**: 如果设置了 `default_model`，优先使用
2. **能力匹配**: 根据任务需求选择支持相应能力的模型
3. **上下文匹配**: 根据对话长度选择合适上下文窗口的模型
4. **成本优化**: 考虑成本因素选择最优模型

### 场景示例

#### 场景 1: 图片理解任务

```python
# 需要处理图片，选择支持 image 的模型
multimodal_models = config.provider.get_models_by_capability("image")
if multimodal_models:
    agent.set_model(multimodal_models[0].id)
```

#### 场景 2: 长对话任务

```python
# 需要长上下文，选择 contextWindow 最大的模型
longest_context_model = max(
    config.provider.models,
    key=lambda m: m.contextWindow
)
agent.set_model(longest_context_model.id)
```

#### 场景 3: 成本敏感任务

```python
# 选择成本最低的模型
cheapest_model = min(
    config.provider.models,
    key=lambda m: m.cost.input + m.cost.output
)
agent.set_model(cheapest_model.id)
```

## 兼容性

### 兼容旧配置格式

旧格式仍然支持：

```python
# 旧格式
{
    "name": "assistant",
    "role": "通用助手",
    "system_prompt": "...",
    "tools": [...],
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "your-key",
    "llm_model": "gpt-4"
}
```

系统会自动转换为新的内部格式。

### 环境变量支持

仍然支持通过环境变量配置：

```bash
LLM_API_KEY=your-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4
```

## 最佳实践

### 1. 配置文件管理

- 将配置文件放在 `configs/` 目录
- 使用环境变量管理敏感信息（API Key）
- 为不同环境（开发/测试/生产）使用不同配置文件

### 2. 模型选择建议

- **代码任务**: 选择擅长编程的模型
- **长对话**: 选择大上下文窗口模型
- **图片处理**: 选择支持 image 输入的模型
- **成本敏感**: 选择成本较低的模型

### 3. 配置示例

```json
{
  "agents": {
    "coder": {
      "role": "代码专家",
      "system_prompt": "...",
      "default_model": "Qwen-V3.5-256K",
      "provider": {
        "baseUrl": "...",
        "models": [
          {
            "id": "Qwen-V3.5-256K",
            "input": ["text", "image"],
            "contextWindow": 256000,
            ...
          }
        ]
      }
    },
    "assistant": {
      "role": "通用助手",
      "default_model": "GLM-V5-128K",
      "provider": {
        "models": [
          {
            "id": "GLM-V5-128K",
            "input": ["text"],
            "contextWindow": 128000,
            ...
          }
        ]
      }
    }
  }
}
```

## 常见问题

### Q: 如何在不修改配置文件的情况下切换模型？

A: 使用 `agent.set_model("model-id")` 方法。

### Q: 如何知道当前模型的能力？

A: 使用 `agent.get_model_info()` 查看详细信息，包括 `capabilities` 字段。

### Q: 如何添加新的模型？

A: 在配置文件的 `models` 数组中添加新的模型配置即可。

### Q: 配置文件修改后如何生效？

A: 需要重启服务，或使用配置管理器重新加载。

## 相关文档

- [快速开始](QUICKSTART.md)
- [项目结构](PROJECT_STRUCTURE.md)
- [API 文档](http://localhost:8000/docs)
