# Catown 文档索引

`docs/` 是 Catown 的设计知识库。它按文档用途拆成 ADR、PRD、Schema、Spec 和参考资料，方便 Prompt 按需渐进式披露，也方便人在开发前快速找到相关设计背景。

推荐阅读顺序：

1. 项目概览先读根目录 [README.md](../README.md)。
2. 产品和目标读 [02_PRD/PRD.md](02_PRD/PRD.md)。
3. 当前代码架构读 [04_Spec/tech-spec.md](04_Spec/tech-spec.md) 和 [01_ADR/Architecture.md](01_ADR/Architecture.md)。
4. 具体实现争议或历史决策读 [01_ADR/](01_ADR/) 下的对应 ADR。
5. 涉及运行时协议、产物、策略或评估契约时读 [03_Schema/](03_Schema/)。

说明：文件路径是当前导航锚点；个别历史文档标题中的 ADR 编号保留原始记录。

## 目录结构

```text
docs/
  01_ADR/     架构决策、运行时边界、状态机、监控、安全和演进记录
  02_PRD/     产品需求、功能需求和用户体验方向
  03_Schema/  稳定协议对象和数据契约
  04_Spec/    当前代码架构分析和技术说明
  05_Ref/     外部协议与背景参考
```

## 01_ADR - 架构与决策记录

| 文档 | 主题 |
|------|------|
| [Architecture.md](01_ADR/Architecture.md) | Catown logic architecture, runtime boundaries, workflow contracts, monitor read model |
| [Business-Flow.md](01_ADR/Business-Flow.md) | 对话驱动项目管理、项目/会话/工作区关系和同步机制 |
| [Session-Project-Flow.md](01_ADR/Session-Project-Flow.md) | Session、Project、Workspace 的创建、展示、孵化和计数规则 |
| [ADR-001-queue-modes.md](01_ADR/ADR-001-queue-modes.md) | LLM 对话系统队列调度模式 |
| [ADR-004-knowledge-graph.md](01_ADR/ADR-004-knowledge-graph.md) | 知识图谱集成方案 |
| [ADR-005-chat-input.md](01_ADR/ADR-005-chat-input.md) | 聊天框输入体验优化 |
| [ADR-006-omni.md](01_ADR/ADR-006-omni.md) | OMNI 多模态能力集成方案 |
| [ADR-007-ui-ux-skill.md](01_ADR/ADR-007-ui-ux-skill.md) | UI/UX Pro Max Skill 集成可行性 |
| [ADR-008-skills-progressive-disclosure.md](01_ADR/ADR-008-skills-progressive-disclosure.md) | Skills 渐进式披露机制 |
| [ADR-009-chat-runtime-state.md](01_ADR/ADR-009-chat-runtime-state.md) | Chat Runtime State 分层 |
| [ADR-009-context-compression.md](01_ADR/ADR-009-context-compression.md) | 上下文压缩策略、RTK 竞品分析和架构决策 |
| [ADR-010-monitoring-audit-visualization.md](01_ADR/ADR-010-monitoring-audit-visualization.md) | 监控审计与交互可视化 |
| [ADR-011-chatroom-full-event-cards.md](01_ADR/ADR-011-chatroom-full-event-cards.md) | 聊天室全事件卡片统一 |
| [ADR-012-llm-session-context-management.md](01_ADR/ADR-012-llm-session-context-management.md) | LLM 会话上下文管理 |
| [ADR-013-skill-packages.md](01_ADR/ADR-013-skill-packages.md) | Canonical Skill Packages |
| [ADR-014-network-monitor-semantics.md](01_ADR/ADR-014-network-monitor-semantics.md) | Network Monitor Semantics |
| [ADR-015-codex-style-runtime-evolution.md](01_ADR/ADR-015-codex-style-runtime-evolution.md) | Codex 风格运行时内核演进 |
| [ADR-016-chat-project-browser.md](01_ADR/ADR-016-chat-project-browser.md) | Chat 右侧 Project Browser |
| [ADR-017-chat-file-reader-editor.md](01_ADR/ADR-017-chat-file-reader-editor.md) | Chat 临时 File Reader / Editor |
| [ADR-018-task-activity-projection.md](01_ADR/ADR-018-task-activity-projection.md) | Task Activity Projection for Chat Steps |
| [ADR-019-canonical-chat-timeline.md](01_ADR/ADR-019-canonical-chat-timeline.md) | Canonical Chat Timeline |
| [ADR-020-prompt-vs-runtime-contracts.md](01_ADR/ADR-020-prompt-vs-runtime-contracts.md) | Prompt Guidance vs Runtime Contracts |
| [ADR-021-user-visible-runtime-step-projection.md](01_ADR/ADR-021-user-visible-runtime-step-projection.md) | User-Visible Runtime Step Projection |
| [ADR-022-tester-runner-contract.md](01_ADR/ADR-022-tester-runner-contract.md) | Tester Runner Contract |
| [ADR-023-execution-authorization-timing.md](01_ADR/ADR-023-execution-authorization-timing.md) | Execution and Authorization Timing |
| [ADR-024-artifact-naming-and-storage.md](01_ADR/ADR-024-artifact-naming-and-storage.md) | Artifact Naming and Storage Conventions |
| [ADR-024-configurable-tool-authorization-policy.md](01_ADR/ADR-024-configurable-tool-authorization-policy.md) | Configurable Tool Authorization Policy |
| [ADR-025-llm-upstream-error-retry-policy.md](01_ADR/ADR-025-llm-upstream-error-retry-policy.md) | LLM Upstream Error and Retry Policy |
| [ADR-026-dynamic-context-control-trigger-flow.md](01_ADR/ADR-026-dynamic-context-control-trigger-flow.md) | Dynamic Context Control and Compaction Trigger Flow |
| [ADR-027-chat-runtime-state-machine-hardening.md](01_ADR/ADR-027-chat-runtime-state-machine-hardening.md) | Chat Runtime 状态机健壮性改进 |
| [ADR-028-chat-runtime-state-machine-overview.md](01_ADR/ADR-028-chat-runtime-state-machine-overview.md) | Chat 运行时状态机全景分析 |
| [ADR-028-context-compression-early-trigger-fix.md](01_ADR/ADR-028-context-compression-early-trigger-fix.md) | 上下文压缩提前触发的根因分析与系统性改进方案 |
| [ADR-029-taskrun-hanging-analysis-and-fix.md](01_ADR/ADR-029-taskrun-hanging-analysis-and-fix.md) | TaskRun 悬挂问题分析与改进计划 |
| [ADR-030-event-sourced-taskrun-state.md](01_ADR/ADR-030-event-sourced-taskrun-state.md) | Event-Sourced TaskRun State |
| [ADR-031-telemetry-storage-and-serial-write-adapter.md](01_ADR/ADR-031-telemetry-storage-and-serial-write-adapter.md) | Telemetry Split DB and Serial Write Adapter |
| [ADR-032-stable-public-identifiers-and-recovery-safe-identity.md](01_ADR/ADR-032-stable-public-identifiers-and-recovery-safe-identity.md) | Stable Public Identifiers and Recovery-Safe Identity Boundaries |
| [ADR-033-approval-single-source.md](01_ADR/ADR-033-approval-single-source.md) | Approval Notification Single Source of Truth |
| [ADR-034-monitor-performance.md](01_ADR/ADR-034-monitor-performance.md) | Monitor/Network 页面性能问题根因分析与改进方案 |

## 02_PRD - 产品与功能需求

| 文档 | 主题 |
|------|------|
| [PRD.md](02_PRD/PRD.md) | Catown 产品需求、用户故事、Agent 体系、Pipeline、产物、监控和运行配置 |
| [skills-prd.md](02_PRD/skills-prd.md) | Skills 功能 PRD |
| [feature-agent-avatars.md](02_PRD/feature-agent-avatars.md) | Agent-specific business avatars in chat |
| [feature-agent-color-identity.md](02_PRD/feature-agent-color-identity.md) | Agent color identity across chat, tasks, artifacts, and processes |
| [feature-chat-handoff-tail-mention.md](02_PRD/feature-chat-handoff-tail-mention.md) | Chat handoff tail-mention trigger rule |
| [feature-right-sidebar-runtime-monitor.md](02_PRD/feature-right-sidebar-runtime-monitor.md) | Right sidebar runtime monitor and Monitor runtime map |

## 03_Schema - 协议与数据契约

| 文档 | 主题 |
|------|------|
| [Schema-Action-Request-v1.md](03_Schema/Schema-Action-Request-v1.md) | Agent 请求使用工具、询问 Agent、请求审批、发布产物等动作的协议 |
| [Schema-Artifact-Contract-v1.md](03_Schema/Schema-Artifact-Contract-v1.md) | Workspace file、directory、document、structured asset 等产物契约 |
| [Schema-Evaluation-Result-v1.md](03_Schema/Schema-Evaluation-Result-v1.md) | 评估结果结构 |
| [Schema-Evaluation-Rubric-v1.md](03_Schema/Schema-Evaluation-Rubric-v1.md) | 评估 Rubric 结构 |
| [Schema-Policy-Decision-v1.md](03_Schema/Schema-Policy-Decision-v1.md) | 运行时策略判断和违规结果结构 |
| [Schema-Workflow-Spec-v1.md](03_Schema/Schema-Workflow-Spec-v1.md) | Workflow / Pipeline 阶段规格 |

## 04_Spec - 技术说明

| 文档 | 主题 |
|------|------|
| [tech-spec.md](04_Spec/tech-spec.md) | 当前项目架构分析、模块职责、数据流、风险、技术债和后续改进建议 |

## 05_Ref - 参考资料

| 文档 | 主题 |
|------|------|
| [OpenAi.md](05_Ref/OpenAi.md) | OpenAI 兼容协议、Chat Completions、Tool Calling、Structured Output 和实现参考 |
