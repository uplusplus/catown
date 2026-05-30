# ADR-009: Chat Runtime State 分层

**日期**: 2026-04-23
**状态**: 已确认
**决策者**: BOSS + Catown UI Runtime

---

## 背景

Chat 页当前同时存在三类消息状态：

1. `messages`：后端已经落库的正式消息
2. `optimisticMessages`：前端在一次发送过程中维护的运行态消息
3. `chat-local-overlay`：ChatTab 自己的本地兜底层

过去的问题在于 `chat-local-overlay` 实际镜像了 `optimisticMessages` 的大量流式细节，导致：

- `localStorage` 体积暴涨
- prompt / tool result / response draft 被重复持久化
- 页面刷新时偶发 `QuotaExceededError`

## 决策

采用三层分工，但明确职责边界：

### 1. `messages` = 唯一正式事实源

- 来源：后端 `/api/chatrooms/:id/messages`
- 作用：最终聊天记录、刷新后的稳定结果
- 要求：UI 最终总是以它为准

### 2. `optimisticMessages` = 前端运行态

- 来源：发送消息后由 `App.tsx` 立即创建，并持续接收 SSE / WS 更新
- 作用：承载流式步骤、tool call 进度、增量回复内容
- 持久化策略：允许持久化，但必须做体积裁剪和写入失败兜底

### 3. `chat-local-overlay` = 极小的 pending-turn 兜底层

- 来源：ChatTab 在用户点击发送时立即写入
- 作用：
  - 页面首帧立即回显用户消息
  - 刷新时恢复“刚发出去但未正式落库”的 turn
  - 支持 `pending -> chatId` 的迁移
- 持久化策略：
  - 只保留极小字段
  - 只保留未完成的本地消息
  - 不再持久化大块 `detailContent`

## 数据流

```text
用户发送消息
  -> ChatTab 立即写入 local overlay
  -> App 创建 optimisticMessages
  -> 后端开始流式返回
  -> optimisticMessages 持续更新步骤/内容
  -> ChatTab 将 optimistic 结果映射到当前可见线程
  -> 后端正式消息落库
  -> messages 覆盖同 turn 的 optimistic / overlay
  -> overlay 清理
```

## 持久化原则

### 应该持久化

- `client_turn_id`
- 用户消息正文
- assistant 是否仍在处理中
- 最后一条简短状态摘要

### 不应该持久化

- 完整 system prompt
- 完整 prompt payload
- 完整 tool 参数
- 完整 tool 输出
- 长篇 response draft 细节

这些大对象只应该存在于：

- 内存态 `optimisticMessages`
- 或后端 runtime cards / message records

## 落地结果

本 ADR 生效后：

- `chat-local-overlay` 被收缩为最小 pending-turn 层
- `optimisticMessages` 写入 `localStorage` 时增加裁剪和失败兜底
- Chat 页仍保留“立即回显 + 刷新后不中断”的体验
- 同时避免本地存储再次因重复 trace 数据而爆掉
