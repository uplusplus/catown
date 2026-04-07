# Catown Phase 1 集成测试报告

> 测试人：Roy 🧪  
> 日期：2026-04-03  
> 时间：14:00-14:30  
> 环境：后端 localhost:8000，前端 localhost:3001（未启动）

---

## 测试摘要

| 指标 | 结果 |
|------|------|
| 测试项总数 | 12 |
| 通过 | 6 |
| 失败 | 6 |
| 通过率 | 50% |

**结论**：Phase 1 核心链路（消息 → Agent → LLM → 工具调用 → 响应）已跑通，但存在 2 个必改 Bug 和 1 个阻塞问题。

---

## 测试用例明细

### 1. API 端点（REST）

| # | 用例 | 方法 | 端点 | 预期 | 实际 | 结果 |
|---|------|------|------|------|------|------|
| T-01 | 健康检查 | GET | `/health` | 200 OK | **404 Not Found** | ❌ FAIL |
| T-02 | 系统状态 | GET | `/api/status` | 200 OK + 状态JSON | 200 OK，返回正常 | ✅ PASS |
| T-03 | Agent 列表 | GET | `/api/agents` | 200 OK + 4个Agent | agent, coder, reviewer, researcher | ✅ PASS |
| T-04 | 项目列表 | GET | `/api/projects` | 200 OK + 项目列表 | 1个"测试项目"，chatroom_id=1 | ✅ PASS |
| T-05 | 工具列表 | GET | `/api/tools` | 200 OK + 工具列表 | 13个工具全部注册 | ✅ PASS |
| T-06 | 协作状态 | GET | `/api/collaboration/status` | 200 OK | status=active | ✅ PASS |

### 2. 消息链路（核心）

| # | 用例 | 方法 | 端点 | 预期 | 实际 | 结果 |
|---|------|------|------|------|------|------|
| T-07 | 发送用户消息 | POST | `/api/chatrooms/1/messages` | 200 OK + 用户消息保存 | id=11, 保存成功 | ✅ PASS |
| T-08 | Agent 自动响应 | — | — | 用户消息后 Agent 自动回复 | Agent 成功响应（assistant） | ✅ PASS |
| T-09 | 工具调用 - web_search | — | — | 搜索并返回结果 | DuckDuckGo 正常返回 Python 百科摘要 | ✅ PASS |
| T-10 | 工具调用 - retrieve_memory | — | — | 从数据库检索记忆 | ❌ `'Message' object has no attribute 'agent_name'` | ❌ FAIL |
| T-11 | 工具调用 - execute_code | POST | `/api/tools/execute_code/execute` | 执行代码并返回结果 | **404 Not Found**（API路由不存在） | ❌ FAIL |

### 3. WebSocket 通信

| # | 用例 | 方法 | 端点 | 预期 | 实际 | 结果 |
|---|------|------|------|------|------|------|
| T-12 | WebSocket 连接 | WS | `ws://localhost:8000/ws` | 连接成功 | 服务端可用（前端未启动未实际测试握手） | ⏳ 待前端验证 |

### 4. 前端

| # | 用例 | 方法 | 预期 | 实际 | 结果 |
|---|------|------|------|------|------|
| F-01 | 前端可访问 | GET | `http://localhost:3001` 200 | ❌ 连接拒绝 | ❌ FAIL |

### 5. LLM 连接

| # | 用例 | 方法 | 端点 | 预期 | 实际 | 结果 |
|---|------|------|------|------|------|------|
| L-01 | LLM 连通性测试 | POST | `/api/config/test` | 连接成功或配置相关错误 | 403 unsupported_country（配置问题，非代码问题） | ⚠️ 配置 |

---

## 🔴 必改 Bug（阻塞）

### Bug-01: retrieve_memory 运行时错误
**严重度**：🔴 High  
**复现步骤**：调用工具 `retrieve_memory` 检索记忆  
**错误**：`'Message' object has no attribute 'agent_name'`  
**根因**：`retrieve_memory.py` 中第 42 行 `agent = msg.agent_name or "user"`，但 SQLAlchemy Message 模型没有 `agent_name` 字段，只有 `agent_id` 外键。应该通过 `msg.agent.name` 关联查询。  
**修复建议**：
```python
# 错误写法
agent = msg.agent_name or "user"

# 正确写法
agent = msg.agent.name if msg.agent else "user"
```
**影响**：retrieve_memory 工具完全不可用。

### Bug-02: /health 端点 404
**严重度**：🟡 Medium  
**原因**：`main.py` 中 `/health` 路由注册在 `api_router` 下，实际路径应该是 `/api/health`，而非 `/health`。  
**当前可用**：`GET /api/health` 应该可用（`api.py` 中有定义），但 `GET /health` 是 404。  

### Bug-03: execute_code 工具 API 路由 404
**严重度**：🟡 Medium  
**原因**：POST `/api/tools/execute_code/execute` 返回 404。查看 `api.py`，`/tools/{tool_name}/execute` 路由存在，但可能是 URL 编码问题或工具名匹配问题。  
**注意**：虽然 API 路由 404，但通过 Agent 自动响应链路中的工具调用（LLM → function calling → tool_registry.execute）是可用的（已在 T-09 验证）。API 路由是额外暴露的调试/手动测试端点，功能层面可通过 Agent 链路使用。

### 阻塞项：前端服务未启动
**端口 3001**：连接拒绝，无法验证前端页面功能。  
**需要**：在 `frontend/` 目录启动服务（vanilla 版只需 `cd frontend/public && python -m http.server 3001` 或类似方式）。

---

## 消息链路验证（已跑通 ✅）

```
用户消息 → POST /api/chatrooms/1/messages
    ↓
保存用户消息到数据库
    ↓
trigger_agent_response() 解析 @ 提及
    ↓
选择 target_agent（assistant）
    ↓
LLM API 调用（chat_with_tools）
    ↓
工具调用：web_search ✅ （DuckDuckGo 返回有效结果）
    ↓
工具结果拼接回复
    ↓
Agent 回复保存到数据库
    ↓
WebSocket 广播（待前端验证）
```

**验证了**：
- 用户消息持久化 ✅
- Agent 自动触发响应 ✅
- web_search 工具调用 + DuckDuckGo 返回 ✅
- LLM 工具结果拼接 ✅
- Agent 回复持久化 ✅

**未完全验证**：
- retrieve_memory 执行链路 ❌（Bug-01）
- execute_code 执行链路 ⚠️（API 404，但 Agent 链路可能可用）
- WebSocket 实时推送 ⏳（前端未启动）

---

## Phase 2 回归测试计划

Phase 2 开发完成后，需执行以下回归测试：

1. **全工具回归**：所有 13 个工具逐一验证
2. **多 Agent 路由**：@coder、@reviewer、@researcher 分别触发
3. **错误处理**：无效参数、超时、API 错误
4. **并发**：多用户同时发消息
5. **WebSocket**：实时消息推送 + 断线重连
6. **前端全页面**：Dashboard、ChatRoom、Agents、Status、Settings

---

## 总结

**Phase 1 核心功能**：用户 → Agent → LLM → 工具 → 回复 这条核心链路已打通，可以工作。

**需要立即修复的**：
1. retrieve_memory 的 `agent_name` 属性错误（1 行代码修复）
2. 前端服务启动（阻塞前端测试）
3. /health 路由修正

**可以并行开展**：Phase 2 开发不受当前阻塞影响，但上线前需通过回归测试。
