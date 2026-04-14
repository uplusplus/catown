# ADR-020: 服务层拆分与实施顺序

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-017-main-api-design.md`, `docs/ADR-018-legacy-to-new-mapping.md`, `docs/ADR-019-schema-and-migration-plan.md`

---

## 1. 目标

本文档的目标，是把 Catown 从“文档上已经讲通”推进到“代码应该怎么下手”。

要回答的问题是：

**新的 `Project / Asset / Decision / StageRun` 内核在后端代码层应该怎么拆？哪些模块要新建？哪些旧模块可以吸收？从哪里开刀最稳？**

本文档不直接写 ORM 和路由代码，而是提供：

- 服务层边界
- 模块职责分工
- 路由层与服务层的关系
- 适配器应该放哪
- 具体实施顺序

---

## 2. 当前后端结构判断

当前 `backend/` 目录已经有几块成熟积累：

- `agents/`：Agent 配置、协作策略、注册逻辑
- `tools/`：工具体系
- `llm/`：模型调用
- `pipeline/`：旧执行主轴
- `chatrooms/`：旧交互主轴
- `models/`：数据库与审计模型
- `routes/`：API 路由层

问题不在于后端没有能力，而在于：

- 业务主语义还没有独立服务层
- 路由层过于贴近旧 chatroom/pipeline 心智
- `pipeline/` 承担了过多产品层语义
- 缺少围绕 `Project / Asset / Decision / StageRun` 的服务编排中心

所以这次不是单纯“多加几个 route”，而是要把服务层立起来。

---

## 3. 新后端分层建议

建议把新后端明确分成五层。

## 3.1 模型层（Models）

负责：

- ORM 定义
- 基础关系声明
- 轻量字段级约束

不负责：

- 项目状态流转逻辑
- 决策副作用
- 阶段推进编排

### 建议目录

- `backend/models/database.py`（可继续承载，或逐步拆分）
- 后续可演化为：
  - `backend/models/project.py`
  - `backend/models/asset.py`
  - `backend/models/decision.py`
  - `backend/models/stage_run.py`
  - `backend/models/audit.py`

### P0 建议

P0 阶段不必一上来把 model 文件拆太散，但至少要在代码组织上把新模型分区清楚。

---

## 3.2 服务层（Services）

这是本轮最关键的新层。

负责：

- 业务规则
- 状态迁移
- 资源创建与版本演化
- 决策副作用链
- 最小闭环推进

### 建议新增目录

- `backend/services/`

### 建议子模块

- `project_service.py`
- `asset_service.py`
- `decision_service.py`
- `stage_run_service.py`
- `dashboard_service.py`
- `release_service.py`

### 原则

**路由层不要自己拼业务逻辑，统一由 service 层接住。**

---

## 3.3 编排层（Orchestration / Coordinators）

负责：

- 跨多个 service 的推进动作
- 项目从一个业务动作推进到下一个阶段
- 与旧 pipeline 引擎打交道
- 处理 autopilot / checkpoint 模式差异

### 建议新增目录

- `backend/orchestration/`

### 建议子模块

- `project_flow_coordinator.py`
- `decision_effects.py`
- `stage_execution_adapter.py`
- `legacy_pipeline_adapter.py`

### 说明

service 层解决“对象本身的业务逻辑”；
coordinator 层解决“多个对象一起如何推进”。

---

## 3.4 适配层（Adapters）

负责：

- 吸收旧执行能力
- 吸收旧事件格式
- 把旧 `pipeline` / `chatroom` 结果转成新内核语义

### 建议新增目录

- `backend/adapters/`

### 建议子模块

- `pipeline_to_stage_run.py`
- `stage_artifact_to_asset.py`
- `legacy_events_adapter.py`
- `chat_context_adapter.py`

### 关键要求

adapter 必须是：

- 单向吸收
- 过渡性的
- 不能反过来污染新 service 接口

---

## 3.5 路由层（Routes）

负责：

- 参数解析
- 调 service
- 返回响应
- 做最薄的一层 HTTP 翻译

### 建议新增路由文件

- `backend/routes/projects_v2.py`
- `backend/routes/assets_v2.py`
- `backend/routes/decisions_v2.py`
- `backend/routes/stage_runs_v2.py`
- `backend/routes/dashboard_v2.py`

### 原则

- 旧 `api.py` / `pipeline.py` 暂时保留
- 新 API 直接走新 route 文件
- 不建议把新旧逻辑继续混写在同一堆 legacy route 里

---

## 4. 核心服务职责细化

## 4.1 `project_service.py`

### 应负责

- 创建项目
- 读取项目列表/详情
- 更新项目可编辑字段
- 计算项目当前摘要
- 校验项目是否允许继续推进

### 不应负责

- 决策 resolve 副作用链
- 资产版本生成细节
- 调旧 pipeline 引擎

### 典型函数

- `create_project()`
- `list_projects()`
- `get_project()`
- `update_project()`
- `mark_project_blocked()`
- `set_project_stage()`

---

## 4.2 `asset_service.py`

### 应负责

- 创建资产
- 生成资产新版本
- 查询资产版本链
- 查询资产依赖关系
- 资产审批状态更新

### 不应负责

- 直接决定项目状态迁移
- 直接决定阶段推进

### 典型函数

- `create_asset()`
- `create_asset_version()`
- `get_current_assets_for_project()`
- `link_assets()`
- `approve_asset()`
- `regenerate_asset_request()`

---

## 4.3 `decision_service.py`

### 应负责

- 创建决策对象
- 查询待处理决策
- 处理决策 resolve
- 记录决策结果

### 注意

`resolve_decision()` 不应把所有副作用都写死在 route 里，而应由它自己联动 coordinator 或 effect handler。

### 典型函数

- `create_decision()`
- `get_pending_decisions()`
- `get_project_decisions()`
- `resolve_decision()`

---

## 4.4 `stage_run_service.py`

### 应负责

- 创建阶段推进记录
- 查询阶段历史
- 标记运行状态
- 记录输入输出资产关联
- 重试/取消阶段

### 不应负责

- 自己直接操作 HTTP
- 自己决定复杂决策副作用链

### 典型函数

- `create_stage_run()`
- `start_stage_run()`
- `complete_stage_run()`
- `fail_stage_run()`
- `retry_stage_run()`
- `attach_input_asset()`
- `attach_output_asset()`

---

## 4.5 `dashboard_service.py`

### 应负责

- 首页聚合读模型
- Mission Board 聚合读模型
- Release Center 聚合读模型

### 原则

它解决的是“页面怎么少拼一点”，不是主业务真理来源。

### 典型函数

- `build_dashboard_view()`
- `build_project_overview()`
- `build_release_view()`

---

## 5. 编排层职责细化

## 5.1 `project_flow_coordinator.py`

### 作用

这是“项目推进总控”。

### 应负责

- `POST /projects/:id/continue` 这类跨对象动作
- autopilot/checkpoint 差异
- 阶段切换逻辑
- 项目最小闭环推进

### 典型函数

- `continue_project()`
- `advance_from_brief_confirmed()`
- `advance_after_stage_completed()`
- `replan_project()`

---

## 5.2 `decision_effects.py`

### 作用

把“决策处理后会发生什么”单独抽出来。

### 应负责

- `scope_confirmation` 批准后的副作用
- `direction_confirmation` 拒绝后的副作用
- `release_approval` 批准/拒绝后的副作用

### 为什么单独拆出来

因为这部分最容易在 service 里越写越乱。

把它抽出来可以让：

- 决策对象逻辑更干净
- 各决策类型副作用更可读
- 测试更好写

---

## 5.3 `stage_execution_adapter.py`

### 作用

连接新 `StageRun` 和未来/过渡期执行器。

### 应负责

- 根据 `stage_type` 选择执行策略
- 调旧 pipeline engine 或新执行器
- 接住执行结果并回写新模型

### 关键原则

新 `StageRun` 是主语义；
执行器只是执行手段。

---

## 5.4 `legacy_pipeline_adapter.py`

### 作用

在过渡期复用旧 pipeline 执行能力。

### 应负责

- 将新阶段推进请求映射成旧 engine 能理解的输入
- 将旧阶段结果映射成新 `StageRun` / `Asset` / `Event`

### 不应负责

- 直接成为新业务 API 的中心
- 让新 route 直接依赖旧 `PipelineRun` / `PipelineStage`

---

## 6. 路由层实施建议

## 6.1 新旧 route 并行，不混写

当前已有：

- `routes/api.py`
- `routes/pipeline.py`
- `routes/audit.py`
- `routes/websocket.py`

建议新增：

- `routes/projects_v2.py`
- `routes/assets_v2.py`
- `routes/decisions_v2.py`
- `routes/stage_runs_v2.py`
- `routes/dashboard_v2.py`

### 原因

如果继续把新内核 route 塞回旧 `api.py`，会导致：

- 新旧语义继续打架
- 代码审查和维护都很痛苦
- 更难判断哪些接口已经真正迁出旧世界

---

## 6.2 每个 route 文件只做一件事

### `projects_v2.py`

- `POST /projects`
- `GET /projects`
- `GET /projects/:id`
- `PATCH /projects/:id`
- `POST /projects/:id/continue`
- `POST /projects/:id/replan`

### `assets_v2.py`

- `GET /projects/:id/assets`
- `GET /assets/:id`
- `GET /assets/:id/versions`
- `GET /assets/:id/links`
- `PATCH /assets/:id`
- `POST /assets/:id/regenerate`
- `POST /assets/:id/approve`

### `decisions_v2.py`

- `GET /decisions`
- `GET /projects/:id/decisions`
- `GET /decisions/:id`
- `POST /decisions/:id/resolve`

### `stage_runs_v2.py`

- `GET /projects/:id/stage-runs`
- `GET /stage-runs/:id`
- `POST /stage-runs/:id/retry`
- `POST /stage-runs/:id/cancel`

### `dashboard_v2.py`

- `GET /dashboard`
- `GET /projects/:id/overview`
- `GET /projects/:id/release`

---

## 7. 推荐实施顺序

## 7.1 Phase A: 立新模型和 service 空壳

先做：

- 新 ORM / schema
- `services/` 目录
- 最小 service 函数骨架
- 新 route 文件骨架

目标：

- 让新代码结构先成立
- 先不要急着接旧 pipeline

---

## 7.2 Phase B: 打通最小闭环

先只打这条：

1. `POST /projects`
2. 创建 `project_brief`
3. 创建 `scope_confirmation`
4. `POST /decisions/:id/resolve`
5. 项目进入 `brief_confirmed`

这个阶段先不强求真正调旧 engine。

目标：

- 验证新对象和状态链是活的
- 验证新 API 和 service 层没有滑回旧主轴

---

## 7.3 Phase C: 补聚合读模型

再做：

- `GET /dashboard`
- `GET /projects/:id/overview`
- `GET /projects/:id/assets`
- `GET /projects/:id/decisions`
- `GET /projects/:id/stage-runs`

目标：

- 新前端可以开始吃新读模型
- 页面主信息源切出旧 chatroom/pipeline

---

## 7.4 Phase D: 接执行适配器

再做：

- `stage_execution_adapter.py`
- `legacy_pipeline_adapter.py`
- 新 `StageRun` 到旧执行引擎的桥接

目标：

- 复用现有能力
- 但保证产品语义仍是新的

---

## 7.5 Phase E: 补任务、AgentRun、审计重挂接

最后做：

- `tasks`
- `agent_runs`
- `llm_calls/tool_calls/events` 新引用字段
- 更细的阶段执行可观测能力

目标：

- 让执行观察面完整
- 不影响前面主链路先落地

---

## 8. 明确哪些事现在不要做

## 8.1 不要先重构 `agents/`

原因：

- Agent runtime 不是当前主矛盾
- 先动它容易把项目拖进“执行细节泥潭”

## 8.2 不要先重写旧 pipeline engine

原因：

- 先把业务骨架立住更重要
- 过早重写引擎，风险高、收益低

## 8.3 不要先做新前端大改版

原因：

- 如果后端主语义还没立住，前端只会变成更漂亮的假新架构

## 8.4 不要让 route 直接写复杂状态流转

原因：

- 这会让逻辑散落
- 后面很难测试和维护

---

## 9. 建议目录蓝图

P0/P1 可参考如下目标结构：

```text
backend/
  models/
    database.py
    audit.py
  services/
    project_service.py
    asset_service.py
    decision_service.py
    stage_run_service.py
    dashboard_service.py
    release_service.py
  orchestration/
    project_flow_coordinator.py
    decision_effects.py
    stage_execution_adapter.py
    legacy_pipeline_adapter.py
  adapters/
    pipeline_to_stage_run.py
    stage_artifact_to_asset.py
    legacy_events_adapter.py
    chat_context_adapter.py
  routes/
    api.py
    pipeline.py
    projects_v2.py
    assets_v2.py
    decisions_v2.py
    stage_runs_v2.py
    dashboard_v2.py
```

注意：

- 这是目标结构，不必一次到位
- P0 可以先只加 `services/`、`orchestration/`、新的 `routes/*_v2.py`

---

## 10. 最终实施判断

如果把这次重构看成一次真正的“换心脏”，那么顺序必须是：

- 先立新业务对象
- 再立新 service 层
- 再立新 route 层
- 再接旧执行能力
- 最后才清理旧页面和旧入口

如果顺序反过来，比如先改前端、先改 Agent、先改 pipeline engine，就会高概率再次失焦。

---

## 11. 一句话总结

**Catown 下一阶段代码实施的关键，不是继续往旧 `routes/api.py` 和旧 `pipeline/` 上打补丁，而是新增以 `services + orchestration + v2 routes` 为核心的新业务骨架：先让 `Project / Asset / Decision / StageRun` 在代码层拥有自己的服务编排中心，再通过 adapter 有节制地吸收旧执行能力。**
