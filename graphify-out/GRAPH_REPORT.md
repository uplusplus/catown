# Graph Report - .  (2026-04-14)

## Corpus Check
- 87 files · ~88,720 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1663 nodes · 4087 edges · 51 communities detected
- Extraction: 45% EXTRACTED · 55% INFERRED · 0% AMBIGUOUS · INFERRED: 2245 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]

## God Nodes (most connected - your core abstractions)
1. `Project` - 175 edges
2. `Pipeline` - 131 edges
3. `PipelineRun` - 131 edges
4. `PipelineMessage` - 131 edges
5. `PipelineStage` - 128 edges
6. `StageArtifact` - 128 edges
7. `StageConfig` - 115 edges
8. `LLMClient` - 112 edges
9. `BaseTool` - 99 edges
10. `Event` - 96 edges

## Surprising Connections (you probably didn't know these)
- `RateLimiter` --uses--> `Frontend E2E Tests — Catown Pipeline Dashboard =================================`  [INFERRED]
  backend/main.py → tests/test_frontend.py
- `RateLimiter` --uses--> `Shared TestClient backed by the real FastAPI app.     Uses isolated tmp DB and c`  [INFERRED]
  backend/main.py → tests/test_frontend.py
- `RateLimiter` --uses--> `Create a throwaway project for tests that need one.`  [INFERRED]
  backend/main.py → tests/test_frontend.py
- `RateLimiter` --uses--> `Create a pipeline linked to the sample project.`  [INFERRED]
  backend/main.py → tests/test_frontend.py
- `RateLimiter` --uses--> `Verify the frontend HTML is served and contains critical elements.`  [INFERRED]
  backend/main.py → tests/test_frontend.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (95): ABC, BaseTool, JSON Schema for a tool, Base class for all tools, Get OpenAI-compatible tool schema, Override this to define parameters schema, Registry for managing tools, List all registered tool names (+87 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (115): AgentInfo, create_project(), _extract_memories(), get_agent(), get_config(), get_messages(), get_project(), list_agents() (+107 more)

### Community 2 - "Community 2"
Cohesion: 0.08
Nodes (146): Event, LLMCall, Token 汇总统计 + 成本估算（GPT-4 pricing）, 聚合时间线：events + llm_calls + tool_calls 混合排序, 单条 LLM 调用详情（含完整 prompt 和 response）, ToolCall, Base, LLMClient (+138 more)

### Community 3 - "Community 3"
Cohesion: 0.02
Nodes (62): LLMConfigModel, BaseModel, CollaborationManager, CollaborationStrategy, MultiAgentStrategy, Multiple agents collaborate on complex tasks, Select multiple agents based on task type, Manager for agent collaboration (+54 more)

### Community 4 - "Community 4"
Cohesion: 0.02
Nodes (53): BaseHTTPMiddleware, _forward_pipeline_events_to_ws(), RateLimiter, RateLimitMiddleware, 将 engine 事件转发到所有通用 /ws 连接, RequestLoggingMiddleware, client(), Frontend E2E Tests — Catown Pipeline Dashboard ================================= (+45 more)

### Community 5 - "Community 5"
Cohesion: 0.03
Nodes (33): AssetService, BootstrapStageExecutor, Owns the scaffold generation path until a real runtime replaces it., Minimal execution contract for stage executors under the new kernel., Owns the scaffold generation path until a real runtime replaces it., StageExecutionResult, Asset, AssetLink (+25 more)

### Community 6 - "Community 6"
Cohesion: 0.02
Nodes (18): Frontend Visual Rendering Tests — Catown =======================================, 验证关键 CSS 规则在 HTML 的 <style> 块中。, 验证响应式 class 存在（不能验证实际效果）。, 验证 Agent 状态的 CSS class 和图标引用。, 验证 Pipeline Dashboard 的视觉标记。, 验证 API 数据能正确传递给渲染函数（端到端）。, renderMarkdown 出错时 fallback 到 escapeHtml。, TestAgentStatusIndicators (+10 more)

### Community 7 - "Community 7"
Cohesion: 0.03
Nodes (40): clean_db(), GET /api/agents/{id} → 返回 Agent 详情, GET /api/agents/99999 → 404, 验证 Agent 角色与 PRD 定义一致, POST /api/projects → 创建项目并分配 Agent, GET /api/projects → 列出所有项目, GET /api/projects/{id} → 返回项目详情, DELETE /api/projects/{id} → 删除项目 (+32 more)

### Community 8 - "Community 8"
Cohesion: 0.04
Nodes (16): client(), _make_app(), API 路由测试  使用 FastAPI TestClient 测试 REST 端点（mock LLM）, 在指定临时目录下创建全新的 FastAPI app（隔离测试）, 创建 FastAPI TestClient（完全隔离）, TestAgentEndpoints, TestChatEndpoints, TestCollaborationEndpoints (+8 more)

### Community 9 - "Community 9"
Cohesion: 0.05
Nodes (24): agents_config_file(), LLM 两级配置测试  覆盖： 1. Agent 自身 provider → 直接使用 2. Agent 无 provider → fallback 到 glo, 没有 provider 字段的 Agent fallback 到 global_llm, 不存在的 Agent 也 fallback 到 global_llm, _load_global_provider 测试, 没有 global_llm 段时返回 None, _get_first_provider 测试 — 优先 Agent，兜底 global, 有 Agent provider 时优先返回 Agent 的 (+16 more)

### Community 10 - "Community 10"
Cohesion: 0.06
Nodes (18): client(), _make_app(), 前端启动流程测试  覆盖 start.bat 启动时序问题： 1. 后端未就绪时 API 返回错误 → 重试后成功 2. 多次调用 idempotent（不会重, 完整启动序列：status → agents → projects → config → tools, 创建项目后 loadProjects 能拿到数据, 同名项目可以创建多次（业务允许），每次返回不同 ID, WebSocket 启动流程测试（模拟前端 connectWebSocket + 数据补拉）, WebSocket join room 后可以正常加载消息 (+10 more)

### Community 11 - "Community 11"
Cohesion: 0.13
Nodes (3): _handle_send_message(), PipelineEngine, _pop_messages_for_agent()

### Community 12 - "Community 12"
Cohesion: 0.09
Nodes (8): ChatroomInstance, ChatroomManager, 聊天室管理器测试  覆盖 ChatroomManager 的 CRUD、消息发送、消息获取, TestChatroomCreation, TestChatroomInstance, TestGetMessages, TestProcessUserMessage, TestSendMessage

### Community 13 - "Community 13"
Cohesion: 0.09
Nodes (6): WebSocket 管理器测试  覆盖连接管理、房间广播、消息路由, TestBroadcast, TestReceive, TestRoomManagement, TestWebSocketConnection, WebSocketManager

### Community 14 - "Community 14"
Cohesion: 0.07
Nodes (8): 数据库模型测试  覆盖 Agent / Project / Chatroom / Message / Memory 表的 CRUD 和关系, TestAgentAssignment, TestAgentModel, TestCascadeDelete, TestChatroomModel, TestMemoryModel, TestMessageModel, TestProjectModel

### Community 15 - "Community 15"
Cohesion: 0.1
Nodes (6): LLM 客户端扩展测试  覆盖 chat / chat_with_tools / chat_stream / get_llm_client / set_llm_, get_llm_client / set_llm_client 测试, TestLLMClientChat, TestLLMClientChatStream, TestLLMClientChatWithTools, TestLLMClientSingleton

### Community 16 - "Community 16"
Cohesion: 0.11
Nodes (3): engine_status(), get_pipeline(), pipeline_websocket()

### Community 17 - "Community 17"
Cohesion: 0.17
Nodes (16): get_default_llm_client(), _get_first_provider(), get_llm_client(), get_llm_client_for_agent(), _load_agent_provider(), _load_global_provider(), 解析字符串中的 ${ENV_VAR} 占位符, 从 agents.json 加载指定 Agent 的 provider 配置      优先级：Agent 自身 provider > global_llm p (+8 more)

### Community 18 - "Community 18"
Cohesion: 0.12
Nodes (1): coordinator()

### Community 19 - "Community 19"
Cohesion: 0.16
Nodes (9): side-panel 是 flex-col 父容器，必须有 min-h-0。, chat-container 是 flex-col 父容器，必须有 min-h-0。, messages-area 必须有 overflow-y-auto。, logs-content 必须有 overflow-y-auto。, logs-content 必须有 flex-1 以占满父容器剩余空间。, side-panel 的父级（chat-area wrapper）必须有 overflow-hidden。, 系统性检查：所有 flex-col 容器中，有 overflow-y-auto 的子元素，         其父级必须有 min-h-0 或固定高度。, Flex 布局中，overflow-y-auto 的子元素要能滚动，其父级 flex 容器     必须有 min-h-0（或固定 height/max-hei (+1 more)

### Community 20 - "Community 20"
Cohesion: 0.22
Nodes (9): execute_tool(), get_workspace(), load_agent_tools(), Load per-agent tool allowlists from agents.json., tool_execute_code(), tool_list_files(), tool_read_file(), tool_write_file() (+1 more)

### Community 21 - "Community 21"
Cohesion: 0.24
Nodes (3): FileWatcher, 通过 WebSocket 广播 reload 事件, 启动文件监听（在 FastAPI 启动时调用）

### Community 22 - "Community 22"
Cohesion: 0.49
Nodes (9): create_demo_project(), demo_workflow(), get_chatroom_messages(), get_system_status(), interactive_demo(), list_agents(), print_section(), 示例脚本 - 演示如何创建项目和使用 Agent (+1 more)

### Community 23 - "Community 23"
Cohesion: 0.46
Nodes (1): PipelineConfigManager

### Community 24 - "Community 24"
Cohesion: 0.25
Nodes (2): populated_db(), 预填充数据的数据库（含 Agent、项目、聊天室）

### Community 25 - "Community 25"
Cohesion: 0.29
Nodes (3): audit_timeline(), get_llm_call(), token_summary()

### Community 26 - "Community 26"
Cohesion: 0.29
Nodes (6): ensure_workspace_path(), is_catown_protected(), Block traversal and protected metadata access outside the execution workspace., Check whether a path points into the protected .catown metadata directory., Resolve and create the workspace directory for an execution run., validate_workspace_target()

### Community 27 - "Community 27"
Cohesion: 0.29
Nodes (6): fail_llm_call(), finalize_llm_call(), Create an in-memory LLMCall record and its start timestamp., Persist a failed LLM call., Persist a completed LLM call and return the flushed row., start_llm_call()

### Community 28 - "Community 28"
Cohesion: 0.5
Nodes (1): New business services for the v2 project-first API.

### Community 29 - "Community 29"
Cohesion: 0.67
Nodes (0): 

### Community 30 - "Community 30"
Cohesion: 0.67
Nodes (0): 

### Community 31 - "Community 31"
Cohesion: 0.67
Nodes (2): append_tool_call(), Append a normalized tool-call audit row.

### Community 32 - "Community 32"
Cohesion: 0.67
Nodes (2): append_event(), Append a normalized audit event across old and new execution paths.

### Community 33 - "Community 33"
Cohesion: 0.67
Nodes (0): 

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): Settings

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (0): 

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (0): 

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (0): 

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (0): 

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (0): 

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (0): 

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (0): 

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (0): 

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (0): 

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (0): 

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (0): 

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (0): 

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (0): 

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): 从 SOUL JSON 动态组装 system_prompt

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): 将 engine 事件转发到所有通用 /ws 连接

## Knowledge Gaps
- **146 isolated node(s):** `Settings`, `将 engine 事件转发到所有通用 /ws 连接`, `通过 WebSocket 广播 reload 事件`, `启动文件监听（在 FastAPI 启动时调用）`, `Load per-agent tool allowlists from agents.json.` (+141 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 34`** (2 nodes): `config.py`, `Settings`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (2 nodes): `test_llm.py`, `test_llm()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `vite.config.ts`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `check.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `test_backend.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `check_code.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `从 SOUL JSON 动态组装 system_prompt`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `将 engine 事件转发到所有通用 /ws 连接`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Project` connect `Community 2` to `Community 1`, `Community 3`, `Community 5`, `Community 11`, `Community 12`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Why does `BaseTool` connect `Community 0` to `Community 1`?**
  _High betweenness centrality (0.097) - this node is a cross-community bridge._
- **Why does `LLMClient` connect `Community 2` to `Community 3`, `Community 17`, `Community 11`, `Community 15`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Are the 173 inferred relationships involving `Project` (e.g. with `TestPipelineCreation` and `TestPipelineStart`) actually correct?**
  _`Project` has 173 INFERRED edges - model-reasoned connections that need verification._
- **Are the 129 inferred relationships involving `Pipeline` (e.g. with `TestPipelineCreation` and `TestPipelineStart`) actually correct?**
  _`Pipeline` has 129 INFERRED edges - model-reasoned connections that need verification._
- **Are the 129 inferred relationships involving `PipelineRun` (e.g. with `TestPipelineCreation` and `TestPipelineStart`) actually correct?**
  _`PipelineRun` has 129 INFERRED edges - model-reasoned connections that need verification._
- **Are the 129 inferred relationships involving `PipelineMessage` (e.g. with `TestPipelineCreation` and `TestPipelineStart`) actually correct?**
  _`PipelineMessage` has 129 INFERRED edges - model-reasoned connections that need verification._