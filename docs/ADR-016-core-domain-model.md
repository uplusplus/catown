# ADR-016: 核心领域模型定义（Project / Asset / Decision / StageRun）

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-013-business-architecture-solo-app-factory.md`, `docs/ADR-014-project-flow-and-state-machine.md`, `docs/ADR-015-current-architecture-gap-analysis.md`

---

## 1. 目标

本文档的目标不是继续讨论产品定位，也不是直接进入代码实现，而是先把 Catown 下一阶段的**核心领域模型**钉死。

要回答的问题是：

**如果 Catown 的产品主轴要从 `chatroom/message + pipeline` 切换到 `project/asset/decision/stage_run`，那么这四个核心对象到底分别是什么、彼此如何关联、谁负责什么、不负责什么？**

本文档的定位是：

- 给后续 API 设计提供统一对象定义
- 给数据库设计提供建模边界
- 给前端信息架构提供主状态来源
- 给旧系统迁移提供语义锚点

---

## 2. 核心设计原则

### 2.1 项目优先，不再聊天优先

Catown 的主业务对象必须是：

- 项目状态
- 正式资产
- 人工决策
- 阶段推进实例

而不是：

- 会话消息
- Agent 发言
- Pipeline 内部消息

### 2.2 正式对象与执行对象分离

必须明确区分两类东西：

#### 正式业务对象

这些对象代表产品层事实：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

#### 执行与审计对象

这些对象代表系统如何完成工作：

- `Task`
- `AgentRun`
- `ExecutionLog`
- `LLMCall`
- `ToolCall`
- `Event`

原则：

**用户买的是项目推进和交付结果，不是执行细节本身。**

### 2.3 资产是正式产物，不是阶段附件

`Asset` 不能再是某个阶段上的附属文件记录。

它必须是：

- 有类型
- 有版本
- 有依赖
- 有状态
- 能被批准
- 能成为后续阶段输入

的正式业务对象。

### 2.4 决策是业务对象，不是内部 Gate 标记

`Decision` 不是引擎里的一个暂停 flag。

它必须能独立回答：

- 为什么要用户确认
- 当前有哪些可选项
- 系统建议选什么
- 该决策影响哪些资产和阶段
- 决策被批准或拒绝后，系统应该发生什么

### 2.5 阶段实例是项目推进单元，不是产品主入口

`StageRun` 很重要，但它的职责是记录和承载一次阶段推进。

它不是：

- 项目本身
- 用户主要心智入口
- 资产的替代品

也就是说：

**项目由 `Project` 持有主状态，阶段由 `StageRun` 表达过程。**

---

## 3. 领域总图

P0 阶段建议把核心领域关系收敛为如下结构：

```text
Project
  ├── Asset (many)
  ├── Decision (many)
  ├── StageRun (many)
  ├── Task (many, execution layer)
  └── AgentRun (many, audit layer)

StageRun
  ├── consumes Asset (many)
  ├── produces Asset (many)
  ├── may create Decision (0..many)
  ├── contains Task (many)
  └── contains AgentRun (many)

Decision
  ├── belongs to Project
  ├── may relate to StageRun
  └── references Asset (many)

Asset
  ├── belongs to Project
  ├── may be produced by StageRun
  ├── may supersede Asset
  └── may be referenced by Decision
```

其中主从关系必须明确：

- `Project` 是全局聚合根
- `Asset` / `Decision` / `StageRun` 是 Project 下的一等对象
- `Task` / `AgentRun` 属于执行层附属对象

---

## 4. Project 定义

### 4.1 定义

`Project` 是 Catown 中最顶层的业务容器，代表一个被持续推进的产品目标。

它负责表达：

- 项目要做什么
- 项目现在推进到哪里
- 项目当前健康度如何
- 当前卡点是什么
- 是否允许继续自动推进

### 4.2 应承担的职责

`Project` 必须承担：

- 全局业务状态归口
- 顶层推进模式归口
- 当前阶段索引归口
- 风险与阻塞归口
- 全局汇总视图归口

### 4.3 不应承担的职责

`Project` 不应直接承担：

- 聊天历史容器
- 具体任务执行明细
- 单个 Agent 的运行日志
- 阶段内部中间态存储

### 4.4 建议字段

#### 基础身份字段

- `id`
- `slug`
- `name`
- `description`
- `owner_id`（P0 可先保留单用户语义）

#### 业务目标字段

- `one_line_vision`
- `target_users`
- `target_platforms`
- `primary_outcome`
- `references`

#### 推进控制字段

- `status`
- `current_stage`
- `execution_mode`
- `health_status`
- `autopilot_enabled`

#### 运行摘要字段

- `current_focus`
- `blocking_reason`
- `last_decision_id`
- `latest_summary`

#### 时间字段

- `created_at`
- `updated_at`
- `last_activity_at`
- `released_at`

### 4.5 状态机建议

P0 建议保留以下主状态：

- `draft`
- `brief_confirmed`
- `defining`
- `building`
- `testing`
- `release_ready`
- `released`
- `blocked`

### 4.6 核心约束

- 一个 `Project` 同时只能有一个 `current_stage`
- 一个 `Project` 在任意时刻允许存在多个 `StageRun`，但只能有一个主推进 `StageRun`
- `Project.status` 必须是用户可理解的业务状态，而不是引擎内部状态映射值

---

## 5. Asset 定义

### 5.1 定义

`Asset` 是 Catown 的正式交付对象，代表在项目推进中形成的、可被审阅、可被批准、可被后续阶段消费的正式产物。

它是产品层最重要的数据对象之一。

### 5.2 Asset 与旧 StageArtifact 的本质区别

旧 `StageArtifact` 更像：

- 某阶段产出的附件
- 引擎附属记录
- 偏内部留档

新 `Asset` 必须是：

- 项目正式资产
- 可独立展示和审阅
- 可形成版本链
- 可建立依赖链
- 可成为决策依据

### 5.3 P0 固定资产类型

P0 先固定 8 类正式资产：

- `project_brief`
- `prd`
- `ux_blueprint`
- `tech_spec`
- `task_plan`
- `build_artifact`
- `test_report`
- `release_pack`

### 5.4 统一字段

#### 身份字段

- `id`
- `project_id`
- `asset_type`
- `title`

#### 正文字段

- `content_json`
- `content_markdown`（可选）
- `summary`

#### 生命周期字段

- `version`
- `status`
- `supersedes_asset_id`
- `is_current`

#### 归属与来源字段

- `owner_agent`
- `produced_by_stage_run_id`
- `source_input_refs`
- `approval_decision_id`

#### 时间字段

- `created_at`
- `updated_at`
- `approved_at`

### 5.5 状态机建议

P0 建议统一为：

- `draft`
- `in_review`
- `approved`
- `superseded`
- `blocked`

### 5.6 资产版本规则

每一种 `Asset` 必须支持版本演化：

- 同一项目、同一 `asset_type` 允许多个版本存在
- 最新有效版本应通过 `is_current = true` 或等价逻辑标记
- 新版本出现后，旧版本通常进入 `superseded`
- 被明确批准的版本，不应被“静默覆盖”，必须通过新版本替代

### 5.7 资产依赖规则

建议使用 `asset_links` 或等价结构表达：

- 哪个资产依赖哪个资产
- 哪个资产由哪些资产派生
- 哪个资产是哪个资产的补充输出

P0 核心依赖链：

- `project_brief -> prd`
- `prd -> ux_blueprint`
- `prd + ux_blueprint -> tech_spec`
- `tech_spec -> task_plan`
- `task_plan -> build_artifact`
- `build_artifact -> test_report`
- `test_report + product_metadata -> release_pack`

### 5.8 Asset 的边界

`Asset` 不负责：

- 保存每一步对话
- 承担任务调度职责
- 作为实时运行状态来源

它负责的是：

**正式产物与正式输入输出关系。**

---

## 6. Decision 定义

### 6.1 定义

`Decision` 是 Catown 中的人工确认对象，用来表达“系统推进到某个关键节点，需要用户拍板”。

它是人机协同的正式界面，而不是引擎内部临时暂停点。

### 6.2 决策对象必须能回答的五个问题

一个合格的 `Decision` 必须能回答：

1. 为什么现在需要确认
2. 不确认会影响什么
3. 系统推荐哪个选项
4. 还有哪些替代选项
5. 这个决定会影响哪些资产或阶段

### 6.3 P0 固定决策类型

- `scope_confirmation`
- `direction_confirmation`
- `release_approval`

必要时可预留：

- `replan_confirmation`
- `risk_acceptance`

但不建议在 P0 一开始铺太多类型。

### 6.4 建议字段

#### 身份字段

- `id`
- `project_id`
- `stage_run_id`（可为空）
- `decision_type`
- `title`

#### 决策内容字段

- `context_summary`
- `recommended_option`
- `alternative_options`
- `impact_summary`
- `requested_action`

#### 生命周期字段

- `status`
- `resolved_option`
- `resolution_note`

#### 关联字段

- `related_asset_ids`
- `blocking_stage_run_id`
- `created_by_system_reason`

#### 时间字段

- `created_at`
- `resolved_at`
- `expires_at`

### 6.5 状态机建议

P0 建议：

- `pending`
- `approved`
- `rejected`
- `expired`

### 6.6 关键约束

- 同一 `Project` 在同一决策类型上可以有多次历史记录，但同时只应存在一个活跃的 `pending` 决策
- `Decision` 被 `approved` 或 `rejected` 后，必须触发明确的后续动作，而不是只改个状态
- 首页待办区、项目待办区、发布中心待办区，都应该直接消费 `Decision`，而不是自己推断 Gate 状态

### 6.7 Decision 的边界

`Decision` 不负责：

- 存储完整资产正文
- 承担实时执行细节
- 替代项目状态

它负责的是：

**把“要不要继续、往哪边走、能不能发布”变成可追踪的正式对象。**

---

## 7. StageRun 定义

### 7.1 定义

`StageRun` 表示项目在某一阶段上的一次推进实例。

它不是静态阶段定义，而是一次实际发生过的执行过程。

例如：

- 第一次 Brief 生成是一条 `StageRun`
- 第二次重做 Brief 也是另一条 `StageRun`
- 某次 QA 回退重跑，也是一条新的 `StageRun`

### 7.2 StageRun 的意义

`StageRun` 让系统能够表达：

- 这个阶段执行过几次
- 这次推进由谁触发
- 消耗了哪些输入资产
- 产出了哪些正式资产
- 是成功、失败，还是等待决策

### 7.3 P0 阶段类型建议

- `briefing`
- `product_definition`
- `solution_design`
- `build_execution`
- `qa_validation`
- `release_preparation`
- `post_launch_iteration`

### 7.4 建议字段

#### 身份字段

- `id`
- `project_id`
- `stage_type`
- `run_index`

#### 控制字段

- `status`
- `triggered_by`
- `trigger_reason`
- `execution_mode_snapshot`

#### 输入输出字段

- `input_asset_ids`
- `output_asset_ids`
- `decision_ids`
- `summary`

#### 运行字段

- `started_at`
- `ended_at`
- `failed_reason`
- `checkpoint_summary`

### 7.5 状态机建议

P0 建议统一为：

- `queued`
- `running`
- `waiting_for_decision`
- `completed`
- `failed`
- `cancelled`

### 7.6 与 Project 的关系

- `Project` 决定全局状态
- `StageRun` 决定阶段推进过程
- `Project.current_stage` 应来自最近一次有效 `StageRun` 的业务归纳，而不是反过来让阶段对象决定项目语义

### 7.7 与执行层对象的关系

`StageRun` 下可以挂：

- `Task`
- `AgentRun`
- `ExecutionLog`

但这些都是执行层观察面。

`StageRun` 本身仍应保持业务可读性，避免退化成纯引擎对象。

### 7.8 StageRun 的边界

`StageRun` 不应承担：

- 项目总览聚合根职责
- 资产版本管理职责
- 决策结果长期持有职责

它负责的是：

**表达一次阶段推进行为。**

---

## 8. 四大对象之间的关系约束

### 8.1 Project -> Asset

- 一个项目可以有多个资产
- 同一类型资产允许多版本
- 项目页应默认展示每类资产的当前版本

### 8.2 Project -> Decision

- 一个项目可以有多个历史决策
- 项目首页和 Dashboard 应优先聚合 `pending` 决策

### 8.3 Project -> StageRun

- 一个项目有阶段历史
- 每次阶段重试、回滚、重跑都应形成新的 `StageRun`

### 8.4 StageRun -> Asset

- 一个阶段可以消费多个输入资产
- 一个阶段可以产出多个输出资产
- 阶段与资产的关系必须显式记录，而不是只靠日志推断

### 8.5 StageRun -> Decision

- 某些阶段会创建决策对象
- 当决策成为阶段阻塞原因时，应显式记录 `blocking_stage_run_id`

### 8.6 Decision -> Asset

- 决策必须引用其上下文资产
- 用户做决策时，应能直接看到关联资产而不是回头翻日志

---

## 9. 建模反例：明确不要做什么

### 9.1 不要让 Project 继续只是薄壳

不能只在旧 `Project` 上多加几个字段，然后继续把真实语义放在聊天室和 Pipeline 中。

### 9.2 不要把 Asset 做成 StageArtifact 改名版

如果 `Asset` 仍然只能附着在 `PipelineStage` 上，那只是换名，不是重构。

### 9.3 不要把 Decision 做成一个布尔开关

`Decision` 不是 `needs_approval = true`。

它必须是一个完整对象。

### 9.4 不要把 StageRun 直接等同 PipelineRun

如果 `StageRun` 完全继承旧 `PipelineRun` 的产品语义，那么新模型还是会被旧世界观拖回去。

---

## 10. P0 最小可实现模型

如果要尽快落地第一阶段，建议 P0 先收敛到下面这组最小核心字段。

### 10.1 Project（P0 最小集）

- `id`
- `name`
- `one_line_vision`
- `status`
- `current_stage`
- `execution_mode`
- `health_status`
- `blocking_reason`
- `created_at`
- `updated_at`

### 10.2 Asset（P0 最小集）

- `id`
- `project_id`
- `asset_type`
- `title`
- `content_json`
- `version`
- `status`
- `supersedes_asset_id`
- `produced_by_stage_run_id`
- `created_at`
- `updated_at`

### 10.3 Decision（P0 最小集）

- `id`
- `project_id`
- `decision_type`
- `title`
- `context_summary`
- `recommended_option`
- `status`
- `related_asset_ids`
- `created_at`
- `resolved_at`

### 10.4 StageRun（P0 最小集）

- `id`
- `project_id`
- `stage_type`
- `status`
- `triggered_by`
- `summary`
- `started_at`
- `ended_at`

---

## 11. 建模结论

从领域建模角度，Catown 下一阶段必须承认以下事实：

- `Project` 是业务聚合根
- `Asset` 是正式交付骨架
- `Decision` 是人机协同入口
- `StageRun` 是阶段推进记录

这四个对象一起定义了产品主轴。

而：

- `Chatroom`
- `Message`
- `Pipeline`
- `PipelineMessage`

最多只能作为兼容层、协作层或执行层存在，不能继续担任产品中心。

---

## 12. 一句话总结

**Catown 的新内核应以 `Project` 统领全局业务状态，以 `Asset` 承载正式交付物，以 `Decision` 承载关键人工拍板，以 `StageRun` 承载阶段推进过程；只有把这四个对象的边界先钉死，后续 API、数据库、前端和执行引擎改造才不会继续滑回旧的 `chatroom/message + pipeline` 世界观。**
