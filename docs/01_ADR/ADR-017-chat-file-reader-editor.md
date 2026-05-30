# ADR-017: Chat 临时 File Reader / Editor

**状态**: 进行中
**日期**: 2026-05-11
**决策人**: BOSS
**相关**: ADR-011 (聊天室全事件卡片统一), ADR-016 (Chat 右侧 Project Browser)

---

## 1. 背景

ADR-016 将右侧栏定位为 Project Browser，负责发现项目中的 Files、Artifacts 和 Processes。这个设计解决了“项目里有什么”的问题，但 Files 仍然只是索引列表：用户能看到文件，却不能在 Catown 内直接验证文件内容。

如果把完整文件查看和编辑能力都塞进右侧栏，右侧栏会从浏览器膨胀成一个小 IDE，挤压主 chat 的协作空间，也会让文件编辑、AI 解释、运行命令和审阅 diff 分散在不同区域。

---

## 2. 决策

采用 **右侧栏发现文件，主 chat 承载临时 File Reader / Editor card** 的交互模型。

- Project Browser / Files：负责发现、筛选、展开目录和选择文件。
- Chat 主窗口：负责读取、解释、编辑、审阅和保存相关工作流。
- Monitor：继续负责系统级事件、运行时诊断和历史记录。

MVP 先实现只读 File Reader card：

- 点击右侧 Files 中的文件后，在主 chat 线程顶部打开一个临时 reader card。
- 读取接口只允许访问当前 project workspace 内的文件。
- 文本文件显示内容、路径、大小、mtime、截断状态。
- 二进制文件和超大文件不直接编辑；MVP 中二进制只显示 metadata。
- Reader card 是临时 UI 状态，不写入 chat 历史，后续需要可审计时再转为正式 event/card。

Editor 作为后续阶段：

- 显式进入 Edit mode，不默认可编辑。
- 保存前展示 diff。
- 保存需要基于文件 mtime/hash 做并发保护。
- 保存动作进入 chat/runtime event，便于审计和回滚。

---

## 3. 安全边界

后端文件读取必须满足：

- 通过 project id 定位 workspace，不接受任意根路径。
- 请求路径必须 resolve 后仍位于 workspace 内。
- 拒绝目录读取。
- 限制读取字节数，默认只返回 bounded preview。
- 对疑似二进制内容返回 metadata，不返回原始字节。
- 不在前端暴露本机绝对路径作为读取能力；前端只传 Project Browser 中的相对路径。

---

## 4. 非目标

- MVP 不实现保存、重命名、删除、移动文件。
- MVP 不实现完整 IDE、tab 管理、多文件 diff 或跨文件搜索。
- MVP 不替代用户本地编辑器。
- MVP 不把文件内容自动注入 LLM 上下文；用户仍需显式要求解释或修改。

---

## 5. 演进计划

1. **File Reader Card**
   - Files 点击打开主 chat 临时 reader。
   - 后端 workspace-scoped 只读 API。
   - 文本预览、复制路径、关闭 card。

2. **File Editor Card**
   - 显式 edit mode。
   - 保存前 diff。
   - mtime/hash 防覆盖。

3. **AI-aware File Actions**
   - 选中行后发起 Explain / Refactor / Add test。
   - 将选中片段作为显式上下文传给 assistant。
