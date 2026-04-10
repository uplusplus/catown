# Skills 体系

Skills 是 Catown 中 Agent 能力的高层封装。每个 Skill 定义一种专业行为模式，由**指令片段**（注入 system prompt）和**依赖工具**组成。

## 工作原理

```
agents.json 的 agent.skills → 查找 skills.json → 注入 prompt_fragment 到 system prompt
```

Agent 配置了哪些 skills，它的 system prompt 就包含对应的指令片段，指导 Agent 如何完成特定类型的任务。

## 预置 Skills

| Skill | 类别 | 说明 | 依赖工具 |
|-------|------|------|---------|
| `code-generation` | development | 代码生成规范 | read_file, write_file, list_files |
| `unit-testing` | development | 单元测试规范 | read_file, write_file, execute_code |
| `refactoring` | development | 重构规范 | read_file, write_file, search_files |
| `document-analysis` | analysis | 文档分析规范 | read_file, write_file, web_search |
| `architecture-design` | architecture | 架构设计规范 | read_file, write_file, web_search |
| `changelog-generation` | release | Changelog 生成规范 | read_file, write_file, execute_code |
| `knowledge-graph` | analysis | 知识图谱构建与查询 | execute_code, read_file, write_file |

## 知识图谱 Skill (knowledge-graph)

基于 [graphify](https://github.com/safishamsi/graphify) 的代码知识图谱能力。

### 两阶段模型

| 阶段 | 操作 | 决策方 | 成本 |
|------|------|--------|------|
| **建图** | `graphify . --no-viz` | BOSS 审批 | 高（LLM API 调用） |
| **查询** | `graphify query "{问题}"` | Agent 自主 | 极低（本地计算） |
| **增量更新** | `graphify . --update` | BOSS 审批 | 中（LLM API 调用） |

### Agent 行为规则

1. 处理代码任务前，检查 `graphify-out/graph.json` 是否存在
2. 不存在 → 向 BOSS 请求建图许可
3. 已存在 → 读取 `GRAPH_REPORT.md` 获取全局概览，按需查询具体问题

### 产出物

```
graphify-out/
├── graph.html          # 交互式图谱（可选，--no-viz 跳过）
├── GRAPH_REPORT.md     # 结构概览：god nodes, communities, 惊喜连接
├── graph.json          # 可查询的图数据
└── cache/              # SHA256 缓存，增量更新用
```

### 适用 Agent

- **developer** — 核心使用者，编码时需要理解代码结构
- **architect** — 设计架构时需要了解现有依赖

详见 [ADR-004](../docs/ADR-004-knowledge-graph.md)。

## UI/UX Pro Max Skill（规划中）

### 定位

让 Agent 能生成高质量前端界面，并通过截图验证形成迭代闭环。

### 前置条件

| 依赖 | 状态 | 说明 |
|------|------|------|
| `screenshot` 工具 | ✅ 已实现 | Headless Chromium 截图 |
| `browser` 工具 | ❌ 未实现 | Playwright 自动化 |
| `execute_code` 增强 | ❌ 未实现 | 需支持 Node.js 运行时 |
| `ui-designer` Agent | ❌ 未实现 | 专门 UI 设计师角色 |

### 核心迭代闭环

```
Agent 生成 HTML/CSS 代码
    │
    ├── screenshot 工具截图 → 渲染效果
    │       │
    │       ├── 符合预期 → 进入下一组件
    │       └── 不符合 → LLM 分析截图 → 修改代码 → 再截图
    │
    └── browser 工具交互测试 → 响应式/交互验证
```

### 预计 Skill 配置

```json
{
  "ui-ux-pro-max": {
    "name": "UI/UX 专业设计",
    "description": "生成高质量前端界面并通过截图验证迭代",
    "required_tools": ["read_file", "write_file", "execute_code", "screenshot", "browser"],
    "prompt_fragment": "## UI/UX 设计规范\n- 先出 wireframe 再出实现\n- 每次修改后截图验证\n- 遵循设计系统 token\n- 响应式优先（mobile-first）",
    "category": "design"
  }
}
```

### 分阶段引入

- **Phase 1**：screenshot + browser 工具 + Node.js 运行时 + ui-designer Agent
- **Phase 2**：截图对比 + 截图式审计 + ui-ux-pro-max Skill 配置
- **Phase 3**：设计稿解析 + 多分辨率测试 + 设计资产管理

详见 [ADR-007](../docs/ADR-007-ui-ux-skill.md)。

## 配置

### 新增 Skill

在 `configs/skills.json` 添加条目：

```json
{
  "my-skill": {
    "name": "我的技能",
    "description": "一句话说明",
    "required_tools": ["read_file", "write_file"],
    "prompt_fragment": "## 我的技能\n- 规则1\n- 规则2",
    "category": "development"
  }
}
```

### 分配给 Agent

在 `configs/agents.json` 的 agent 配置中添加：

```json
{
  "developer": {
    "skills": ["code-generation", "unit-testing", "my-skill"]
  }
}
```

### 热加载

修改 `skills.json` 后调用 `POST /api/config/reload` 生效，无需重启。
