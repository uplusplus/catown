# ADR-016: Chat 右侧 Project Browser

**状态**: 进行中
**日期**: 2026-05-11
**决策人**: BOSS
**相关**: ADR-010 (Monitoring Audit Visualization), ADR-011 (Chatroom Full Event Cards), ADR-015 (Codex 风格运行时内核演进)

---

## 1. 背景

Chat 主界面已经承担了当前对话、审批、运行中命令、Agent 工具卡片和恢复操作。如果右侧栏继续展示 Now、审批列表、运行状态或 Agent 上下文，会和主界面及 Monitor 重复，增加认知负担。

右侧栏更适合作为轻量浏览界面：帮助用户快速回答“这个项目有什么文件、产出了什么、后台还有什么进程记录”，而不是再次解释当前聊天正在发生什么。

---

## 2. 决策

将 Chat 右侧栏定位为 **Project Browser**，职责是浏览和验证项目上下文，不承担主流程决策。

右侧栏包含三个一级页签：

- **Files**：项目文件和最近变更的轻量索引。
- **Artifacts**：PRD、tech spec、test report、changelog 等正式产物和预期产物。
- **Processes**：仅展示当前仍在运行的后台命令和任务，侧重查看，不替代主界面的审批/继续等待操作。

职责边界：

- Chat 主界面：对话、审批、选择、当前进度、运行中命令的主要交互。
- Project Browser：查找、浏览、验证文件和产物，查看当前运行中的后台任务。
- Monitor：Agent 上下文、事件流、系统诊断、深度调试、非活动任务和历史任务记录。

---

## 3. 开发计划

### Phase 1: 前端可用基线

不新增后端 API，先基于 Chat 页面已有数据实现右侧浏览体验：

- 从 `project.workspace_path`、工具调用参数、工具结果、runtime cards 中推断文件路径。
- 从 `expected_artifacts`、task run title/summary、stage cards 中汇总产物。
- 从 `run_shell` cards 和 task run 状态中汇总后台进程/命令记录。
- 提供页签、计数、空状态、路径/命令复制、最近项优先排序。

### Phase 2: 后端真实数据

新增 workspace-scoped browser API：

- 文件树：路径、大小、mtime、git 状态、是否敏感。
- 文件预览：文本/Markdown 小文件预览，二进制和大文件只显示元数据。
- 产物索引：绑定 stage artifact、task run artifact、expected artifact 的状态。
- 进程索引：读取 tracked `run_shell` 状态并过滤到当前 project/chat。

### Phase 3: 易用性增强

- 文件名搜索和常用产物 pin。
- 最近修改、最近打开、按阶段分组。
- 小文件内联预览，大文件跳转 Monitor/Workspace。
- 后台进程支持查看完整日志和复制命令。

### 2026-05-11 Files 交互修订

Files 不采用扁平路径卡片列表，改为常见文件浏览器模式：

- 以 workspace 作为根节点，展示可展开/折叠的目录树。
- 目录优先、文件其次，单行紧凑排列，长文件名省略。
- 文件行只保留弱化来源/时间元信息，不放复制按钮。
- Phase 1 仍不扫描真实磁盘；目录树由已有工具路径引用推断，Phase 2 再接入后端真实文件树 API。

### 2026-05-11 Artifacts 分类修订

Artifacts 只展示可审阅、可交付、可归档的项目产物，不展示普通源码文件、运行日志、Agent 动作或重试指令。

纳入范围：

- PRD、需求文档、用户故事等需求产物。
- ADR、架构决策、设计方案、tech spec。
- Test plan、test report、QA/verification report。
- Review、audit、migration、deployment、release notes、changelog。
- 明确命名为 artifact 且符合交付物语义的文档。

页面布局采用和 Files 接近的轻量单行列表：类型、名称、状态、更新时间；详情只作为 hover/title 和后续预览入口。

### 2026-05-11 Processes 范围修订

Processes 只展示当前活动的后台执行项：

- `run_shell` 工具卡片必须仍处于 `running` 状态。
- task run 必须仍处于 `running` 状态，且不能是审批或 timeout 等阻塞等待状态。
- 已完成、失败、取消、审批等待、timeout 等非当前活动记录不在 Project Browser 展示，统一进入 Monitor。

---

## 4. 非目标

- 不把右侧栏做成第二个 Monitor。
- 不在右侧重复审批主操作。
- 不在右侧承载完整 Agent prompt、工具事件流或系统诊断。
- Phase 1 不实现真实文件树扫描，避免扩大后端权限和安全面。
