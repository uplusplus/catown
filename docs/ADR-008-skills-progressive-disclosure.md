# ADR-008: Skills 渐进式披露机制

**日期**: 2026-04-10
**状态**: 已确认
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 的 Skill 体系当前定义在 `agents.json` 的 `skills` 数组中，但 `skills.json` 配置文件不存在——Agent 引用了 17 个 skill 名称（如 `code-generation`、`architecture-design`），却没有对应的定义（`prompt_fragment`、`required_tools` 等）。Skills 注入机制形同虚设。

同时，如果每个 skill 都将完整 `prompt_fragment` 注入 system prompt，6 个 Agent × 17 个 skill = 大量冗余 token。实际上 Agent 大部分时候只需要知道自己"能做什么"，只有执行特定任务时才需要详细指引。

需要设计一种既能补全 skill 定义、又能控制 token 消耗的方案。

## 候选方案

### 方案 A：单层全量注入（当前 PRD 方案）

每个 skill 定义一个 `prompt_fragment`，Agent 配置了哪些 skill 就注入哪些完整内容。

```
Agent system prompt:
├── 灵魂层
├── 角色层
├── code-generation 的完整 prompt_fragment (200 token)
├── unit-testing 的完整 prompt_fragment (200 token)
├── refactoring 的完整 prompt_fragment (200 token)
└── ...
```

| 维度 | 评估 |
|------|------|
| 实现复杂度 | 低 |
| Token 消耗 | 高（6 Agent × ~3 skill × 200 token = ~3600 token 纯 skill 内容） |
| 灵活性 | 差——Agent 不在对应 stage 时也被注入完整指引 |
| 深度参考 | 不支持——prompt_fragment 受 token 预算限制 |

### 方案 B：三级内容 + 按需注入（推荐）

每个 skill 定义三个层级的内容，由引擎根据上下文决定注入哪一级。

```
Agent system prompt:
├── 灵魂层
├── 角色层
├── hint: code-generation (30 token) ← 始终注入
├── hint: unit-testing (30 token)    ← 始终注入
├── hint: refactoring (30 token)     ← 始终注入
├── guide: code-generation (150 token) ← 仅 development stage 注入
├── guide: unit-testing (150 token)    ← 仅 development stage 注入
└── ...
```

Agent 需要深入时，通过 `read_file` 读取 `.catown/skills/` 下的 `full` 文档。

### 方案 C：纯按需加载（工具调用式）

不注入任何 skill 内容到 system prompt。Agent 需要时通过 `[USE_SKILL: name]` 语法请求，引擎动态返回。

| 维度 | 评估 |
|------|------|
| Token 消耗 | 最低 |
| 实现复杂度 | 高——需改造 system prompt 组装 + 新增拦截机制 |
| 问题 | Agent 不知道自己有什么能力，需要先探索才能发现 |

## 决策

**采用方案 B：三级内容 + 按需注入。**

### 决策理由

1. **Token 效率好**：hint 层始终注入（~30 token/skill），guide 层仅当前 stage 注入（~150 token/skill），比全量注入节省约 30%
2. **Agent 自主性强**：full 层通过文件系统暴露，Agent 可按需 `read_file` 深入，不需要引擎介入
3. **与现有架构兼容**：不改变 system prompt 组装的基本模式，只是在 `prompt_fragment` 基础上拆分层级
4. **实现成本低**：新增 `levels` 字段，修改 `build_system_prompt()` 函数，写入 `.catown/skills/` 文件——都是增量改动
5. **不选方案 C 的理由**：Agent 需要知道自己"能做什么"才能有效工作。完全不注入会导致 Agent 在需要某能力时不知道该去查什么

## 方案设计

### 技能数据模型

配置文件：`backend/configs/skills.json`

```json
{
  "code-generation": {
    "name": "代码生成",
    "description": "根据技术规范生成高质量代码",
    "required_tools": ["read_file", "write_file", "list_files"],
    "category": "development",
    "levels": {
      "hint": "代码生成: 遵循项目风格，函数须有 docstring，代码写 src/",
      "guide": "## 代码生成规范\n- 遵循项目已有代码风格和约定\n- 每个函数必须有 docstring\n- 写代码前先 read_file 了解现有结构\n- 代码写入 src/ 目录，测试写入 tests/ 目录",
      "full": "## 代码生成完整指南\n\n### 流程\n1. 先 read_file 了解项目结构和现有约定\n2. 设计函数签名，确认接口与 tech-spec 一致\n3. 实现代码，每个函数必须有 docstring（参数、返回值、异常）\n4. 写完后 execute_code 验证 import 无报错\n\n### 命名约定\n- 函数/变量: snake_case\n- 类: PascalCase\n- 常量: UPPER_SNAKE_CASE\n- 测试函数: test_<module>_<behavior>\n\n### 常见错误\n- 不要在函数体内 import\n- 不要用 bare except\n- 路径操作用 pathlib 而非 os.path\n\n### 代码审查清单\n- [ ] 函数 < 50 行\n- [ ] 无硬编码配置\n- [ ] 错误处理完善\n- [ ] 类型注解完整"
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | Skill 显示名 |
| `description` | string | 一句话说明 |
| `required_tools` | string[] | 依赖的工具列表（校验用，不授予权限） |
| `category` | string | 分类标签 |
| `levels.hint` | string | 一句话能力提示，始终注入 system prompt |
| `levels.guide` | string | 核心用法指引，stage 激活时注入 |
| `levels.full` | string | 完整文档，写入文件系统按需读取 |

### 注入策略

```python
def build_system_prompt(agent_config, skill_configs, stage_config=None):
    parts = []

    # 1. 灵魂层、角色层、规则层
    parts.append(build_soul_section(agent_config))
    parts.append(build_role_section(agent_config))

    # 2. Skills hint — 始终注入（轻量）
    hints = []
    for skill_name in agent_config.get("skills", []):
        skill = skill_configs.get(skill_name)
        if skill and "hint" in skill.get("levels", {}):
            hints.append(skill["levels"]["hint"])
    if hints:
        parts.append("## 可用技能\n" + "\n".join(f"- {h}" for h in hints))

    # 3. Skills guide — 仅当前 stage 的 active_skills 注入
    if stage_config and stage_config.get("active_skills"):
        guides = []
        for skill_name in stage_config["active_skills"]:
            skill = skill_configs.get(skill_name)
            if skill and "guide" in skill.get("levels", {}):
                guides.append(skill["levels"]["guide"])
        if guides:
            parts.append("\n\n".join(guides))

    # 4. 项目记忆、长期记忆
    if project_memory:
        parts.append(f"## 项目上下文\n{project_memory}")
    if long_term_memory:
        parts.append(f"## 你的经验\n{long_term_memory}")

    return "\n\n".join(parts)
```

### Full 层存储

`full` 内容不注入 system prompt，而是以 Markdown 文件形式写入项目 workspace：

```
projects/{project_id}/
└── .catown/
    └── skills/
        ├── code-generation.md
        ├── unit-testing.md
        ├── architecture-design.md
        └── ...
```

引擎在 Pipeline 启动时，将该 Agent 配置的所有 skill 的 `full` 内容写入此目录。Agent 通过 `read_file` 按需读取。

### Pipeline stage 技能绑定

`pipelines.json` 的 stage 配置新增 `active_skills` 和 `hint_only_skills` 字段：

```json
{
  "stages": [
    {
      "name": "development",
      "agent": "developer",
      "active_skills": ["code-generation", "unit-testing", "refactoring"],
      "hint_only_skills": ["knowledge-graph", "debugging"]
    }
  ]
}
```

| 字段 | 行为 |
|------|------|
| `active_skills` | 注入 `guide` 层到 system prompt |
| `hint_only_skills` | 只保留 `hint`（Agent 知道有此能力，需要时自行深入） |
| 未列出的 skill | 不注入任何内容 |

### Agent 视角示例

```
Developer Agent 的 system prompt（development stage）:

## 可用技能
- 代码生成: 遵循项目风格，函数须有 docstring，代码写 src/
- 单元测试: 每个公共函数至少一个用例，覆盖正常路径和边界
- 重构: 小步提交，重构前后跑测试确认行为不变
- 知识图谱: 代码任务前检查 graphify-out/，查询为本地计算无需审批
- 调试: 用 execute_code 定位问题，先复现再修

## 代码生成规范              ← active_skills 注入
- 遵循项目已有代码风格和约定
- 每个函数必须有 docstring
- 写代码前先 read_file 了解现有结构
- 代码写入 src/ 目录，测试写入 tests/ 目录

## 测试规范                  ← active_skills 注入
- 每个公共函数至少一个测试用例
- 测试覆盖正常路径和边界条件
- 用 execute_code 运行测试验证通过
- 测试文件命名: test_<module>.py

[Agent 需要深入了解重构细节时]
→ read_file(.catown/skills/refactoring.md) 按需获取 full 内容
```

## Token 节省估算

| Agent | 旧方案（全量注入） | 新方案（三级） | 节省 |
|-------|-------------------|--------------|------|
| developer (4 skills) | 4 × 200 = 800 token | 4×30 + 3×150 = 570 token | 29% |
| analyst (3 skills) | 3 × 200 = 600 token | 3×30 + 2×150 = 390 token | 35% |
| 6 Agent 全部 | ~5000 token | ~3200 token | ~36% |

注：旧方案 Agent 不在对应 stage 时仍然注入完整 prompt_fragment，浪费更大。新方案通过 stage 绑定消除了这部分浪费。

## 不采用的方案

### 为什么不选方案 A（单层全量注入）

1. Token 浪费——Agent 不在对应 stage 时仍被注入完整指引
2. prompt_fragment 大小受限——无法容纳深度参考文档
3. 无按需扩展能力——Agent 想深入了解没有途径

### 为什么不选方案 C（纯按需加载）

1. Agent 不知道自己有什么能力——不知道该查什么
2. 需要新增拦截机制——改造 system prompt 组装链路
3. 每次使用都需要额外的工具调用——增加延迟和 token

## 影响模块

| 模块 | 改动 |
|------|------|
| `backend/configs/skills.json` | **新增**，17 个 skill 三级定义 |
| `backend/configs/pipelines.json` | stage 新增 `active_skills` / `hint_only_skills` |
| `backend/pipeline/engine.py` | `build_system_prompt()` 改为三级注入；启动时写 `.catown/skills/` |
| `backend/agents/config_manager.py` | 加载 skills.json，校验 agent skills 与 tools 一致性 |
| `docs/PRD.md` | §4.5 更新为三级模型 |
| `wiki/skills.md` | 更新数据模型和工作机制 |

## 与 PRD 的关系

本 ADR 替代 PRD §4.5 中的 skills 体系描述（原方案为单层 `prompt_fragment`）。PRD 中的以下部分保持不变：

- §4.5 "工具/技能白名单机制"——工具权限来源仍为 `agents.json` 的 `tools` 字段
- §4.5 "Skills 工作机制"中的"工具访问联动"——`required_tools` 校验逻辑不变
- §4.5 "与工具白名单的关系"——tools = 权限边界，skills = 行为指引的关系不变

## 参考

- PRD §4.5 — 工具/技能白名单机制
- PRD §5 — Pipeline 工作流引擎
- ADR-004 — 知识图谱集成方案（knowledge-graph skill 的定义参考）
