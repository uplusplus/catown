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

### 6. 继续抽 runtime primitive
- 新增 `backend/execution/event_log.py`
- 新增 `backend/execution/tool_audit.py`
- 新增 `backend/execution/llm_audit.py`
- 新增 `backend/execution/tool_dispatch.py`
- 让 legacy `pipeline/engine.py` 的部分事件写入、tool-call 审计、llm-call 审计、tool registry/build/execute 开始复用共享 helper
- 目标：把 audit/event sink 和 tool dispatch 从 legacy engine 里继续薄化，给后续新 executor 复用打底

## 7. 补充架构判断：为什么继续往下拆
- 新增 ADR：`docs/ADR-022-project-first-backend-layering-and-refactor-purpose.md`
- 新增 wiki 页面：`docs/Project-First-Backend-Architecture.md`
- 核心结论：继续下拆不是为了“代码好看”，而是为了
  - 分离业务推进与接口返回 shape
  - 防止 `ProjectService` 重新膨胀成上帝类
  - 稳定 `/api/v2/*` contract，给前端一个可靠靶子

## 8. 前端迁移判断进一步明确：不迁就旧 Pipeline Dashboard
- 新增文档：`docs/Frontend-Mission-Board-Migration-Audit.md`
- 审计结论：`frontend/index.html` 不是“残留少量旧接口”，而是仍由整块 `pipeline + chatroom` 主壳组织
- `tests/test_frontend.py` 也仍然把产品主流程编码成 `project -> chatroom -> pipeline`
- 当前更合理的方向不是让后端兼容旧前端心智，而是：
  - 保留有产品价值的交互块
  - 重建 project-first Mission Board
  - 让主视图围绕 `Project / StageRun / Decision / Asset / Event`
- 进一步补充：已新增 `docs/Mission-Board-Minimum-V2-Contract.md`，冻结第一版 Mission Board 所需最小 `/api/v2/*` 字段面，避免前后端继续互相猜字段
- 进一步补充：已新增 `docs/Mission-Board-Information-Architecture.md`，把新前端主视图的布局、区块职责、默认行为和实现切片固定下来，避免又按旧 Pipeline Dashboard 思维落页面
- 架构决策进一步收敛：用户明确选择直接启动方案 C，不做兼容迁移；因此已新增 `docs/ADR-023-frontend-react-mission-board-architecture.md`，并把 `frontend/index.html` 切成 Vite shell，建立 `frontend/src/` 下的 React/TypeScript Mission Board 骨架
- 前端重构计划已正式写入 TODO 和 wiki/ADR：后续阶段按 `detail rail 收口 -> state/query 收口 -> continue/resolve 刷新与错误态收口 -> activity feed 事件面定型 -> 重写 frontend tests -> 固化 dist 发布链路` 推进，而不是再回到旧 dashboard 兼容修补

## 当前判断
- 新主轴已经从 `legacy pipeline adapter` 转到 `project-first stage execution kernel`
- 下一步重点不再是给旧 pipeline 做桥，而是继续抽 runtime primitive，并同时冻结 Mission Board 所需最小 v2 contract
- 后端继续下拆的直接目标，是把 project-first v2 后端整理成可继续演进、可稳定对接前端的分层底座
- 前端迁移的直接目标，不是“把旧 Pipeline Dashboard 接上新后端”，而是用新后端支撑一个新的 project-first 主工作面

## Wiki 同步状态
- 目标：把这份过程记录同步到外部 wiki
- 当前状态：已完成
- 目标仓库：`https://github.com/uplusplus/catown.wiki.git`
- wiki 提交：`06c139d` `Update project-first refactor wiki`
- 同步页面：
  - `Home.md`
  - `Project-First-Refactor-Log.md`
