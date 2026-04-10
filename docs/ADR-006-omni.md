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

让 Catown 支持三种多模态输入：**图片**、**视频**、**音频**，贯穿 BOSS 输入 → Agent 处理 → 产出物全链路。

## 方案

### 1. 多模态能力分级

| 能力 | 场景 | 优先级 | 实现阶段 |
|------|------|--------|---------|
| **图片理解** | UI 截图需求、架构图分析、错误截图排查 | P0 | V1 |
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

### 5. Skill 层新增

```json
{
  "multimodal-analysis": {
    "name": "多模态分析",
    "description": "分析图片、视频、音频内容",
    "required_tools": ["analyze_image", "transcribe_audio", "extract_video_frames", "read_file", "write_file"],
    "prompt_fragment": "## 多模态分析规范\n- 收到图片/视频/音频附件时，优先使用 analyze_image / transcribe_audio 工具\n- 视频先用 extract_video_frames 抽帧，再逐帧分析\n- 分析结果结构化输出，包含关键发现和建议",
    "category": "analysis"
  }
}
```

适用 Agent：
- **analyst** — 分析需求截图/原型图
- **developer** — 分析错误截图/UI 设计稿
- **tester** — 分析 Bug 截图/复现录屏

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
- 音频：支持 WAV/MP3/M4A，点击上传
- 视频：支持 MP4/MOV，点击上传（P2）

附件通过 WebSocket 或 multipart 上传到后端，路径注入 Agent 上下文。

### 8. 文件存储

```
projects/{id}/
├── uploads/           # BOSS 上传的附件
│   ├── ui-mockup.png
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

### 10. 与竞品的差异

| 能力 | OpenClaw | AutoGen | **Catown** |
|------|----------|---------|------------|
| 图片理解 | ✅ | ✅ | ✅（P0） |
| 视频分析 | ❌ | ❌ | ✅（P1） |
| 音频转录 | ✅ | ❌ | ✅（P1） |
| Pipeline 多模态输入 | ❌ | ❌ | ✅ |
| Agent 工具层多模态 | ❌ | 需自己搭 | ✅ 内置 |

## 影响模块

| 模块 | 改动量 | 阶段 |
|------|--------|------|
| `llm/client.py` | 中 — multimodal messages 支持 | P0 |
| `tools/analyze_image.py` | 新增 | P0 |
| `tools/transcribe_audio.py` | 新增 | P1 |
| `tools/extract_video_frames.py` | 新增 | P1 |
| `configs/skills.json` | 新增 multimodal-analysis | P0 |
| `configs/agents.json` | model 增加 capabilities 字段 | P0 |
| `routes/api.py` | 文件上传接口 | P0 |
| `frontend/index.html` | 附件上传 UI | P0 |
| `pipeline/engine.py` | 附件注入 Agent 上下文 | P0 |

## 验收标准

### P0（图片理解）
- [ ] BOSS 可在聊天框上传图片
- [ ] Agent 使用 analyze_image 分析图片并返回结构化结果
- [ ] Pipeline 支持图片附件作为需求输入
- [ ] LLM Client 正确发送 multimodal messages

### P1（音频 + 视频）
- [ ] BOSS 可上传音频，Agent 调用 transcribe_audio 转录
- [ ] BOSS 可上传视频，Agent 自动抽帧 + 逐帧分析
- [ ] Whisper API 集成正常
- [ ] ffmpeg 抽帧在 Docker 环境正常工作
