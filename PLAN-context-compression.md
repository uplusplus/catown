# feature/context-compression 开发计划

> **分支**: `feature/context-compression`
> **基准**: master `cf84342`
> **文档**: ADR-009 (`docs/ADR-009-context-compression.md`) + PRD Section 21 (`docs/PRD.md`)
> **目标**: 降低 Catown 全链路 token 消耗 ~75%，Agent 零改动

---

## 🧭 快速恢复指南（给未来的我）

你在一个新 session 里，没有记忆。做这些事：

1. 读 `docs/ADR-009-context-compression.md` — 竞品分析 + 架构决策
2. 读 `docs/PRD.md` 的 Section 21 — 11 个需求（CC-001 ~ CC-011）和验收标准
3. 读这个文件 — 开发计划和进度
4. `git checkout feature/context-compression` 切到开发分支
5. 按下面的 Phase 继续

**核心原则**（写在 ADR 里了，这里重复强调）：
- 过滤只在工具执行层做，**不改 Agent prompt / SOUL / 配置**
- 过滤失败必须 fallback 到原始输出（**Fail-Safe**）
- 被过滤掉的信息必须可通过 tee 机制恢复（**可追溯**）
- **不引入外部依赖**，纯 Python 标准库

---

## Phase 1: 框架 + P0 过滤器 + Tee（3 天）

**需求**: CC-001, CC-002, CC-003, CC-004, CC-008

### 1.1 过滤器框架 (`tools/output_filter.py` + `tools/filters/`)

```
backend/tools/filters/
├── __init__.py       # FilterRegistry，过滤器注册表
├── base.py           # BaseFilter 抽象类
├── git_filter.py     # CC-003
├── test_filter.py    # CC-002
├── build_filter.py   # CC-004
└── generic_filter.py # fallback（Phase 4 完善，先留骨架）
```

**BaseFilter 接口**:
```python
class BaseFilter(ABC):
    @abstractmethod
    def match(self, command: str) -> bool:
        """是否匹配此过滤器"""
        ...

    @abstractmethod
    def apply(self, raw_output: str, exit_code: int) -> str:
        """执行过滤，返回过滤后文本"""
        ...
```

**OutputFilter 核心**:
```python
class FilterResult:
    output: str
    raw_tokens: int        # len(raw) // 4
    filtered_tokens: int   # len(output) // 4
    savings_pct: float
    tee_path: str | None

class OutputFilter:
    def __init__(self, tee_dir=".catown/tee", tee_max_files=50):
        self._filters: list[BaseFilter] = []
        self._tee_dir = tee_dir
        self._tee_max = tee_max_files

    def register(self, filt: BaseFilter): ...
    def filter(self, command: str, raw_output: str, exit_code: int) -> FilterResult: ...
    def _route(self, command: str) -> BaseFilter | None: ...
    def _tee(self, command: str, raw_output: str) -> str | None: ...
```

**路由逻辑**：按注册顺序遍历 `_filters`，调用 `match()`，第一个命中的生效。无命中 → return 原文。

### 1.2 CC-003: Git 输出过滤

| 子命令 | 输出格式 | 算法 |
|--------|---------|------|
| `git status` | `3 files changed: 2 modified, 1 added` | 解析 `Changes to be committed` / `Changes not staged` / `Untracked` 段 |
| `git diff` | `5 files changed, +142, -89` | 提取最后的 summary 行（`N files changed, +X, -Y`） |
| `git log` | `5 commits, +142, -89` | 统计提交数 + `--shortstat` 聚合 |
| `git add` | `ok` | exit_code 0 → ok |
| `git commit` | `ok abc1234` | 解析 commit hash |
| `git push` | `ok main` | 解析分支名 |
| `git pull` | `ok 3 files +10 -2` | 解析 summary |

**关键**：别用正则硬解析所有 git 输出格式。优先检查 exit_code，失败时返回 stderr。成功时按子命令分支处理。

### 1.3 CC-002: 测试输出过滤

**pytest 状态机**：
```
IDLE → 看到测试名 → 看到 PASSED/FAILED → 记录 → SUMMARY
```

输出格式：
```
FAILED: 2/15 tests
  test_auth_login: AssertionError at test_auth.py:42
  test_overflow: OverflowError at utils.py:18
```

**实现要点**：
- 逐行扫描，用正则匹配 `test_xxx ... PASSED/FAILED`
- 只保留 FAILED 的行及其附近上下文（后续几行通常是 traceback）
- 最后输出汇总行

**先只支持 pytest**，vitest/cargo test 留接口后续扩展。

### 1.4 CC-004: 构建输出过滤

```python
if exit_code == 0:
    return "Build succeeded"
else:
    # 从 raw_output 中提取 error/warning 行
    # 去掉 ANSI 转义
    # 去掉进度条行（包含 \r 的）
    # 保留包含 "error" / "Error" / "warning" / "Warning" 的行
```

### 1.5 CC-008: Tee 机制

```python
def _tee(self, command: str, raw_output: str) -> str | None:
    ts = int(time.time())
    safe_cmd = re.sub(r'[^\w\-]', '_', command)[:50]
    filename = f"{ts}_{safe_cmd}.log"
    path = os.path.join(self._tee_dir, filename)
    os.makedirs(self._tee_dir, exist_ok=True)
    with open(path, 'w') as f:
        f.write(raw_output)
    # 清理旧文件（保留最近 N 个）
    self._rotate_tee()
    return path
```

### 1.6 接入工具执行层

找到 `backend/tools/code_executor.py`（或实际执行命令的地方），在 `subprocess.run` 之后加：

```python
from .output_filter import output_filter

result = subprocess.run(...)
raw = result.stdout.decode()
filtered = output_filter.filter(command, raw, result.returncode)
# tee 路径追加到输出末尾
output = filtered.output
if filtered.tee_path:
    output += f"\n[full output: {filtered.tee_path}]"
return output
```

**⚠️ 先找到 `code_executor.py` 的实际路径和调用方式，可能在 `tools/` 或 `pipeline/` 里。**

---

## Phase 2: 跨阶段摘要（2 天）

**需求**: CC-010

在 Pipeline 引擎里，每个 Agent stage 结束后：

1. 从 Agent 的消息历史中提取结构化摘要
2. 摘要存入 pipeline 阶段记录
3. 下一阶段 Agent 的 system prompt 注入摘要（而非上游完整历史）

摘要格式：
```json
{
  "stage": "analyst",
  "artifacts": ["PRD.md"],
  "key_decisions": ["React + TypeScript", "REST API"],
  "metrics": {"requirements": 12},
  "warnings": []
}
```

**⚠️ 需要先读懂 `backend/pipeline/` 的阶段执行逻辑，找到注入上下文的位置。**

---

## Phase 3: P1 过滤器 + Token 追踪 + Dashboard（3 天）

**需求**: CC-005, CC-006, CC-009, CC-011

### CC-005: Lint 输出过滤
- 支持 ruff JSON 模式
- 按 rule 分桶计数，降序排列

### CC-006: 文件读取过滤
- Minimal 级：去注释（识别 `//`、`#`、`/* */` 等）
- Aggressive 级：去函数体（正则匹配签名 + 大括号深度追踪）

### CC-009: Token 追踪
- 新建 `token_tracking` 表（或加到现有数据库）
- OutputFilter 每次调用自动写入
- API 端点 `GET /api/token-savings`

### CC-011: Dashboard 面板
- 前端新增 Token Savings Tab
- 调用 CC-009 的 API 展示数据

---

## Phase 4: 通用过滤器 + 收尾（1 天）

**需求**: CC-007

- `generic_filter.py`：相邻重复行合并 + ANSI 剥离
- 全面测试所有过滤器
- 更新验收清单

---

## 进度追踪

| Phase | 状态 | 日期 | 备注 |
|-------|------|------|------|
| Phase 1 | ⬜ 待做 | — | 框架 + P0 过滤器 + Tee + 接入 |
| Phase 2 | ⬜ 待做 | — | 跨阶段摘要 |
| Phase 3 | ⬜ 待做 | — | P1 过滤器 + Tracking + Dashboard |
| Phase 4 | ⬜ 待做 | — | 通用过滤器 + 收尾 |

---

## 关键文件参考

| 文件 | 用途 |
|------|------|
| `docs/ADR-009-context-compression.md` | 竞品分析 + 架构决策（含落地分析） |
| `docs/PRD.md` Section 21 | 11 个需求定义和验收标准 |
| `backend/tools/code_executor.py` | 工具执行层，接入点（需确认实际路径） |
| `backend/pipeline/` | Pipeline 引擎，阶段摘要注入点 |
| `backend/main.py` | FastAPI 入口，API 端点注册 |
