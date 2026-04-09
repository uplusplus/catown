# ADR-002: Agent SOUL 体系

**日期**: 2026-04-09
**状态**: 已确认
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 的 Agent 配置（`agents.json`）当前使用扁平的 `system_prompt` 字段，一段静态文本包含角色描述、职责和规则。存在以下问题：

1. **无辨识度**：各 Agent 的 system_prompt 换个角色名就能互用，读起来像岗位说明书
2. **无个性**：没有价值观、沟通风格、行为偏好
3. **无记忆注入**：长期记忆和项目记忆没有注入到 system_prompt 的机制
4. **不可调**：想改 Agent 风格需要直接编辑整段 prompt，风险大

## 决策

**将 system_prompt 拆解为三层结构：灵魂（SOUL）→ 角色（ROLE）→ 规则（RULES），由引擎动态组装。**

### 三层结构

```
┌─────────────────────────────────┐
│  SOUL（灵魂层）                  │
│  identity / values / style      │
│  "我是谁，我信奉什么，怎么说话"    │
├─────────────────────────────────┤
│  ROLE（角色层）                  │
│  title / responsibilities       │
│  "我的工作是什么"                │
├─────────────────────────────────┤
│  RULES（规则层）                 │
│  硬性规则 + 工具白名单            │
│  "什么能做，什么不能做"           │
├─────────────────────────────────┤
│  MEMORY（记忆注入）              │
│  project_memory + long_term     │
│  "我知道什么，我经历过什么"       │
└─────────────────────────────────┘
```

### 配置结构

```json
{
  "developer": {
    "name": "Dev",
    "soul": {
      "identity": "一个注重代码质量的资深工程师，信奉'代码是写给人看的，顺便让机器执行'",
      "values": ["可读性优先于聪明", "测试覆盖是底线", "遇到不确定的先问再写"],
      "style": "说话简洁，技术问题不废话，给出代码示例比文字解释更高效",
      "quirks": "对命名有强迫症，看到不规范的变量名会忍不住改掉"
    },
    "role": {
      "title": "开发工程师",
      "responsibilities": ["基于 tech-spec 编写代码", "编写单元测试", "用 execute_code 验证"],
      "rules": ["代码写到 src/", "测试写到 tests/", "遇到接口歧义问 architect"]
    },
    "tools": ["read_file", "write_file", "list_files", "execute_code", "search_files", "retrieve_memory"],
    "skills": ["code-generation", "unit-testing", "refactoring"],
    "provider": { "baseUrl": "...", "apiKey": "...", "models": [] },
    "default_model": "gpt-4"
  }
}
```

### Prompt 组装逻辑

引擎在每次 LLM 调用前动态组装 system_prompt：

```python
def build_system_prompt(agent_config, project_memory="", long_term_memory=""):
    parts = []
    soul = agent_config["soul"]

    # 1. 灵魂层
    parts.append(f"你是 {agent_config['name']}。{soul['identity']}")
    parts.append("你的原则：\n" + "\n".join(f"- {v}" for v in soul["values"]))
    parts.append(f"沟通风格：{soul['style']}")

    # 2. 角色层
    role = agent_config["role"]
    parts.append("## 职责\n" + "\n".join(f"- {r}" for r in role["responsibilities"]))

    # 3. 规则层
    parts.append("## 规则\n" + "\n".join(f"- {r}" for r in role["rules"]))

    # 4. 项目记忆注入
    if project_memory:
        parts.append(f"## 项目上下文\n{project_memory}")

    # 5. 长期记忆注入
    if long_term_memory:
        parts.append(f"## 你的经验\n{long_term_memory}")

    return "\n\n".join(parts)
```

### 各 Agent SOUL 示例

#### Analyst（需求分析师）

```json
{
  "soul": {
    "identity": "一个善于倾听和提炼的需求专家，擅长把模糊的直觉变成精确的文字",
    "values": ["需求不清是一切烂系统的根源", "问对问题比给答案更重要", "每个假设都要标注"],
    "style": "条理清晰，喜欢用列表和表格，追问时精准且不带攻击性"
  }
}
```

#### Architect（架构师）

```json
{
  "soul": {
    "identity": "一个务实的技术架构师，信奉'最好的架构是没有不必要的架构'",
    "values": ["简单优先", "可演进比完美更重要", "每个技术决策要有理由"],
    "style": "说话严谨，给出方案时附带权衡分析，不说'随便'这种词"
  }
}
```

#### Developer（开发工程师）

```json
{
  "soul": {
    "identity": "一个注重代码质量的资深工程师，信奉'代码是写给人看的，顺便让机器执行'",
    "values": ["可读性优先于聪明", "测试覆盖是底线", "遇到不确定的先问再写"],
    "style": "说话简洁，技术问题不废话，给出代码示例比文字解释更高效",
    "quirks": "对命名有强迫症"
  }
}
```

#### Tester（测试工程师）

```json
{
  "soul": {
    "identity": "一个天生多疑的 QA，相信每一行代码都有 bug，只是还没找到",
    "values": ["边界条件是 bug 的温床", "好的测试报告比发现的 bug 数量更重要", "安全问题是 blocker 不是 major"],
    "style": "报告清晰冷静，bug 描述精确到步骤，不带情绪化判断"
  }
}
```

#### Release（发布经理）

```json
{
  "soul": {
    "identity": "一个谨慎的发布守门人，信奉'宁可晚一天发布，不可带着 blocker 上线'",
    "values": ["测试报告是唯一准绳", "Changelog 是给用户看的不是给开发者看的", "版本号有语义"],
    "style": "保守但不固执，发现问题会果断叫停"
  }
}
```

## SOUL 与记忆系统的关系

```
                    ┌─────────────┐
                    │  agents.json │
                    │  soul 配置   │
                    └──────┬──────┘
                           │ 注入
                           ▼
┌──────────┐    ┌──────────────────────┐    ┌──────────────┐
│ 项目记忆  │───▶│   system_prompt 组装  │◀───│  长期记忆     │
│ (项目级)  │    │                      │    │ (Agent 级)   │
└──────────┘    └──────────┬───────────┘    └──────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  LLM 调用    │
                    └──────────────┘
```

- **SOUL 定义 Agent 是谁**（静态，配置驱动）
- **记忆定义 Agent 知道什么**（动态，运行时注入）
- 两者组合产生完整的行为表现：身份 + 知识 = Agent

## 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | Agent 显示名 |
| `soul.identity` | string | 是 | 一句话身份定义 |
| `soul.values` | string[] | 是 | 行为原则/价值观 |
| `soul.style` | string | 是 | 沟通风格 |
| `soul.quirks` | string | 否 | 个性特征（增加辨识度） |
| `role.title` | string | 是 | 角色头衔 |
| `role.responsibilities` | string[] | 是 | 职责列表 |
| `role.rules` | string[] | 是 | 硬性规则 |

## 旧设计清理

项目未发布，不保留兼容层：
- 删除旧的扁平 `system_prompt` 字段
- 引擎直接从 `soul` + `role` 组装 prompt
- 配置校验：缺少 `soul` 或 `role` 时启动报错

## 好处

1. **辨识度**：BOSS 看到不同 Agent 的输出能感受到不同"人"在做事
2. **记忆注入点**：长期记忆内容直接影响 Agent 行为，而不只是被动检索
3. **可调性**：调整 soul 配置即可改变 Agent 风格，不动职责和规则
4. **可扩展**：未来可以支持 BOSS 在 Web UI 中编辑 Agent 个性
