# ADR-015: 现状架构审计与重构边界判断

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-013-business-architecture-solo-app-factory.md`, `docs/ADR-014-project-flow-and-state-machine.md`

---

## 1. 目的

本文档不是重新定义目标架构，而是回答一个更现实的问题：

**Catown 当前实现离目标业务架构到底差多远？哪些东西值得保留，哪些必须重写，哪些应该降级？**

本次审计基于以下原则：

- 以 `best architecture first` 为第一原则
- 不为了兼容旧抽象而保留错误骨架
- 旧实现可以作为底层能力来源，但不能继续主导产品形态

---

## 2. 审计结论摘要

### 2.1 结论一句话

**Catown 当前实现的主轴仍然是 `chatroom/message + pipeline`，而不是 `project/asset/decision/stage_run`。**

因此：

- 它不是“已经有正确业务内核，只差换皮”
- 也不是“局部改一改接口就能转正”
- 它更接近：**底层能力丰富，但产品骨架和业务对象中心错位**

### 2.2 重构判断

基于当前代码结构，推荐采用：

**重建核心、复用底层。**

也就是说：

- 重建新的业务内核和主 API
- 重建主产品页面的信息架构
- 复用旧执行引擎、工具体系、审计体系、事件推送等底层能力
- 将旧 `chatroom` / `pipeline` 从产品主角降级为辅助层或执行层

---

## 3. 现状主轴判断

### 3.1 Project 当前只是一个薄壳

当前 `Project` 模型字段非常少：

- `name`
- `description`
- `status`

其真正价值不是承载项目资产与阶段状态，而是作为两个旧主体系的挂载点：

- `chatroom`
- `agent_assignments`
- `pipeline`

文件证据：

- `backend/models/database.py`

判断：

**Project 当前不是“项目业务内核”，而更像“聊天室和 Pipeline 的共同宿主”。**

### 3.2 Chatroom / Message 仍是交互主轴

当前系统的聊天链路包括：

- `Chatroom`
- `Message`
- `chatroom_manager`
- `/chatrooms/{id}/messages`
- `/chatrooms/{id}/messages/stream`
- `trigger_agent_response()`

其核心流程是：

1. 用户向聊天室发消息
2. 系统从聊天室拿上下文
3. 根据 @mention 或默认规则选 Agent
4. 拼接历史消息与 system prompt
5. 让 Agent 回答并回写聊天室

文件证据：

- `backend/models/database.py`
- `backend/chatrooms/manager.py`
- `backend/routes/api.py`

判断：

**当前系统最自然的主入口不是“推进项目”，而是“往聊天室发消息”。**

这与目标中的 Mission Board / Decision Center / Asset-first 模型明显冲突。

### 3.3 Pipeline 是另一条完整主轴

当前 Pipeline 不是轻量工作流，而是一整套独立子系统，包含：

- `Pipeline`
- `PipelineRun`
- `PipelineStage`
- `StageArtifact`
- `PipelineMessage`
- `pipeline_engine`
- `pipeline_router`

它覆盖的能力包括：

- 生命周期管理
- 阶段流转
- Gate 审批
- Agent 调度
- 工具调用循环
- 错误恢复
- Agent 间消息路由
- 产出物归档

文件证据：

- `backend/models/database.py`
- `backend/pipeline/engine.py`
- `backend/routes/pipeline.py`

判断：

**当前系统其实有两条并行主轴：聊天室主轴 + Pipeline 主轴。**

这会导致产品认知和数据模型认知分裂：

- 用户视角像聊天室系统
- 执行视角像 Pipeline 系统
- 真正的项目业务对象并没有站在中心

---

## 4. 与目标架构的差距

### 4.1 缺少 Project-first 业务骨架

目标架构要求以以下对象为中心：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

而现状中：

- `Project` 太薄
- `Asset` 不存在为主对象
- `Decision` 不存在为独立业务对象
- `StageRun` 被旧 `PipelineRun / PipelineStage` 语义绑定

结果是：

- 项目推进无法以正式资产驱动
- 人工 Gate 无法作为一等对象管理
- 项目状态不是业务状态，而是旧执行状态拼装结果

### 4.2 缺少资产优先模型

目标架构要求正式资产成为主对象，如：

- `Project Brief`
- `PRD`
- `UX Blueprint`
- `Tech Spec`
- `Task Plan`
- `Build Artifact`
- `Test Report`
- `Release Pack`

而现状中：

- 只有 `StageArtifact`，且它附着在 `PipelineStage` 上
- 资产没有统一元数据、版本链、审批状态、依赖链
- 产出物更像 Pipeline 的附属文件记录，而不是项目正式资产

判断：

**现有系统有“产物”，但没有真正的“资产模型”。**

### 4.3 缺少 Decision-first 人机协作模型

目标架构要求：

- `scope_confirmation`
- `direction_confirmation`
- `release_approval`

都应作为独立 `Decision` 对象存在。

而现状中：

- Gate 主要属于 Pipeline 阶段机制
- 它是引擎内部控制点，不是产品业务对象
- 首页和项目页无法自然聚合“待你确认的事情”

判断：

**当前系统有 Gate 机制，但没有 Decision 模型。**

### 4.4 前端仍然是“项目壳 + 聊天内核”

虽然当前前端视觉已被改成了 command deck 风格，但状态和数据流仍然围绕：

- `projects`
- `messages`
- `pipelineCards`
- `logs`

主交互链路仍然是：

- `loadProjects()`
- `selectProject()`
- `loadMessages(chatroom_id)`
- `sendUserMessage()` -> `/chatrooms/{id}/messages/stream`

文件证据：

- `frontend/index.html`

判断：

**当前前端本质上仍然是聊天室，只是被包装成了项目控制台。**

---

## 5. 保留 / 重写 / 降级候选

### 5.1 建议保留并吸收的能力

以下内容建议保留为底层能力：

#### 5.1.1 Agent Runtime 与工具体系

包括：

- Agent 注册与配置
- LLM client 获取逻辑
- 工具白名单与工具执行
- 协作工具注册逻辑

理由：

- 这些能力属于“如何执行”层，而不是“业务对象是什么”层
- 重写价值低，复用价值高

#### 5.1.2 Pipeline 引擎中的执行能力

包括：

- 阶段推进引擎思想
- Agent 调度循环
- 工具调用循环
- workspace 隔离
- 基础错误恢复机制

理由：

- 这些是新 `StageRun` 执行层的良好基础
- 但必须从旧 Pipeline 产品语义中剥离出来

#### 5.1.3 审计与事件推送能力

包括：

- `LLMCall`
- `ToolCall`
- `Event`
- `event_bus`
- WebSocket 广播能力

理由：

- 审计与推送是高价值基础设施
- 只需要重新绑定到新业务对象，而不是继续绑定旧 `run_id/stage_id`

### 5.2 建议重写的内容

以下内容建议按新架构重写：

#### 5.2.1 Project 主模型

现状问题：

- 字段过薄
- 无法承载项目状态机
- 无法承载项目业务目标

应重写为：

- `Project` 作为项目业务内核
- 包含 `current_stage`、`execution_mode`、`health_status` 等字段

#### 5.2.2 主业务 API 边界

现状问题：

- 项目 API 返回 `chatroom_id`
- 主链路仍围绕聊天室消息
- Pipeline API 形成平行主系统

应重写为：

- `projects`
- `assets`
- `decisions`
- `stage-runs`

为中心的一组新 API。

#### 5.2.3 主产品前端

现状问题：

- 主状态变量错位
- 主交互还是发消息
- 产物和决策没有被放在中心

应重写为：

- Dashboard
- Mission Board
- Release Center

围绕状态、资产、决策运转。

#### 5.2.4 项目创建流

现状问题：

- 创建项目本质上是创建聊天室并分配 Agent
- 没有 `Project Brief` 生成和确认

应重写为：

- 创建项目
- 生成 Brief
- 形成 Decision
- 用户确认
- 进入下一阶段

### 5.3 建议降级的内容

#### 5.3.1 Chatroom

建议定位为：

- 协作通道
- 运行日志
- Agent 对话历史

而不再作为产品主入口。

#### 5.3.2 PipelineMessage

建议定位为：

- 执行期内部协作记录
- 审计的一部分

而不是项目正式沟通骨架。

#### 5.3.3 Pipeline 公开主入口

建议定位为：

- 内部编排层
- 底层 StageRun 执行引擎

而不是产品层的一套平行主系统。

---

## 6. 第一阶段重构落点

### 6.1 第一刀不应砍前端皮肤

第一刀应落在：

- 新业务数据模型
- 新主 API
- 新最小闭环

理由：

- 前端如果继续建立在旧聊天模型上，只会继续放大错误抽象

### 6.2 第一阶段必须先立的新对象

建议优先建立：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

这四个对象立起来后，才有资格谈：

- Mission Board
- Release Center
- Autopilot / Checkpoint
- 资产链路
- 待确认事项中心

### 6.3 第一阶段最小闭环

建议最先打通：

1. 创建项目
2. 生成 `Project Brief`
3. 创建 `scope_confirmation`
4. 用户确认
5. 项目进入 `brief_confirmed`
6. 能继续进入下一阶段

理由：

- 这是从“聊天室产品”切向“项目推进系统”的第一条真正主链路

---

## 7. 可执行重构蓝图

### 7.1 分层改造原则

为了避免“表面重构、骨架照旧”，建议把 Catown 明确拆成四层：

#### L1: 产品业务层（必须重建）

负责回答：

- 项目当前处于什么阶段
- 当前最重要的资产是什么
- 哪个决策在等待用户确认
- 下一步系统应该推进什么

这一层应以以下对象为核心：

- `Project`
- `Asset`
- `Decision`
- `StageRun`

判断：

**这是本轮重构的核心战场，不能沿用旧聊天抽象继续演化。**

#### L2: 任务编排层（重构语义，吸收能力）

负责回答：

- 当前阶段要执行哪些任务
- 由哪些 Agent 参与
- 是否需要工具调用
- 如何处理失败、重试与恢复

这一层可以吸收旧 Pipeline 引擎的执行机制，但需要把公开语义从：

- `Pipeline`
- `PipelineRun`
- `PipelineStage`

迁移为：

- `StageRun`
- `StageTask`
- `ExecutionLog`

判断：

**引擎能力可复用，但公开模型和命名要彻底脱离旧 Pipeline 产品心智。**

#### L3: Agent Runtime / Tooling 层（优先保留）

负责回答：

- 哪个 Agent 可以执行什么
- 工具如何注册与调用
- LLM client 如何选择与注入
- 多 Agent 协作如何落地

这一层原则上保留并做边界清理，不应成为本轮重构的主阻塞项。

#### L4: 基础设施层（保留并重新挂接）

包括：

- `LLMCall`
- `ToolCall`
- `Event`
- `event_bus`
- WebSocket 推送
- workspace / 文件系统能力

判断：

**这些是基础设施，不应跟着旧产品语义一起推倒。要做的是重新挂接到新业务对象。**

### 7.2 保留层 / 重写层 / 兼容层

为了便于工程落地，建议直接按下表理解：

| 层级 | 处理策略 | 代表对象/模块 | 说明 |
|---|---|---|---|
| 业务模型层 | 重写 | `Project`, 新 `Asset`, 新 `Decision`, 新 `StageRun` | 这是新内核 |
| 主 API 层 | 重写 | `projects`, `assets`, `decisions`, `stage-runs` | 替换旧 chat-first 入口 |
| 主前端层 | 重写 | Dashboard / Mission Board / Release Center | 状态、资产、决策置中 |
| 执行引擎层 | 吸收改造 | `pipeline_engine` 等 | 保留执行能力，剥离旧语义 |
| Agent/Tool 层 | 保留 | agent registry, tools, llm client | 做边界收口，不优先重写 |
| 审计/事件层 | 保留 | `LLMCall`, `ToolCall`, `Event`, `event_bus` | 改绑定对象即可 |
| 聊天协作层 | 降级兼容 | `Chatroom`, `Message`, `PipelineMessage` | 保留为协作/审计，不再做主入口 |

### 7.3 迁移顺序

建议按“先立新骨架，再接旧能力，最后下线旧入口”的顺序推进。

#### Phase 0: 冻结旧主轴扩张

目标：

- 停止在 `chatroom/message + pipeline` 主轴上继续加产品功能
- 停止继续强化“项目页面 = 聊天页面”的交互模式
- 所有新增需求优先映射到目标内核模型上评估

交付物：

- 本 ADR 确认
- 新核心模型草案
- 新 API 草案

#### Phase 1: 建立新业务内核

目标：

- 建立新的 `Project`
- 建立新的 `Asset`
- 建立新的 `Decision`
- 建立新的 `StageRun`
- 定义明确状态机与依赖关系

最低要求：

- 项目状态不再由聊天室活动隐式表达
- 正式产物不再挂在 `PipelineStage` 附属语义下
- 用户确认事项可以独立查询与处理

#### Phase 2: 打通第一个 project-first 闭环

目标闭环：

1. 创建项目
2. 生成 `Project Brief`
3. 创建 `scope_confirmation`
4. 用户确认
5. 项目进入 `brief_confirmed`
6. 创建下一阶段 `StageRun`

判断标准：

**只要这个闭环还依赖聊天室消息来表达主状态，就说明迁移没有成功。**

#### Phase 3: 吸收旧执行能力

目标：

- 将旧 pipeline engine 中有价值的调度逻辑挂接到新 `StageRun`
- 将旧工具调用循环重新绑定到新执行对象
- 将旧事件广播从 `pipeline/stage/message` 语义迁移到 `project/decision/stage_run/asset`

这一步应避免：

- 为了复用而保留旧公开 API
- 让前端直接消费旧 `PipelineRun` 作为新页面主数据

#### Phase 4: 重建主产品界面

目标：

- Dashboard 展示项目状态、待决策事项、风险与产出
- Mission Board 展示当前阶段、资产链、执行进度
- Release Center 展示测试状态、发布包、发布决策

完成标准：

- 用户的主操作不再是“发一条消息试试看”
- 用户的主操作变成“确认范围 / 查看资产 / 批准发布 / 触发继续推进”

#### Phase 5: 旧入口降级与收尾

目标：

- `Chatroom` 退化为协作视图
- `Pipeline` 退化为内部引擎术语或被完全隐藏
- 对外文档、前端文案、API 命名统一切换到新业务模型

### 7.4 迁移边界与兼容策略

为了避免重构期间系统失血，建议采用以下边界控制：

#### 7.4.1 不做“双主模型长期共存”

可以有短期兼容层，但不能长期允许：

- 一套前端页面说 `Project/Asset/Decision`
- 一套真实后端还在以 `Chatroom/Pipeline` 为主对象

原因：

- 会让团队在讨论中持续混用术语
- 会让数据一致性越来越难保证
- 会让任何一次产品改动都要穿透两套心智模型

#### 7.4.2 允许短期适配器，但必须是单向吸收

允许出现：

- 旧 engine -> 新 `StageRun` adapter
- 旧 `StageArtifact` -> 新 `Asset` adapter
- 旧 event payload -> 新事件模型 adapter

但不建议出现：

- 新前端直接读旧 chatroom 数据当主状态
- 新业务对象反向退化成旧 API 语义

#### 7.4.3 数据迁移优先保证“新写入走新模型”

对于已存在的旧项目，可以接受：

- 老数据继续只读存在
- 新项目全部走新模型
- 迁移脚本逐步补做，而不是一开始就强求全量转换

原则：

**先保证新世界是干净的，再考虑旧世界如何搬运。**

### 7.5 主要风险点

#### 7.5.1 最大风险：换名不换骨

表现为：

- 把 `PipelineRun` 改名叫 `StageRun`
- 把 `StageArtifact` 改名叫 `Asset`
- 但数据关系和控制流完全不变

结果是：

- 文档看起来先进了
- 实际产品仍然被旧语义绑死

#### 7.5.2 第二风险：前端先跑太快

如果前端先做一套新皮肤，而后端主状态仍基于聊天室和 pipeline，最后只会得到：

- 更漂亮的界面
- 更复杂的数据拼装
- 更难维护的产品错觉

#### 7.5.3 第三风险：底层复用过度反噬

如果为了“尽量复用”，把旧 Pipeline 的边界原封不动保留下来，会导致：

- 新业务模型被迫向旧引擎妥协
- 阶段语义、决策语义、资产语义都被执行语义污染
- 最终又回到兼容式修补

### 7.6 建议的开工判据

在真正开始大规模编码前，建议先确认以下四项已经明确：

1. 新 `Project` 的状态机是否明确
2. 新 `Asset` 的类型、版本链、依赖链是否明确
3. 新 `Decision` 的生命周期和处理动作是否明确
4. 新 `StageRun` 与执行引擎的边界是否明确

如果这四项没有明确，就不应该进入全面实现阶段。

---

## 8. 最终判断

### 7.1 不是全盘推倒

不建议把所有底层基础设施全部重写。

因为当前系统已经有不少值得保留的工程积累：

- Agent 执行能力
- 工具体系
- 审计采集
- WebSocket 推送
- workspace 能力

### 7.2 也不是兼容式修补

不建议在现有 `chatroom/message + pipeline` 主结构上继续堆新业务抽象。

因为那样会造成：

- 双重模型并存
- 概念混乱
- 长期维护成本持续上升
- 前端永远像“聊天室伪装成项目系统”

### 7.3 推荐路线

**最优路线是：重建核心业务骨架，吸收底层执行能力。**

即：

- 重建业务对象和主 API
- 重建主产品层信息架构
- 复用旧执行、审计、工具、事件能力
- 逐步把旧 Chatroom / Pipeline 从产品主角降级

---

## 9. 一句话总结

**Catown 当前不是缺少功能，而是业务中心错位：它拥有大量可复用的底层能力，但产品主轴仍停留在聊天室与 Pipeline 的并排结构上，因此下一阶段应按“先重建 `Project / Asset / Decision / StageRun` 新内核，再吸收旧运行时能力，最后降级旧入口”的顺序推进。**
