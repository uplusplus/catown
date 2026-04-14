# ADR-018: 旧模型到新内核的迁移映射表

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-015-current-architecture-gap-analysis.md`, `docs/ADR-016-core-domain-model.md`, `docs/ADR-017-main-api-design.md`

---

## 1. 目标

本文档的目标不是重复说明为什么要重构，而是专门回答一个工程落地问题：

**Catown 当前已有的表、模型、API、运行时模块，分别应该在新架构里变成什么、保留什么、降级什么、废弃什么？**

如果没有这份映射表，后面在实现时很容易出现三种坏结果：

- 看到旧代码就想硬复用，导致新模型被旧语义污染
- 因为害怕破坏现有能力而不敢清理，最后长期双主模型并存
- 前端、后端、引擎三边对“谁才是正式对象”理解不一致

因此本文档的定位是：

- 给数据库迁移提供落点
- 给 API 重做提供边界
- 给旧模块保留/吸收/废弃提供决策依据
- 给分阶段实施提供顺序参考

---

## 2. 映射原则

### 2.1 先看语义，不先看代码

迁移时不能先问：

- 这个旧表能不能直接复用
- 这个字段能不能原样搬过来

必须先问：

- 它在新世界里到底还有没有正确语义
- 它属于业务层、执行层，还是兼容层

### 2.2 一切以新聚合根为准

新内核聚合根是：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

旧对象只有在能被清楚挂到这四个核心对象之下时，才应该保留或吸收。

### 2.3 保留能力，不保留错位主轴

以下能力值得保留：

- Agent runtime
- 工具调用
- 审计采集
- 事件广播
- 执行调度

但以下主轴不应继续保留：

- `chatroom/message` 作为主入口
- `pipeline` 作为产品主骨架

### 2.4 新写入先走新模型

对于旧项目历史数据，可以容忍只读和延迟迁移。

对于新项目，必须优先写入新模型，不能继续把新业务主数据落到旧聊天/流水线语义里。

---

## 3. 当前对象总览

基于现有后端模型，当前主要对象包括：

### 3.1 业务/协作对象

- `Agent`
- `Project`
- `Chatroom`
- `AgentAssignment`
- `Message`
- `Memory`

### 3.2 Pipeline 对象

- `Pipeline`
- `PipelineRun`
- `PipelineStage`
- `StageArtifact`
- `PipelineMessage`

### 3.3 审计对象

- `LLMCall`
- `ToolCall`
- `Event`

这些对象并不都要删除，但必须重新分层。

---

## 4. 核心映射总表

| 旧对象 | 当前语义 | 新世界定位 | 处理策略 | 说明 |
|---|---|---|---|---|
| `Project` | 聊天室 + Pipeline 宿主 | 新 `Project` 聚合根 | 重写 | 保留名字，不保留旧语义 |
| `Chatroom` | 主交互入口 | 协作/日志视图 | 降级兼容 | 不再做项目主入口 |
| `Message` | 主交互记录 | 协作消息 / 历史上下文 | 降级兼容 | 不再做正式业务对象 |
| `AgentAssignment` | Agent 与项目绑定 | 项目执行配置 / 成员配置 | 吸收改造 | 可并入新 project config/member 结构 |
| `Memory` | Agent 记忆 | Agent runtime 内部能力 | 保留 | 不进入主业务内核 |
| `Pipeline` | 项目执行主骨架 | 内部编排配置 / 可被吸收 | 吸收后降级 | 不再做产品层对象 |
| `PipelineRun` | 一次 pipeline 运行 | `StageRun` 设计参考来源 | 不直接复用 | 语义需拆解 |
| `PipelineStage` | pipeline 阶段记录 | `StageRun` / execution unit 参考来源 | 不直接复用 | 旧阶段语义过重 |
| `StageArtifact` | 阶段产出附件 | 新 `Asset` 的参考来源 | 重写 | 不能直接升级为 `Asset` |
| `PipelineMessage` | Agent 间协作消息 | 执行期内部消息 / 审计 | 降级兼容 | 不做正式沟通骨架 |
| `Agent` | 可执行角色 | Agent runtime 核心对象 | 保留 | 属于执行层 |
| `LLMCall` | 大模型调用审计 | 审计层 | 保留并重挂接 | 外键目标需逐步迁移 |
| `ToolCall` | 工具调用审计 | 审计层 | 保留并重挂接 | 同上 |
| `Event` | 事件流 | 事件与观测层 | 保留并重挂接 | 事件 payload 语义需迁移 |

---

## 5. 逐对象迁移判断

## 5.1 `Project`

### 当前问题

当前 `backend/models/database.py` 中的 `Project` 只有：

- `name`
- `description`
- `status`

并通过关系主要挂载：

- `chatroom`
- `agent_assignments`
- `pipeline`

这说明它还不是业务聚合根，而是旧世界两个主系统的共同父节点。

### 新定位

新 `Project` 必须成为：

- 顶层业务容器
- 状态机归口
- 当前阶段归口
- 风险/阻塞归口
- 全局汇总归口

### 处理策略

**保留 `Project` 这个名称，但重写表语义与字段。**

### 迁移建议

- 旧 `projects` 表可保留主键与基础身份字段
- 新增字段如：
  - `one_line_vision`
  - `current_stage`
  - `execution_mode`
  - `health_status`
  - `blocking_reason`
  - `current_focus`
- 与 `chatroom`、`pipeline` 的一对一主关系应被降级

### 结论

`Project` 是**唯一建议保留名称但重写语义**的旧核心对象。

---

## 5.2 `Chatroom`

### 当前语义

`Chatroom` 是用户与 Agent 的主要交互入口。

新消息流、上下文累积、Agent 回复都围绕它展开。

### 新定位

在新架构中，`Chatroom` 最多只能作为：

- 协作视图
- 运行日志视图
- 上下文辅助视图

### 处理策略

**降级兼容，不纳入主产品内核。**

### 保留价值

- 便于查看历史讨论
- 便于保留 Agent 协作语境
- 可作为调试和审计入口

### 不再承担的职责

- 项目主状态来源
- 项目推进入口
- 用户确认入口
- 正式产物主视图

### API 处理建议

旧：

- `/chatrooms/{id}/messages`
- `/chatrooms/{id}/messages/stream`

新定位：

- 协作记录查询
- 内部上下文辅助
- 可在 Mission Board 中作为次级 tab 存在

---

## 5.3 `Message`

### 当前语义

`Message` 是主交互数据单元。

### 新定位

在新架构里，它只能代表：

- 协作消息
- 执行对话历史
- 某些资产生成时的背景上下文

### 处理策略

**降级兼容。**

### 重要提醒

不能再让前端通过 message 流拼出项目当前状态。

如果需要展示“项目现在发生了什么”，应优先来自：

- `Project`
- `StageRun`
- `Decision`
- `Asset`

而不是消息推断。

---

## 5.4 `AgentAssignment`

### 当前语义

表示项目和 Agent 的关联。

### 新定位

这个对象本身不是错误的，但表达方式太贴近旧“项目聊天室配几个 Agent”的思路。

新世界里，更合理的语义可能是：

- 项目执行配置
- 项目角色编排配置
- 阶段默认责任 Agent 配置
- 项目成员/能力配置

### 处理策略

**吸收改造，不作为独立产品中心对象。**

### 建议去向

两种可行路径：

#### 路径 A：并入 `Project` 配置

例如：

- `default_agent_roles`
- `execution_profile`
- `collaboration_mode`

#### 路径 B：演化为项目成员/能力表

例如未来的：

- `project_members`
- `project_agent_profiles`

### P0 建议

先不把它放入新主 API 中，只在执行层保留。

---

## 5.5 `Memory`

### 当前语义

这是 Agent 记忆表。

### 新定位

它属于 Agent runtime 内部能力，而不是产品业务核心。

### 处理策略

**直接保留。**

### 原因

- 记忆是执行质量问题，不是业务建模问题
- 与项目聚合根关系弱
- 不值得卷入本轮核心重构

---

## 5.6 `Pipeline`

### 当前语义

`Pipeline` 是项目执行主骨架：

- 持有项目级运行状态
- 管理当前阶段索引
- 是 `PipelineRun` 的宿主

### 新定位

在新架构里，它不应再作为产品层对象暴露。

它最多可退化为：

- 内部编排配置对象
- 新执行引擎的过渡宿主
- 与 Stage 模板有关的底层配置对象

### 处理策略

**吸收后降级。**

### 关键判断

- 不建议让 `Pipeline = StageRun`
- 也不建议让前端继续直接使用 `Pipeline.status`

### 迁移建议

- 项目级推进状态迁移到 `Project`
- 当前阶段实例迁移到 `StageRun`
- 旧 pipeline 模板/配置可作为引擎层保留

---

## 5.7 `PipelineRun`

### 当前语义

表示 pipeline 的一次运行，包含：

- `run_number`
- `status`
- `input_requirement`
- `workspace_path`

并挂载：

- `PipelineStage`
- `PipelineMessage`

### 为什么不能直接变成 `StageRun`

因为它的语义是：

- 一整条 pipeline 的运行实例

而新 `StageRun` 的语义是：

- 某一个阶段的一次推进实例

这两个粒度不同。

### 新定位

`PipelineRun` 更适合作为：

- 旧执行引擎的批次运行概念
- 新执行层中某种 session/run 容器的参考来源

### 处理策略

**不直接复用为新主模型。**

### 迁移建议

- 它的部分字段可拆到新 `StageRun` 或未来 `ExecutionRun`
- 但不应原封不动升格

---

## 5.8 `PipelineStage`

### 当前语义

代表 pipeline 中的阶段记录，已经很接近阶段语义。

字段包括：

- `stage_name`
- `display_name`
- `agent_name`
- `status`
- `gate_type`
- `input_context`
- `output_summary`
- `retry_count`

### 为什么不能直接变成新 `StageRun`

原因不是它不重要，而是它仍深度绑定旧 pipeline 体系：

- 从属于 `PipelineRun`
- 状态和 gate 逻辑偏引擎视角
- 输入输出没有和正式资产模型分离

### 新定位

它是新 `StageRun` 最重要的参考对象之一，但不是可以直接复用的最终对象。

### 处理策略

**重做业务语义，吸收字段经验。**

### 可吸收内容

- `stage_name` -> `stage_type`
- `status`
- `output_summary` -> `summary`
- `started_at` / `completed_at`
- `retry_count` 可折叠为新 run 历史语义

### 不应直接继承的部分

- `gate_type` 直接承载业务决策语义
- `input_context` 作为万能 JSON 黑盒
- 对 `StageArtifact` 的附属关系

---

## 5.9 `StageArtifact`

### 当前语义

它表示某个阶段产出的文件/目录记录：

- `artifact_type`
- `file_path`
- `summary`

### 为什么不能直接升级为 `Asset`

因为它本质仍是：

- 阶段附件
- 文件留档
- 偏引擎视图

而新 `Asset` 必须是：

- 正式业务产物
- 有版本
- 有审批状态
- 有依赖链
- 有统一类型系统

### 处理策略

**重写。**

### 可吸收内容

- 文件路径或落地产物引用
- 内容摘要
- 与阶段的来源关系

### 必须新增的能力

- `asset_type`
- `version`
- `status`
- `supersedes_asset_id`
- `approval_decision_id`
- `is_current`

### 结论

`StageArtifact` 是最典型的“看似接近，实际上不能直接复用”的对象。

---

## 5.10 `PipelineMessage`

### 当前语义

它记录 Agent 间消息与人工指令：

- `STAGE_OUTPUT`
- `AGENT_QUESTION`
- `AGENT_REPLY`
- `HUMAN_INSTRUCT`

### 新定位

它应退化为：

- 执行期内部消息
- 调试与审计材料
- 协作记录的一部分

### 处理策略

**降级兼容。**

### 重要原则

不能再让这类消息承担正式沟通骨架。

比如：

- “用户要不要批准发布” -> 应来自 `Decision`
- “当前正式输出是什么” -> 应来自 `Asset`
- “项目现在卡在哪” -> 应来自 `Project` / `StageRun`

而不是从 `PipelineMessage` 中猜。

---

## 5.11 `Agent`

### 当前语义

`Agent` 保存：

- 身份
- 角色
- soul/config
- tools/skills

### 新定位

它仍然是执行层核心对象。

### 处理策略

**保留。**

### 原因

- Agent 不是本轮业务中心错位的根源
- 它属于“谁来执行”的层，而不是“产品对象是什么”的层
- 可以在后续仅做边界清理，不必先重构

### 注意事项

不要让 Agent 再反向成为前端主心智中心。

用户看到的应该是：

- 项目正在什么阶段
- 产出了什么
- 等待什么决策

而不是主要看“哪个 Agent 说了什么”。

---

## 5.12 `LLMCall` / `ToolCall` / `Event`

### 当前语义

这三类对象构成审计与观测层：

- `LLMCall` 记录 prompt/response/token
- `ToolCall` 记录工具执行
- `Event` 记录阶段流转、gate、错误、消息等事件

### 新定位

它们仍然成立，而且价值很高。

### 处理策略

**保留并重挂接。**

### 需要迁移的不是对象本身，而是引用语义

当前审计对象大量绑定：

- `run_id -> pipeline_runs`
- `stage_id -> pipeline_stages`
- `stage_name`

新世界里应逐步增加或迁移为：

- `project_id`
- `stage_run_id`
- `decision_id`（必要时）
- `asset_id`（必要时）

### 迁移策略建议

#### Phase 1

先保留旧字段，补新字段。

#### Phase 2

新写入优先使用新引用字段。

#### Phase 3

前端和审计查询逐步改读新语义。

#### Phase 4

旧字段仅保留兼容或逐步废弃。

---

## 6. 表级迁移建议

## 6.1 建议新增的新主表

P0/P1 建议新增：

- `projects`（扩展重构）
- `assets`
- `asset_links`
- `decisions`
- `stage_runs`
- `stage_run_assets`（可选，若不用数组/JSON 方案）
- `project_members` 或 `project_agent_profiles`（可后置）

## 6.2 建议保留的旧表

- `agents`
- `memories`
- `llm_calls`
- `tool_calls`
- `events`

## 6.3 建议降级保留的旧表

- `chatrooms`
- `messages`
- `pipeline_messages`
- `pipelines`
- `pipeline_runs`
- `pipeline_stages`
- `stage_artifacts`

注意：

这里的“降级保留”不是说永远不动，而是说在 P0/P1 阶段它们可以继续服务兼容和过渡，不再是新主链路写入中心。

---

## 7. API 级迁移建议

## 7.1 新主 API

应新增并优先接入：

- `POST /projects`
- `GET /projects`
- `GET /projects/:id`
- `POST /projects/:id/continue`
- `GET /projects/:id/assets`
- `GET /projects/:id/decisions`
- `POST /decisions/:id/resolve`
- `GET /projects/:id/stage-runs`
- `GET /dashboard`
- `GET /projects/:id/overview`

## 7.2 旧 API 处理策略

### `chatrooms/*`

- 保留读取能力
- 降级为协作/日志接口
- 不再作为主产品入口

### `pipelines/*`

- 保留内部执行与兼容能力
- 不再作为新页面主数据源

### 原 `projects/*`

若旧 `projects` API 仍返回：

- `chatroom_id`
- pipeline 视图拼装字段

则应：

- 逐步替换
- 或明确标记为 legacy

---

## 8. 模块级迁移建议

## 8.1 建议保留的模块方向

- `backend/agents/*`
- `backend/tools/*`
- `backend/models/audit.py`
- 事件总线 / WebSocket 推送能力
- pipeline engine 中的调度和工具循环能力

## 8.2 建议重写的模块方向

- 新业务模型层
- 新主 API routes
- 新页面聚合读模型
- 项目创建与推进服务层

## 8.3 建议降级的模块方向

- `backend/chatrooms/*`
- 旧 `backend/routes/api.py` 中 chat-first 路由
- `backend/routes/pipeline.py` 作为产品主入口的角色

---

## 9. 推荐实施顺序

### Step 1: 先加新表和新服务，不拆旧表

目的：

- 保证重构有安全垫
- 先让新世界跑起来

### Step 2: 新项目只写新模型

目的：

- 确保新世界是干净的
- 避免继续积累旧语义债务

### Step 3: 用 adapter 吸收旧执行引擎

例如：

- 旧阶段执行结果 -> 新 `StageRun`
- 旧产物记录 -> 新 `Asset` 引用信息
- 旧事件 -> 新事件语义

### Step 4: 新前端只吃新 API

目的：

- 彻底验证新主轴是否站得住
- 避免前端继续混读旧对象

### Step 5: 旧入口降级为辅助视图

最后再做：

- 旧聊天视图收口
- 旧 pipeline 页面收口
- 逐步停止新写入

---

## 10. 最终映射结论

可以把这次迁移浓缩成一句工程判断：

- `Project`：保留名字，重写语义
- `Asset`：不能从 `StageArtifact` 直接升级，必须重建
- `Decision`：旧系统没有真正对应物，必须新建
- `StageRun`：可借鉴 `PipelineStage`，但不能直接等同
- `Chatroom/Message/PipelineMessage`：全部降级为协作/审计层
- `Pipeline/PipelineRun/PipelineStage`：只保留底层执行参考价值，不再做产品主骨架
- `Agent/Memory/LLMCall/ToolCall/Event`：作为执行与观测基础设施保留并重挂接

---

## 11. 一句话总结

**Catown 的迁移核心不是把旧对象逐个重命名，而是把旧 `chatroom/message + pipeline` 双主轴拆开：保留 Agent、工具、审计、执行这些底层能力；重建 `Project / Asset / Decision / StageRun` 这套产品内核；再把 Chatroom、Pipeline 和其附属消息/产物对象整体降级为兼容层、协作层和观测层。**
