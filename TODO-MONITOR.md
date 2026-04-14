# 监控审计 + 交互可视化 TODO

> 基于 ADR-010 / PRD §14 阶段零
> 开始时间: 2026-04-11 22:42

## Project-First Refactor 主线

> 基于 `docs/ADR-020-service-layer-and-implementation-plan.md`
> 当前优先级高于监控面板；监控审计链路放在新业务骨架稳定后继续接。

### Phase B/C — 已完成
- [x] `Project / Asset / Decision / StageRun` 新模型主链路打通
- [x] `POST /api/v2/projects` 创建 `project_brief + scope_confirmation`
- [x] `POST /api/v2/decisions/{id}/resolve` 推进 scope / release 决策
- [x] `GET /api/v2/dashboard` / `GET /api/v2/projects/{id}/overview` 新读模型落地
- [x] 拆出 `ProjectFlowCoordinator`
- [x] 拆出 `DecisionEffectsCoordinator`
- [x] 拆出 `ProjectViewBuilder`
- [x] 拆出 `AssetService`
- [x] 统一 stage-run lifecycle / release-readiness 读模型字段
- [x] 补齐 bootstrap 资产依赖链（`build_artifact` <- `prd+ux_blueprint+tech_spec`; `release_pack` <- `build_artifact+test_report`）

### Phase D — 进行中
- [x] 新建 `backend/execution/stage_execution_kernel.py`，把 `continue_project()` 的执行入口切到新 kernel
- [x] 新建 `backend/execution/bootstrap_stage_executor.py`，承接当前 scaffold-only 的 stage 执行逻辑
- [x] 明确 `legacy_pipeline_adapter` 不再是默认路线，改为待审查项（见 `docs/ADR-021-pipeline-disposition-and-stage-execution-kernel.md`）
- [x] 审计 `backend/pipeline/`，形成 `保留 / 改造 / 废弃` 清单并映射到实际文件/接口（见 `docs/pipeline-file-audit.md`）
- [ ] 把可复用的 runtime primitive 从旧 `pipeline/engine.py` 抽成独立薄模块，而不是继续保留整块 pipeline
  - 已完成首块：`backend/execution/workspace_guard.py`（workspace/path safety helpers）
  - 已完成第二块：`backend/execution/event_log.py`（shared event append helper，已接入 project-first + legacy pipeline 部分路径）
  - 已完成第三块：`backend/execution/tool_audit.py`（shared tool-call audit helper，已接入 legacy engine tool path）
- [ ] 明确 autopilot / checkpoint 在新 kernel 层的分流点

### Phase E — 进行中
- [x] 为 bootstrap executor 立最小 `StageExecutionResult` contract（后续真实 executor 复用）
- [x] 为 `StageRun` 补最小事件/指令入口：`GET /api/v2/stage-runs/{id}/events` + `POST /api/v2/stage-runs/{id}/instructions`
- [x] 把 `events` 初步挂到 `project_id / stage_run_id / asset_id`
- [ ] 为真实 stage executor 设计完整输入/输出 contract（替换 bootstrap executor）
- [ ] 为新内核补 `tasks / agent_runs / audit` 的挂接策略
- [ ] 补更细的 stage execution 可观测事件
- [ ] 前端逐步去 pipeline 化，Mission Board 替代旧 pipeline dashboard 主视图
- [x] 将过程记录同步到外部 wiki（GitHub wiki: `catown.wiki.git`，提交 `06c139d`）

---

## P0 — 数据管道

### 0a. LLM Client 返回 usage
- [ ] `LLMClient.chat_with_tools()` 解析 `response.usage` 并返回
- [ ] `LLMClient.chat_stream()` 在 `done` 事件中附带 usage
- [ ] 单元测试：mock response 验证 usage 解析
- **文件**: `backend/llm/client.py`

### 0b. 三表采集管道
- [ ] 新建 `backend/models/audit.py` — llm_calls / tool_calls / events 三表
- [ ] 数据库迁移：`init_database()` 自动建表
- [ ] `engine.py` `_run_agent_stage()` 每轮 LLM 调用写入 llm_calls
- [ ] `engine.py` `_execute_tool()` 写入 tool_calls
- [ ] `engine.py` 阶段流转 / gate / rollback 写入 events
- [ ] `api.py` `trigger_agent_response()` 写入 llm_calls + tool_calls
- [ ] `api.py` `send_message_stream()` 写入 llm_calls + tool_calls
- **文件**: `backend/models/audit.py`, `backend/pipeline/engine.py`, `backend/routes/api.py`

### 0c. 审计 API
- [ ] 新建 `backend/routes/audit.py`
- [ ] `GET /api/audit/llm` — LLM 调用查询
- [ ] `GET /api/audit/llm/{id}` — 单条详情
- [ ] `GET /api/audit/tools` — 工具调用查询
- [ ] `GET /api/audit/events` — 事件流查询
- [ ] `GET /api/audit/tokens/summary` — Token 汇总 + 成本估算
- [ ] `GET /api/audit/timeline` — 聚合时间线
- [ ] 注册到 main.py
- **文件**: `backend/routes/audit.py`, `backend/main.py`

### 0d. SSE 事件扩展
- [ ] `engine.py` 阶段开始推送 `stage_start` 事件
- [ ] `engine.py` 阶段结束推送 `stage_end` 事件
- [ ] `engine.py` LLM 调用推送 `llm_call` 事件
- [ ] `engine.py` Skill 注入推送 `skill_inject` 事件
- [ ] `engine.py` Gate 阻塞推送 `gate_blocked` 事件
- **文件**: `backend/pipeline/engine.py`, `backend/routes/api.py`

## P1 — 前端

### 0e. 聊天窗卡片前端
- [ ] LLM 卡片渲染函数 `renderLLMCard()`
- [ ] 工具卡片渲染函数 `renderToolCard()`（按工具类型区分 icon）
- [ ] Agent 间消息卡片 `renderAgentMessageCard()`
- [ ] 阶段事件卡片 `renderStageCard()`
- [ ] Gate 卡片 `renderGateCard()`（含操作按钮）
- [ ] Skill 注入卡片 `renderSkillCard()`
- [ ] BOSS 指令卡片 `renderBossInstructionCard()`
- [ ] 修改 `renderMessages()` 支持混合卡片流
- [ ] 折叠/展开/自动展开（失败时）
- [ ] 连续同类卡片合并
- **文件**: `frontend/index.html`

### 0f. Token 面板
- [ ] Token 汇总面板渲染
- [ ] 按 agent/stage 分组统计
- [ ] 成本估算显示
- **文件**: `frontend/index.html`

## P2 — 完善

### 0g. 滚动清理
- [ ] 保留期配置
- [ ] 定时清理任务
- [ ] 锁定/解锁机制
