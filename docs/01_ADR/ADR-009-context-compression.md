# ADR-009: 上下文压缩策略 — RTK 竞品分析与架构决策

**日期**: 2026-04-11
**状态**: 草案
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 作为 AI 软件工厂，6 个 Agent 在 Pipeline 执行过程中会产生大量工具调用（代码读取、测试输出、Git 操作、构建日志等）。这些命令输出直接进入 LLM 上下文窗口，导致：

1. **Token 成本飙升**：一个中型项目的一次完整 Pipeline 执行，工具输出可达 100K+ tokens
2. **上下文窗口挤压**：有效信息被噪声淹没，Agent 推理质量下降
3. **长对话退化**：上下文越长，LLM 对关键信息的注意力越分散

2026 年 1 月，RTK (Rust Token Killer) 开源项目横空出世（⭐ 23K+），声称通过 CLI 代理将 LLM token 消耗降低 60-90%。本文档深入分析其上下文压缩算法，并对比业界主流方案，为 Catown 的上下文压缩策略提供决策依据。

---

## 一、竞品全景

### 1.1 RTK (Rust Token Killer)

**定位**：CLI 输出代理，位于 shell 和 LLM 之间
**语言**：Rust 单二进制，零依赖
**核心思路**：不压缩 prompt 本身，而是压缩 **工具调用的输出**

```
传统流程:  Agent → shell → 命令 → 原始输出(5000 tokens) → LLM
RTK 流程:  Agent → shell → RTK → 命令 → 过滤后输出(500 tokens) → LLM
```

**支持范围**：100+ 命令，覆盖 Git、测试框架、lint、包管理器、云 CLI 等
**接入方式**：通过 hook 系统透明改写 Bash 命令（`git status` → `rtk git status`），100% 采纳率

### 1.2 LLMLingua 系列 (Microsoft Research)

**定位**：Prompt 压缩引擎，直接压缩发给 LLM 的文本
**语言**：Python
**核心思路**：用小模型评估每个 token 的信息熵，删除低价值 token

- **LLMLingua v1**：基于 perplexity 的 token 级剪枝
- **LLMLingua-2**：基于 BERT 的 token 级压缩（更快、更准）
- **LongLLMLingua**：针对长上下文的注意力感知压缩

### 1.3 KV Cache 压缩方案

**定位**：推理层优化，压缩 Transformer 的 KV Cache
**代表项目**：FastKV、ScatterGen、Adaptive KV Cache (ICLR 2024 Oral)
**核心思路**：利用层间注意力相似性，动态裁剪/合并 KV Cache 条目
**特点**：需要修改模型推理代码，对应用层透明但侵入性强

### 1.4 Prompt Compression (学术方案)

**代表**：
- **CAPE** (Context-Aware Prompt Encoding)：句子级压缩，用上下文感知编码替代原始 prompt
- **Selective Context**：基于信息密度选择性保留上下文片段
- **Token Buncher**：对抗性 token 合并，防止 fine-tuning 攻击

---

## 二、RTK 上下文压缩算法深度剖析

RTK 的核心不是单一算法，而是一个 **分类策略体系**——针对不同命令类型采用不同的压缩策略。以下是其 12 种过滤策略的详细分析：

### 2.1 策略分类体系

| # | 策略 | 算法本质 | 压缩率 | 适用场景 |
|---|------|---------|--------|---------|
| 1 | **Stats Extraction** | 计数/聚合，丢弃细节 | 90-99% | git status/diff/log |
| 2 | **Error Only** | 丢弃 stdout，仅保留 stderr | 60-80% | 构建失败、测试错误 |
| 3 | **Grouping by Pattern** | 按规则/文件分组 + 计数 | 80-90% | lint、tsc、grep |
| 4 | **Deduplication** | 相同行合并 + 出现次数 | 70-85% | 日志输出 |
| 5 | **Structure Only** | JSON 模式提取，丢弃值 | 80-95% | JSON 输出 |
| 6 | **Code Filtering** | 语法感知的注释/函数体剥离 | 20-90% | 源码读取 |
| 7 | **Failure Focus** | 仅保留失败用例 | 94-99% | 测试框架输出 |
| 8 | **Tree Compression** | 扁平列表 → 层级树 + 计数 | 50-70% | 目录列表 |
| 9 | **Progress Filtering** | ANSI 转义序列剥离 | 85-95% | 包管理器安装 |
| 10 | **JSON/Text Dual Mode** | 优先 JSON 解析，fallback 文本 | 80%+ | ruff、pip |
| 11 | **State Machine Parsing** | 测试状态机追踪 | 90%+ | pytest |
| 12 | **NDJSON Streaming** | 逐行 JSON 解析 + 聚合 | 90%+ | go test |

### 2.2 关键算法详解

#### 2.2.1 Code Filtering (三级过滤)

RTK 在 `filter.rs` 中实现了三级代码过滤，这是最复杂的策略：

```rust
enum FilterLevel {
    None,       // 原样保留
    Minimal,    // 剥离注释 (20-40% 压缩)
    Aggressive, // 剥离注释 + 函数体 (60-90% 压缩)
}
```

**Minimal 级**：
- 识别语言特定的注释模式（`//`、`#`、`/* */`、`"""` 等）
- 删除单行注释、块注释
- 保留 doc comments（`///`、`"""docstring"""`）
- 合并连续空行（最多保留 2 行）
- 支持 11 种语言：Rust、Python、JS/TS、Go、C/C++、Java、Ruby、Shell

**Aggressive 级**：
- 在 Minimal 基础上，用正则匹配函数/类签名（`^(pub )?(async )?(fn|def|function|func|class|struct|enum|trait|interface|type) \w+`）
- 保留签名行和开/闭花括号，丢弃函数体
- 保留 import/use 语句
- Data 格式（JSON/YAML/TOML）自动降级为 Minimal，避免破坏结构

**设计精妙之处**：不依赖完整的 AST 解析，而是用轻量级正则 + 大括号深度追踪实现"近似 AST"效果。这保证了 <10ms 的开销。

#### 2.2.2 Grouping by Pattern (以 lint 为例)

原始 ESLint 输出（100 条错误，~3000 tokens）：
```
src/foo.js:5:1 error no-unused-vars 'x' is defined but never used
src/foo.js:12:1 error semi Missing semicolon
src/bar.js:3:1 error no-unused-vars 'y' is defined but never used
...
```

RTK 分组后（~100 tokens）：
```
no-unused-vars: 45 violations
semi: 32 violations
no-console: 23 violations
Files: src/foo.js, src/bar.js, src/baz.js
```

算法：按 error code 分桶 → 计数 → 按频率降序排列 → 文件路径去重

#### 2.2.3 State Machine Parsing (以 pytest 为例)

```
状态机: IDLE → TEST_START → PASSED/FAILED → SUMMARY

输入: "test_auth.py::test_login PASSED"
      "test_auth.py::test_logout FAILED"
      "test_user.py::test_create PASSED"

追踪:
  - test_login: PASSED ✓
  - test_logout: FAILED ✗  ← 保留详情
  - test_create: PASSED ✓

输出: "FAILED: 1/3 tests\ntest_logout: assertion failed at line 42"
```

核心思想：**测试通过是噪声，测试失败才是信号**。

#### 2.2.4 Deduplication (日志去重)

```
输入:
  [ERROR] Connection timeout
  [ERROR] Connection timeout
  [ERROR] Connection timeout
  [INFO] Retrying...
  [ERROR] Connection timeout

输出:
  [ERROR] Connection timeout (×4)
  [INFO] Retrying... (×1)
```

算法：滑动窗口 + 行哈希 → 相邻重复行合并 + 计数

### 2.3 架构设计亮点

1. **Hook 系统透明代理**：通过 PreToolUse hook，在 Bash 命令执行前自动改写（`git status` → `rtk git status`），Agent 完全无感知，100% 采纳率
2. **Fail-Safe 设计**：过滤失败时 fallback 到原始输出，不阻断工作流
3. **Tee 机制**：过滤后的输出标记 `[full output: ~/.local/share/rtk/tee/xxx.log]`，需要时可恢复
4. **Token 追踪**：SQLite 持久化每次命令的输入/输出 token 数和压缩率
5. **Ecosystem 模块化**：42 个命令模块按生态系统组织（git/rust/js/python/go/ruby/dotnet/cloud/system），每个模块独立实现过滤策略

---

## 三、对比分析

### 3.1 RTK vs LLMLingua vs KV Cache 压缩

| 维度 | RTK | LLMLingua v2 | KV Cache 压缩 |
|------|-----|-------------|--------------|
| **压缩对象** | 命令输出（pre-LLM） | Prompt 文本（pre-LLM） | 推理时 KV Cache |
| **侵入性** | 低（CLI 代理） | 中（需调用压缩 API） | 高（需改推理代码） |
| **压缩粒度** | 整条命令输出 | Token 级 | Token 级 |
| **信息损失** | 结构化丢弃（可控） | 统计学丢弃（概率性） | 注意力驱动丢弃 |
| **延迟开销** | <10ms | ~100ms（小模型推理） | 0（推理内完成） |
| **需要 GPU** | 否 | 是（小模型） | 是（大模型本身） |
| **压缩率** | 60-99% | 20-50% | 30-60% |
| **语义保真度** | 高（领域感知） | 中（通用统计） | 高（注意力驱动） |
| **适用场景** | AI Coding Agent | 通用 LLM 应用 | 长上下文推理服务 |
| **成本** | 免费（单二进制） | 需要小模型部署 | 需要推理框架集成 |

### 3.2 核心差异：压缩范式

| 范式 | 代表 | 思路 | 优势 | 劣势 |
|------|------|------|------|------|
| **输出级过滤** | RTK | 理解命令语义，按领域规则过滤 | 压缩率极高、语义精确、零推理开销 | 需逐命令实现、不通用 |
| **Token 级剪枝** | LLMLingua | 用小模型评估每个 token 的重要性 | 通用、不依赖命令类型 | 需要 GPU、可能误删关键信息 |
| **注意力级压缩** | KV Cache 方案 | 利用 attention score 识别冗余 | 对应用完全透明 | 需要改推理框架、部署复杂 |
| **Prompt 级重写** | Catown SOUL 体系 | 通过 prompt engineering 引导 Agent 精简输出 | 简单、无额外部署 | 效果依赖 LLM 遵从度 |

### 3.3 RTK 的独特优势

1. **领域特化**：理解 `git status` 的语义，知道"哪些文件被修改了"才是关键，而不是完整的 diff。这种领域知识是通用压缩算法（如 LLMLingua）不可能具备的。

2. **确定性压缩**：同样的命令输出，RTK 总是产生相同结果。LLMLingua 的 token 剪枝是概率性的，可能误删关键信息。

3. **零推理开销**：纯字符串处理，<10ms 延迟，不需要 GPU。对于高频工具调用场景（一次对话 50+ 命令），这至关重要。

4. **渐进式冗余消除**：`rtk git commit -m "msg"` → `"ok abc1234"`。Agent 通常只需要知道"commit 成功了，hash 是 abc1234"，不需要完整的 Git 输出。

### 3.4 RTK 的局限

1. **仅覆盖 CLI 输出**：不处理 Agent 自身生成的长文本、文件内容、API 响应等
2. **无语义理解**：不知道当前任务的上下文，无法判断哪些输出对当前任务重要
3. **被动压缩**：只能等命令执行完再过滤，无法主动引导 Agent 用更高效的命令
4. **100+ 命令的维护成本**：每个工具版本更新都可能导致解析失败

---

## 四、对 Catown 的启示与决策

### 4.1 Catown 的上下文压缩需求特殊性

Catown 与 RTK 的典型使用场景（Claude Code）有本质区别：

| 维度 | Claude Code | Catown |
|------|------------|--------|
| Agent 数量 | 1 个 | 6 个（各有独立上下文） |
| 工具调用模式 | 交互式，人工引导 | 自动化，Pipeline 驱动 |
| 上下文生命周期 | 单次会话 | 多阶段 Pipeline（跨 Agent） |
| 压缩目标 | 降低单次对话成本 | 降低全链路 token 消耗 |
| 输出消费者 | 人 + LLM | 多个 LLM（不同 Agent） |

### 4.2 推荐方案：三层压缩架构

```
┌─────────────────────────────────────────────────────────┐
│                    Catown 上下文压缩                      │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Layer 1: 输出级过滤 (RTK 策略)                          │
│  ├── 工具输出 → 按命令类型应用 12 种过滤策略             │
│  ├── 实现：集成 RTK 或复用其过滤策略                     │
│  └── 预期效果：工具输出 token -70%                       │
│                                                          │
│  Layer 2: 跨阶段摘要 (Pipeline 级)                       │
│  ├── Agent 完成 stage 后生成结构化摘要                   │
│  ├── 摘要注入下一阶段 Agent 的上下文（替代原始输出）     │
│  └── 预期效果：跨阶段 token -60%                         │
│                                                          │
│  Layer 3: 智能剪枝 (LLM 辅助)                            │
│  ├── 对非结构化文本（讨论、分析报告）做摘要              │
│  ├── 仅在上下文 > 阈值时触发                             │
│  └── 预期效果：长文本 token -50%                         │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 4.3 具体决策

#### 决策 1：Layer 1 — 复用 RTK 过滤策略，而非直接集成 RTK

**理由**：
- Catown 的 Agent 通过 Python `subprocess` 调用工具，不是 shell 环境，无法使用 RTK 的 hook 机制
- 直接在 Catown 的 `tools/` 层实现过滤更可控
- 复用 RTK 的策略分类和算法思路，而非二进制

**实现路径**：
1. 在 `tools/` 下新增 `output_filter.py`
2. 实现与 RTK 对齐的 12 种过滤策略
3. 工具执行后自动应用对应过滤器
4. 保留原始输出到 `.catown/tee/` 目录

#### 决策 2：Layer 2 — Pipeline 阶段摘要

**理由**：
- Catown 的 6 个 Agent 是串行 Pipeline，天然适合阶段摘要
- 下游 Agent 不需要上游的完整输出，只需要关键结论

**实现路径**：
1. 每个 Agent 完成 stage 后，生成结构化摘要 JSON：
   ```json
   {
     "stage": "analyst",
     "artifacts": ["PRD.md"],
     "key_decisions": ["使用 React + TypeScript", "REST API 设计"],
     "metrics": {"requirements": 12, "user_stories": 8},
     "warnings": []
   }
   ```
2. 下游 Agent 的 system prompt 中注入摘要，而非上游的完整对话历史
3. 原始对话归档到 `.catown/stages/{stage_id}/` 供追溯

#### 决策 3：Layer 3 — 条件触发的 LLM 摘要

**理由**：
- 非结构化内容（Agent 之间的讨论、分析推理过程）无法用规则过滤
- 但 LLM 摘要有成本，应仅在必要时触发

**触发条件**：
- 上下文 token 数超过 Agent 预算的 80%
- 即将进入新的 Pipeline 阶段
- 手动触发（BOSS 指令）

**实现**：
- 使用轻量模型（如 GPT-4o-mini）做摘要
- 摘要长度控制在原始内容的 10-20%

#### 决策 4：Token 追踪与可观测性

借鉴 RTK 的 SQLite 追踪机制：
1. 每次工具调用记录：原始 token 数、过滤后 token 数、压缩率
2. 每个 Pipeline 阶段记录：输入 token、输出 token、各 Layer 节省
3. 前端 Dashboard 增加 "Token Savings" 面板

### 4.4 预期收益

| 场景 | 当前 token 消耗 | 三层压缩后 | 节省 |
|------|----------------|-----------|------|
| 单次 Pipeline (小型项目) | ~80K | ~20K | -75% |
| 单次 Pipeline (中型项目) | ~200K | ~50K | -75% |
| Agent 间通信 (10 轮) | ~30K | ~8K | -73% |
| 测试输出 (pytest) | ~25K | ~2.5K | -90% |
| Git 操作 (20 次) | ~15K | ~3K | -80% |

---

## 五、实施计划

| 阶段 | 内容 | 优先级 | 预计工时 |
|------|------|--------|---------|
| Phase 1 | output_filter.py — 复用 RTK 核心策略 | P0 | 3 天 |
| Phase 2 | Pipeline 阶段摘要机制 | P0 | 2 天 |
| Phase 3 | Token 追踪 + Dashboard 面板 | P1 | 2 天 |
| Phase 4 | LLM 摘要（条件触发） | P2 | 2 天 |
| Phase 5 | Agent 间通信压缩 | P2 | 1 天 |

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 过度过滤导致信息丢失 | Agent 做出错误决策 | Tee 机制保留原始输出，-v 标志可查看 |
| 阶段摘要丢失关键细节 | 下游 Agent 缺乏上下文 | 摘要格式标准化，关键信息有专用字段 |
| LLM 摘要引入偏差 | 决策链路被污染 | 摘要与原始数据分离存储，可追溯 |
| RTK 策略不覆盖 Catown 特有工具 | 部分输出未压缩 | 可扩展的过滤器注册机制 |

---

---

## 七、落地实施分析

### 7.1 核心判断：不集成 RTK 二进制，翻译其策略

| 因素 | 分析 |
|------|------|
| **Hook 机制不可用** | RTK 靠 PreToolUse hook 拦截 shell 命令。Catown Agent 通过 Python subprocess 调工具，无 shell 环境，hook 用不上 |
| **部署复杂度** | 引入 Rust 二进制做依赖，Docker 构建链复杂度翻倍，CI/CD 需交叉编译 |
| **策略可翻译** | RTK 的 12 种策略本质是正则 + 状态机 + 聚合逻辑，Python 实现难度低 |
| **可控性** | 自有实现可针对 Catown 特有工具（code_executor、file_tool）扩展，不受上游限制 |

### 7.2 目录结构设计

```
backend/tools/
├── output_filter.py          # 新增：过滤器核心入口
├── filters/                  # 新增：各命令类型的过滤器
│   ├── __init__.py           #   过滤器注册表
│   ├── base.py               #   BaseFilter 抽象类
│   ├── git_filter.py         #   git status/diff/log/commit/push
│   ├── test_filter.py        #   pytest/cargo test/npm test/vitest
│   ├── lint_filter.py        #   ruff/eslint/tsc/golangci-lint
│   ├── build_filter.py       #   cargo build/npm run build/go build
│   ├── read_filter.py        #   文件读取（三级代码过滤）
│   └── generic_filter.py     #   通用 fallback（去重 + 进度过滤）
├── file_tool.py              # 现有
├── code_executor.py          # 现有
└── ...
```

### 7.3 OutputFilter 核心接口

```python
@dataclass
class FilterResult:
    output: str             # 过滤后的输出
    raw_tokens: int         # 原始 token 数（len // 4）
    filtered_tokens: int    # 过滤后 token 数
    savings_pct: float      # 节省百分比
    tee_path: Optional[str] # 原始输出备份路径

class OutputFilter:
    def filter(self, command: str, raw_output: str, exit_code: int) -> FilterResult:
        # 1. 路由到对应过滤器
        # 2. 执行过滤
        # 3. Tee 备份
        # 4. Token 追踪
        ...

    def register(self, command_prefix: str, filter_cls: type):
        # 注册命令前缀对应的过滤器
        ...
```

### 7.4 过滤器实现优先级与算法

#### P0 — 覆盖 70% token 节省

| 过滤器 | 对应 RTK 策略 | 核心算法 | 预期压缩率 |
|--------|-------------|---------|-----------|
| `test_filter.py` | Failure Focus + State Machine | 状态机追踪测试名称和 PASSED/FAILED，仅保留失败用例及详情 | 90-99% |
| `git_filter.py` | Stats Extraction | 解析 git 输出：status → 文件计数；diff → +N/-N；log → 提交数+统计；commit/push → ok hash | 75-99% |
| `build_filter.py` | Error Only | 丢弃 stdout，提取 stderr 中的 error/warning 行 | 60-80% |

#### P1 — 覆盖额外 15%

| 过滤器 | 对应 RTK 策略 | 核心算法 | 预期压缩率 |
|--------|-------------|---------|-----------|
| `lint_filter.py` | Grouping by Pattern | 按 error code / rule 分桶计数，按频率降序排列 | 80-90% |
| `read_filter.py` | Code Filtering (三级) | None/Minimal(去注释)/Aggressive(去函数体)，基于正则 + 大括号深度追踪 | 20-90% |

#### P2 — 覆盖剩余 15%

| 过滤器 | 对应 RTK 策略 | 核心算法 | 预期压缩率 |
|--------|-------------|---------|-----------|
| `generic_filter.py` | Deduplication + Progress | 相邻重复行合并计数 + ANSI 转义序列剥离 | 50-85% |

### 7.5 接入点：工具执行层

过滤只改工具执行层，Agent 和 Pipeline 零改动：

```python
# backend/tools/code_executor.py — 改动前
result = subprocess.run(cmd, capture_output=True, timeout=timeout)
return result.stdout.decode()

# 改动后
result = subprocess.run(cmd, capture_output=True, timeout=timeout)
raw = result.stdout.decode()
filtered = output_filter.filter(cmd, raw, result.returncode)
return filtered.output  # Agent 拿到过滤后的输出；原始输出存 tee/
```

**关键约束**：
- Agent 完全无感知——不改 prompt、不改 SOUL 体系
- 过滤失败时 fallback 到原始输出（Fail-Safe）
- FilterResult.tee_path 附在输出末尾，需要时可追溯

### 7.6 跨阶段摘要（Layer 2）

每个 Agent stage 结束后，引擎生成结构化摘要注入下一阶段：

```json
{
  "stage": "analyst",
  "artifacts": ["PRD.md"],
  "key_decisions": ["使用 React + TypeScript", "REST API"],
  "metrics": {"requirements": 12, "user_stories": 8},
  "warnings": []
}
```

- 替代上游 Agent 的完整对话历史
- 原始对话归档到 `.catown/stages/{stage_id}/`
- 摘要格式标准化，关键信息有专用字段保证可追溯

### 7.7 Token 追踪与可观测性

借鉴 RTK 的 SQLite 追踪：

| 维度 | 记录内容 |
|------|---------|
| 每次工具调用 | 命令、原始 token、过滤后 token、压缩率、时间戳 |
| 每个 Pipeline 阶段 | 输入 token、输出 token、各 Layer 节省 |
| 前端 Dashboard | Token Savings 面板：实时压缩率、累计节省、趋势图 |

### 7.8 不做的事

1. **不做 LLM 辅助摘要（Layer 3）**——成本高、延迟大、引入偏差，先把规则层做好
2. **不一次性实现 12 种策略**——P0 的 3 个先上线验证效果
3. **不改 Agent 的 prompt**——过滤在工具层做，不污染 SOUL 体系
4. **不跳过 tee 机制**——过滤掉的信息必须可追溯

### 7.9 验证方式

上线后跑一个完整 Pipeline，对比 output_filter 的 token 追踪日志和 Dashboard 上的 token 消耗曲线。P0 三个过滤器到位后，工具输出 token 应直接降低 60-70%。

---


## 参考

- RTK 仓库: https://github.com/rtk-ai/rtk
- RTK 架构文档: docs/contributing/ARCHITECTURE.md
- LLMLingua-2: https://www.microsoft.com/en-us/research/project/llmlingua/llmlingua-2/
- FastKV (KV Cache 压缩): https://arxiv.org/abs/2502.01068
- Catown ADR-008 (Skills 渐进式披露): docs/ADR-008-skills-progressive-disclosure.md
