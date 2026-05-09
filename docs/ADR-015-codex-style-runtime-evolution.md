# ADR-015: Codex 风格运行时内核演进

**状态**: 进行中
**日期**: 2026-04-24
**决策人**: BOSS
**相关**: ADR-012 (会话上下文管理), PRD §21 (上下文压缩), PRD §1645 (`/api/pipelines/{id}/messages`)

---

## 1. 背景

Catown 当前有两套明显不同的执行形态：

- **工作流层**：`pipeline/engine.py` 提供固定 stage、gate、artifact、rollback、workspace、git 节点。
- **任务运行时层**：`routes/api.py` 负责聊天室 turn loop、工具调用、SSE 流式输出、多 Agent 接力。

在项目式交付上，Catown 的 workflow/gate 能力并不弱；问题主要出在 **runtime kernel 不统一**：

- 聊天室和 Pipeline 过去都存在 append-only `messages` 增长问题。
- Pipeline 的 Agent 消息传递更像“留言箱”，而不是调度语义。
- 运行时状态缺少统一的 task/run ledger。

相较之下，Codex 风格系统的核心不是静态 pipeline，而是 **动态任务运行时**：

- 以 `task/run` 为主，而不是以固定 stage 为主
- 以 `turn` 为基本调度单位
- 每轮重组上下文，而不是无限追加 transcript
- 工具调用、消息、审批、恢复都作为运行时状态的一部分
- 是否并行、是否委派、是否等待，是调度器的决定，不是模板硬编码

---

## 2. 对标 Codex 时值得吸收的设计

### 2.1 Turn-first runtime

Codex 更像：

```
Task/Run
  -> Turn Loop
    -> LLM
    -> Tools / Environment
    -> State update
    -> Re-assemble next turn
```

而不是：

```
Pipeline template
  -> fixed stage A
  -> fixed stage B
  -> fixed stage C
```

### 2.2 每轮重组上下文

Codex 风格运行时不会把完整历史无上限 append 到 prompt。较好的模式是：

- 最近 1 轮工具协议保留原始 assistant/tool transcript
- 更早轮次折叠成摘要
- BOSS 指令、工具策略、共享事实、前序 handoff 分层注入

### 2.3 控制面 / 数据面分离

需要把不同类型的上下文拆开：

- `system`: 稳定身份层
- `developer`: 操作规则、工具政策、阶段约束、BOSS 指令
- `user`: 任务事实、共享消息、handoff、项目状态
- `assistant/tool protocol`: 最近一轮结构化协议

### 2.4 Durable inbox/outbox

Agent 间消息如果只存在进程内内存队列，无法支撑：

- backend 重启恢复
- run resume
- 崩溃后的 message replay
- 对消息是否已消费的审计

Codex 风格 runtime 需要 durable message state，而不是仅有 transcript。

### 2.5 Scheduler 而非“只有 stage 当前执行者”

真正的多 Agent runtime 不是“允许 A 给 B 发消息”就够了，还需要定义：

- message 是留言还是阻塞请求
- 是否收到消息就调度目标 agent 跑一轮
- 哪些任务是 blocker，哪些可以 sidecar 并行
- 谁拥有当前 write scope / workspace slice

---

## 3. Catown 当前差距

### 3.1 已有优势

Catown 在 workflow/gate 层已经具备：

- manual gate / approve / reject / rollback
- artifact 记录
- workspace 隔离
- Git commit / tag

这些是项目治理层面的优势，不应丢掉。

### 3.2 主要短板

#### A. Stage-first，run-time kernel 不统一

Pipeline 主要仍由“当前 stage 的 agent”驱动，而不是统一调度器决定下一步。

#### B. Agent 消息传递语义偏弱

早期实现中，pipeline inter-agent message 主要依赖进程内 `_interagent_message_queue`，更像临时留言，而不是 durable inbox。

#### C. 上下文直到最近才开始 turn-state 化

聊天室同步、SSE、以及 Pipeline stage loop 过去都倾向于直接 append `messages`。

#### D. 缺少更高层的 RunLedger / TaskState

当前有：

- `TurnContextState`：适合一轮或几轮 prompt 重组

但仍缺：

- run 级 ledger
- scheduler state
- message delivery state
- durable handoff / work ownership

---

## 4. 决策

Catown **不改成纯 Codex 形态**，而是采用双层架构：

### 4.1 底层：Codex 风格 Runtime Kernel

目标能力：

- `TurnContextState`
- durable inbox/outbox
- tool ledger
- BOSS / inter-agent state 注入
- scheduler hooks
- 可恢复 run state

### 4.2 上层：保留 Catown Workflow Layer

保留并继续利用：

- pipeline template
- stage / gate / artifact / rollback
- project governance

但这层不再默认主导所有运行时路径：

- 聊天室内的多 Agent 协作优先走 **orchestration-first runtime**
- 固定 pipeline 只在确实需要 stage/gate 治理时启用
- 如果后续验证表明 pipeline 对默认交互价值不高，可以继续降级为可选治理模块

换言之：

> 底层学 Codex，做动态任务运行时；上层保留 Catown，做项目治理和交付流程。

---

## 5. 演进阶段

### Phase 1: Turn-state prompt rebuild

目标：所有主要 LLM/tool loop 都改成“状态驱动重组消息”，不再 append-only。

当前状态：

- `routes/api.py` 聊天室同步单 Agent：已完成
- `routes/api.py` 单 Agent SSE：已完成
- `routes/api.py` standalone/project 多 Agent SSE：已完成
- `pipeline/engine.py` `_run_agent_stage()`：已完成

### Phase 2: Durable pipeline inbox/outbox

目标：移除 pipeline 进程内 message queue，改用 durable inbox。

当前状态：

- 新增 `pipeline_message_deliveries` 作为 direct message inbox
- `send_message` / BOSS `instruct` / rollback handoff 已写入 durable delivery
- `_pop_messages_for_agent()` 已改为从数据库 claim + consume

未完成：

- 仍是 `pending -> consumed` 的轻量消费语义
- 尚无 lease / retry / dead-letter 语义

### Phase 3: Scheduler semantics

目标：定义消息如何驱动调度，而不只是“留一条消息”。

待实现：

- direct message 是否触发目标 agent 子轮次
- blocker / sidecar 任务建模
- ownership / write scope 约束

### Phase 4: RunLedger / TaskState

目标：引入统一 run 级 ledger，连接：

- tool work
- inter-agent handoff
- decisions / assumptions
- stage handover
- resume/recovery

当前状态：

- 已新增 `task_runs` / `task_run_events` 两张持久化表
- 聊天室同步单 Agent、SSE 单 Agent、SSE 多 Agent 编排开始写入 run 级事件
- 事件类型已覆盖 `user_message_saved`、`runtime_mode_selected`、`agent_turn_started`、`tool_round_recorded`、`handoff_created`、`agent_turn_completed`、`task_run_failed`
- 已暴露查询接口：`GET /api/chatrooms/{chatroom_id}/task-runs` 与 `GET /api/task-runs/{task_run_id}`

未完成：

- pipeline 路径尚未统一接入同一套 run ledger
- scheduler blocker/sidecar/ownership 语义仍未进入 ledger
- resume/recovery 仍主要依赖现有消息与运行时状态，尚未形成完整 checkpoint 模型

---

## 6. 本次实现落点

本次演进已完成两类关键改动：

### 6.1 Pipeline 也接入 TurnContextState

`backend/pipeline/engine.py` 的 `_run_agent_stage()` 现在：

- 用 `TurnContextState` 收集 BOSS 指令
- 用 `TurnContextState` 收集 inter-agent message
- 记录 tool round
- 每轮重建 prompt，而不是直接 append raw fragments

这让 Pipeline 路径与聊天室运行时开始共享同一种 prompt 组装模型。

### 6.2 引入 durable pipeline inbox

新增表：

- `pipeline_message_deliveries`

作用：

- direct pipeline message 的 durable recipient inbox
- 支持 `pending` / `consumed` 状态
- 支持 run 恢复后重新读取数据库中的待处理消息

这一步的重点不是“做完完整 scheduler”，而是先把消息状态从内存队列迁到 durable storage。

### 6.3 聊天室多 Agent 路径改成 orchestration-first

`routes/api.py` 中原来的 “multi-agent pipeline” 已开始改写为 Codex 风格编排：

- 以 `@mention` 形成 turn agenda，而不是进入固定 stage
- 每个 agent turn 都重建 prompt
- 前序 agent 输出通过 handoff / inbox 注入到下一位 agent，而不是直接把上一个回答拼进用户原始消息
- 工具调用开始透传运行时上下文（`agent_id` / `agent_name` / `chatroom_id` / `project_id`）

### 6.4 RunLedger 首轮落地

`backend/services/run_ledger.py` 与 `backend/models/database.py` 新增了 run 级持久化骨架：

- `TaskRun`：一条聊天室执行 run 的摘要记录
- `TaskRunEvent`：该 run 下按顺序追加的事件明细

当前聊天室路径的落地方式：

- `POST /api/chatrooms/{id}/messages`：用户消息保存后创建 `TaskRun`
- `POST /api/chatrooms/{id}/messages/stream`：SSE 路径也创建独立 `TaskRun`
- 单 Agent / 多 Agent 编排均在执行过程中写入事件
- run 完成或失败时写回 `status` / `summary` / `completed_at`

这一步的价值不是取代 pipeline，而是先把聊天 runtime 从“只有 transcript 和 runtime_card”推进到“run + ordered events”的可审计形态。

这意味着 Catown 的默认多 Agent 协作路径，已经不再依赖 pipeline 才能工作。

---

## 7. 后续明确不在本阶段完成的内容

以下内容仍然保留到后续阶段：

- `reply_to_message_id` / thread/correlation 语义
- inflight lease / ack timeout / retry / dead-letter
- message-driven subturn scheduler
- run 级 TaskState / RunLedger
- stage 内并行 sidecar worker

---

## 8. 结论

Catown 的正确方向不是“取消 pipeline 改成纯对话式 runtime”，而是：

- 用 Codex 风格 runtime kernel 强化底层执行与恢复能力
- 继续保留 Catown 的 pipeline/gate/artifact 治理层

这是“项目治理”与“任务运行时”两层分离的架构路线。

---

## 9. 2026-04-25 回看：当前与 Codex 的差距

截至 2026-04-25，Catown 相比本文前述阶段已经继续向 Codex 风格运行时推进，尤其是在聊天室 orchestration 路径上已有明显收敛，但整体仍是：

> 已有 Codex 风格 orchestration 外形，尚未形成 Codex 风格统一运行内核。

### 9.1 已经比较接近 Codex 的部分

- **orchestration-first 聊天室运行时已成形**
  - 多 Agent 聊天默认走 `task/run -> scheduler -> turn -> handoff` 路径，而不是固定 stage pipeline。
  - 已具备 blocker / sidecar 调度建模、handoff 事件、运行态可视化。

- **run ledger + Monitor 基础链路已落地**
  - `task_runs` / `task_run_events` 已能记录 mode selection、tool round、handoff、scheduler dispatch/completion、failure、resume/recovery。
  - Monitor 已可查看 run ledger、scheduler plan、handoff、runtime state、recovery lease。

- **主 turn loop 已开始共享执行器**
  - 非流式路径已通过 `backend/services/nonstream_turn_executor.py` 收敛，覆盖 sync 单 Agent、sync orchestration、pipeline stage agent loop。
  - 流式路径已通过 `backend/services/stream_turn_executor.py` 收敛，覆盖 standalone SSE、project 单 Agent SSE、multi-agent orchestration SSE。

- **resume / recovery 能力已有实用可靠性**
  - 支持启动时扫描中断 orchestration run 自动恢复。
  - 支持手动 `resume`。
  - 已补上 recovery lease，避免多实例对同一个 run 重复恢复。

- **prompt rebuild / turn-state 方向基本正确**
  - 聊天路径和 pipeline stage loop 都已经转向每轮重组上下文，不再纯 append transcript。
  - `TurnContextState`、history summary、tool round summary、task-state fragments 已经构成第一层 runtime context kernel。

### 9.2 仍明显落后于 Codex 的部分

#### A. 仍缺少统一 runner 外壳

当前仍是多条执行路径并存：

- 单 Agent sync
- 单 Agent SSE
- multi-agent orchestration sync
- multi-agent orchestration stream
- pipeline stage engine

这些路径虽然已经开始共享 `nonstream_turn_executor` / `stream_turn_executor`、上下文重组和 run ledger 语义，但还没有收敛成一套完整的 runner / execution envelope。  
这意味着：

- 行为语义仍可能分叉
- 恢复逻辑仍需按路径分别处理
- approval / tool / subagent / resume 难以统一接入

这是当前与 Codex 的**最大结构性差距**。

#### B. 还没有真正的 subagent lifecycle

Codex 风格更强调：

- spawn
- wait
- close
- tree / ownership / parent-child relation

Catown 当前更接近“协作工具”层：

- `delegate_task`
- `send_direct_message`
- `query_agent`

这些能力可用，但还不是统一的 runtime-managed subagent lifecycle。  
Monitor 里的 `Sub-Agent Tree` 目前也更像项目 agent 目录，而不是实际运行时子树。

#### C. 消息传递和调度语义仍偏轻

虽然 pipeline durable inbox 已落地，但目前仍主要是：

- `pending -> consumed`

尚缺：

- inflight lease
- ack timeout
- retry
- dead-letter
- ownership / write-scope enforcement

换言之，Catown 已有 durable message storage，但还没有形成 Codex 风格的**消息驱动执行语义**。

#### D. recovery 仍是“重建型”，不是“checkpoint 型”

当前的恢复逻辑已经可用，但本质仍然主要依赖：

- 已保存消息
- task_run ledger 事件
- scheduler state replay

来重建剩余执行状态。  
这与更理想的 Codex 风格 checkpoint continuation 仍有差距：

- 缺少完整 executor snapshot
- 缺少标准化 task state checkpoint
- 缺少更细粒度的恢复断点模型

#### E. TaskState 仍偏薄

当前已有 `TaskState`，但内容主要仍集中在：

- current request
- goal
- blockers
- working summary
- validation checklist

距离更成熟的 Codex 风格 task/run state 还缺：

- assumptions / open questions
- todo / done criteria
- ownership / write scope
- delegated work state
- step-level progress / pending actions

#### F. approval / sandbox 还未进入统一 runtime 主链

Catown 现有：

- pipeline gate approval
- 若干工具级 sandbox / security 限制

但还没有形成 Codex 风格那种统一的：

- action approval
- escalation
- sandbox policy projection
- pending approval queue
- execution policy as runtime state

目前 Monitor 里这部分也仍以占位 UI 为主。

#### G. context compaction 还是早期形态

当前已经具备：

- history summary
- tool round summary
- task-state fragments

但还没有形成真正的：

- compaction event
- checkpoint-friendly context snapshot
- context budget pressure telemetry
- compaction-aware resume model

Monitor 里这块仍是“尚未发出 compaction 事件”的状态。

#### H. 缺少 workspace-native shell kernel

Codex 的一个关键优势是“面向工作区执行”的原生能力，而不是纯 RPC 工具箱。

Catown 当前仍以工具为主：

- `read_file`
- `write_file`
- `list_files`
- `execute_code`

这能做很多事，但与 Codex 风格的：

- 持续 shell session
- PTY / stdin / session state
- patch-aware editing loop
- command-level approval / escalation

相比还有明显差距。

### 9.3 当前阶段的判断

截至目前，Catown 更准确的定位应是：

> `Codex-style orchestration runtime + Catown workflow/governance layer`

而不是：

> `Codex-style unified agent kernel`

这说明路线没有走偏，但“底层内核收敛”还没有完成。

### 9.4 建议的后续优先级

如果目标是继续向 Codex 风格演进，优先级建议如下：

1. **先收敛统一 runner**
   - 把 sync / SSE / orchestration / pipeline stage 的核心执行语义进一步收敛。

2. **再补 subagent lifecycle**
   - 从协作工具升级为 runtime-managed spawn / wait / close / tree。

3. **然后补 approval / sandbox 主链**
   - 让危险动作、权限升级、执行策略进入统一 runtime state。

4. **最后推进 checkpoint 型恢复**
   - 把当前 replay/rebuild 恢复，逐步演进为更标准化的 task/run checkpoint continuation。

---

## 10. Pipeline 是否保留：与 Codex 编排的差别、优劣势、目标定位

### 10.1 判断结论

当前判断是：

> Pipeline 有必要存在，但不应继续作为默认执行主干。

更准确地说：

- **Codex 风格编排** 应负责默认任务运行时
- **pipeline** 应负责治理、审批、交付约束

因此，pipeline 的长期定位不应是“另一套和 runtime 平级的执行内核”，而应逐步演进为：

> 基于统一 runner 的 governance layer / blueprint layer

### 10.2 什么场景下 pipeline 仍有必要

pipeline 在以下场景仍明显有价值：

- **阶段化交付**
  - 如 PRD -> 架构 -> 开发 -> 测试 -> 发布 的固定链路
- **人工审批 / gate**
  - 需要明确的 `approve / reject / rollback`
- **产物导向**
  - 需要文档、测试报告、CHANGELOG、release tag 等明确 artifact
- **项目治理**
  - 需要阶段状态、负责人、回滚目标、对外可解释流程
- **发布控制**
  - release 阶段与 Git tag / 版本动作天然适合 workflow 语义

换言之，pipeline 更适合：

- 治理型项目
- 合规型流程
- milestone 明确的交付链
- 需要向管理者或协作团队清晰展示阶段状态的场景

### 10.3 什么场景下 pipeline 不该做默认主干

对于以下典型 Codex 场景，pipeline 不适合作默认路径：

- 交互式调研
- bug 修复
- 代码 review
- 探索式实现
- 短循环调试
- 多 agent 临时接力协作

这些任务的特点是：

- 回路短
- 路径不稳定
- 经常中途改变计划
- 强依赖工具调用和即时反馈

这类任务更适合：

- `task/run`
- `turn`
- `scheduler`
- `handoff`
- `resume/recovery`

而不是先进入固定 stage 再执行。

### 10.4 pipeline 与 Codex 编排的本质差别

#### A. pipeline：stage-first

pipeline 更接近：

```text
Pipeline Template
  -> Stage A
  -> Stage B
  -> Stage C
  -> Gate / Rollback / Release
```

特征是：

- 固定模板驱动
- 阶段先于任务细节
- 人工审批清晰
- artifact / rollback 语义强
- 适合项目治理

#### B. Codex 风格编排：task/run/turn-first

Codex 风格更接近：

```text
Task / Run
  -> Scheduler
  -> Turn
  -> Tools / Messages / Handoff
  -> State Update
  -> Next Turn
```

特征是：

- 任务先于阶段
- 调度器决定谁先做、谁 sidecar、谁接力
- 每轮基于状态重建上下文
- 更像真实 coding loop
- 适合交互式开发

### 10.5 pipeline 的优势

- **治理能力强**
  - 固定阶段、审批、打回、回滚、release 动作都天然契合
- **项目可解释性强**
  - 更容易回答“当前卡在哪一阶段”
- **产物意识强**
  - 对文档、报告、版本产物管理更自然
- **流程稳定**
  - 适合交付链清晰、参与方较多的项目
- **对非技术管理更友好**
  - 比动态 runtime 更容易被组织理解与采纳

### 10.6 pipeline 的劣势

- **刚性强**
  - 不适合真实开发中的回环、跳转、临时插入 side task
- **不适合默认聊天协作**
  - 会把短任务、探索式任务、调试任务过度流程化
- **重复实现运行时能力**
  - 容易形成第二套 executor / messaging / recovery 逻辑
- **消息与调度语义偏轻**
  - 目前 durable inbox 还主要是 `pending -> consumed`
- **还未完全并入统一 run ledger / task state**
  - 导致恢复、观测、调度语义仍分叉

### 10.7 Codex 风格编排的优势

- **更贴近实际 coding 过程**
  - 调研、修改、测试、再调研的短循环非常自然
- **调度灵活**
  - 哪个 agent 先上、谁 sidecar、何时 handoff 都可动态决定
- **默认多 agent 协作体验更好**
  - 不必先把所有行为塞进 pipeline template
- **更适合工具循环**
  - tool call / result / state update / next turn 语义更统一
- **更适合 run-level 恢复**
  - 与 scheduler state、handoff、task run ledger 更容易统一

### 10.8 Codex 风格编排的劣势

- **治理弱**
  - 如果没有额外约束，缺少明确 stage / gate / artifact 结构
- **对管理视角不够友好**
  - 动态运行时强，但“阶段看板”表达力偏弱
- **更依赖底层内核成熟度**
  - 需要统一 runner、subagent lifecycle、approval/sandbox、checkpoint state 才能真正稳定
- **如果没有治理层，容易漂移**
  - 可能一直局部推进，但难以形成可审计的交付链

### 10.9 当前 Catown 的最合理定位

结合现状，更合理的结构不是：

- 删掉 pipeline，全部改成纯动态 runtime

也不是：

- 保持 pipeline 与 runtime 两套平行内核长期共存

而是：

> 用 Codex 风格编排做默认 runtime，用 pipeline 做可选治理壳。

这意味着：

- 默认聊天室、多 agent 协作、短任务执行走 orchestration-first
- pipeline 在确有治理需求时启用
- pipeline 长期要“降级为治理层”，而不是维持完整独立 engine

### 10.10 代码层面的保留 / 并入 / 弱化建议

#### A. 应保留的 pipeline 能力

- pipeline template / config
- stage / gate 定义
- rollback 规则
- release / tagging / artifact 语义
- 面向治理的 API 与 UI

这些能力是 Catown 相比纯 Codex 风格运行时的差异化优势。

#### B. 应逐步并入统一 runner 的能力

- stage 执行时的 agent turn loop
- tool 调用主循环
- inter-agent 消息消费语义
- prompt rebuild / turn-state 组装
- resume / recovery 主链
- run ledger / task state 写入

也就是说，真正执行 agent work 的“发动机”不应长期分裂在：

- `backend/routes/api.py`
- `backend/pipeline/engine.py`

两套实现里。

#### C. 应逐步弱化的 pipeline 特征

- pipeline 作为默认多 agent 聊天协作主路径
- pipeline 自己维护一整套独立 async execution kernel
- pipeline 特有但无法复用到统一 runtime 的消息语义

### 10.11 推荐的目标架构

长期更推荐的目标是：

```text
Unified Run Controller
  -> Orchestration Runtime Kernel
     -> turn loop
     -> tool loop
     -> handoff / inbox
     -> scheduler
     -> run ledger
     -> recovery / checkpoint

Pipeline Governance Layer
  -> pipeline template
  -> stage / gate policy
  -> artifact expectations
  -> rollback / release rules
  -> governance UI / API
```

也就是说：

- **运行时只有一套**
- **治理策略可以有多种**

pipeline 在这个架构中更像：

- blueprint compiler
- governance wrapper
- delivery policy layer

而不是另一台执行引擎。

### 10.12 演进建议

#### Phase A：短期

- 保留现有 pipeline
- orchestration 继续作为默认聊天室运行时
- 尽快把 pipeline run 也接入统一 run ledger / monitor 语义

#### Phase B：中期

- 让 pipeline template 能编译成统一 runner 可理解的约束
- gate / artifact / rollback 成为 run policy，而不是独立 engine 私有逻辑

#### Phase C：长期

- 把 `pipeline/engine.py` 收缩成治理适配层
- 让真正执行逻辑完全收敛到统一 runner
- 如果后续真实使用表明某些 pipeline 能力价值低，可再继续裁剪

### 10.13 最终判断

因此，关于“pipeline 有必要存在么”的最终结论是：

- **有必要存在**
- **没必要继续做默认执行主干**
- **最有价值的未来角色是治理层，而不是第二套运行时内核**

---

## 11. 拆解后的开发计划（按 Codex 风格逐步实施）

### 11.1 总体原则

实施上不做“大爆炸式重写”，而采用：

- **先打通观测与状态骨架**
  - 先让 pipeline 与 orchestration 写入同一套 run ledger，避免继续分叉。
- **再收敛执行内核**
  - 先共享 runner 语义，再谈删减 `pipeline/engine.py`。
- **最后把 pipeline 压缩成治理层**
  - 保留 gate / artifact / rollback / release 的项目治理价值。

换言之，顺序应是：

```text
Unified visibility
  -> Unified execution semantics
  -> Unified runner
  -> Pipeline as governance layer
```

### 11.2 Phase 1：pipeline 先接入统一 run ledger

目标：让 pipeline run 不再是 Monitor 与恢复体系外的一等公民缺口。

本阶段具体工作：

1. **建立 `PipelineRun -> TaskRun` 链接**
   - 在 `pipeline_runs` 上增加 `task_run_id`
   - 启动 pipeline 时创建对应 `TaskRun`
   - 后续 API / Monitor 都能回到同一条 run 轨迹

2. **把 pipeline 生命周期事件写入 `task_run_events`**
   - 覆盖 `start / pause / resume / approve / reject / instruct`
   - 覆盖 `stage_started / stage_completed / stage_failed / gate_blocked`
   - 覆盖 `pipeline_completed / pipeline_failed`

3. **先统一观测，不急于统一执行**
   - 这一阶段的目标不是删除 pipeline engine
   - 而是先消除“聊天 run 可观测、pipeline run 不可观测”的断层

当前落地状态：

- 已为 `PipelineRun` 增加 `task_run_id`
- 已在 `start_pipeline()` 中自动创建并链接 `TaskRun`
- 已在 pipeline 主要生命周期节点追加 `task_run_events`
- 已在 `PipelineRunOut` 中暴露 `task_run_id`
- 已补测试，验证：
  - `start_pipeline()` 会创建 run-ledger bridge
  - `instruct()` 会向关联 `TaskRun` 追加事件

这一步完成后，pipeline 虽仍是独立执行路径，但已经开始进入统一 runtime ledger。

### 11.3 Phase 2：抽出共享 runner 语义

目标：减少 `routes/api.py` 与 `pipeline/engine.py` 两套执行内核的重复。

建议拆分的共享能力：

- `turn execution`
  - LLM 调用
  - tool loop
  - tool round 记录
  - turn-state rebuild
- `message / handoff consumption`
  - durable inbox 读取
  - handoff 注入
  - blocker / sidecar 基础语义
- `run state mutation`
  - target agent 更新
  - summary 写回
  - run ledger 事件落盘

阶段性目标不是立刻把 pipeline 全量迁移，而是让：

- orchestration turn
- pipeline stage turn

逐步调用同一批 runner helper。

### 11.4 Phase 3：把 pipeline 从执行引擎改造成治理编排器

目标：让 pipeline 负责“约束与治理”，而不是继续维护自己的完整 runtime。

这一阶段的方向应是：

- pipeline template 编译成 run policy / stage policy
- gate 变成统一 runner 可识别的 approval node
- artifact expectation 变成可审计的 delivery contract
- rollback 变成治理动作，而不是 pipeline 私有恢复机制

理想状态下，pipeline 负责回答：

- 这次交付有哪几个治理节点
- 哪些节点需要人工批准
- 需要哪些产物
- 失败时回到哪里

而统一 runner 负责回答：

- 当前轮该谁执行
- 当前轮上下文如何组装
- 工具怎么跑
- 消息怎么消费
- run 如何恢复

### 11.5 Phase 4：补齐 Codex 风格缺口

在 pipeline 已退居治理层后，再补当前与 Codex 的核心差距：

1. **subagent lifecycle**
   - `spawn / wait / close`
   - parent-child run tree
   - delegated work state

2. **approval / sandbox 主链**
   - action approval
   - escalation reason
   - pending approval queue
   - policy 进入 runtime state

3. **checkpoint 型恢复**
   - 比当前 replay/rebuild 更强的 executor snapshot
   - 更标准的 task state checkpoint
   - 更细粒度的 resume 断点

### 11.6 推荐实施顺序

按当前代码基座，建议严格按下面顺序推进：

1. **先完成 Phase 1**
   - 让 pipeline run 在 ledger / Monitor / API 层完全可追踪
2. **再做 Phase 2**
   - 抽共享 runner helper，减少双引擎分叉
3. **然后做 Phase 3**
   - 把 pipeline 收缩为治理层
4. **最后做 Phase 4**
   - 补 subagent / approval / checkpoint

### 11.7 当前实施切片

当前已开始执行的切片是：

> `pipeline -> task_run ledger bridge`

它是最小风险、最高杠杆的第一步，原因是：

- 不破坏现有 pipeline 行为
- 能立刻提升可观测性
- 为后续统一 recovery / monitor / scheduler state 打基础
- 为将来把 pipeline 从“执行引擎”收缩为“治理层”提供迁移锚点

### 11.8 2026-04-25 新进展：pipeline policy 已开始显式编译

本轮又向前推进了一小步：

- 新增 `backend/services/runner_policy.py`
  - 把 pipeline template 中的治理约束显式编译为统一 runner 可理解的 policy 结构
  - 当前已覆盖：
    - `approval`
    - `delivery contract`
    - `rollback`
    - `timeout`
    - `stage ordering`

- `pipeline_run_started` 事件现在会带上 `runner_policy`
  - 这意味着 pipeline 不只是“开始跑了”，还会把本次 run 的治理约束一起投影到 run ledger。

- `pipeline_stage_started / completed / gate_blocked`
  - 现在都会带上 `stage_policy`
  - manual gate、expected artifacts、rollback target 不再只是 engine 内部 if/else，而是开始以显式 policy 形式暴露

- pipeline engine 已开始用编译后的 policy 驱动部分行为
  - 如 `timeout`
  - `expected_artifacts`
  - `manual gate`
  - `rollback_on_blocker`

这还不是“统一 runner 已经完成”，但意义在于：

> pipeline 的治理语义已经不再完全藏在 `pipeline/engine.py` 私有分支里，而是开始投影成统一 runtime 可以识别的 policy object。

下一步更自然的方向就是：

- 让 orchestration / chat runtime 也逐步消费同一类 policy object
- 再进一步把 approval / sandbox / escalation 纳入同一条 runtime policy 主链

### 11.9 2026-04-25 新进展：chat / orchestration runtime 也开始消费同类 policy

在上一小步完成后，聊天室运行时也开始接入同一条 `runner_policy` 表达：

- 单 Agent chat / stream
  - `runtime_mode_selected` 已开始写入 `runner_policy`
  - `agent_turn_started` 已开始带上单步 `stage_policy`

- multi-agent orchestration / stream
  - `orchestration_started`
  - `scheduler_plan_created`
  - `scheduler_step_dispatched / completed / resumed`
  - 这些事件现在都会带上 `runner_policy` 或对应的 `stage_policy`

- recovery 路径
  - orchestration recovery 也开始带上同类 policy snapshot
  - 这样恢复链路不再只重建 scheduler state，也开始重建治理/调度语义视图

这一步的重点仍不是让 chat runtime 立刻受复杂治理约束驱动，而是先把：

- pipeline governance policy
- chat runtime policy
- orchestration schedule policy

收敛到同一类可序列化对象上。

这样后续把 approval / sandbox / escalation 接进统一 runtime state 时，就不必再分别改：

- `pipeline/engine.py`
- `routes/api.py`
- recovery path

而是可以围绕一条共享 policy 主链继续演进。

### 11.10 2026-04-25 新进展：tool-level approval / sandbox / escalation 已投影进 runner policy

本轮继续把之前还停留在“隐式约束”的工具语义显式化：

- `backend/tools/base.py`
  - 新增统一 tool policy snapshot / pack 构建能力
  - 每个工具现在都可以被投影成统一结构，至少包含：
    - `approval`
    - `sandbox`
    - `escalation`
    - `risk_level`
    - `side_effect_scope`

- chat / orchestration runtime
  - `runtime_mode_selected`
  - `orchestration_started`
  - `scheduler_plan_created`
  - 对应 `runner_policy.metadata` 现在会带上：
    - `tool_names`
    - `tool_policies`
    - `tool_policy_summary`

- stage 级 policy
  - 单 Agent turn / orchestration step / pipeline stage 的 `stage_policy.metadata`
  - 现在会带上：
    - `tool_names`
    - `tool_policy_summary`
  - 这样 UI / recovery / audit 不必反查工具注册表，也能知道该阶段允许哪些工具、是否涉及 network、是否可能触发 escalation。

- pipeline
  - `pipeline_run_started.runner_policy.metadata.stage_tool_packs`
  - 现在会按 stage 保存各自的 tool policy pack
  - 这意味着 pipeline 不只是表达 gate / rollback / timeout，也开始表达“每个 stage 允许什么工具、这些工具的治理面是什么”。

这一轮仍然是 **projection first**，不是 **enforcement first**：

- 还没有真正做成统一 pending approval queue
- 还没有把 escalation 做成可恢复的 action 节点
- 还没有把 sandbox 拒绝 / 升级执行接成完整状态机

但现在最关键的一步已经完成：

> approval / sandbox / escalation 不再只是散落在 tool 实现里的注释和 if/else，而是已经进入统一 `runner_policy` 主链，能被 runtime ledger、monitor、recovery 共同消费。

接下来再往 Codex 风格靠拢时，更自然的增量路径就是：

1. 把 tool call 失败区分为普通失败 vs `approval_blocked` / `sandbox_blocked`
2. 引入统一 pending approval / escalation queue
3. 把 recovery 扩展到可恢复被阻塞的 action，而不只是恢复 orchestration 调度

### 11.11 2026-04-25 新进展：tool blocked 已显式分成 approval_blocked / sandbox_blocked

在 11.10 把 tool policy 投影进 `runner_policy` 之后，这一轮继续往前走了一步：

- 共享 tool result classification 已落在统一服务层
  - 现在 tool 执行结果不再只有粗糙的 `success=true/false`
  - 会进一步归类为：
    - `succeeded`
    - `failed`
    - `approval_blocked`
    - `sandbox_blocked`

- chat / stream runtime
  - `tool_result` SSE 事件
  - `tool_call` runtime card
  - 现在都显式带上：
    - `status`
    - `blocked`
    - `blocked_kind`
    - `blocked_reason`

- run ledger
  - `tool_round_recorded` 现在会带上：
    - `tool_status_counts`
    - `blocked_tool_count`
    - `blocked_tools`
  - 同时会额外写入 `tool_call_blocked` 事件
  - 这样 task run detail 可以直接看到阻塞点，而不必从原始 tool output 里猜测。

- pipeline runtime
  - pipeline 工具白名单拒绝现在被显式投影成 `approval_blocked`
  - workspace / sandbox 型拒绝会被显式投影成 `sandbox_blocked`
  - 其 task ledger 事件也与 chat runtime 对齐

这一轮依然没有实现真正的：

- pending approval queue
- approval token / resume token
- blocked action resume

但它已经完成了一个很关键的中间层收敛：

> 现在系统已经能稳定地区分“普通工具失败”与“治理/沙箱阻塞”，后续再补 pending approval queue 时，不需要重新发明 blocked action 的表达。

### 11.12 2026-04-25 新进展：统一 pending approval / escalation queue 已落地

在 11.11 解决 blocked action 表达之后，这一轮把“阻塞后放到哪里等待人工处理”也补上了。

- 新增持久化队列模型
  - `approval_queue_items`
  - 统一承载两类等待处理项：
    - `approval`
    - `escalation`

- shared service
  - 新增 `backend/services/approval_queue.py`
  - 提供：
    - create
    - list
    - get
    - resolve
    - serialize
  - 并通过 `request_key` 做最小去重，避免同一阻塞点无限重复入队。

- runtime / ledger 接入
  - blocked tool call 现在会：
    - 先写 `approval_queue_item_created`
    - 再写 `tool_call_blocked`
  - `tool_call_blocked` payload 里会带 `queue_item_id`
  - `task_run detail` 现在也会直接附带 `approval_queue_items`

- pipeline gate 接入
  - manual gate 被阻塞时，也会进入同一个 approval queue
  - queue item target 为 `pipeline_gate`
  - 后续 gate approve / reject 时，会同步 resolve 对应 queue item

- 新 API
  - `GET /api/approval-queue`
  - `GET /api/approval-queue/{id}`
  - `POST /api/approval-queue/{id}/approve`
  - `POST /api/approval-queue/{id}/reject`

当前这套 queue 已经能承担：

- 统一查看当前有哪些 pending approval / escalation
- 把 tool blocked 与 pipeline gate 放进同一条人工处理通道
- 为后续 UI / monitor / recovery 接入提供稳定对象模型

但它还没有完全达到 Codex 那种闭环：

- tool approval 通过后，还不会自动恢复原 blocked action
- escalation 目前只是“持久化决策 + ledger 可见”，还不是“执行恢复”
- queue 还没进入 monitor 首页的一等公民视图

所以后续最自然的下一步就是：

1. 把 queue resolution 接成 blocked action resume / retry
2. 再把 queue 投影到 monitor / frontend runtime controls

### 11.13 2026-04-25 新进展：approval queue 已开始闭环到 blocked tool replay

在 11.12 把 pending approval queue 落地之后，这一轮继续把它往 Codex 风格闭环推进了一步：

- tool approval 不再只是“人工点同意然后改状态”
  - 对 `target_kind=tool`
  - 且 `request_payload.resume_supported=true`
  - 的 queue item，`POST /api/approval-queue/{id}/approve` 现在会直接尝试 replay blocked tool

- runtime tool replay
  - 普通 chat / orchestration 路径下，会按 queue item 里保存的：
    - `tool_name`
    - `arguments`
    - `chatroom_id`
    - `project_id`
    - `agent_name`
  - 重建 runtime kwargs，并以 `__catown_approval_granted=true` 再执行一次 tool
  - replay 结果会重新进入统一 `tool_round_recorded` ledger，而不是走一条旁路

- pipeline tool replay 准备工作也补上了
  - pipeline blocked tool queue item 现在会额外落：
    - `pipeline_run_id`
    - `pipeline_stage_id`
    - `stage_name`
    - `display_name`
  - 并且 `pipeline.engine` 新增了 blocked pipeline tool replay helper，为后续 API / UI 统一恢复打底

- queue resolution payload 更有“动作语义”
  - approve 之后的 `resolution_payload` 现在会显式记录：
    - `action_taken`
    - `replay_status`
    - `replay_success`
    - `replay_blocked`
    - `replay_blocked_kind`
    - `replay_result_preview`
  - 因此前端/monitor 不必再自己推断“批准之后到底发生了什么”

- 删除清理链也同步补齐
  - project/chat 删除时，现在会连带清掉 `approval_queue_items`
  - 避免 bulk delete `task_runs` 绕过 ORM cascade 后留下孤儿 queue row

这意味着系统已经从：

- blocked tool 只能“记录下来，等人看”

推进到：

- 一部分 blocked tool 已经能“批准后立即 replay，并把 replay 结果重新写回统一 ledger”

离完全体 Codex 风格闭环还差两步：

1. replay 之后继续回到 agent turn，而不是只停在 tool replay 本身
2. monitor / frontend 把 pending queue 和 replay 结果做成一等公民控制面

### 11.14 2026-04-25 新进展：approval queue 已投影到 monitor 控制面

这一轮把 queue 从“后端可查”继续推进到“monitor 可操作”：

- 新增 monitor 视图
  - `GET /api/monitor/approval-queue`
  - 返回 pending / approved / rejected 的统一视图
  - 每个条目会补齐：
    - chat / project / task run 语义
    - request / resolution preview
    - replay 状态

- monitor overview 也补了队列统计
  - `system.stats.approval_queue_total`
  - `system.stats.approval_queue_pending`

- 前端 monitor 的 Approvals 页改成读真实 queue
  - pending approvals 直接来自 `approval-queue`
  - approve / reject 能在 monitor 里直接执行
  - queue 状态变更后会回流到 task-run / ledger / monitor snapshot

这一步的意义是：

- approval queue 不再只是运行时内部日志
- 它已经成为 monitor 上的操作对象
- 更接近 Codex 那种“任务态 / 审批态 / 运行态统一可见”的控制面

### 11.15 2026-04-25 新进展：approval replay 已能回到原 agent turn 继续执行

这一轮把 approval queue 从“批准后 replay tool”继续推进到“批准后恢复 agent turn”：

- approve replayable runtime tool 时，不再停在 tool replay
  - 对 runtime chat/orchestration 里的 replayable blocked tool
  - approve 后会先 replay tool
  - 然后把 replay 结果写成一条 `tool_result` chat message
  - 再回到原 `task_run` 上继续触发 `trigger_agent_response(...)`

- 续跑保持在同一个 task run 内
  - 不新建第二个 run
  - 会把原 run 重新置回 `running`
  - 然后继续追加：
    - `approval_queue_item_followup_triggered`
    - 后续 agent turn / tool round / agent completion 事件
  - 这样 ledger 视角更接近 Codex 的“单次执行连续恢复”

- prompt/history 也补了 tool-role 语义
  - `build_recent_history(...)` 现在识别 `message_type in {"tool_result", "tool"}`
  - 会把它们转换成 `role="tool"` 的 history message
  - 并带上 `tool_call_id`
  - 这使 replay 结果能以更像原生工具回传的方式重新进入模型上下文

- follow-up 只对 runtime replay 生效
  - pipeline gate / pipeline blocked tool 目前仍保持保守
  - 先不跨到 pipeline 自动续跑，避免把治理性阻塞和可恢复执行混在一起

这一步完成后，runtime approval 闭环已经从：

- blocked
- 人工 approve
- replay tool

推进到：

- blocked
- 人工 approve
- replay tool
- 把结果回灌给模型
- 在原 turn 上继续完成执行

这已经明显更接近 Codex 风格的“工具中断后恢复同一轮执行”。

### 11.16 2026-04-25 实施状态核对

为避免这份 ADR 同时承载“目标态”和“已实现增量”后产生误读，这里补一份当前核对结论。

#### A. 已基本实现

- `11.8 pipeline policy 已开始显式编译`
- `11.9 chat / orchestration runtime 也开始消费同类 policy`
- `11.10 tool-level approval / sandbox / escalation 已投影进 runner policy`
- `11.11 tool blocked 已显式分成 approval_blocked / sandbox_blocked`
- `11.12 统一 pending approval / escalation queue 已落地`
- `11.13 approval queue 已开始闭环到 blocked tool replay`
- `11.14 approval queue 已投影到 monitor 控制面`
- `11.15 approval replay 已能回到原 agent turn 继续执行`

这些条目当前都已有对应代码落点，不再只是计划。

#### B. 部分实现，尚未收口

- `11.2 Phase 1：pipeline 先接入统一 run ledger`
  - bridge 与主要 lifecycle 事件已落地
  - 但 pipeline 仍未完全成为统一 runtime 的一等公民

- `11.3 Phase 2：抽出共享 runner 语义`
  - 已抽出 shared turn executor / turn-state / policy / ledger helper
  - 但 sync / SSE / orchestration / pipeline stage 仍未完全收敛成单一 runner envelope

- `11.4 Phase 3：把 pipeline 从执行引擎改造成治理编排器`
  - 已开始出现 policy compiler / governance projection
  - 但 pipeline 仍保留独立 engine 语义，还不是纯治理层

#### C. 尚未完成

- `11.5 Phase 4：补齐 Codex 风格缺口`
  - 真正的 subagent lifecycle 仍未完成
  - checkpoint 型恢复仍未完成
  - ownership / write-scope / retry / dead-letter 仍未完成
  - pipeline blocked tool 的 approve 后自动续跑仍未完成

#### D. 当前准确判断

截至当前，更准确的状态不是：

- “ADR 里的目标架构已经全部做完”

而是：

- “ADR 中最近几轮增量进展大多已落地”
- “ADR 中定义的长期目标架构仍在收敛中”

### 11.17 2026-04-25 新进展：monitor 已显式展示 approval replay follow-up 状态

在 11.15 把 runtime approval replay 接回原 agent turn 之后，这一轮继续把这段状态补到 monitor 控制面：

- `monitor approval queue entry` 不再只暴露 replay 结果
  - 现在会额外投影：
    - `followup_attempted`
    - `followup_status`
    - `followup_reason`
    - `followup_error`
    - `followup_message_id`

- resolution preview 也更贴近实际恢复结果
  - 如果 replay 后续跑失败
  - monitor 不再只显示 replay preview
  - 会优先带出 `followup_error`

- 前端 Approvals 页面已能区分：
  - queue item 是否只是 replay 成功
  - 还是 replay 后已经继续完成 follow-up
  - 或 replay 后 follow-up 被跳过 / 失败

这一步的意义是：

- approval queue 不再只表达“批了没批”
- 也能表达“批完之后执行链有没有真正继续跑完”
- 更接近 Codex 风格 control plane 对恢复结果的可见性

### 11.18 2026-04-25 新进展：context compaction 已开始进入 runtime event 与 monitor

此前 Catown 虽然已经有：

- history summary
- task-state fragments
- `ContextSelector` token / fragment budget

但 compaction 仍然是“静默发生”，没有进入控制面。

这一轮把它往前推进了一步：

- `ContextSelector` 现在会显式产出 selector diagnostics
  - developer / user 各自的：
    - candidate_count
    - selected_count
    - dropped_count
    - truncated_count
    - candidate_tokens
    - selected_tokens
  - 以及 selector 侧的：
    - `max_fragments`
    - `max_tokens`

- chat runtime / pipeline runtime 现在会在发生 compaction 时写 `context_compaction` event
  - 不再只是“消息被截短了，但外部不知道”
  - 而是会把 compaction 作为 run 级事件留痕

- monitor overview / Context 页面也开始消费这类事件
  - overview 新增：
    - `system.stats.context_compactions`
    - `recent_compactions`
  - Context 页不再显示“尚未发出 compaction 事件”的占位文案
  - 已能看到最近 compaction 的：
    - dropped / truncated 数量
    - candidate / selected 数量
    - max fragment / token budget

这一步还不是完整的 checkpoint-friendly compaction 模型，但意义在于：

- compaction 首次从“隐式 selector 行为”升级成“显式 runtime telemetry”
- 后续再做：
  - compaction-aware resume
  - checkpoint snapshot
  - context pressure trend

就有了可复用的观测基础。

### 11.19 2026-04-25 新进展：checkpoint-friendly task snapshot 已开始进入 recovery 与 monitor

在 11.18 把 compaction 提升成显式 runtime telemetry 之后，这一轮继续往“checkpoint-friendly recovery”推进了一步，但仍然保持增量路线：

- 没有新增专门的 checkpoint 表
- 而是先从现有 `TaskRun.events` 与 `approval_queue_items` 派生统一 `checkpoint_snapshot`

这一轮补上的内容是：

- `serialize_task_run_summary()` 现在会输出 `checkpoint_snapshot`
  - 当前快照包含：
    - `event_count`
    - `latest_event_type`
    - `latest_event_at`
    - `latest_agent_turn`
    - `latest_compaction`
    - `latest_scheduler_runtime`
    - `pending_approval_count`
    - `approval_queue_count`
    - `status`
    - `summary`

- recovery 入口事件现在显式记录恢复起点快照
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - 都会带上恢复开始时的 `checkpoint_snapshot`

- monitor 的 task-run detail 现在也直接显示 checkpoint snapshot
  - 不再需要手工翻整条 event stream 才知道：
    - 最近一次 agent turn 停在什么响应
    - 最近一次 compaction 丢了多少上下文
    - 最近一次 scheduler runtime 是否已经可重建

这一步仍然不是完整的 Codex 风格 checkpoint continuation：

- 现在的 snapshot 还是“从 ledger 派生”
- 不是 executor 在关键点主动持久化的标准 checkpoint object
- 也还不包含完整 subagent tree / inflight tool state / turn-local continuation cursor

但它已经把恢复语义从“只有 rebuild 逻辑”推进到：

- 恢复前有可读 checkpoint 视图
- 恢复时有显式 checkpoint 起点记录
- monitor 上能直接看到 checkpoint 级状态

后续如果继续往前走，下一层自然会是：

- 更标准的 scheduler continuation cursor
- approval / replay / follow-up 与 checkpoint snapshot 的统一表达
- 从“派生 snapshot”逐步演进到“显式 checkpoint object”

### 11.20 2026-04-25 新进展：pipeline blocked tool 已能停住 stage 并在 approval 后恢复

在 11.13 到 11.15 把 runtime blocked tool replay 链逐步闭环之后，pipeline 侧还留着一处明显差距：

- tool 调用虽然已经会产出 `approval_queue_item`
- 但 stage 本身并不会因为 blocked tool 停住
- approve 后也不会把 pipeline 接回当前 stage 继续执行

这一轮先把这条链补到了可用状态：

- `_run_agent_stage()` 现在会把 blocked tool round 显式挂到当前 stage 的临时状态
  - 记录：
    - `tool_name`
    - `status`
    - `blocked_kind`
    - `blocked_reason`
    - `turn`

- `_execute_stage()` 发现本轮 stage 因 blocked tool 停住后，不再把阶段记成 `completed`
  - 而是：
    - 置 `stage.status = "blocked"`
    - 保留 `output_summary`
    - 写入 `pipeline_stage_blocked`
    - 让外层执行循环按既有逻辑把 pipeline 置成 `paused`

- approval queue API 在 replay pipeline blocked tool 成功后，开始补 follow-up continuation
  - 先通过 `pipeline_engine.instruct(...)` 注入 replay result
  - 再对暂停中的 pipeline 调 `pipeline_engine.resume(...)`
  - 同时写：
    - `approval_queue_item_followup_triggered`
    - 若失败则 `approval_queue_item_followup_failed`

这一步的意义是：

- pipeline 不再把“被审批挡住的 tool turn”伪装成正常 stage 完成
- pipeline blocked tool 与 runtime blocked tool 终于开始共享同一套：
  - blocked queue
  - replay
  - follow-up continuation

它仍然不是完整的 executor checkpoint continuation：

- resume 现在还是“给当前 stage 注入 follow-up context，然后重跑当前 stage”
- 不是从精确的 turn-local cursor 继续

但相较之前，已经从“只有 replay，没有恢复执行”推进到了：

- stage 会停住
- queue 能 replay
- replay 后 pipeline 会继续

### 11.21 2026-04-25 新进展：checkpoint snapshot 已开始携带 continuation cursor

在 11.19 把 checkpoint snapshot 引入 recovery 与 monitor 之后，仍然留着一个明显问题：

- snapshot 能告诉你“最近发生了什么”
- 但还不能明确告诉你“如果现在要恢复，下一步应该怎么续”

这一轮先补了一个轻量级 `continuation_cursor`，仍然坚持不新增专门表结构：

- `checkpoint_snapshot.continuation_cursor` 完全从现有：
  - `TaskRun.events`
  - `approval_queue_items`
  - `runtime` payload
  派生

当前 cursor 会显式区分几类恢复语义：

- `await_approval`
  - 存在 pending tool approval / escalation queue item
  - 同时给出：
    - `resume_strategy`
    - `turn`
    - `tool_name`
    - `blocked_kind`
    - `queue_item_id`

- `followup_injected`
  - approval replay 已完成，follow-up continuation 已经被触发

- `resume_scheduler`
  - 当前 run 仍有 scheduler runtime snapshot，可按现有恢复逻辑继续 rebuild

- `continue_agent_turn`
  - 最近停在 tool round 之后，但还没有进入 blocked/pending queue 语义

- `none`
  - 当前没有需要恢复的 continuation cursor

这一轮同时把 cursor 投到了两个地方：

- recovery 入口事件
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - 都会记录恢复开始前的 continuation cursor

- monitor task-run 控制面
  - 列表接口已经能返回 `checkpoint_snapshot.continuation_cursor`
  - 详情页也直接展示：
    - next action
    - resume strategy
    - tool / turn / queue 等 cursor payload

这一步仍然不是完整的 turn-local continuation cursor：

- 现在的 cursor 只是“恢复决策视图”
- 还不是 executor 可直接反序列化恢复的内部 program counter

但它已经把 Catown 从“只有快照，没有恢复建议”推进到了：

- snapshot 除了描述状态
- 还开始描述恢复入口与下一动作

### 11.22 2026-04-25 新进展：checkpoint snapshot 已开始携带 turn-local continuation state

在 11.21 加入 `continuation_cursor` 之后，还有一个残留问题：

- cursor 已经知道“下一步应该做什么”
- 但还没有携带“恢复时实际要喂给模型/执行器的最近一轮 turn-local payload”

这一轮继续保持无 schema 迁移路线，先把最近一轮 tool round 的 continuation state 显式写进 ledger：

- `tool_round_recorded` payload 现在开始携带 `turn_local_state`
  - 包括：
    - `assistant_content`
    - `tool_results`
    - `protocol_messages`

- `checkpoint_snapshot` 现在会继续派生 `turn_local_state`
  - 当前包含：
    - `turn`
    - `tool_names`
    - `blocked_tool_count`
    - `assistant_content`
    - `protocol_messages`
    - `tool_results`
    - `blocked_tool`

这一步的意义在于：

- `continuation_cursor` 负责回答：
  - “接下来该做什么”
- `turn_local_state` 负责回答：
  - “如果要从最近一轮 tool turn 继续，手头可恢复的最小 protocol payload 是什么”

monitor 上也同步做了投影：

- task-run 详情现在能直接查看：
  - continuation cursor
  - turn-local state payload

这仍然不是完整的 executor continuation object：

- 当前只覆盖“最近一轮 tool turn”的 protocol payload
- 还没有把多轮 turn state、older summary rounds、完整 in-flight cursor 结构化持久化

但相较之前，已经从“只有恢复建议”推进到了：

- 有恢复建议
- 也有最近一轮可直接复用的 turn-local continuation payload

### 11.23 2026-04-25 新进展：turn-local continuation state 已开始保留 multi-round protocol tail

在 11.22 把最近一轮 tool turn 的 continuation payload 放进 snapshot 之后，还有一个明显差距：

- 只能看到“最后一轮”
- 看不到多轮 tool turn 的最近 protocol tail
- 也看不到更早轮次已经被压缩成了什么摘要

这一轮继续保持纯派生路线，从已有 `tool_round_recorded` event 序列里补了两层结构：

- `protocol_tail_messages`
  - 当前取最近两轮 tool round 的 protocol messages
  - 作为“恢复时最值得保留的 recent protocol tail”

- `prior_round_summaries`
  - 更早的 tool round 不再混在 tail 里
  - 而是派生为更轻量的 per-round summary
    - `turn`
    - `tool_names`
    - `blocked_tool_count`
    - `assistant_content`

于是当前的 `turn_local_state` 分成了三层：

- 最新一轮：
  - `protocol_messages`
  - `tool_results`
- 最近多轮 tail：
  - `protocol_tail_messages`
- 更老轮次摘要：
  - `prior_round_summaries`

这一步的意义是：

- snapshot 不再只是“最近一轮的孤立 payload”
- 而开始接近真正 continuation object 会需要的：
  - recent protocol tail
  - older round summary
  - blocked tool marker

它仍然不是完整的 executor continuation object：

- tail 长度现在是派生规则，不是 executor 显式 checkpoint policy
- older summary 仍然是展示/恢复辅助结构，不是 turn engine 直接消费的原生 state

但已经比 11.22 更接近 Codex 风格：

- 不只知道最后一轮
- 也开始知道“最近几轮该带什么、再早的轮次该怎么折叠”

### 11.24 2026-04-25 新进展：continuation state 已开始回填到 follow-up runtime turn

在 11.23 之前，Catown 已经能：

- 派生 continuation cursor
- 派生 turn-local state
- 派生 multi-round protocol tail

但这些状态仍然主要停留在“可观测层”，没有真正接回运行态。

这一轮把第一条消费链补上了：

- `build_turn_state_from_checkpoint_snapshot(...)`
  - 现在能从 `checkpoint_snapshot` 回填：
    - `continuation_protocol_messages`
    - `continuation_summaries`
  - 并把它们装回 `TurnContextState`

- `TurnContextState.protocol_messages()`
  - 现在会先输出 continuation protocol tail
  - 再拼接本轮新增的 tool round protocol

- `TurnContextState.summarized_tool_lines()`
  - 现在也会先带上 prior round summaries
  - 再接本轮运行中继续累积的 summary lines

- runtime approval replay follow-up
  - 在重新触发 `trigger_agent_response(...)` 时
  - 不再只传一段 `extra_context`
  - 而是同时把 `checkpoint_snapshot` 回填到 `TurnContextState`

这意味着当前的 follow-up runtime turn 已经开始真正消费：

- prior tool-call protocol
- multi-round continuation tail
- older round summaries

而不再只是“给模型一句文字描述”。

这一步仍然不是完整的 executor continuation：

- 当前只接回了 runtime follow-up 这条链
- orchestration / pipeline / startup recovery 还没有统一消费这一套 state

但意义很明确：

- continuation state 第一次从“监控/调试数据”
- 进入“实际运行态输入”

### 11.25 2026-04-25 新进展：recovery 事件已开始显式声明 continuation-state consumption

在 11.24 之后，runtime follow-up 已经会真实消费 continuation state，但 startup/manual recovery 仍有一个可见性缺口：

- 恢复链本身虽然依赖 checkpoint snapshot
- 但事件层还没有明确声明：
  - “这次恢复到底消费了哪类 continuation state”

这一轮先把这层恢复元数据补齐：

- 新增 `recovery_continuation_state`
  - 当前由 recovery 起点 snapshot 派生
  - 会显式记录：
    - `consumed`
    - `next_action`
    - `resume_strategy`
    - `consumed_layers`
    - `protocol_tail_message_count`
    - `prior_round_summary_count`

- 这份元数据现在会进入：
  - `task_run_recovery_started`
  - `scheduler_recovery_state_rebuilt`
  - `task_run_recovery_completed`

对于当前 orchestration recovery，这层元数据最常见的表达是：

- `next_action = resume_scheduler`
- `resume_strategy = rebuild_from_runtime_snapshot`
- `consumed_layers` 至少包含 `runtime_snapshot`

这一步的意义是：

- recovery 不再只是“做了恢复”
- 而是开始显式说明“恢复消费了什么 continuation state”

它仍然不是完整的 recovery executor integration：

- 现在更多是恢复路径的消费声明与观测增强
- 不是让 orchestration recovery 直接反序列化 `protocol_tail_messages` 去推进执行

但它把 recovery 也纳入了同一套 continuation-state 语义体系里：

- follow-up runtime turn 已经消费
- recovery 事件现在也开始声明消费

### 11.26 2026-04-25 新进展：orchestration recovery 已开始真实消费 checkpoint protocol tail

11.25 只补齐了 recovery 的消费声明，但执行面仍有明显缺口：

- interrupted orchestration recovery 在恢复 agent turn 时
- 还是主要依赖 `extra_context` 文本与 runtime snapshot 元数据
- 没有把 checkpoint 中保留下来的 protocol tail 真正送回 `TurnContextState`

这意味着：

- recovery event 已经会说自己消费了 `runtime_snapshot`
- 但恢复后的 prompt 未必真的带回最近一轮 tool-call assistant/tool message
- continuation state 在 recovery 路径里仍然停留在“声明过”，没有完全进入“执行输入”

这一轮把执行链补上：

- `_resume_interrupted_orchestration_task_run(...)`
  - 在恢复开始时继续构建 `recovery_checkpoint_snapshot`
  - 并把这份 snapshot 传进恢复用的 `_run_single_agent_turn(...)`

- `_run_single_agent_turn(...)`
  - 新增 `checkpoint_snapshot` 参数
  - turn state 初始化不再只用空白 `TurnContextState`
  - 而是先通过 `build_turn_state_from_checkpoint_snapshot(...)`
    回填：
    - continuation protocol tail
    - prior round summaries
    - previous agent work
  - 然后再合并当前调度阶段补充的 inter-agent messages

因此现在的 interrupted orchestration recovery：

- 不只是事件层声明自己消费了 continuation state
- 而是在真正恢复 agent turn 时
- 把 checkpoint 里的 recent protocol tail 一并送回 LLM 上下文

测试也同步收紧：

- recovery fixture 里显式注入一段 `tool_round_recorded`
  - assistant 发起 `read_file`
  - tool 返回 `Design checkpoint contents`
- startup recovery test 现在会断言：
  - `recovery_continuation_state.protocol_tail_message_count == 2`
  - 恢复后的首个 LLM 输入中确实包含：
    - 该 assistant tool-call message
    - 该 tool result message

这一步的意义很直接：

- recovery continuation state 终于不只是一份可观察元数据
- orchestration recovery 开始与 runtime approved replay follow-up 共享同一类 prompt rehydration 语义
- “checkpoint 保留了什么” 与 “恢复时真正喂回了什么” 开始对齐

### 11.27 2026-04-25 新进展：checkpoint turn state 已收口到 latest-turn window，并随 recovery 前滚

11.26 把 orchestration recovery 接上了 checkpoint protocol tail，但随即暴露出一个更细的正确性问题：

- `checkpoint_snapshot.turn_local_state`
  - 仍然是从“全量历史里最近一次 `tool_round_recorded`”直接派生
- 如果恢复后的下一步 turn 本身没有再跑 tool
  - 旧 turn 的 protocol tail 仍会继续留在 snapshot 里
- 这样在多 step recovery 里
  - 第一轮恢复消费是正确的
  - 第二轮开始就可能继续吃到上一轮 restart 前的 stale tool protocol

这和 Codex 风格 continuation 语义不一致：

- continuation state 应该描述“当前最近那个 turn 的可继续状态”
- 不是“这个 run 历史上最后一次出现过的 tool round”

这一轮做了两个收口动作：

- `build_task_run_checkpoint_snapshot(...)`
  - 新增 latest-turn window 识别逻辑
  - 先定位最近一个 turn 的事件窗口：
    - 若最后一个 `agent_turn_started` 还未完成，就取该 in-flight window
    - 否则取最近一个 `agent_turn_completed` 对应的 start/completed window
  - `latest_tool_round` / `latest_tool_blocked` / `latest_followup`
    以及 `turn_local_state`
    都改为只从这个窗口里派生

- `_resume_interrupted_orchestration_task_run(...)`
  - 不再把 recovery 起点那一份 snapshot 固定传给后续所有 step
  - 而是在每个恢复 step 开始前：
    - `refresh(task_run)`
    - 重新构建当前 step-local `checkpoint_snapshot`
  - 这样 continuation state 会随着恢复推进自然前滚

结果是：

- restart 前 analyst 留下的 tool protocol
  - 仍会进入 recovery 后的第一个 turn
- 但如果这个 turn 完成后没有新的 tool round
  - 后续 step 的 checkpoint state 就会清空旧 tail
- recovery 不会再把“已经被后续 turn 覆盖掉的旧 protocol”重复喂回模型

测试也相应补上：

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 验证 analyst 有 tool round、developer 后续无 tool 的情况下
  - monitor 看到的 latest checkpoint turn state 已经清空旧 protocol

- `test_startup_recovers_interrupted_orchestration_run`
  - 继续验证第一轮 recovery prompt 会吃到 checkpoint tool tail
  - 同时新增断言：第二轮 recovery prompt 不再包含旧的 `read_file` tool result

这一轮的意义在于：

- checkpoint continuation state 终于从“latest tool activity”
- 收口成“latest turn-local continuation window”
- recovery prompt rehydration 开始具备真正的前滚语义，而不是一次性把旧 tail 粘到底

### 11.28 2026-04-25 新进展：recovered dispatch 事件已暴露 step-local continuation snapshot

11.27 之后，恢复执行面已经具备 step-by-step 前滚语义，但观测面还有一个空洞：

- 我们知道 recovery executor 每一步都会重建 `checkpoint_snapshot`
- 也知道第一步和后续步吃到的 continuation state 可能不同
- 但事件流里还看不到“某一个 recovered dispatch 实际用了哪份 snapshot”

这会让排障停留在推断层：

- 你能从最终结果看出恢复成功了
- 但不容易直接确认：
  - 第一个恢复 step 是否真的吃到了旧 protocol tail
  - 第二个恢复 step 是否已经看到前滚后的空 tail

这一轮把这层 step-local observability 补上：

- `_resume_interrupted_orchestration_task_run(...)`
  - 在每个 recovery step dispatch 前
    先重建一次当前 `step_checkpoint_snapshot`
  - 并派生对应的 `step_recovery_continuation_state`

- `scheduler_step_dispatched`
  - 当 `recovered = true` 时
  - payload 现在额外带上：
    - `checkpoint_snapshot`
    - `recovery_continuation_state`

这样 recovery trace 现在可以逐步回答：

- 这个 step 恢复时看到的 latest agent turn 是谁
- 这个 step 恢复时 `turn_local_state` 里还有没有 protocol tail
- 这个 step 真正消费了多少条 tail message / prior summaries

测试也同步加强：

- `test_startup_recovers_interrupted_orchestration_run`
  - 现在会显式检查 recovered dispatch events
  - 第一条 recovered dispatch：
    - `assistant_content == "Open the design doc before continuing."`
    - `protocol_tail_message_count == 2`
  - 第二条 recovered dispatch：
    - `turn_local_state.protocol_messages == []`
    - `protocol_tail_message_count == 0`

这一步的意义是：

- recovery 的 continuation-state 前滚
  - 不再只是执行器内部行为
- 而是进入 durable event trace
- 让 monitor / 调试 / 审计都能按 step 精确看到“恢复时到底喂回了什么”

### 11.29 2026-04-25 新进展：pipeline resumed stage 已开始消费 checkpoint continuation state

11.28 之后，orchestration recovery 已经具备：

- step-local checkpoint snapshot
- step-local continuation-state observability
- prompt rehydration 前滚

但 pipeline 仍有一条残留分叉：

- pipeline blocked tool approve 后虽然会：
  - replay tool
  - 注入 boss instruction
  - 调 `pipeline_engine.resume(...)`
- 可真正恢复 stage 时
  - `_run_agent_stage(...)` 仍从空白 `TurnContextState()` 起步
  - 只能吃到 replay 后新增的 instruction / inter-agent message
  - 吃不到 checkpoint 里保留下来的最近一轮 tool protocol tail

这意味着 pipeline 虽然“能继续跑”：

- 但 resumed stage 首次 prompt
  - 不具备 runtime / orchestration recovery 已有的 continuation rehydration 语义
- 也就是：
  - approval replay 把执行结果接回来了
  - 但模型侧上下文并没有真正从 checkpoint continuation state 继续

这一轮把 pipeline 也接入同一条语义链：

- `_run_agent_stage(...)`
  - 在开始 stage turn loop 前
  - 先取关联 `task_run`
  - 构建 `checkpoint_snapshot`
  - 再通过 `build_turn_state_from_checkpoint_snapshot(...)`
    初始化 stage 的 `TurnContextState`

因此 resumed pipeline stage 现在不再只是：

- 从空白 turn state + 新注入 instruction 重新开跑

而是会额外带回：

- 最近一轮 assistant tool-call protocol
- 最近一轮 tool result message
- prior round summaries（若 snapshot 内存在）

测试也同步补上：

- `test_run_agent_stage_rehydrates_checkpoint_protocol_tail_on_resume`
  - 预先在关联 `task_run` 中种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `tool_call_blocked`
    - `approval_queue_item_followup_triggered`
  - 然后直接恢复 pipeline stage
  - 断言首次 LLM 输入中确实重新出现：
    - `read_file` assistant tool-call
    - `Design checkpoint contents` tool result

这一步的意义是：

- pipeline 不再只是在治理面接入统一 approval queue
- 而是开始在执行输入面共享同一套 checkpoint continuation 语义
- `chat/runtime/orchestration/pipeline`
  - 四条主路径的 continuation rehydration 开始真正收敛

### 11.30 2026-04-25 新进展：streaming orchestration step 已开始消费 checkpoint continuation state

11.29 之后，pipeline resumed stage 已接上 checkpoint continuation，但 stream orchestration 仍留着一条偏旧分支：

- 多 agent streaming orchestration 的每个 step
  - 仍通过 `_iter_agent_turn_events(...)` 起一个新的 `TurnContextState`
- 它会带：
  - `previous_agent_work`
  - inter-agent messages
- 但不会带：
  - 上一位 agent 最近留下的 tool protocol tail

这意味着 streaming orchestration 虽然表面上已经能连续协作：

- 后续 agent 能看到文字摘要和 handoff
- 但看不到最近一轮真实 assistant/tool protocol
- 与 non-stream orchestration recovery、runtime follow-up、pipeline resumed stage 的 continuation 语义仍不一致

这一轮把 stream orchestration 也接回统一语义：

- `_iter_agent_turn_events(...)`
  - 新增 `checkpoint_snapshot` 参数
  - turn state 初始化改为：
    - `build_turn_state_from_checkpoint_snapshot(checkpoint_snapshot, previous_agent_work=...)`
  - 不再从纯空白 `TurnContextState(previous_agent_work=...)` 起步

- stream orchestration scheduler loop
  - 在每个 step dispatch 前
    - `refresh(task_run)`
    - 重新构建当前 step-local `checkpoint_snapshot`
  - 然后把它传给 `_iter_agent_turn_events(...)`

因此后续 streaming step 现在会同时看到：

- 上一阶段的文字工作摘要
- durable handoff / boss / inter-agent messages
- checkpoint 中保留下来的最近 tool protocol tail

测试也同步收紧：

- `test_standalone_multi_mention_stream_rebuilds_tool_loop_from_turn_state`
  - 原本只验证 developer step 能看到 analyst 的 inter-agent message
  - 现在继续断言：
    - developer 那一轮的 LLM 输入里
    - 还能重新看到 `read_file` 的 tool protocol message

这一步的意义是：

- stream orchestration 不再只是一条“摘要驱动”的协作链
- 而是开始共享同一套 checkpoint-backed prompt rehydration 语义
- `non-stream / stream / recovery / pipeline`
  - 几条主执行路径的 continuation model 又收敛了一步

### 11.31 2026-04-25 新进展：streaming single-agent 入口也已接入 checkpoint continuation state

11.30 之后，多 agent streaming orchestration 已不再从空白 turn state 起步，但还有两处单 agent stream 入口仍保留旧逻辑：

- standalone assistant stream
- project single-agent stream

这两条路径的问题一致：

- stream turn loop 启动前直接 new 一个空白 `TurnContextState()`
- 即便关联 `task_run` 已经带有 checkpoint continuation 信息
- 首次 streaming LLM 调用也不会把这份状态重新带回 prompt

这会形成一个不必要的行为分叉：

- non-stream single-agent 已接 checkpoint snapshot
- stream orchestration 已接 checkpoint snapshot
- pipeline resumed stage 已接 checkpoint snapshot
- 但 single-agent stream 仍停留在“每次都从空白 turn state 开始”

这一轮把这两个入口也接回统一语义：

- `_stream_standalone_assistant_response(...)`
  - 在启动 `iter_stream_turn_events(...)` 前
  - 先从 `task_run` 构建 `checkpoint_snapshot`
  - 再通过 `build_turn_state_from_checkpoint_snapshot(...)`
    初始化 stream turn state

- project single-agent stream 分支
  - 同样先从当前 `task_run` 构建 `checkpoint_snapshot`
  - 再用它初始化 streaming turn state

因此现在的 single-agent stream 入口：

- 不再强制从空白 `TurnContextState()` 起步
- 如果当前 run 已保留 recent protocol tail / prior summaries
  - 首个 streaming prompt 也会真实消费这份 continuation state

测试也同步补上：

- `test_project_single_agent_stream_rehydrates_checkpoint_state`
  - 在创建 streaming `task_run` 后立刻种入一段 `tool_round_recorded`
  - 然后发起 project single-agent stream
  - 断言首个 streaming LLM 输入中确实带回：
    - `read_file` assistant tool-call
    - `Design checkpoint contents` tool result

这一步的意义是：

- 剩余直接 new 空白 `TurnContextState()` 的主 stream 入口进一步减少
- single-agent / multi-agent / recovery / pipeline 开始共享更一致的 continuation rehydration 语义
- “streaming 是不是会丢 checkpoint continuation” 这类分叉行为又少了一层

### 11.32 2026-04-25 新进展：pipeline stage-start 事件已开始投影 checkpoint continuation consumption

11.31 之后，pipeline stage 在执行输入面已经会真实消费 checkpoint continuation state，但事件层还留着一处不对齐：

- `pipeline_stage_started`
  - 仍只记录 stage policy / gate / timeout / expected artifacts
- 看不到：
  - 这个 stage 开始时实际拿到的 `checkpoint_snapshot`
  - 以及它准备消费多少 protocol tail / prior summaries

于是 pipeline 在这一点上还落后于 orchestration recovery：

- recovery 的 step-dispatch event 已经能精确说明“这一步拿了什么 continuation state”
- pipeline resumed stage 虽然执行面已正确
- 但 monitor / 调试 / 审计层还不能直接看到这次 stage-start 的 continuation 输入

这一轮把这层事件投影补齐：

- `services.run_ledger.describe_checkpoint_continuation_state(...)`
  - 抽出通用 helper
  - 统一从 `checkpoint_snapshot` 派生：
    - `consumed`
    - `next_action`
    - `resume_strategy`
    - `consumed_layers`
    - `protocol_tail_message_count`
    - `prior_round_summary_count`

- `routes/api.py`
  - recovery 继续复用同一 helper
  - 避免 recovery / pipeline 各自维护一份 continuation-state 统计逻辑

- `pipeline_stage_started`
  - 现在会额外带上：
    - `checkpoint_snapshot`
    - `continuation_state`

这样 pipeline stage-start event 现在也能直接回答：

- 这次 stage 开始时是否带着 continuation state
- 最近 protocol tail 有多少条
- prior round summaries 有多少条
- 当前 snapshot 的 latest turn-local payload 是什么

测试也同步补上：

- 新增 `test_pipeline_stage_started_exposes_checkpoint_continuation_state`
  - 预先种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `approval_queue_item_followup_triggered`
  - 然后启动 stage
  - 断言 `pipeline_stage_started` 事件中：
    - `checkpoint_snapshot.turn_local_state.assistant_content == "Open the design doc before continuing."`
    - `continuation_state.consumed is True`
    - `continuation_state.protocol_tail_message_count == 2`

- 既有 pipeline stage 测试也补充断言：
  - 新开 stage 的 `pipeline_stage_started` 明确显示：
    - `checkpoint_snapshot.event_count == 0`
    - `continuation_state.consumed is False`

这一步的意义是：

- pipeline 不只是执行器内部吃到了 checkpoint continuation
- 它的 stage-start event 现在也能把这份消费状态 durable 地投影出来
- execution semantics 与 observability semantics 又进一步对齐

### 11.33 2026-04-25 新进展：pipeline resume 事件已开始投影 checkpoint continuation consumption

11.32 把 `pipeline_stage_started` 的 checkpoint consumption 投影出来之后，pipeline 事件层还剩最后一个明显断点：

- `pipeline_resumed`
  - 仍然只说明“pipeline 恢复了”
- 但看不到：
  - 恢复那一刻 task run 上实际持有什么 `checkpoint_snapshot`
  - 以及 resume 将继续消费多少 protocol tail / prior summaries

这会让 approval replay 之后的观测链缺一段：

- `approval_queue_item_followup_triggered`
  - 会说 follow-up 原因是 `pipeline_resumed`
- `pipeline_stage_started`
  - 之后会说 stage 起步时拿到了什么 continuation state
- 但中间这个真正的 `resume` 动作本身
  - 还不能直接说明它恢复时看到的 checkpoint continuation 输入

这一轮把这段链补齐：

- `pipeline_engine.resume(...)`
  - 在把 run 状态切回 `running` 后
  - 先拿关联 `task_run` 构建 `checkpoint_snapshot`
  - 再用 `describe_checkpoint_continuation_state(...)`
    派生 `continuation_state`

- `pipeline_resumed`
  - payload 现在额外带上：
    - `checkpoint_snapshot`
    - `continuation_state`

这样 pipeline resume event 现在也能回答：

- 这次 resume 是不是带着 continuation state 恢复
- 当时 snapshot 中最近的 turn-local payload 是什么
- protocol tail / prior summaries 分别有多少

测试也同步补上：

- `test_resume_appends_checkpoint_continuation_state`
  - 预先种入：
    - `agent_turn_started`
    - `tool_round_recorded`
    - `approval_queue_item_followup_triggered`
  - 调 `engine.resume(...)`
  - 断言 `pipeline_resumed` 事件中：
    - `checkpoint_snapshot.turn_local_state.assistant_content == "Open the design doc before continuing."`
    - `continuation_state.consumed is True`
    - `continuation_state.protocol_tail_message_count == 2`

这一步的意义是：

- approval replay -> pipeline resume -> stage start
  - 这整条恢复链的每个关键事件
  - 现在都能投影 checkpoint continuation consumption
- pipeline 的执行语义与事件可观测语义又少了一层断点

### 11.34 2026-04-25 新进展：task-run checkpoint snapshot 已直接暴露 derived continuation state

11.33 之后，事件层对 continuation consumption 的投影已经补了不少，但 monitor / frontend 还有一个可用性缺口：

- `checkpoint_snapshot` 本身虽然已经同时带有：
  - `continuation_cursor`
  - `turn_local_state`
- 但如果前端想回答：
  - “当前 snapshot 是否真的消费了 continuation state？”
  - “具体消费了多少 tail message / prior summaries？”
- 还要自己再从 cursor + turn-local state 二次拼装判断逻辑

这会让同一份语义在多处重复实现：

- recovery 事件层会算一次
- pipeline 事件层会算一次
- monitor / frontend 如果也想展示摘要，又要再推一次

这一轮把这层派生结果直接放进 snapshot 主体里：

- `build_task_run_checkpoint_snapshot(...)`
  - 现在在构建完 snapshot 后
  - 会直接附带：
    - `continuation_state`

- 这份 `continuation_state`
  - 继续复用统一的 `describe_checkpoint_continuation_state(...)`
  - 因此它和 recovery / pipeline 事件里的统计口径保持完全一致

这样 monitor / frontend 现在不需要再自行推断：

- snapshot 本身就直接告诉你：
  - `consumed`
  - `next_action`
  - `resume_strategy`
  - `consumed_layers`
  - `protocol_tail_message_count`
  - `prior_round_summary_count`

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 继续验证 blocked-tool snapshot 时
  - `checkpoint_snapshot.continuation_state` 会直接显示：
    - `consumed is True`
    - `next_action == "await_approval"`
    - `protocol_tail_message_count == 4`
    - `prior_round_summary_count == 1`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 同时验证 latest-turn 已无 continuation payload 时
  - `checkpoint_snapshot.continuation_state.consumed is False`

Monitor 侧也顺手做了投影：

- Checkpoint Snapshot 卡片新增 `Continuation State`
  - 直接显示：
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers
- 同时提供原始 payload 折叠查看

这一步的意义是：

- continuation-state 摘要第一次成为 checkpoint snapshot 的一等公民字段
- monitor / frontend 不再需要重复实现派生逻辑
- snapshot、event、UI 三层的 continuation 语义进一步统一

### 11.35 2026-04-25 新进展：Monitor run 列表与事件流已开始直接展示 continuation-state 摘要

11.34 之后，`checkpoint_snapshot` 本身已经直接带 `continuation_state`，但 Monitor 交互上还留着一个可见性问题：

- 详情页的 Checkpoint Snapshot 卡片里已经能看到 continuation-state
- 但 run 列表本身仍然只显示：
  - event count
  - latest event type
  - client turn id
- event 流也仍然主要依赖用户自己展开 raw payload

这导致一个常见排障动作仍然不够直接：

- 你想快速扫一批 run，找出哪些 run 带着 continuation state
- 或想在事件流里快速看出：
  - 这条 `pipeline_resumed` / `pipeline_stage_started` / `task_run_recovery_started`
    到底消费了什么 continuation payload
- 还是得点开详情或直接读 raw JSON

这一轮把这层 UI 摘要补上：

- Monitor task-run 列表
  - 如果该 run 的 `checkpoint_snapshot.continuation_state.consumed == true`
  - footer 现在会直接显示 continuation-state 摘要
  - 例如：
    - next action
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers

- Task-run 事件流
  - 新增通用 continuation-state 摘要提取逻辑
  - 会优先读取：
    - `recovery_continuation_state`
    - `continuation_state`
    - `checkpoint_snapshot.continuation_state`
  - 对这些事件直接显示一行可读摘要

因此现在像这些事件：

- `task_run_recovery_started`
- `scheduler_step_dispatched`（recovered step）
- `pipeline_resumed`
- `pipeline_stage_started`

在 UI 上都不再只是“有个 payload 可以点开”：

- 它们会直接把 continuation-state 摘要暴露在事件行里
- 让 operator 扫读时就能知道这一步到底吃了什么 continuation state

这一步的意义是：

- continuation-state 终于不只在 snapshot/detail/raw payload 里可见
- 它开始进入 run 列表与事件流的主阅读路径
- Monitor 对这类恢复/续跑语义的可读性又提升了一层

### 11.36 2026-04-25 新进展：task-run detail 事件已直接序列化 derived continuation state

11.35 之后，Monitor UI 已经会从 event payload 中提取 continuation-state 摘要，但这层逻辑本质上仍有重复：

- 后端事件 payload 里可能出现三种入口：
  - `recovery_continuation_state`
  - `continuation_state`
  - `checkpoint_snapshot.continuation_state`
- 前端为了展示摘要
  - 还得自己逐类判断这些分支

这意味着同一份语义仍然横跨两层：

- 后端负责产出 continuation-state 相关 payload
- 前端还要再负责“识别哪一种 payload 里藏着 continuation-state”

这一轮把这层派生逻辑继续前移到序列化层：

- `serialize_task_run_detail(...)`
  - 现在每条 event 除了原始 `payload`
  - 还会直接带上：
    - `continuation_state`
    - `continuation_state_summary`

- 提取逻辑统一为：
  - 优先读 `recovery_continuation_state`
  - 其次读 `continuation_state`
  - 再次读 `checkpoint_snapshot.continuation_state`
  - 若只有 `checkpoint_snapshot`，则按统一 helper 现场派生

同时补了一个通用 summary formatter：

- `summarize_continuation_state(...)`
  - 用统一口径输出：
    - next action
    - resume strategy
    - tail message 数量
    - prior summary 数量
    - consumed layers

测试也同步补上：

- `test_startup_recovers_interrupted_orchestration_run`
  - 继续验证 recovery started event 的原始 payload
  - 同时新增断言：
    - `continuation_state.consumed is True`
    - `continuation_state_summary == "resume scheduler · via rebuild_from_runtime_snapshot · 2 tail messages · runtime_snapshot, protocol_tail"`

前端也顺手收口：

- `TaskRunEvent` 类型现在直接声明：
  - `continuation_state`
  - `continuation_state_summary`
- Monitor event 列表优先使用后端直接给出的 summary
  - 只把原有 payload 解析逻辑保留为兜底兼容

这一步的意义是：

- event 级 continuation 语义第一次成为 task-run detail API 的显式字段
- 前端不再需要理解后端不同事件 payload 的分支结构
- continuation-state 的派生逻辑继续从 UI 层回收到序列化层

### 11.37 2026-04-25 新进展：checkpoint snapshot 也已直接序列化 continuation summary

11.36 之后，event 级 continuation 摘要已经直接由后端输出，但 snapshot 级展示还留着最后一层重复：

- `checkpoint_snapshot.continuation_state` 已经存在
- 前端虽然可以直接展示它
- 但 run 列表和 detail 卡片里那段简短摘要
  - 仍然还要自己做一遍字符串拼装

这意味着：

- event 摘要已经是后端产物
- snapshot 摘要却还停留在前端拼接
- 同一类 summary 在 snapshot / event 两侧仍有两套来源

这一轮把 snapshot 侧也收口：

- `build_task_run_checkpoint_snapshot(...)`
  - 在附加 `continuation_state` 之后
  - 继续直接附带：
    - `continuation_state_summary`

- 这份 summary 同样复用统一的：
  - `summarize_continuation_state(...)`

因此现在：

- event 级摘要
  - 来自后端 `continuation_state_summary`
- snapshot 级摘要
  - 也来自后端 `continuation_state_summary`

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 继续验证 blocked-tool snapshot
  - 新增断言：
    - `continuation_state_summary == "await approval · via replay_tool_then_continue_turn · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 新增断言：
    - 无 continuation payload 时
    - `continuation_state_summary is None`

前端也相应收口：

- Monitor run 列表
  - 优先使用 `checkpoint_snapshot.continuation_state_summary`
- Checkpoint Snapshot detail 卡片
  - 也优先使用后端给出的 summary
- 原有本地拼装逻辑继续保留为兼容兜底

这一步的意义是：

- run 列表 / detail 卡片 / event 行
  - 三类 continuation 摘要现在都开始优先依赖后端统一产物
- 前端本地字符串拼装再次减少
- snapshot 与 event 的 continuation summary 来源进一步统一

### 11.38 2026-04-25 新进展：continuation summary 已提升为 task-run summary 顶层字段

11.37 之后，snapshot 与 event 都已经直接带 continuation summary，但 task-run summary 本身还保留着一层嵌套依赖：

- run 列表想读 continuation summary
- 仍然需要先走到：
  - `checkpoint_snapshot.continuation_state_summary`

虽然这已经比前端自己拼字符串好多了，但对 summary 视角来说仍然不够直接：

- task-run summary 本身就是列表接口的主对象
- continuation summary 既然已经是 summary 级语义
- 最自然的位置应该就是 summary 顶层字段

这一轮把它再往上提一层：

- `serialize_task_run_summary(...)`
  - 现在除了 `checkpoint_snapshot`
  - 还会直接附带：
    - `continuation_state`
    - `continuation_state_summary`

这样：

- task-run 列表接口
- task-run detail 接口
- monitor websocket detail payload

都会天然带上同一份顶层 continuation 摘要，而不需要调用方再钻进 checkpoint 子对象里找。

测试也同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 新增断言：
    - `entry["continuation_state"]["consumed"] is True`
    - `entry["continuation_state_summary"] == "await approval · via replay_tool_then_continue_turn · 4 tail messages · 1 prior summaries · protocol_tail, prior_round_summaries"`

- `test_monitor_checkpoint_snapshot_scopes_turn_state_to_latest_turn`
  - 新增断言：
    - `entry["continuation_state"]["consumed"] is False`
    - `entry["continuation_state_summary"] is None`

前端也顺手收口：

- Monitor run 列表
  - 优先读顶层 `run.continuation_state_summary`
- detail 卡片
  - 也优先读顶层 `selectedTaskRunDetail.continuation_state_summary`
- `checkpoint_snapshot.continuation_state_summary`
  - 仍保留为兼容与下钻视图来源

这一步的意义是：

- continuation summary 终于进入 task-run summary 顶层对象
- run 列表/详情页对嵌套 checkpoint 结构的依赖进一步降低
- summary / snapshot / event 三层对象的职责开始更清晰

### 11.39 2026-04-25 新进展：task-run summary 已直接暴露 latest continuation event 摘要

11.38 之后，task-run summary 已经直接带当前 snapshot 的 continuation 摘要，但仍有一个“当前状态 vs 最近动作”的语义差：

- `continuation_state_summary`
  - 描述的是当前 snapshot 的 continuation 状态
- 它不一定告诉你：
  - 最近一次真正发生的 continuation 相关事件是什么
  - 比如最近一次 recovery started / pipeline resumed / stage started 到底是哪一步

于是当 operator 想快速扫列表时，仍会遇到一个细小断点：

- 当前 snapshot 可能已经被后续 turn 前滚覆盖
- 但你还想知道“最近一次 continuation 动作”到底是什么
- 还是得点开完整事件流往回找

这一轮把这个“最近动作摘要”也提到 summary 顶层：

- `serialize_task_run_summary(...)`
  - 现在会回扫 events
  - 找到最近一条带 `continuation_state_summary` 的事件
  - 直接输出：
    - `latest_continuation_event_type`
    - `latest_continuation_event_summary`
    - `latest_continuation_event_at`

因此 task-run summary 现在同时有两类 continuation 语义：

- 当前 snapshot continuation
  - `continuation_state`
  - `continuation_state_summary`

- 最近一次 continuation 动作
  - `latest_continuation_event_type`
  - `latest_continuation_event_summary`
  - `latest_continuation_event_at`

测试也同步补上：

- `test_startup_recovers_interrupted_orchestration_run`
  - 新增断言：
    - `latest_continuation_event_type == "task_run_recovery_completed"`
    - `latest_continuation_event_summary == "resume scheduler · via rebuild_from_runtime_snapshot · 2 tail messages · runtime_snapshot, protocol_tail"`

前端也顺手接上：

- Monitor run 列表
  - 直接显示 `latest_continuation_event_summary`
- detail 卡片
  - 新增 `Latest Continuation Event`
  - 直接展示 event type + summary + 时间

这一步的意义是：

- task-run summary 不再只回答“当前 continuation 状态是什么”
- 也开始回答“最近一次 continuation 动作是什么”
- current state 与 recent action 两条线索在 summary 层开始并列可见

### 11.40 2026-04-27 新进展：task-run summary 已直接暴露 scheduler runtime 摘要

继续沿着 “summary 层先回答 operator 最常问的问题” 这个方向，当前 monitor 还有一个明显断点：

- `checkpoint_snapshot.latest_scheduler_runtime`
  - 已经有原始 runtime snapshot
- 但 run 列表和 run 详情如果只看 summary 顶层
  - 仍然不知道最近 scheduler 运行态到底是什么
  - 还得下钻到嵌套 checkpoint 或 raw payload

这会让编排态势判断停在半路：

- 你知道 run 现在能不能继续
- 但不知道 scheduler 当前到底积压了多少 ready / running / waiting step

这一轮把 scheduler runtime 也提到 task-run summary 顶层：

- `serialize_task_run_summary(...)`
  - 直接输出：
    - `latest_scheduler_runtime`
    - `scheduler_runtime_summary`
- `scheduler_runtime_summary`
  - 由后端统一格式化
  - 当前输出：
    - `completed`
    - `ready`
    - `running`
    - `waiting`
    - 如有 `step_count` 再补 `total`

测试同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 追加一条带 `runtime` payload 的 ledger event
  - 断言 task-run summary 会直接返回：
    - `latest_scheduler_runtime.completed_step_count == 1`
    - `latest_scheduler_runtime.ready_step_count == 2`
    - `scheduler_runtime_summary == "1 completed · 2 ready · 1 running · 0 waiting · 4 total"`

前端也顺手收口：

- Monitor run 列表
  - 直接显示 `run.scheduler_runtime_summary`
- Selected Run Detail
  - 新增 summary 级 `Scheduler Runtime`
  - 优先读顶层 summary 字段，缺失时再退回 checkpoint snapshot

这一步的意义是：

- scheduler 当前运行态开始成为 task-run summary 的一等字段
- operator 不必先点开 checkpoint raw payload 才能看出排队态势
- summary / checkpoint / raw event 三层的职责继续分离：summary 用来扫态势，checkpoint 用来恢复，raw event 用来追溯

### 11.41 2026-04-27 新进展：continuation cursor 摘要已提升到 task-run summary / snapshot

11.40 之后，task-run summary 顶层已经能直接回答 scheduler 当前运行态，但 monitor 上还有一块恢复语义仍然留在前端本地拼装：

- `checkpoint_snapshot.continuation_cursor`
  - 已经有结构化字段
- 但 run 列表和 detail 卡片想快速回答：
  - “这个 run 下一步到底准备怎么续？”
  - “是等 approval、继续 turn，还是 resume scheduler？”
- 仍然需要前端自己从：
  - `next_action`
  - `resume_strategy`
  - `tool_name`
  - `turn`
  - runtime counts
  再拼一遍短摘要

这跟前面已经收口的 continuation state / latest continuation event / scheduler runtime 一样，仍然会造成 summary 语义来源分叉：

- snapshot 有原始字段
- UI 再自行拼装字符串
- 不同列表 / 卡片很容易继续出现不同口径

这一轮把 cursor 摘要也正式提升出来：

- `build_task_run_checkpoint_snapshot(...)`
  - 现在直接附带：
    - `continuation_cursor_summary`
- `serialize_task_run_summary(...)`
  - 也把它提升到顶层：
    - `continuation_cursor`
    - `continuation_cursor_summary`

摘要规则保持轻量，只回答 operator 最关心的恢复入口：

- `await approval`
- `continue agent turn`
- `resume scheduler`
- 再按需补：
  - `via ...`
  - `tool ...`
  - `turn ...`
  - `ready / running / waiting` 计数

测试同步补上：

- `test_monitor_task_runs_exposes_continuation_cursor`
  - 新增断言：
    - `entry["continuation_cursor_summary"] == "await approval · via replay_tool_then_continue_turn · tool delete_file · turn 3"`
    - `entry["checkpoint_snapshot"]["continuation_cursor_summary"] == "await approval · via replay_tool_then_continue_turn · tool delete_file · turn 3"`
- 无 continuation cursor 的 summary / snapshot
  - 断言 `continuation_cursor_summary is None`

前端也继续收口：

- Monitor run 列表
  - 直接显示顶层 `run.continuation_cursor_summary`
- Selected Run Detail
  - 新增 summary 级 `Continuation Cursor`
  - 优先读顶层 summary，再回退到 snapshot
- Checkpoint Snapshot detail 卡片
  - 也优先消费后端给出的 `continuation_cursor_summary`

这一步的意义是：

- “下一步怎么续” 开始成为 summary 层的一等语义
- cursor 的短摘要不再散落在前端本地拼装逻辑里
- summary / snapshot / raw payload 的职责继续向 Codex 式编排靠拢：summary 先回答动作入口，snapshot 保留恢复所需结构，raw payload 保留追溯细节

### 11.42 2026-04-27 新进展：orchestration startup policy 已在 sync / stream / recovery 间共享

前面几轮主要都在收 observability 和 summary 层，但真正往 `P0` 靠的时候，一个很具体的 runner 分叉点开始明显：

- sync multi-agent orchestration
- stream multi-agent orchestration
- recovery rebuild

三条路径虽然最终都会：

- resolve targets
- build schedule
- compile orchestration runner policy

但这些动作之前还是各做各的，而且 startup 时刻的 policy projection 也不一致：

- `scheduler_plan_created`
  - 已经会带完整 `runner_policy`
- 但 `runtime_mode_selected`
  - 对 multi-agent orchestration 还只带：
    - `agents`
    - `project_id`
  - 还没有把编排 policy 提前暴露出来

这意味着 run startup 还没有真正成为统一 envelope：

- mode selection 只告诉你“选了 orchestration”
- 却没有同时告诉你“这次 orchestration 准备按什么 runner policy 跑”
- sync / stream / recovery 也仍在各自重建同一套 startup 准备逻辑

这一轮先做一个最小的 `P0.1` 收口：

- `backend/routes/api.py`
  - 新增共享的 orchestration startup 准备逻辑
  - 统一负责：
    - target resolution
    - schedule build
    - orchestration runner policy compile
- sync orchestration
  - 改为消费这份共享 prepared runtime
- stream orchestration
  - 改为消费同一份共享 prepared runtime
- recovery rebuild
  - 也开始复用同一份 orchestration startup 准备逻辑

同时把 policy projection 前移到 startup 事件：

- `runtime_mode_selected`
  - 对 multi-agent orchestration / streaming orchestration
  - 现在也直接输出：
    - `runner_policy`

这样当前 run 在一开始就能回答：

- 这次是不是 orchestration
- 是 linear blocking 还是 blocking-chain-with-sidecars
- 一共多少 step
- sidecar 数量多少
- tool policy pack 是什么

测试同步补上：

- sync multi-agent orchestration
  - 断言 `runtime_mode_selected.payload.runner_policy`
  - 与后续 `scheduler_plan_created.payload.runner_policy`
  - 在 `mode` / `stage_count` / `sidecar_step_count` 上保持一致
- stream multi-agent orchestration
  - 同样补上对应断言

这一步的意义是：

- `runtime_mode_selected` 不再只是“模式标签事件”
- 它开始变成真正的 runner startup envelope 投影
- orchestration 的 startup 准备逻辑第一次在 sync / stream / recovery 三条主链上开始共享
- 这还不是完整统一 runner，但已经是一个真实的 `P0` 内核收口点，而不再只是外围 summary 整理

### 11.43 2026-04-27 新进展：runtime mode selection envelope 已在 sync / stream 入口间共享

11.42 把 orchestration 的 startup 准备逻辑先收了一层，但继续往 `P0` 看，很快就会发现另一块明显重复：

- sync chat entry
- stream chat entry

两边在 mode selection 时都要重复做同样三件事：

- 更新 `task_run.run_kind`
- 视情况更新 `target_agent_name`
- 追加 `runtime_mode_selected` 事件
  - 写入 `project_id`
  - 写入 `runner_policy`
  - 某些模式再补 `agents` / `target_agent_name`

这类重复表面上只是几段 append/update，但实质上它正是 runner startup envelope 的最外层。

如果这一层继续散落在各分支里，后面会持续出现两个问题：

- sync / stream mode-selected 事件口径容易再分叉
- 新增 approval / policy / ownership 投影时，仍要在多处重复接线

这一轮继续做一个小收口：

- `backend/routes/api.py`
  - 提取统一的 mode-selection 写入辅助逻辑
  - 统一负责：
    - `run_kind` 更新
    - `target_agent_name` 更新
    - `runtime_mode_selected` payload 组装

覆盖的入口包括：

- standalone single-agent sync
- standalone single-agent stream
- project single-agent sync
- project single-agent stream
- standalone multi-agent orchestration sync / stream
- project multi-agent orchestration sync / stream

因此 `runtime_mode_selected` 现在开始更接近真正的 startup envelope：

- sync / stream 不再各自手拼一份 mode-selection event
- runner policy projection 也不再散落在八个分支里重复写入

这一步的意义是：

- run startup 的最外层信封开始从“分支代码”抽成“共享语义”
- 统一 runner 外壳虽然还没完全成形，但 mode selection 这一层已经开始具有单一入口味道
- 后续如果要把 approval / sandbox / ownership 等 runtime policy 再继续前移到 startup 阶段，挂载点会更稳定

### 11.44 2026-04-27 新进展：project target-agent resolution 已在 sync / stream 入口间共享

继续沿着 `P0.1` 收 startup envelope，除了 mode selection 之外，还有另一段很容易反复分叉的逻辑：

- project single-agent sync
- project single-agent stream

两条路径都要自己处理：

- `@mention` 指定的 agent 是否已经在项目里
- 如果不在项目里，是否从全局自动分配进项目
- 如果仍然找不到，是否退回默认 agent 或项目第一个 agent

这段逻辑虽然不长，但它本质上决定了 runner startup 的“执行主体是谁”。

如果 sync / stream 各自维护一份，很容易在后续继续出现细小漂移：

- auto-assign 时机不一致
- fallback 顺序不一致
- 后续如果要叠加 ownership / approval / target capability 检查
  - 也会被迫重复接两遍

这一轮把这层也收成共享辅助逻辑：

- `backend/routes/api.py`
  - 新增统一的 project target-agent resolution helper
  - 收口：
    - project-local lookup
    - global auto-assign into project
    - default fallback

这样 project single-agent 的 sync / stream 入口现在开始共享：

- 谁是本轮真正 target agent
- 什么时候把全局 agent 自动挂进项目
- 如果没有显式 mention，如何回落到默认 agent

这一步的意义是：

- runner startup 不再只共享“选了什么模式”
- 也开始共享“选中了谁来执行”
- 统一 runner 外壳又往前收了一层：mode selection 之外，target resolution 也开始脱离分支代码

### 11.45 2026-04-27 新进展：chat turn runtime preparation 已在多条执行路径间共享

`P0.2` 往 turn execution envelope 继续拆时，最容易看到的一块重复不是 LLM loop 本身，而是“进入 LLM loop 之前”的那层准备动作：

- 拿 `llm_client`
- 取 recent messages
- 从 checkpoint 构建 `turn_state`
- 注入 previous work / inter-agent messages
- 取 tool schemas
- 取 tool runtime kwargs

之前这些动作散落在至少四条路径里：

- project single-agent sync
- project single-agent stream
- orchestrated sync turn
- orchestrated stream turn

每条路径都大同小异，但分别自己准备一遍。  
这会带来两个问题：

- turn execution envelope 还停留在“每个入口自己攒材料”
- 后续如果要补统一的 budget / approval / ownership / checkpoint policy
  - 仍会被迫在多处重复接线

这一轮先把这层公共准备抽出来：

- `backend/routes/api.py`
  - 新增共享的 chat turn runtime preparation helper
  - 统一负责：
    - `llm_client`
    - `recent_messages`
    - `turn_state`
    - `tool_schemas`
    - `runtime_kwargs`

目前已接入：

- project single-agent sync
- project single-agent stream
- orchestrated sync turn
- orchestrated stream turn

这一步还没有把所有 turn loop 合并成一个函数，也没有覆盖 pipeline stage engine；但它的价值很明确：

- turn execution 不再只是共享底层 executor
- 连“进入 executor 之前的 runtime 准备层”也开始共享
- 这说明 P0 已经从 startup envelope 继续推进到 turn envelope，而不只是 mode / policy 的表层收口

### 11.46 2026-04-27 新进展：standalone turn runtime preparation 已在 sync / stream 间共享

11.45 把 chat turn runtime preparation 收到了 project single-agent 与 orchestration 路径，但还留着一条最“轻”的分支没并回来：

- standalone assistant sync
- standalone assistant stream

这两条路径虽然不走工具，也没有 project agent 选择，但依然重复了同一类 turn-runtime 准备动作：

- standalone target resolution
- default client fallback
- recent messages
- checkpoint turn state

如果这条分支继续单独维护，后面会出现一个典型问题：

- 统一 runner 收口时，最简单的 standalone 分支反而继续保留自己的准备语义
- 导致 “有工具 / 无工具、project / standalone” 之间的 envelope 继续分层

这一轮把 standalone 也拉回共享准备逻辑：

- `backend/routes/api.py`
  - 新增 standalone turn runtime preparation helper
  - 统一负责：
    - standalone target resolution
    - default client fallback
    - recent messages
    - checkpoint turn state

目前已接入：

- standalone sync
- standalone stream

这一步的意义是：

- turn runtime preparation 的共享范围不再只覆盖“带 project / 带工具”的路径
- 连最轻量的 standalone 分支也开始挂回统一 runtime 语义
- P0.2 的 turn envelope 收口因此更完整了一层：单 Agent project / orchestration / standalone 三类 chat 路径都开始脱离各自手工准备

### 11.47 2026-04-27 新进展：context compaction callback 语义已在 chat 与 pipeline 间共享

继续顺着 turn envelope 往下看，除了 runtime preparation 本身，还有一块很像“边角料”，但实际上很容易让运行时语义再次分叉：

- chat runtime 的 `context_compaction` 事件回调
- pipeline stage runtime 的 `context_compaction` 事件回调

之前两边各自维护了几乎同构的一段逻辑：

- 只在 `compacted=True` 时触发
- 根据 diagnostics 做去重
- 组装 payload
- 生成 summary 文案
- 再落到各自的 event sink

这类代码如果继续平行演化，问题不在“今天会不会坏”，而在：

- future 增加新的 compaction metadata 时
- chat / pipeline 很容易再次出现 payload 或 summary 口径漂移

这一轮把 compaction callback 的共享语义抽出来：

- `backend/services/runtime_event_helpers.py`
  - 新增统一的 context compaction callback helper
  - 收口：
    - 去重签名
    - `compacted` 判定
    - payload 基础字段
    - summary 文案生成

目前已接入：

- chat runtime callback
- pipeline stage runtime callback

两边现在只保留各自的“事件落点适配”：

- chat 走 `append_task_event(...)`
- pipeline 走 `_append_pipeline_task_event(...)`

并补了一层 helper 单测，确保：

- 相同 diagnostics 不会重复发 event
- `compacted=False` 或空 diagnostics 不会误发 event

这一步的意义是：

- turn envelope 不只是开始共享“怎么准备上下文”
- 也开始共享“上下文被压缩时怎么投影成运行时事件”
- 这让 compaction 从 UI 观测项，进一步变成统一 runner 语义的一部分

### 11.48 2026-04-27 新进展：runtime lifecycle event payload building 已开始共享

继续检查 `agent_turn_started` / `agent_turn_completed` 这一层时，当前重复点不在事件落库函数本身：

- `runner_lifecycle.py` 已经统一了事件类型和写入

真正分散的是各调用方手工拼基础 payload：

- `client_turn_id`
- `stage_policy`
- `target_agent_name`
- pipeline id / run id / stage name / display name

这些字段看似只是元数据，但它们是 runner envelope 的重要部分：

- operator 依赖这些字段知道一个 turn 属于哪个入口
- recovery / monitor 依赖这些字段把 turn 与 stage / policy 关联起来
- 后续如果要加入 ownership / approval / sandbox policy，也会继续挂在这里

这一轮把这层基础 payload 组装抽到共享 helper：

- `backend/services/runtime_event_helpers.py`
  - 新增 runtime lifecycle payload helper
  - 统一负责：
    - optional 字段过滤
    - `client_turn_id`
    - `stage_policy.to_payload()`
    - extra metadata merge

目前已接入：

- standalone sync / stream turn start
- project single-agent sync / stream turn start
- orchestrated sync / stream turn start
- pipeline stage turn start / completed

这一步的意义是：

- lifecycle event 不再只共享“写入函数”
- 也开始共享“基础 payload shape”
- P0.2 的 turn envelope 继续往下收：从 context preparation、compaction projection，推进到 lifecycle metadata projection

### 11.49 2026-04-27 新进展：approval replay follow-up resolution shape 已共享

P0.3 开始从 approved blocked-tool replay / follow-up 主链往统一 runner envelope 收口。

这轮先处理最小但高频的分叉点：replay 完成后是否继续执行，以及继续执行结果如何写回 approval resolution payload。

之前 runtime follow-up 与 pipeline follow-up 各自手工拼：

- `followup_attempted=false / followup_status=skipped / followup_reason=...`
- `followup_attempted=true / followup_status=failed / followup_error=...`
- `followup_attempted=true / followup_status=continued / followup_reason=...`
- replay 是否 actionable 的判断：成功且没有再次 blocked

这类 payload 是 monitor、approval queue、operator 审计共同消费的接口。它不应继续由 runtime 与 pipeline 分支各自维护。

本轮新增：

- `backend/services/approval_replay.py`
  - `replay_result_is_actionable(...)`
  - `build_followup_skipped_payload(...)`
  - `build_followup_failed_payload(...)`
  - `build_followup_continued_payload(...)`

并接入：

- chat runtime approved replay follow-up
- pipeline approved replay follow-up

这一步的意义是：

- approval replay 的 follow-up resolution shape 开始统一
- runtime 与 pipeline 仍保留不同的实际恢复动作，但不再各自定义“恢复结果怎么表达”
- 后续可以继续把 approval/sandbox/escalation 主链推进到更完整的 shared runner continuation policy

### 11.50 2026-04-27 新进展：approval replay resolution 与 replay round payload 已共享

继续推进 P0.3 时，approved blocked-tool replay 主链还有第二层重复：

- approval item resolve 时写入的 replay resolution payload
- replay 后写入 `tool_round_recorded` 的 replay round payload

这两类 payload 是同一个恢复动作的两面：

- resolution payload 给 approval queue / monitor / operator 看
- replay round payload 给 task ledger / recovery / continuation cursor 看

之前它们还留在 API route 内部手工拼装。这样会让后续 runtime / pipeline / sandbox approval 扩展时继续把 payload shape 分散到入口层。

本轮把这层语义也并入 `backend/services/approval_replay.py`：

- `build_queue_replay_resolution_payload(...)`
- `build_approval_queue_replay_round_payload(...)`

现在 API approve 路径只负责：

- 找到 queue item
- 执行 replay
- 触发 runtime 或 pipeline follow-up
- resolve approval item

而 replay resolution / replay round 的基础 shape 由共享 service 决定。

这一步的意义是：

- P0.3 的 approval replay envelope 从“后续执行结果”继续扩大到“replay 审计与 ledger 记录”
- API route 的 orchestration 职责更清晰，不再顺手定义 runtime payload 协议
- 后续如果引入 sandbox/escalation replay，只需要复用同一组 replay payload helpers

### 11.51 2026-04-27 新进展：blocked-tool approval queue request 语义已共享

P0.3 继续从 replay 后半段往前推进到 blocked-tool queue item 创建阶段。

之前 `runner_lifecycle.record_tool_round(...)` 在发现 blocked tool 后，直接在函数内部决定：

- queue kind：approval 还是 escalation
- title 文案：approval / escalation 的 operator 标题
- resume_supported 判定
- request_key 去重签名
- request_payload 的基础 shape 与 pipeline cursor 字段

这些字段共同决定一个 blocked tool 能否被 operator 批准、是否支持 replay、后续 replay 如何找到 pipeline/stage cursor。它们不是普通 route/ledger 细节，而是 approval runtime 的协议面。

本轮把这层语义并入 `backend/services/approval_replay.py`：

- `blocked_tool_queue_kind(...)`
- `blocked_tool_queue_title(...)`
- `blocked_tool_resume_supported(...)`
- `build_blocked_tool_request_key(...)`
- `build_blocked_tool_request_payload(...)`

现在 `runner_lifecycle.record_tool_round(...)` 只负责在 tool round 中发现 blocked tool 并创建 queue item；queue item 的 request 协议由共享 helper 生成。

这一步的意义是：

- approval / escalation queue request 的入口语义开始从 ledger 写入函数中拆出
- runtime 与 pipeline 的 blocked-tool cursor 字段保持同一套 request payload shape
- 后续引入 sandbox/escalation 更完整的 continuation policy 时，可以复用同一个 request envelope，而不是继续在 lifecycle 里加分支

### 11.52 2026-04-27 新进展：pipeline gate approval request/resolution 语义已共享

P0.3 继续把 approval 主链中另一类 pending item 收进共享语义：pipeline manual gate。

pipeline gate 与 blocked-tool replay 不同：它不需要重放 tool call，但同样会进入 approval queue，并且同样需要稳定的 request key、request payload、resolution payload：

- request key 用于避免重复 gate approval item
- request payload 携带 pipeline/run/stage cursor 与 stage policy
- resolution payload 记录 gate 被批准/拒绝时的 stage cursor

之前这部分仍在 `pipeline/engine.py` 内部手工定义，导致 approval queue 协议散落在 pipeline engine 与 runtime lifecycle 两侧。

本轮新增共享 helper：

- `build_pipeline_gate_request_key(...)`
- `build_pipeline_gate_request_payload(...)`
- `build_pipeline_gate_resolution_payload(...)`

并接入：

- `_queue_pipeline_gate_approval(...)`
- `_resolve_pipeline_gate_queue_item(...)`

这一步的意义是：

- approval queue 不再只有 blocked-tool replay 语义被共享，manual gate 也开始进入同一组 approval runtime helpers
- pipeline engine 保留 gate 状态推进职责，request/resolution payload shape 由共享 service 决定
- 后续把 approval queue 进一步抽成 runner continuation node 时，blocked tool 与 pipeline gate 已经有可复用的 request envelope 基础

### 11.53 2026-04-27 新进展：approval queue created/resolved event payload 已共享

P0.3 继续把 approval 主链的事件投影层收口。

在 blocked-tool、pipeline gate 的 request / replay payload 逐步共享后，仍有一层重复留在调用方：

- `approval_queue_item_created` event payload
- `approval_queue_item_resolved` event payload
- reject queue item 时的 resolution payload

这些事件是 runtime ledger、monitor、recovery cursor 共同消费的审计面。如果 created/resolved payload 继续由 route、pipeline engine、runner lifecycle 分别拼装，后续 approval/sandbox/escalation 字段仍会很容易漂移。

本轮新增共享 helper：

- `build_approval_queue_item_created_event_payload(...)`
- `build_approval_queue_item_resolved_event_payload(...)`
- `build_queue_rejection_resolution_payload(...)`

并接入：

- runtime blocked-tool queue item created event
- pipeline gate queue item created event
- runtime approve/reject resolved event
- pipeline approve/reject resolved event

这一步的意义是：

- approval queue 的 request、replay、follow-up、created/resolved event 四个面都开始有共享 payload helper
- route / engine / lifecycle 不再各自定义 approval ledger payload shape
- 这为下一步把 approval queue 抽象成更完整的 runner continuation node 减少了接口漂移风险

### 11.54 2026-04-27 新进展：approved tool replay follow-up context 已共享

P0.3 继续收口 approved blocked-tool replay 后的 continuation prompt。

之前 replay 成功后，runtime follow-up 与 pipeline follow-up 都依赖 API route 内的 `_build_tool_replay_followup_context(...)` 拼出一段额外上下文：

- replay 的 tool name
- replay status
- replay result preview
- 明确要求继续执行，不要无条件重跑同一个 tool call

这段文本虽然看起来只是 prompt，但实际上是 approved replay continuation 的核心协议：它决定 agent/pipeline 恢复时如何理解已经完成的工具结果。

本轮把它并入共享 approval runtime helper：

- `build_tool_replay_followup_context(...)`

并让：

- chat runtime approved replay follow-up
- pipeline approved replay follow-up

都复用同一段 context 构造逻辑。

这一步的意义是：

- approved replay 后的 continuation context 不再藏在 API route 内
- runtime 与 pipeline 恢复时收到同一套“已 replay tool result，继续但不要重复执行”的指令语义
- approval replay 主链现在从 request、resolution、ledger event 到 continuation prompt 都更接近统一 runner envelope

### 11.55 2026-04-27 新进展：approval replay request parsing 与 cursor 解析已共享

P0.3 继续把 approved replay 的入口解析层收口。

在 request/replay/follow-up/event payload helper 已共享后，runtime 与 pipeline replay 仍各自维护一些低层解析逻辑：

- 从 `request_payload_json` 解析 approval queue request payload
- 解析 replay tool name
- 解析 replay arguments text
- 生成 replay tool call id
- 解析 pipeline run/stage cursor

这些逻辑本身不复杂，但它们决定 approved replay 后能否找到正确工具、正确 pipeline run/stage，以及 ledger 中 replay tool result 是否能稳定关联 queue item。

本轮新增共享 helper：

- `load_approval_queue_request_payload(...)`
- `resolve_replay_tool_name(...)`
- `resolve_replay_arguments_text(...)`
- `replay_tool_call_id(...)`
- `resolve_pipeline_replay_run_id(...)`
- `resolve_pipeline_replay_stage_id(...)`

并接入：

- API approve/reject queue item 的 request payload 读取
- runtime blocked-tool replay
- pipeline blocked-tool replay

这一步的意义是：

- approval replay 的 request 解析与 cursor 解析不再分散在 route 与 pipeline engine
- runtime/pipeline replay 对 queue item id、tool name、arguments、pipeline cursor 的解释口径一致
- 后续把 replay 执行器继续抽成 runner continuation action 时，可以直接复用这些 resolver，而不是迁移分支内解析代码

### 11.56 2026-04-27 新进展：replay arguments JSON 解析已共享

P0.3 继续收口 approved replay 执行前的参数解析。

runtime replay 与 pipeline replay 都需要把 approval queue 中保存的 `arguments` 文本重新解析成 JSON object，且都必须拒绝：

- 非法 JSON
- JSON array / string / number 等非 object 参数

之前两边各自维护 `json.loads(...) + isinstance(dict)` 的同构代码。这个判断虽然小，但它是 replay 执行前最后一道协议校验。如果后续 sandbox/escalation replay 接入时继续复制，很容易出现某条路径接受了非 object 参数。

本轮新增：

- `parse_replay_arguments(...)`

并接入：

- runtime blocked-tool replay
- pipeline blocked-tool replay

这一步的意义是：

- approved replay 的 request payload 解析、cursor 解析、arguments JSON object 校验都已经在共享 helper 内收口
- runtime 与 pipeline 在真正执行 tool 前的参数校验口径一致
- 后续抽 runner continuation action 时，replay executor 可以直接复用这组解析/校验 helper

### 11.57 2026-04-27 新进展：replay tool result record 构造已共享

P0.3 继续收口 approved replay 执行结果写回 ledger 前的 record 构造。

runtime replay 与 pipeline replay 都会把 replay 执行结果包装成 `ToolResultRecord`，并且需要稳定使用：

- `queue-replay-<queue_item_id>` 作为 tool call id
- 原始 arguments text
- replay result text
- success/status/block 分类逻辑

之前两边各自调用 `build_tool_result_record(...)` 并重复拼 `tool_call_id`。这会让 replay 结果与 queue item 的关联规则散落在不同执行器里。

本轮新增：

- `build_replay_tool_result_record(...)`

并接入：

- runtime blocked-tool replay 的成功/失败返回
- pipeline blocked-tool replay 的成功/失败返回

这一步的意义是：

- approved replay 的 request 解析、参数解析、tool result record 构造都已经进入共享 helper
- runtime 与 pipeline replay 写回 ledger 的 tool call id 与 result classification 口径一致
- 后续把 replay 执行器抽成 runner continuation action 时，结果落 ledger 的协议已经有可复用入口

### 11.58 2026-04-27 新进展：approval queue pipeline cursor 与 resume strategy 已共享

P0.3 继续把 approval replay 的分流与恢复策略判断收口。

approved replay 现在有两类后续路径：

- 普通 chat turn：replay tool 后继续原 agent turn
- pipeline stage：replay tool 后恢复 pipeline stage

之前这个判断散在不同位置：

- API replay 分流用 `item.pipeline_run_id` 或 request payload 判断
- runtime follow-up 需要跳过 pipeline queue item
- approve 路径需要决定走 runtime follow-up 还是 pipeline follow-up
- run ledger continuation cursor 需要把 pending approval 映射成 resume strategy

这些判断必须一致，否则会出现 queue item 被 API 当成 pipeline replay，但 checkpoint cursor 却显示普通 turn resume 的漂移。

本轮新增共享 helper：

- `approval_queue_item_has_pipeline_cursor(...)`
- `approval_queue_resume_strategy(...)`

并接入：

- API blocked-tool replay 分流
- runtime approved replay follow-up guard
- approve queue item 的 runtime/pipeline follow-up 分流
- run ledger continuation cursor 的 `resume_strategy`
- run ledger continuation cursor 的 pipeline run/stage id 解析

这一步的意义是：

- approval queue 的 pipeline cursor 判断不再由 route 与 ledger 各自实现
- monitor/recovery 看到的 pending approval continuation strategy 与 API 实际 replay/follow-up 分流保持一致
- 后续把 approval queue 抽成 runner continuation node 时，resume strategy 已经有共享入口

### 11.59 2026-04-27 新进展：pending approval continuation cursor 已共享

P0.3 继续把 approval queue 与 checkpoint/recovery 的连接点收口。

run ledger 在构造 `checkpoint_snapshot.continuation_cursor` 时，需要把 pending approval queue item 映射成：

- `next_action=await_approval`
- resume strategy
- source blocked event
- turn/tool/blocked_kind
- queue item id
- pipeline run/stage cursor

这份 cursor 是 monitor 与 recovery 理解“当前 run 卡在哪里”的核心结构。之前它直接在 `run_ledger.py` 内联拼装，虽然已经复用了部分 request/cursor resolver，但 pending approval cursor 的整体 shape 仍由 ledger 私有定义。

本轮新增：

- `build_pending_approval_continuation_cursor(...)`

并让 run ledger 在发现 pending tool approval queue item 时直接调用该 helper。

这一步的意义是：

- pending approval 的 continuation cursor shape 从 run ledger 私有逻辑中拆出
- approval queue runtime helper 现在同时覆盖 request、replay、event、follow-up context、pending cursor
- 后续把 approval queue 抽成 runner continuation node 时，checkpoint cursor 已有共享协议入口

### 11.60 2026-04-27 新进展：approved replay follow-up event payload 已共享，P0 baseline 收口完成

P0 最后一轮收口把 approved replay follow-up 的事件 payload 也从 API route 中拆出。

之前 runtime follow-up 与 pipeline follow-up 已经共享了：

- request parsing
- replay arguments parsing
- replay result record
- replay resolution payload
- pending approval cursor
- continuation context

但 `approval_queue_item_followup_triggered` / `approval_queue_item_followup_failed` 的 event payload 仍分别在 runtime 与 pipeline 分支内手工拼：

- runtime 分支记录 `queue_item_id / tool / tool_call / message_id`
- pipeline 分支记录 `queue_item_id / pipeline/run/stage / tool / tool_call`
- failed 分支记录 error 与可选 pipeline cursor

本轮新增：

- `build_followup_triggered_event_payload(...)`
- `build_followup_failed_event_payload(...)`

并接入：

- chat runtime approved replay follow-up triggered/failed event
- pipeline approved replay follow-up triggered/failed event

到这里，P0 baseline 视为完成，完成范围不是“所有历史执行分支已经合并成一个大函数”，而是更实际的 Codex-style runtime envelope baseline：

- startup envelope：orchestration policy / mode selection / target agent resolution 已共享
- turn envelope：chat / standalone / orchestration 的 runtime preparation、compaction callback、lifecycle payload 已共享
- approval/replay envelope：blocked-tool request、pipeline gate request、queue event、replay parsing、argument validation、result record、resolution payload、follow-up context、pending cursor、follow-up event payload 已共享
- checkpoint/recovery envelope：pending approval continuation cursor 与 run recovery projection 共享同一套 approval cursor helper

P0 完成后的剩余差距进入 P1，不再属于 P0 baseline：

- 真正把 chat runtime 与 pipeline stage runtime 合并成单一 executor loop
- subagent lifecycle 的完整 spawn / wait / close / cancellation 状态机
- 更完整的 sandbox escalation resume token / lease 机制
- durable inbox/outbox 级别的跨进程 message replay

### 11.61 2026-04-27 新进展：P1 启动，durable pipeline inbox/outbox service 已抽出

P0 baseline 完成后，P1 第一刀先从 durable inbox/outbox 做收口，而不是直接合并 executor loop。

原因是：pipeline 的跨 agent 消息已经有数据库表承载，但核心投递/消费语义仍散在 `pipeline/engine.py`：

- 创建 `PipelineMessageDelivery`
- 按 agent claim pending delivery
- 区分普通 agent message 与 `HUMAN_INSTRUCT`
- 把 legacy `HUMAN_INSTRUCT` 回填成 consumed delivery
- 把 `PipelineMessage` 序列化成 runtime inbox item

这些是 Codex-style durable message replay 的基础能力，不应该继续由 pipeline engine 私有维护。

本轮新增：

- `backend/services/pipeline_inbox.py`
  - `enqueue_message_delivery(...)`
  - `pop_messages_for_agent(...)`
  - `pop_instruction_texts_for_agent(...)`
  - `consume_legacy_instruction_texts_for_agent(...)`
  - `serialize_pipeline_message(...)`

并接入：

- tool `send_message` 的 durable delivery 创建
- pipeline `instruct(...)` 的 BOSS instruction delivery 创建
- rollback message 的 durable delivery 创建
- stage runtime 读取 inter-agent inbox
- stage runtime 读取 BOSS instruction inbox
- legacy instruction delivery backfill

这一步的意义是：

- pipeline engine 不再直接拥有 durable inbox/outbox 的协议细节
- durable inbox 从“pipeline 内部实现细节”开始变成共享 runtime service
- P1 后续要做跨进程 message replay / scheduler recovery 时，可以以 `pipeline_inbox` 为入口继续扩展 lease、claim、retry 与 replay cursor

### 11.62 2026-04-27 新进展：pipeline inbox 开始具备 lease / retry / dead-letter 语义

P1 durable inbox 的第二刀补上消息驱动 runtime 最小需要的跨进程执行语义。

上一轮只是把 delivery 创建和消费从 `pipeline/engine.py` 抽到了 `pipeline_inbox` service，但 delivery 状态仍基本等同于：

- `pending`
- `consumed`

这不足以支撑 Codex-style runtime 的 message replay。真实执行中 worker 可能在 claim 消息后崩溃，或者处理失败后需要重试 / dead-letter。如果没有 lease，消息要么被重复处理，要么永久卡在不可见状态。

本轮新增 durable delivery 字段：

- `lease_owner`
- `leased_at`
- `lease_expires_at`
- `attempt_count`
- `last_error`
- `failed_at`

并在 `backend/services/pipeline_inbox.py` 中补上：

- `claim_messages_for_agent(...)`
- `mark_delivery_consumed(...)`
- `mark_delivery_failed(...)`

新的状态语义是：

- `pending -> inflight`：worker claim delivery，并写入 lease owner / expiry / attempt count
- `inflight -> consumed`：worker 成功处理后 ack
- `inflight -> pending`：worker 处理失败但允许 retry
- `inflight -> dead_letter`：worker 处理失败且不再 retry
- expired `inflight -> inflight`：其他 worker 可重新 claim 过期 lease

同时保留 `pop_messages_for_agent(...)` / `pop_instruction_texts_for_agent(...)` 的兼容行为，让现有 pipeline stage loop 仍可一次性 claim + consume。

这一步仍不是完整的 runtime scheduler，但 durable inbox 已经从“有表可存”推进到“有最小 replay 协议”：

- delivery claim 有 owner
- inflight 有 lease expiry
- crash 后可 reclaim
- failure 可选择 retry 或 dead-letter
- attempt count 可用于后续 backoff / max retry policy

后续 P1 可以继续沿这个 service 增加 scheduler cursor、monitor projection，或者把 stage loop 改成显式 claim / execute / ack 的 executor loop。

### 11.63 2026-04-27 新进展：pipeline inbox 状态进入 TaskRun checkpoint projection

P1 durable inbox 已具备 lease / retry / dead-letter 语义后，下一步把这份状态放进 runtime checkpoint，而不是只停留在数据库表里。

本轮新增：

- `TaskRun.pipeline_runs` / `PipelineRun.task_run` 双向 relationship
- `summarize_pipeline_run_inbox(...)`
- `checkpoint_snapshot.pipeline_inbox`
- `checkpoint_snapshot.pipeline_inbox_summary`
- task-run summary 顶层 `pipeline_inbox_summary`

projection 内容包括：

- pipeline run id 与 status
- delivery 总数
- pending / inflight / consumed / dead-letter count
- 按 agent 聚合的 delivery status counts
- oldest pending timestamp
- next inflight lease expiry timestamp

这一步的意义是：

- durable inbox 不再只是 pipeline 内部执行细节
- monitor / recovery 可以直接看到消息是否卡在 pending、inflight lease、dead-letter
- 后续恢复 executor 可以基于 checkpoint projection 决定是恢复 scheduler、重新 claim delivery，还是暴露人工处理 dead-letter

这仍不是完整单一 executor loop，但它把 P1 的 inbox replay 状态接入了 Catown 当前最核心的 runtime ledger / checkpoint 面。

### 11.64 2026-04-27 新进展：scheduler events 开始投影为 subagent lifecycle

P1 的第二个核心差距是 subagent lifecycle。当前 Catown 的多 agent 编排已经有 scheduler plan、dispatch、resume、complete 事件，但它们仍是“调度事件”，不是 runtime-managed subagent state。

本轮先不重写执行器，而是把现有 scheduler 事件投影成 Codex-style lifecycle view：

- `scheduler_plan_created` -> subagent `spawned`
- `scheduler_step_dispatched` -> subagent `running`
- `scheduler_step_resumed` -> subagent `spawned`
- `scheduler_step_completed` -> subagent `completed`

新增：

- `backend/services/subagent_lifecycle.py`
  - `build_subagent_lifecycle_from_events(...)`
  - `summarize_subagent_lifecycle(...)`
- `checkpoint_snapshot.subagent_lifecycle`
- `checkpoint_snapshot.subagent_lifecycle_summary`
- task-run summary 顶层 `subagent_lifecycle_summary`

projection 保留：

- step id
- agent name / type
- dispatch kind
- wait-for / attached-to relationship
- status
- started / completed timestamp
- latest lifecycle event

这一步的边界很明确：

- 已经形成可读的 subagent state projection
- 还没有把 spawn / wait / close / cancel 做成 executor primitive
- failed / cancelled 状态目前只是 lifecycle model 支持，执行路径还没有统一写入这些终态

它的价值是先建立统一状态面。后续要把 orchestration loop 改成真正的 parent executor + child subagent lifecycle 时，不需要再从散落事件里临时推断状态。

### 11.65 2026-04-27 新进展：subagent lifecycle 开始记录 failed terminal state

上一轮只是把已有 scheduler event 投影成 subagent lifecycle。问题是：异常路径仍可能直接冒泡，导致 TaskRun 停在 running，subagent 也没有明确终态。

本轮补上最小失败终态：

- `scheduler_step_failed` -> subagent `failed`
- `scheduler_step_cancelled` -> subagent `cancelled`（projection 支持，执行路径后续接入）

并接入：

- 非流式 multi-agent orchestration：单个 step 执行异常时写入 `scheduler_step_failed`，然后将 task run 标记为 failed
- 流式 multi-agent orchestration：单个 step 执行异常时写入 `scheduler_step_failed`，将 task run 标记为 failed，并通过 SSE 返回 error / done

这一步让 subagent lifecycle 不再只有 happy path：

- spawn / running / completed 已可由正常 scheduler events 推导
- failed 已由执行异常路径写入
- cancelled 已进入 lifecycle projection model，等待后续 cancel primitive 接入

它仍不是完整 Codex subagent runtime，但已经补齐了最关键的 terminal-state 可观测性，避免异常时 parent run 与 child step 状态脱节。

### 11.66 2026-04-27 新进展：approval / escalation queue 开始具备 resume token 与 resolution lease

P1 的第三个核心差距是 sandbox escalation resume token / lease。Catown 已经能把 blocked tool 放进 approval / escalation queue，但 queue item 本身缺少 durable resume token，也缺少 resolution 侧的 lease。

这会带来两个问题：

- recovery / follow-up 只能依赖 queue item id 与 payload 推断恢复点，缺少稳定 resume token
- 多个处理者同时 approve / reject 同一个 pending item 时，没有显式 resolution owner / lease

本轮新增字段：

- `approval_queue_items.resume_token`
- `approval_queue_items.resolution_owner`
- `approval_queue_items.resolution_lease_expires_at`

并新增 service 能力：

- `ensure_approval_queue_resume_token(...)`
- `claim_approval_queue_resolution_lease(...)`

创建新的 approval / escalation item 时会自动生成 `resume_token`。数据库初始化会给历史 item 回填 token。

同时：

- approval queue serialization 暴露 resume token / lease state
- pending approval continuation cursor 携带 resume token / resolution lease state
- resolve item 后会清理 resolution lease

这一步的意义是把 approval / sandbox escalation 从“一个 pending 队列项”推进成更接近 Codex 风格的 resumable action token：

- 有稳定 token 表示恢复对象
- 有 lease owner 表示当前处理者
- 有 expiry 允许 lease 丢失后重新处理

后续还需要把 approve / reject API 强制接入 lease 校验，以及把 sandbox escalation policy 与 executor action resume 合成更完整的 runtime primitive。

### 11.67 2026-04-27 新进展：approve / reject API 接入 approval queue resolution lease

上一轮已经为 approval / escalation queue item 增加了 resume token 与 resolution lease 字段，但 API 还没有强制使用 lease。

本轮把 approve / reject 入口接入 lease claim：

- approve 前 claim `resolution_owner = approval-api:<resolved_by>:<item_id>`
- reject 前 claim 同样的 resolution lease
- 如果 item 已被其他 resolver 持有且 lease 未过期，返回 409
- resolve 完成后清理 lease owner / expiry

这一步让 approval / escalation queue 的处理路径具备最小互斥语义：

- pending item 不是任意多个处理者都能同时解决
- resolver identity 可观测
- lease 过期后可重新 claim
- continuation cursor / monitor serialization 可以看到 token 与 lease 状态

这仍不是完整 sandbox action runtime，但 approval / escalation 的 durable resume token + resolution lease 已经落到实际 API 执行路径。

### 11.68 2026-04-27 新进展：TaskRun cancellation primitive 接入 subagent lifecycle

P1 subagent lifecycle 已有 cancelled projection，但之前只是状态模型支持，并没有实际 runtime 入口会写入 cancellation terminal state。

本轮新增最小 cancellation primitive：

- `POST /api/task-runs/{task_run_id}/cancel`
- `cancellable_subagents_from_lifecycle(...)`

取消流程：

1. 只允许取消 `running` TaskRun
2. 从当前 checkpoint 的 `subagent_lifecycle` 找出所有非 terminal subagents
3. 对每个 active subagent 写入 `scheduler_step_cancelled`
4. 写入 `task_run_cancelled` ledger event
5. 将 TaskRun 状态更新为 `cancelled`

这一步的意义是：

- subagent lifecycle 的 `cancelled` 终态不再只是 projection model
- parent TaskRun 可以主动 terminalize child step state
- monitor / checkpoint 能看到取消前 snapshot 与取消后的 child terminal state
- API 层开始具备 Codex 风格 `close/cancel` primitive 的最小雏形

边界仍然清楚：

- 这不是正在运行中 agent coroutine 的抢占式 cancellation
- 还没有 worker/process 级 interrupt
- 但 ledger、checkpoint、subagent terminal state 已经一致，后续可以把真实 executor interrupt 接到同一个 cancellation event model 上

### 11.69 2026-04-27 新进展：orchestration scheduler event helper 已抽出

P1 继续向单一 executor loop 靠拢。本轮没有直接重写 executor，而是先把 orchestration step lifecycle 事件从 API route 中抽成共享 service。

之前 sync orchestration、stream orchestration、recovery orchestration 与 cancel API 都在各自路径中手工拼：

- `scheduler_step_dispatched`
- `scheduler_step_completed`
- `scheduler_step_resumed`
- `scheduler_step_failed`
- `scheduler_step_cancelled`
- `runtime`
- `step_state`
- `stage_policy`
- released / resumed / recovered metadata

这些字段是 subagent lifecycle、monitor、recovery cursor 共同依赖的协议面。如果继续散落在 route 里，后续真正抽 executor loop 时会继续重复。

本轮新增：

- `backend/services/orchestration_events.py`
  - `scheduler_event_payload(...)`
  - `scheduler_plan_payload(...)`
  - `record_scheduler_step_dispatched(...)`
  - `record_scheduler_step_completed(...)`
  - `record_scheduler_step_resumed(...)`
  - `record_scheduler_step_failed(...)`
  - `record_scheduler_step_cancelled(...)`

并接入：

- 非流式 multi-agent orchestration
- 流式 multi-agent orchestration
- interrupted orchestration recovery
- task-run cancellation API

这一步的意义是：

- scheduler step lifecycle event shape 开始由 service 管理
- subagent lifecycle projection 的上游事件来源更稳定
- API route 不再直接拥有 step lifecycle payload 细节
- 后续抽 parent executor / child subagent primitive 时，可以复用这组 event helper，而不是重新定义事件协议

边界：

- 这仍不是完整单一 executor loop
- 但它把 sync / stream / recovery / cancel 四条路径中最关键的 step lifecycle ledger 写入统一到了一个 service

### 11.70 2026-04-27 新进展：orchestration handoff helper 已抽出

P1 继续把 orchestration executor 周边协议从 API route 中拆出。本轮处理的是 handoff / previous-work 这条重复逻辑。

之前 sync orchestration、stream orchestration、recovery orchestration 都各自维护：

- completed turns -> previous work 文本
- agent output -> handoff payload
- pending handoff queue append
- `handoff_created` ledger event
- recovered handoff metadata

这些行为是 subagent 间消息传递的执行协议，不应该长期留在 route 内部。

本轮新增：

- `backend/services/orchestration_handoffs.py`
  - `compact_runtime_text(...)`
  - `build_orchestration_previous_work(...)`
  - `build_orchestration_handoff(...)`
  - `record_orchestration_handoffs(...)`

并接入：

- 非流式 multi-agent orchestration
- 流式 multi-agent orchestration
- interrupted orchestration recovery

这一步的意义是：

- handoff payload 与 `handoff_created` event shape 开始由 service 管理
- pending handoff queue append 与 ledger record 变成一个原子 helper
- sync / stream / recovery 三条路径不再重复拼 handoff event
- 后续如果把 handoff 迁到 durable inbox 或子 agent message primitive，可以从这个 service 继续演进

边界：

- handoff 仍是 orchestration loop 的内存 pending map，不是完全 durable queue
- 但协议入口已收口，下一步可以把 handoff helper 接到 durable inbox/outbox 或统一 executor message bus

### 11.71 2026-04-27 新进展：orchestration finalizer helper 已抽出

P1 继续把 orchestration executor 的末端行为从 API route 中拆出。本轮处理 TaskRun final summary / completion 逻辑。

之前 sync orchestration、stream orchestration、recovery orchestration 各自决定：

- 优先使用 last blocking result
- 否则使用最后一个 result / completed turn
- 否则使用 fallback 文本
- 然后调用 `complete_task_run(...)`

这套选择规则影响 task-run summary、monitor 展示、recovery result detail。它属于 executor finalization 协议，不应该散在三条路径里。

本轮新增：

- `backend/services/orchestration_finalizer.py`
  - `summarize_orchestration_result(...)`
  - `finalize_orchestration_task_run(...)`

并接入：

- 非流式 multi-agent orchestration finalization
- 流式 multi-agent orchestration finalization
- interrupted orchestration recovery finalization

这一步的意义是：

- final summary 选择规则统一
- TaskRun complete 行为开始由 orchestration service 管理
- recovery completion event 的 detail 与 TaskRun summary 复用同一套 summary builder
- route 层继续变薄，为后续抽单一 parent executor loop 做准备

边界：

- failure finalization 仍有多处按具体错误路径处理
- 但正常完成路径已完成 sync / stream / recovery 的共享 finalizer 收口

### 11.72 2026-04-27 新进展：orchestration step output state helper 已抽出

P1 继续拆 orchestration loop 内部状态。本轮处理每个 step 完成后的输出状态更新。

之前 sync orchestration、stream orchestration、recovery orchestration 分别维护：

- `completed_turns.append(...)`
- `results.append(...)`
- blocking step 更新 `last_blocking_result`

这些状态是 finalizer、previous-work、handoff、recovery rebuild 的共同输入。它们如果继续在三条路径里手工维护，后续抽统一 executor step primitive 会继续有分叉。

本轮新增：

- `backend/services/orchestration_step_state.py`
  - `OrchestrationStepOutputState`
  - `record_orchestration_step_output(...)`

并接入：

- 非流式 multi-agent orchestration
- 流式 multi-agent orchestration
- interrupted orchestration recovery

兼容边界：

- sync orchestration 仍维护 `results`，用于既有响应统计与 final summary fallback
- stream / recovery 只写 completed turns，不额外写 results
- recovery rebuild 仍会从历史 agent_turn_completed event 还原初始 state

这一步的意义是：

- step output state 更新规则统一
- finalizer / handoff / previous-work 的输入状态更稳定
- route loop 中又少一块手工状态拼装
- 后续可继续把 “run one step -> record output -> complete/resume/handoff” 合成单一 executor step helper

### 11.73 2026-04-27 新进展：orchestration scheduler step completion helper 已抽出

P1 继续把 orchestration loop 的 step primitive 往 service 层收口。本轮处理 step 完成后的组合动作。

之前 sync orchestration、stream orchestration、recovery orchestration 都各自执行：

1. `queue.mark_completed(step.step_id)`
2. 写入 `scheduler_step_completed`
3. 遍历 ready steps 写入 `scheduler_step_resumed`
4. 如果当前 step 有输出，则写入 handoff 并更新 pending handoff map
5. recovery path 额外携带 `recovered: true`

这组动作本质上是一个 executor step completion primitive，不应该散在三条 loop 里。

本轮新增：

- `backend/services/orchestration_step_completion.py`
  - `complete_orchestration_scheduler_step(...)`

并接入：

- 非流式 multi-agent orchestration
- 流式 multi-agent orchestration
- interrupted orchestration recovery

这一步的意义是：

- step completion 的状态转换、resume、handoff 变成单一 helper
- sync / stream / recovery 的 executor loop 进一步同构
- recovered metadata 与普通路径差异被参数化，而不是复制整段逻辑
- 后续可继续把 “dispatch -> execute agent turn -> record output -> complete step” 合成更完整的 executor step runner

边界：

- agent turn execution 本身仍分别走 `_run_single_agent_turn` 与 streaming `_iter_agent_turn_events`
- 但 step 完成后的 scheduler state transition 已经完成共享收口

### 11.74 2026-04-27 新进展：nonstream orchestration step runner 已抽出

P1 开始从“拆小 helper”进入真正的 step runner 收口。本轮先覆盖非流式 orchestration 与 recovery，两者都可以用 async function 返回完整 step result，不受 SSE yield 约束。

之前非流式 orchestration 与 interrupted recovery 各自执行：

1. 记录 `scheduler_step_dispatched`
2. 调 `_run_single_agent_turn(...)`
3. publish saved chat message
4. 记录 step output state
5. complete scheduler step / resume next steps / handoff
6. 异常时记录 `scheduler_step_failed`

本轮新增：

- `backend/services/orchestration_step_runner.py`
  - `OrchestrationStepRunResult`
  - `run_nonstream_orchestration_step(...)`

并接入：

- 非流式 multi-agent orchestration
- interrupted orchestration recovery

runner 参数化差异：

- sync path：`include_result=True`，保留既有 `results` 统计
- recovery path：`include_result=False`，只更新 completed turns
- recovery path：dispatch payload 额外携带 checkpoint snapshot 与 recovery continuation state
- recovery path：completion/resume/handoff 携带 `recovered: true`

这一步的意义是：

- dispatch -> execute -> publish -> output state -> complete step 的主链第一次形成共享 runner
- 非流式与 recovery 的 executor loop 不再各自手写 step 主流程
- route 层只负责准备 step context 与错误终止 TaskRun
- 下一步可以把 streaming path 的 event-yield 特殊性包成 stream runner，或者继续把 failure finalization 收口

边界：

- streaming orchestration 仍保留独立 loop，因为它需要逐 chunk yield SSE
- `_run_single_agent_turn(...)` 本身仍是 route-local callable，通过参数传入 runner，后续可以继续下沉

### 11.75 2026-04-27 新进展：stream orchestration step runner helper 已抽出

P1 继续补齐 streaming orchestration 的 executor 收口。上一轮 nonstream / recovery 已经共享 `run_nonstream_orchestration_step(...)`，但 streaming path 因为需要逐 chunk yield SSE，不能直接复用同一个 await-return runner。

本轮采用更实际的做法：先把 streaming step 的稳定协议拆成 helper，route 保留 async generator 的 yield 控制。

新增：

- `backend/services/orchestration_stream_runner.py`
  - `start_stream_orchestration_step(...)`
  - `iter_stream_orchestration_agent_events(...)`
  - `handle_stream_orchestration_turn_complete(...)`
  - `fail_stream_orchestration_step(...)`
  - `complete_stream_orchestration_step(...)`

并接入 streaming orchestration route。

收口内容：

- `scheduler_step_dispatched` 与 `collab_step` payload
- `_iter_agent_turn_events(...)` 的 shared 参数组装
- `turn_complete` 后保存 message、publish message、record agent turn completed、memory extraction、output state update
- `scheduler_step_failed`
- scheduler step completion 与 `collab_step_done` payload

这一步的意义是：

- streaming path 的 step lifecycle 协议也开始由 service 管理
- sync / recovery / stream 三条路径现在都围绕 step runner/helper 组织
- route 中剩下的主要职责是 SSE 事件转发与错误响应 yield
- 继续向 Codex 风格“一个 executor 管生命周期，transport 只负责呈现”靠拢

边界：

- streaming runner 还不是完整 async generator runner，route 仍持有 yield 控制
- 但 dispatch、turn_complete、failure、completion 四个稳定节点已经下沉

### 11.76 2026-04-27 新进展：orchestration failure finalizer 已收口

P1 在完成正常 completion finalizer 后，继续补齐失败终结路径。此前 sync orchestration、streaming orchestration 与 interrupted recovery 的失败分支仍在 route 层直接调用 `complete_task_run(... status="failed" ...)`，导致失败事件、TaskRun 终态 summary 与 recovery failure 语义分散。

本轮新增/扩展：

- `backend/services/orchestration_finalizer.py`
  - `fail_orchestration_task_run(...)`

并接入：

- 非流式 orchestration：无有效 agent、runtime 未准备、step exception
- streaming orchestration：无有效 agent、runtime 未准备、step exception
- interrupted recovery：chatroom 缺失、无有效 agent、无 runnable plan、无 runnable steps、incomplete、exception

这一步的意义是：

- orchestration 成功与失败终结都进入同一个 service 边界
- route 层不再手写主要 orchestration failure terminalization
- failure event 与 TaskRun failed summary 的关系更稳定，便于后续接 executor-level typed failure event
- recovery 失败路径继续保留 `task_run_recovery_failed` 事件类型，但终态写入统一走 finalizer helper

边界：

- route 中仍有 standalone / single-agent streaming 的失败终结逻辑，它们不属于本轮 orchestration 收口范围
- step-level failure event 仍由 step runner / stream runner 负责，task-run terminal failure 由 finalizer 负责
- 下一步应继续下沉 `_run_single_agent_turn(...)`，减少 route 对 agent turn runtime 的直接持有

### 11.77 2026-04-27 新进展：nonstream orchestration agent turn runner 已下沉

P1 继续削薄 `backend/routes/api.py`。此前 `_run_single_agent_turn(...)` 是 orchestration step runner 的核心执行回调，但仍完整定义在 route 层，里面直接负责 collaboration context、turn runtime 准备、prompt assembly、tool loop、message 保存、agent lifecycle event 与 memory extraction。

本轮新增：

- `backend/services/orchestration_agent_turn.py`
  - `OrchestrationAgentTurnDeps`
  - `run_orchestration_agent_turn(...)`

并接入：

- 非流式 multi-agent orchestration
- interrupted orchestration recovery

设计取舍：

- turn runner 的主执行逻辑下沉到 service
- route 只保留一个小型 dependency adapter：`_build_orchestration_agent_turn_executor()`
- prompt builder、runtime preparation、chatroom message manager 等仍由 route 注入，避免一次性迁移过大造成行为漂移

这一步的意义是：

- `run_nonstream_orchestration_step(...)` 不再依赖 route-local `_run_single_agent_turn(...)`
- orchestration step runner 调用的是 service-level executor primitive
- route 层少持有一段完整 agent turn runtime，继续靠近 Codex 风格“route 负责适配，executor 负责运行”

边界：

- streaming `_iter_agent_turn_events(...)` 仍在 route 层，下一步应按同样模式下沉为 stream agent event runner
- `_prepare_chat_turn_runtime(...)` 与 `_assemble_chat_messages(...)` 仍是 route-local dependency，后续可继续拆到 chat runtime service

### 11.78 2026-04-27 新进展：stream orchestration agent turn event runner 已下沉

P1 继续处理 streaming orchestration 与 Codex 风格 executor 的差距。上一轮已将 nonstream `_run_single_agent_turn(...)` 下沉为 service，本轮对 streaming `_iter_agent_turn_events(...)` 做同样收口。

本轮扩展：

- `backend/services/orchestration_agent_turn.py`
  - `StreamOrchestrationAgentTurnDeps`
  - `iter_stream_orchestration_agent_turn_events(...)`

并接入：

- streaming multi-agent orchestration step execution
- `orchestration_stream_runner.iter_stream_orchestration_agent_events(...)` 的 route callback 参数

设计取舍：

- streaming agent turn 的主逻辑下沉到 service
- route 只保留 `_build_stream_orchestration_agent_turn_iterator()` dependency adapter
- SSE yield 仍由 route 控制，避免本轮同时改变 transport 行为

这一步的意义是：

- route 不再直接持有 streaming agent turn runtime loop
- stream turn lifecycle event、tool round event、runtime card 构建入口统一进入 service-level runner
- streaming path 与 nonstream path 现在都通过 orchestration agent turn service 进入底层 LLM/tool loop

边界：

- streaming orchestration route 仍负责 `async for` 事件分发与 SSE 序列化
- 下一步应把 streaming orchestration route 改成消费 service-level typed runtime events，使 transport 只负责 render

### 11.79 2026-04-27 新进展：stream orchestration runtime event runner 已抽出

P1.2 将 streaming orchestration 的 step loop 从 route 层继续下沉。此前 route 虽然已经不直接持有 streaming agent turn loop，但仍在 `_stream_multi_agent_orchestration(...)` 中负责：pop ready step、dispatch、转发 agent event、处理 runtime card、保存 turn_complete、失败终结、step complete 与最终 done。

本轮扩展：

- `backend/services/orchestration_stream_runner.py`
  - `StreamOrchestrationRuntimeDeps`
  - `StreamOrchestrationRuntimeEvent`
  - `iter_stream_orchestration_runtime_events(...)`

并接入 streaming orchestration route。

新的边界：

- service 负责 runtime execution：scheduler step loop、step lifecycle、turn completion persistence、failure handling、finalizer
- route 负责 transport rendering：`runtime_card` 调 `sse_card(...)`，普通 payload 序列化为 SSE `data:`
- route 仍负责前置准备：解析 prepared runtime、记录 orchestration_started / scheduler_plan_created、处理 no-agent / no-plan 早退

这一步的意义是：

- streaming orchestration 开始具备 Codex 风格 typed runtime event 边界
- route 不再手写每个 streaming step 的执行细节
- 后续把 route 前置准备也下沉后，transport 层可以进一步收敛为纯 render

边界：

- `StreamOrchestrationRuntimeDeps` 仍注入 route-local prompt/runtime/message adapter
- durable handoff 仍未接入，runtime event runner 仍使用 `pending_handoffs` map
- 下一步优先处理 handoff durability，把 in-memory pending handoff 替换/桥接到 inbox/outbox

### 11.80 2026-04-28 新进展：orchestration durable handoff inbox 已接入

P1.3 开始把 orchestration handoff 从纯内存 `pending_handoffs` map 往 durable inbox 语义推进。此前 handoff_created 事件虽然可以用于 recovery rebuild，但运行时真正消费 handoff 仍依赖进程内 map，缺少 lease / retry / dead-letter 语义。

本轮新增：

- `backend/models/database.py`
  - `OrchestrationHandoffDelivery`
- `backend/services/orchestration_inbox.py`
  - durable claim / ack / fail / projection helpers

并接入：

- `record_orchestration_handoffs(...)` 在保留兼容 map 的同时，写入 durable handoff delivery
- nonstream step runner 在执行前 claim handoff，成功后 ack，失败后 release retry
- streaming runtime event runner 复用同一套 durable handoff claim / ack / fail 语义
- recovery rebuild 在检测到 durable handoff 存在时，不再额外从事件重建 pending map
- checkpoint snapshot 新增 `orchestration_handoff_inbox` 与 summary 投影

这一步的意义是：

- orchestration handoff 首次拥有和 pipeline inbox 对齐的 lease/retry/dead-letter 基础语义
- handoff 不再仅依赖内存 map，在 step 失败和 recovery 场景下更接近真正 durable inbox
- route / runner 继续向统一 executor primitive 靠拢，handoff 输入源开始从“内存状态”过渡到“durable delivery state”

边界：

- 当前仍保留 `pending_handoffs` map 作为兼容层，便于老 recovery 路径和测试平滑过渡
- durable handoff 目前是 orchestration 专用 inbox，而不是直接复用 pipeline 表
- 下一步可以继续抽离 chat runtime preparation / prompt assembly，减少 route dependency adapter 持有

### 11.81 2026-04-28 新进展：chat runtime preparation 与 prompt assembly 已抽到 shared service

P1.4 开始削 route 对 executor dependency adapter 的直接持有。此前 orchestration agent turn runner 和 project single-agent path 虽然已经把执行 loop 下沉，但底层仍通过 route-local `_prepare_chat_turn_runtime(...)`、`_assemble_chat_messages(...)`、`_tool_runtime_kwargs(...)` 组装 runtime。

本轮新增：

- `backend/services/chat_runtime.py`
  - `PreparedChatTurnRuntime`
  - `prepare_chat_turn_runtime(...)`
  - `assemble_runtime_chat_messages(...)`
  - `build_tool_runtime_kwargs(...)`

并接入：

- orchestration nonstream agent turn runner dependency adapter
- orchestration stream agent turn iterator dependency adapter
- project single-agent sync path
- project single-agent stream path
- blocked tool replay runtime kwargs
- standalone assistant path的 message assembly 也改用 shared service

这一步的意义是：

- chat runtime preparation 和 prompt assembly 不再由 route 持有主逻辑
- orchestration executor 依赖的 adapter 数量进一步收缩，route 更接近“少量 wiring + transport”
- 相关测试环境也同步升级，`services.chat_runtime` 成为新的 LLM mock 注入边界

边界：

- standalone target agent resolution 仍在 route 层
- context compaction callback 与 runtime card builder 仍是 route-local adapter
- 下一步可以继续把 standalone runtime preparation 和更多 prompt/runtime callback 下沉，进一步压缩 route 责任

### 11.82 2026-04-28 新进展：executor-level cancellation check primitive 已接入 orchestration

P1.5 先解决“cancel 只改 ledger，不影响正在运行的 executor loop”这个问题。本轮不追求中断已经飞出去的单次模型请求，而是把 cancellation 变成 executor 在 step / turn / stream event 边界可观察的控制信号。

本轮新增：

- `backend/services/task_run_control.py`
  - `TaskRunCancelledError`
  - `raise_if_task_run_cancelled(...)`

并接入：

- `execute_non_stream_turn_loop(...)` 新增 `before_tool_call` callback
- `iter_stream_turn_events(...)` 新增 `before_turn` / `before_event` / `before_tool_call` callback
- orchestration nonstream agent turn 在 turn/tool 边界检查 cancelled
- orchestration stream agent turn 在 turn/event/tool 边界检查 cancelled
- sync orchestration step loop、stream orchestration runtime loop、interrupted recovery loop 在 step/finalize 边界检查 cancelled

行为变化：

- task run 被 API 标记为 `cancelled` 后，executor 不再继续自然跑完整个 orchestration 队列
- streaming orchestration 会尽快收口成 `done(cancelled=true)`，而不是继续 dispatch 后续 step
- recovery 过程中如果 task run 被取消，会返回 `reason=\"cancelled\"`，而不是误记为 failed

边界：

- 仍不能强杀已经在飞行中的单次 LLM 请求或外部工具进程
- 当前是“cooperative cancellation”，不是 preemptive interrupt
- 下一步如果要更接近 Codex，需要把 cancel token 继续传到更底层的模型/工具执行单元

### 11.83 2026-04-28 新进展：nonstream orchestration runtime loop 已抽成 shared service

在 11.79 抽出 streaming orchestration runtime event runner 后，Catown 仍保留一块明显差距：sync orchestration 和 interrupted recovery 的顶层 step loop 还分别定义在 route 内，只是都调用了 shared step runner。

本轮新增：

- `backend/services/orchestration_runtime_runner.py`
  - `NonstreamOrchestrationRuntimeDeps`
  - `run_nonstream_orchestration_runtime(...)`

并接入：

- sync multi-agent orchestration
- interrupted orchestration recovery

设计取舍：

- 这次先统一 nonstream 顶层 runtime loop，不强行把 start/plan event、lease claim、finalization 一次性全部并进去
- service 负责：step pop、agent resolve、cancellation check、step context build、step runner 调度
- route 仍负责：前置 runtime 准备、recovery lease 续约 callback、terminal result/finalizer 组装

这一步的意义是：

- sync orchestration 与 recovery 首次复用同一条顶层 nonstream executor loop
- orchestration runtime 的“顶层 while step loop”现在 stream / nonstream 两边都已有 service 边界
- route 中剩下的非传输逻辑进一步收敛到准备态和 terminalization，而不是持续持有 step 调度循环

边界：

- route 仍保留 `orchestration_started` / `scheduler_plan_created` / recovery started/rebuilt 这些前置事件
- recovery lease 的续约与 lease lost 分支仍由 route callback 提供
- 下一步如果继续逼近 Codex，可以把这些前置事件和 lease orchestration 也纳入统一 runtime driver

### 11.84 2026-04-28 新进展：orchestration startup / recovery 事件辅助已收口

在 11.83 之后，sync / stream / recovery 虽然已经更多依赖 service-level runtime runner，但 route 仍保留一段重复的前置事件拼装逻辑：

- `orchestration_started`
- `scheduler_plan_created`
- `task_run_recovery_started`
- `scheduler_recovery_state_rebuilt`

本轮扩展 `backend/services/orchestration_events.py`，新增：

- `record_orchestration_started(...)`
- `record_scheduler_plan_created(...)`
- `record_task_run_recovery_started(...)`
- `record_scheduler_recovery_state_rebuilt(...)`

并接入：

- sync orchestration startup
- stream orchestration startup
- interrupted recovery startup / rebuild

这一步的意义是：

- orchestration 的“准备态协议”也开始脱离 route，统一进入 event helper service
- sync / stream / recovery 的 started-plan payload shape 更稳定，测试也可以直接覆盖 helper
- route 进一步收敛为 runtime wiring，而不是持续手写 event payload 细节

边界：

- no-agent / no-plan 失败分支仍在 route
- recovery lease claim / renew / lease-lost 编排仍在 route
- 下一步如果继续压缩 route，可以继续把这些失败与 lease 协议推进到 runtime driver / lease service

### 11.85 2026-04-28 新进展：recovery lease 协议已抽成 shared service

在 11.84 之后，interrupted recovery 路径里最大的一块 route-local 控制协议变成了 recovery lease：

- claim recovery lease
- renew recovery lease
- lease lost error
- claimed / leased / not_running / not_recoverable 结果整形

本轮新增：

- `backend/services/orchestration_recovery_lease.py`
  - `RecoveryLeaseClaimResult`
  - `RecoveryLeaseLostError`
  - `claim_recovery_lease(...)`
  - `renew_recovery_lease(...)`
  - `ensure_recovery_lease(...)`

并接入 `backend/routes/api.py` 的 recovery flow。

这一步的意义是：

- recovery lease 从 route 内部状态机提升为可复用的 shared control primitive
- manual resume / startup recovery 对 lease claimed、lease lost 的语义更集中、更容易测试
- route 进一步减少底层 lease 条件判断和状态拼装

边界：

- recovery 主流程仍在 route 驱动，lease helper 只是先收口协议本身
- sync/stream orchestration 的 no-agent / no-plan 早退失败仍在 route
- 下一步继续收这些早退失败分支，或者把 recovery 主驱动整体往 service 推

### 11.86 2026-04-28 新进展：orchestration 早退 failure guard 已收口

在 11.85 之后，route 里还残留一类重复控制协议：setup/preflight 阶段的早退失败分支。

主要包括：

- sync orchestration：`no_valid_agents`、`runtime_unprepared`
- stream orchestration：`no_valid_agents`、`runtime_unprepared`
- interrupted recovery：`chatroom_missing`、`no_valid_agents`、`no_runnable_plan`、`no_runnable_steps`、`incomplete`

本轮新增：

- `backend/services/orchestration_guards.py`
  - `fail_orchestration_preflight(...)`
  - `fail_recovery_guard(...)`
  - `RecoveryFailureOutcome`

并接入 sync/stream/recovery route。

这一步的意义是：

- orchestration setup 阶段的 terminal early-exit wording、event type、TaskRun failed summary 与 recovery result detail 开始统一
- route 进一步减少重复的 fail-event + result-object 拼装
- 这些 guard 现在可以独立测试，不必每次都通过整条 route 链路覆盖

边界：

- stream route 对 `collab_skip` / `done` 的 transport 输出仍在 route
- recovery 主驱动与 no-agent/no-plan 的决策点仍在 route，只是失败动作已共享
- 下一步可以继续把 recovery 主驱动整体推向 service，或者开始收口 stream route 的 transport-only 渲染边界

### 11.87 2026-04-28 新进展：recovery 主驱动已抽成 shared runtime service

在 11.86 之后，interrupted recovery route 里仍保留一段最大的控制流：在 prepared runtime 之上完成 recovery started/rebuilt、scheduler rebuild、runtime 执行、incomplete guard、completed result/finalizer 整形。

本轮新增：

- `backend/services/orchestration_recovery_runner.py`
  - `OrchestrationRecoveryRuntimeDeps`
  - `OrchestrationRecoveryRuntimeResult`
  - `run_orchestration_recovery_runtime(...)`

并接入 `backend/routes/api.py`。

新的边界：

- service 负责：prepared recovery runtime 的 started/rebuilt event、rebuild state、nonstream runtime execute、no-runnable/incomplete guard、completed result/finalizer
- route 负责：claim lease、chatroom/project/agent resolve、prepared runtime compile、exception fallback

这一步的意义是：

- interrupted recovery 不再在 route 中持有完整执行主链
- recovery 现在和 sync/stream runtime 一样，开始拥有独立的 service-level runtime driver
- route 中剩下的 orchestration 控制协议进一步集中到前置解析与 transport/exception 边界

边界：

- chatroom/project/agent resolve 仍在 route
- recovery claim lease 与 top-level exception mapping 仍在 route
- 下一步可以继续把这些前置解析 / claim 协议也推向 recovery driver，或者转去收 stream route 的 transport-only 边界

### 11.88 2026-04-28 新进展：recovery 前置解析已抽成 shared preparation service

在 11.87 之后，recovery route 剩余最重的一段非传输责任变成了前置解析：

- resolve chatroom
- resolve project / agents
- recover requested agent names
- prepare orchestration runtime
- no-valid-agent / no-runnable-plan guard

本轮新增：

- `backend/services/orchestration_recovery_prepare.py`
  - `PreparedOrchestrationRecoveryContext`
  - `prepare_orchestration_recovery_context(...)`

并接入 `backend/routes/api.py`。

这一步的意义是：

- interrupted recovery 的“准备态”与“执行态”现在都已经拥有独立 service 边界
- route 中 recovery 剩余的责任进一步压缩到：claim lease、top-level exception mapping、结果转 API payload
- 测试环境也同步补了 service module reset，避免 stale `models.database` registry 污染新一轮 app import

边界：

- recovery claim lease 仍由 route 驱动后再调用 preparation service
- stream route 的 transport-only 渲染边界仍然是另一个可继续下沉的方向
- 如果继续逼近 Codex，可以把 recovery 整体包装成一个更高层的 orchestration resume driver，对 route 暴露单入口

### 11.89 2026-04-28 新进展：stream orchestration session wrapper 已抽成 shared helper

在 11.88 之后，stream route 里剩下最明显的一段 transport-adjacent protocol 是：

- `collab_start`
- `collab_skip`
- preflight `done`
- stream runtime session 包装

此前这些虽然不再属于核心 executor loop，但仍由 route 自己判断和发出。

本轮扩展 `backend/services/orchestration_stream_runner.py`：

- `stream_collab_start_payload(...)`
- `stream_collab_skip_payload(...)`
- `stream_collab_done_payload(...)`
- `iter_stream_orchestration_session_events(...)`

并接入 `backend/routes/api.py`。

这一步的意义是：

- stream route 更接近“只 render runtime events”，不再自己决定 startup/skip/done payload
- stream orchestration 从 session start 到 runtime execution 现在都在 service 内有连续边界
- 现有 SSE body shape 保持兼容，但 route 中的 stream 控制协议进一步减少

边界：

- route 仍负责把 `StreamOrchestrationRuntimeEvent` render 成真正的 SSE 文本
- `runtime_card` 的 `sse_card(...)` 序列化仍在 route
- 如果继续往 Codex 靠拢，下一步可以考虑把 render helper 也抽成更显式的 transport adapter

### 11.90 2026-04-28 新进展：stream runtime event 渲染已收口到 transport helper

在 11.89 之后，stream route 还保留最后一层明显的 transport 分支：

- `runtime_card` 走 `sse_card(...)`
- 普通 payload 走 `json.dumps(...) -> data: ...`

本轮继续扩展 `backend/services/orchestration_stream_runner.py`：

- `render_stream_runtime_event(...)`

并让 `backend/routes/api.py` 改为统一调用这个 helper。

这一步的意义是：

- stream route 更接近纯粹的 async yield 转发
- `StreamOrchestrationRuntimeEvent -> SSE chunk` 现在有了独立 helper，可单测、可复用
- orchestration streaming 从 session wrapper 到 transport rendering 基本都已有 service-level protocol 边界

边界：

- route 仍注入 `sse_card(...)` 与 `json.dumps(...)` 这两个 transport dependency
- standalone / single-agent streaming 仍保留各自的 route-local SSE 分支
- 如果继续收口，下一步可以把这套 renderer 泛化到其它 streaming path

### 11.91 2026-04-28 新进展：shared streaming transport helper 已覆盖 standalone / single-agent / orchestration

在 11.90 之后，orchestration streaming 已经走 shared render helper，但 standalone assistant stream 与 project single-agent stream 仍各自手写：

- `runtime_card` 持久化与公开 payload
- 普通 event -> `data: ...`
- `turn_complete` 提取

本轮新增：

- `backend/services/stream_transport.py`
  - `render_sse_payload(...)`
  - `render_chatroom_runtime_card_sse(...)`
  - `render_stream_turn_event(...)`
  - `StreamTurnRenderResult`

并接入：

- standalone assistant stream
- project single-agent stream
- orchestration stream runtime renderer继续复用统一 transport helper 思路

这一步的意义是：

- Catown 主要 streaming path 的 transport adapter 形态开始统一
- route 中不再散落多份 `runtime_card / payload / turn_complete` 渲染逻辑
- runtime card 持久化与公开 payload 的协议可以被 focused test 直接覆盖

边界：

- route 仍持有 `_store_runtime_card(...)` 与 `_public_runtime_card_payload(...)`
- standalone / single-agent / orchestration 还没有共享更高层的 full streaming session driver
- 下一步如果继续收口，可以考虑把这些 path 的 session-level streaming driver 再往统一 executor/transport 层抽

### 11.92 2026-04-28 新进展：standalone / single-agent streaming render loop 已复用 shared helper

在 11.91 之后，standalone assistant stream 和 project single-agent stream 虽然已经使用了 shared transport function，但仍各自手写：

- `async for event in iter_stream_turn_events(...)`
- `runtime_card / payload / turn_complete` 分支处理
- `final_content` 提取

本轮继续扩展 `backend/services/stream_transport.py`：

- `iter_rendered_stream_turn_events(...)`

并接入：

- standalone assistant stream
- project single-agent stream

这一步的意义是：

- 主要 streaming path 已经共享同一套 event-render loop helper
- route 中不再散落两份几乎相同的 `iter_stream_turn_events -> render -> final_content` 控制流
- 后续如果再统一 single-agent / standalone 的 session-level streaming driver，会更容易在这一层继续收口

边界：

- final response save / failure fallback 仍由各 route 分支控制
- orchestration stream 已经更高一层复用 session wrapper，不需要再走同一个 helper
- 下一步可以继续把 single-agent / standalone 的 success/failure session driver 也抽成 shared service

### 11.93 2026-04-28 新进展：standalone / single-agent streaming session driver 已复用 shared service

在 11.92 之后，standalone assistant stream 和 project single-agent stream 仍各自保留一段几乎相同的 session-level 主链：

- 调 `iter_stream_turn_events(...)`
- 通过 shared render loop 渲染 chunk
- 维护 `final_content`
- 后续进入 route 自己的 success/failure 分支

本轮新增：

- `backend/services/single_agent_stream_session.py`
  - `SingleAgentStreamSessionDeps`
  - `SingleAgentStreamSessionResult`
  - `iter_single_agent_stream_session(...)`

并接入：

- standalone assistant stream
- project single-agent stream

这一步的意义是：

- 主要 single-agent streaming path 现在也拥有 shared session driver，而不仅仅是 shared render helper
- route 中不再手写 `iter_stream_turn_events -> render -> final_content` 这条核心控制流
- 后续如果继续统一 single-agent / standalone 的 success/failure 终结逻辑，会更容易在这一层继续推进

边界：

- success path 的消息保存 / `complete_task_run(...)` / memory extraction 仍在 route
- failure path 的 error card / fallback message 仍在 route
- 下一步可以继续把 single-agent / standalone 的 success/failure finalizer 也抽到 shared session service

### 11.94 2026-04-28 新进展：standalone / single-agent streaming session finalizer 已共享

在 11.93 之后，standalone assistant stream 和 project single-agent stream 还各自保留一段重复的 session 终结逻辑：

- success path：
  - 保存最终消息
  - publish saved message
  - `record_agent_turn_completed(...)`
  - `complete_task_run(...)`
  - 发 `done`
  - 可选 memory extraction
- failure path：
  - `task_run_failed`
  - `complete_task_run(... failed ...)`
  - 根据是否已有最终消息，返回 `error` 或 fallback `done`

本轮新增：

- `backend/services/single_agent_stream_finalizer.py`
  - `finalize_single_agent_stream_success(...)`
  - `finalize_single_agent_stream_failure(...)`
  - `SingleAgentStreamFinalizeResult`

并接入：

- standalone assistant stream
- project single-agent stream

这一步的意义是：

- single-agent streaming path 的 session-level success/failure 终结都开始共享
- route 中剩下的 streaming 责任进一步压缩到上下文准备和 very-thin wiring
- standalone 路径里原先对 `assistant_name/assistant_id` 的直接引用也顺带统一到 runtime context 字段

边界：

- orchestration stream 仍走自己的 runtime/session wrapper，不复用这个 finalizer
- `_persist_stream_failure(...)`、runtime-card 持久化和 websocket publish 仍在 route 侧 helper
- 下一步如果继续统一，可以考虑把 `_persist_stream_failure(...)` 以及 runtime-card store/publish 也继续往 shared stream service 推

### 11.95 2026-04-28 新进展：runtime-card 持久化与 stream failure fallback 已抽成 shared service

在 11.94 之后，route 中还剩一块明显的 streaming 辅助逻辑：

- runtime-card public payload 过滤
- runtime-card 持久化 + websocket/monitor 发布
- stream failure fallback 的 error card + 可见消息持久化

本轮新增：

- `backend/services/stream_runtime_persistence.py`
  - `public_runtime_card_payload(...)`
  - `publish_runtime_card_event(...)`
  - `store_runtime_card(...)`
  - `summarize_stream_error(...)`
  - `persist_stream_failure(...)`

并接入：

- standalone assistant stream
- project single-agent stream
- stream transport helper / runtime-card replay endpoint

这一步的意义是：

- route 里又去掉了一整段 runtime-card / failure-fallback 细节实现
- runtime-card public payload 规则与 failure fallback 文案现在有 shared service 边界和 focused tests
- `_make_app` 的 test module reset 也继续扩充，避免服务拆分后出现 stale `models.database` registry

边界：

- websocket/monitor publish 仍通过 shared service 内部依赖 `websocket_manager`
- `_publish_saved_chat_message(...)` 仍由 route 注入给 failure persistence helper
- 如果继续推进，下一步可以考虑把 `_publish_saved_chat_message(...)` 本身也继续往 shared message/runtime publish service 收

### 11.96 2026-04-28 新进展：saved-message publish 已抽成 shared service

在 11.95 之后，route 里还残留一个经常被调用的发布侧 helper：`_publish_saved_chat_message(...)`。它本质上只是“消息已落库后，向 room/monitor 广播”的适配器。

本轮新增：

- `backend/services/chat_publish.py`
  - `publish_saved_chat_message(...)`

并接入：

- 普通 `send_message(...)`
- tool replay result publish
- standalone / single-agent / orchestration stream path
- `stream_runtime_persistence.persist_stream_failure(...)`

这一步的意义是：

- route 又少了一段重复的 websocket / monitor publish 辅助逻辑
- saved chat message 的 room/monitor 广播协议现在有了独立 service 边界和 focused test
- stream runtime persistence 也不再需要 route 传 message publish callback

边界：

- `chat_publish` 仍直接依赖 `websocket_manager`
- route 仍保留 `_publish_saved_chat_message` 之外的 message save 决策
- 如果继续推进，下一步可以进一步看普通 non-stream path 的 message save + publish + ledger completion 是否也值得统一成 shared session finalizer

### 11.97 2026-04-28 新进展：non-stream single-agent session finalizer 已共享

在 11.96 之后，non-stream single-agent path 仍有一块和 streaming finalizer 对应的重复控制流：

- success path：
  - save final message
  - publish saved message
  - `record_agent_turn_completed(...)`
  - `complete_task_run(...)`
  - optional memory extraction
- failure path：
  - `task_run_failed`
  - `complete_task_run(... failed ...)`

本轮新增：

- `backend/services/single_agent_session_finalizer.py`
  - `finalize_single_agent_session_success(...)`
  - `finalize_single_agent_session_failure(...)`
  - `SingleAgentSessionFinalizeResult`

并接入：

- standalone non-stream assistant path
- project single-agent non-stream path

这一步的意义是：

- single-agent sync 与 stream 两条路径的 session finalizer 终于开始对齐
- route 中 non-stream path 的终结动作进一步收口，不再手写消息保存/发布/记账组合
- 后续如果继续统一 single-agent path，会更容易抽出更高层的 session driver

边界：

- tool loop / prompt assembly / runtime mode selection 仍在 route
- streaming finalizer 与 non-stream finalizer 还没有再合成一层更高抽象
- 下一步如果继续推进，可以考虑把 single-agent sync/stream 的 session driver 也往统一层收

### 11.98 2026-04-28 新进展：saved-message publish 与 stream runtime persistence 已进一步收口

在 11.97 之后，streaming path 里还有一块 route 侧辅助逻辑：

- runtime-card public payload / store / publish
- stream failure fallback 持久化
- saved chat message 的 room/monitor 广播

本轮继续推进两层收口：

- `backend/services/chat_publish.py`
  - `publish_saved_chat_message(...)`
- `backend/services/stream_runtime_persistence.py`
  - 继续承接 runtime-card / failure fallback 持久化，并改为直接复用 `chat_publish`

效果：

- route 中不再保留 `_publish_saved_chat_message(...)`
- stream runtime persistence 不再需要 route 注入 publish callback
- 普通消息发送、tool replay result、single-agent streaming、runtime-card replay 统一走 shared publish / persistence service

这一步的意义是：

- route 中 streaming 辅助逻辑继续变薄，更多转为 shared service graph
- message publish 与 runtime persistence 现在各自拥有独立、可测的 service 边界
- single-agent sync/stream 与 orchestration stream 都开始复用同一套下游发布能力

边界：

- websocket/monitor 仍由 service 内部直接依赖 manager/serializer
- 普通 non-stream path 还没有统一更高层的 session driver
- 下一步可以继续把 single-agent sync/stream 的更高层 session driver 进一步合并

### 11.99 2026-04-28 新进展：single-agent sync session driver 已抽成 shared service

在 11.98 之后，single-agent sync path 还保留一块和 streaming session driver 对应的控制流：

- 执行 session 主体
- 空响应 early-exit
- success finalizer
- failure finalizer

本轮新增：

- `backend/services/single_agent_session_runner.py`
  - `SingleAgentSessionRunnerDeps`
  - `SingleAgentSessionRunnerResult`
  - `run_single_agent_session(...)`

并接入：

- standalone non-stream assistant path
- project single-agent non-stream path

这一步的意义是：

- single-agent sync 和 single-agent stream 两条路径都开始拥有独立的 shared session driver
- route 中 non-stream path 不再手写“execute -> if empty -> finalize success/failure”控制流
- 继续逼近 single-agent sync/stream 的更高层会话统一

边界：

- sync 与 stream 仍分别使用不同的 driver，而不是统一成一个更高层 session abstraction
- orchestration path 不复用这个 single-agent session driver
- 如果继续推进，下一步可以考虑把 single-agent sync/stream 再合成更高层 unified session driver

### 11.100 2026-04-28 新进展：single-agent sync/stream 已接到统一 orchestrator facade

在 11.99 之后，single-agent sync 与 single-agent stream 虽然都有 shared driver，但 route 仍分别直接依赖：

- `run_single_agent_session(...)`
- `iter_single_agent_stream_session(...)`

本轮新增：

- `backend/services/single_agent_session_orchestrator.py`
  - `SyncSingleAgentSessionSpec`
  - `StreamSingleAgentSessionSpec`
  - `run_sync_single_agent_session(...)`
  - `iter_stream_single_agent_session(...)`

并接入：

- standalone non-stream assistant
- project single-agent sync
- standalone assistant stream
- project single-agent stream

这一步的意义是：

- route 现在不再直接依赖底层 sync/stream runner，而是通过更高层 orchestrator facade 进入 single-agent session stack
- single-agent sync/stream 开始有统一的会话入口表面，后续再进一步合并 driver 时改动面更小
- focused test 也可以直接覆盖这一层 facade，而不总是穿透到底层 runner

边界：

- facade 目前仍是薄封装，sync 与 stream 底层实现尚未合并成一个真正统一的 driver
- single-agent finalizer 仍区分 sync 与 stream 两套实现
- 如果继续推进，下一步就该考虑把 sync/stream finalizer 与 session driver 再往上一层统一

### 11.101 2026-04-28 新进展：single-agent unified session abstraction 已替代直连 runner

在 11.100 之后，虽然已经有 orchestrator facade，但 route 侧仍然在直接构造底层 sync/stream runner deps。为了让这层真正成为 single-agent 的统一会话抽象，本轮把 route 依赖面进一步压到 unified spec：

- `UnifiedSingleAgentSyncSessionSpec`
- `UnifiedSingleAgentStreamSessionSpec`
- `run_unified_single_agent_sync_session(...)`
- `iter_unified_single_agent_stream_session(...)`

接入后：

- standalone non-stream assistant
- project single-agent sync
- standalone assistant stream
- project single-agent stream

都不再直接依赖底层 `run_single_agent_session(...)` / `iter_single_agent_stream_session(...)`。

这一步的意义是：

- route 与底层 single-agent sync/stream runner 进一步解耦
- single-agent 会话栈开始具备更稳定的上层抽象边界，后续合并 driver/finalizer 时只需调整 orchestrator 内部
- focused tests 也开始覆盖 unified facade，而不是只盯底层 runner

边界：

- sync 与 stream 底层实现仍分别存在
- unified facade 目前仍是结构性统一，不是行为上完全同构
- 如果继续推进，下一步就该考虑把 sync/stream finalizer 也往更统一的结果模型收

### 11.102 2026-04-28 新进展：single-agent sync/stream finalizer 结果模型已统一

在 11.101 之后，single-agent sync 与 stream 虽然已经通过 unified facade 进入 session stack，但它们的 finalizer 结果模型仍然不一致：

- sync finalizer 返回 `saved_message`
- stream finalizer 返回 `payload + saved_message`

本轮新增：

- `backend/services/single_agent_session_terminal.py`
  - `SingleAgentSessionTerminalResult`
  - `persist_single_agent_session_success(...)`
  - `terminalize_single_agent_session_failure(...)`

并让：

- `single_agent_session_finalizer.py`
- `single_agent_stream_finalizer.py`

都复用这套 shared terminal helper。

这一步的意义是：

- single-agent sync/stream finalizer 首次共享同一个 terminal result model
- success path 的消息保存/发布/记账/complete 逻辑不再重复维护两份
- failure path 的 failed terminalization 也开始共享同一条 helper

边界：

- stream finalizer 仍会额外补 `payload`
- sync finalizer 仍不需要 `payload`
- 如果继续推进，下一步可以考虑把 sync/stream finalizer 的外部接口也统一成更一致的返回契约

### 11.103 2026-04-28 新进展：single-agent managed session stack 已开始落地

在 11.102 之后，single-agent sync/stream 虽然已经有 unified facade，但 route 里 still retained 一层更高的会话控制：

- sync：
  - 组 `UnifiedSingleAgentSyncSessionSpec`
  - 手工调用 unified sync facade
- stream：
  - 组 `UnifiedSingleAgentStreamSessionSpec`
  - 手工处理 stream session 的 success/failure finalizer 与 terminal payload

本轮在 `backend/services/single_agent_session_orchestrator.py` 上继续扩展：

- `ManagedSingleAgentSyncSessionSpec`
- `ManagedSingleAgentStreamSessionSpec`
- `run_managed_single_agent_sync_session(...)`
- `iter_managed_single_agent_stream_session(...)`

并接入：

- standalone assistant sync/stream
- project single-agent sync/stream

这一步的意义是：

- route 进一步从 single-agent session control flow 中退出
- stream path 的 final done/error payload 也开始由 managed stack 统一发出，而不是 route 手写
- single-agent session stack 的层次现在更完整：driver -> finalizer -> managed orchestrator

边界：

- managed sync/stream 仍分别基于不同底层 runner
- finalizer 外部接口仍未完全同构
- 如果继续推进，下一步可以进一步统一 single-agent sync/stream 的 spec/result 契约

### 11.104 2026-04-28 新进展：single-agent unified facade 已真正取代 route 直连

在 11.103 之后，虽然已经有 managed stack，但 route 仍直接调用了底层 facade：

- `run_single_agent_session(...)`
- `iter_single_agent_stream_session(...)`

本轮把 route 全部切到更高层入口：

- `run_unified_single_agent_sync_session(...)`
- `iter_unified_single_agent_stream_session(...)`
- `run_managed_single_agent_sync_session(...)`
- `iter_managed_single_agent_stream_session(...)`

并补 focused tests 覆盖 unified facade 与 managed stack。

这一步的意义是：

- route 进一步退出 single-agent 会话实现细节
- single-agent sync/stream 会话栈开始真正围绕统一抽象组织，而不是只是在 service 内并排存在
- 后续继续统一 spec/result/driver 时，改动面会进一步集中到 orchestrator 内部

边界：

- sync/stream 底层 runner 与 finalizer 仍分别存在
- managed stack 仍然需要 route 注入不少 lambda/deps
- 如果继续推进，下一步可以再压一层，把 spec 构建也从 route 中抽掉

### 11.105 2026-04-28 新进展：single-agent managed stack 已开始透过 unified facade 收口

在 11.104 之后，route 虽然已经不直接调用底层 runner，但 single-agent sync/stream 的 managed stack 仍没有完全收敛到统一入口。

本轮继续调整：

- standalone assistant sync 改为通过 `run_unified_single_agent_sync_session(...)`
- project single-agent sync 改为通过 `run_unified_single_agent_sync_session(...)`
- standalone assistant stream 改为通过 `iter_unified_single_agent_stream_session(...)`
- project single-agent stream 改为通过 `iter_unified_single_agent_stream_session(...)`

并补 focused orchestrator tests，覆盖 unified sync/stream facade 在 route 使用方式上的兼容。

这一步的意义是：

- route 对 single-agent runner 的直接依赖进一步减少
- single-agent sync/stream 会话栈更清晰地围绕 unified facade 组织
- 继续为后续统一 spec/result 契约和 finalizer 模型打基础

边界：

- sync/stream 仍通过不同的底层数据结构和 finalizer 工作
- route 仍需要构造较多 lambda/deps
- 如果继续推进，下一步就可以进一步统一 sync/stream 的 spec/result 契约

### 11.106 2026-04-28 新进展：managed single-agent sync/stream spec 契约已统一一层

在 11.105 之后，managed single-agent stack 的 sync/stream 输入形状仍不够统一：

- sync 走 `ManagedSingleAgentSyncSessionSpec`
- stream 走 `ManagedSingleAgentStreamSessionSpec`

本轮改成统一的：

- `ManagedSingleAgentSessionSpec`
- `ManagedSingleAgentSessionCallbacks`

并让 standalone/project 的 sync/stream path 都通过这一套 managed 契约进入 orchestrator。

这一步的意义是：

- single-agent sync/stream 的 managed 输入模型开始真正同构
- route 侧构造 managed stack 的方式更一致
- 后续继续压缩 callback/lambda 或统一 unified spec 时，有了更稳定的共同表面

边界：

- `UnifiedSingleAgentSyncSessionSpec` 与 `UnifiedSingleAgentStreamSessionSpec` 仍分别存在
- managed callbacks 对 sync 仍有“未使用”字段
- 如果继续推进，下一步可以进一步统一 sync/stream 的 unified spec/result 契约

### 11.107 2026-04-28 新进展：managed single-agent spec 已统一为一套 contract

在 11.106 之后，managed stack 已经开始同构，但 route 和 orchestrator 仍然混用不同的 managed spec 名称与 shape。

本轮继续把 managed 层彻底收口为：

- `ManagedSingleAgentSessionSpec`
- `ManagedSingleAgentSessionCallbacks`

并让 standalone/project 的 sync/stream path 都通过这套 contract 构造 managed stack。

这一步的意义是：

- single-agent sync/stream 的 managed 输入模型正式统一
- route 构造 single-agent session stack 的方式进一步同构
- 后续如果继续统一 `UnifiedSingleAgentSessionSpec` 或结果模型，改动面会更集中在 orchestrator 内部

边界：

- `UnifiedSingleAgentSyncSessionSpec` 与 `UnifiedSingleAgentStreamSessionSpec` 仍分别存在
- sync 与 stream 依然通过不同的底层 deps 表达执行差异
- 如果继续推进，下一步就该考虑把 unified sync/stream spec 本身也收成更统一的一套

### 11.108 2026-04-28 新进展：legacy single-agent wrapper spec 已移除

在 11.107 之后，orchestrator 内仍残留两层已经不再被 route 使用的 legacy wrapper：

- `SyncSingleAgentSessionSpec`
- `StreamSingleAgentSessionSpec`
- `run_sync_single_agent_session(...)`
- `iter_stream_single_agent_session(...)`

本轮把这些 wrapper 从 orchestrator 与 focused tests 中移除，single-agent 栈进一步收敛到：

- `UnifiedSingleAgentSessionSpec`
- `ManagedSingleAgentSessionSpec`
- `ManagedSingleAgentSessionCallbacks`

这一步的意义是：

- single-agent orchestrator 的抽象层次更清楚，不再保留“旧入口 + 新入口”双轨
- 后续继续统一 unified sync/stream spec 时，内部结构更干净
- focused tests 也更直接反映当前真实公开契约

边界：

- `UnifiedSingleAgentSessionSpec` 仍有 `mode + execute_turn` / `mode + stream_deps` 的差异
- route 仍需显式构造这套 unified spec
- 如果继续推进，下一步就该尝试把 unified sync/stream spec 进一步同构

### 11.109 2026-04-29 新进展：single-agent unified sync/stream spec 已改为 builder 契约

在 11.108 之后，single-agent unified spec 仍保留一个明显结构差异：

- sync 走 `mode=\"sync\" + execute_turn`
- stream 走 `mode=\"stream\" + stream_deps`

本轮改成 builder 契约：

- `build_unified_sync_single_agent_session_spec(...)`
- `build_unified_stream_single_agent_session_spec(...)`

`UnifiedSingleAgentSessionSpec` 本身收敛为单一字段：

- `iterate: Callable[[], AsyncIterator[UnifiedSingleAgentSessionOutcome]]`

这一步的意义是：

- single-agent sync/stream 的 unified 层不再靠 `mode` 分支区分结构
- route 构造 unified spec 的方式开始真正同构
- orchestrator 内部后续再统一 managed stack 或 result model 时，会有更稳定的公共入口

边界：

- sync/stream 的底层执行方式仍然不同，只是被 builder 隐藏到了统一 spec 背后
- managed stack 的 callbacks 仍需分别处理 sync 与 stream 的终结差异
- 如果继续推进，下一步可以继续压缩 managed callbacks 的差异

### 11.110 2026-04-29 新进展：single-agent unified spec 已去掉显式 mode 分叉

在 11.109 之后，single-agent unified spec 虽然已经通过 builder 构造，但内部仍保留一个显式 `mode` 分叉：

- sync：`mode="sync"`
- stream：`mode="stream"`

本轮继续把统一层往前推进：

- `UnifiedSingleAgentSessionSpec` 收敛为单一 `iterate` 契约
- `build_unified_sync_single_agent_session_spec(...)` 与 `build_unified_stream_single_agent_session_spec(...)` 负责把 sync/stream 差异隐藏到 builder 内部
- route 和 focused tests 都改为直接通过 builder 构造 unified spec

这一步的意义是：

- unified 层不再暴露 sync/stream 的结构分叉
- single-agent sync/stream 在 orchestrator 内部第一次真正共享同一种 spec 形状
- 后续若继续收 managed callbacks 或更高层 session 结果模型，基础会更干净

边界：

- managed callbacks 仍保留 sync/stream 的终结差异
- stream path 仍需要 `serialize_payload` 这类 transport 细节
- 如果继续推进，下一步可以继续压缩 managed callbacks 的差异

### 11.111 2026-04-29 新进展：managed single-agent stream transport 已从 callbacks 拆出

在 11.110 之后，managed single-agent stack 还残留一处很明显的异味：

- `ManagedSingleAgentSessionCallbacks` 里同时放 lifecycle finalizer 和 stream-only `serialize_payload`
- sync path 只能被迫传 `serialize_payload=None`
- transport 细节继续污染本应只描述 session terminalization 的 managed callbacks

本轮继续把这层 contract 收紧：

- `ManagedSingleAgentSessionCallbacks` 只保留 `finalize_success` / `finalize_failure`
- 新增 `ManagedSingleAgentStreamTransport`
- `ManagedSingleAgentSessionSpec` 通过 `stream_transport` 显式承载 streaming terminal SSE 序列化能力
- standalone assistant stream 与 project single-agent stream 改为单独注入 transport，而 sync path 不再传空的 stream 字段

这一步的意义是：

- managed callbacks 更接近纯 lifecycle contract，而不是混合 transport concerns
- single-agent sync path 不再被 stream-only 参数污染
- 后续如果继续把 managed session builder 再往上收，会有更清楚的 execution / finalization / transport 分层

边界：

- route 仍需要手工构造 `ManagedSingleAgentStreamTransport`
- stream terminal output 仍由 managed stream 入口单独渲染
- 如果继续推进，下一步可以考虑把 managed sync/stream spec 的 route-local 构造再包成更高层 builder

### 11.112 2026-04-29 新进展：managed single-agent spec 的 route-local 组装已下沉为 builder

在 11.111 之后，虽然 managed callbacks 与 stream transport 已经分层，但 route 侧还保留一块重复样板：

- standalone assistant sync 手工拼 `ManagedSingleAgentSessionSpec(...)`
- standalone assistant stream 手工拼 `ManagedSingleAgentSessionSpec(...)`
- project single-agent sync / stream 也各自重复同一种 managed spec 装配

这意味着 route 虽然不再直连底层 runner，但仍然知道太多 managed contract 的内部结构。

本轮继续把这层收下去：

- 新增 `build_managed_single_agent_sync_session_spec(...)`
- 新增 `build_managed_single_agent_stream_session_spec(...)`
- route 改为直接调用 managed builders，而不是自己拼 `ManagedSingleAgentSessionSpec`
- focused tests 也跟随切到 managed builders，只保留一条 raw-spec 负向用例验证缺失 transport 的契约

这一步的意义是：

- single-agent route 进一步退出会话栈内部装配细节
- managed spec 的公开进入方式开始和 unified spec 一样走 builder，而不是 route 手工 new dataclass
- 后续如果继续压 session builder / finalizer / transport 的边界，改动面会更集中在 orchestrator service

边界：

- route 仍要提供 finalize lambdas 和 stream deps，本轮只收了 spec 装配层
- managed sync/stream 仍分别走不同 builder，而不是单一自动判别入口
- 如果继续推进，下一步可以考虑把 finalize / transport 这层 route-local lambda 继续往更高层 helper 收

### 11.113 2026-04-29 新进展：managed stream builder 已直接复用 stream deps serializer

在 11.112 之后，managed stream builder 虽然已经替 route 收了 spec 装配，但还有一处机械重复：

- `SingleAgentStreamSessionDeps` 自己带一份 `serialize_payload`
- `build_managed_single_agent_stream_session_spec(...)` 又额外要求 route 再传一份 terminal `serialize_payload`
- standalone / project single-agent stream 因此还在重复透传同一个 serializer

这类重复没有引入额外语义，只是在扩大 route 和 builder 的接口面。

本轮继续把它压掉：

- `build_managed_single_agent_stream_session_spec(...)` 不再接收独立 `serialize_payload`
- managed stream transport 直接从 `deps.serialize_payload` 派生
- standalone / project single-agent stream route 去掉重复的 serializer 参数
- focused test 改为验证 builder 派生的 serializer 仍能正确渲染 terminal SSE

这一步的意义是：

- streaming path 的 serializer threading 更接近单一来源
- route 继续从纯粹的 transport 透传样板中退出
- managed stream builder 和底层 stream session deps 的边界更加一致

边界：

- route 仍需要构造 `SingleAgentStreamSessionDeps`
- raw `ManagedSingleAgentSessionSpec` 仍允许缺失 transport，本轮只收 builder 主路径
- 如果继续推进，下一步可以考虑把 finalize lambda 与 deps 构造进一步抽成更高层 helper

### 11.114 2026-04-29 新进展：single-agent finalizer callback 装配已从 route 下沉

在 11.113 之后，single-agent route 侧还残留一段相当机械的 managed callback 样板：

- sync path 手工拼 `finalize_single_agent_session_success(...)` / `finalize_single_agent_session_failure(...)`
- stream path 手工拼 `finalize_single_agent_stream_success(...)` / `finalize_single_agent_stream_failure(...)`
- standalone 与 project 两条路径还在重复构造 memory extraction / failure summary / stream failure persistence 这些 callback 细节

这说明 route 虽然已经不再手工拼 managed spec，但仍深度了解 finalizer 级别的实现细节。

本轮继续把这层往 service 收：

- 新增 `single_agent_session_callbacks.py`
- 引入 `SingleAgentSessionSuccessCallbackDeps`
- 引入 `SingleAgentSessionFailureCallbackDeps`
- 引入 `SingleAgentStreamFailureCallbackDeps`
- 用 shared builder 生成 sync/stream success/failure callbacks
- standalone / project single-agent sync/stream route 改为复用这些 callback builders

这一步的意义是：

- single-agent route 更接近只负责准备 runtime input，而不是拼装 terminalization control flow
- managed session stack 的三层分工更清楚：session builder、callback builder、底层 finalizer
- 后续若继续收 memory extraction / stream failure persistence 等策略，落点会更集中在 service 层

边界：

- route 仍要提供 callback deps，尚未把这些 deps 再进一步收成更高层 policy object
- stream failure 的 `persist_stream_failure(...)` 适配仍由 route 注入
- 如果继续推进，下一步可以考虑把 success/failure deps 里的 memory extraction 与 stream failure persistence 再抽成共享 policy helper

### 11.115 2026-04-29 新进展：single-agent callback policy helper 已继续从 route 下沉

在 11.114 之后，route 虽然已经不再直接调用 finalizer，但 callback deps 里还残留两类重复策略：

- success path 重复拼 `asyncio.create_task(_extract_memories(...))`
- stream failure path 重复拼 `persist_stream_failure(...)` 的 message metadata / traceback adapter

这说明 callback builder 虽然已经出现，但 route 仍在承担一部分 callback policy 装配。

本轮继续把这层再收一格：

- `single_agent_session_callbacks.py` 新增 shared memory-extraction helper
- `single_agent_session_callbacks.py` 新增 shared stream-failure persistence helper
- standalone / project single-agent sync/stream route 改为复用这些 policy helpers
- focused tests 覆盖 fallback empty-response memory extraction 与 stream-failure persistence adapter

这一步的意义是：

- single-agent route 进一步退出 callback policy 细节
- memory extraction 与 stream-failure persistence 开始拥有统一的 policy surface，而不是散落在 route lambda 中
- callback builder service 更接近真正的 single-agent terminal policy assembly layer

边界：

- route 仍要提供 agent identity、user message、traceback builder 这些策略输入
- `_extract_memories(...)` 本身仍定义在 route 内，尚未下沉为独立 runtime service
- 如果继续推进，下一步可以考虑把 single-agent memory extraction scheduler 与 stream failure policy deps 再统一成更高层 profile/helper

### 11.116 2026-04-29 新进展：single-agent managed callback profile 已合并为成组 builder

在 11.115 之后，route 虽然已经不再直接拼 memory helper 和 stream-failure persistence helper，但还保留一层成对样板：

- sync path 仍分别构造 success callback deps 和 failure callback deps
- stream path 仍分别构造 success callback deps 和 failure callback deps
- standalone / project 两条路径都还在显式维护这两套 deps 组合

这说明 callback builder service 已经吸收了策略细节，但 route 仍在负责“如何把这些策略拼成一对 managed callbacks”。

本轮继续把这层组合责任移出 route：

- 新增 `SingleAgentManagedCallbackSet`
- 新增 `SingleAgentSyncCallbackProfile`
- 新增 `SingleAgentStreamCallbackProfile`
- 新增 `build_single_agent_sync_callbacks(...)`
- 新增 `build_single_agent_stream_callbacks(...)`
- standalone / project single-agent sync/stream route 改为通过 profile 一次性产出 managed callback pair

这一步的意义是：

- route 进一步退出 callback composition 细节
- single-agent callback service 从“零散 helper 集合”演进成更明确的 managed callback assembly layer
- 后续若继续抽更高层 session profile，已有稳定的 callback-set 抽象可以承接

边界：

- route 仍要提供 runtime-specific profile inputs，例如 agent identity、user message、traceback builder
- managed session spec builder 仍直接接收 `finalize_success` / `finalize_failure`，尚未直接消费 callback set
- 如果继续推进，下一步可以考虑让 managed session spec builder 直接接受 callback bundle，进一步压 route 参数面

### 11.117 2026-04-29 新进展：managed session builder 已直接接收 callback bundle

在 11.116 之后，single-agent route 虽然已经不再分别拼 success/failure deps，但仍有最后一层机械拆包：

- callback profile builder 先产出一组 managed callbacks
- route 再把这组 callbacks 拆成 `finalize_success` / `finalize_failure`
- managed session builder 再在内部重新组回 `ManagedSingleAgentSessionCallbacks`

这是一段纯粹的参数搬运，没有新增任何语义。

本轮把这层拆包去掉：

- `build_managed_single_agent_sync_session_spec(...)` 直接接收 callback bundle
- `build_managed_single_agent_stream_session_spec(...)` 直接接收 callback bundle
- callback profile builder 直接产出 orchestrator 使用的 `ManagedSingleAgentSessionCallbacks`
- standalone / project single-agent sync/stream route 改为把 callback bundle 直接传给 managed session builder

这一步的意义是：

- single-agent callback assembly 与 managed session builder 的契约正式对齐
- route 不再承担“拆 bundle 再传回去”的中间样板
- callback service 与 orchestrator 开始共享同一套 callback contract，而不是各自维护一层近似结果模型

边界：

- callback profile builder 仍然独立于 managed session builder，本轮没有把二者进一步合并
- route 仍要构造 stream deps / execute_turn 等 runtime input
- 如果继续推进，下一步可以考虑把 callback profile 与 managed session spec 再往更高层 session profile 合并

### 11.118 2026-04-29 新进展：memory extraction 已从 route 下沉为 shared service

在 11.117 之后，single-agent callback / session builder 已经基本成层，但还有一块 runtime behavior 仍直接定义在 route：

- `_extract_memories(...)` 本身仍在 `routes/api.py`
- single-agent callback profile 与 orchestration memory scheduling 都依赖这个 route-local async function
- 该实现里还存在一个实际 bug：prompt 组装错误引用了未定义的 `agent_name`

这说明 memory extraction 这条 runtime capability 还没有真正进入 service layer。

本轮把它独立出来：

- 新增 `services/memory_extraction.py`
- 抽出 message builder、response parser、memory persistence helper
- `extract_agent_memories(...)` 成为 single-agent 与 orchestration 共用的 shared runtime service
- 修正 extraction prompt 中的未定义变量问题，统一用 `agent_type` 标记 agent reply

这一步的意义是：

- route 进一步退出 runtime capability implementation 细节
- memory extraction 从“路由内匿名能力”变成可测试、可复用的 shared service
- single-agent 与 orchestration 的 memory scheduling 终于指向同一个底层实现，而不再依赖 route-local helper

边界：

- memory extraction 仍通过现有 LLM client 和数据库接口直接工作，尚未进一步纳入统一 runtime policy registry
- save-memory tool 与 auto extraction 还没有进一步收口到统一 memory write policy
- 如果继续推进，下一步可以考虑把 single-agent callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.119 2026-04-29 新进展：memory extraction scheduling 也已共享

在 11.118 之后，memory extraction 的核心实现虽然已经移到 service，但任务调度入口还残留两套写法：

- single-agent callback helper 内部单独包装 `asyncio.create_task(...)`
- orchestration route 里也各自写一层 `asyncio.create_task(extract_agent_memories(...))`

这意味着 memory extraction 这项能力虽然已经是 shared service，但其调度语义还没有真正统一。

本轮把这层调度也收回来：

- `memory_extraction.py` 新增 shared scheduling helper
- single-agent callback helper 改为复用该 scheduler
- orchestration memory scheduling lambda 改为复用同一个 scheduler

这一步的意义是：

- memory extraction 从“共享实现 + 分散调度”进一步演进成“共享实现 + 共享调度入口”
- single-agent 与 orchestration 在 memory scheduling 上开始真正共享同一条 helper 路径
- 后续如果需要给 memory extraction 增加 tracing / throttling / queueing，这一层已有统一挂点

边界：

- 当前 scheduler 仍是轻量的 task launcher，不包含限流或去重语义
- orchestration runner 自身的长度阈值判断仍保留在现有上层逻辑
- 如果继续推进，下一步可以考虑把 single-agent callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.120 2026-04-29 新进展：single-agent session contract model 已从 orchestrator 中拆出

在 11.119 之后，single-agent callback service 与 orchestrator 之间还残留一层结构性耦合：

- callback service 需要返回 orchestrator 使用的 `ManagedSingleAgentSessionCallbacks`
- 该 contract type 仍定义在 orchestrator 模块内
- 结果就是 callback service 必须反向 import orchestrator，只为了拿 contract dataclass

这类依赖方向不利于继续把 callback profile / session profile 再往上合并。

本轮先把 contract surface 独立出来：

- 新增 `single_agent_session_contracts.py`
- 将 `UnifiedSingleAgentSessionOutcome`
- `UnifiedSingleAgentSessionSpec`
- `ManagedSingleAgentSessionCallbacks`
- `ManagedSingleAgentStreamTransport`
- `ManagedSingleAgentSessionSpec`
  从 orchestrator 中抽到 shared contract module
- callback service 改为直接依赖 shared contract，而不再反向依赖 orchestrator

这一步的意义是：

- single-agent session stack 的 contract layer 与 execution layer 开始分离
- callback service / orchestrator / tests 可以围绕同一份 contract model 组织，而不是把 orchestrator 当成类型宿主
- 后续如果继续合并 callback profile 与 managed session profile，依赖方向会更干净

边界：

- orchestrator 仍然是这些 contract 的主要执行入口，本轮只拆了模型定义
- route 还没有直接消费 `single_agent_session_contracts.py`
- 如果继续推进，下一步可以考虑把 callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.121 2026-04-29 新进展：single-agent stream runner deps 已改为 builder 入口

在 11.120 之后，streaming single-agent 路径还保留一块明显的底层依赖面：

- route 仍直接构造 `SingleAgentStreamSessionDeps`
- focused tests 也直接依赖这一低层 dataclass 形状
- 这和前面已经逐步 builder 化的 unified spec、managed spec、callback profile 边界不一致

本轮把 stream runner deps 也收成 builder 入口：

- `single_agent_stream_session.py` 新增 `build_single_agent_stream_session_deps(...)`
- standalone / project single-agent streaming route 改为通过 builder 构造底层 deps
- stream-session / orchestrator focused tests 也切到 builder

这一步的意义是：

- route 进一步退出对 low-level streaming session dataclass shape 的直接依赖
- single-agent streaming path 的 contract surface 更接近前面已经 builder 化的其它层
- 后续如果继续把 callback profile 与 stream deps 再合并成更高层 session profile，变更面会更集中

边界：

- builder 当前主要是隐藏 dataclass 装配，本轮没有继续压缩参数数量
- single-agent sync path 仍没有对应的 low-level deps dataclass，因此这里只有 stream 路径收口
- 如果继续推进，下一步可以考虑把 callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.122 2026-04-29 新进展：single-agent callback profile 也已改为 builder 入口

在 11.121 之后，single-agent route 虽然已经不再直接 new stream runner deps，但 callback profile 这层还保留同类形态：

- route 仍直接构造 `SingleAgentSyncCallbackProfile`
- route 仍直接构造 `SingleAgentStreamCallbackProfile`
- callback tests 也仍然直接依赖这些 dataclass 形状

这与前面已 builder 化的 stream deps / managed spec / unified spec 边界仍然不一致。

本轮把 callback profile 也统一收成 builder 入口：

- `single_agent_session_callbacks.py` 新增 `build_single_agent_sync_callback_profile(...)`
- `single_agent_session_callbacks.py` 新增 `build_single_agent_stream_callback_profile(...)`
- standalone / project single-agent sync/stream route 改为通过 builder 构造 callback profile
- focused callback tests 也切到 builder

这一步的意义是：

- route 进一步退出对 callback profile dataclass shape 的直接依赖
- single-agent callback assembly layer 与 stream deps builder 一样开始提供稳定 builder surface
- 后续如果继续把 callback profile 与 stream session deps 合成更高层 session profile，builder 迁移成本更低

边界：

- builder 仍主要隐藏 dataclass 装配，本轮没有减少 callback profile 所需参数
- route 仍分别构造 callback profile 与 stream deps，两者尚未合并到统一 session profile
- 如果继续推进，下一步可以考虑把 callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.123 2026-04-29 新进展：managed session builder 已直接接受 callback profile

在 11.122 之后，route 已经不再直接 new callback profile dataclass，但还保留一段中间组合链：

- route 先 build callback profile
- 再调用 `build_single_agent_sync_callbacks(...)` / `build_single_agent_stream_callbacks(...)`
- 再把得到的 callback bundle 传给 managed session builder

这仍然是一层纯 assembly glue，而不是新的运行时语义。

本轮继续把这层 glue 收回 orchestrator：

- orchestrator 新增从 callback profile 直接构造 managed sync session spec 的 helper
- orchestrator 新增从 callback profile 直接构造 managed stream session spec 的 helper
- standalone / project single-agent sync/stream route 改为把 callback profile 直接传给 managed session builder
- focused orchestrator tests 覆盖 callback-profile 到 managed session spec 的组合链

这一步的意义是：

- route 再退出一层 callback assembly glue
- callback profile 开始真正成为 managed session builder 的上游输入，而不只是 route-local intermediate object
- 后续若继续把 callback profile 与 stream deps 再合并成更高层 session profile，orchestrator 已具备承接入口

边界：

- route 仍分别构造 callback profile 与 stream deps，两者尚未统一成单一 session profile
- sync / stream 两条路径仍然存在两套 profile-to-spec helper，而不是一个自动判别入口
- 如果继续推进，下一步可以考虑把 callback profile 与 stream session deps 再往更高层 session profile 合并

### 11.124 2026-04-29 新进展：managed single-agent session profile 已抬高一层

在 11.123 之后，route 虽然已经不再手工把 callback profile 转成 callbacks bundle，但仍然保留一段 spec-level glue：

- route 先构造 callback profile
- 再调用 orchestrator 的 profile-to-spec helper
- 再把结果传给 `run_managed_single_agent_sync_session(...)` 或 `iter_managed_single_agent_stream_session(...)`

这说明 route 还知道 managed session spec 这一层实现细节，而没有真正只面向更高层 session profile。

本轮继续把 single-agent 入口往上抬：

- orchestrator 新增 `ManagedSingleAgentSyncSessionProfile`
- orchestrator 新增 `ManagedSingleAgentStreamSessionProfile`
- 新增对应 profile builder
- 新增直接从 profile 运行 sync/stream session 的 helper
- standalone / project single-agent sync/stream route 改为直接构造 managed session profile 并执行

这一步的意义是：

- route 进一步退出 managed spec 组装与执行细节
- single-agent session stack 开始具备真正更高层的 session profile 入口，而不只是 callback profile 和 spec builder 的串联
- 后续如果继续把 callback profile 与 stream deps 做更高层默认化/模板化，这一层 profile runner 已经是稳定承接点

边界：

- route 仍分别构造 callback profile 与 stream deps，再交给 managed session profile builder
- sync / stream 仍是两套 profile 类型，而不是统一的单一 profile
- 如果继续推进，下一步可以考虑把 callback profile 与 stream deps 再进一步合并成更完整的 single-agent runtime profile

### 11.125 2026-04-29 新进展：managed single-agent session profile 已可直接从 runtime inputs 构造

在 11.124 之后，route 虽然已经面向 managed session profile 执行，但 still retained 一层装配：

- sync path 先 build callback profile
- stream path 先 build callback profile
- stream path 还要再 build stream deps
- 最后再把这些中间对象交给 managed session profile builder

这说明 route 虽然退出了 spec 层，但还在串装多个中间 profile/deps object。

本轮把这层装配继续收回 orchestrator：

- orchestrator 新增从 runtime inputs 直接构造 managed sync session profile 的 helper
- orchestrator 新增从 runtime inputs 直接构造 managed stream session profile 的 helper
- standalone / project single-agent sync/stream route 改为直接把 runtime inputs 传给这些 runtime-profile builder

这一步的意义是：

- route 再退出一层中间 object assembly
- callback profile 与 stream deps 开始真正被吸收到更高层 managed session profile builder 后面
- single-agent session stack 更接近“route 只提供 runtime inputs，service 负责装配 profile/spec/runner”的 Codex 风格边界

边界：

- runtime-profile builder 目前参数仍然较多，本轮主要解决装配层级，不是参数瘦身
- sync / stream 仍然各自维护一套 runtime-profile builder，而不是统一单入口
- 如果继续推进，下一步可以考虑把 sync/stream 共享的 runtime input 再抽成更高层公共 session runtime model

### 11.126 2026-04-29 新进展：single-agent shared runtime context 已抽出

在 11.125 之后，虽然 route 已经不再分别 build callback profile 与 stream deps，但 sync/stream runtime-profile builder 仍各自接一大串相同公共参数：

- `db`
- `task_run`
- `chatroom_id`
- `client_turn_id`
- `agent_id/agent_name/agent_type`
- `user_message`
- `save/publish/record`
- `message_metadata`
- `compact_summary/completion_summary/failure_summary`
- `extract_memories`

这是一块明显的 shared runtime state，但之前还没有统一模型承接。

本轮先把它单独提出来：

- orchestrator 新增 `SingleAgentSessionRuntimeContext`
- 新增 `build_single_agent_session_runtime_context(...)`
- sync / stream runtime-profile builder 改为消费 shared runtime context
- route 改为先构造 runtime context，再交给 sync/stream runtime-profile builder

这一步的意义是：

- sync / stream 的公共 runtime input 第一次拥有明确的共享模型
- route 不再把同一套公共参数在 sync/stream builder 之间来回重复传递
- 后续如果继续把 stream-specific inputs 再和 runtime context 组合成更高层统一 runtime profile，会更直接

边界：

- stream builder 仍保留自己特有的一组 execution inputs
- sync / stream 仍然是两套 runtime-profile builder，而不是完全统一成单一入口
- 如果继续推进，下一步可以考虑把 stream-specific execution inputs 也抽成更高层 single-agent runtime model

### 11.127 2026-04-29 新进展：single-agent stream execution context 已抽出

在 11.126 之后，sync / stream 共享 runtime context 已经收好，但 stream 路径还保留一条长参数面：

- `llm_client`
- `tools`
- `turn_state`
- `assemble_messages`
- `execute_tool`
- `build_llm_runtime_card`
- `snapshot_messages`
- `preview_tool_calls`
- `format_prompt_messages`
- `tool_result_success`
- `serialize_payload`
- `store_runtime_card`
- `public_runtime_card_payload`
- `max_turns`
- `on_tool_round`

这些本质上都是 stream-specific execution inputs，但之前还没有单独模型承接。

本轮把它们提成独立层：

- `single_agent_stream_session.py` 新增 `SingleAgentStreamExecutionContext`
- 新增 execution context builder
- orchestrator 的 stream runtime-profile builder 改为消费 `runtime context + execution context`
- standalone / project single-agent stream route 改为先构造 execution context，再交给 orchestrator

这一步的意义是：

- single-agent stream 入口终于不再直接把一长串 execution 参数传给 orchestrator
- stream-specific execution inputs 和 shared runtime context 开始形成更清晰的分层
- 后续如果继续往统一 single-agent runtime profile 演进，现在已经有了 `runtime context + execution context` 两个稳定构件

边界：

- sync 路径还没有对应的 execution context，因为它目前只有 `execute_turn`
- `runtime context + execution context` 仍然在 orchestrator 内二次组合，尚未进一步合成单一统一 runtime profile model
- 如果继续推进，下一步可以考虑把 sync/stream 的 runtime context 与 execution context 再往更高层统一 single-agent runtime profile 合并

### 11.128 2026-04-29 新进展：single-agent sync execution context 也已抽出

在 11.127 之后，stream 路径已经拥有 `execution context`，但 sync 路径还停留在更低层形态：

- route 仍直接把 `execute_turn`
- 以及可选 `on_empty`
  裸传给 orchestrator

这使得 sync / stream 在 runtime profile 形状上依然不完全对称。

本轮把 sync 也补齐：

- `single_agent_session_runner.py` 新增 `SingleAgentSyncExecutionContext`
- 新增 sync execution context builder
- orchestrator 的 sync runtime-profile builder 改为消费 `runtime context + sync execution context`
- standalone / project single-agent sync route 改为先构造 sync execution context，再交给 orchestrator

这一步的意义是：

- single-agent sync / stream 终于都拥有 `runtime context + execution context` 的对称入口形状
- route 进一步退出对 low-level execution fields 的直接装配
- 后续若继续往更高层统一 single-agent runtime profile 演进，sync / stream 现在已经拥有可对齐的两块输入模型

边界：

- sync execution context 仍然非常轻，只包含 `execute_turn` 与可选 `on_empty`
- sync / stream 还没有真正共享同一份 execution context 类型
- 如果继续推进，下一步可以考虑把 sync/stream 的 `runtime context + execution context` 再合并成更统一的 single-agent runtime profile model

### 11.129 2026-04-29 新进展：single-agent runtime profile runner 已抬为最高层入口

在 11.128 之后，single-agent sync / stream 已经拥有：

- shared runtime context
- sync/stream execution context

但 route 还保留一段中间跳转：

- 先构造 runtime profile
- 再交给 session-profile builder
- 再通过 session-profile runner 执行

这说明 route 虽然已经不直接碰 spec / callback / deps，但仍知道 session-profile 这层中间对象。

本轮继续把执行入口抬高：

- orchestrator 新增 `SingleAgentSyncRuntimeProfile`
- orchestrator 新增 `SingleAgentStreamRuntimeProfile`
- 新增直接运行 sync/stream runtime profile 的 helper
- standalone / project single-agent sync/stream route 改为直接构造并运行 runtime profile

这一步的意义是：

- route 进一步退出 single-agent session stack 的中间层细节
- single-agent sync / stream 终于都有明确的最高层 runtime profile runner 入口
- 后续如果继续把 sync/stream runtime profile 自身再统一一层，落点已经非常集中

边界：

- sync / stream runtime profile 仍是两套类型
- runtime profile 内部仍要再经过 session-profile/spec 组合链
- 如果继续推进，下一步可以考虑把 sync/stream runtime profile 再统一成更共享的 single-agent runtime model

### 11.130 2026-04-29 新进展：single-agent runtime profile 已可直接从 raw runtime inputs 构造

在 11.129 之后，route 虽然已经面向 runtime profile runner，但还保留最后一段 profile 前置装配：

- 先 build shared runtime context
- 再 build sync/stream execution context
- 再 build sync/stream runtime profile
- 最后交给 runtime profile runner

这说明 route 虽然已经脱离 session-profile/spec 层，但仍要显式串装 `context -> execution -> runtime profile` 这条链。

本轮继续把这层 builder 链藏到 orchestrator 后面：

- orchestrator 新增从 raw runtime inputs 直接构造 sync runtime profile 的 helper
- orchestrator 新增从 raw runtime inputs 直接构造 stream runtime profile 的 helper
- standalone / project single-agent sync/stream route 改为直接把原始 runtime inputs 交给这些 builder

这一步的意义是：

- route 进一步退出 single-agent runtime stack 的中间对象装配
- single-agent sync/stream 的高层使用面更接近“直接描述本轮运行输入”，而不是拼对象图
- 后续如果继续统一 sync/stream runtime profile，本轮已经把大部分 route-local assembly 清到了 orchestrator 内部

边界：

- sync / stream runtime profile 仍是两套类型
- orchestrator 内部仍保留 `runtime context + execution context + runtime profile` 这几个层级
- 如果继续推进，下一步可以考虑把 sync/stream runtime profile 再统一成更共享的 single-agent runtime model

### 11.131 2026-04-29 新进展：single-agent sync/stream 顶层 runtime profile 已统一

在 11.130 之后，route 已经不再手工拼 `runtime context` / `execution context`，但 orchestrator 顶层仍然保留两套 profile 类型：

- `SingleAgentSyncRuntimeProfile`
- `SingleAgentStreamRuntimeProfile`

并且它们都还要再经过一层 managed session profile 中转，才会落到最终 `ManagedSingleAgentSessionSpec`。

这说明 runtime profile 虽然已经抬到最高层入口，但其内部结构仍然留着 sync/stream 双轨。

本轮继续把这层双轨压掉：

- 统一为单一 `SingleAgentRuntimeProfile`
- 顶层 runtime profile 直接持有 `ManagedSingleAgentSessionSpec`
- 删除只做中转的 managed session profile 层
- sync / stream runner helper 继续保留，但现在都消费同一种顶层 runtime profile

这一步的意义是：

- single-agent sync / stream 在最高层 profile shape 上第一次真正统一
- orchestrator 内部减少了一整层只做组装转发的中间对象
- 后续如果继续压缩 raw-runtime builder 的参数面，落点会更集中在同一个顶层 runtime profile 上

边界：

- sync / stream 仍然保留各自的 builder 入口和 runner helper
- unified runtime profile 之下仍然会分别构造 sync execution / stream execution
- 如果继续推进，下一步可以考虑进一步压缩 raw-runtime builder 共享参数，或者把 sync/stream builder 收成更统一的入口

### 11.132 2026-04-29 新进展：single-agent runtime profile 顶层 builder core 已统一

在 11.131 之后，顶层 profile shape 虽然已经统一成 `SingleAgentRuntimeProfile`，但 orchestrator 内部还残留一块重复：

- sync raw-runtime builder 自己拼一套 runtime-profile 组装逻辑
- stream raw-runtime builder 也各自拼一套相似的 runtime-profile 组装逻辑

也就是说，profile shape 统一了，但真正的顶层 builder core 仍是双轨。

本轮继续把这层收紧：

- orchestrator 新增统一 `build_single_agent_runtime_profile(...)`
- sync / stream raw-runtime builder 退化为围绕该核心 builder 的薄包装

这一步的意义是：

- single-agent runtime profile 的最高层 contract 和最高层 builder 都开始统一
- orchestrator 内部进一步减少 sync/stream 平行实现
- 后续如果要继续压缩 raw-runtime builder 的共享参数，这个 unified builder core 会是更清晰的落点

边界：

- sync / stream raw-runtime builder 对外仍然保留两套入口
- unified builder core 之下仍需分别适配 sync execution / stream execution 的差异
- 如果继续推进，下一步可以考虑进一步统一 sync/stream raw-runtime builder 的参数分组或引入更高层 shared input bundle

### 11.133 2026-04-29 新进展：single-agent raw runtime inputs 已抽成共享 bundle

在 11.132 之后，single-agent 顶层 runtime profile builder core 虽然已经统一，但 sync/stream raw-runtime builder 仍各自接一大串重复参数：

- `db/task_run/chatroom_id/client_turn_id`
- `agent_id/agent_name/agent_type`
- `user_message`
- `save/publish/record`
- `message_metadata`
- `compact_summary/completion_summary/failure_summary`
- `extract_memories`

这些本质上就是更原始的一层 shared runtime input，但此前还没有单独模型承接。

本轮把它再往前提一格：

- 新增 `SingleAgentRawRuntimeInputs`
- 新增对应 builder
- sync / stream raw-runtime builder 改为消费这个 shared raw input bundle
- route 也改为先构造 raw runtime inputs，再交给 sync/stream raw-runtime builder

这一步的意义是：

- single-agent raw-runtime builder 的公共参数面第一次被显式建模
- route 继续退出大段重复参数传递
- 后续如果要进一步统一 sync/stream raw-runtime builder，自然的落点已经从“长参数列表”变成了“shared raw input bundle”

边界：

- sync / stream raw-runtime builder 仍分别保留自己的 execution-specific 参数
- shared raw input bundle 之上还没有进一步形成更统一的 top-level builder 参数对象
- 如果继续推进，下一步可以考虑进一步统一 sync/stream raw-runtime builder 的 execution-specific 参数分组

### 11.134 2026-04-29 新进展：shared raw-runtime bundle 已成为 single-agent route 入口公共面

在 11.133 之后，虽然 shared raw-runtime bundle 已经存在，但 route 仍然要各自把那一长串公共参数原样透传给 sync/stream raw-runtime builder。

这说明 shared bundle 被定义出来了，但还没有真正成为高层入口的主参数面。

本轮继续把这层切换完成：

- sync raw-runtime builder 改为直接接收 `SingleAgentRawRuntimeInputs`
- stream raw-runtime builder 也改为直接接收 `SingleAgentRawRuntimeInputs`
- standalone / project single-agent sync/stream route 改为显式构造 shared raw-runtime bundle，再交给 builder

这一步的意义是：

- single-agent route 终于拥有一套明确的共享高层输入面，而不是继续传长参数列表
- sync / stream raw-runtime builder 的公共输入 contract 首次真正一致
- 后续如果继续统一 execution-specific 参数，就可以在 `raw-runtime bundle + execution-specific inputs` 这两个层面上干净推进

边界：

- sync / stream raw-runtime builder 仍各自持有自己的 execution-specific 参数
- shared raw-runtime bundle 还没有进一步和 execution-specific inputs 合并为单一顶层 request model
- 如果继续推进，下一步可以考虑进一步统一 sync/stream execution-specific 参数分组

### 11.135 2026-04-29 新进展：shared raw execution input envelope 已落地

在 11.134 之后，single-agent route 已经共享 `SingleAgentRawRuntimeInputs`，但 execution-specific 入参仍保持两套裸列表：

- sync path 仍直接传 `execute_turn/on_empty`
- stream path 仍直接传 `llm_client/tools/turn_state/...`

这意味着公共 runtime 输入已经统一，但 execution-specific 输入还没有形成对称的高层 contract。

本轮继续把这层提成 shared envelope：

- 新增 `SingleAgentRawExecutionInputs`
- sync runner 侧新增 raw sync execution input bundle
- stream runner 侧新增 raw stream execution input bundle
- sync / stream raw-runtime builder 改为统一接收 `SingleAgentRawExecutionInputs`

这一步的意义是：

- single-agent sync / stream 在 raw-runtime builder 输入层终于都变成 `raw runtime inputs + raw execution inputs`
- route 继续退出 execution-specific 长参数传递
- 后续如果继续统一 sync/stream builder，就能直接围绕这两个共享 envelope 推进，而不是围绕两套零散参数

边界：

- `SingleAgentRawExecutionInputs` 当前仍是一个 envelope，内部依赖 sync/stream 各自的 raw execution input 类型
- execution-specific 参数本身还没有被进一步抽象成更共享的字段层
- 如果继续推进，下一步可以考虑进一步统一 sync/stream raw execution input 的参数组织方式

### 11.136 2026-04-29 新进展：shared raw execution envelope 已成为 single-agent route 入口公共面

在 11.135 之后，`SingleAgentRawExecutionInputs` 已经存在，但 route 仍各自把 execution-specific 参数原样透传给 sync/stream raw-runtime builder。

也就是说，execution envelope 被定义出来了，但还没有真正成为高层入口的主参数面。

本轮继续把这层切换完成：

- sync raw-runtime builder 改为直接接收 `SingleAgentRawExecutionInputs`
- stream raw-runtime builder 也改为直接接收 `SingleAgentRawExecutionInputs`
- standalone / project single-agent sync/stream route 改为显式构造 shared raw execution envelope，再交给 builder

这一步的意义是：

- single-agent sync / stream 的 raw-runtime builder 终于都变成 `raw runtime inputs + raw execution inputs`
- route 进一步退出 execution-specific 长参数传递
- 后续如果继续统一 execution-side 参数组织，可以直接围绕 shared execution envelope 推进，而不再围绕散落的参数列表

边界：

- `SingleAgentRawExecutionInputs` 之下仍依赖 sync/stream 各自的 raw execution input 类型
- execution-side 还没有进一步合并成更共享的字段模型
- 如果继续推进，下一步可以考虑收 execution envelope 之下的 sync/stream 参数组织方式

### 11.137 2026-04-29 新进展：stream failure-specific 参数已收成独立 policy bundle

在 11.136 之后，single-agent sync / stream 已经都切到 `raw runtime inputs + raw execution inputs`，但 stream 侧还残留最后一组独有参数：

- `failure_agent_name`
- `failure_agent_id`
- `detail_builder`
- `final_message_saved`
- `empty_response_text`

这些不属于 execution 本体，更像 stream terminalization / failure handling policy。

本轮把它们提成独立层：

- 新增 `SingleAgentStreamFailurePolicy`
- 新增对应 builder
- standalone / project single-agent stream route 改为显式构造 failure policy，再交给 stream raw-runtime builder

这一步的意义是：

- stream 路径的剩余特有参数终于不再散落在 builder 参数面里
- single-agent builder 输入开始更清楚地分成三层：runtime inputs、execution inputs、failure policy
- 后续如果继续统一 execution-side 参数组织，stream 特有的 failure concerns 已经从 execution 维度中分离出来

边界：

- stream failure policy 仍是 stream 专属，sync 没有对应的 policy bundle
- sync / stream raw execution inputs 之下的字段组织仍未进一步统一
- 如果继续推进，下一步可以考虑继续压缩 sync/stream execution-specific 字段分组

### 11.138 2026-04-29 新进展：stream failure policy 已成为 raw-runtime 入口公共面

在 11.137 之后，`SingleAgentStreamFailurePolicy` 已经存在，但 stream raw-runtime builder 仍把这组 policy 参数在入口上拆开接收。

也就是说，failure policy 被建模出来了，但还没有真正成为高层 builder 的主输入之一。

本轮继续把这层切换完成：

- stream raw-runtime builder 改为直接接收 `SingleAgentStreamFailurePolicy`
- standalone / project single-agent stream route 改为显式构造 failure policy，再交给 builder

这一步的意义是：

- single-agent stream 的高层输入面更明确地稳定为三块：raw runtime inputs、raw execution inputs、failure policy
- route 进一步退出 stream failure-specific 参数的长列表传递
- 后续如果继续统一 execution-side 字段组织，failure concerns 已经从 builder 参数面中独立出来

边界：

- failure policy 目前仍然只服务 stream path
- sync / stream 在 execution-specific 字段组织上依旧存在差异
- 如果继续推进，下一步可以考虑继续压缩 sync/stream execution-specific 字段分组

### 11.139 2026-04-30 新进展：stream execution 已拆出 loop/transport 子分组

在 11.138 之后，stream raw execution envelope 已经形成，但其内部仍是一大块混合字段：

- 一部分描述 turn loop 行为
- 另一部分描述 transport / runtime-card persistence 行为

这会让后续继续收 execution-specific 字段时，修改面还是偏大。

本轮先在 service 层把它再切一刀：

- 新增 `SingleAgentStreamLoopCallbacks`
- 新增 `SingleAgentStreamTransportContext`
- 用 focused tests 覆盖这两个子分组的基本投影行为

这一步的意义是：

- stream execution 内部开始出现更细的结构边界
- 后续如果要继续把 stream execution envelope 进一步收口，可以直接围绕 loop / transport 两块独立推进
- 这一步先只落在 service/test 层，风险低

边界：

- route 和 orchestrator 还没有直接消费这两个子分组
- stream raw execution envelope 本身的对外 shape 暂时未变
- 如果继续推进，下一步可以考虑让 raw stream execution inputs 直接以内部分组为输入，而不是继续保持扁平参数面

### 11.140 2026-04-30 新进展：raw stream execution 已直接投影到 loop/transport 子分组

在 11.139 之后，stream execution 内部虽然已经拆出 `loop callbacks` 与 `transport context`，但 raw execution envelope 还只是扁平参数的另一层包装。

这说明 service 内部分组已经存在，但还没有真正成为 raw execution input 的主结构。

本轮把这层也切换完成：

- `SingleAgentStreamRawExecutionInputs` 直接持有 `SingleAgentStreamLoopCallbacks`
- 同时持有 `SingleAgentStreamTransportContext`
- orchestrator 的 raw stream execution envelope 改为先构造这两个子分组，再投影到 raw execution input

这一步的意义是：

- stream execution 的内部结构开始真正反映到 raw input contract 上
- 后续如果继续统一 execution-specific 参数，就能围绕更稳定的 `loop/transport` 两块继续推进
- route 仍保持不变，因为 envelope helper 已经把这层细节完全藏在 service/orchestrator 内部

边界：

- sync execution 仍只有较轻的一组字段，没有对应的子分组
- raw stream execution input 对外虽然更结构化，但 sync/stream 执行层仍未统一成同一种字段模型
- 如果继续推进，下一步可以考虑继续抽 sync execution 的子结构，或者统一 execution envelope 的更高层 contract

### 11.141 2026-04-30 新进展：stream raw execution envelope 已直接接收 loop/transport 子分组

在 11.140 之后，`SingleAgentStreamRawExecutionInputs` 虽然已经内部持有 `loop/transport` 两块，但 orchestrator 对外的 raw execution envelope 入口还维持着扁平参数列表。

这意味着 service 内部结构已经变了，但高层调用面还没有同步收紧。

本轮继续把这层切换完成：

- orchestrator 的 `build_single_agent_stream_raw_execution_envelope(...)`
  直接接收 `SingleAgentStreamLoopCallbacks`
  与 `SingleAgentStreamTransportContext`
- focused stream/orchestrator/API 回归继续验证行为兼容

这一步的意义是：

- stream execution 的高层输入面和底层结构终于真正对齐
- route 以及后续 builder 不再需要关心 transport/loop 字段如何扁平展开
- 后续如果继续统一 sync/stream execution envelope，stream 侧已经有更清楚的稳定结构

边界：

- sync execution 仍然没有对应的更细子分组
- sync/stream execution envelope 的整体 shape 还没有完全统一
- 如果继续推进，下一步可以考虑是否值得为 sync execution 也抽更细子结构，或者直接统一 envelope 级别的 contract

### 11.142 2026-04-30 新进展：single-agent stream route 已切到 loop/transport 子分组入口

在 11.141 之后，service 与 orchestrator 入口虽然都已经切到 `loop/transport` 子分组，但 route 侧还没有直接采用这组高层 contract。

这意味着 stream execution 的结构边界已经存在，但 single-agent route 仍然在沿用上一层的扁平调用习惯。

本轮把 route 这一层也补齐：

- standalone single-agent stream route 改为显式构造 `SingleAgentStreamLoopCallbacks`
- project single-agent stream route 也改为显式构造 `SingleAgentStreamTransportContext`
- focused orchestrator tests 同步改成围绕 route 级子分组入口验证行为兼容

这一步的意义是：

- stream execution 从 route 到 orchestrator 到 service 三层，终于使用同一组结构边界
- single-agent route 不再把 loop callbacks / transport callbacks 以扁平参数列表展开
- 后续如果继续统一 sync/stream execution-side contract，stream 这条链路已经具备稳定的 route-level shape

边界：

- sync execution 仍没有对应的子分组入口
- route 侧虽然已经不再扁平展开 stream loop/transport 参数，但 sync/stream execution envelope 仍是两套模式
- 如果继续推进，下一步可以开始看 multi-agent runtime 的 subagent lifecycle 是否也值得做类似 contract 收口

### 11.143 2026-04-30 新进展：subagent lifecycle 已开始消费 scheduler runtime state

在 11.142 之后，single-agent 的 route/runtime 收口已经比较稳定，但和 Codex 的真实差距仍主要落在 multi-agent runtime。

其中最直接的一块问题是：

- `subagent_lifecycle` 还主要是根据事件类型做投影
- 它虽然能看出 `spawned/running/completed/failed/cancelled`
- 但还拿不到 scheduler runtime 已经知道的更细状态
  - `step_state.status`
  - `released_by_step_id`
  - `dispatch_count`
  - `completion_count`
- recovery rebuild 虽然已有 `scheduler_recovery_state_rebuilt`
  - 但 lifecycle 还不能直接从其中的 runtime snapshot 重建 child state

这会导致 checkpoint 里的 subagent 视图仍偏“事件汇总”，而不是更接近 runtime-native child state。

本轮把这层先推进一步：

- `subagent_lifecycle` 开始显式消费 scheduler `step_state`
- 同时消费 scheduler `runtime.steps`
- recovery rebuild 事件现在能直接重建 subagent runtime state
- cancel 事件也开始保留：
  - `previous_status`
  - `cancelled_by`
  - `note`
  这些治理上下文

这一步的意义是：

- checkpoint snapshot 中的 subagent 视图更接近真实 scheduler runtime
- lifecycle 不再只知道“谁终态了”，也开始知道“它是如何被释放/调度/取消的”
- 这为后续继续往 Codex 风格 child-runtime 模型推进，补上了更稳定的 runtime-facing contract

边界：

- 这仍然是基于 persisted runtime/event payload 的重建，不是独立的 child runtime object
- 还没有真正的 wait/close/cancel primitive，也没有 owner-scoped subagent handle
- 但 compared to 之前纯事件型投影，checkpoint 里的 subagent lifecycle 已经明显更 runtime-native

### 11.144 2026-04-30 新进展：checkpoint 已开始显式投影 subagent runtime handles

在 11.143 之后，checkpoint 里的 `subagent_lifecycle` 已经明显更 runtime-native，但它本质上仍是一组状态记录。

也就是说：

- 它知道 child step 当前是 `spawned/running/completed/...`
- 也开始知道 `scheduler_status/released_by_step_id/dispatch_count/...`
- 但它还没有形成更接近 Codex 的“child handle / control contract”
  - 当前能不能 `wait`
  - 当前能不能 `cancel`
  - 当前是在等依赖、等调度，还是等完成

如果没有这层 contract，后续就算要补 runtime-native `wait/close/cancel` primitive，也还要再从 lifecycle 字段里重新推断一遍控制面语义。

本轮把这层先投影出来：

- 在 lifecycle 之上新增 `subagent handles` projection
- 每个 handle 现在显式带有：
  - `control_state`
    - `await_dependency`
    - `await_dispatch`
    - `await_completion`
    - 以及各类 terminal states
  - `awaitable`
  - `cancellable`
  - `available_actions`
    - 当前先投影 `wait`
    - 和 `cancel`
- task-run checkpoint / detail 开始携带：
  - `subagent_handles`
  - `subagent_handles_summary`

这一步的意义是：

- checkpoint 不再只描述 child state，也开始描述 child control surface
- 后续如果继续补真实 runtime primitive，就有一层稳定的 control-facing contract 可复用
- cancel endpoint 也开始和这层 handle contract 对齐，而不是继续单独按 lifecycle 状态做过滤

边界：

- `wait` 现在仍只是 control contract 上的显式 capability，不是独立 API
- `close` primitive 仍不存在
- handle 仍来自 checkpoint 重建，不是长期存活的 runtime object
- 但 compared to 之前只有 lifecycle state，现在已经更接近 Codex 风格的 child handle 形状

### 11.145 2026-04-30 新进展：subagent handle 已开始暴露独立控制端点

在 11.144 之后，checkpoint 里已经有了 `subagent handles`，但它仍然只是 detail/monitor 可见数据。

这意味着：

- child handle 的 control contract 已经出现
- 但外部还不能直接围绕 handle 做控制
- route 侧仍只有 coarse-grained 的：
  - `resume task run`
  - `cancel task run`

这和 Codex 风格的 child-runtime / handle-oriented control 面还有明显差距。

本轮先补一层保守但有用的 API：

- 新增 `GET /api/task-runs/{task_run_id}/subagents`
  - 直接暴露当前 task run 的 lifecycle + handle projection
- 新增 `POST /api/task-runs/{task_run_id}/subagents/{step_id}/cancel`
  - 支持按 handle 粒度取消单个 subagent
- 语义保持保守：
  - 单 handle cancel 默认不直接终结整个 task run
  - 只有当最后一个可取消 handle 被取消时，才级联终结 task run

这一步的意义是：

- subagent handle 首次从“checkpoint 数据结构”变成“独立 control endpoint”
- child-level cancel 不再必须借道整个 task run 的 coarse cancel
- route/control 面开始具备更接近 Codex child handle 的操作边界

边界：

- 目前仍只有 `cancel` 动作，没有 `wait` / `close`
- `wait` 仍只是 handle contract 里的显式 capability，还未成为 API
- 取消动作底层仍是补 ledger event + 重建 checkpoint，不是真正的 executor-native child cancel primitive
- 但 compared to 之前只有 task-run 级 cancel，现在已经向 child-handle-oriented control 面前进了一步

### 11.146 2026-04-30 新进展：subagent handle 已开始暴露 wait observation contract

在 11.145 之后，subagent handle 已经有独立 `cancel` endpoint，但 `wait` 还只是 handle contract 中的可用动作标签。

这意味着：

- child-level control 面已经从 coarse task-run control 前进了一步
- 但对于 “我现在能不能继续等这个 child，还是它已经变化/终结了” 这种最基础的 handle wait 语义，外部仍没有稳定接口

如果直接跳到真正 blocking / long-poll / executor-native wait primitive，改动面会很大，也会把 route / runtime / recovery 一起卷进去。

因此本轮先做保守收口：

- 新增 `GET /api/task-runs/{task_run_id}/subagents/{step_id}/wait`
- 它不是 blocking primitive
- 而是一个 poll-style observe contract
  - 接收 `since_event_index`
  - 返回 handle 当前状态
  - 返回自该 cursor 之后是否发生状态变化
  - 以及建议继续 poll 还是立即处理
- 同时 handle projection 现在开始显式携带 `last_event_index`

这一步的意义是：

- `wait` 首次从“handle 的文字能力”变成“可调用的 control contract”
- 后续如果要升级成 long-poll / SSE / executor-native wait，不必重新设计 API shape
- handle control 面开始具备：
  - `list`
  - `cancel`
  - `wait-observe`
  这三块最基础的 child-handle primitive

边界：

- 当前 `wait` 仍不阻塞，也不持有 lease
- 还没有 timeout / wakeup / notification push 语义
- 也还没有 owner-scoped wait token
- 但 compared to 之前只有 `cancel` 和只读 projection，现在已经把 child wait contract 也显式固定下来了

### 11.147 2026-04-30 新进展：subagent handle 已开始暴露 close contract

在 11.146 之后，child-handle control 面已经具备：

- `list`
- `cancel`
- `wait-observe`

但还有一个明显缺口：

- terminal child handle 仍然只能一直挂在 active handle 集里
- 外部无法显式表达“这个 child 我已经处理完，不必再作为活跃 control target 展示”

如果没有 `close`，child handle 虽然有 terminal state，但还缺少一个非常典型的 handle lifecycle 末端动作。

本轮先补一个保守版本：

- handle projection 新增：
  - `closed`
  - `closed_at`
  - `closed_by`
  - `close_note`
- 新增 `POST /api/task-runs/{task_run_id}/subagents/{step_id}/close`
- 语义目前刻意收窄：
  - 只允许 `completed/failed` terminal handle 被 close
  - `cancelled` handle 仍默认视为已经退出 active set
    - 不再要求显式 close

这一步的意义是：

- child handle 第一次拥有了显式的 terminal-archive 动作
- handle lifecycle 开始形成更完整的 control surface：
  - list
  - wait
  - cancel
  - close
- monitor / detail / route 后续都可以围绕 “open terminal handle vs closed terminal handle” 做更清晰的展示和过滤

边界：

- `close` 现在仍只是 projection + ledger 事件驱动，不会影响 scheduler 内核
- 还没有真正的 active/archived handle store
- `cancelled` handle 暂时不暴露 close，保持旧语义兼容
- 但 compared to 之前 terminal handle 只能被动悬挂，现在 child-handle lifecycle 已经更接近一个完整的 Codex-style control model

### 11.148 2026-04-30 新进展：subagent handle wait 已升级到 bounded long-poll contract

在 11.147 之后，child handle control surface 已经有：

- `list`
- `wait-observe`
- `cancel`
- `close`

但 `wait` 还只是一个即时快照接口：

- 只能返回当前状态
- 调用方必须自己反复 polling
- route 侧没有任何 bounded blocking 语义

这和 Codex 风格的 handle wait primitive 仍然有明显距离，哪怕还不做真正的 executor-native wait，也至少应该先把 API contract 稳定到“支持有限等待”。

本轮先补最小升级：

- `GET /api/task-runs/{task_run_id}/subagents/{step_id}/wait`
  开始支持 `timeout_ms`
- route 会在限定时间内反复重建 checkpoint
  - 如果 handle 状态变化，立即返回
  - 如果超时仍未变化，返回 `timed_out=true`
  - 并把 `suggested_poll` 明确为 `timeout`
- timeout 统一经过 bounded normalization helper
  - 当前上限 5s

这一步的意义是：

- `wait` 从“纯快照查询”推进到“bounded long-poll contract”
- 后续如果继续升级到 lease-based wait / notification push / executor-native wakeup，不需要重做 API shape
- child handle control 面已经开始具备更接近 runtime primitive 的时间语义

边界：

- 当前实现仍是 route 层轮询 checkpoint，不是 runtime-native wakeup
- 还没有 wait token / owner / lease
- 也没有 SSE / websocket push
- 但 compared to 之前的即时查询，`wait` 已经开始具备真正的阻塞观察语义

### 11.149 2026-04-30 新进展：已明确 Runtime Policy / Workflow Spec / Evaluation Rubric / Final Approval 的语义边界

随着 child-handle control 面逐步补齐，一个更根本的问题开始变得明显：

- 讨论中经常把行为层语义和实现层术语混在一起
- 例如：
  - `root runtime`
  - `agent session`
  - `pipeline`
  - `approval`
  这些词有时被当成产品行为词，有时又被当成代码实现词

这会导致架构讨论容易跑偏：

- 一旦直接用实现词讨论行为模式
- 就会把“谁理解内容、谁控制流程、谁做最终裁决”混成一团

本轮先把语义层明确成四块：

1. `Runtime Policy`
   - 本地软件主导
   - 负责 lifecycle legality、approval、recovery、handle control
2. `Workflow Spec`
   - LLM 可生成/适配，本地软件执行
   - 负责阶段、角色、gate、artifact、rollback、timeout
3. `Evaluation Rubric`
   - LLM 主导，必要时人兜底
   - 负责内容质量、方案优劣、blocker 判断、风格与品味
4. `Final Approval`
   - 人或未来的用户代理主导
   - 负责高风险、高偏好、最终放行

这一步的意义是：

- 后续再讨论“数字分身”“任务管理者”“阶段 owner”时，不必再直接套实现词
- 也能更清楚地区分：
  - Agent 在判断什么
  - 软件在判断什么
  - 哪些变化应该落在 workflow spec
  - 哪些模糊问题应该落在 rubric / approval

边界：

- 这一步主要是架构语义澄清，不是新的 runtime feature
- 但它会直接影响后续是否要把 stage runtime / twin-agent / owner policy 做成一等实体

### 11.150 2026-04-30 新进展：已明确软件执行器 / 编排 Agent / 工作 Agent 的输入输出 contract

在 11.149 明确四层语义边界之后，另一个容易混淆的问题也被暴露出来：

- 讨论时很容易把“软件执行器收到脚本”当成统一说法
- 但这会把：
  - bounded workflow spec
  - worker artifact
  - action request
  这三类完全不同的输入混成同一种东西

这会让边界再次变模糊：

- 编排 Agent 到底是在给流程定义，还是在直接控制 runtime？
- 工作 Agent 到底是在产出业务内容，还是在直接推进状态机？

本轮把三方 contract 固定成更精确的语言：

1. `本地软件执行器`
   - 接收 bounded workflow/control spec
   - 接收 artifact 与 action request
   - 负责解释、裁定、推进、落盘
2. `编排 Agent`
   - 不直接操作 runtime state
   - 负责给出 bounded execution spec
   - 如阶段、角色、路由、rubric 建议
3. `工作 Agent`
   - 不直接推进状态机
   - 负责产出 artifact
   - 并发出 bounded action request

这一步的意义是：

- 后续讨论“数字分身”和“任务管理者”时，不会再把它们误认为 runtime kernel
- 也更容易判断某个新能力应落在哪一侧：
  - spec
  - artifact
  - request
  - state transition

边界：

- 这一步仍是架构语言收敛
- 还没有把 workflow spec / action request 建成统一 schema
- 但已经把讨论词汇从“脚本”收敛成更适合长期演进的 contract 语言

### 11.151 2026-04-30 新进展：已明确“开放语义、收敛协议、稳定内核”的扩展原则

在 11.150 之后，三方 contract 已经比较清楚，但如果继续往下演进，仍有一个高风险点：

- 很容易把 LLM 的开放输出能力，错误地映射成软件层也应无限开放
- 最终把系统做成“每来一个新事务，就加一套新流程代码”的形态

这会直接走回传统软件的老路：

- 软件项目一套实现
- 视频项目一套实现
- UI 项目再一套实现
- 每一类任务都扩一个新的 state machine 和专用 API

这不是我们要的方向。

本轮把原则进一步收敛为：

- `开放语义`
  - 留给 LLM
- `收敛协议`
  - 压缩成 bounded `workflow spec / action request / artifact contract`
- `稳定内核`
  - 留给本地软件执行器

也就是说：

- 软件层不应试图“理解所有事务”
- 软件层应试图“解释一组稳定协议”

这一步的意义是：

- 为后续跨领域扩展设定清晰方向
- 避免把 Catown 做成“一事务一实现”的业务拼盘
- 也为下一步真正该抽的一等 schema 指明了优先级：
  1. workflow spec schema
  2. action request schema
  3. artifact schema

边界：

- 这一步仍是架构原则收敛，不是新的 runtime 代码
- 但它会直接影响后续内核应该新增什么 primitive，以及什么应该只作为协议扩展解决

### 11.152 2026-05-06 新进展：`action_request schema v1` 已完成第一版草案

在 11.151 确立“开放语义、收敛协议、稳定内核”之后，最自然的下一步就是把其中一个高优先级协议真正落成草案。

本轮先选择 `action_request schema`，原因很直接：

- 现有系统里已经有很多“像 request 但还不是统一 schema”的东西
  - blocked tool request payload
  - pipeline gate request payload
  - tool / approval / rollback 相关 event payload
- 如果不先把 agent -> executor 的请求边界收紧，后续 artifact schema 和 workflow schema 都会继续漂

本轮产出：

- 新增 `docs/Schema-Action-Request-v1.md`
- 新增 `backend/services/action_request_contracts.py`
- 定义 v1 支持的 bounded request kinds：
  - `use_tool`
  - `ask_agent`
  - `request_approval`
  - `report_blocker`
  - `suggest_rollback`
  - `publish_artifact`

这一步的意义是：

- 终于把“agent 向软件执行器发的请求”从隐式 payload 收敛成可验证合同
- 也把“OpenAI 协议”与“Catown 内部运行时协议”之间的边界具体化
- 后续开始把现有 approval/tool/blocker payload 映射到统一 contract 时，有了明确目标

边界：

- v1 目前还是草案 contract，不代表 runtime 已全量接入
- 还没有把现有 implicit payload 全部编译/迁移到它上面
- 但这是把内部协议从概念推进到实现骨架的重要第一步

### 11.153 2026-05-06 新进展：`artifact_contract schema v1` 已完成第一版草案

在 11.152 之后，agent -> executor 的 request 边界已经有了第一版 contract，但另一条同样关键的边界仍然偏弱：

- 当前系统里的“产物”还分散在：
  - `expected_artifacts`
  - `StageArtifact`
  - `Asset`
- 它们虽然都在表达 artifact，但彼此并没有统一的 typed contract

这会带来两个后果：

- action request 里的 `publish_artifact` 只能先携带一个简化 payload
- runtime 很难把 pipeline 文件产物和 richer project asset 当成同一种协议对象看待

本轮先补 `artifact_contract schema v1` 草案：

- 新增 `docs/Schema-Artifact-Contract-v1.md`
- 新增 `backend/services/artifact_contracts.py`
- 新增四种 bounded artifact modes：
  - `workspace_file`
  - `workspace_directory`
  - `document`
  - `structured_asset`

这一步的意义是：

- 终于把“产物本身”从文件命名约定推进到 typed contract
- 也让 action request 与 artifact 之间的关系更清楚：
  - `publish_artifact` 表达 intent
  - `artifact_contract` 表达被发布的对象
- 后续开始把 `StageArtifact` 与 `Asset` 收到统一协议层时，有了明确目标

边界：

- v1 目前仍是草案 contract，不代表现有 artifact 写路径已切换
- 还没有接 supersession / approval / dependency graph
- 但这是把 artifact 从隐式文件约定推进到一等协议对象的重要一步

### 11.154 2026-05-06 新进展：已开始把 blocked-tool implicit payload 映射到 `action_request schema v1`

在 11.152 起草 `action_request schema v1` 之后，下一步不应该继续空谈，而应该先选一条现有 implicit payload 最清楚的路径做兼容映射。

本轮选择：

- blocked tool
- approval queue request payload

原因：

- 这条链已经相对稳定
- payload 字段集中
- 与 `request_approval` action kind 的语义最贴近

本轮没有直接改 queue item 持久化格式，而是先补一层兼容桥：

- 在 `approval_replay.py` 中新增 helper
- 把 blocked-tool approval intent 编译成 `request_approval` action request
- 用 focused tests 锁定输出形状

这一步的意义是：

- 开始把“隐式 payload”真正往统一协议迁移，而不是只停留在 schema 文档阶段
- 同时又避免一次性改掉 queue persistence/replay 语义，降低了迁移风险

边界：

- 当前仍只是 compatibility bridge
- queue item 里保存的 request payload 还没有全面切到 action-request envelope
- 但这已经是从“有 schema 草案”走向“有协议收敛路径”的第一步

### 11.155 2026-05-06 新进展：已开始把 pipeline-gate implicit payload 映射到 `action_request schema v1`

在 11.154 先打通 blocked-tool -> `request_approval` 兼容桥之后，另一条语义上同样接近的路径也该尽快对齐：

- pipeline gate approval

原因很简单：

- 它本质上也是“请求审批”
- 只是 target 不再是 tool，而是 `pipeline_gate`
- 如果这条链继续保留另一套独立 payload 语言，schema 收敛很快又会分叉

本轮继续沿同一策略推进：

- 不直接改 queue item persistence
- 不改 pipeline engine 的主执行路径
- 先在 `approval_replay.py` 中补 compatibility helper
- 把 pipeline-gate approval intent 编译成统一的 `request_approval` action request 形状

这一步的意义是：

- `request_approval` action kind 第一次同时覆盖：
  - blocked tool
  - pipeline gate
- 说明这个 schema 不是单点 patch，而是真开始承接一类 runtime intent

边界：

- 仍然只是 compatibility bridge
- 现有 queue persistence / replay 主链尚未切到统一 envelope
- 但 action-request 收敛路径已经从单条样例扩展成两条真实业务路径

### 11.156 2026-05-07 新进展：`workflow_spec schema v1` 已完成第一版草案

在 11.151 里明确“开放语义、收敛协议、稳定内核”之后，三类最优先的一等协议对象里，前两类已经有了第一版：

- `action_request schema v1`
- `artifact_contract schema v1`

剩下最关键的一块就是：

- `workflow_spec schema`

当前系统里这一层虽然已经有 `pipelines.json`、`PipelineConfig`、`StageConfig`，但它仍然有几个问题：

- 还是 executor 直接消费的当前形状
- 还没有真正成为独立版本化协议
- 也还没与 action-request / artifact contract 形成统一的协议层

本轮先补 `workflow_spec schema v1` 草案：

- 新增 `docs/Schema-Workflow-Spec-v1.md`
- 新增 `backend/services/workflow_spec_contracts.py`
- 新增兼容编译器，把当前 pipeline template payload 编译成 `workflow_spec`

v1 当前覆盖：

- workflow identity
- ordered stage list
- agent ownership
- gate
- timeout
- delivery / expected_artifacts
- rollback
- skill injection

这一步的意义是：

- 终于把“当前 pipeline 配置形状”从内部配置模型推进到一等协议对象
- 后续 action-request 就可以开始基于 workflow policy 做更严格验证
- 也为将来把软件开发、视频生成、UI 设计等不同领域都收进统一 executor 预留了结构位置

边界：

- v1 仍然是 ordered stage list，不支持更丰富的 DAG / sidecar topology
- executor 还没有正式改为消费 canonical workflow spec
- 但这是把 `pipelines.json` 从配置文件推进到协议层的第一步

### 11.157 2026-05-07 新进展：`PipelineConfigManager` 已开始导出 canonical workflow spec

在 11.156 起草完 `workflow_spec schema v1` 之后，下一步不适合直接大改 executor 主链，而应该先在当前配置加载入口上补一个低风险桥接层。

本轮先推进：

- `PipelineConfigManager`

让它在继续保留：

- `PipelineConfig`
- `StageConfig`

这套 legacy 视图的同时，也能直接导出：

- canonical `workflow_spec`

也就是说，当前系统第一次在真正的运行时代码入口上，同时拥有：

- legacy config view
- canonical schema view

这一步的意义是：

- 后续如果要让 engine/runtime 渐进切到 canonical workflow spec，不需要一刀切重写
- action-request / artifact-contract 后续也有了一个更稳定的 workflow policy 来源

边界：

- 当前还只是读取/导出桥接
- executor 仍然主要消费 `PipelineConfig` / `StageConfig`
- 但协议层已经开始进入真实加载路径，而不再只是文档和 isolated helper

### 11.158 2026-05-07 新进展：`runner_policy` 已开始直接消费 canonical workflow spec

在 11.157 让 `PipelineConfigManager` 开始导出 canonical `workflow_spec` 之后，下一步更自然的不是立刻硬切 executor 主链，而是先让更靠近执行语义、但仍相对稳定的一层开始直接消费它：

- `runner_policy`

本轮继续推进：

- `compile_workflow_stage_policy(...)`
- `compile_workflow_run_policy(...)`

让 `RunnerGovernancePolicy` 可以直接从 canonical `workflow_spec` 编译出来，而不再依赖 legacy `StageConfig`。

这一步的意义是：

- canonical workflow spec 不再只是“配置导出物”
- 它开始进入真正的治理/执行前置层
- 后续如果要让 executor 主链渐进切换，就可以先复用已经稳定的 runner-policy 编译结果

边界：

- 当前 pipeline engine 主路径仍主要调用 legacy `compile_pipeline_*`
- 还没有把主执行链全部切到 `compile_workflow_*`
- 但 canonical workflow spec 已经连续打通了：
  - config bridge
  - governance-policy bridge

### 11.159 2026-05-08 新进展：canonical workflow spec 已开始通过 Pipeline API 暴露

在 11.158 打通 config bridge 和 governance-policy bridge 之后，canonical workflow spec 还缺少一个很实际的问题：

- 外部现在还看不到它
- 只能通过内部 helper / config manager / runner policy 间接接触

这会让它仍然更像“内部迁移对象”，而不是一等协议。

本轮先补一个低风险出口：

- `GET /api/pipelines/templates/{pipeline_name}/workflow-spec`

特点：

- 只读
- 不改 executor 主链
- 不依赖当前被并行修改的 `pipeline/engine.py`
- 直接暴露 canonical workflow spec payload

这一步的意义是：

- canonical workflow spec 第一次有了稳定 API 可见性
- 后续无论是前端、调试工具、还是新的编排 Agent，都有一个统一读取入口
- 也使 workflow spec 不再只是“内部迁移桥”，而开始具备真正的协议层身份

边界：

- 当前仍只是 read-side exposure
- executor 还没有从这个 API/contract 反向驱动
- 但 canonical workflow spec 已经形成三层落点：
  - config bridge
  - runner-policy bridge
  - API exposure bridge

### 11.160 2026-05-09 新进展：action request 已能按 workflow policy 做独立裁定

在 11.157 到 11.159 之后，canonical workflow spec 已经具备：

- 配置加载侧的 canonical view
- runner governance policy 编译
- 只读 API 暴露

但 `action_request schema v1` 仍缺少一个关键中间层：

- agent 可以表达 intent
- workflow spec 可以表达阶段/角色/gate/rollback/产物要求
- 中间还没有一个稳定裁定器判断“这个 intent 在当前 workflow policy 下是否合法”

本轮新增 `backend/services/action_request_policy.py`，先不改 pipeline engine 主路径，只补一个独立 validator：

- 校验 source stage 是否属于 workflow
- 校验 source agent 是否匹配 stage owner
- 校验 pipeline/stage gate approval 是否指向 manual/condition gate
- 校验 rollback suggestion 是否符合 workflow rollback target
- 校验 publish artifact 是否匹配当前 stage 的 expected artifacts

这一步的意义是：

- `action_request` 不再只是 schema 和兼容桥
- `workflow_spec` 不再只是计划描述和 API payload
- 二者第一次通过软件裁定层连接起来

边界：

- 当前仍是 isolated validator
- 还没有接入 approval queue creation、pipeline engine 或 artifact publication 主路径
- 不改变现有 runtime 行为，只为后续渐进接入提供可测试 contract

### 11.161 2026-05-09 新进展：publish_artifact intent 已能编译成 artifact contract

在 11.160 之后，`action_request` 已经能按 workflow policy 做独立裁定。
但其中的 `publish_artifact` 仍只是一个 agent intent：

- 它说明 agent 想发布什么
- 但还没有一个稳定桥把它变成 artifact 层的一等 contract

本轮新增 `backend/services/artifact_publication.py`：

- 接收 `publish_artifact` action request
- 根据 payload 推导 artifact mode
- 生成 canonical `artifact_contract`
- 保留 producer、source request、metadata 等追踪字段

当前支持的推导形状：

- markdown/json 内容 -> `document`
- 普通文件路径 -> `workspace_file`
- 目录路径 -> `workspace_directory`
- `structured.*` / `asset.*` 类型 -> `structured_asset`

这一步的意义是：

- `publish_artifact` 不再只停留在 action request schema
- `artifact_contract` 不再只停留在孤立 schema
- intent 和 produced-object contract 之间有了可测试转换层

边界：

- 当前仍未改 pipeline stage artifact persistence
- 未改 project asset persistence
- 未引入 artifact approval / supersession policy
- 仍然是兼容桥，而不是主路径迁移

### 11.162 2026-05-09 新进展：workflow spec 已有执行前诊断层

随着 workflow spec 开始承担“LLM 可生成、软件可执行”的协议角色，只靠 Pydantic 字段形状还不够。
很多错误不是类型错误，而是执行语义错误：

- stage list 为空
- stage id 重复
- stage 没有 owner agent
- timeout 无效
- rollback target 不存在或指向后续阶段
- delivery 标记 required 但没有 expected artifacts

本轮新增 `backend/services/workflow_spec_policy.py`：

- 对 canonical workflow spec 做 execution-readiness diagnostics
- 输出 deterministic report，而不是直接改配置或启动执行
- 把 error / warning 分开，让 condition gate 和 skill overlap 这类 v1 暂不完整能力先以 warning 暴露

这一步的意义是：

- LLM 生成 workflow spec 后，软件有了进入执行器前的裁定层
- workflow spec 不再只是“能 parse”，而是开始具备“能否执行”的独立判断
- 后续可以把 config load、API submit、runtime start 逐步接到同一个 diagnostics contract

边界：

- 当前仍未接入 `PipelineConfigManager.load`
- 未接入 pipeline executor start path
- 不改变现有 `pipelines.json` 加载和执行行为

### 11.163 2026-05-09 新进展：pipeline template 编译已能携带 workflow diagnostics

11.162 新增了 workflow spec 的 execution-readiness diagnostics，但调用方仍需要手工分两步：

- 先把 pipeline template 编译成 canonical workflow spec
- 再调用 diagnostics

这对后续接入配置加载、API submit 或编排 Agent 生成流程都不够直接。

本轮在 `backend/services/workflow_spec_policy.py` 增加：

- `WorkflowSpecCompilationResult`
- `compile_pipeline_template_with_policy_report(...)`

它把当前 `pipelines.json` 形状一次性转成：

- canonical `workflow_spec`
- execution-readiness report
- top-level `executable` flag

这一步的意义是：

- 现有 pipeline template 形状第一次有了“编译 + 裁定”的单一入口
- 后续如果让 LLM 生成 workflow spec 或让 API 接收 workflow spec，可以复用同一组 report 结构
- 仍然保持主执行路径不变，避免在 engine 并行修改期间扩大影响面

边界：

- 当前 helper 还没有接入 `PipelineConfigManager`
- 还没有在 API 层对 submitted workflow spec 做 enforcement
- 仍然只是 service-level contract

### 11.164 2026-05-09 新进展：pipeline config manager 已缓存 workflow diagnostics

11.163 已经提供了 compile-with-diagnostics helper，但真实配置加载路径仍然只缓存：

- legacy `PipelineConfig`
- canonical `workflow_spec`

如果 diagnostics 不进入 config manager，那么它仍然更像独立工具，而不是配置读取面的稳定 contract。

本轮更新 `backend/pipeline/config.py`：

- `PipelineConfigManager.load()` 继续保留 legacy config 行为
- 同时调用 `compile_pipeline_template_with_policy_report(...)`
- 缓存 canonical workflow spec
- 缓存 execution-readiness report
- 新增 `get_workflow_spec_report(...)`

这一步的意义是：

- 当前 `pipelines.json` 加载后，已经能同时获得 legacy view、canonical spec、diagnostics
- 后续 API 或 monitor 可以读取 diagnostics，而不需要重复编译
- 这仍然是 read-side contract，不会突然改变生产执行行为

边界：

- diagnostics 目前不阻断 config load
- pipeline executor start path 仍未 enforce diagnostics
- API 暂未暴露 diagnostics

### 11.165 2026-05-09 新进展：workflow diagnostics 已通过 Pipeline API 暴露

11.164 之后，`PipelineConfigManager` 已经缓存了 execution-readiness report，但外部仍然只能看到 canonical workflow spec 本体。
这会让前端、调试工具和未来编排 Agent 无法读取软件裁定结果。

本轮新增只读接口：

- `GET /api/pipelines/templates/{pipeline_name}/workflow-spec/report`

返回：

- `workflow_id`
- `executable`
- `diagnostic_count`
- full diagnostics payload

这一步的意义是：

- workflow spec 的软件裁定结果第一次具备稳定 API 可见性
- 编排 Agent 可以读取“为什么这个 workflow 不能执行”，而不是只能看到原始 spec
- 这仍然保持 read-side，不进入 executor enforcement

边界：

- 不改变 `/workflow-spec` 原有响应
- 不阻断 pipeline start
- 不把 diagnostics 写入数据库

### 11.166 2026-05-09 新进展：submitted workflow spec 已可通过 API 做软件裁定

11.165 暴露的是已加载 pipeline template 的 diagnostics。
但如果未来由编排 Agent 或 LLM 生成 workflow spec，还需要一个入口让软件先裁定：

- spec 是否能 parse
- 是否满足 execution-readiness policy
- 失败原因是什么

本轮新增：

- `POST /api/pipelines/workflow-spec/validate`

请求体是 canonical `workflow_spec`。
响应复用 execution-readiness report：

- `workflow_id`
- `executable`
- `diagnostic_count`
- diagnostics payload

这一步的意义是：

- LLM 生成 workflow spec 后，终于有了进入持久化/执行之前的软件裁定入口
- workflow spec 开始具备“开放生成、受限验证”的协议形态
- API 层仍然不创建 pipeline、不写数据库、不启动执行器

边界：

- 当前只是 validation endpoint
- 没有 workflow draft/template persistence
- 没有 executor enforcement

### 11.167 2026-05-09 新进展：evaluation rubric 已成为第四类协议对象

此前的 schema 收敛主要覆盖三类对象：

- `workflow_spec`
- `action_request`
- `artifact_contract`

但这三类对象还不能很好表达“怎么判断好坏”。
如果把 UI 品味、PRD 完整性、架构合理性、测试 blocker 标准都硬塞进 workflow 或 runtime policy，会把语义判断和执行控制重新混在一起。

本轮新增：

- `backend/services/evaluation_rubric_contracts.py`
- `docs/Schema-Evaluation-Rubric-v1.md`

v1 覆盖：

- rubric identity
- workflow/stage/agent/artifact applicability
- criteria
- scale
- evaluator owner
- required / weight
- acceptance threshold
- guidance

这一步的意义是：

- 模糊质量和品味判断有了独立协议对象
- 软件可以校验 rubric 结构，但不假装自己是语义裁判
- LLM / 人可以基于 rubric 做 judgment，再通过 action request 向 runtime 提出后续动作

边界：

- 当前没有 rubric result schema
- 未接入 workflow stage gates
- 未接入 artifact acceptance 或 review persistence

### 11.168 2026-05-09 新进展：evaluation result 已从 rubric 中拆出

11.167 新增了 `evaluation_rubric`，用于描述“怎么判断好坏”。
但 rubric 本身只是标准，不是一次实际评审。

如果没有独立 result schema，系统后续很容易把：

- rubric criteria
- agent/human 评审结论
- recommended rollback / approval / release action

混在一个 payload 里。

本轮新增：

- `backend/services/evaluation_result_contracts.py`
- `docs/Schema-Evaluation-Result-v1.md`

v1 覆盖：

- result identity
- rubric reference
- evaluated target reference
- reviewer ownership
- overall status
- criterion-level result
- evidence refs
- recommended action request ids

这一步的意义是：

- `evaluation_rubric` 负责“怎么评”
- `evaluation_result` 负责“一次评审得出什么”
- `action_request` 负责“评审后希望 runtime 做什么”
- runtime policy 仍然裁定这些请求是否能影响状态

边界：

- 当前没有 durable review persistence
- 未接入 artifact acceptance
- 未接入 stage/release gate enforcement

### 11.169 2026-05-09 新进展：evaluation result 已能桥接为 follow-up action request

11.168 把 `evaluation_result` 从 `evaluation_rubric` 中拆出来后，仍然缺少一个关键连接：

- 评审结果如何影响后续运行时动作？

如果让 evaluation result 直接改 runtime state，会破坏前面确立的边界。
更稳的路径是：

- evaluation result 记录语义判断
- action request 表达后续意图
- runtime policy 再裁定是否生效

本轮新增 `backend/services/evaluation_action_requests.py`：

- failed / needs-review result -> `report_blocker`
- failed result -> `suggest_rollback`
- 保留 evaluation result id、rubric id、target 等 metadata

这一步的意义是：

- 模糊质量判断可以产生结构化 follow-up intent
- 但不会直接改变 stage、gate、rollback 等 runtime state
- 后续可以把生成的 action request 继续送入 workflow-aware policy validator

边界：

- 当前没有自动接入 pipeline/tester/release 主路径
- 未持久化 evaluation result
- 未自动执行生成的 action request

### 11.170 2026-05-09 新进展：evaluation rollback intent 已能按 workflow policy 裁定

11.169 已经能从 failed evaluation result 生成 `suggest_rollback` action request。
但这仍然只是 intent，如果没有继续进入 workflow-aware policy validator，就还不能判断：

- 目标 stage 是否存在
- 目标 stage 是否符合当前 source stage 的 rollback policy
- source stage / agent ownership 是否一致

本轮在 `backend/services/evaluation_action_requests.py` 增加组合 helper：

- 先从 failed evaluation result 构造 `suggest_rollback`
- 再调用 `validate_action_request_for_workflow(...)`
- 返回 action request + policy decision

这一步的意义是：

- `evaluation_result -> action_request -> workflow policy decision` 形成了第一条可测试闭环
- 评审结论可以提出 rollback，但能否 rollback 仍由软件策略裁定
- 这强化了“LLM/人做语义判断，软件做运行时合法性裁定”的边界

边界：

- 当前仍不自动执行 rollback
- 未持久化 decision
- 未接入 pipeline engine 主路径

### 11.171 2026-05-09 新进展：evaluation result 已有稳定 read-model summary

11.168 到 11.170 已经让 evaluation result 具备：

- schema contract
- action request bridge
- workflow policy validation bridge

但后续如果进入 API 或 Monitor，仍需要一个稳定 read model，而不是让每个调用方自己解析完整 result payload。

本轮在 `backend/services/evaluation_result_contracts.py` 增加：

- `summarize_evaluation_result(...)`

输出：

- result id
- rubric id
- overall status
- target
- reviewer
- criterion counts
- recommended action request count
- summary

这一步的意义是：

- evaluation result 从“只可存原始 payload”推进到“可投影为稳定摘要”
- 后续 API/Monitor 可以复用同一 read model
- 不引入数据库 schema，也不改变执行行为

边界：

- 当前没有持久化 evaluation result
- 没有 monitor endpoint
- 没有 artifact review workflow

### 11.172 2026-05-09 新进展：workflow stage 已能引用 evaluation rubric

11.167 到 11.171 已经补齐 rubric/result/action bridge/read-model summary。
但 workflow spec 还不能表达：

- 哪个 stage 应该使用哪个 rubric
- 这个 stage 是否要求 evaluation

这会让 rubric 仍然游离在 workflow 外部。

本轮更新 `backend/services/workflow_spec_contracts.py`：

- 新增 `WorkflowEvaluationSpec`
- `WorkflowStageSpec.evaluation.rubric_refs`
- `WorkflowStageSpec.evaluation.required`
- pipeline template compiler 支持可选 `evaluation_rubrics`

这一步的意义是：

- workflow spec 可以声明 stage-level evaluation policy
- evaluation rubric 不再只是独立 schema
- 后续可以在 stage completion / artifact acceptance / release review 中读取统一 rubric refs

边界：

- 当前不改变 gate 行为
- 不强制 stage 必须产出 evaluation result
- 不接入 pipeline engine 主路径

### 11.173 2026-05-09 新进展：runner policy 已投影 stage evaluation policy

11.172 让 workflow stage 能引用 evaluation rubric。
但如果 runner governance policy 不携带这些引用，下游 action/policy/read-side 仍然要回头读 workflow spec 原文。

本轮更新 `backend/services/runner_policy.py`：

- `compile_workflow_stage_policy(...)` 读取 `stage_spec.evaluation`
- stage metadata 增加 `evaluation_policy`
- payload 包含 `rubric_refs` 与 `required`

这一步的意义是：

- workflow spec 中的 evaluation intent 进入 runner governance projection
- 后续 stage completion、artifact acceptance、monitor 展示可以读取统一 policy payload
- 仍然不改变 executor 主行为

边界：

- legacy `StageConfig` 路径没有 evaluation policy
- pipeline engine 未消费该 metadata
- 未强制 rubric result 存在

### 11.174 2026-05-09 新进展：evaluation result 已能按 runner policy 裁定 rubric 适用性

11.173 已经把 stage evaluation policy 投影进 runner governance policy。
下一步需要验证 evaluation result 是否真的符合该 policy：

- target stage 是否存在
- result.rubric_id 是否属于该 stage 的 `evaluation_policy.rubric_refs`

本轮新增：

- `backend/services/evaluation_result_policy.py`
- `backend/tests/test_evaluation_result_policy.py`

它返回 `EvaluationResultPolicyDecision`，包含：

- accepted
- stage_name
- violations
- policy metadata

这一步的意义是：

- `workflow_spec -> runner_policy -> evaluation_result decision` 形成闭环
- rubric refs 不再只是文档或 metadata，而有了独立裁定器
- 仍然保持 evaluation result 不直接影响 runtime state

边界：

- 当前不强制 pipeline stage gate 消费该 decision
- 不持久化 decision
- stage 没有 rubric policy 时仍保持兼容接受

### 11.175 2026-05-09 新进展：StageArtifact 已能归一化为 artifact_contract

前面几轮已经补齐：

- `publish_artifact` action request 到 artifact_contract 的编译桥
- workflow stage delivery policy
- evaluation result policy

但当前 pipeline 主路径仍会写入历史形态的 `StageArtifact`：

- `artifact_type`
- `file_path`
- `summary`

如果不先提供一个无副作用的归一化桥，后续 executor、API、Monitor、持久化层会继续各自解释这三个字段。

本轮新增：

- `backend/services/artifact_normalization.py`
- `backend/tests/test_artifact_normalization.py`

新增 `compile_stage_artifact_to_contract(...)`，支持把 ORM row 或 dict 形态的 StageArtifact-like payload 转成 canonical `artifact_contract`：

- `file` 转为 `workspace_file`
- `directory` 转为 `workspace_directory`
- 保留 source row id、stage id、created_at、原始 artifact_type 到 metadata
- 从 stage/run 关系投影 producer 的 agent、stage、task run、pipeline run、pipeline stage

这一步的意义是：

- StageArtifact 不再只是 legacy DB row，而有了统一协议层投影
- artifact_contract 开始承接现有 pipeline artifact，而不只承接未来的 publish_artifact
- 后续可以在不立即迁移数据库 schema 的情况下，让 API/Monitor/executor 逐步消费统一 contract

边界：

- 当前不改 `pipeline/engine.py` 的 artifact 写入流程
- 当前不新增 artifact_contract 持久化字段或表
- 当前不处理 project `Asset` 归一化

### 11.176 2026-05-09 新进展：Asset 已能归一化为 artifact_contract

11.175 把 pipeline `StageArtifact` 收进 artifact_contract 投影，但项目侧还有另一条 artifact 形态：

- `Asset.asset_type`
- `Asset.title`
- `Asset.content_json`
- `Asset.content_markdown`
- `Asset.storage_path`
- `Asset.source_input_refs_json`

如果只处理 StageArtifact，artifact_contract 仍无法覆盖项目资产、设计资产、结构化资产和文档资产。

本轮扩展 `backend/services/artifact_normalization.py`：

- 新增 `compile_asset_to_contract(...)`
- document-like asset 转为 `document`
- structured JSON asset 转为 `structured_asset`
- `workspace.file*` / `workspace.directory*` asset 在有 `storage_path` 时转为 workspace artifact
- 解析并校验 `content_json` 与 `source_input_refs_json`
- 保留 project id、version、status、is_current、supersession、approval decision 和 timestamps 到 metadata

这一步的意义是：

- artifact_contract 现在同时覆盖 pipeline `StageArtifact` 和 project `Asset`
- 项目资产可以先通过 read-side contract 投影统一进入 API/Monitor/后续 evaluator
- 后续无需一次性迁移数据库，也能逐步把 artifact read/write path 收到同一种协议对象

边界：

- 当前不改 `assets` 表结构
- 当前不改 Asset 创建或审批流程
- 当前不持久化 canonical artifact_contract payload

### 11.177 2026-05-09 新进展：artifact_contract 已能按 runner delivery policy 裁定

11.175 和 11.176 解决了现有 artifact 形态到 canonical contract 的投影，但还缺少一层软件裁定：

- artifact_contract 说明“产物是什么”
- runner/workflow delivery policy 说明“某个 stage 期望什么产物”

如果没有这层裁定，后续 stage completion、artifact acceptance、Monitor 只能展示 artifact，不能判断它是否满足 workflow contract。

本轮新增：

- `backend/services/artifact_contract_policy.py`
- `backend/tests/test_artifact_contract_policy.py`

新增 `validate_artifact_contract_for_workflow(...)` 和 `validate_artifact_contract_for_policy(...)`：

- 从 artifact producer 或显式参数确定 stage
- 按 runner governance policy 查找 stage delivery contract
- 校验 workspace/document/structured asset 的 path 是否匹配 `expected_artifacts`
- 支持目录型 expected artifact 接受 nested file
- 返回独立 `ArtifactContractPolicyDecision`

这一步的意义是：

- `workflow_spec -> runner_policy -> artifact_contract decision` 形成闭环
- artifact 是否可被 stage 接受由软件 policy 决定，而不是由 LLM 文本判断
- 后续 executor 可以先接这个 decision，再决定是否完成 stage、进入 gate 或要求补产物

边界：

- 当前不接 pipeline stage completion 主路径
- 当前不持久化 policy decision
- 当前只覆盖 expected artifact path，不处理 approval/supersession/asset dependency graph

### 11.178 2026-05-09 新进展：publish_artifact 已有 contract+policy 组合闭环

11.177 让 artifact_contract 可以按 runner delivery policy 被裁定。
但 `publish_artifact` action request 的主链仍需要一个组合入口，否则调用方必须自己串：

- parse action request
- compile artifact_contract
- validate artifact_contract against workflow/runner policy

这会让每个调用方重新发明同一段 glue code。

本轮扩展 `backend/services/artifact_publication.py`：

- 新增 `ArtifactPublicationPolicyResult`
- 新增 `compile_and_validate_publish_artifact_request_for_workflow(...)`
- 新增 `compile_and_validate_publish_artifact_request_for_policy(...)`

这一步形成的闭环是：

```text
publish_artifact action_request
  -> artifact_contract
  -> artifact_contract_policy decision
```

这一步的意义是：

- `publish_artifact` 不再只停在 schema compile 层
- runtime 后续接入 artifact publication 时，可以直接消费 contract+decision
- 是否接受产物由 workflow/runner policy 裁定，而不是由 action request 文本本身决定

边界：

- 当前不持久化 artifact_contract
- 当前不写 artifact acceptance decision
- 当前不改变 pipeline engine 或 orchestration runtime 主路径

### 11.179 2026-05-09 新进展：policy decision 已有统一 schema 投影

11.170 到 11.178 已经形成了多条 policy decision：

- action_request policy decision
- artifact_contract policy decision
- evaluation_result policy decision

但这些 decision 仍是各服务自己的 shape。
如果直接接入 run ledger、Monitor 或持久化，会再次出现三套 read model。

本轮新增：

- `docs/Schema-Policy-Decision-v1.md`
- `backend/services/policy_decision_contracts.py`
- `backend/tests/test_policy_decision_contracts.py`

新增 schema-v1 `policy_decision`：

- `decision_type`
- `subject`
- `accepted`
- `stage_name`
- `policy_source`
- `pipeline_name`
- `violations`
- `metadata`

并新增 `project_policy_decision(...)`，可把当前已有的：

- `ActionRequestPolicyDecision`
- `ArtifactContractPolicyDecision`
- `EvaluationResultPolicyDecision`

投影为统一 payload。

这一步的意义是：

- policy decision 成为独立控制面对象
- local software verdict 与 LLM/人产生的语义对象分离
- 后续 run ledger / Monitor / executor gate 可以消费统一 shape

边界：

- 当前不写 run ledger
- 当前不新增数据库表
- 当前不改变任何 policy checker 的行为

### 11.180 2026-05-09 新进展：policy decision 已有稳定 read-model summary

11.179 让 action/artifact/evaluation 三类 policy decision 可以投影成统一 schema。
但 Monitor/API 后续如果直接读完整 payload，仍会把 UI/read-side 绑定到完整 contract 结构。

本轮在 `backend/services/policy_decision_contracts.py` 增加：

- `summarize_policy_decision(...)`

输出：

- decision id
- decision type
- subject kind/id/type
- accepted
- stage/policy context
- violation counts by severity

这一步的意义是：

- policy decision 从“可持久化 payload”推进到“可展示 read model”
- Monitor/API 可以先消费 summary，而不是重复解析完整 payload
- 后续 ledger event、decision persistence、UI 投影可以共享同一摘要逻辑

边界：

- 当前不接 Monitor endpoint
- 当前不写 run ledger
- 当前不改变 policy checker 行为

### 11.181 2026-05-09 新进展：policy decision 已有标准 ledger event payload

11.180 提供了 policy decision summary，但真正接 run ledger 时还需要一层固定事件 payload：

- event kind
- summary
- accepted
- decision type
- subject identity
- optional full contract

如果各调用方直接手写这些 payload，ledger 里会再次出现多种形态。

本轮在 `backend/services/policy_decision_contracts.py` 增加：

- `build_policy_decision_event_payload(...)`

它生成 `policy_decision_recorded` 事件 payload，默认包含：

- `policy_decision_summary`
- `policy_decision`
- accepted / decision_type / subject_kind / subject_id / stage_name 的扁平投影

这一步的意义是：

- run ledger 接 policy decision 前已有稳定 payload contract
- Monitor 可以优先读 summary，也可以按需展开完整 contract
- 后续 action/artifact/evaluation policy checker 接 ledger 时不需要重复定义事件形态

边界：

- 当前不调用 `append_task_event`
- 当前不改 run ledger 主路径
- 当前不改变 policy checker 行为

### 11.182 2026-05-09 新进展：artifact publication result 已携带 policy_decision 投影

11.178 已经让 `publish_artifact` 形成：

```text
action_request -> artifact_contract -> artifact policy decision
```

11.179 到 11.181 又补齐了 policy decision contract、summary 和 ledger event payload。

本轮把两条线接起来，扩展 `ArtifactPublicationPolicyResult.to_payload()`：

- 保留原始 `contract`
- 保留原始 artifact policy `decision`
- 新增 canonical `policy_decision`
- 新增 `policy_decision_event_payload`

这一步的意义是：

- artifact publication 调用方不需要再手工投影 policy decision
- 后续 runtime 持久化 artifact 前，可以同时拿到 contract、decision、ledger payload
- `publish_artifact` 的接入面进一步靠近 executor 可消费的统一形态

边界：

- 当前不写 run ledger
- 当前不持久化 artifact_contract
- 当前不改变 artifact acceptance 语义

### 11.183 2026-05-09 新进展：evaluation rollback result 已携带 policy_decision 投影

11.170 已经让 failed evaluation result 可以生成 `suggest_rollback` 并按 workflow policy 裁定。
但该 helper 仍返回 `(request, decision)` 二元组，调用方如果要进 ledger 还要自己投影 policy decision。

本轮扩展 `backend/services/evaluation_action_requests.py`：

- 新增 `EvaluationRollbackPolicyResult`
- 新增 `build_rollback_policy_result_from_evaluation_result(...)`

`to_payload()` 现在输出：

- generated action request
- original action-request policy decision
- canonical `policy_decision`
- `policy_decision_event_payload`

这一步的意义是：

- evaluation-result -> rollback-action -> policy-decision 的链路与 artifact publication 链路对齐
- 后续 executor/runtime 可以直接拿统一 policy decision payload 写 ledger
- failed evaluation result 仍不会直接执行 rollback，执行权仍留给 runtime policy / executor

边界：

- 当前不执行 rollback
- 当前不写 run ledger
- 当前不改变 action_request policy checker 行为

### 11.184 2026-05-09 新进展：evaluation result policy result 已携带 policy_decision 投影

11.174 已经让 evaluation result 可以按 runner policy 裁定 rubric 适用性。
但这个裁定结果仍只返回 `EvaluationResultPolicyDecision`，没有统一的 policy_decision 投影。

本轮扩展 `backend/services/evaluation_result_policy.py`：

- 新增 `EvaluationResultPolicyResult`
- 新增 `validate_and_project_evaluation_result_for_policy(...)`

`to_payload()` 输出：

- original evaluation result
- original evaluation-result policy decision
- canonical `policy_decision`
- `policy_decision_event_payload`

这一步的意义是：

- evaluation result 本身的策略裁定也能进入统一 ledger/read-model 形态
- evaluation result -> policy decision 与 evaluation result -> rollback action -> policy decision 两条链路分清
- 软件仍只裁定 rubric/stage policy，不执行业务动作

边界：

- 当前不写 run ledger
- 当前不接 stage gate
- 当前不改变 evaluation-result policy checker 行为

### 11.185 2026-05-09 新进展：workflow spec readiness 已能投影为 policy_decision

workflow spec policy report 之前已经能给出 execution-readiness diagnostics，但它仍是独立 report shape。
如果 pipeline start path 后续要把“可执行/不可执行”的裁定写入 ledger，仍需要统一 policy_decision。

本轮扩展 `backend/services/workflow_spec_policy.py`：

- 新增 `WorkflowSpecPolicyDecisionResult`
- 新增 `project_workflow_spec_policy_report(...)`
- 新增 `validate_and_project_workflow_spec_for_execution(...)`

`to_payload()` 输出：

- workflow policy report
- canonical `policy_decision`
- `policy_decision_event_payload`

这一步的意义是：

- workflow spec 自身的 readiness check 也进入统一控制面 verdict
- pipeline start 前的 block reason 可以用同一种 policy decision 记录
- workflow/action/artifact/evaluation 四类 policy verdict 的外形开始一致

边界：

- 当前不阻断 pipeline start 主路径
- 当前不写 run ledger
- 当前不改变 workflow spec diagnostic 规则

### 11.186 2026-05-09 新进展：action_request policy result 已携带 policy_decision 投影

action request policy checker 是最早接入 workflow/runner policy 的裁定器之一。
但通用入口此前仍只返回 `ActionRequestPolicyDecision`，只有 artifact/evaluation 特定链路额外做了 policy_decision 投影。

本轮扩展 `backend/services/action_request_policy.py`：

- 新增 `ActionRequestPolicyResult`
- 新增 `validate_and_project_action_request_for_workflow(...)`
- 新增 `validate_and_project_action_request_for_policy(...)`

`to_payload()` 输出：

- original action request
- original action-request policy decision
- canonical `policy_decision`
- `policy_decision_event_payload`

这一步的意义是：

- 任意 action request policy check 都能进入统一 ledger/read-model 形态
- artifact/evaluation 特定链路不再是唯一拥有 projection 的路径
- 后续 approval queue、tool request、ask_agent 等主链接入时可以复用同一个返回结构

边界：

- 当前不写 run ledger
- 当前不改 approval queue 主路径
- 当前不改变 action-request policy checker 行为

### 11.187 2026-05-09 新进展：policy decision 已有集合级 read-model summary

前面几轮补齐了单条 policy decision 的 contract、summary 和 event payload。
但 Monitor/run detail 通常需要回答集合问题：

- 本 run 产生了多少 policy decision
- accepted / rejected 各多少
- error / warning 各多少
- 每类 decision type 的分布是什么

如果这些统计由 UI 或 API 各自计算，read model 会再次分叉。

本轮在 `backend/services/policy_decision_contracts.py` 增加：

- `summarize_policy_decision_set(...)`

输出：

- decision_count
- accepted_count / rejected_count
- error_count / warning_count / info_count
- by_decision_type counters

这一步的意义是：

- policy decision 已具备单条和集合两级 read model
- 后续 run ledger / Monitor 可以复用同一个聚合逻辑
- 控制面 verdict 的可视化不需要解析完整 contract 列表

边界：

- 当前不接 Monitor endpoint
- 当前不扫描 run ledger events
- 当前不改变 policy decision contract

### 11.188 2026-05-09 新进展：run ledger read model 已能汇总 policy_decision events

11.181 已经定义了 `policy_decision_recorded` 事件 payload，但 run ledger read model 还不会识别它。
如果后续开始写这些事件而 read-side 不聚合，Monitor 仍然只能展示原始 event 列表。

本轮更新 `backend/services/run_ledger.py`：

- 识别 event_type 或 payload `event_kind` 为 `policy_decision_recorded` 的事件
- 提取其中 canonical `policy_decision`
- 使用 `summarize_policy_decision_set(...)` 生成集合摘要
- 在 checkpoint snapshot 和 task run summary 中暴露 `policy_decision_summary`

这一步的意义是：

- run ledger read-side 已准备好消费 policy decision events
- Monitor/API 可以读取聚合后的 accepted/rejected/error/warning counters
- 仍保持写路径不变，避免在 executor 主路径脏文件上冲突

边界：

- 当前不主动写 `policy_decision_recorded` 事件
- 当前不改 Monitor UI
- 当前不改变 task execution 行为

### 11.189 2026-05-09 新进展：task run detail 已能列出 policy_decision 明细

11.188 让 run ledger summary 能聚合 policy decision，但 detail read model 仍只包含原始 events。
这会让 Monitor/API 如果要展示每条 verdict，还要自己扫描 events。

本轮继续更新 `backend/services/run_ledger.py`：

- `serialize_task_run_detail(...)` 新增 `policy_decisions`
- 每条 entry 包含 event id/index/type、event summary、policy decision summary、完整 policy_decision payload
- 仍只读取已有 `policy_decision_recorded` 事件，不负责写事件

这一步的意义是：

- task run summary 有聚合 counters
- task run detail 有可展示明细列表
- Monitor/API 不需要重复解析事件 payload

边界：

- 当前不改 Monitor UI
- 当前不主动写 policy decision event
- 当前不改变 execution behavior

### 11.190 2026-05-09 新进展：policy decision 已有标准 ledger append helper

11.188 和 11.189 让 run ledger read-side 能消费 policy decision events。
但写入侧如果未来由各 executor 手工调用 `append_task_event`，仍会重复拼 event type、summary 和 payload。

本轮在 `backend/services/run_ledger.py` 增加：

- `append_policy_decision_event(...)`

它做三件事：

- 调用 `build_policy_decision_event_payload(...)`
- 使用标准 event type `policy_decision_recorded`
- 生成默认 summary 后调用现有 `append_task_event`

这一步的意义是：

- policy decision 写 ledger 有了唯一 helper
- 后续 action/artifact/evaluation/workflow checks 接 executor 时，只需要传 canonical decision
- read-side summary/detail 已经能消费该 helper 写出的事件

边界：

- 当前不主动接任何 executor 主路径
- 当前不新增数据库表
- 当前不改变 policy checker 行为

### 11.191 2026-05-09 新进展：policy decision event summary formatter 已上移到 contract service

11.190 增加了 `append_policy_decision_event(...)`，但默认 summary 文本仍由 run ledger 私有 helper 生成。
这会让后续如果 Monitor、executor 或其他 read/write adapter 也需要同样的人类可读描述，容易再次分叉。

本轮更新：

- `backend/services/policy_decision_contracts.py` 新增 `format_policy_decision_summary(...)`
- `backend/services/run_ledger.py` 的 `append_policy_decision_event(...)` 改为复用该 formatter
- 删除 run ledger 内部私有 summary formatter

这一步的意义是：

- policy decision 的机器 payload、read summary 和 human-readable event summary 都集中在 contract service
- run ledger 只负责写事件，不再持有 policy decision 的展示规则
- 后续 Monitor/API 如果需要相同文本，可直接复用 contract service

边界：

- 当前不改变 `policy_decision_recorded` payload shape
- 当前不改变 task-run read model shape
- 当前不主动接 executor 主路径

### 11.192 2026-05-09 新进展：run ledger read-side 已兼容 summary-only policy decision events

`build_policy_decision_event_payload(...)` 从一开始支持 `include_contract=false`，但 run ledger read-side 只读取完整 `policy_decision` contract。
这会导致轻量级事件虽然带有 `policy_decision_summary`，却不会被 checkpoint summary 或 detail read model 统计。

本轮更新：

- `summarize_policy_decision(...)` 可消费完整 contract 或已生成的单条 summary
- `summarize_policy_decision_set(...)` 可混合聚合完整 contract 和 summary-only 条目
- `build_task_run_checkpoint_snapshot(...)` 识别 summary-only `policy_decision_recorded` 事件
- `serialize_task_run_detail(...)` 对 summary-only 事件仍输出 detail entry，full contract 缺失时为 `None`

这一步的意义是：

- run ledger read-side 与 policy decision event payload helper 的可选 full-contract 语义一致
- 未来如果某些 adapter 为了降低 ledger payload 体积只写 summary，Monitor/API 仍能展示控制面 verdict
- policy decision read model 更接近 Codex-style 的“宽输入、稳定投影”边界

边界：

- 默认 `append_policy_decision_event(...)` 仍写完整 contract
- 当前不新增数据库表
- 当前不主动接 executor 主路径

### 11.193 2026-05-09 新进展：policy decision 已能投影为 executor gate result

policy decision 已有 contract、ledger event payload 和 read model，但 executor 如果要消费 verdict，仍缺一个统一的 gate projection。
如果每条 executor path 都自己判断 `accepted`、拼 blocked reason，会很快回到一事务一判断的传统软件分叉。

本轮在 `backend/services/policy_decision_contracts.py` 增加：

- `build_policy_decision_gate_result(...)`

它输出：

- `status`: `accepted` 或 `rejected`
- `allowed`
- `blocked`
- `blocked_kind`
- `blocked_reason`
- `policy_decision_summary`
- `policy_decision`
- `violations`

这一步的意义是：

- policy decision 的 verdict 现在有了执行侧可直接消费的稳定 projection
- full-contract rejection 会优先用 violation message 作为 block reason
- summary-only decision 也能被投影成 blocked result，但不会伪造完整 contract
- 这为后续 action/artifact/evaluation/workflow executor gate 接入提供同一入口

边界：

- 当前不接 executor 主路径
- 当前不改变 tool governance 的 blocked result
- 当前不改变 ledger event schema

### 11.194 2026-05-09 新进展：Monitor overview API 已暴露 policy decision read model

11.188 到 11.192 让 task-run summary/detail 能读取 policy decision events。
但 Monitor overview 仍没有任何 policy decision 入口，控制面 verdict 只能通过 task run detail 间接查看。

本轮更新：

- `backend/services/monitor_projection.py` 新增 `serialize_monitor_policy_decision_item(...)`
- `backend/routes/monitor.py` 的 overview 统计新增 `policy_decision_events`
- `backend/routes/monitor.py` 的 overview 响应新增 `recent_policy_decisions`
- `backend/tests/test_monitor.py` 覆盖 overview policy decision projection

这一步的意义是：

- policy decision 已从 run ledger detail 进入 Monitor API 顶层 read model
- Monitor API 复用 canonical `policy_decision_summary`，不重新解释 contract
- 前端后续可以直接展示最近 verdict，而不用扫描 task-run events

边界：

- 当前不改前端 UI
- 当前不改变 ledger 写入路径
- 当前不主动接 executor 主路径

### 11.195 2026-05-09 新进展：run ledger 已能从 service result payload 写入 policy decision

当前 action/artifact/evaluation/workflow policy result 的 `to_payload()` 已经包含 `policy_decision` 和 `policy_decision_event_payload`。
但 run ledger 只有 `append_policy_decision_event(...)`，调用方仍要知道从 result payload 哪个字段取 decision。

本轮在 `backend/services/run_ledger.py` 增加：

- `append_policy_decision_event_from_result_payload(...)`

它支持：

- result payload 顶层 `policy_decision`
- result payload 顶层 `policy_decision_event_payload`
- 直接传入 `event_kind=policy_decision_recorded` 的 event payload
- summary-only `policy_decision_event_payload`

这一步的意义是：

- executor 后续接入时可以把 service result payload 直接交给 run ledger adapter
- policy checker、result payload、ledger event 三者之间少一层手工字段拼接
- summary-only 和 full-contract 两种事件都走同一写入边界

边界：

- 当前不主动接 action/artifact/evaluation/workflow executor 主路径
- 当前不改变默认 full-contract 写入行为
- 当前不新增数据库表

### 11.196 2026-05-09 新进展：policy result payload 已携带 gate result

11.193 增加了 `build_policy_decision_gate_result(...)`，但各类 policy result payload 仍只携带 `policy_decision` 和 `policy_decision_event_payload`。
这意味着 executor adapter 即使拿到 result payload，也还要再调用一次 gate projection。

本轮将 `policy_decision_gate_result` 加入：

- `ActionRequestPolicyResult.to_payload()`
- `ArtifactPublicationPolicyResult.to_payload()`
- `EvaluationRollbackPolicyResult.to_payload()`
- `EvaluationResultPolicyResult.to_payload()`
- `WorkflowSpecPolicyDecisionResult.to_payload()`

这一步的意义是：

- policy checker 输出、ledger 写入 payload、executor gate projection 已在同一个 result payload 内对齐
- 后续执行路径可以直接读取 `allowed` / `blocked` / `blocked_reason`
- 不需要每个 executor 自己重新解释 canonical decision

边界：

- 当前不改变 executor 主路径
- 当前不改变原有 `policy_decision` payload
- 当前不改变原有 `policy_decision_event_payload`

### 11.197 2026-05-09 当前差距：policy decision 已完成 contract/read-model/payload 层，下一步才是 executor 接线

从 11.179 到 11.196，policy decision 已经补齐：

- canonical `policy_decision` schema
- service-level decision -> canonical decision projection
- single summary / set summary / human-readable summary
- ledger event payload
- task-run checkpoint summary
- task-run detail list
- standard ledger append helper
- result-payload ledger append adapter
- executor-facing gate result
- Monitor overview API read model
- action/artifact/evaluation/workflow result payload 中的 `policy_decision_gate_result`

当前仍未完成的是把这些能力接入真实执行路径：

- action request policy check 发生时自动写 ledger
- artifact publication/acceptance 时自动写 ledger 并消费 gate result
- evaluation result/rollback policy check 时自动写 ledger 并消费 gate result
- workflow start/stage completion 时自动写 ledger 并消费 gate result
- Monitor 前端展示 `recent_policy_decisions`
- 决定 full-contract 与 summary-only ledger event 的保留策略

这说明当前工作已经把“判断对象”和“执行动作”分离出来：

- policy checker 产生 canonical decision
- result payload 同时携带 ledger event payload 和 gate result
- run ledger adapter 负责写事件
- executor 后续只需要调用 adapter 并按 gate result 决定继续、阻塞或等待审批

下一阶段应优先接 executor 主路径，但要避开当前并行修改中的 `backend/pipeline/engine.py`、`backend/routes/api.py`、`backend/services/tool_governance.py` 等脏文件，避免覆盖其他线程的改动。

### 11.198 2026-05-09 新进展：pipeline start 已记录 workflow_spec_policy decision

11.195 已提供 `append_policy_decision_event_from_result_payload(...)`，但还没有任何真实 executor path 使用它。
本轮选择最低风险的 pipeline start path 先接一处：只记录 workflow spec readiness verdict，不改变启动行为。

本轮更新 `backend/pipeline/engine.py`：

- pipeline start 创建 task-run ledger 后，从当前 pipeline template 编译 workflow spec
- 调用 `validate_and_project_workflow_spec_for_execution(...)`
- 将 result payload 交给 `append_policy_decision_event_from_result_payload(...)`
- 写入 `policy_decision_recorded` event，decision type 为 `workflow_spec_policy`

这一步的意义是：

- policy decision 首次从纯 helper/read model 接入真实 pipeline executor path
- pipeline start 的 workflow readiness verdict 可在 run ledger 和 Monitor API 中看到
- 仍保持行为安全：当前只记录，不阻止 pipeline 启动

边界：

- 当前不改变 pipeline start allow/block 行为
- 当前不接 stage completion / artifact acceptance
- 当前不接 approval queue resolution

### 11.199 2026-05-09 新进展：pipeline stage completion 已记录 artifact_contract_policy decision

11.198 接入了 pipeline start 的 workflow spec verdict。
下一个低风险执行点是 stage completion：当前 pipeline 已经会按 `expected_artifacts` 记录 `StageArtifact`，但不会把 artifact delivery policy verdict 写入 task-run ledger。

本轮更新 `backend/pipeline/engine.py`：

- `_record_artifacts(...)` 返回本轮发现并记录的 `StageArtifact` 列表
- 对每个已记录 artifact 构造 schema-v1 `artifact_contract`
- 调用 `validate_artifact_contract_for_policy(...)`
- 用 `ArtifactPublicationPolicyResult.to_payload()` 生成 canonical payload
- 用 `append_policy_decision_event_from_result_payload(...)` 写入 `policy_decision_recorded`

这一步的意义是：

- artifact delivery policy decision 首次接入真实 pipeline stage completion path
- run ledger / Monitor API 可以看到每个已记录 artifact 的 accepted/rejected verdict
- policy checker、result payload、ledger adapter 三层开始在真实 executor path 串起来

边界：

- 当前只为已发现并记录的 expected artifacts 写 verdict
- 当前不因 rejected artifact 阻塞 stage completion
- 当前不处理 missing expected artifact 的 rejected verdict

### 11.200 2026-05-09 新进展：missing expected artifact 已记录 rejected policy decision

11.199 只为已发现并记录的 artifact 写 `artifact_contract_policy` verdict。
这会漏掉更重要的场景：stage 声明了 expected artifact，但 workspace 中没有产物。

本轮继续更新 `backend/pipeline/engine.py`：

- stage completion 后计算 expected artifacts 与 recorded artifacts 的差异
- 对缺失项构造 `ArtifactContractPolicyDecision`
- violation code 使用 `artifact_missing`
- 通过 `project_policy_decision(...)` 投影为 canonical `policy_decision`
- 继续用 `append_policy_decision_event_from_result_payload(...)` 写入 task-run ledger

这一步的意义是：

- artifact delivery read model 能同时看到“已产出且通过/拒绝”和“缺失”的 verdict
- missing artifact 不再只能从 StageArtifact 缺行推断
- Monitor/API 可以直接展示 rejected policy decision

边界：

- 当前仍不因 missing artifact 阻塞 stage completion
- 当前不自动创建 StageArtifact row
- 当前不改变 manual gate / auto gate 流程

### 11.131 2026-04-29 新进展：single-agent sync/stream 顶层 runtime profile 已统一

在 11.130 之后，route 已经不再手工拼 `runtime context` / `execution context`，但 orchestrator 顶层仍然保留两套 profile 类型：

- `SingleAgentSyncRuntimeProfile`
- `SingleAgentStreamRuntimeProfile`

并且二者都还要再经过一层 managed session profile 中转，才会落到最终 `ManagedSingleAgentSessionSpec`。

这说明 runtime profile 虽然已经抬到最高层入口，但其内部结构仍然留着 sync/stream 双轨。

本轮继续把这层双轨压掉：

- 统一为单一 `SingleAgentRuntimeProfile`
- 顶层 runtime profile 直接持有 `ManagedSingleAgentSessionSpec`
- 删除只做中转的 managed session profile 层
- sync / stream runner helper 继续保留，但现在都消费同一种顶层 runtime profile

这一步的意义是：

- single-agent sync / stream 在最高层 profile shape 上第一次真正统一
- orchestrator 内部减少了一整层只做组装转发的中间对象
- 后续如果继续压缩 raw-runtime builder 的参数面，落点会更集中在同一个顶层 runtime profile 上

边界：

- sync / stream 仍然保留各自的 builder 入口和 runner helper
- unified runtime profile 之下仍然会分别构造 sync execution / stream execution
- 如果继续推进，下一步可以考虑进一步压缩 raw-runtime builder 共享参数，或者把 sync/stream builder 收成更统一的入口
