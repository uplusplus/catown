# OpenAI 协议全面科普

## 一、它是什么

OpenAI 协议不是某个正式标准（没有 RFC，没有 ISO），而是 **OpenAI 定义的一套 HTTP API 接口规范**，因其广泛采用而成为 LLM 领域的事实标准。

```
核心本质：一套 RESTful HTTP 接口 + JSON 数据格式
```

---

## 二、主要概念

### 1. 认证（Authentication）

```http
Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxx
```

- API Key 通过 Header 传递
- 所有请求必须携带，否则返回 `401`
- 某些实现也支持 query param `?api_key=xxx`（不推荐）

---

### 2. Chat Completions（核心接口）

这是整个协议**最核心的接口**，90% 的场景都在用它。

**端点：**
```
POST /v1/chat/completions
```

**请求结构：**
```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system",    "content": "你是一个翻译助手"},
    {"role": "user",      "content": "把'你好'翻译成英文"},
    {"role": "assistant", "content": "Hello"},
    {"role": "user",      "content": "谢谢怎么说"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": true
}
```

**响应结构：**
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Thank you"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 3,
    "total_tokens": 28
  }
}
```

---

### 3. Message 角色体系

```
┌─────────────────────────────────────────────────┐
│                   Messages                       │
│                                                  │
│  system ──→ 设定人设、规则、约束                  │
│  user   ──→ 用户输入                             │
│  assistant ──→ 模型回复（可以是历史记录）           │
│  tool    ──→ 工具调用结果（见下文）                │
│                                                  │
└─────────────────────────────────────────────────┘
```

```json
// system：设定行为
{"role": "system", "content": "你是一个专业的 Python 导师，回答简洁"}

// user：用户提问
{"role": "user", "content": "什么是装饰器？"}

// assistant：模型历史回复（多轮对话中携带）
{"role": "assistant", "content": "装饰器是一种修改函数行为的语法糖..."}

// tool：工具返回结果
{"role": "tool", "tool_call_id": "call_abc", "content": "{\"temperature\": 26}"}
```

---

### 4. 流式输出（Streaming）

`"stream": true` 时，响应不是一次性返回，而是 **SSE（Server-Sent Events）** 逐 token 推送：

```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"Hello"}}]}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":" world"}}]}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

```
普通模式：  客户端 ──请求──→ 服务器 ────等待────→ 返回完整结果
流式模式：  客户端 ──请求──→ 服务器 ──token1─→ token2─→ token3─→ [DONE]
```

**响应头关键字段：**
```http
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
```

---

### 5. Tool Calling（函数调用）

让模型**决定调用什么函数**，由客户端执行后返回结果：

```
用户: 北京今天天气怎么样？
  │
  ▼
LLM: 我需要调用 get_weather(city="北京")  ← tool_calls
  │
  ▼
客户端: 执行 get_weather → 返回结果
  │
  ▼
LLM: 北京今天晴，26°C，适合出行          ← 最终回复
```

**定义工具：**
```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "北京天气"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {
              "type": "string",
              "description": "城市名称"
            }
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

**模型响应（要求调用工具）：**
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\": \"北京\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

**客户端执行后，把结果喂回去：**
```json
{
  "messages": [
    {"role": "user", "content": "北京天气"},
    {"role": "assistant", "tool_calls": [{"id": "call_abc123", ...}]},
    {"role": "tool", "tool_call_id": "call_abc123", "content": "{\"temp\": 26, \"weather\": \"晴\"}"}
  ]
}
```

---

### 6. JSON Mode / Structured Output

强制模型输出合法 JSON：

```json
{
  "model": "gpt-4o",
  "messages": [{"role": "user", "content": "提取人名和年龄"}],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "person_info",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "age":  {"type": "integer"}
        },
        "required": ["name", "age"]
      }
    }
  }
}
```

---

### 7. Token 计费（Usage）

```json
"usage": {
  "prompt_tokens": 25,      // 输入消耗
  "completion_tokens": 3,   // 输出消耗
  "total_tokens": 28        // 总计
}
```

```
费用 = prompt_tokens × 输入单价 + completion_tokens × 输出单价
```

---

### 8. 核心参数一览

| 参数 | 说明 | 范围 |
|------|------|------|
| `model` | 模型名称 | `gpt-4o`, `gpt-4o-mini` 等 |
| `messages` | 对话历史 | 必填 |
| `temperature` | 随机性，越高越发散 | 0.0 ~ 2.0 |
| `top_p` | 核采样概率阈值 | 0.0 ~ 1.0 |
| `max_tokens` | 最大输出长度 | 整数 |
| `stream` | 是否流式 | true/false |
| `stop` | 停止词序列 | 字符串数组 |
| `presence_penalty` | 话题重复惩罚 | -2.0 ~ 2.0 |
| `frequency_penalty` | 字词重复惩罚 | -2.0 ~ 2.0 |
| `tools` | 工具定义列表 | 数组 |
| `tool_choice` | 工具选择策略 | `"auto"` / `"none"` / 具体函数 |
| `seed` | 可复现性种子 | 整数 |
| `response_format` | 输出格式约束 | `json_object` / `json_schema` |

---

### 9. 其他端点

```
POST /v1/completions          ← 旧版文本补全（已过时）
POST /v1/embeddings           ← 文本向量化
POST /v1/images/generations   ← 图片生成（DALL·E）
POST /v1/audio/transcriptions ← 语音转文字（Whisper）
POST /v1/audio/speech         ← 文字转语音（TTS）
POST /v1/moderations          ← 内容安全审核
GET  /v1/models               ← 列出可用模型
```

---

### 10. 错误处理

```json
{
  "error": {
    "message": "Incorrect API key provided: sk-xxx",
    "type": "invalid_request_error",
    "param": null,
    "code": "invalid_api_key"
  }
}
```

| HTTP 状态码 | 含义 |
|-------------|------|
| 400 | 请求参数错误 |
| 401 | 认证失败 |
| 403 | 权限不足 |
| 404 | 端点/模型不存在 |
| 429 | 速率限制 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 |

---

## 三、实现方案

### 方案一：自己实现一个兼容服务（Provider 端）

目标：**让客户端以为你在调 OpenAI，实际走你自己的模型。**

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json, time, uuid

app = FastAPI()

# ============ 数据模型 ============

class Message(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list] = None
    tool_call_id: Optional[str] = None

class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[list] = None

# ============ 核心接口 ============

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    # 1. 验证 API Key（你自己的认证逻辑）
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, detail={
            "error": {"message": "Missing API key", "type": "auth_error"}
        })

    # 2. 调用你自己的模型（这里用模拟代替）
    reply = await call_your_model(req.messages, req.model)

    # 3. 流式 or 非流式
    if req.stream:
        return StreamingResponse(
            stream_response(reply, req.model),
            media_type="text/event-stream"
        )
    else:
        return build_response(reply, req.model)


def build_response(content: str, model: str) -> dict:
    """构造标准 OpenAI 响应"""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
        }
    }


async def stream_response(content: str, model: str):
    """SSE 流式输出"""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    # 逐字/逐词拆分发送
    for chunk in content.split():
        data = json.dumps({
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": chunk + " "},
                "finish_reason": None
            }]
        })
        yield f"data: {data}\n\n"

    # 结束标记
    data = json.dumps({
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop"
        }]
    })
    yield f"data: {data}\n\n"
    yield "data: [DONE]\n\n"


async def call_your_model(messages: list[Message], model: str) -> str:
    """替换成你自己的模型调用逻辑"""
    # 可以是：本地 vLLM、Ollama、另一个 API、规则引擎...
    last_msg = messages[-1].content or ""
    return f"你说了: {last_msg}"
```

**客户端无感使用：**
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-key",
    base_url="http://localhost:8000/v1"  # 指向你的服务
)

resp = client.chat.completions.create(
    model="any-model",
    messages=[{"role": "user", "content": "你好"}]
)
print(resp.choices[0].message.content)
```

---

### 方案二：代理/网关层（Gateway）

不自己训练模型，而是**统一代理多个后端模型**：

```
客户端
  │
  ▼
┌─────────────────────────────────┐
│  OpenAI 兼容网关                 │
│                                 │
│  /v1/chat/completions           │
│    ├─ model=gpt-4o  → OpenAI    │
│    ├─ model=deepseek → DeepSeek │
│    ├─ model=claude  → Anthropic │
│    └─ model=local   → vLLM      │
│                                 │
│  统一认证 / 限流 / 计费 / 日志   │
└─────────────────────────────────┘
```

典型开源项目：**LiteLLM、One API、New API**

```python
# LiteLLM 示例：一个端点代理所有模型
import litellm

# 调 OpenAI
resp = litellm.completion(
    model="openai/gpt-4o",
    messages=[{"role": "user", "content": "你好"}]
)

# 调 DeepSeek（同样的接口）
resp = litellm.completion(
    model="deepseek/deepseek-chat",
    messages=[{"role": "user", "content": "你好"}]
)
```

---

### 方案三：客户端适配器（Consumer 端）

你已经有非 OpenAI 格式的模型服务，想让别人用 OpenAI SDK 调用：

```python
# 你的原始模型接口
class YourModel:
    def generate(self, prompt: str) -> str:
        ...

# OpenAI 兼容适配层
@app.post("/v1/chat/completions")
async def adapter(req: ChatRequest):
    # OpenAI 格式 → 你的格式
    prompt = convert_messages_to_prompt(req.messages)

    # 调用你的模型
    result = your_model.generate(prompt)

    # 你的格式 → OpenAI 格式
    return build_response(result, req.model)
```

---

### 方案四：完整实现参考架构

```
┌─────────────────────────────────────────────────────────┐
│                    OpenAI 兼容服务                        │
│                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────────────┐    │
│  │ 认证中间件 │→ │ 路由分发  │→ │  业务逻辑层       │    │
│  │          │   │          │   │                  │    │
│  │ API Key  │   │ 模型选择  │   │ Prompt 构建      │    │
│  │ 限流     │   │ 负载均衡  │   │ Tool Calling     │    │
│  │ 计费     │   │ 降级策略  │   │ 流式拼装         │    │
│  └──────────┘   └──────────┘   │ Token 计数       │    │
│                                └────────┬─────────┘    │
│                                         │              │
│                                ┌────────▼─────────┐    │
│                                │  模型推理层       │    │
│                                │                  │    │
│                                │  vLLM / TGI /    │    │
│                                │  TensorRT-LLM    │    │
│                                └──────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

关键模块的实现要点：

```python
# 1. 认证中间件
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/v1/"):
        api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not validate_key(api_key):
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "Invalid API key", "type": "auth_error"}}
            )
    return await call_next(request)


# 2. Token 计数（近似）
def count_tokens(messages: list[dict], model: str) -> int:
    import tiktoken
    enc = tiktoken.encoding_for_model(model)
    total = 0
    for msg in messages:
        total += 4  # 每条消息的格式开销
        for value in msg.values():
            if isinstance(value, str):
                total += len(enc.encode(value))
    total += 2  # reply priming
    return total


# 3. Tool Calling 流程
@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    # 如果有工具定义，需要模型支持 function calling
    if req.tools:
        # 把工具 schema 注入 system prompt 或原生支持
        system_with_tools = inject_tool_definitions(req.messages, req.tools)
        # 调模型...
        # 解析模型输出，提取 tool_calls
        # 返回包含 tool_calls 的响应
        ...
```

---

## 四、生态现状

```
OpenAI 协议兼容方（部分）：

模型厂商          兼容方式
─────────────────────────
OpenAI           原生
DeepSeek         完全兼容
Moonshot (Kimi)  完全兼容
通义千问          完全兼容
智谱 (GLM)       完全兼容
零一万物          完全兼容
Groq             完全兼容
Mistral          完全兼容
xAI (Grok)       完全兼容

本地推理          兼容方式
─────────────────────────
vLLM             原生支持 OpenAI 格式
Ollama           原生支持 OpenAI 格式
llama.cpp        通过 server 模式支持
Text Generation   通过 API 支持
  Inference (TGI)
LocalAI          完全兼容

代理/网关         功能
─────────────────────────
LiteLLM          统一 100+ 模型接口
One API          国产开源网关
New API          社区增强版
```

---

## 五、总结

```
OpenAI 协议 = 一套约定

  约定1: POST /v1/chat/completions 是核心端点
  约定2: messages 数组承载对话上下文
  约定3: 用 tools 定义外部能力
  约定4: SSE 实现流式输出
  约定5: 统一的 JSON 响应格式
  约定6: Bearer Token 认证

它不是标准，但比标准更有效——
因为所有人都在用，所以它就是标准。
```

**学习建议：** 如果要深入理解，直接读 [OpenAI API Reference](https://platform.openai.com/docs/api-reference)，然后用 curl 手动发几个请求，比看任何封装库都清楚。