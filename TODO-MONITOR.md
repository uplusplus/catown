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
- [x] 新建 `backend/orchestration/project_bootstrap_coordinator.py`，把 `create_project()` 的初始 briefing/brief/decision bootstrap 从 `ProjectService` 抽离
- [x] 明确 `legacy_pipeline_adapter` 不再是默认路线，改为待审查项（见 `docs/ADR-021-pipeline-disposition-and-stage-execution-kernel.md`）
- [x] 审计 `backend/pipeline/`，形成 `保留 / 改造 / 废弃` 清单并映射到实际文件/接口（见 `docs/pipeline-file-audit.md`）
- [ ] 把可复用的 runtime primitive 从旧 `pipeline/engine.py` 抽成独立薄模块，而不是继续保留整块 pipeline
  - 已完成首块：`backend/execution/workspace_guard.py`（workspace/path safety helpers）
  - 已完成第二块：`backend/execution/event_log.py`（shared event append helper，已接入 project-first + legacy pipeline 部分路径）
  - 已完成第三块：`backend/execution/tool_audit.py`（shared tool-call audit helper，已接入 legacy engine tool path）
  - 已完成第四块：`backend/execution/llm_audit.py`（shared llm-call audit helper，已接入 legacy engine llm path）
  - 已完成第五块：`backend/execution/tool_dispatch.py`（shared tool registry/dispatch helper，已接入 legacy engine tool build/execute path）
- [ ] 明确 autopilot / checkpoint 在新 kernel 层的分流点

### Phase E — 进行中
- [x] 为 bootstrap executor 立最小 `StageExecutionResult` contract（后续真实 executor 复用）
- [x] 为 `StageRun` 补最小事件/指令入口：`GET /api/v2/stage-runs/{id}/events` + `POST /api/v2/stage-runs/{id}/instructions`
- [x] 把 `events` 初步挂到 `project_id / stage_run_id / asset_id`
- [ ] 把 `bootstrap_stage_executor.py` 里的阶段产物生成继续拆成更清晰的 stage spec / asset recipe，减少 executor 内联数据块
- [ ] 为真实 stage executor 设计完整输入/输出 contract（替换 bootstrap executor）
- [ ] 为新内核补 `tasks / agent_runs / audit` 的挂接策略
- [ ] 补更细的 stage execution 可观测事件
- [ ] 前端逐步去 pipeline 化，Mission Board 替代旧 pipeline dashboard 主视图
- [x] 将过程记录同步到外部 wiki（GitHub wiki: `catown.wiki.git`，提交 `06c139d`）

### Phase F — 运行底座收口（当前优先）
- [x] 建立正式数据库迁移链路（补 `alembic/versions/`，把当前 project-first schema 变更固化为 revision）
  - 已落地：`backend/alembic/versions/20260415_1015_project_first_projects_schema.py`
- [x] 清理 `init_database()` 临时 SQLite 补列 shim，迁回正式 migration 驱动
- [x] 盘点并修复现有库对 `assets / decisions / stage_runs / events` 的 schema 漂移风险
  - `assets / decisions / stage_runs` 当前库结构与模型一致
  - `events` 已通过 `20260415_1028_project_first_event_fields.py` 补齐 `project_id / stage_run_id / asset_id`
- [x] 清理 `backend/main.py` 的 legacy 入口装配，只保留仍然必须兼容的 router / ws / audit 挂载
  - 当前已摘除旧 `pipeline` router 与 pipeline event-bus 桥接，主入口不再因 `pipeline.engine` 导入失败而阻塞启动
- [x] 给 v2 后端补一组最小 contract 验证：`/api/v2/dashboard`、`/api/v2/projects`、`/api/v2/projects/{id}/overview`、`/api/v2/stage-runs/{id}`

### Phase G — Service / Read Model 收口
- [x] 继续抽 `ProjectService` 剩余的 asset/decision/stage-run link 查询，压缩 service 的读模型职责
  - `asset detail/dependencies` 已下沉到 `backend/read_models/asset_views.py`
  - `project overview/dashboard` 的 readiness / link aggregation 已收回 `backend/read_models/project_views.py`
  - `stage-run list/detail/events` 已收回 `backend/read_models/stage_run_views.py`
  - decisions 的 list/get/resolve payload 入口已统一到 `ProjectService`
- [x] 让 `routes/*_v2.py` 尽量只保留参数校验 + service 调用，减少 route 层重复查询/404 模式
  - `projects_v2.py` / `assets_v2.py` / `stage_runs_v2.py` / `decisions_v2.py` 已完成一轮收薄
- [ ] 明确并冻结一版 v2 frontend contract，避免前端继续依赖 `/api/pipelines/*`
  - 当前结论见 `docs/Frontend-Mission-Board-Migration-Audit.md`
  - 决策方向已改为：不迁就旧 Pipeline Dashboard，而是抽取仍有产品价值的交互块，重组为 project-first Mission Board
  - 当前前端盘点结论：`frontend/index.html` 仍深度绑定 legacy `pipeline/chatroom` 心智
  - 旧 pipeline API 直接依赖至少包括：
    - `/api/pipelines`
    - `/api/pipelines/{id}`
    - `/api/pipelines/{id}/start`
    - `/api/pipelines/{id}/pause|resume|approve|reject`
    - `/api/pipelines/{id}/messages`
    - `/api/pipelines/{id}/artifacts`
    - `/api/pipelines/{id}/instruct`
    - `/api/pipelines/{id}/files`
    - `/api/pipelines/config/templates`
    - `/api/pipelines/ws`
  - 旧 chatroom API 直接依赖包括：
    - `/api/chatrooms/{id}/messages`
    - `/api/chatrooms/{id}/messages/stream`
  - 前端主问题不是“少量旧接口残留”，而是仍存在一整块 Pipeline Dashboard / chatroom 交互壳
  - 下一步改成两件事：
    1. 先冻结 Mission Board 所需最小 `/api/v2/*` contract（已落地：`docs/Mission-Board-Minimum-V2-Contract.md`）
    2. 新建 project-first 主视图，再逐步删除 pipeline/chatroom 主壳
  - 页面信息架构已落地：`docs/Mission-Board-Information-Architecture.md`
  - 前端架构决策已切到方案 C：`docs/ADR-023-frontend-react-mission-board-architecture.md`
  - 当前执行策略已改为：不做兼容迁移，直接以 `React + Vite + TypeScript` 重建 project-first Mission Board
  - 当前已完成首个骨架：`frontend/index.html` 已改为 Vite shell，`frontend/src/` 已建立 Mission Board 主结构并接入核心 `/api/v2/*` 读流与基本 continue / decision resolve 动作

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
