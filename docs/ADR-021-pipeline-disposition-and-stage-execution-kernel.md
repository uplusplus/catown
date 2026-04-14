# ADR-021: Pipeline 去留判断与新 Stage Execution Kernel

**日期**: 2026-04-14
**状态**: 待确认
**决策者**: BOSS + AI 架构分析
**相关**: `docs/ADR-015-current-architecture-gap-analysis.md`, `docs/ADR-020-service-layer-and-implementation-plan.md`

---

## 1. 背景

`ADR-020` 原本预留了“接旧执行适配器”的阶段，默认假设旧 `pipeline/` 至少能作为过渡执行层被吸收。

但在进一步审视当前实现后，需要修正这个假设：

**旧 `pipeline/` 不仅和目标架构 worldview 不一致，而且其自身也没有被证明是稳定可复用的执行主干。**

因此，下一阶段不应默认投入 `legacy_pipeline_adapter`，而应先重新判断旧 `pipeline/` 是否有继续存在的必要。

---

## 2. 重新判断后的结论

### 2.1 结论一句话

**旧 `pipeline/` 不再作为默认继承对象，而是作为待审查资产处理。**

### 2.2 具体判断

- 不保留旧 `pipeline` 的产品语义
- 不再围绕 `Pipeline / PipelineRun / PipelineStage / StageArtifact` 继续设计新主流程
- 仅在明确存在“低耦合、可验证、可迁出的执行能力”时，才局部吸收其内部实现
- 新架构的执行主语义，改由 `StageRun + Asset + Decision` 直接承载

---

## 3. 为什么不默认做 legacy pipeline adapter

### 3.1 旧 pipeline 本身没有证明自己是可靠主干

现状代码里，`pipeline/engine.py` 试图同时负责：

- 生命周期管理
- 阶段流转
- Gate 审批
- Agent 调度
- 工具调用循环
- 错误恢复
- Agent 间消息
- Artifact 归档
- 审计落库
- WebSocket 事件推送

这意味着它不是一个“小而稳的 runtime primitive”，而是一个高度耦合的大总管。

如果在这种状态下继续做 `legacy_pipeline_adapter`，本质上不是“复用能力”，而是“替旧大总管续命”。

### 3.2 adapter 会把错误世界观继续带进新内核

如果 adapter 的上游需要不断理解：

- `pipeline_id`
- `current_stage_index`
- `PipelineMessage`
- `StageArtifact`
- manual gate / rollback 旧状态

那么新业务层迟早会被迫兼容这些概念。

这会反向污染新 `Project / Asset / Decision / StageRun` 模型。

### 3.3 当前更需要的是新执行骨架，不是旧壳翻译层

在当前阶段，最短正确路径不是：

`StageRun -> legacy adapter -> old pipeline`

而是：

`StageRun -> Stage Execution Kernel -> 对应 stage executor`

先把新执行边界立住，再决定是否从旧系统里提取少量低层能力接进来。

---

## 4. 对旧 pipeline 的去留判断

### 4.1 建议保留的部分

仅保留那些属于“执行原语”而非“旧产品骨架”的能力，例如：

- Agent 配置加载 / registry
- LLM client 获取与 provider fallback
- 工具白名单和工具执行函数中可抽离的安全校验逻辑
- 事件总线 / 审计写入模式中可复用的薄能力

### 4.2 建议改造后再吸收的部分

这些内容不是直接复用，而是重包后吸收：

- tool execution sandbox 封装
- stage-level event emission
- execution audit sink
- future agent-run orchestration primitives

### 4.3 建议废弃主导地位的部分

以下内容不应继续作为主业务骨架：

- `Pipeline`
- `PipelineRun`
- `PipelineStage`
- `StageArtifact`
- `PipelineMessage`
- `/api/pipelines/*` 作为主入口
- 前端 pipeline dashboard 作为主控制视图

---

## 5. 新设计：Stage Execution Kernel

### 5.1 目标

建立一个围绕 `StageRun` 的新执行内核，使执行层天然服务于 project-first 模型，而不是反过来让项目模型附着在执行器上。

### 5.2 最小职责

`StageExecutionKernel` 只负责三件事：

1. 根据 `stage_type` 选择 executor
2. 调用 executor 执行当前 `StageRun`
3. 在没有 executor 时显式失败，而不是偷偷回落到旧 pipeline

### 5.3 executor contract

每个 executor 至少满足：

- `supports(stage_type)`
- `execute(project, stage_run, now)`

这让执行层天然可替换。

### 5.4 第一批 executor

第一批只落一个：

- `BootstrapStageExecutor`

当前还补上了一个最小返回契约：

- `StageExecutionResult`
  - `status`
  - `summary`
  - `emitted_asset_types`
  - `queued_stage_types`
  - `pending_decision_types`

它承接当前已经跑通的 scaffold 逻辑：

- `product_definition`
- `build_execution`
- `qa_validation`
- `release_preparation`

注意：

**这不是最终执行器，而是新内核下的临时 bootstrap executor。**

它的价值在于：

- 先确立正确边界
- 不再把 bootstrap 逻辑硬编码在 `ProjectFlowCoordinator`
- 后面可被真实 executor 逐步替换

---

## 6. 实施调整

相对 `ADR-020`，实施顺序调整为：

### 新 Phase D

- 先审计旧 `pipeline/` 的保留 / 改造 / 废弃边界
- 先建立 `StageExecutionKernel`
- 先让 `continue_project()` 依赖 kernel，而不是旧执行器
- 再决定是否从旧 pipeline 提取少量 runtime primitive

### 新 Phase E

- 为真实 stage executor 设计完整输入输出 contract
- 补 `tasks / agent_runs / audit / events` 对 `StageRun` 的挂接
- 当前已先落最小 v2 入口：
  - `GET /api/v2/stage-runs/{id}/events`
  - `POST /api/v2/stage-runs/{id}/instructions`
- 前端逐步去 pipeline 化

---

## 7. 一句话总结

**对当前 Catown 来说，正确的下一步不是给旧 `pipeline` 做翻译层，而是先让新架构拥有自己的执行内核；旧 `pipeline` 只在被证明确有低层复用价值时，才以零碎能力的形式被吸收。**
