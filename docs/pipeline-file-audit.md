# Pipeline File Audit

**日期**: 2026-04-14
**目的**: 将 `backend/pipeline/` 及其外围耦合面拆成 `保留 / 改造 / 废弃` 清单，作为 project-first 重构执行依据。
**相关**: `docs/ADR-015-current-architecture-gap-analysis.md`, `docs/ADR-021-pipeline-disposition-and-stage-execution-kernel.md`

---

## 1. 总结

当前 `pipeline` 不是一个可直接挂接到新内核上的“小 runtime”，而是一整套旧产品骨架，耦合了：

- 路由主入口
- 旧数据模型
- 前端 Pipeline Dashboard
- E2E / frontend 测试假设
- 事件推送形态
- 审批 / 回滚 / Agent 间消息语义

因此它应该拆开看，而不是整块继承。

---

## 2. 保留候选（extract and keep）

### `backend/pipeline/engine.py`

建议只保留以下可抽离能力，且都需要重包后再吸收：

- 工具执行相关的 path safety / workspace safety 逻辑
- Agent 工具白名单装配逻辑
- stage-level event emission 模式
- 审计写入模式（`LLMCall / ToolCall / Event` 的落库方式）
- 并发控制 / background task orchestration 的薄能力

判断：

- **保留的是 execution primitive，不是 `PipelineEngine` 本体**

### `backend/pipeline/config.py`

可保留的不是 `PipelineConfig` worldview 本身，而是“外部模板驱动阶段配置”的思路。

候选吸收方向：

- 为新 `StageExecutionKernel` 保留 executor registry / stage profile 配置模式
- 不再保留 `pipeline_name -> stages[]` 作为产品一级对象

判断：

- **改造后可保留少量配置思路**

---

## 3. 改造候选（rewrite around new kernel）

### `backend/routes/pipeline.py`

现状问题：

- 全部围绕 `Pipeline` / `PipelineRun` / `PipelineStage`
- 生命周期 API 与新 `Project / StageRun / Decision` 语义冲突
- `/approve` `/reject` 把 Decision/Gate 继续藏在引擎内部

建议：

- 不继续扩展该路由
- 未来将有价值的能力重挂到：
  - `projects_v2.py`
  - `stage_runs_v2.py`
  - `decisions_v2.py`
  - `dashboard_v2.py`

判断：

- **路由层整体废弃，能力迁移到 v2 route 面**

### `backend/pipeline/engine.py` 中的 gate / instruct / message 逻辑

现状问题：

- `approve/reject` 以 pipeline blocked stage 为中心
- `instruct` 绑定 `PipelineMessage`
- 人机协作语义不是独立 Decision / StageRun note / Event

建议：

- gate 审批改挂 `Decision`
- 人工干预改挂 `StageRun` 上的 note/event/instruction 模型
- agent 间消息如果要保留，也应脱离 `PipelineMessage`

判断：

- **业务语义保留，数据模型和接口重写**

---

## 4. 废弃候选（retire as product surface）

### 旧数据模型

`backend/models/database.py` 中以下模型不再适合作为主业务骨架：

- `Pipeline`
- `PipelineRun`
- `PipelineStage`
- `StageArtifact`
- `PipelineMessage`

原因：

- 与 `Project / Asset / Decision / StageRun` 重叠且 worldview 冲突
- 会持续把前端和路由拉回旧主轴

### 旧前端 / 测试假设

以下测试文件大量把 pipeline dashboard 当成主视图，需要后续分阶段去除：

- `tests/test_frontend.py`
- `tests/test_integration_e2e.py`
- `tests/test_visual_rendering.py`

典型旧假设包括：

- `/api/pipelines` 是一等入口
- 创建项目后应创建 pipeline
- pipeline stages 顺序固定为 `analysis -> architecture -> development -> testing -> release`
- pipeline messages / artifacts 是前端主数据源

判断：

- **这些测试不是“坏测试”，而是旧产品定义的遗留物，需要在前端去 pipeline 化时系统迁移**

---

## 5. 推荐迁移顺序

### 第一步：冻结旧 pipeline 面

- 不再给 `backend/routes/pipeline.py` 增新能力
- 不再给 `Pipeline*` 模型新增字段
- 不再新增基于 pipeline 的前端功能

### 第二步：补新内核能力

- 继续推进 `StageExecutionKernel`
- 明确真实 executor contract
- 将 audit / event / instruction 都优先挂到 `StageRun`

### 第三步：搬迁可复用薄能力

优先候选：

- path validation
- tool execution sandbox helpers
- event emission helpers
- audit sink helpers

当前已完成：

- `backend/execution/workspace_guard.py`
- `backend/execution/event_log.py`

### 第四步：前端与测试去 pipeline 化

- 用 Mission Board / Project Overview 替代 pipeline dashboard 主入口
- 新增围绕 `Project / Asset / Decision / StageRun` 的前后端测试
- 将 pipeline 相关测试逐步降级为 legacy coverage 或删除

---

## 6. 结论

**`pipeline` 目录仍有一些可回收材料，但它更像“拆迁回收场”，不是下一代架构的地基。下一阶段应继续建设新 execution kernel，并只从旧实现里拆出少量经验证的薄能力。**
