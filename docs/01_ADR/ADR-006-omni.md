# ADR-006: OMNI 多模态能力集成方案

**日期**: 2026-04-10
**状态**: 草案
**决策者**: BOSS + AI 架构分析

---

## 背景

Catown 当前仅支持纯文本交互。但在软件开发场景中，BOSS 经常需要通过截图、录屏、语音描述需求，Agent 也需要分析 UI 截图、架构图、错误截图等视觉信息。

**现状**：
- LLM Client（`llm/client.py`）只发纯文本 `messages`
- 工具层无图片/音频/视频处理能力
- Skills 无多模态相关定义
- Pipeline 只接受文本需求输入

## 目标

让 Catown 支持四类多模态输入：**图片**、**PDF 文档**、**视频**、**音频**，贯穿 BOSS 输入 → Agent 处理 → 产出物全链路。

## 方案

### 1. 多模态能力分级

| 能力 | 场景 | 优先级 | 实现阶段 |
|------|------|--------|---------|
| **图片理解** | UI 截图需求、架构图分析、错误截图排查 | P0 | V1 |
| **PDF 文档分析** | 需求文档、合同/规范、测试报告、产品资料分析 | P0 | V1.1 |
| **视频分析** | 录屏演示需求、Bug 复现视频 | P1 | V2 |
| **音频转录** | 语音描述需求、会议录音 | P1 | V2 |

### 2. 架构设计

```
┌─────────────────────────────────────────────────┐
│                  BOSS 输入                        │
│  文本 / 图片 / 视频 / 音频 / 混合                 │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│           多模态预处理层 (Multimodal Processor)    │
│                                                   │
│  图片 → base64 + 尺寸元数据                       │
│  PDF → 原生 file input 或本地文本抽取              │
│  视频 → ffmpeg 抽帧 → 多张图片                     │
│  音频 → 语音转录 → 文本                            │
└──────────────┬──────────────────────────────────┘
               │ 统一输出: [{type, data, metadata}]
               ▼
┌─────────────────────────────────────────────────┐
│          LLM Client (改造)                        │
│                                                   │
│  chat() 支持 multimodal messages                  │
│  [                                                  │
│    {"type": "text", "text": "..."},               │
│    {"type": "image_url", "image_url": {"url": "data:..."}} │
│  ]                                                  │
└──────────────┬──────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────┐
│          Agent 工具层                              │
│                                                   │
│  analyze_image  — 图片分析（P0）                  │
│  analyze_document — PDF 文档分析（P0）             │
│  transcribe_audio — 音频转录（P1）                │
│  extract_video_frames — 视频抽帧（P1）            │
└─────────────────────────────────────────────────┘
```

### 3. LLM Client 改造

当前 `chat()` 只接受 `List[Dict[str, str]]`，需扩展支持 OpenAI 多模态格式：

```python
# 改造前
messages = [
    {"role": "user", "content": "分析这个UI设计"}
]

# 改造后
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "分析这个UI设计"},
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,iVBOR...",
                    "detail": "high"  # low / high / auto
                }
            }
        ]
    }
]
```

**改动点**：

| 文件 | 改动 |
|------|------|
| `llm/client.py` | `chat()` / `chat_stream()` 的 `messages` 参数兼容 `str` 和 `list` 两种 content 格式 |
| `llm/client.py` | 新增 `supports_multimodal()` 方法，检查模型是否支持 vision |
| `llm/client.py` | 新增 `_prepare_multimodal_messages()` 内部方法，统一格式化 |
| `llm/client.py` | 支持 PDF file part：`{type: "file", file: {filename, file_data, mime_type}}`，用于 provider 支持原生 PDF 输入时的快速路径 |

**模型能力声明**（agents.json）：

```json
{
  "developer": {
    "provider": {
      "baseUrl": "...",
      "apiKey": "...",
      "models": [
        {
          "id": "gpt-4o",
          "capabilities": ["text", "vision"]
        }
      ]
    },
    "default_model": "gpt-4o"
  }
}
```

### 4. 工具层新增

#### 4.1 analyze_image（P0）

```python
class AnalyzeImageTool(BaseTool):
    name = "analyze_image"
    description = "分析图片内容：UI 截图、架构图、错误截图等"

    # 参数
    #   image_path: str — 图片文件路径（workspace 内）
    #   prompt: str — 分析指令（如"这个 UI 有什么问题"）
    #   detail: str — "low" | "high" | "auto"（默认 "auto"）

    # 实现
    #   1. 读取图片文件，转 base64
    #   2. 构造 multimodal message 发送给 LLM
    #   3. 返回 LLM 分析结果
```

#### 4.2 transcribe_audio（P1）

```python
class TranscribeAudioTool(BaseTool):
    name = "transcribe_audio"
    description = "将音频文件转录为文本"

    # 参数
    #   audio_path: str — 音频文件路径
    #   language: str — 语言代码（可选，自动检测）

    # 实现
    #   方案 A: 调用 OpenAI Whisper API（/v1/audio/transcriptions）
    #   方案 B: 本地 whisper 模型（需额外依赖）
    #   第一版走方案 A，简单可控
```

#### 4.3 extract_video_frames（P1）

```python
class ExtractVideoFramesTool(BaseTool):
    name = "extract_video_frames"
    description = "从视频中抽取关键帧图片"

    # 参数
    #   video_path: str — 视频文件路径
    #   fps: float — 抽帧频率（默认 1fps）
    #   max_frames: int — 最大帧数（默认 10）

    # 实现
    #   使用 ffmpeg 抽帧，输出到 workspace 临时目录
    #   返回帧图片路径列表，供 analyze_image 使用
```

#### 4.4 analyze_document（P0 / PDF）

```python
class AnalyzeDocumentTool(BaseTool):
    name = "analyze_document"
    description = "分析 PDF 文档：需求文档、合同/规范、测试报告、产品资料等"

    # 参数
    #   file_path: str — PDF 文件路径（workspace 内）
    #   prompt: str — 分析指令；为空时仅返回提取文本
    #   mode: str — "auto" | "extract" | "analyze"
    #   page_start: int — 从第几页开始（1-based）
    #   max_pages: int — 最大读取页数
    #   max_chars: int — 最大抽取字符数，防止超出上下文预算

    # 实现
    #   1. 校验路径必须在 workspace 内，限制文件大小
    #   2. 使用 pypdf 抽取 PDF 文本层，保留页码分段
    #   3. 如果 prompt 存在，则把抽取文本交给 LLM 做结构化分析
    #   4. 如果无法抽取文本，提示扫描件 PDF 需要 OCR/图片分析后续阶段
```

PDF 支持采用双路径：
1. **原生多模态路径**：上传 PDF 后，后端把 PDF 编码为 `data:application/pdf;base64,...`，作为 OpenAI 兼容 file part 交给支持 PDF 输入的模型。
2. **本地解析兜底路径**：当 provider 不支持原生 PDF、需要页码证据、或文档过长需要缩小范围时，Agent 调用 `analyze_document` 先抽取文本，再进行分析。

### 5. Skill 层新增

```json
{
  "multimodal-analysis": {
    "name": "多模态分析",
    "description": "分析图片、PDF 文档、视频、音频内容",
    "required_tools": ["analyze_image", "analyze_document", "transcribe_audio", "extract_video_frames", "read_file", "write_file"],
    "prompt_fragment": "## 多模态分析规范\n- 收到图片附件时，优先使用 analyze_image\n- 收到 PDF 附件时，优先走原生 file input；如 provider 不支持或需要页码证据，使用 analyze_document\n- 视频先用 extract_video_frames 抽帧，再逐帧分析\n- 音频用 transcribe_audio 转录\n- 分析结果结构化输出，包含关键发现和建议",
    "category": "analysis"
  }
}
```

适用 Agent：
- **analyst** — 分析需求截图/原型图
- **developer** — 分析错误截图/UI 设计稿
- **tester** — 分析 Bug 截图/复现录屏
- **valet** — 识别用户上传 PDF 的意图，必要时委派给 analyst/developer/tester

### 6. Pipeline 需求输入改造

当前 Pipeline 只接受文本需求。改造后支持附件：

```
POST /api/pipelines/{id}/start
Content-Type: multipart/form-data

{
  "requirement": "做一个用户管理系统",
  "attachments": [
    {"type": "image", "path": "uploads/ui-mockup.png"},
    {"type": "image", "path": "uploads/architecture.png"}
  ]
}
```

**附件处理流程**：
1. 上传文件存入 `projects/{id}/uploads/`
2. Pipeline 启动时，附件路径注入 analyst 的输入上下文
3. Analyst 用 `analyze_image` 分析附件，结果写入 PRD.md

### 7. 聊天框支持多模态输入

BOSS 在聊天框可直接发送图片：

```
┌─────────────────────────────────────┐
│ 📎 附件                              │
│ ┌─────┐ ┌─────┐                     │
│ │ 🖼️  │ │ 🎤  │  拖拽/粘贴/点击上传   │
│ │图片 │ │语音 │                     │
│ └─────┘ └─────┘                     │
│                                     │
│ 描述一下这个 UI 有什么问题...          │
└─────────────────────────────────────┘
```

- 图片：支持 PNG/JPG/GIF/WebP，拖拽或粘贴
- PDF：支持 `application/pdf`，拖拽或点击上传
- 音频：支持 WAV/MP3/M4A，点击上传
- 视频：支持 MP4/MOV，点击上传（P2）

当前项目聊天链路中，图片与 PDF 并不是前端直接上传到 LLM，而是先上传到 Catown，再由后端以内联多模态消息的形式转发给 LLM。

**当前已实现的附件上传链路（2026-05-27，2026-06-01 补充 PDF）**：
1. 前端选图或 PDF 后，先调用 `/api/chatrooms/{chatroom_id}/upload`，以 `multipart/form-data` 把附件上传到 Catown 后端。
2. 后端校验 MIME type 和大小后，将文件落盘到当前项目 workspace 的 `uploads/` 目录，并在主业务库 `cached_multimodal_files` 中记录可恢复索引，返回 `file_id`、`file_path`、`file_name`、`file_size`、`mime_type` 等附件元数据。
3. 用户真正发送聊天消息时，前端通过 `/messages` 或 `/messages/stream` 发送的是文本内容加附件元数据，不再重复发送附件二进制。
4. 后端在生成本轮 user message 时，根据 `file_path` 回到 workspace 内重新读取附件文件。图片编码为 `data:image/...;base64,...`；PDF 编码为 `data:application/pdf;base64,...`。
5. `LLMClient` 再将它组装成 OpenAI 兼容的多模态 `content` 数组，例如图片使用 `{type: "image_url", ...}`，PDF 使用 `{type: "file", file: {filename, file_data, mime_type}}`，并通过 `chat.completions.create(messages=...)` 发给 LLM。

这意味着当前实现是“Catown 后端中转并内联图片字节到 JSON 请求”，不是“前端把文件直接上传到 LLM 服务器”。当前原生多模态链路已覆盖项目内单 Agent 的同步与流式聊天入口。

附件大小限制由 `agents.json` 顶层 `multimodal.max_upload_size_bytes` 控制，并在 Settings > Multimodal 中显示为 `Max upload size (MB)`；默认值是 20MB，当前后端允许配置范围为 1MB 到 200MB。聊天上传、发送轮次重新读取附件、`analyze_image` 和 `analyze_document` 都必须读取同一个有效配置。

PDF 分析在运行时应遵循以下优先级：
1. 模型/provider 明确支持 PDF file input 时，优先使用原生多模态 PDF 路径，减少本地抽取损失。
2. provider 不支持 file input、需要精确页码证据、或 PDF 很长时，Agent 使用 `analyze_document` 进行本地文本抽取与分析。
3. 如果 `analyze_document` 没有抽到文本，判定为扫描件或图片型 PDF，后续阶段再进入 OCR/页面渲染为图片的流程。

**审计与日志约束（2026-05-29 修订）**：
- 真实发给 LLM 的请求仍按 provider 协议使用 `data:*;base64,...`，不额外塞入 Catown 私有字段，避免破坏 OpenAI 兼容格式。
- 任何审计、Monitor 网络原文、runtime prompt snapshot 中的内联多模态 bytes 都不得直接持久化。
- 日志副本必须保留原 JSON 结构和大小/类型信息，但将 data URI 替换成 `file_id` 占位，例如 `<cached_file file_id="file_..." mime="application/pdf" bytes="12345">`。
- `file_id` 指向主业务库 `cached_multimodal_files`，该表记录 workspace 相对路径、文件名、MIME、大小、sha256、chatroom/message 关联等信息；需要还原时从该表定位服务器缓存文件。
- 如果出现未登记的内联文件，只能标记为 `file_id="unregistered"`，不能伪造可恢复引用。

### 8. 文件存储

```
projects/{id}/
├── uploads/           # BOSS 上传的附件
│   ├── ui-mockup.png
│   ├── product-spec.pdf
│   └── voice-note.wav
├── media/             # Agent 处理过程中产生的媒体
│   └── video-frames/
│       ├── frame_001.png
│       └── frame_002.png
└── ...
```

### 9. 依赖

| 依赖 | 用途 | 阶段 |
|------|------|------|
| OpenAI API (vision) | 图片理解 | P0 |
| OpenAI API (whisper) | 音频转录 | P1 |
| ffmpeg | 视频抽帧 | P1 |
| python-multipart | 文件上传 | P0（已有） |
| pypdf | PDF 文本层抽取，用于 analyze_document 兜底 | P0 |

### 10. 与竞品的差异

| 能力 | OpenClaw | AutoGen | **Catown** |
|------|----------|---------|------------|
| 图片理解 | ✅ | ✅ | ✅（P0） |
| PDF 文档分析 | ✅ | 部分支持 | ✅（P0） |
| 视频分析 | ❌ | ❌ | ✅（P1） |
| 音频转录 | ✅ | ❌ | ✅（P1） |
| Pipeline 多模态输入 | ❌ | ❌ | ✅ |
| Agent 工具层多模态 | ❌ | 需自己搭 | ✅ 内置 |

## 影响模块

| 模块 | 改动量 | 阶段 |
|------|--------|------|
| `llm/client.py` | 中 — multimodal messages 支持 | P0 |
| `tools/analyze_image.py` | 新增 | P0 |
| `tools/analyze_document.py` | 新增 PDF 文本抽取与分析兜底 | P0 |
| `services/multimodal_config.py` | 统一附件大小有效配置，默认 20MB，可由 Settings 持久化修改 | P0 |
| `tools/transcribe_audio.py` | 新增 | P1 |
| `tools/extract_video_frames.py` | 新增 | P1 |
| `configs/skills.json` | 新增 multimodal-analysis | P0 |
| `configs/agents.json` | model 增加 capabilities 字段 | P0 |
| `routes/api.py` | 文件上传接口 | P0 |
| `frontend/index.html` / `ConfigTab.tsx` | 附件上传 UI 与 Settings > Multimodal 配置项 | P0 |
| `pipeline/engine.py` | 附件注入 Agent 上下文 | P0 |
| `configs/agents.json` / live `agents.json` | 暴露 `analyze_document` 给 valet/analyst/developer/tester/ui-designer | P0 |

## 验收标准

### P0（图片理解）
- [ ] BOSS 可在聊天框上传图片
- [ ] Agent 使用 analyze_image 分析图片并返回结构化结果
- [ ] Pipeline 支持图片附件作为需求输入
- [ ] LLM Client 正确发送 multimodal messages

### P0（PDF 文档分析）
- [x] BOSS 可在聊天框上传 PDF
- [x] Settings > Multimodal 可配置单文件上传大小，默认 20MB
- [x] LLM Client 可把 PDF 作为 OpenAI 兼容 file part 发送给支持该能力的 provider
- [x] Agent 可调用 `analyze_document` 抽取 PDF 文本并返回带页码上下文的结果
- [ ] provider 不支持原生 PDF 输入时，提示或自动转入 `analyze_document` 兜底路径
- [ ] 扫描件 PDF 明确返回“无可抽取文本”，后续进入 OCR/图片分析计划

### P1（音频 + 视频）
- [ ] BOSS 可上传音频，Agent 调用 transcribe_audio 转录
- [ ] BOSS 可上传视频，Agent 自动抽帧 + 逐帧分析
- [ ] Whisper API 集成正常
- [ ] ffmpeg 抽帧在 Docker 环境正常工作

## PDF 文档分析开发计划（启动）

### 阶段 0：文档与范围确认
- [x] 将 PDF 纳入多模态能力矩阵、附件链路、审计约束和验收标准。
- [x] 明确双路径策略：原生 PDF file input 优先，本地 `analyze_document` 作为兜底。
- [x] 将原 20MB 单文件限制改为 Settings > Multimodal 可配置项，后端统一从 `multimodal.max_upload_size_bytes` 读取。

### 阶段 1：文本型 PDF 兜底工具
- [x] 新增 `tools/analyze_document.py`，支持 workspace 内 PDF 路径校验、大小限制、页码范围、字符预算和 `pypdf` 文本层抽取。
- [x] 将 `analyze_document` 注册到 tool registry、默认 agent config、live config 迁移补齐逻辑和 multimodal-analysis skill。
- [x] 增加单元测试覆盖：文本抽取、LLM 分析调用、路径逃逸拦截。

### 阶段 2：运行时自动兜底
- [ ] 在 PDF 附件进入聊天轮次时记录 provider/model 是否支持原生 PDF file input。
- [ ] 当 provider 拒绝 file part 或模型能力未知时，向 agent prompt 注入明确建议：调用 `analyze_document`。
- [ ] 在 Monitor/runtime card 中区分“原生 PDF 多模态请求”和“本地 PDF 文本抽取工具调用”。

### 阶段 3：扫描件 PDF 与 OCR
- [ ] 将扫描件 PDF 页面渲染为图片帧，复用 `analyze_image` 或 OCR 工具抽取文本。
- [ ] 为 OCR 结果保留页码与块级来源，避免把扫描件结果混成无来源纯文本。
- [ ] 对大 PDF 增加分页/分块摘要策略，避免一次性塞满上下文。
