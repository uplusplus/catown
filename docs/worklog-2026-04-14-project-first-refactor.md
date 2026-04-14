# 2026-04-14 Project-First Refactor Worklog

## 本轮过程记录

### 1. 收紧 v2 资产依赖链
- 新增 `backend/services/asset_service.py`
- 将 stage bootstrap 的 source refs 统一通过 `build_source_refs()` 生成
- 补齐：
  - `build_artifact <- prd + ux_blueprint + tech_spec`
  - `release_pack <- build_artifact + test_report`
- 提交：`1e4a9aa` `Tighten asset dependency flow for v2 stages`

### 2. 调整路线：不再默认接 legacy pipeline
- 重新审视后确认：旧 `pipeline/` 没有证明自己是稳定主干，且 worldview 持续污染新内核
- 新判断：旧 `pipeline/` 作为待审查资产，而不是默认继承对象
- 新 ADR：`docs/ADR-021-pipeline-disposition-and-stage-execution-kernel.md`

### 3. 建立新 execution kernel
- 新增 `backend/execution/stage_execution_kernel.py`
- 新增 `backend/execution/bootstrap_stage_executor.py`
- `continue_project()` 已改为走 kernel，而不是直接绑定旧 stage coordinator
- 旧 `StageExecutionCoordinator` 降为兼容 facade
- 提交：`3640d19` `Introduce a project-first stage execution kernel`

### 4. 完成 pipeline 文件级去留审计
- 新文档：`docs/pipeline-file-audit.md`
- 结论：
  - 保留可抽离的 runtime primitive
  - 路由/旧模型/前端 pipeline dashboard 不再作为主骨架
- 从旧 `pipeline/engine.py` 抽出首块 primitive：
  - `backend/execution/workspace_guard.py`
- 提交：`056b5d8` `Audit legacy pipeline and extract workspace guards`

### 5. 给新内核补最小 contract 和 StageRun 事件面
- 新增 `StageExecutionResult`
- `StageExecutionKernel` 改为返回结果对象
- `ProjectFlowCoordinator` 在 stage 执行后写入 `stage_execution_completed`
- `Event` 模型开始挂接：
  - `project_id`
  - `stage_run_id`
  - `asset_id`
- 新增 v2 路由：
  - `GET /api/v2/stage-runs/{id}/events`
  - `POST /api/v2/stage-runs/{id}/instructions`
- `stage_run detail` 现在返回 `events`
- 提交：`541d17e` `Add project-first stage events and executor contract`

## 当前判断
- 新主轴已经从 `legacy pipeline adapter` 转到 `project-first stage execution kernel`
- 下一步重点不再是给旧 pipeline 做桥，而是继续抽 runtime primitive，并补真实 executor contract

## Wiki 同步状态
- 目标：把这份过程记录同步到外部 wiki
- 当前状态：阻塞
- 原因：`feishu_wiki` 调用 `spaces` 连续返回 `400`，暂时无法可靠定位/写入 wiki 空间
- 暂代：先落盘到仓库文档，待 wiki 接口恢复后再同步
