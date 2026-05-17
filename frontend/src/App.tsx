import { useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

import { api } from "./api/client";
import { AppSidebar } from "./components/AppSidebar";
import { ChatTab } from "./components/ChatTab";
import { ConfigTab } from "./components/ConfigTab";
import { ProjectsTab } from "./components/ProjectsTab";
import { UI_VERSION } from "./uiVersion";
import { DEFAULT_AGENT_TYPE, defaultAgentName, findAgentByType, getAgentDisplayName } from "./utils/agents";
import { buildLlmTimingsMarkdown, formatTimingDuration } from "./utils/llmTimings";
import type {
  AppTab,
  AgentInfo,
  AgentConfigPayload,
  ChatCardItem,
  ChatCardLlmTimings,
  ChatEventItem,
  ChatEventTone,
  ChatProcessEntry,
  ChatSummary,
  ChatTimelineProjection,
  ConfigSection,
  ConfigResponse,
  ContextConfigPayload,
  GlobalConfigPayload,
  MessageItem,
  MessageStreamStep,
  PermissionsConfigPayload,
  ProjectBrowserIndex,
  ProjectBrowserStreamBatch,
  ProjectSummary,
  TaskActivityProjection,
  TaskRunDetail,
  TaskRunSummary,
  ToolAuthorizationRule,
} from "./types";

const LAST_CHAT_STORAGE_KEY = "catown:last-chat-id";
const OPTIMISTIC_MESSAGES_STORAGE_KEY = "catown:optimistic-messages";
const LOCAL_OVERLAY_STORAGE_KEY = "catown:chat-local-overlay";
const STREAM_STEP_LIMIT = 8;
const OPTIMISTIC_MAX_CHATS = 6;
const OPTIMISTIC_MAX_MESSAGES_PER_CHAT = 4;
const OPTIMISTIC_MAX_CONTENT_CHARS = 6000;
const OPTIMISTIC_MAX_STEP_COUNT = 8;
const OPTIMISTIC_MAX_STEP_LABEL_CHARS = 160;
const OPTIMISTIC_MAX_STEP_DETAIL_CHARS = 800;
const OPTIMISTIC_MAX_STEP_DETAIL_CONTENT_CHARS = 2400;
const ERROR_AUTO_DISMISS_MS = 8000;
const TASK_ACTIVITY_POLL_MS = 2500;
const APP_SIDEBAR_DEFAULT_WIDTH = 258;
const APP_SIDEBAR_MIN_WIDTH = 220;
const APP_SIDEBAR_MAX_WIDTH = 420;

const CONFIG_SECTION_META: Record<
  ConfigSection,
  {
    sidebarLabel: string;
    sidebarDescription: string;
    title: string;
    subtitle: string;
  }
> = {
  agents: {
    sidebarLabel: "Agents",
    sidebarDescription: "Models, roles, tools, skills, and defaults",
    title: "Agent management",
    subtitle: "Manage provider defaults, roles, souls, tools, and per-agent runtime behavior.",
  },
  skills: {
    sidebarLabel: "Skills",
    sidebarDescription: "Skill coverage, bindings, and usage scope",
    title: "Skill management",
    subtitle: "Review discovered skills, which agents use them, and how they are distributed today.",
  },
  tools: {
    sidebarLabel: "Tools",
    sidebarDescription: "Tool coverage, bindings, and approval surface",
    title: "Tool management",
    subtitle: "Inspect which tools exist, which agents can use them, and how approval policy applies today.",
  },
  memory: {
    sidebarLabel: "Memory",
    sidebarDescription: "Long-term memory, retained context, and summaries",
    title: "Memory management",
    subtitle: "Inspect retained memory footprint by agent and understand what long-term context already exists.",
  },
  permissions: {
    sidebarLabel: "Permissions",
    sidebarDescription: "Approval defaults for low-risk read tools",
    title: "Permissions",
    subtitle: "Control whether low-risk read-only tool requests are allowed by default.",
  },
  context: {
    sidebarLabel: "Context",
    sidebarDescription: "Prompt budgets, selector caps, and compaction thresholds",
    title: "Context budgets",
    subtitle: "Tune how much runtime context Catown can inject before fragment dropping or truncation is applied.",
  },
};

function debugConsole(level: "info" | "warn", event: string, details: Record<string, unknown>) {
  if (typeof window === "undefined") return;
  const payload = {
    uiVersion: UI_VERSION,
    origin: window.location.origin,
    path: window.location.pathname,
    ...details,
  };
  console[level](`[CatownDebug] ${event}`, payload);
  (window as Window & { __CATOWN_DEBUG__?: Record<string, unknown> }).__CATOWN_DEBUG__ = {
    event,
    ...payload,
  };
}

function readLastChatId(): number | null {
  if (typeof window === "undefined") return null;
  const rawValue = window.localStorage.getItem(LAST_CHAT_STORAGE_KEY);
  if (!rawValue) return null;

  const parsed = Number.parseInt(rawValue, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function writeLastChatId(chatId: number | null) {
  if (typeof window === "undefined") return;

  if (chatId === null) {
    window.localStorage.removeItem(LAST_CHAT_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(LAST_CHAT_STORAGE_KEY, String(chatId));
}

function optimisticScopeKey(chatId: number | null) {
  return chatId === null ? "pending" : `chat:${chatId}`;
}

function trimPersistedText(value: string | undefined, limit: number) {
  if (!value) return undefined;
  const normalized = value.trim();
  if (!normalized) return undefined;
  return normalized.length > limit ? `${normalized.slice(0, Math.max(limit - 3, 0))}...` : normalized;
}

function sanitizePersistedStep(step: MessageStreamStep): MessageStreamStep {
  return {
    id: step.id,
    label: trimPersistedText(step.label, OPTIMISTIC_MAX_STEP_LABEL_CHARS) || "Step",
    detail: trimPersistedText(step.detail, OPTIMISTIC_MAX_STEP_DETAIL_CHARS),
    detailContent: trimPersistedText(step.detailContent, OPTIMISTIC_MAX_STEP_DETAIL_CONTENT_CHARS),
    state: step.state,
    kind: step.kind,
    agent: step.agent,
    tool: step.tool,
    toolCallIndex: step.toolCallIndex,
    toolCallId: step.toolCallId,
    runId: step.runId,
  };
}

function streamStepDedupeKey(step: MessageStreamStep) {
  return [
    step.kind || "",
    step.agent || "",
    step.tool || "",
    step.toolCallIndex ?? "",
    step.toolCallId ?? "",
    step.runId ?? "",
    step.label || "",
    step.detail || "",
  ].join("\u0001");
}

function dedupeStreamSteps(steps: MessageStreamStep[]) {
  const byKey = new Map<string, MessageStreamStep>();
  const order: string[] = [];

  for (const step of steps) {
    const key = streamStepDedupeKey(step);
    if (!byKey.has(key)) {
      order.push(key);
    }
    byKey.set(key, step);
  }

  return order.map((key) => byKey.get(key)).filter((step): step is MessageStreamStep => Boolean(step));
}

function dedupeMessageStreamSteps(message: MessageItem) {
  const steps = message.streamSteps ?? [];
  if (steps.length <= 1) return message;

  const dedupedSteps = dedupeStreamSteps(steps);
  return dedupedSteps.length === steps.length ? message : { ...message, streamSteps: dedupedSteps };
}

function sanitizePersistedMessage(message: MessageItem): MessageItem {
  return {
    id: message.id,
    agent_id: message.agent_id,
    content: trimPersistedText(message.content, OPTIMISTIC_MAX_CONTENT_CHARS) || "",
    message_type: message.message_type,
    created_at: message.created_at,
    agent_name: message.agent_name,
    client_turn_id: message.client_turn_id,
    isStreaming: message.isStreaming,
    statusDetail: trimPersistedText(message.statusDetail, OPTIMISTIC_MAX_STEP_DETAIL_CHARS),
    optimisticKind: message.optimisticKind,
    localOnly: message.localOnly,
    streamSteps: dedupeStreamSteps((message.streamSteps || []).map(sanitizePersistedStep)).slice(-OPTIMISTIC_MAX_STEP_COUNT),
  };
}

function compactOptimisticMessageStore(
  store: Record<string, MessageItem[]>,
  preferredKeys: string[] = [],
) {
  const normalizedEntries = Object.entries(store)
    .map(([key, value]) => {
      const sanitizedMessages = (Array.isArray(value) ? value : [])
        .slice(-OPTIMISTIC_MAX_MESSAGES_PER_CHAT)
        .map(sanitizePersistedMessage)
        .sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
      return [key, sanitizedMessages] as const;
    })
    .filter(([, value]) => value.length > 0);

  const preferredSet = new Set(preferredKeys.filter(Boolean));
  const preferredEntries = normalizedEntries.filter(([key]) => preferredSet.has(key));
  const remainingEntries = normalizedEntries
    .filter(([key]) => !preferredSet.has(key))
    .sort((left, right) => {
      const leftLast = left[1][left[1].length - 1];
      const rightLast = right[1][right[1].length - 1];
      return new Date(rightLast?.created_at || 0).getTime() - new Date(leftLast?.created_at || 0).getTime();
    });

  return Object.fromEntries([...preferredEntries, ...remainingEntries].slice(0, OPTIMISTIC_MAX_CHATS));
}

function readOptimisticMessageStore() {
  if (typeof window === "undefined") return {} as Record<string, MessageItem[]>;
  try {
    const rawValue = window.localStorage.getItem(OPTIMISTIC_MESSAGES_STORAGE_KEY);
    if (!rawValue) return {};
    const parsed = JSON.parse(rawValue) as Record<string, MessageItem[]>;
    return parsed && typeof parsed === "object" ? compactOptimisticMessageStore(parsed) : {};
  } catch {
    return {};
  }
}

function writeOptimisticMessageStore(store: Record<string, MessageItem[]>, preferredKeys: string[] = []) {
  if (typeof window === "undefined") return;
  let nextStore = compactOptimisticMessageStore(store, preferredKeys);
  const nextEntries = Object.entries(nextStore).filter(([, value]) => Array.isArray(value) && value.length > 0);
  if (nextEntries.length === 0) {
    window.localStorage.removeItem(OPTIMISTIC_MESSAGES_STORAGE_KEY);
    return;
  }

  const persist = (value: Record<string, MessageItem[]>) =>
    window.localStorage.setItem(OPTIMISTIC_MESSAGES_STORAGE_KEY, JSON.stringify(value));

  try {
    persist(Object.fromEntries(nextEntries));
  } catch {
    const orderedKeys = Object.keys(nextStore).sort((left, right) => {
      const leftMessages = nextStore[left] ?? [];
      const rightMessages = nextStore[right] ?? [];
      const leftLast = leftMessages[leftMessages.length - 1];
      const rightLast = rightMessages[rightMessages.length - 1];
      return new Date((rightLast?.created_at || 0) as string | number).getTime()
        - new Date((leftLast?.created_at || 0) as string | number).getTime();
    });
    const protectedKeys = new Set(preferredKeys.filter(Boolean));
    const removableKeys = orderedKeys.filter((key) => !protectedKeys.has(key));

    while (removableKeys.length > 0) {
      const nextKey = removableKeys.pop();
      if (!nextKey) continue;
      delete nextStore[nextKey];
      try {
        persist(nextStore);
        return;
      } catch {
        // Keep trimming until the payload fits or nothing remains.
      }
    }

    if (preferredKeys.length > 0) {
      const fallbackStore = compactOptimisticMessageStore(
        Object.fromEntries(
          preferredKeys
            .filter((key) => nextStore[key]?.length)
            .map((key) => [key, (nextStore[key] ?? []).slice(-2).map((message) => ({
              ...sanitizePersistedMessage(message),
              content: trimPersistedText(message.content, 1200) || "",
              statusDetail: trimPersistedText(message.statusDetail, 360),
              streamSteps: (message.streamSteps || []).slice(-2).map((step) => ({
                ...sanitizePersistedStep(step),
                detail: trimPersistedText(step.detail, 240),
                detailContent: trimPersistedText(step.detailContent, 640),
              })),
            }))]),
        ),
        preferredKeys,
      );
      try {
        persist(fallbackStore);
        return;
      } catch {
        // Fall through to hard reset below.
      }
    }

    window.localStorage.removeItem(OPTIMISTIC_MESSAGES_STORAGE_KEY);
  }
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function readOptimisticMessages(chatId: number | null) {
  const store = readOptimisticMessageStore();
  return store[optimisticScopeKey(chatId)] ?? [];
}

function writeOptimisticMessages(chatId: number | null, messages: MessageItem[]) {
  const store = readOptimisticMessageStore();
  const key = optimisticScopeKey(chatId);
  if (messages.length === 0) {
    delete store[key];
  } else {
    store[key] = messages;
  }
  writeOptimisticMessageStore(store, [key]);
}

function clearLocalMessageStoreScope(storageKey: string, chatId: number | null) {
  if (typeof window === "undefined") return;

  try {
    const rawValue = window.localStorage.getItem(storageKey);
    if (!rawValue) return;

    const parsed = JSON.parse(rawValue) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return;

    delete parsed[optimisticScopeKey(chatId)];

    if (Object.keys(parsed).length === 0) {
      window.localStorage.removeItem(storageKey);
      return;
    }

    window.localStorage.setItem(storageKey, JSON.stringify(parsed));
  } catch {
    // Corrupt local cache should not block chat/project deletion flows.
  }
}

function clearChatLocalCaches(chatId: number | null | undefined) {
  if (!chatId) return;

  writeOptimisticMessages(chatId, []);
  clearLocalMessageStoreScope(LOCAL_OVERLAY_STORAGE_KEY, chatId);

  if (readLastChatId() === chatId) {
    writeLastChatId(null);
  }
}

function clearManyChatLocalCaches(chatIds: Array<number | null | undefined>) {
  const uniqueChatIds = Array.from(
    new Set(chatIds.filter((chatId): chatId is number => Number.isFinite(chatId) && chatId > 0)),
  );
  uniqueChatIds.forEach(clearChatLocalCaches);
}

function migrateOptimisticMessages(fromChatId: number | null, toChatId: number | null) {
  const fromKey = optimisticScopeKey(fromChatId);
  const toKey = optimisticScopeKey(toChatId);
  if (fromKey === toKey) return;

  const store = readOptimisticMessageStore();
  const fromMessages = store[fromKey] ?? [];
  if (fromMessages.length === 0) return;

  const targetMessages = store[toKey] ?? [];
  store[toKey] = mergeMessages(targetMessages, fromMessages);
  delete store[fromKey];
  writeOptimisticMessageStore(store, [toKey]);
}

function parseOptimisticScopeChatId(scope: string) {
  if (scope === "pending") return null;
  const parsed = Number.parseInt(scope.replace("chat:", ""), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function buildInitialChatTitle(content: string) {
  const normalized = content.trim().replace(/\s+/g, " ");
  if (!normalized) return "New Chat";
  return normalized.length > 32 ? `${normalized.slice(0, 32)}...` : normalized;
}

function resolveRestoredSelection(
  projects: ProjectSummary[],
  chats: ChatSummary[],
  preferredChatId: number | null,
) {
  if (preferredChatId !== null) {
    const matchedProject = projects.find((project) => project.default_chatroom_id === preferredChatId) ?? null;
    if (matchedProject) {
      return {
        projectId: matchedProject.id,
        chatId: matchedProject.default_chatroom_id,
      };
    }

    const matchedChat = chats.find((chat) => chat.id === preferredChatId) ?? null;
    if (matchedChat) {
      return {
        projectId: matchedChat.project_id ?? null,
        chatId: matchedChat.id,
      };
    }
  }

  const fallbackProject = projects[0] ?? null;
  if (fallbackProject) {
    return {
      projectId: fallbackProject.id,
      chatId: fallbackProject.default_chatroom_id,
    };
  }

  const fallbackChat = chats[0] ?? null;
  return {
    projectId: fallbackChat?.project_id ?? null,
    chatId: fallbackChat?.id ?? null,
  };
}

function isKnownChatId(
  chatId: number | null | undefined,
  projects: ProjectSummary[],
  chats: ChatSummary[],
) {
  if (!chatId) return false;
  return (
    projects.some((project) => project.default_chatroom_id === chatId) ||
    chats.some((chat) => chat.id === chatId)
  );
}

function buildEvent(message: string, tone: ChatEventTone = "info"): ChatEventItem {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    message,
    tone,
    created_at: new Date().toISOString(),
  };
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function readSkillDetails(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      return {
        name: typeof record.name === "string" ? record.name : undefined,
        hint: typeof record.hint === "string" ? record.hint : undefined,
        guide: typeof record.guide === "string" ? record.guide : undefined,
        guide_tokens: typeof record.guide_tokens === "number" ? record.guide_tokens : undefined,
      };
    })
    .filter(
      (item): item is { name?: string; hint?: string; guide?: string; guide_tokens?: number } =>
        item !== null,
    );
}

function readLlmTimings(value: unknown): ChatCardLlmTimings | undefined {
  if (!value || typeof value !== "object") return undefined;
  const record = value as Record<string, unknown>;
  const timings: ChatCardLlmTimings = {};
  for (const key of [
    "request_sent_ms",
    "first_chunk_ms",
    "first_content_ms",
    "first_tool_call_ms",
    "tool_call_ready_ms",
    "completed_ms",
  ] as const) {
    if (typeof record[key] === "number") {
      timings[key] = record[key];
    }
  }
  return Object.keys(timings).length > 0 ? timings : undefined;
}

function readLlmFinishReason(payload: Record<string, unknown>) {
  if (typeof payload.finish_reason === "string" && payload.finish_reason) {
    return payload.finish_reason;
  }

  const rawResponse = typeof payload.raw_response === "string" ? payload.raw_response.trim() : "";
  if (!rawResponse) return undefined;

  try {
    const parsed = JSON.parse(rawResponse) as Record<string, unknown>;
    return typeof parsed.finish_reason === "string" && parsed.finish_reason ? parsed.finish_reason : undefined;
  } catch {
    return undefined;
  }
}

function buildCard(payload: Record<string, unknown>): ChatCardItem | null {
  const type = typeof payload.type === "string" ? payload.type : "";
  if (!type) return null;

  const createdAt =
    typeof payload.created_at === "string" && payload.created_at
      ? payload.created_at
      : new Date().toISOString();
  const runtimeMessageId =
    typeof payload.runtime_message_id === "number" ? payload.runtime_message_id : undefined;
  const clientTurnId =
    typeof payload.client_turn_id === "string" && payload.client_turn_id
      ? payload.client_turn_id
      : undefined;

  const baseCard = {
    id:
      runtimeMessageId !== undefined
        ? `${type}-${runtimeMessageId}`
        : `${type}-${createdAt}-${Math.random().toString(16).slice(2)}`,
    kind: type as ChatCardItem["kind"],
    created_at: createdAt,
    source: typeof payload.source === "string" ? payload.source : "chatroom",
    client_turn_id: clientTurnId,
    pipeline_id: typeof payload.pipeline_id === "number" ? payload.pipeline_id : undefined,
    run_id: typeof payload.run_id === "number" ? payload.run_id : undefined,
  };

  switch (type) {
    case "llm_call":
      return {
        ...baseCard,
        kind: "llm_call",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        model: typeof payload.model === "string" ? payload.model : undefined,
        turn: typeof payload.turn === "number" ? payload.turn : undefined,
        tokens_in: typeof payload.tokens_in === "number" ? payload.tokens_in : undefined,
        tokens_out: typeof payload.tokens_out === "number" ? payload.tokens_out : undefined,
        tokens_total: typeof payload.tokens_total === "number" ? payload.tokens_total : undefined,
        context_window: typeof payload.context_window === "number" ? payload.context_window : undefined,
        context_usage_ratio:
          typeof payload.context_usage_ratio === "number" ? payload.context_usage_ratio : undefined,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        system_prompt: typeof payload.system_prompt === "string" ? payload.system_prompt : undefined,
        prompt_messages: typeof payload.prompt_messages === "string" ? payload.prompt_messages : undefined,
        response: typeof payload.response === "string" ? payload.response : undefined,
        raw_response: typeof payload.raw_response === "string" ? payload.raw_response : undefined,
        finish_reason: readLlmFinishReason(payload),
        tool_calls: Array.isArray(payload.tool_calls)
          ? payload.tool_calls
              .map((item) => {
                if (!item || typeof item !== "object") return null;
                const record = item as Record<string, unknown>;
                return {
                  name: typeof record.name === "string" ? record.name : undefined,
                  args_preview: typeof record.args_preview === "string" ? record.args_preview : undefined,
                  index: typeof record.index === "number" ? record.index : undefined,
                  id: typeof record.id === "string" ? record.id : null,
                };
              })
              .filter(
                (item): item is { name?: string; args_preview?: string; index?: number; id?: string | null } => item !== null,
              )
          : [],
        timings: readLlmTimings(payload.timings),
      };
    case "tool_call":
      return {
        ...baseCard,
        kind: "tool_call",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        tool: typeof payload.tool === "string" ? payload.tool : undefined,
        arguments: typeof payload.arguments === "string" ? payload.arguments : undefined,
        success: typeof payload.success === "boolean" ? payload.success : undefined,
        status: typeof payload.status === "string" ? payload.status : undefined,
        blocked: typeof payload.blocked === "boolean" ? payload.blocked : undefined,
        blocked_kind: typeof payload.blocked_kind === "string" ? payload.blocked_kind : null,
        blocked_reason: typeof payload.blocked_reason === "string" ? payload.blocked_reason : null,
        result: typeof payload.result === "string" ? payload.result : undefined,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        pid: typeof payload.pid === "number" ? payload.pid : undefined,
        tool_call_index: typeof payload.tool_call_index === "number" ? payload.tool_call_index : undefined,
        tool_call_id: typeof payload.tool_call_id === "string" ? payload.tool_call_id : null,
      };
    case "consult_call":
      return {
        ...baseCard,
        kind: "consult_call",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        target_agent: typeof payload.target_agent === "string" ? payload.target_agent : undefined,
        question_preview: typeof payload.question_preview === "string" ? payload.question_preview : undefined,
        response_preview: typeof payload.response_preview === "string" ? payload.response_preview : undefined,
        consult_step_id: typeof payload.consult_step_id === "string" ? payload.consult_step_id : undefined,
        status: typeof payload.status === "string" ? payload.status : undefined,
        available_actions: Array.isArray(payload.available_actions)
          ? payload.available_actions.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
          : [],
        error: typeof payload.error === "string" ? payload.error : undefined,
      };
    case "agent_error":
      return {
        ...baseCard,
        kind: "agent_error",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
        content: typeof payload.content === "string" ? payload.content : undefined,
        error: typeof payload.error === "string" ? payload.error : undefined,
      };
    case "stage_started":
      return {
        ...baseCard,
        kind: "stage_start",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        display_name: typeof payload.display_name === "string" ? payload.display_name : undefined,
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
        active_skills: readStringArray(payload.active_skills),
        expected_artifacts: readStringArray(payload.expected_artifacts),
        content: typeof payload.content === "string" ? payload.content : undefined,
      };
    case "stage_completed":
      return {
        ...baseCard,
        kind: "stage_end",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
      };
    case "gate_blocked":
      return {
        ...baseCard,
        kind: "gate_blocked",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        display_name: typeof payload.display_name === "string" ? payload.display_name : undefined,
      };
    case "gate_approved":
      return {
        ...baseCard,
        kind: "gate_approved",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
      };
    case "gate_rejected":
      return {
        ...baseCard,
        kind: "gate_rejected",
        from_stage: typeof payload.from_stage === "string" ? payload.from_stage : undefined,
        to_stage: typeof payload.to_stage === "string" ? payload.to_stage : undefined,
      };
    case "skill_inject":
      return {
        ...baseCard,
        kind: "skill_inject",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        skills: readSkillDetails(payload.skills),
        agent_all_skills: readStringArray(payload.agent_all_skills),
      };
    case "agent_message":
      return {
        ...baseCard,
        kind: "agent_message",
        from_agent: typeof payload.from_agent === "string" ? payload.from_agent : undefined,
        to_agent: typeof payload.to_agent === "string" ? payload.to_agent : undefined,
        content: typeof payload.content === "string" ? payload.content : undefined,
      };
    case "boss_instruction":
      return {
        ...baseCard,
        kind: "boss_instruction",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        content_preview: typeof payload.content_preview === "string" ? payload.content_preview : undefined,
      };
    default:
      return null;
  }
}

function updateMessage(
  current: MessageItem[],
  messageId: number,
  updater: (message: MessageItem) => MessageItem,
) {
  let didUpdate = false;
  const next = current.map((message) => {
    if (message.id !== messageId) return message;
    didUpdate = true;
    return updater(message);
  });
  return didUpdate ? next : current;
}

function replaceMessageId(
  current: MessageItem[],
  previousId: number,
  nextId: number,
  patch: Partial<MessageItem> = {},
) {
  return updateMessage(current, previousId, (message) => ({
    ...message,
    ...patch,
    id: nextId,
  }));
}

function mergeMessages(current: MessageItem[], incoming: MessageItem[]) {
  const merged = new Map<number, MessageItem>();
  for (const item of current) {
    merged.set(item.id, item);
  }
  for (const item of incoming) {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  }
  return Array.from(merged.values()).sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
}

function mergeCards(current: ChatCardItem[], incoming: ChatCardItem[]) {
  const merged = new Map<string, ChatCardItem>();
  for (const item of current) {
    merged.set(item.id, item);
  }
  for (const item of incoming) {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  }
  return Array.from(merged.values()).sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
}

function mergeTaskRuns(current: TaskRunSummary[], incoming: TaskRunSummary[]) {
  const merged = new Map<number, TaskRunSummary>();
  for (const item of current) {
    merged.set(item.id, item);
  }
  for (const item of incoming) {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  }
  return Array.from(merged.values()).sort((left, right) => {
    const leftTime = new Date(left.updated_at || left.created_at || 0).getTime();
    const rightTime = new Date(right.updated_at || right.created_at || 0).getTime();
    return rightTime - leftTime;
  });
}

function buildStreamStep(
  label: string,
  detail?: string,
  state: MessageStreamStep["state"] = "live",
  detailContent?: string,
  meta?: Partial<Pick<MessageStreamStep, "kind" | "agent" | "tool" | "toolCallIndex" | "toolCallId" | "runId">>,
): MessageStreamStep {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    label,
    detail,
    detailContent,
    state,
    ...meta,
  };
}

function trimStreamSteps(steps: MessageStreamStep[]) {
  return steps.slice(-STREAM_STEP_LIMIT);
}

function prettyJson(value: string | undefined) {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function isJsonContent(value: string | undefined) {
  if (!value) return false;
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
}

function markdownCodeFence(content: string, language = "") {
  const normalized = content.replace(/\n+$/g, "");
  return `\`\`\`${language}\n${normalized}\n\`\`\``;
}

function markdownSection(title: string, content: string, options?: { language?: string; asMarkdown?: boolean }) {
  const normalized = content.trim();
  if (!normalized) return "";
  if (options?.asMarkdown) {
    return `### ${title}\n\n${normalized}`;
  }
  return `### ${title}\n\n${markdownCodeFence(normalized, options?.language ?? "")}`;
}

function toolResultMarkdownSection(title: string, content: string) {
  return isJsonContent(content)
    ? markdownSection(title, prettyJson(content), { language: "json" })
    : markdownSection(title, content, { language: "text" });
}

function settleLiveStreamSteps(
  steps: MessageStreamStep[] | undefined,
  state: Extract<MessageStreamStep["state"], "done" | "error"> = "done",
) {
  return (steps ?? []).map((step) => (step.state === "live" ? { ...step, state } : step));
}

function pushStreamingStep(
  message: MessageItem,
  label: string,
  detail?: string,
  state: MessageStreamStep["state"] = "live",
  detailContent?: string,
  meta?: Partial<Pick<MessageStreamStep, "kind" | "agent" | "tool" | "toolCallIndex" | "toolCallId" | "runId">>,
) {
  const nextStep = buildStreamStep(label, detail, state, detailContent, meta);
  const settledSteps = settleLiveStreamSteps(message.streamSteps);
  const nextKey = streamStepDedupeKey(nextStep);
  const existingIndex = settledSteps.findIndex((step) => streamStepDedupeKey(step) === nextKey);
  const nextSteps =
    existingIndex === -1
      ? [...settledSteps, nextStep]
      : settledSteps.map((step, index) => (index === existingIndex ? { ...step, ...nextStep, id: step.id } : step));
  return {
    ...message,
    streamSteps: trimStreamSteps(nextSteps),
  };
}

function patchLatestStreamingStep(
  message: MessageItem,
  patch: Partial<Omit<MessageStreamStep, "id">>,
  fallbackLabel?: string,
) {
  const nextSteps = [...(message.streamSteps ?? [])];
  for (let index = nextSteps.length - 1; index >= 0; index -= 1) {
    if (nextSteps[index].state === "live") {
      nextSteps[index] = { ...nextSteps[index], ...patch };
      return {
        ...message,
        streamSteps: trimStreamSteps(nextSteps),
      };
    }
  }

  if (!fallbackLabel) return message;
  return {
    ...message,
    streamSteps: trimStreamSteps([
      ...nextSteps,
      buildStreamStep(fallbackLabel, patch.detail, patch.state ?? "done", patch.detailContent, {
        kind: patch.kind,
        agent: patch.agent,
        tool: patch.tool,
        toolCallIndex: patch.toolCallIndex,
        toolCallId: patch.toolCallId,
        runId: patch.runId,
      }),
    ]),
  };
}

function patchMatchingStreamingStep(
  message: MessageItem,
  matcher: (step: MessageStreamStep) => boolean,
  patch: Partial<Omit<MessageStreamStep, "id">>,
  fallbackLabel?: string,
) {
  const nextSteps = [...(message.streamSteps ?? [])];
  for (let index = nextSteps.length - 1; index >= 0; index -= 1) {
    if (!matcher(nextSteps[index])) continue;
    nextSteps[index] = { ...nextSteps[index], ...patch };
    return {
      ...message,
      streamSteps: trimStreamSteps(nextSteps),
    };
  }

  if (!fallbackLabel) return message;
  return {
    ...message,
    streamSteps: trimStreamSteps([
      ...nextSteps,
      buildStreamStep(fallbackLabel, patch.detail, patch.state ?? "done", patch.detailContent, {
        kind: patch.kind,
        agent: patch.agent,
        tool: patch.tool,
        toolCallIndex: patch.toolCallIndex,
        toolCallId: patch.toolCallId,
        runId: patch.runId,
      }),
    ]),
  };
}

function findMatchingStreamStep(
  message: MessageItem,
  matcher: (step: MessageStreamStep) => boolean,
) {
  return [...(message.streamSteps ?? [])].reverse().find(matcher) ?? null;
}

function finalizeStreamingTrace(
  message: MessageItem,
  state: Extract<MessageStreamStep["state"], "done" | "error"> = "done",
  finalLabel?: string,
  finalDetail?: string,
) {
  const settled = settleLiveStreamSteps(message.streamSteps, state);
  if (!finalLabel) {
    return {
      ...message,
      streamSteps: trimStreamSteps(settled),
    };
  }

  const existingFinal = [...settled].reverse().find((step) => step.label === finalLabel);
  const finalStep = existingFinal
    ? {
        ...existingFinal,
        detail: finalDetail ?? existingFinal.detail,
        state,
      }
    : buildStreamStep(finalLabel, finalDetail, state);

  return {
    ...message,
    streamSteps: trimStreamSteps([...settled.filter((step) => step.label !== finalLabel), finalStep]),
  };
}

function summarizeStepDetail(value: string | undefined, limit = 140) {
  const normalized = value?.replace(/\s+/g, " ").trim() ?? "";
  if (!normalized) return "";
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit)}...`;
}

function formatStreamingElapsed(elapsedMs?: number) {
  return formatTimingDuration(elapsedMs);
}

function buildStatusMarkdown(status: string) {
  return `### Status\n\n${status}`;
}

function llmOutboundStepLabel(actor: string, toolName?: string) {
  return toolName ? `${actor} -> LLM (${toolName} result)` : `${actor} -> LLM`;
}

function llmInboundStepLabel(actor: string, finishReason?: string) {
  switch (finishReason) {
    case "tool_calls":
      return `LLM -> ${actor} · requested tools`;
    case "stop":
      return `LLM -> ${actor} · final answer`;
    case "length":
      return `LLM -> ${actor} · partial answer`;
    default:
      return `LLM -> ${actor}`;
  }
}

function toolCallStepLabel(actor: string, toolName: string) {
  return `${actor} calls ${toolName}`;
}

function toolOutputStepLabel(actor: string, toolName: string) {
  return `Tool Output · ${actor} · ${toolName}`;
}

function buildToolWaitKey(actor: string, toolCallIndex?: number, toolName?: string) {
  if (typeof toolCallIndex === "number") {
    return `${actor}::${toolCallIndex}`;
  }
  return `${actor}::${toolName || "tool"}`;
}

function buildLlmMetaSummary(model?: string, turn?: number) {
  return [
    model,
    typeof turn === "number" ? `turn ${turn}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function buildLlmPlannedToolsMarkdown(toolCalls?: ChatCardItem["tool_calls"]) {
  if (!toolCalls || toolCalls.length === 0) return "";
  return `### Requested Tools\n\n${toolCalls
    .map(
      (tool) =>
        `- **${tool.name || "tool"}**${
          tool.args_preview ? `\n  - args preview: \`${tool.args_preview.replace(/`/g, "'").replace(/\n/g, " ")}\`` : ""
        }`,
    )
    .join("\n")}`;
}

function isActorOutboundStep(step: MessageStreamStep, actor: string) {
  return (
    (step.agent === actor && step.kind === "llm_outbound") ||
    step.label === llmOutboundStepLabel(actor) ||
    (!step.tool && step.label.startsWith(`${actor} -> LLM (`))
  );
}

function isActorInboundStep(step: MessageStreamStep, actor: string) {
  return (
    (step.agent === actor && step.kind === "llm_inbound") ||
    step.label === llmInboundStepLabel(actor) ||
    step.label.startsWith(`LLM -> ${actor} · `)
  );
}

function llmOutcomeSummary(card: ChatCardItem) {
  if (card.finish_reason === "tool_calls") {
    if (card.tool_calls && card.tool_calls.length > 0) {
      return `Requested tools: ${card.tool_calls.map((tool) => tool.name || "tool").join(", ")}`;
    }
    return "Model requested tool calls before answering.";
  }

  if (card.finish_reason === "length") {
    return "Model returned a partial answer because the output hit a length limit.";
  }

  return "";
}

function isInternalToolPause(card: ChatCardItem) {
  if (card.kind !== "tool_call") return false;
  if (!card.blocked) return false;
  const blockedKind = String(card.blocked_kind || "").trim().toLowerCase();
  const status = String(card.status || "").trim().toLowerCase();
  return blockedKind === "approval" || blockedKind === "timeout" || status === "approval_blocked" || status === "timeout_waiting";
}

function isToolCardFailure(card: ChatCardItem) {
  if (isInternalToolPause(card)) return false;
  return card.success === false;
}

function isActorToolCallStep(step: MessageStreamStep, actor: string, toolName: string) {
  return (
    (step.agent === actor &&
      step.kind === "tool_call" &&
      (step.tool === toolName || (toolName === "tool" && !step.tool))) ||
    step.label === toolCallStepLabel(actor, toolName)
  );
}

function isActorToolCallStepByRef(
  step: MessageStreamStep,
  actor: string,
  toolName: string,
  toolCallIndex?: number,
  toolCallId?: string | null,
) {
  if (step.agent !== actor || step.kind !== "tool_call") return false;
  if (toolCallId && step.toolCallId && step.toolCallId === toolCallId) return true;
  if (typeof toolCallIndex === "number" && typeof step.toolCallIndex === "number") {
    return step.toolCallIndex === toolCallIndex;
  }
  return isActorToolCallStep(step, actor, toolName);
}

function isActorToolResultStep(step: MessageStreamStep, actor: string, toolName: string, toolCallIndex?: number) {
  return (
    (step.agent === actor &&
      step.kind === "tool_result_to_llm" &&
      ((typeof toolCallIndex === "number" && step.toolCallIndex === toolCallIndex) || step.tool === toolName)) ||
    step.label === toolOutputStepLabel(actor, toolName)
  );
}

function isRunningToolRuntimeCard(card: ChatCardItem) {
  if (card.kind !== "tool_call") return false;
  const status = (card.status || "").trim().toLowerCase();
  return status === "running" || status === "approval_blocked" || status === "timeout_waiting";
}

function streamStepHasToolOutput(step: MessageStreamStep | null | undefined) {
  const detailContent = step?.detailContent || "";
  return (
    detailContent.includes("### Tool Result") ||
    detailContent.includes("### Result") ||
    detailContent.includes("### Error")
  );
}

function streamStepStateFromRuntimeCard(card: ChatCardItem): MessageStreamStep["state"] {
  if (isRunningToolRuntimeCard(card)) return "live";
  if (isToolCardFailure(card)) return "error";
  if (card.kind === "agent_error" || card.kind === "gate_blocked" || card.kind === "gate_rejected") return "error";
  return "done";
}

function isToolResultFailure(rawResult: string) {
  const normalized = rawResult.trim();
  if (!normalized) return false;
  return /^error\b/i.test(normalized) || /^\[[^\]]+\]\s+error:/i.test(normalized);
}

const SYSTEM_PROMPT_DYNAMIC_MARKERS = [
  "\n\nThis is a standalone chat.",
  "\n\nCurrent project context:",
  "\n\nCurrent chat context:",
  "\n\nYou have access to the following tools:",
  "\n\nTeam members in this project:",
  "\n\nAvailable agents in this standalone chat:",
  "\n\nYour memories:",
  "\n\nYour memories (context for your responses):",
  "\n\nShared context from other agents:",
  "\n\nPrevious agent's work for you to build upon:",
];

function extractDisplayableSystemPrompt(value: string | undefined) {
  const normalized = (value || "").replace(/\r\n/g, "\n").trimEnd();
  if (!normalized) return "";

  let firstMarkerIndex = -1;
  for (const marker of SYSTEM_PROMPT_DYNAMIC_MARKERS) {
    const markerIndex = normalized.indexOf(marker);
    if (markerIndex === -1) continue;
    if (firstMarkerIndex === -1 || markerIndex < firstMarkerIndex) {
      firstMarkerIndex = markerIndex;
    }
  }

  if (firstMarkerIndex === -1) return "";
  return normalized.slice(firstMarkerIndex).trim();
}

function buildLiveLlmPromptDetailContent(
  userContent: string,
  options?: {
    systemPrompt?: string;
    promptMessages?: string;
    model?: string;
    turn?: number;
    waitStatus?: string;
    timings?: ChatCardLlmTimings;
  },
) {
  const sections: string[] = [];
  const normalizedUser = userContent.trim();
  const meta = buildLlmMetaSummary(options?.model, options?.turn);

  if (options?.waitStatus) {
    sections.push(buildStatusMarkdown(options.waitStatus));
  }

  if (meta) {
    sections.push(`### Meta\n\n- ${meta}`);
  }

  const timingsMarkdown = buildLlmTimingsMarkdown(options?.timings);
  if (timingsMarkdown) {
    sections.push(timingsMarkdown);
  }

  const displaySystemPrompt = extractDisplayableSystemPrompt(options?.systemPrompt);
  if (displaySystemPrompt) {
    sections.push(markdownSection("System Prompt Context", displaySystemPrompt, { asMarkdown: true }));
  }

  if (options?.promptMessages?.trim()) {
    sections.push(markdownSection("Full Prompt Payload", prettyJson(options.promptMessages), { language: "json" }));
  } else if (normalizedUser) {
    sections.push(markdownSection("User Message", normalizedUser, { language: "text" }));
  }

  if (sections.length === 0) {
    sections.push("### Status\n\nPreparing prompt for the model.");
  }

  return sections.join("\n\n");
}

function buildLiveLlmResponseDetailContent(currentDraft: string, waitStatus?: string, timings?: ChatCardLlmTimings) {
  const sections: string[] = [];
  const normalizedDraft = currentDraft.trim();
  if (waitStatus) sections.push(buildStatusMarkdown(waitStatus));

  const timingsMarkdown = buildLlmTimingsMarkdown(timings);
  if (timingsMarkdown) sections.push(timingsMarkdown);

  if (normalizedDraft) {
    sections.push(markdownSection("Current Response Draft", normalizedDraft, { asMarkdown: true }));
  }

  if (sections.length === 0) {
    sections.push("### Status\n\nWaiting for the model to return tokens.");
  }

  return sections.join("\n\n");
}

function buildLiveToolCallDetailContent(args?: string, waitStatus?: string) {
  const sections: string[] = [];

  if (waitStatus) {
    sections.push(buildStatusMarkdown(waitStatus));
  }

  if (args && args.trim()) {
    sections.push(markdownSection("Arguments", prettyJson(args), { language: "json" }));
  }

  if (sections.length === 0) {
    sections.push("### Status\n\nPreparing tool call.");
  }

  return sections.join("\n\n");
}

function buildToolResultToLlmDetailContent(result?: string, failed = false) {
  const sections: string[] = [];

  if (result && result.trim()) {
    sections.push(
      toolResultMarkdownSection(failed ? "Error" : "Tool Result", result),
    );
  }

  if (sections.length === 0) {
    sections.push("### Status\n\nWaiting for tool output.");
  }

  return sections.join("\n\n");
}

function buildCardLlmPromptDetailContent(card: ChatCardItem) {
  const sections: string[] = [];
  const meta = buildLlmMetaSummary(card.model, card.turn);
  if (meta) sections.push(`### Meta\n\n- ${meta}`);

  const timingsMarkdown = buildLlmTimingsMarkdown(card.timings);
  if (timingsMarkdown) sections.push(timingsMarkdown);

  const displaySystemPrompt = extractDisplayableSystemPrompt(card.system_prompt);
  if (displaySystemPrompt) {
    sections.push(markdownSection("System Prompt Context", displaySystemPrompt, { asMarkdown: true }));
  }

  if (card.prompt_messages) {
    sections.push(markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }));
  }

  return sections.join("\n\n");
}

function buildCardLlmResponseDetailContent(card: ChatCardItem) {
  const sections: string[] = [];
  const timingsMarkdown = buildLlmTimingsMarkdown(card.timings);
  if (timingsMarkdown) sections.push(timingsMarkdown);
  const outcomeSummary = llmOutcomeSummary(card);
  if (outcomeSummary) sections.push(buildStatusMarkdown(outcomeSummary));
  const plannedToolsMarkdown = buildLlmPlannedToolsMarkdown(card.tool_calls);
  if (plannedToolsMarkdown) sections.push(plannedToolsMarkdown);
  if (card.response) sections.push(markdownSection("Response", card.response, { asMarkdown: true }));
  if (card.raw_response) sections.push(markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }));
  return sections.join("\n\n");
}

function buildCardToolCallDetailContent(card: ChatCardItem) {
  const sections: string[] = [];
  if (card.arguments) sections.push(markdownSection("Arguments", prettyJson(card.arguments), { language: "json" }));
  return sections.join("\n\n");
}

function buildCardToolResultDetailContent(card: ChatCardItem) {
  const sections: string[] = [];
  if (card.result) {
    sections.push(
      toolResultMarkdownSection(isToolCardFailure(card) ? "Error" : "Tool Result", card.result),
    );
  }
  return sections.join("\n\n");
}

function buildCardToolStepDetailContent(card: ChatCardItem) {
  return [
    buildCardToolCallDetailContent(card),
    buildCardToolResultDetailContent(card),
  ].filter(Boolean).join("\n\n");
}

function buildLlmResponseStepDetail(card: ChatCardItem) {
  const bits: string[] = [];
  const outcomeSummary = llmOutcomeSummary(card);
  if (outcomeSummary) bits.push(outcomeSummary);
  if (card.tool_calls && card.tool_calls.length > 0) {
    bits.push(`tools: ${card.tool_calls.map((tool) => tool.name || "tool").join(", ")}`);
  }
  if (card.response) bits.push(summarizeStepDetail(card.response));
  if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
  return bits.filter(Boolean).join(" · ");
}

function buildToolCallStepDetail(card: ChatCardItem) {
  const bits: string[] = [];
  if (isRunningToolRuntimeCard(card)) bits.push("running");
  if (card.arguments) bits.push(`args: ${summarizeStepDetail(card.arguments, 90)}`);
  if (card.result && isRunningToolRuntimeCard(card)) bits.push(summarizeStepDetail(card.result, 140));
  if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
  if (typeof card.success === "boolean") bits.push(card.success ? "ok" : "failed");
  return bits.filter(Boolean).join(" · ");
}

function buildToolResultStepDetail(card: ChatCardItem) {
  const bits: string[] = [];
  if (card.result) bits.push(summarizeStepDetail(card.result));
  if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
  if (typeof card.success === "boolean") bits.push(card.success ? "ok" : "failed");
  return bits.filter(Boolean).join(" · ");
}

function buildUnifiedToolStepDetail(card: ChatCardItem) {
  return buildToolResultStepDetail(card) || buildToolCallStepDetail(card);
}

function buildCardStepDetail(card: ChatCardItem) {
  const bits: string[] = [];

  switch (card.kind) {
    case "llm_call":
      if (card.model) bits.push(card.model);
      if (typeof card.turn === "number") bits.push(`turn ${card.turn}`);
      if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
      if (card.finish_reason === "tool_calls") bits.push("requested tools");
      if (card.finish_reason === "stop") bits.push("final answer");
      if (card.finish_reason === "length") bits.push("partial answer");
      if (card.tool_calls && card.tool_calls.length > 0) {
        bits.push(`tools: ${card.tool_calls.map((tool) => tool.name || "tool").join(", ")}`);
      }
      if (card.response) bits.push(summarizeStepDetail(card.response));
      break;
    case "tool_call":
      if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
      if (typeof card.success === "boolean") bits.push(card.success ? "ok" : "failed");
      if (card.arguments) bits.push(`args: ${summarizeStepDetail(card.arguments, 90)}`);
      if (card.result) bits.push(summarizeStepDetail(card.result));
      break;
    case "agent_error":
      if (card.agent) bits.push(card.agent);
      if (card.error) bits.push(summarizeStepDetail(card.error));
      else if (card.summary) bits.push(summarizeStepDetail(card.summary));
      break;
    case "stage_start":
    case "stage_end":
      if (card.stage) bits.push(card.stage);
      if (card.summary) bits.push(summarizeStepDetail(card.summary));
      break;
    case "skill_inject":
      if (card.skills && card.skills.length > 0) {
        bits.push(card.skills.map((skill) => skill.name).filter(Boolean).join(", "));
      }
      break;
    case "agent_message":
      if (card.to_agent) bits.push(`to ${card.to_agent}`);
      if (card.content) bits.push(summarizeStepDetail(card.content));
      break;
    case "boss_instruction":
      if (card.agent) bits.push(`to ${card.agent}`);
      if (card.content_preview) bits.push(summarizeStepDetail(card.content_preview));
      break;
    case "gate_blocked":
      if (card.display_name || card.stage) bits.push(card.display_name || card.stage || "");
      bits.push("Waiting for approval");
      break;
    case "gate_approved":
      if (card.stage) bits.push(card.stage);
      break;
    case "gate_rejected":
      bits.push(`Rollback to ${card.to_stage || "previous stage"}`);
      break;
    default:
      break;
  }

  return bits.filter(Boolean).join(" · ");
}

function buildCardStepDetailContent(card: ChatCardItem) {
  switch (card.kind) {
    case "llm_call": {
      const sections: string[] = [];
      const meta = [
        card.model,
        typeof card.turn === "number" ? `turn ${card.turn}` : "",
        typeof card.duration_ms === "number" ? `${card.duration_ms}ms` : "",
        card.finish_reason === "tool_calls"
          ? "requested tools"
          : card.finish_reason === "stop"
            ? "final answer"
            : card.finish_reason === "length"
              ? "partial answer"
              : "",
      ]
        .filter(Boolean)
        .join(" · ");
      if (meta) sections.push(`### Meta\n\n- ${meta}`);
      const outcomeSummary = llmOutcomeSummary(card);
      if (outcomeSummary) sections.push(buildStatusMarkdown(outcomeSummary));
      const displaySystemPrompt = extractDisplayableSystemPrompt(card.system_prompt);
      if (displaySystemPrompt) {
        sections.push(markdownSection("System Prompt Context", displaySystemPrompt, { asMarkdown: true }));
      }
      if (card.prompt_messages) sections.push(markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }));
      if (card.tool_calls && card.tool_calls.length > 0) {
        sections.push(
          `### Requested Tools\n\n${card.tool_calls
            .map(
              (tool) =>
                `- **${tool.name || "tool"}**${
                  tool.args_preview ? `\n  - args preview: \`${tool.args_preview.replace(/`/g, "'").replace(/\n/g, " ")}\`` : ""
                }`,
            )
            .join("\n")}`,
        );
      }
      if (card.response) sections.push(markdownSection("Response", card.response, { asMarkdown: true }));
      if (card.raw_response) sections.push(markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }));
      return sections.join("\n\n");
    }
    case "tool_call": {
      const sections: string[] = [];
      if (card.arguments) sections.push(markdownSection("Arguments", prettyJson(card.arguments), { language: "json" }));
      if (card.result) {
        sections.push(
          toolResultMarkdownSection(isToolCardFailure(card) ? "Error" : "Result", card.result),
        );
      }
      return sections.join("\n\n");
    }
    case "agent_error": {
      const sections: string[] = [];
      if (card.summary) sections.push(markdownSection("Summary", card.summary, { asMarkdown: true }));
      if (card.error) sections.push(markdownSection("Error", card.error, { asMarkdown: true }));
      if (card.content) sections.push(markdownSection("Failure Detail", card.content, { asMarkdown: true }));
      return sections.join("\n\n");
    }
    case "stage_start":
    case "stage_end":
      return card.summary || card.content || card.stage || "";
    case "skill_inject":
      return card.skills
        ?.map((skill) => `${skill.name || "skill"}${skill.hint ? `\n${skill.hint}` : ""}${skill.guide ? `\n\n${skill.guide}` : ""}`)
        .join("\n\n") || "";
    case "agent_message":
      return card.content || "";
    case "boss_instruction":
      return card.content_preview || "";
    case "gate_blocked":
      return `Waiting for approval${card.display_name ? `: ${card.display_name}` : ""}`;
    case "gate_rejected":
      return `Rollback target: ${card.to_stage || "previous stage"}`;
    default:
      return "";
  }
}

function sameClientTurn(left?: string, right?: string) {
  return Boolean(left) && Boolean(right) && left === right;
}

function findMatchingServerUserMessage(rows: MessageItem[], optimisticMessage: MessageItem) {
  return rows.find((row) => {
    if (row.agent_name) return false;
    if (sameClientTurn(row.client_turn_id, optimisticMessage.client_turn_id)) {
      return true;
    }
    if (optimisticMessage.client_turn_id) return false;
    return (
      row.message_type === optimisticMessage.message_type &&
      row.content === optimisticMessage.content &&
      Math.abs(new Date(row.created_at).getTime() - new Date(optimisticMessage.created_at).getTime()) < 30_000
    );
  }) ?? null;
}

function findMatchingServerAssistantMessage(rows: MessageItem[], optimisticMessage: MessageItem) {
  return rows.find((row) => {
    if (!row.agent_name) return false;
    if (sameClientTurn(row.client_turn_id, optimisticMessage.client_turn_id)) {
      return true;
    }
    if (optimisticMessage.client_turn_id) return false;
    return new Date(row.created_at).getTime() >= new Date(optimisticMessage.created_at).getTime() - 1_000;
  }) ?? null;
}

function findMatchingTaskRun(taskRuns: TaskRunSummary[], optimisticMessage: MessageItem) {
  if (!optimisticMessage.client_turn_id) return null;
  return taskRuns.find((run) => sameClientTurn(run.client_turn_id ?? undefined, optimisticMessage.client_turn_id)) ?? null;
}

function taskRunTerminalState(taskRun: TaskRunSummary) {
  const status = (taskRun.status || "").toLowerCase();
  return status === "completed" || status === "failed" || status === "cancelled";
}

function isInternalTaskRunSummary(value: string | null | undefined) {
  const normalized = (value || "").trim().toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes("rebuild_turn_state_from_tool_round") ||
    normalized.includes("protocol_tail") ||
    normalized.includes("prior_round_summaries") ||
    normalized.includes("continue agent turn · via") ||
    normalized.includes("continue agent turn - via") ||
    normalized.includes(" · via ") ||
    normalized.includes(" - via ") ||
    normalized === "user message saved." ||
    normalized === "user message saved for execution." ||
    normalized === "user message saved for streaming execution." ||
    normalized.includes("selected standalone") ||
    normalized.includes("selected project") ||
    normalized.includes("execution mode") ||
    normalized.includes("streaming execution mode") ||
    normalized.includes("runtime mode") ||
    normalized.includes("streaming schedule") ||
    normalized.includes("orchestration schedule") ||
    normalized.includes("scheduler state") ||
    normalized.includes("checkpoint") ||
    normalized.includes("continuation cursor") ||
    normalized.includes("recovery started") ||
    normalized.includes("recovered interrupted")
  );
}

function userFacingTaskRunSummary(value: string | null | undefined) {
  const normalized = value?.trim();
  if (!normalized || isInternalTaskRunSummary(normalized)) return null;
  return normalized;
}

function taskRunActorName(taskRun: TaskRunSummary, fallback?: string | null) {
  return taskRun.target_agent_name || fallback || "Agent";
}

function taskRunContinuationToolName(taskRun: TaskRunSummary) {
  const cursor = taskRun.continuation_cursor ?? taskRun.checkpoint_snapshot?.continuation_cursor ?? null;
  if (cursor?.tool_name) return cursor.tool_name;
  const toolNames = cursor?.tool_names ?? taskRun.checkpoint_snapshot?.continuation_cursor?.tool_names ?? null;
  if (Array.isArray(toolNames) && toolNames.length > 0) return toolNames[toolNames.length - 1] || null;
  const blockedTool = taskRun.checkpoint_snapshot?.turn_local_state?.blocked_tool;
  return blockedTool && typeof blockedTool["tool_name"] === "string" ? String(blockedTool["tool_name"]) : null;
}

function taskRunDetailEvents(taskRun: TaskRunSummary | TaskRunDetail) {
  return "events" in taskRun && Array.isArray(taskRun.events) ? taskRun.events : [];
}

function latestTaskRunDetailEvent(taskRun: TaskRunSummary | TaskRunDetail, options?: { skipCompaction?: boolean }) {
  const events = taskRunDetailEvents(taskRun);
  if (events.length === 0) return null;
  if (options?.skipCompaction) {
    return [...events].reverse().find((event) => event.event_type !== "context_compaction") ?? events[events.length - 1];
  }
  return events[events.length - 1];
}

function latestTaskRunToolResult(taskRun: TaskRunSummary | TaskRunDetail) {
  const events = taskRunDetailEvents(taskRun);
  const toolRoundEvent = [...events].reverse().find((event) => event.event_type === "tool_round_recorded");
  const turnLocalState = toolRoundEvent?.payload?.turn_local_state;
  if (!turnLocalState || typeof turnLocalState !== "object") return null;
  const toolResults = (turnLocalState as Record<string, unknown>).tool_results;
  if (!Array.isArray(toolResults) || toolResults.length === 0) return null;
  const result = toolResults[toolResults.length - 1];
  return result && typeof result === "object" ? result as Record<string, unknown> : null;
}

function taskRunLatestEventType(taskRun: TaskRunSummary | TaskRunDetail) {
  const latestEvent = latestTaskRunDetailEvent(taskRun, { skipCompaction: true });
  return (
    latestEvent?.event_type ||
    taskRun.latest_event_type ||
    taskRun.checkpoint_snapshot?.latest_event_type ||
    taskRun.latest_continuation_event_type ||
    taskRun.continuation_cursor?.source_event_type ||
    taskRun.checkpoint_snapshot?.continuation_cursor?.source_event_type ||
    ""
  ).toLowerCase();
}

function taskRunUserRequestPreview(taskRun: TaskRunSummary | TaskRunDetail, limit = 140) {
  return typeof taskRun.user_request === "string" && taskRun.user_request.trim()
    ? summarizeStepDetail(taskRun.user_request, limit)
    : "";
}

function taskRunPrimarySummary(taskRun: TaskRunSummary | TaskRunDetail) {
  return userFacingTaskRunSummary(taskRun.summary) || taskRunUserRequestPreview(taskRun);
}

function latestTaskRunAgentResponse(taskRun: TaskRunSummary | TaskRunDetail) {
  const events = taskRunDetailEvents(taskRun);
  const responseEvent = [...events].reverse().find((event) => {
    const response = event.payload?.response_preview;
    return event.event_type === "agent_turn_completed" && typeof response === "string" && response.trim();
  });
  const response = responseEvent?.payload?.response_preview;
  if (typeof response === "string" && response.trim()) return response.trim();
  const checkpointResponse = taskRun.checkpoint_snapshot?.latest_agent_turn?.response_preview;
  return typeof checkpointResponse === "string" && checkpointResponse.trim() ? checkpointResponse.trim() : "";
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readTextField(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readObjectArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function readNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatTaskProgressCounts(runtime: Record<string, unknown> | null) {
  if (!runtime) return "";
  const completed = readNumber(runtime.completed_step_count);
  const running = readNumber(runtime.running_step_count);
  const ready = readNumber(runtime.ready_step_count);
  const waiting = readNumber(runtime.waiting_step_count);
  const total = readNumber(runtime.step_count);
  const parts = [
    completed !== null ? `${completed}${total !== null ? `/${total}` : ""} done` : "",
    running ? `${running} running` : "",
    ready ? `${ready} ready` : "",
    waiting ? `${waiting} waiting` : "",
  ].filter(Boolean);
  return parts.join(", ");
}

function describeSchedulerStep(step: Record<string, unknown> | null) {
  if (!step) return "";
  const agent = readTextField(step, "agent_name") || readTextField(step, "requested_name") || "agent";
  const kind = readTextField(step, "dispatch_kind");
  const status = readTextField(step, "status");
  return [agent, kind, status].filter(Boolean).join(" / ");
}

function describeSchedulerPlan(payload: Record<string, unknown> | null) {
  const runtime = readRecord(payload?.runtime);
  const runtimeSteps = readObjectArray(runtime?.steps);
  const planSteps = readObjectArray(payload?.steps);
  const steps = runtimeSteps.length > 0 ? runtimeSteps : planSteps;
  const countSummary = formatTaskProgressCounts(runtime) || [
    readNumber(payload?.blocking_step_count) !== null ? `${readNumber(payload?.blocking_step_count)} blocking` : "",
    readNumber(payload?.sidecar_step_count) !== null ? `${readNumber(payload?.sidecar_step_count)} sidecar` : "",
  ].filter(Boolean).join(", ");
  const active = steps.find((step) => readTextField(step, "status") === "running")
    || steps.find((step) => readTextField(step, "status") === "ready")
    || null;
  const next = active ? describeSchedulerStep(active) : "";
  return [countSummary, next ? `current: ${next}` : ""].filter(Boolean).join("; ");
}

function readToolArgumentSummary(argumentsText: string | null | undefined, fallback = "") {
  const text = argumentsText?.trim() || "";
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text);
    const record = readRecord(parsed);
    if (record) {
      const important = [
        readTextField(record, "command"),
        readTextField(record, "path"),
        readTextField(record, "file_path"),
        readTextField(record, "filepath"),
        readTextField(record, "query"),
        readTextField(record, "pattern"),
      ].find(Boolean);
      if (important) return summarizeStepDetail(important, 180);
    }
  } catch {
    // Arguments are often streamed as partial JSON; fall back to a plain preview.
  }
  return summarizeStepDetail(text, 180) || fallback;
}

function collectToolFileHints(toolName: string | null | undefined, argumentsText: string | null | undefined) {
  const normalizedTool = (toolName || "").toLowerCase();
  if (!/(read|write|edit|file|search|list|delete)/.test(normalizedTool)) return [];
  const text = argumentsText?.trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    const record = readRecord(parsed);
    if (!record) return [];
    return [
      readTextField(record, "path"),
      readTextField(record, "file_path"),
      readTextField(record, "filepath"),
      readTextField(record, "directory"),
      readTextField(record, "target_path"),
    ].filter((value): value is string => Boolean(value)).slice(0, 3);
  } catch {
    return [];
  }
}

function latestTaskRunToolResultPayload(taskRun: TaskRunSummary | TaskRunDetail) {
  const latestResult = latestTaskRunToolResult(taskRun);
  if (latestResult) return latestResult;
  const checkpointResults = taskRun.checkpoint_snapshot?.turn_local_state?.tool_results;
  const result = Array.isArray(checkpointResults) ? checkpointResults[checkpointResults.length - 1] : null;
  return readRecord(result);
}

function latestTaskRunEventOfType(taskRun: TaskRunSummary | TaskRunDetail, eventType: string) {
  const events = taskRunDetailEvents(taskRun);
  return [...events].reverse().find((event) => event.event_type === eventType) ?? null;
}

function buildTaskRunStatusDetail(taskRun: TaskRunSummary | TaskRunDetail, actor = "Agent") {
  const latestEvent = latestTaskRunDetailEvent(taskRun, { skipCompaction: true });
  const payload = readRecord(latestEvent?.payload);
  const latestEventType = taskRunLatestEventType(taskRun);
  const lines: string[] = [];
  const pushLine = (label: string, value: string | null | undefined) => {
    const detail = value?.trim();
    if (detail) lines.push(`${label}: ${detail}`);
  };

  if (latestEventType === "scheduler_plan_created") {
    pushLine("Plan", describeSchedulerPlan(payload) || eventSummaryFromTaskRunEvent(latestEvent));
  } else if (latestEventType === "user_message_saved") {
    pushLine("User", readTextField(payload, "content") || readTextField(payload, "content_preview"));
  } else if (latestEventType === "target_agent_selected") {
    pushLine("User", readTextField(readRecord(latestTaskRunEventOfType(taskRun, "user_message_saved")?.payload), "content_preview"));
    pushLine("Agent", readTextField(payload, "agent_name") || actor);
    pushLine("Model", readTextField(payload, "model"));
    const tools = readStringArray(payload?.tool_names);
    pushLine("Tools", tools.length > 0 ? tools.join(", ") : "");
  } else if (latestEventType === "target_agents_selected") {
    pushLine("User", readTextField(readRecord(latestTaskRunEventOfType(taskRun, "user_message_saved")?.payload), "content_preview"));
    const agents = readStringArray(payload?.agent_names);
    pushLine("Agents", agents.length > 0 ? agents.join(", ") : "");
    pushLine("Mode", readTextField(payload, "run_kind"));
  } else if (latestEventType === "scheduler_step_dispatched" || latestEventType === "scheduler_step_resumed") {
    const stepState = readRecord(payload?.step_state);
    pushLine("Plan", describeSchedulerPlan(payload));
    pushLine("Agent", describeSchedulerStep(stepState) || eventSummaryFromTaskRunEvent(latestEvent));
  } else if (latestEventType === "scheduler_step_completed") {
    const released = readNumber(payload?.released_step_count);
    pushLine("Plan", describeSchedulerPlan(payload));
    pushLine("Released", released !== null ? String(released) : "");
  } else if (latestEventType === "approval_queue_item_created" || Number(taskRun.pending_approval_count || 0) > 0) {
    const tool = readTextField(payload, "target_name") || taskRunContinuationToolName(taskRun);
    pushLine("Approval", tool || "pending");
  } else if (latestEventType === "approval_queue_item_resolved") {
    const tool = readTextField(payload, "target_name") || readTextField(payload, "tool_name") || taskRunContinuationToolName(taskRun);
    pushLine("Approval", tool || "resolved");
    pushLine("Status", readTextField(payload, "status"));
  } else if (latestEventType === "approval_queue_item_followup_triggered") {
    const tool = readTextField(payload, "tool_name") || taskRunContinuationToolName(taskRun);
    pushLine("Tool", tool);
    pushLine("Followup", readTextField(payload, "replay_status") || "triggered");
  } else if (latestEventType === "tool_call_started") {
    const tool = readTextField(payload, "tool_name") || taskRunContinuationToolName(taskRun) || "tool";
    const args = readToolArgumentSummary(readTextField(payload, "arguments"));
    pushLine("Tool", tool);
    pushLine("Args", args);
    const files = collectToolFileHints(tool, readTextField(payload, "arguments"));
    pushLine("Files", files.join(", "));
  } else if (latestEventType === "tool_round_recorded" || latestEventType === "tool_call_blocked") {
    const result = latestTaskRunToolResultPayload(taskRun);
    const tool = readTextField(result, "tool_name") || readTextField(payload, "tool_name") || taskRunContinuationToolName(taskRun) || "tool";
    const status = readTextField(result, "status") || readTextField(payload, "status");
    const resultText = readTextField(result, "blocked_reason") || readTextField(result, "result");
    pushLine("Tool", tool);
    pushLine("Status", status);
    pushLine("Result", resultText ? summarizeStepDetail(resultText, 180) : "");
    const files = collectToolFileHints(tool, readTextField(result, "arguments"));
    pushLine("Files", files.join(", "));
  } else if (latestEventType === "llm_request_created") {
    const model = readTextField(payload, "model");
    const turn = readNumber(payload?.turn);
    pushLine("LLM", "request");
    pushLine("Model", model);
    pushLine("Turn", turn !== null ? String(turn) : "");
  } else if (latestEventType === "llm_response_started") {
    const turn = readNumber(payload?.turn);
    pushLine("LLM", "response_started");
    pushLine("Turn", turn !== null ? String(turn) : "");
  } else if (latestEventType === "llm_response_completed") {
    const finishReason = readTextField(payload, "finish_reason");
    pushLine("LLM", "response_completed");
    pushLine("Finish", finishReason);
  } else if (latestEventType === "agent_turn_started") {
    pushLine("Agent", latestEvent?.agent_name || actor);
    pushLine("Turn", readNumber(payload?.turn) !== null ? String(readNumber(payload?.turn)) : "");
  } else if (latestEventType === "agent_turn_completed") {
    const response = readTextField(payload, "response_preview") || latestTaskRunAgentResponse(taskRun);
    pushLine("Agent", latestEvent?.agent_name || actor);
    pushLine("Response", response ? summarizeStepDetail(response, 180) : "");
  } else {
    pushLine("Event", latestEventType);
  }

  const schedulerRuntime =
    readRecord(payload?.runtime)
    || readRecord(taskRun.latest_scheduler_runtime)
    || readRecord(taskRun.checkpoint_snapshot?.latest_scheduler_runtime);
  const schedulerSummary = formatTaskProgressCounts(schedulerRuntime);
  if (schedulerSummary && !lines.some((line) => line.startsWith("Plan:"))) {
    pushLine("Plan", schedulerSummary);
  }

  const activeHandle = readActiveHandleSummaryFromTaskRun(taskRun);
  if (activeHandle) pushLine("Background", activeHandle);

  if (lines.length > 0) return lines.slice(0, 4).join("\n");

  return "";
}

function buildFieldStatusDetail(lines: Array<[string, string | null | undefined]>) {
  return lines
    .map(([label, value]) => {
      const detail = value?.trim();
      return detail ? `${label}: ${detail}` : "";
    })
    .filter(Boolean)
    .join("\n");
}

function eventSummaryFromTaskRunEvent(event: ReturnType<typeof latestTaskRunDetailEvent>) {
  return userFacingTaskRunSummary(typeof event?.summary === "string" ? event.summary : null) || "";
}

function readActiveHandleSummaryFromTaskRun(taskRun: TaskRunSummary | TaskRunDetail) {
  const checkpoint = readRecord(taskRun.checkpoint_snapshot);
  const handles = readRecord(checkpoint?.subagent_handles);
  const entries = readObjectArray(handles?.entries);
  const active = entries.find((entry) => {
    const status = (readTextField(entry, "status") || readTextField(entry, "control_state") || "").toLowerCase();
    return status && !["completed", "failed", "cancelled", "closed"].includes(status);
  });
  if (!active) return "";
  return describeSchedulerStep(active);
}

function formatInlineSourceList(values: string[]) {
  return values.length > 0 ? values.map((value) => `\`${value}\``).join(", ") : "none";
}

function buildContextCompactionStepDetail(payload: Record<string, unknown> | null, fallbackSummary?: string | null) {
  const diagnostics = readRecord(payload?.selector_diagnostics);
  if (!diagnostics) return { detail: fallbackSummary || "Context was compacted.", detailContent: fallbackSummary || "" };

  const selector = readRecord(diagnostics.selector);
  const summary = readRecord(diagnostics.summary);
  const developer = readRecord(diagnostics.developer);
  const user = readRecord(diagnostics.user);
  const roleBudgets = readRecord(selector?.max_tokens_by_role);
  const scopeBudgets = readRecord(selector?.max_tokens_by_scope);
  const scopeUsage = readRecord(summary?.by_scope);
  const maxFragments = readNumber(selector?.max_fragments);
  const maxTokens = readNumber(selector?.max_tokens);
  const candidateCount = readNumber(summary?.candidate_count);
  const selectedCount = readNumber(summary?.selected_count);
  const droppedCount = readNumber(summary?.dropped_count) ?? 0;
  const truncatedCount = readNumber(summary?.truncated_count) ?? 0;
  const candidateTokens = readNumber(summary?.candidate_tokens);
  const selectedTokens = readNumber(summary?.selected_tokens);
  const droppedSources = [
    ...readStringArray(developer?.dropped_sources),
    ...readStringArray(user?.dropped_sources),
  ];
  const truncatedSources = [
    ...readStringArray(developer?.truncated_sources),
    ...readStringArray(user?.truncated_sources),
  ];
  const budget = [
    maxFragments !== null ? `${maxFragments} fragments` : "",
    maxTokens !== null ? `${maxTokens} tokens` : "",
  ].filter(Boolean).join(" / ");
  const detail = [
    candidateCount !== null && selectedCount !== null ? `${candidateCount} -> ${selectedCount} fragments` : "",
    candidateTokens !== null && selectedTokens !== null ? `${candidateTokens} -> ${selectedTokens} tokens` : "",
    `${droppedCount} dropped`,
    `${truncatedCount} truncated`,
  ].filter(Boolean).join(" · ");
  const detailContent = [
    fallbackSummary ? `### Summary\n\n${fallbackSummary}` : "",
    `### Why\n\nThe context selector compacted the prompt${budget ? ` to fit the ${budget} budget` : ""}.`,
    [
      "### What Changed",
      "",
      `- Dropped sources: ${formatInlineSourceList(droppedSources)}`,
      `- Truncated sources: ${formatInlineSourceList(truncatedSources)}`,
      roleBudgets ? `- Role budgets: developer ${readNumber(roleBudgets?.developer) ?? "?"} / user ${readNumber(roleBudgets?.user) ?? "?"}` : "",
      scopeBudgets ? `- Scope budgets: ${Object.entries(scopeBudgets).map(([scope, value]) => `${scope} ${value}`).join(" / ")}` : "",
      scopeUsage ? `- Scope usage: ${Object.entries(scopeUsage).map(([scope, report]) => {
        const scopeReport = readRecord(report);
        return `${scope} ${readNumber(scopeReport?.selected_count) ?? "?"}/${readNumber(scopeReport?.candidate_count) ?? "?"} fragments, ${readNumber(scopeReport?.selected_tokens) ?? "?"}/${readNumber(scopeReport?.candidate_tokens) ?? "?"} tokens`;
      }).join(" / ")}` : "",
      developer ? `- Developer context: ${readNumber(developer.selected_count) ?? "?"}/${readNumber(developer.candidate_count) ?? "?"} selected, ${readNumber(developer.selected_tokens) ?? "?"}/${readNumber(developer.candidate_tokens) ?? "?"} tokens` : "",
      user ? `- User context: ${readNumber(user.selected_count) ?? "?"}/${readNumber(user.candidate_count) ?? "?"} selected, ${readNumber(user.selected_tokens) ?? "?"}/${readNumber(user.candidate_tokens) ?? "?"} tokens` : "",
    ].filter(Boolean).join("\n"),
  ].filter(Boolean).join("\n\n");
  return { detail, detailContent };
}

function updateRecoveredTaskRunPlaceholder(message: MessageItem, taskRun: TaskRunSummary | TaskRunDetail) {
  const actor = taskRunActorName(taskRun, message.agent_name);
  const detail = buildTaskRunStatusDetail(taskRun, actor);
  return {
    ...message,
    content: message.content,
    statusDetail: detail || message.statusDetail,
    agent_name: actor,
    client_turn_id: taskRun.client_turn_id || message.client_turn_id,
    isStreaming: true,
    streamSteps: [],
  };
}

function messageLooksApprovalHold(message: MessageItem) {
  return (message.streamSteps ?? []).some((step) => {
    const label = (step.label || "").toLowerCase();
    const detail = (step.detail || "").toLowerCase();
    return label.includes("waiting for approval") || detail.includes("waiting for approval");
  });
}

function finalizeRecoveredTaskRunPlaceholder(message: MessageItem, taskRun: TaskRunSummary) {
  const status = (taskRun.status || "").toLowerCase();
  const isFailed = status === "failed" || status === "cancelled";
  const label = isFailed ? "Failed" : status === "completed" ? "Completed" : "Runtime";
  const detail =
    userFacingTaskRunSummary(taskRun.summary) ||
    latestTaskRunAgentResponse(taskRun) ||
    taskRunUserRequestPreview(taskRun) ||
    (isFailed ? "Task run stopped before a final reply was saved." : "Task run state recovered from the run ledger.");

  return finalizeStreamingTrace(
    {
      ...message,
      content: message.content || detail,
      agent_name: taskRun.target_agent_name || message.agent_name,
      client_turn_id: taskRun.client_turn_id || message.client_turn_id,
      isStreaming: false,
    },
    isFailed ? "error" : "done",
    label,
    detail,
  );
}

function replayRuntimeCardsForTurn(message: MessageItem, cards: ChatCardItem[]) {
  if (cards.length === 0) return message;
  const lastActor =
    [...cards]
      .reverse()
      .map((card) => card.agent || card.from_agent || card.to_agent)
      .find((actor): actor is string => Boolean(actor)) ?? null;
  if (lastActor && message.agent_name !== lastActor) {
    return { ...message, agent_name: lastActor };
  }
  return message;
}

function finalizeRecoveredPlaceholder(
  message: MessageItem,
  savedMessage: MessageItem,
  finalDetail = "Recovered from saved server state.",
) {
  const alreadyCompleted = (message.streamSteps ?? []).some((step) => step.label === "Completed");
  return finalizeStreamingTrace(
    {
      ...message,
      id: savedMessage.id,
      content: savedMessage.content || message.content || "(Agent returned empty response)",
      created_at: savedMessage.created_at,
      message_type: savedMessage.message_type,
      agent_name: savedMessage.agent_name || message.agent_name,
      client_turn_id: savedMessage.client_turn_id || message.client_turn_id,
      isStreaming: false,
    },
    "done",
    alreadyCompleted ? undefined : "Completed",
    alreadyCompleted ? undefined : finalDetail,
  );
}

function reconcileOptimisticMessagesWithServer(
  current: MessageItem[],
  rows: MessageItem[],
  cards: ChatCardItem[],
  taskRuns: TaskRunSummary[] = [],
) {
  return current.reduce<MessageItem[]>((next, item) => {
    if (item.optimisticKind === "user") {
      if (findMatchingServerUserMessage(rows, item)) {
        return next;
      }
      next.push(item);
      return next;
    }

    if (item.optimisticKind === "assistant_placeholder") {
      const turnCards = item.client_turn_id
        ? cards.filter((card) => sameClientTurn(card.client_turn_id, item.client_turn_id))
        : [];
      const replayed = replayRuntimeCardsForTurn(item, turnCards);
      const savedReply = findMatchingServerAssistantMessage(rows, item);
      if (savedReply) {
        next.push(finalizeRecoveredPlaceholder(replayed, savedReply));
        return next;
      }

      const matchingTaskRun = findMatchingTaskRun(taskRuns, item);
      if (matchingTaskRun) {
        const taskRunDetail = "events" in matchingTaskRun
          ? matchingTaskRun
          : taskRuns.find((run) => run.id === matchingTaskRun.id && "events" in run) || matchingTaskRun;
        const pendingApprovalCount = Number(matchingTaskRun.pending_approval_count || 0);
        if (taskRunTerminalState(matchingTaskRun)) {
          next.push(finalizeRecoveredTaskRunPlaceholder(replayed, matchingTaskRun));
          return next;
        }
        if (pendingApprovalCount === 0 && messageLooksApprovalHold(item)) {
          next.push(updateRecoveredTaskRunPlaceholder(replayed, taskRunDetail));
          return next;
        }
        next.push(updateRecoveredTaskRunPlaceholder(replayed, taskRunDetail));
        return next;
      }

      next.push(
        turnCards.length > 0
          ? {
              ...replayed,
              isStreaming: true,
            }
          : item,
      );
      return next;
    }

    next.push(item);
    return next;
  }, []);
}

function upsertProject(current: ProjectSummary[], nextProject: ProjectSummary) {
  const existingIndex = current.findIndex((project) => project.id === nextProject.id);
  if (existingIndex === -1) {
    return [nextProject, ...current];
  }

  const next = [...current];
  next[existingIndex] = nextProject;
  return next;
}

function reorderProjects(current: ProjectSummary[], draggedProjectId: number, targetProjectId: number) {
  if (draggedProjectId === targetProjectId) return current;
  const next = [...current];
  const draggedIndex = next.findIndex((project) => project.id === draggedProjectId);
  const targetIndex = next.findIndex((project) => project.id === targetProjectId);
  if (draggedIndex === -1 || targetIndex === -1) return current;

  const [draggedProject] = next.splice(draggedIndex, 1);
  next.splice(targetIndex, 0, draggedProject);
  return next.map((project, index) => ({ ...project, display_order: index }));
}

function App() {
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const [activeConfigSection, setActiveConfigSection] = useState<ConfigSection>("agents");
  const [sidebarDrawerOpen, setSidebarDrawerOpen] = useState(false);
  const [activityDrawerOpen, setActivityDrawerOpen] = useState(false);
  const [appSidebarWidth, setAppSidebarWidth] = useState(APP_SIDEBAR_DEFAULT_WIDTH);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [authorizationRules, setAuthorizationRules] = useState<ToolAuthorizationRule[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<number | null>(() => readLastChatId());
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [optimisticMessages, setOptimisticMessages] = useState<MessageItem[]>([]);
  const [chatCards, setChatCards] = useState<ChatCardItem[]>([]);
  const [taskRuns, setTaskRuns] = useState<TaskRunSummary[]>([]);
  const [chatProcesses, setChatProcesses] = useState<ChatProcessEntry | null>(null);
  const [projectBrowserIndex, setProjectBrowserIndex] = useState<ProjectBrowserIndex | null>(null);
  const [liveTaskRunDetailsById, setLiveTaskRunDetailsById] = useState<Record<number, TaskRunDetail>>({});
  const [taskActivitiesById, setTaskActivitiesById] = useState<Record<number, TaskActivityProjection>>({});
  const [taskTimelinesById, setTaskTimelinesById] = useState<Record<number, ChatTimelineProjection>>({});
  const [chatEvents, setChatEvents] = useState<ChatEventItem[]>([]);
  const [connectionState, setConnectionState] = useState<"connected" | "connecting" | "disconnected">("connecting");
  const [bootstrapped, setBootstrapped] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [refreshingMessages, setRefreshingMessages] = useState(false);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [creatingProjectFromChat, setCreatingProjectFromChat] = useState(false);
  const [syncingProjectId, setSyncingProjectId] = useState<number | null>(null);
  const [savingConfig, setSavingConfig] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [error, setError] = useState<string>("");
  const primaryAgent = useMemo(
    () => findAgentByType(agents, DEFAULT_AGENT_TYPE) ?? agents[0] ?? null,
    [agents],
  );
  const settingsSections = useMemo(
    () => [
      {
        id: "agents" as const,
        label: CONFIG_SECTION_META.agents.sidebarLabel,
        description: CONFIG_SECTION_META.agents.sidebarDescription,
        badge: `${agents.length}`,
      },
      {
        id: "skills" as const,
        label: CONFIG_SECTION_META.skills.sidebarLabel,
        description: CONFIG_SECTION_META.skills.sidebarDescription,
        badge: `${new Set(agents.flatMap((agent) => agent.skills ?? [])).size}`,
      },
      {
        id: "tools" as const,
        label: CONFIG_SECTION_META.tools.sidebarLabel,
        description: CONFIG_SECTION_META.tools.sidebarDescription,
        badge: `${new Set(agents.flatMap((agent) => agent.tools ?? [])).size}`,
      },
      {
        id: "memory" as const,
        label: CONFIG_SECTION_META.memory.sidebarLabel,
        description: CONFIG_SECTION_META.memory.sidebarDescription,
      },
      {
        id: "permissions" as const,
        label: CONFIG_SECTION_META.permissions.sidebarLabel,
        description: CONFIG_SECTION_META.permissions.sidebarDescription,
      },
      {
        id: "context" as const,
        label: CONFIG_SECTION_META.context.sidebarLabel,
        description: CONFIG_SECTION_META.context.sidebarDescription,
        badge: `${Object.keys(config?.context?.selector_profiles ?? {}).length}`,
      },
    ],
    [agents, config?.context?.selector_profiles],
  );
  const activeConfigMeta = CONFIG_SECTION_META[activeConfigSection];
  const appShellStyle = useMemo(
    () => ({ "--app-sidebar-width": `${appSidebarWidth}px` }) as CSSProperties,
    [appSidebarWidth],
  );

  const socketRef = useRef<WebSocket | null>(null);
  const bootstrappedRef = useRef<boolean>(bootstrapped);
  const selectedChatIdRef = useRef<number | null>(selectedChatId);
  const selectedProjectIdRef = useRef<number | null>(selectedProjectId);
  const chatsRef = useRef<ChatSummary[]>(chats);
  const projectsRef = useRef<ProjectSummary[]>(projects);
  const activeChatRef = useRef<ChatSummary | null>(null);
  const optimisticScopeRef = useRef<string>(optimisticScopeKey(selectedChatId));
  const joinedRoomRef = useRef<number | null>(null);
  const tempMessageIdRef = useRef(-1);
  const streamingAssistantIdRef = useRef<number | null>(null);
  const sendAbortRef = useRef<AbortController | null>(null);
  const processRefreshTimerRef = useRef<number | null>(null);
  const lastProcessRefreshByChatRef = useRef<Record<number, number>>({});
  function isCurrentChatRequest(chatId: number | null | undefined) {
    return Boolean(chatId) && selectedChatIdRef.current === chatId;
  }
  const handleAppSidebarResizeStart = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = appSidebarWidth;
    const pointerId = event.pointerId;
    event.currentTarget.setPointerCapture(pointerId);

    function handlePointerMove(moveEvent: PointerEvent) {
      setAppSidebarWidth(clampNumber(startWidth + moveEvent.clientX - startX, APP_SIDEBAR_MIN_WIDTH, APP_SIDEBAR_MAX_WIDTH));
    }

    function handlePointerUp() {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
  };

  function pushEvent(message: string, tone: ChatEventTone = "info") {
    setChatEvents((current) => [...current.slice(-79), buildEvent(message, tone)]);
  }

  function pushCard(card: ChatCardItem) {
    setChatCards((current) => mergeCards(current, [card]).slice(-40));
  }

  function patchProcessTreeEntry(
    node: ChatProcessEntry | null,
    patch: {
      taskRunId: number;
      stepId: string;
      status?: string;
      availableActions?: string[];
      controlState?: string | null;
      terminal?: boolean;
      label?: string;
      detail?: string | null;
      dispatchKind?: string | null;
      source?: string | null;
      timestamp?: string | null;
    },
  ): ChatProcessEntry | null {
    if (!node) return node;
    const metadata = node.metadata && typeof node.metadata === "object" ? node.metadata : null;
    const matches =
      node.kind === "subagent"
      && typeof metadata?.["task_run_id"] === "number"
      && metadata["task_run_id"] === patch.taskRunId
      && typeof metadata?.["step_id"] === "string"
      && metadata["step_id"] === patch.stepId;
    const nextChildren = node.children
      .map((child) => patchProcessTreeEntry(child, patch))
      .filter((child): child is ChatProcessEntry => child !== null);
    if (!matches) {
      const childrenChanged = nextChildren.some((child, index) => child !== node.children[index]);
      return childrenChanged ? { ...node, children: nextChildren } : node;
    }
    const nextMetadata: Record<string, unknown> = { ...(metadata || {}) };
    if (patch.availableActions) nextMetadata["available_actions"] = patch.availableActions;
    if (patch.controlState !== undefined) nextMetadata["control_state"] = patch.controlState;
    nextMetadata["task_run_id"] = patch.taskRunId;
    if (patch.dispatchKind !== undefined) nextMetadata["dispatch_kind"] = patch.dispatchKind;
    if (patch.source !== undefined) nextMetadata["source"] = patch.source;
    return {
      ...node,
      label: patch.label || node.label,
      detail: patch.detail ?? node.detail,
      status: patch.terminal ? "terminated" : (patch.status || node.status),
      metadata: nextMetadata,
      timestamp: patch.timestamp || node.timestamp,
      children: nextChildren,
    };
  }

  function upsertTaskProcessEntry(
    node: ChatProcessEntry | null,
    taskRun: TaskRunSummary | TaskRunDetail,
  ): ChatProcessEntry | null {
    if (!node) return node;
    const nextChildren = node.children.map((child) => upsertTaskProcessEntry(child, taskRun));
    const matchesTaskNode = node.kind === "task" && node.id === `task-run:${taskRun.id}`;
    if (matchesTaskNode) {
      return {
        ...node,
        label: taskRun.title || node.label,
        detail: taskRunPrimarySummary(taskRun) || node.detail,
        status: taskRunTerminalState(taskRun) ? "terminated" : "running",
        timestamp: taskRun.updated_at || taskRun.created_at || node.timestamp,
        children: nextChildren,
      };
    }
    const childrenChanged = nextChildren.some((child, index) => child !== node.children[index]);
    if (node.kind === "chat" && node.id.startsWith("chat:")) {
      const hasTaskChild = nextChildren.some((child) => child.kind === "task" && child.id === `task-run:${taskRun.id}`);
      if (!hasTaskChild && !taskRunTerminalState(taskRun)) {
        return {
          ...node,
          children: [
            {
              id: `task-run:${taskRun.id}`,
              label: taskRun.title || `Task run ${taskRun.id}`,
              kind: "task",
              detail: taskRunPrimarySummary(taskRun) || "Task run is active.",
              status: "running",
              parent_id: node.id,
              timestamp: taskRun.updated_at || taskRun.created_at || new Date().toISOString(),
              children: [],
            },
            ...nextChildren,
          ],
        };
      }
    }
    return childrenChanged ? { ...node, children: nextChildren } : node;
  }

  function buildSubagentRuntimePatchFromHandle(
    taskRun: TaskRunSummary | TaskRunDetail,
    handle: Record<string, unknown>,
  ) {
    const stepId = typeof handle["step_id"] === "string" ? handle["step_id"] : "";
    if (!stepId) return null;
    const dispatchKind = typeof handle["dispatch_kind"] === "string" ? handle["dispatch_kind"] : "subagent";
    const controlState =
      typeof handle["control_state"] === "string"
        ? handle["control_state"]
        : typeof handle["status"] === "string"
          ? handle["status"]
          : null;
    const availableActions = Array.isArray(handle["available_actions"])
      ? handle["available_actions"].filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const terminal =
      handle["terminal"] === true
      || handle["closed"] === true
      || isTerminalHandleStatus(typeof handle["status"] === "string" ? handle["status"] : controlState || undefined);
    const labelActor =
      typeof handle["agent_name"] === "string"
        ? handle["agent_name"]
        : typeof handle["requested_name"] === "string"
          ? handle["requested_name"]
          : typeof handle["agent_type"] === "string"
            ? handle["agent_type"]
            : "subagent";
    const responsePreview = typeof handle["response_preview"] === "string" ? handle["response_preview"].trim() : "";
    const dependencyStepId = typeof handle["dependency_step_id"] === "string" ? handle["dependency_step_id"].trim() : "";
    const summaryText = typeof handle["summary_text"] === "string" ? handle["summary_text"].trim() : "";
    const detailParts = [
      responsePreview,
      !responsePreview ? controlState : "",
      dependencyStepId ? `waiting on ${dependencyStepId}` : "",
      availableActions.length > 0 ? `actions: ${availableActions.join(", ")}` : "",
    ].filter(Boolean);
    return {
      taskRunId: taskRun.id,
      stepId,
      status: typeof handle["status"] === "string" ? handle["status"] : controlState || undefined,
      availableActions,
      controlState,
      terminal,
      label: `${labelActor} (${dispatchKind})`,
      detail: summaryText || detailParts.join(" | ") || controlState || (typeof handle["status"] === "string" ? handle["status"] : null),
      dispatchKind,
      source: typeof handle["source"] === "string" ? handle["source"] : null,
      timestamp: taskRun.updated_at || taskRun.created_at || null,
    };
  }

  function reconcileTaskProcessSubagentEntry(
    node: ChatProcessEntry | null,
    taskRun: TaskRunSummary | TaskRunDetail,
  ): ChatProcessEntry | null {
    if (!node) return node;
    const activeHandle = readActiveSubagentHandleFromTaskRun(taskRun);
    const activePatch = activeHandle ? buildSubagentRuntimePatchFromHandle(taskRun, activeHandle) : null;
    const activeStepId = activePatch?.stepId ?? null;
    const terminalizeChild = (child: ChatProcessEntry) => {
      if (child.kind !== "subagent") return child;
      const metadata = child.metadata && typeof child.metadata === "object" ? child.metadata : {};
      return {
        ...child,
        status: "terminated",
        metadata: {
          ...metadata,
          available_actions: [],
          control_state:
            activePatch || taskRunTerminalState(taskRun)
              ? "completed"
              : metadata["control_state"],
        },
      };
    };

    const nextChildren = node.children.map((child) => reconcileTaskProcessSubagentEntry(child, taskRun));
    const matchesTaskNode = node.kind === "task" && node.id === `task-run:${taskRun.id}`;
    if (!matchesTaskNode) {
      const childrenChanged = nextChildren.some((child, index) => child !== node.children[index]);
      return childrenChanged ? { ...node, children: nextChildren } : node;
    }

    let sawActiveChild = false;
    const patchedChildren = nextChildren.map((child) => {
      if (child.kind !== "subagent") return child;
      const metadata = child.metadata && typeof child.metadata === "object" ? child.metadata : null;
      const childStepId = typeof metadata?.["step_id"] === "string" ? metadata["step_id"] : "";
      if (activePatch && childStepId === activeStepId) {
        sawActiveChild = true;
        return patchProcessTreeEntry(child, activePatch) ?? child;
      }
      if (taskRunTerminalState(taskRun) || !activePatch || childStepId) {
        return terminalizeChild(child);
      }
      return child;
    });

    let finalChildren = patchedChildren;
    if (activePatch && !activePatch.terminal && !sawActiveChild) {
      finalChildren = [
        {
          id: `subagent:${activePatch.stepId}`,
          label: activePatch.label || `subagent (${activePatch.dispatchKind || "subagent"})`,
          kind: "subagent",
          detail: activePatch.detail ?? activePatch.controlState ?? activePatch.status ?? "Subagent is active.",
          status: activePatch.status || "running",
          parent_id: `task-run:${taskRun.id}`,
          timestamp: activePatch.timestamp || new Date().toISOString(),
          metadata: {
            task_run_id: activePatch.taskRunId,
            step_id: activePatch.stepId,
            dispatch_kind: activePatch.dispatchKind,
            control_state: activePatch.controlState,
            available_actions: activePatch.availableActions,
            source: activePatch.source,
          },
          children: [],
        },
        ...patchedChildren,
      ];
    }

    const childrenChanged = finalChildren.some((child, index) => child !== node.children[index]) || finalChildren.length !== node.children.length;
    return childrenChanged ? { ...node, children: finalChildren } : node;
  }

  function upsertSubagentProcessEntry(
    node: ChatProcessEntry | null,
    patch: {
      taskRunId: number;
      stepId: string;
      status?: string;
      availableActions?: string[];
      controlState?: string | null;
      terminal?: boolean;
      label?: string;
      detail?: string | null;
      dispatchKind?: string | null;
      source?: string | null;
      timestamp?: string | null;
    },
  ): ChatProcessEntry | null {
    if (!node) return node;
    const nextNode = patchProcessTreeEntry(node, patch);
    if (!nextNode) return nextNode;
    const nextChildren = nextNode.children.map((child) => upsertSubagentProcessEntry(child, patch));
    const childrenChanged = nextChildren.some((child, index) => child !== nextNode.children[index]);
    const matchesTaskNode = nextNode.kind === "task" && nextNode.id === `task-run:${patch.taskRunId}`;
    if (!matchesTaskNode) {
      return childrenChanged ? { ...nextNode, children: nextChildren } : nextNode;
    }
    const hasSubagentChild = nextChildren.some((child) => child.kind === "subagent" && child.id === `subagent:${patch.stepId}`);
    if (hasSubagentChild || patch.terminal) {
      return childrenChanged ? { ...nextNode, children: nextChildren } : nextNode;
    }
    return {
      ...nextNode,
      children: [
        {
          id: `subagent:${patch.stepId}`,
          label: patch.label || `subagent (${patch.dispatchKind || "consult"})`,
          kind: "subagent",
          detail: patch.detail ?? patch.controlState ?? patch.status ?? "Subagent is active.",
          status: patch.terminal ? "terminated" : (patch.status || "running"),
          parent_id: `task-run:${patch.taskRunId}`,
          timestamp: patch.timestamp || new Date().toISOString(),
          metadata: {
            task_run_id: patch.taskRunId,
            step_id: patch.stepId,
            dispatch_kind: patch.dispatchKind,
            control_state: patch.controlState,
            available_actions: patch.availableActions,
            source: patch.source,
          },
          children: [],
        },
        ...nextChildren,
      ],
    };
  }

  function patchTaskActivityHandle(
    handle: Record<string, unknown> | null | undefined,
    patch: {
      stepId: string;
      status?: string;
      availableActions?: string[];
      controlState?: string | null;
      note?: string | null;
    },
  ) {
    if (!handle || typeof handle !== "object") return handle;
    if (typeof handle["step_id"] !== "string" || handle["step_id"] !== patch.stepId) return handle;
    const nextHandle: Record<string, unknown> = { ...handle };
    if (patch.status !== undefined) nextHandle["status"] = patch.status;
    if (patch.availableActions) nextHandle["available_actions"] = patch.availableActions;
    if (patch.controlState !== undefined) nextHandle["control_state"] = patch.controlState;
    if (patch.note !== undefined) nextHandle["note"] = patch.note;
    return nextHandle;
  }

  function patchTaskActivityProjection(
    activity: TaskActivityProjection,
    patch: {
      taskRunId: number;
      stepId: string;
      status?: string;
      availableActions?: string[];
      controlState?: string | null;
      terminal?: boolean;
      note?: string | null;
    },
  ) {
    if (activity.task_run_id !== patch.taskRunId) return activity;
    const background = activity.background && typeof activity.background === "object" ? activity.background : {};
    const nextBackground: Record<string, unknown> = { ...background };
    if ("active_consult_handle" in nextBackground) {
      nextBackground["active_consult_handle"] = patchTaskActivityHandle(
        nextBackground["active_consult_handle"] as Record<string, unknown> | null | undefined,
        patch,
      );
    }
    if ("active_subagent_handle" in nextBackground) {
      nextBackground["active_subagent_handle"] = patchTaskActivityHandle(
        nextBackground["active_subagent_handle"] as Record<string, unknown> | null | undefined,
        patch,
      );
    }
    return {
      ...activity,
      background: nextBackground,
    };
  }

  function patchMessageRuntimeSummary(
    message: MessageItem,
    patch: {
      taskRunId: number;
      stepId: string;
      status?: string;
      availableActions?: string[];
      controlState?: string | null;
      note?: string | null;
    },
  ) {
    const summary = message.runtime_summary;
    if (!summary || summary.task_run_id !== patch.taskRunId) return message;
    const nextSummary = { ...summary };
    if (summary.active_consult_handle && typeof summary.active_consult_handle === "object") {
      nextSummary.active_consult_handle = patchTaskActivityHandle(summary.active_consult_handle, patch);
    }
    if (summary.active_subagent_handle && typeof summary.active_subagent_handle === "object") {
      nextSummary.active_subagent_handle = patchTaskActivityHandle(summary.active_subagent_handle, patch);
    }
    return { ...message, runtime_summary: nextSummary };
  }

  function patchSubagentRuntime(patch: {
    taskRunId: number;
    stepId: string;
    status?: string;
    availableActions?: string[];
    controlState?: string | null;
    terminal?: boolean;
    note?: string | null;
    label?: string;
    detail?: string | null;
    dispatchKind?: string | null;
    source?: string | null;
    timestamp?: string | null;
  }) {
    setChatCards((current) =>
      current.map((card) => {
        if (card.kind !== "consult_call") return card;
        if (card.run_id !== patch.taskRunId || card.consult_step_id !== patch.stepId) return card;
        return {
          ...card,
          status: patch.status ?? card.status,
          available_actions: patch.availableActions ?? card.available_actions,
          error: patch.note ?? card.error,
        };
      }),
    );
    setChatProcesses((current) => upsertSubagentProcessEntry(current, patch));
    setTaskActivitiesById((current) => {
      const activity = current[patch.taskRunId];
      if (!activity) return current;
      return { ...current, [patch.taskRunId]: patchTaskActivityProjection(activity, patch) };
    });
    setMessages((current) => current.map((message) => patchMessageRuntimeSummary(message, patch)));
  }

  function isTerminalHandleStatus(status: string | undefined) {
    const value = (status || "").trim().toLowerCase();
    return value === "completed" || value === "failed" || value === "cancelled" || value === "closed";
  }

  function patchSubagentRuntimeFromCard(card: ChatCardItem) {
    if (card.kind !== "consult_call") return;
    if (typeof card.run_id !== "number" || typeof card.consult_step_id !== "string") return;
    const detailParts = [
      card.response_preview || "",
      !card.response_preview ? (card.status ?? "") : "",
      card.available_actions && card.available_actions.length > 0 ? `actions: ${card.available_actions.join(", ")}` : "",
    ].filter(Boolean);
    const summaryText = typeof card.summary === "string" ? card.summary.trim() : "";
    patchSubagentRuntime({
      taskRunId: card.run_id,
      stepId: card.consult_step_id,
      status: card.status,
      availableActions: card.available_actions,
      controlState: card.status ?? null,
      terminal: isTerminalHandleStatus(card.status),
      note: card.error ?? card.response_preview ?? null,
      label: `${card.target_agent || "subagent"} (consult)`,
      detail: summaryText || detailParts.join(" | ") || card.question_preview || card.error || null,
      dispatchKind: "consult",
      source: card.source ?? "runtime_card",
      timestamp: card.created_at,
    });
  }

  function patchTaskActivityProjectionFromTaskRun(
    activity: TaskActivityProjection,
    taskRun: TaskRunSummary | TaskRunDetail,
  ): TaskActivityProjection {
    if (activity.task_run_id !== taskRun.id) return activity;
    const activeHandle = readActiveSubagentHandleFromTaskRun(taskRun);
    const nextBackground = activity.background && typeof activity.background === "object"
      ? { ...activity.background }
      : {};
    if (activeHandle) {
      nextBackground["active_subagent_handle"] = activeHandle;
      if (String(activeHandle["dispatch_kind"] || "").trim() === "consult") {
        nextBackground["active_consult_handle"] = activeHandle;
      }
    }
    if (taskRun.subagent_handles_summary !== undefined) {
      nextBackground["subagent_handles_summary"] = taskRun.subagent_handles_summary;
    }
    return {
      ...activity,
      status: taskRun.status || activity.status,
      title: taskRun.title || activity.title,
      run_kind: taskRun.run_kind || activity.run_kind,
      summary: taskRun.summary ?? activity.summary,
      updated_at: taskRun.updated_at ?? activity.updated_at,
      background: nextBackground,
    };
  }

  function patchMessageRuntimeSummaryFromTaskRun(
    message: MessageItem,
    taskRun: TaskRunSummary | TaskRunDetail,
  ): MessageItem {
    const summary = message.runtime_summary;
    if (!summary || summary.task_run_id !== taskRun.id) return message;
    return {
      ...message,
      runtime_summary: {
        ...summary,
        status: taskRun.status ?? summary.status,
        run_kind: taskRun.run_kind ?? summary.run_kind,
        continuation_state_summary: taskRun.continuation_state_summary ?? summary.continuation_state_summary,
        active_subagent_handle: readActiveSubagentHandleFromTaskRun(taskRun) ?? summary.active_subagent_handle,
        active_consult_handle: readActiveConsultHandleFromTaskRun(taskRun) ?? summary.active_consult_handle,
        subagent_handles_summary: taskRun.subagent_handles_summary ?? summary.subagent_handles_summary,
      },
    };
  }

  function readActiveSubagentHandleFromTaskRun(taskRun: TaskRunSummary | TaskRunDetail) {
    const checkpoint = taskRun.checkpoint_snapshot && typeof taskRun.checkpoint_snapshot === "object"
      ? taskRun.checkpoint_snapshot
      : null;
    if (!checkpoint) return null;
    const handles = checkpoint.subagent_handles && typeof checkpoint.subagent_handles === "object"
      ? checkpoint.subagent_handles as Record<string, unknown>
      : null;
    const lifecycle = checkpoint.subagent_lifecycle && typeof checkpoint.subagent_lifecycle === "object"
      ? checkpoint.subagent_lifecycle as Record<string, unknown>
      : null;
    const latestStep = checkpoint.latest_subagent_step && typeof checkpoint.latest_subagent_step === "object"
      ? checkpoint.latest_subagent_step as Record<string, unknown>
      : null;
    const handleEntries = Array.isArray(handles?.["entries"])
      ? handles?.["entries"] as Record<string, unknown>[]
      : [];
    const lifecycleEntries = Array.isArray(lifecycle?.["subagents"])
      ? lifecycle?.["subagents"] as Record<string, unknown>[]
      : [];
    const preferredStepId = typeof latestStep?.["step_id"] === "string" ? latestStep["step_id"] : "";
    const lifecycleByStepId = new Map<string, Record<string, unknown>>();
    for (const entry of lifecycleEntries) {
      const stepId = typeof entry?.["step_id"] === "string" ? entry["step_id"] : "";
      if (stepId) lifecycleByStepId.set(stepId, entry);
    }
    let candidate =
      (preferredStepId
        ? handleEntries.find((entry) => typeof entry?.["step_id"] === "string" && entry["step_id"] === preferredStepId)
        : null)
      ?? handleEntries.find((entry) => !entry?.["closed"]);
    if (!candidate || typeof candidate !== "object") return null;
    const stepId = typeof candidate["step_id"] === "string" ? candidate["step_id"] : "";
    const lifecycleEntry = lifecycleByStepId.get(stepId) ?? {};
    const projected: Record<string, unknown> = {
      step_id: candidate["step_id"],
      agent_name: candidate["agent_name"],
      agent_type: candidate["agent_type"],
      dispatch_kind: candidate["dispatch_kind"],
      status: candidate["status"],
      control_state: candidate["control_state"],
      available_actions: candidate["available_actions"],
      dependency_step_id: candidate["dependency_step_id"],
      source:
        lifecycleEntry["source"]
        ?? latestStep?.["source"]
        ?? null,
      requested_name: lifecycleEntry["requested_name"] ?? null,
      closed: candidate["closed"],
      terminal: candidate["terminal"],
      summary_text: typeof candidate["summary_text"] === "string" ? candidate["summary_text"] : null,
      response_preview:
        stepId && preferredStepId && stepId === preferredStepId
          ? latestStep?.["response_preview"] ?? null
          : null,
    };
    return Object.fromEntries(Object.entries(projected).filter(([, value]) => value !== null && value !== undefined));
  }

  function readActiveConsultHandleFromTaskRun(taskRun: TaskRunSummary | TaskRunDetail) {
    const handle = readActiveSubagentHandleFromTaskRun(taskRun);
    if (!handle) return null;
    return String(handle["dispatch_kind"] || "").trim() === "consult" ? handle : null;
  }

  function shouldRefreshProcessTreeAfterTaskRunUpdate(taskRun: TaskRunSummary | TaskRunDetail) {
    if (taskRunTerminalState(taskRun)) return false;
    if (readActiveSubagentHandleFromTaskRun(taskRun)) return false;
    return true;
  }

  async function loadOptionalTaskRuns(chatId: number) {
    try {
      return await api.getTaskRuns(chatId);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to load task runs";
      pushEvent(`Task run history unavailable: ${message}`, "warning");
      return [];
    }
  }

  function shouldLoadTaskActivity(run: TaskRunSummary) {
    const clientTurnId = (run.client_turn_id || "").trim().toLowerCase();
    const runKind = (run.run_kind || "").trim().toLowerCase();
    return (
      (run.status || "").toLowerCase() === "running" ||
      Number(run.pending_approval_count || 0) > 0 ||
      clientTurnId.startsWith("delegate-") ||
      runKind.includes("pipeline") ||
      runKind.includes("orchestration")
    );
  }

  function shouldPollTaskActivity(run: TaskRunSummary) {
    return shouldLoadTaskActivity(run) && !taskRunTerminalState(run);
  }

  function mergeTaskActivities(entries: TaskActivityProjection[]) {
    if (entries.length === 0) return;
    setTaskActivitiesById((current) => ({
      ...current,
      ...Object.fromEntries(entries.map((entry) => [entry.task_run_id, entry])),
    }));
  }

  function mergeTaskTimelines(entries: ChatTimelineProjection[]) {
    if (entries.length === 0) return;
    setTaskTimelinesById((current) => ({
      ...current,
      ...Object.fromEntries(
        entries
          .filter((entry) => typeof entry.task_run_id === "number")
          .map((entry) => [entry.task_run_id as number, entry]),
      ),
    }));
  }

  async function loadOptionalTaskActivities(rows: TaskRunSummary[], options: { silent?: boolean } = {}) {
    const inlineRows = rows.filter(shouldLoadTaskActivity);
    const entries = await Promise.all(
      inlineRows.map(async (run) => {
        try {
          return await api.getTaskRunActivity(run.id);
        } catch (nextError) {
          if (!options.silent) {
            const message = nextError instanceof Error ? nextError.message : "Failed to load task activity";
            pushEvent(`Task activity unavailable: ${message}`, "warning");
          }
          return null;
        }
      }),
    );
    return entries.filter((entry): entry is TaskActivityProjection => entry !== null);
  }

  async function loadOptionalTaskTimelines(rows: TaskRunSummary[], options: { silent?: boolean } = {}) {
    const inlineRows = rows.filter(shouldLoadTaskActivity);
    const entries = await Promise.all(
      inlineRows.map(async (run) => {
        try {
          return await api.getTaskRunTimeline(run.id);
        } catch (nextError) {
          if (!options.silent) {
            const message = nextError instanceof Error ? nextError.message : "Failed to load task timeline";
            pushEvent(`Task timeline unavailable: ${message}`, "warning");
          }
          return null;
        }
      }),
    );
    return entries.filter((entry): entry is ChatTimelineProjection => entry !== null);
  }

  async function loadOptionalRuntimeCards(chatId: number) {
    try {
      return await api.getRuntimeCards(chatId);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to load runtime cards";
      pushEvent(`Runtime card history unavailable: ${message}`, "warning");
      return [];
    }
  }

  async function loadOptionalChatProcesses(chatId: number, options: { silent?: boolean } = {}) {
    try {
      return await api.getChatProcesses(chatId);
    } catch (nextError) {
      if (!options.silent) {
        const message = nextError instanceof Error ? nextError.message : "Failed to load processes";
        pushEvent(`Process list unavailable: ${message}`, "warning");
      }
      return null;
    }
  }

  function scheduleChatProcessRefresh(chatId: number | null | undefined) {
    if (!chatId) return;
    const now = Date.now();
    const lastRefreshedAt = lastProcessRefreshByChatRef.current[chatId] ?? 0;
    if (now - lastRefreshedAt < 350) {
      return;
    }
    if (processRefreshTimerRef.current !== null) {
      window.clearTimeout(processRefreshTimerRef.current);
    }
    processRefreshTimerRef.current = window.setTimeout(() => {
      processRefreshTimerRef.current = null;
      void loadOptionalChatProcesses(chatId, { silent: true }).then((processRows) => {
        lastProcessRefreshByChatRef.current[chatId] = Date.now();
        if (isCurrentChatRequest(chatId)) setChatProcesses(processRows);
      });
    }, 150);
  }

  useEffect(() => {
    if (!error) return undefined;

    const timeoutId = window.setTimeout(() => setError(""), ERROR_AUTO_DISMISS_MS);
    return () => window.clearTimeout(timeoutId);
  }, [error]);

  useEffect(() => {
    if (!bootstrapped || !selectedChatId) return undefined;
    const activeRuns = taskRuns.filter(shouldPollTaskActivity);
    if (activeRuns.length === 0) return undefined;

    let cancelled = false;
    let inFlight = false;

    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const [activityEntries, timelineEntries] = await Promise.all([
          loadOptionalTaskActivities(activeRuns, { silent: true }),
          loadOptionalTaskTimelines(activeRuns, { silent: true }),
        ]);
        if (!cancelled && isCurrentChatRequest(selectedChatId)) {
          mergeTaskActivities(activityEntries);
          mergeTaskTimelines(timelineEntries);
        }
      } finally {
        inFlight = false;
      }
    };

    const intervalId = window.setInterval(() => {
      void poll();
    }, TASK_ACTIVITY_POLL_MS);
    void poll();

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [bootstrapped, selectedChatId, taskRuns]);

  function nextTempMessageId() {
    const nextId = tempMessageIdRef.current;
    tempMessageIdRef.current -= 1;
    return nextId;
  }

  function commitMessages(updater: MessageItem[] | ((current: MessageItem[]) => MessageItem[])) {
    setMessages(updater);
  }

  function commitOptimisticMessages(
    updater: MessageItem[] | ((current: MessageItem[]) => MessageItem[]),
  ) {
    setOptimisticMessages((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      return next.map(dedupeMessageStreamSteps);
    });
  }

  function commitChats(updater: ChatSummary[] | ((current: ChatSummary[]) => ChatSummary[])) {
    setChats((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      chatsRef.current = next;
      return next;
    });
  }

  function commitProjects(updater: ProjectSummary[] | ((current: ProjectSummary[]) => ProjectSummary[])) {
    setProjects((current) => {
      const next = typeof updater === "function" ? updater(current) : updater;
      projectsRef.current = next;
      return next;
    });
  }

  function mergeProjectBrowserBatch(
    current: ProjectBrowserIndex | null,
    batch: ProjectBrowserStreamBatch,
  ): ProjectBrowserIndex {
    const workspacePath = batch.workspace_path || current?.workspace_path || "";
    const fileMap = new Map((current?.files ?? []).map((item) => [item.path, item]));
    const artifactMap = new Map((current?.artifacts ?? []).map((item) => [item.path, item]));
    batch.files.forEach((item) => fileMap.set(item.path, item));
    batch.artifacts.forEach((item) => artifactMap.set(item.path, item));
    return {
      workspace_path: workspacePath,
      files: Array.from(fileMap.values()).sort((left, right) => left.path.localeCompare(right.path)),
      artifacts: Array.from(artifactMap.values()).sort((left, right) =>
        left.type === right.type ? left.path.localeCompare(right.path) : left.type.localeCompare(right.type),
      ),
      truncated: Boolean(current?.truncated || batch.truncated),
    };
  }

  function applyChatSelection(nextChatId: number | null, nextProjectId: number | null) {
    const previousChatId = selectedChatIdRef.current;
    const previousProjectId = selectedProjectIdRef.current;
    debugConsole("info", "applyChatSelection", {
      previousChatId,
      previousProjectId,
      nextChatId,
      nextProjectId,
      lastChatStorage: readLastChatId(),
    });
    if (previousChatId !== nextChatId) {
      optimisticScopeRef.current = optimisticScopeKey(nextChatId);
      setOptimisticMessages(readOptimisticMessages(nextChatId));
      setMessages([]);
      setChatCards([]);
      setChatEvents([]);
    }
    if (previousProjectId !== nextProjectId) {
      setProjectBrowserIndex(null);
    }
    selectedChatIdRef.current = nextChatId;
    selectedProjectIdRef.current = nextProjectId;
    writeLastChatId(nextChatId);
    setSelectedChatId(nextChatId);
    setSelectedProjectId(nextProjectId);
  }

  function toggleConfigTab() {
    setNotice("");
    setActiveTab((currentTab) => (currentTab === "config" ? "chat" : "config"));
  }

  async function ensureSelfBootstrapProject() {
    const project = await api.getOrCreateSelfBootstrapProject();
    commitProjects((current) => upsertProject(current, project));
    migrateOptimisticMessages(selectedChatIdRef.current, project.default_chatroom_id);
    optimisticScopeRef.current = optimisticScopeKey(project.default_chatroom_id);
    applyChatSelection(project.default_chatroom_id, project.id);
    setActiveTab("chat");
    return project;
  }

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );

  const selectedStandaloneChat = useMemo(
    () => chats.find((chat) => chat.id === selectedChatId) ?? null,
    [chats, selectedChatId],
  );

  const activeChat = useMemo<ChatSummary | null>(() => {
    if (selectedProject && selectedProject.default_chatroom_id === selectedChatId) {
      return {
        id: selectedProject.default_chatroom_id,
        title: selectedProject.name,
        session_type: "project-bound",
        is_visible_in_chat_list: false,
        project_id: selectedProject.id,
      };
    }
    return selectedStandaloneChat;
  }, [selectedProject, selectedStandaloneChat, selectedChatId]);

  useEffect(() => {
    bootstrappedRef.current = bootstrapped;
  }, [bootstrapped]);

  useEffect(() => {
    selectedChatIdRef.current = selectedChatId;
  }, [selectedChatId]);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  useEffect(() => {
    projectsRef.current = projects;
  }, [projects]);

  useEffect(() => {
    activeChatRef.current = activeChat;
  }, [activeChat]);

  useEffect(() => {
    if (!bootstrapped) return;
    optimisticScopeRef.current = optimisticScopeKey(selectedChatId);
    setOptimisticMessages(readOptimisticMessages(selectedChatId));
  }, [bootstrapped, selectedChatId]);

  function repairUnknownChatSelection(staleChatId: number | null, source: string) {
    if (!staleChatId) return false;
    if (isKnownChatId(staleChatId, projectsRef.current, chatsRef.current)) {
      return true;
    }

    const fallbackSelection = resolveRestoredSelection(projectsRef.current, chatsRef.current, null);
    const nextChatId = fallbackSelection.chatId ?? null;
    const nextProjectId = fallbackSelection.projectId ?? null;

    debugConsole("warn", "repairUnknownChatSelection", {
      source,
      staleChatId,
      fallbackChatId: nextChatId,
      fallbackProjectId: nextProjectId,
      knownProjectChatIds: projectsRef.current.map((project) => project.default_chatroom_id),
      knownStandaloneChatIds: chatsRef.current.map((chat) => chat.id),
    });
    applyChatSelection(nextChatId, nextProjectId);

    if (joinedRoomRef.current === staleChatId) {
      joinedRoomRef.current = null;
    }
    if (nextChatId === null) {
      setMessages([]);
      setChatCards([]);
    }

    pushEvent(`Recovered stale chat #${staleChatId} from ${source}`, "warning");
    return false;
  }

  useEffect(() => {
    if (!bootstrapped) return;
    const scopeChatId = parseOptimisticScopeChatId(optimisticScopeRef.current);
    const timer = window.setTimeout(() => {
      writeOptimisticMessages(scopeChatId, optimisticMessages);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [bootstrapped, optimisticMessages]);

  async function loadBootstrapData() {
    try {
      setError("");
      const preferredChatId = readLastChatId();
      const [agentRows, configData, chatRows, ruleRows] = await Promise.all([
        api.getAgents(),
        api.getConfig(),
        api.getChats(),
        api.getToolAuthorizationRules(),
      ]);
      const selfProject = await api.getOrCreateSelfBootstrapProject();
      const projectRows = await api.getProjects();
      const restoredSelection = resolveRestoredSelection(projectRows, chatRows, preferredChatId);
      const nextSelectedChatId = restoredSelection.chatId ?? selfProject.default_chatroom_id;
      const nextSelectedProjectId = restoredSelection.projectId ?? selfProject.id;

      debugConsole("info", "bootstrapSelection", {
        preferredChatId,
        projectDefaultChatIds: projectRows.map((project) => project.default_chatroom_id),
        standaloneChatIds: chatRows.map((chat) => chat.id),
        resolvedChatId: nextSelectedChatId,
        resolvedProjectId: nextSelectedProjectId,
      });

      if (preferredChatId !== nextSelectedChatId) {
        writeLastChatId(nextSelectedChatId);
      }

      commitChats(chatRows);
      commitProjects(projectRows);
      setAgents(agentRows);
      setConfig(configData);
      setAuthorizationRules(ruleRows);
      applyChatSelection(nextSelectedChatId, nextSelectedProjectId);
      setActiveTab("chat");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load app data");
      pushEvent("Failed to load bootstrap data", "error");
    } finally {
      setBootstrapped(true);
    }
  }

  useEffect(() => {
    void loadBootstrapData();
  }, []);

  useEffect(() => {
    writeLastChatId(selectedChatId);
  }, [selectedChatId]);

  useEffect(() => {
    if (activeTab !== "chat") {
      setSidebarDrawerOpen(false);
      setActivityDrawerOpen(false);
    }
  }, [activeTab]);

  useEffect(() => {
    if (!bootstrapped) return;
    if (selectedChatId !== null && !activeChat) {
      repairUnknownChatSelection(selectedChatId, "selection sync");
      return;
    }
  }, [activeChat, bootstrapped, chats, projects, selectedChatId]);

  useEffect(() => {
    if (!bootstrapped || !selectedProject) {
      setProjectBrowserIndex(null);
      return undefined;
    }

    const controller = new AbortController();
    setProjectBrowserIndex({
      workspace_path: selectedProject.workspace_path || "",
      files: [],
      artifacts: [],
      truncated: false,
    });

    api.streamProjectBrowser(
      selectedProject.id,
      (batch) => {
        if (controller.signal.aborted) return;
        setProjectBrowserIndex((current) => mergeProjectBrowserBatch(current, batch));
      },
      controller.signal,
    ).catch((nextError) => {
      if (controller.signal.aborted) return;
      const message = nextError instanceof Error ? nextError.message : "Failed to scan workspace";
      pushEvent(`Project browser scan unavailable: ${message}`, "warning");
    });

    return () => controller.abort();
  }, [bootstrapped, selectedProject]);

  useEffect(() => {
    if (!bootstrapped) return;
    if (!selectedChatId) {
      setMessages([]);
      optimisticScopeRef.current = optimisticScopeKey(null);
      setOptimisticMessages(readOptimisticMessages(null));
      setChatCards([]);
      setTaskRuns([]);
      setChatProcesses(null);
      setProjectBrowserIndex(null);
      setLiveTaskRunDetailsById({});
      setTaskActivitiesById({});
      setTaskTimelinesById({});
      return;
    }
    if (!activeChat) return;
    const activeChatId = selectedChatId;

    let cancelled = false;

    async function loadMessages() {
      try {
        setLoadingMessages(true);
        setError("");
        setMessages([]);
        setChatCards([]);
        setChatProcesses(null);
        setChatEvents([]);
        setLiveTaskRunDetailsById({});
        setTaskActivitiesById({});
        setTaskTimelinesById({});
        const [rows, runtimeRows, taskRunRows, processRows] = await Promise.all([
          api.getMessages(activeChatId),
          loadOptionalRuntimeCards(activeChatId),
          loadOptionalTaskRuns(activeChatId),
          loadOptionalChatProcesses(activeChatId),
        ]);
        if (!cancelled && isCurrentChatRequest(activeChatId)) {
          const nextCards = runtimeRows
            .map((payload) => buildCard(payload))
            .filter((card): card is ChatCardItem => card !== null);
          setMessages(rows);
          setChatCards(nextCards);
          setTaskRuns(taskRunRows);
          setChatProcesses(processRows);
          setLiveTaskRunDetailsById({});
          void loadOptionalTaskActivities(taskRunRows).then((entries) => {
            if (cancelled || !isCurrentChatRequest(activeChatId)) return;
            setTaskActivitiesById(Object.fromEntries(entries.map((entry) => [entry.task_run_id, entry])));
          });
          void loadOptionalTaskTimelines(taskRunRows).then((entries) => {
            if (cancelled || !isCurrentChatRequest(activeChatId)) return;
            setTaskTimelinesById(
              Object.fromEntries(
                entries
                  .filter((entry) => typeof entry.task_run_id === "number")
                  .map((entry) => [entry.task_run_id as number, entry]),
              ),
            );
          });
          commitOptimisticMessages((current) => reconcileOptimisticMessagesWithServer(current, rows, nextCards, taskRunRows));
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Failed to load messages");
        }
      } finally {
        if (!cancelled) {
          setLoadingMessages(false);
        }
      }
    }

    void loadMessages();

    return () => {
      cancelled = true;
    };
  }, [activeChat, bootstrapped, selectedChatId]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let cancelled = false;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (cancelled) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws`;
      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;
      setConnectionState("connecting");

      socket.onopen = () => {
        if (cancelled) return;
        setConnectionState("connected");
        pushEvent("Realtime connection established", "success");

        const roomId = selectedChatIdRef.current;
        debugConsole("info", "socketOpen", {
          roomId,
          bootstrapped: bootstrappedRef.current,
          selectedChatId: selectedChatIdRef.current,
          selectedProjectId: selectedProjectIdRef.current,
        });
        if (bootstrappedRef.current && roomId) {
          if (!repairUnknownChatSelection(roomId, "websocket open")) {
            return;
          }
          socket.send(JSON.stringify({ type: "join", chatroom_id: roomId }));
          joinedRoomRef.current = roomId;
          void refreshMessages(false, roomId);
        }
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;

          if (data.type === "chat_message" && typeof data.id === "number") {
            const incoming: MessageItem = {
              id: data.id,
              content: typeof data.content === "string" ? data.content : "",
              agent_name: typeof data.agent_name === "string" ? data.agent_name : null,
              message_type: typeof data.message_type === "string" ? data.message_type : "text",
              created_at: typeof data.created_at === "string" ? data.created_at : new Date().toISOString(),
              client_turn_id:
                typeof data.client_turn_id === "string" ? data.client_turn_id : undefined,
            };
            setMessages((current) => {
              const streamingId = streamingAssistantIdRef.current;
              if (streamingId !== null && incoming.agent_name) {
                const next = updateMessage(current, streamingId, (message) => ({
                  ...finalizeStreamingTrace(
                    {
                      ...message,
                      ...incoming,
                      isStreaming: false,
                    },
                    "done",
                  ),
                }));
                if (next !== current) {
                  streamingAssistantIdRef.current = incoming.id;
                  return next;
                }
              }
              return mergeMessages(current, [incoming]);
            });
            commitOptimisticMessages((current) => {
              if (!incoming.agent_name) {
                return current.filter(
                  (item) =>
                    !(
                      item.optimisticKind === "user" &&
                      (
                        (incoming.client_turn_id && item.client_turn_id === incoming.client_turn_id) ||
                        item.id === incoming.id
                      )
                    ),
                );
              }

              let matched = false;
              const next = current.map((item) => {
                const isMatchingPlaceholder =
                  item.optimisticKind === "assistant_placeholder" &&
                  (
                    (incoming.client_turn_id && item.client_turn_id === incoming.client_turn_id) ||
                    item.id === streamingAssistantIdRef.current
                  );
                if (!isMatchingPlaceholder) {
                  return item;
                }
                matched = true;
                return finalizeRecoveredPlaceholder(item, incoming, "Recovered from websocket update.");
              });

              if (matched) {
                return next;
              }

              return current.filter(
                (item) =>
                  !(
                    item.isStreaming &&
                    (item.agent_name || null) === (incoming.agent_name || null)
                  ),
              );
            });
            if (incoming.agent_name) {
              pushEvent(`Incoming reply from ${incoming.agent_name || getAgentDisplayName(primaryAgent)}`, "success");
            }
            return;
          }

          if (data.type === "runtime_card" && data.card && typeof data.card === "object") {
            const card = buildCard(data.card as Record<string, unknown>);
            if (card) {
              pushCard(card);
              patchSubagentRuntimeFromCard(card);
              scheduleChatProcessRefresh(selectedChatIdRef.current);
            }
            return;
          }

          if (data.type === "task_run_update" && data.payload && typeof data.payload === "object") {
            const payload = data.payload as Record<string, unknown>;
            const entry = payload.entry as TaskRunSummary | undefined;
            const detail = payload.detail as TaskRunDetail | undefined;
            if (entry && typeof entry.id === "number") {
              const nextTaskRun = detail && typeof detail.id === "number" ? detail : entry;
              setTaskRuns((current) => mergeTaskRuns(current, [entry]));
              setChatProcesses((current) => reconcileTaskProcessSubagentEntry(upsertTaskProcessEntry(current, nextTaskRun), nextTaskRun));
              setTaskActivitiesById((current) => {
                const activity = current[entry.id];
                if (!activity) return current;
                return { ...current, [entry.id]: patchTaskActivityProjectionFromTaskRun(activity, nextTaskRun) };
              });
              setMessages((current) => current.map((message) => patchMessageRuntimeSummaryFromTaskRun(message, nextTaskRun)));
              if (shouldRefreshProcessTreeAfterTaskRunUpdate(nextTaskRun)) {
                scheduleChatProcessRefresh(entry.chatroom_id);
              }
              void api.getTaskRunActivity(entry.id)
                .then((activity) => {
                  if (!isCurrentChatRequest(entry.chatroom_id)) return;
                  setTaskActivitiesById((current) => ({ ...current, [activity.task_run_id]: activity }));
                })
                .catch((nextError) => {
                  const message = nextError instanceof Error ? nextError.message : "Failed to load task activity";
                  pushEvent(`Task activity unavailable: ${message}`, "warning");
                });
              void api.getTaskRunTimeline(entry.id)
                .then((timeline) => {
                  if (!isCurrentChatRequest(entry.chatroom_id)) return;
                  if (typeof timeline.task_run_id !== "number") return;
                  setTaskTimelinesById((current) => ({ ...current, [timeline.task_run_id as number]: timeline }));
                })
                .catch((nextError) => {
                  const message = nextError instanceof Error ? nextError.message : "Failed to load task timeline";
                  pushEvent(`Task timeline unavailable: ${message}`, "warning");
                });
              if (detail && typeof detail.id === "number") {
                setLiveTaskRunDetailsById((current) => ({ ...current, [detail.id]: detail }));
              } else if (taskRunTerminalState(entry) || Number(entry.pending_approval_count || 0) === 0) {
                setLiveTaskRunDetailsById((current) => {
                  if (!(entry.id in current)) return current;
                  const next = { ...current };
                  delete next[entry.id];
                  return next;
                });
              }
              commitOptimisticMessages((current) =>
                reconcileOptimisticMessagesWithServer(current, [], [], [detail && typeof detail.id === "number" ? detail : entry]),
              );
            }
            return;
          }

          if (data.type === "chat_processes_changed") {
            const chatroomId = typeof data.chatroom_id === "number" ? data.chatroom_id : selectedChatIdRef.current;
            const reason = typeof data.reason === "string" ? data.reason : "";
            if (reason === "runtime_card" || reason === "task_run_update") {
              return;
            }
            if (isCurrentChatRequest(chatroomId)) {
              scheduleChatProcessRefresh(chatroomId);
            }
            return;
          }

          if (typeof data.type === "string" && data.type.startsWith("pipeline_")) {
            const pipelineType = data.type.slice("pipeline_".length);
            const card = buildCard({
              ...data,
              type: pipelineType,
              source: "pipeline",
            });
            if (card && selectedProjectIdRef.current !== null) {
              pushCard(card);
            }

            switch (pipelineType) {
              case "agent_output":
                if (selectedProjectIdRef.current !== null && typeof data.content === "string") {
                  const tempId = nextTempMessageId();
                  const createdAt =
                    typeof data.created_at === "string" ? data.created_at : new Date().toISOString();
                  const agentName = typeof data.agent === "string" ? data.agent : "agent";
                  setMessages((current) =>
                    mergeMessages(current, [
                      {
                        id: tempId,
                        content: data.content,
                        agent_name: agentName,
                        message_type: "text",
                        created_at: createdAt,
                      },
                    ]),
                  );
                }
                break;
              case "pipeline_started":
                pushEvent("Pipeline started", "info");
                break;
              case "pipeline_paused":
                pushEvent("Pipeline paused", "warning");
                break;
              case "pipeline_failed":
                pushEvent(
                  `Pipeline failed${typeof data.failed_stage === "string" ? ` at ${data.failed_stage}` : ""}`,
                  "error",
                );
                break;
              case "pipeline_blocked":
                pushEvent(
                  `Pipeline waiting at ${typeof data.stage === "string" ? data.stage : "manual gate"}`,
                  "warning",
                );
                break;
              case "pipeline_completed":
                pushEvent("Pipeline completed", "success");
                break;
              case "stage_started":
                if (typeof data.display_name === "string") {
                  pushEvent(`Stage started: ${data.display_name}`, "info");
                }
                break;
              case "stage_completed":
                if (typeof data.stage === "string") {
                  pushEvent(`Stage completed: ${data.stage}`, "success");
                }
                break;
              case "gate_blocked":
                if (typeof data.display_name === "string") {
                  pushEvent(`Approval needed: ${data.display_name}`, "warning");
                }
                break;
              case "gate_approved":
                pushEvent("Manual gate approved", "success");
                break;
              case "gate_rejected":
                pushEvent("Manual gate rejected", "warning");
                break;
              case "boss_instruction":
                if (typeof data.agent === "string") {
                  pushEvent(`Boss instruction sent to ${data.agent}`, "info");
                }
                break;
              case "agent_message":
                if (typeof data.from_agent === "string" && typeof data.to_agent === "string") {
                  pushEvent(`${data.from_agent} -> ${data.to_agent}`, "info");
                }
                break;
              default:
                break;
            }
          }
        } catch {
          pushEvent("Realtime payload parse error", "warning");
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        setConnectionState("disconnected");
        joinedRoomRef.current = null;
        reconnectTimer = window.setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        if (cancelled) return;
        setConnectionState("disconnected");
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      if (processRefreshTimerRef.current !== null) {
        window.clearTimeout(processRefreshTimerRef.current);
        processRefreshTimerRef.current = null;
      }
      joinedRoomRef.current = null;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!bootstrapped) return;

    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;

    if (joinedRoomRef.current && joinedRoomRef.current !== selectedChatId) {
      socket.send(JSON.stringify({ type: "leave", chatroom_id: joinedRoomRef.current }));
    }

    if (selectedChatId) {
      if (!repairUnknownChatSelection(selectedChatId, "room join")) {
        return;
      }
      socket.send(JSON.stringify({ type: "join", chatroom_id: selectedChatId }));
      joinedRoomRef.current = selectedChatId;
    } else {
      joinedRoomRef.current = null;
    }
  }, [bootstrapped, selectedChatId]);

  async function refreshMessages(showSpinner = true, chatId = selectedChatId) {
    if (!chatId) return;
    const isCurrentSelection = chatId === selectedChatIdRef.current;
    debugConsole("info", "refreshMessages", {
      chatId,
      showSpinner,
      isCurrentSelection,
      selectedChatId: selectedChatIdRef.current,
      selectedProjectId: selectedProjectIdRef.current,
      knownProjectChatIds: projectsRef.current.map((project) => project.default_chatroom_id),
      knownStandaloneChatIds: chatsRef.current.map((chat) => chat.id),
    });
    if (!isKnownChatId(chatId, projectsRef.current, chatsRef.current)) {
      if (isCurrentSelection) {
        repairUnknownChatSelection(chatId, "message refresh");
      }
      return;
    }
    if (isCurrentSelection && !activeChatRef.current) return;
    try {
      if (showSpinner) {
        setRefreshingMessages(true);
      }
      setError("");
      const [rows, runtimeRows, taskRunRows, processRows] = await Promise.all([
        api.getMessages(chatId),
        loadOptionalRuntimeCards(chatId),
        loadOptionalTaskRuns(chatId),
        loadOptionalChatProcesses(chatId),
      ]);
      if (!isCurrentChatRequest(chatId)) return;
      const nextCards = runtimeRows
        .map((payload) => buildCard(payload))
        .filter((card): card is ChatCardItem => card !== null);
      setMessages(rows);
      commitOptimisticMessages((current) => reconcileOptimisticMessagesWithServer(current, rows, nextCards, taskRunRows));
      setChatCards(nextCards);
      setTaskRuns(taskRunRows);
      setChatProcesses(processRows);
      setLiveTaskRunDetailsById({});
      void loadOptionalTaskActivities(taskRunRows).then((entries) => {
        if (!isCurrentChatRequest(chatId)) return;
        setTaskActivitiesById(Object.fromEntries(entries.map((entry) => [entry.task_run_id, entry])));
      });
      void loadOptionalTaskTimelines(taskRunRows).then((entries) => {
        if (!isCurrentChatRequest(chatId)) return;
        setTaskTimelinesById(
          Object.fromEntries(
            entries
              .filter((entry) => typeof entry.task_run_id === "number")
              .map((entry) => [entry.task_run_id as number, entry]),
          ),
        );
      });
      if (showSpinner) {
        pushEvent("Conversation refreshed", "info");
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to refresh messages");
    } finally {
      if (showSpinner) {
        setRefreshingMessages(false);
      }
    }
  }

  async function refreshRuntimeForTaskRun(taskRunId: number) {
    const chatId = selectedChatIdRef.current;
    if (!chatId) return;
    try {
      const [rows, runtimeRows, taskRunRows, processRows, activity, timeline] = await Promise.all([
        api.getMessages(chatId),
        loadOptionalRuntimeCards(chatId),
        loadOptionalTaskRuns(chatId),
        loadOptionalChatProcesses(chatId, { silent: true }),
        api.getTaskRunActivity(taskRunId),
        api.getTaskRunTimeline(taskRunId),
      ]);
      if (!isCurrentChatRequest(chatId)) return;
      const nextCards = runtimeRows
        .map((payload) => buildCard(payload))
        .filter((card): card is ChatCardItem => card !== null);
      setMessages(rows);
      commitOptimisticMessages((current) => reconcileOptimisticMessagesWithServer(current, rows, nextCards, taskRunRows));
      setChatCards(nextCards);
      setTaskRuns(taskRunRows);
      setChatProcesses(processRows);
      setTaskActivitiesById((current) => ({ ...current, [activity.task_run_id]: activity }));
      if (typeof timeline.task_run_id === "number") {
        setTaskTimelinesById((current) => ({ ...current, [timeline.task_run_id as number]: timeline }));
      }
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to refresh runtime";
      pushEvent(`Runtime refresh unavailable: ${message}`, "warning");
    }
  }

  async function handleSendMessage(content: string, options?: { clientTurnId?: string }) {
    let chatId = selectedChatId;
    let streamReceivedEvent = false;
    const userTempId = nextTempMessageId();
    const createdAt = new Date().toISOString();
    const clientTurnId = options?.clientTurnId;
    let activeAgentName = getAgentDisplayName(primaryAgent);
    let streamCompleted = false;
    let assistantDraftContent = "";
    let pendingContentDelta = "";
    let contentFlushTimer: number | null = null;
    let liveLlmSystemPrompt = "";
    let liveLlmPromptMessages = "";
    let liveLlmModel = "";
    let liveLlmTurn: number | undefined;
    let liveLlmTimings: ChatCardLlmTimings = {};
    const liveToolArgs = new Map<string, string>();

    try {
      setSendingMessage(true);
      setLoadingMessages(false);
      setError("");

      commitOptimisticMessages((current) =>
        mergeMessages(current, [
          {
            id: userTempId,
            content,
            message_type: "user",
            created_at: createdAt,
            agent_name: null,
            client_turn_id: clientTurnId,
            optimisticKind: "user",
          },
        ]),
      );
      pushEvent(`Sent: ${content.slice(0, 72)}`, "info");

      if (!chatId) {
        const selfProject = await ensureSelfBootstrapProject();
        chatId = selfProject.default_chatroom_id;
        pushEvent(`Opened self-bootstrap project "${selfProject.name}"`, "success");
      }

      const controller = new AbortController();
      sendAbortRef.current?.abort();
      sendAbortRef.current = controller;

      const response = await api.streamMessage(chatId, content, controller.signal, clientTurnId);
      if (!response.ok || !response.body) {
        let detail = `Request failed: ${response.status}`;
        try {
          const data = (await response.json()) as { detail?: string; error?: string };
          detail = data.detail || data.error || detail;
        } catch {
          // Ignore JSON parse failures for SSE fallback errors.
        }
        throw new Error(detail);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const ensureStreamingAssistantMessage = () => {
        if (streamingAssistantIdRef.current !== null) {
          return streamingAssistantIdRef.current;
        }
        const assistantMessageId = nextTempMessageId();
        streamingAssistantIdRef.current = assistantMessageId;
        commitOptimisticMessages((current) =>
          mergeMessages(current, [
            {
              id: assistantMessageId,
              content: "",
              message_type: "text",
              created_at: new Date().toISOString(),
              agent_name: activeAgentName,
              client_turn_id: clientTurnId,
              isStreaming: true,
            },
          ]),
        );
        return assistantMessageId;
      };

      const readAssistantMessageId = () => ensureStreamingAssistantMessage();

      const flushPendingContent = () => {
        if (!pendingContentDelta) return;
        const delta = pendingContentDelta;
        pendingContentDelta = "";
        assistantDraftContent = `${assistantDraftContent}${delta}`;
        commitOptimisticMessages((current) =>
          updateMessage(current, readAssistantMessageId(), (message) => ({
            ...message,
            agent_name: message.agent_name || activeAgentName,
            content: `${message.content}${delta}`,
          })),
        );
      };

      const scheduleContentFlush = () => {
        if (contentFlushTimer !== null) return;
        contentFlushTimer = window.setTimeout(() => {
          contentFlushTimer = null;
          flushPendingContent();
        }, 48);
      };

      const handleStreamPayload = async (payload: string) => {
        if (!payload.startsWith("data:")) return;

        const raw = payload.slice(5).trim();
        if (!raw) return;

        const data = JSON.parse(raw) as Record<string, unknown>;
        streamReceivedEvent = true;

        if (data.type !== "content") {
          flushPendingContent();
        }

        switch (data.type) {
          case "user_saved":
            if (typeof data.id === "number") {
              commitOptimisticMessages((current) => replaceMessageId(current, userTempId, data.id));
            }
            if (typeof data.task_run_id === "number") {
              void refreshRuntimeForTaskRun(data.task_run_id);
            }
            break;
          case "agent_start":
            if (typeof data.agent_name === "string" && data.agent_name) {
              activeAgentName = data.agent_name;
            }
            liveLlmSystemPrompt = typeof data.system_prompt === "string" ? data.system_prompt : "";
            liveLlmPromptMessages = typeof data.prompt_messages === "string" ? data.prompt_messages : "";
            liveLlmModel = typeof data.model === "string" ? data.model : "";
            liveLlmTurn = typeof data.turn === "number" ? data.turn : undefined;
            liveLlmTimings = {};
            pushEvent(`Agent: ${activeAgentName}`, "info");
            break;
          case "collab_start": {
            const agentNames = readStringArray(data.agents);
            activeAgentName = "pipeline";
            if (agentNames.length > 0) {
              pushEvent(`Agents: ${agentNames.join(", ")}`, "info");
            }
            break;
          }
          case "collab_step": {
            const agentName = typeof data.agent === "string" && data.agent ? data.agent : activeAgentName;
            activeAgentName = agentName;
            const step = typeof data.step === "number" ? data.step : "?";
            const total = typeof data.total === "number" ? data.total : "?";
            pushEvent(buildFieldStatusDetail([
              ["Plan", `${step}/${total}`],
              ["Agent", agentName],
            ]), "info");
            break;
          }
          case "collab_step_done":
            if (typeof data.agent === "string") {
              pushEvent(`Agent: ${data.agent} / Status: completed`, "success");
            }
            break;
          case "collab_skip":
            if (typeof data.agent === "string") {
              const reason = typeof data.reason === "string" ? data.reason : "skipped";
              pushEvent(`${data.agent} skipped: ${reason}`, "warning");
            }
            break;
          case "content": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            const delta = typeof data.delta === "string" ? data.delta : "";
            if (!delta) break;
            pendingContentDelta = `${pendingContentDelta}${delta}`;
            scheduleContentFlush();
            break;
          }
          case "request_sent":
          case "first_chunk":
          case "first_content": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            const elapsedMs = typeof data.elapsed_ms === "number" ? data.elapsed_ms : undefined;
            if (data.type === "request_sent" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, request_sent_ms: elapsedMs };
            }
            if (data.type === "first_chunk" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_chunk_ms: elapsedMs };
            }
            if (data.type === "first_content" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_content_ms: elapsedMs };
            }
            break;
          }
          case "tool_call_delta": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            const toolName = typeof data.tool === "string" && data.tool ? data.tool : "tool";
            const toolCallIndex = typeof data.tool_call_index === "number" ? data.tool_call_index : undefined;
            const toolCallId = typeof data.tool_call_id === "string" ? data.tool_call_id : null;
            const rawToolArgs = typeof data.args === "string" ? data.args : "";
            const elapsedMs = typeof data.elapsed_ms === "number" ? data.elapsed_ms : undefined;
            if (elapsedMs !== undefined && liveLlmTimings.first_tool_call_ms === undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_tool_call_ms: elapsedMs };
            }
            liveToolArgs.set(buildToolWaitKey(activeAgentName, toolCallIndex, toolName), rawToolArgs);
            break;
          }
          case "tool_call_ready":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.elapsed_ms === "number") {
              liveLlmTimings = { ...liveLlmTimings, tool_call_ready_ms: data.elapsed_ms };
            }
            break;
          case "tool_start":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const toolName = data.tool;
              const toolCallIndex = typeof data.tool_call_index === "number" ? data.tool_call_index : undefined;
              const toolCallId = typeof data.tool_call_id === "string" ? data.tool_call_id : null;
              const rawToolArgs = typeof data.args === "string" && data.args.trim() ? data.args.trim() : "";
              liveToolArgs.set(buildToolWaitKey(activeAgentName, toolCallIndex, toolName), rawToolArgs);
              pushEvent(`Tool: ${toolName}`, "warning");
            }
            break;
          case "llm_wait": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            break;
          }
          case "tool_wait":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const toolName = data.tool;
            }
            break;
          case "tool_result":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const toolName = data.tool;
              const toolCallIndex = typeof data.tool_call_index === "number" ? data.tool_call_index : undefined;
              const toolCallId = typeof data.tool_call_id === "string" ? data.tool_call_id : null;
              liveToolArgs.delete(buildToolWaitKey(activeAgentName, toolCallIndex, toolName));
              const rawResult = typeof data.result === "string" ? data.result : "";
              const blockedKind = typeof data.blocked_kind === "string" ? data.blocked_kind.trim().toLowerCase() : "";
              const status = typeof data.status === "string" ? data.status.trim().toLowerCase() : "";
              const internalPause =
                data.blocked === true &&
                (blockedKind === "approval" || blockedKind === "timeout" || status === "approval_blocked" || status === "timeout_waiting");
              const failed =
                internalPause ? false : typeof data.success === "boolean" ? data.success === false : isToolResultFailure(rawResult);
              pushEvent(`Tool: ${toolName} ${failed ? "failed" : "completed"}`, failed ? "error" : "success");
            }
            break;
          case "approval_pending": {
            streamCompleted = true;
            const finalAgentName =
              typeof data.agent_name === "string" && data.agent_name
                ? data.agent_name
                : activeAgentName;
            const pendingTool =
              typeof data.tool === "string" && data.tool ? data.tool : "tool";
            pushEvent(`Approval: ${pendingTool}`, "warning");
            break;
          }
          case "done": {
            streamCompleted = true;
            const savedMessageId = typeof data.message_id === "number" ? data.message_id : null;
            const savedClientTurnId =
              typeof data.client_turn_id === "string" ? data.client_turn_id : clientTurnId;
            const finalAgentName =
              typeof data.agent_name === "string" && data.agent_name
                ? data.agent_name
                : activeAgentName;
            if (savedMessageId !== null) {
              commitMessages((current) =>
                mergeMessages(current, [
                  {
                    id: savedMessageId,
                    content: assistantDraftContent || "(Agent returned empty response)",
                    created_at: new Date().toISOString(),
                    message_type: "text",
                    agent_name: finalAgentName,
                    client_turn_id: savedClientTurnId,
                    isStreaming: false,
                  },
                ]),
              );
              if (streamingAssistantIdRef.current !== null) {
                commitOptimisticMessages((current) =>
                  current.filter((item) => item.id !== streamingAssistantIdRef.current),
                );
              }
              streamingAssistantIdRef.current = null;
            } else {
              flushPendingContent();
              if (streamingAssistantIdRef.current !== null) {
                commitOptimisticMessages((current) =>
                  updateMessage(current, streamingAssistantIdRef.current ?? 0, (message) => ({
                    ...message,
                    agent_name: finalAgentName,
                    isStreaming: false,
                  })),
                );
              }
            }
            pushEvent(`${finalAgentName} replied`, "success");
            if (connectionState !== "connected" || joinedRoomRef.current !== chatId) {
              await refreshMessages(false, chatId);
            }
            break;
          }
          case "error": {
            const message =
              typeof data.error === "string" && data.error
                ? data.error
                : "Streaming failed";
            if (streamingAssistantIdRef.current !== null) {
              commitOptimisticMessages((current) =>
                updateMessage(current, streamingAssistantIdRef.current ?? 0, (item) => ({
                  ...item,
                  content: item.content || `Error: ${message}`,
                  agent_name: activeAgentName,
                  isStreaming: false,
                })),
              );
            }
            pushEvent(`Message stream failed: ${message}`, "error");
            setError(message);
            break;
          }
          default:
            break;
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const chunk of events) {
          const lines = chunk
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
          for (const line of lines) {
            await handleStreamPayload(line);
          }
        }
      }

      const finalChunk = buffer.trim();
      if (finalChunk) {
        const lines = finalChunk
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        for (const line of lines) {
          await handleStreamPayload(line);
        }
      }

      flushPendingContent();

      if (!streamCompleted) {
        if (streamingAssistantIdRef.current !== null) {
          commitOptimisticMessages((current) =>
            updateMessage(current, streamingAssistantIdRef.current ?? 0, (message) => ({
              ...message,
              isStreaming: false,
            })),
          );
        }
        if (connectionState !== "connected" || joinedRoomRef.current !== chatId) {
          await refreshMessages(false, chatId);
        }
      }
    } catch (nextError) {
      if (contentFlushTimer !== null) {
        window.clearTimeout(contentFlushTimer);
        contentFlushTimer = null;
      }
      const message = nextError instanceof Error ? nextError.message : "Failed to send message";
      setError(message);

      if (chatId && !streamReceivedEvent) {
        commitOptimisticMessages((current) => current.filter((item) => item.id >= 0));
        pushEvent("Streaming unavailable, falling back to sync send", "warning");
        try {
          const saved = await api.sendMessage(chatId, content, clientTurnId);
          setMessages((current) => mergeMessages(current, [saved]));
          if (connectionState !== "connected") {
            await refreshMessages(false, chatId);
          }
          return;
        } catch (fallbackError) {
          const fallbackMessage =
            fallbackError instanceof Error ? fallbackError.message : "Failed to send message";
          setError(fallbackMessage);
        }
      } else if (streamingAssistantIdRef.current !== null) {
        commitOptimisticMessages((current) =>
          updateMessage(current, streamingAssistantIdRef.current ?? 0, (messageItem) =>
            finalizeStreamingTrace(
              {
                ...messageItem,
                content: messageItem.content || `Error: ${message}`,
                isStreaming: false,
              },
              "error",
              "Failed",
              message,
            ),
          ),
        );
      }

      pushEvent("Message send failed", "error");
    } finally {
      if (contentFlushTimer !== null) {
        window.clearTimeout(contentFlushTimer);
      }
      sendAbortRef.current = null;
      streamingAssistantIdRef.current = null;
      setSendingMessage(false);
    }
  }

  async function handleCreateProject(payload: {
    repo_url: string;
    name?: string;
    description: string;
    ref?: string;
    agent_names: string[];
  }) {
    try {
      setCreatingProject(true);
      setError("");
      const created = await api.createProjectFromGithub(payload);
      const nextProjects = await api.getProjects();
      commitProjects(nextProjects);
      applyChatSelection(created.default_chatroom_id, created.id);
      setActiveTab("chat");
      setNotice(`Imported "${created.name}" from GitHub and opened its chat room.`);
      pushEvent(`Imported project "${created.name}" from GitHub`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to import project from GitHub");
    } finally {
      setCreatingProject(false);
    }
  }

  async function handleCreateChat() {
    try {
      setError("");
      const project = await ensureSelfBootstrapProject();
      setNotice("");
      pushEvent(`Opened self-bootstrap project "${project.name}"`, "success");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to open self-bootstrap project");
    }
  }

  async function handleCreateProjectChat(projectId: number) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;

    try {
      setError("");
      const created = await api.createProjectSubchat(projectId);
      clearChatLocalCaches(created.id);
      commitChats((current) => [created, ...current.filter((chat) => chat.id !== created.id)]);
      applyChatSelection(created.id, projectId);
      setActiveTab("chat");
      setNotice("");
      pushEvent(`Created sub chat "${created.title}" in "${targetProject.name}"`, "success");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create sub chat");
    }
  }

  async function handleOpenWorkspace() {
    if (!selectedProject) return;

    try {
      setError("");
      await api.openProjectWorkspace(selectedProject.id);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to open workspace");
    }
  }

  async function handleSyncProject() {
    if (!selectedProject || selectedProject.source_type !== "github") return;

    try {
      setSyncingProjectId(selectedProject.id);
      setError("");
      const result = await api.syncProject(selectedProject.id);
      commitProjects((current) =>
        current.map((project) => (project.id === result.project.id ? result.project : project)),
      );
      setNotice(result.summary);
      pushEvent(result.summary, result.updated ? "success" : "info");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to sync project");
      pushEvent(`Sync failed for "${selectedProject.name}"`, "error");
    } finally {
      setSyncingProjectId(null);
    }
  }

  async function handleReorderProjects(draggedProjectId: number, targetProjectId: number) {
    if (draggedProjectId === targetProjectId) return;

    const previousProjects = projects;
    const nextProjects = reorderProjects(projects, draggedProjectId, targetProjectId);
    if (nextProjects === projects) return;

    try {
      setError("");
      commitProjects(nextProjects);
      const persistedProjects = await api.reorderProjects(nextProjects.map((project) => project.id));
      commitProjects(persistedProjects);
    } catch (nextError) {
      commitProjects(previousProjects);
      setError(nextError instanceof Error ? nextError.message : "Failed to reorder projects");
    }
  }

  async function handleCreateProjectFromCurrentChat(payload: {
    name: string;
    description: string;
    agent_names: string[];
  }) {
    if (!activeChat || activeChat.session_type !== "standalone") return;

    try {
      setCreatingProjectFromChat(true);
      setError("");
      const created = await api.createProjectFromChat({
        source_chatroom_id: activeChat.id,
        name: payload.name,
        description: payload.description,
        agent_names: payload.agent_names,
      });
      const nextProjects = await api.getProjects();
      commitProjects(nextProjects);
      applyChatSelection(created.default_chatroom_id, created.id);
      setActiveTab("chat");
      setNotice(`Created project "${created.name}" from "${activeChat.title}".`);
      pushEvent(`Converted chat "${activeChat.title}" into project "${created.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create project from chat");
    } finally {
      setCreatingProjectFromChat(false);
    }
  }

  async function handleRenameChat(chatId: number, nextTitle: string) {
    const targetChat = chats.find((chat) => chat.id === chatId) ?? null;
    if (!targetChat) return;
    if (!nextTitle || nextTitle === targetChat.title) return;

    try {
      setError("");
      const renamed = await api.renameChat(chatId, nextTitle);
      commitChats((current) => current.map((chat) => (chat.id === chatId ? renamed : chat)));
      setNotice(`Renamed chat to "${renamed.title}".`);
      pushEvent(`Chat renamed to "${renamed.title}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to rename chat");
    }
  }

  async function handleRenameProject(projectId: number, nextName: string) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;
    if (!nextName || nextName === targetProject.name) return;

    try {
      setError("");
      const renamed = await api.renameProject(projectId, nextName);
      commitProjects((current) => current.map((project) => (project.id === projectId ? renamed : project)));
      setNotice(`Renamed project to "${renamed.name}".`);
      pushEvent(`Project renamed to "${renamed.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to rename project");
    }
  }

  async function handleDeleteChat(chatId: number) {
    const targetChat = chats.find((chat) => chat.id === chatId) ?? null;
    if (!targetChat) return;

    try {
      setError("");
      await api.deleteChat(chatId);
      clearChatLocalCaches(chatId);

      const remainingChats = chats.filter((chat) => chat.id !== chatId);
      commitChats(remainingChats);

      if (selectedChatId === chatId) {
        const fallbackProject = targetChat.project_id
          ? projects.find((project) => project.id === targetChat.project_id) ?? null
          : null;
        const nextProjectId = fallbackProject?.id ?? null;
        const nextChatId = fallbackProject?.default_chatroom_id ?? remainingChats[0]?.id ?? null;
        applyChatSelection(nextChatId, nextProjectId);
        if (!fallbackProject && remainingChats.length === 0) {
          setMessages([]);
          setChatCards([]);
        }
      }

      setNotice(`Deleted chat "${targetChat.title}".`);
      pushEvent(`Deleted chat "${targetChat.title}"`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to delete chat");
    }
  }

  async function handleDeleteProject(projectId: number) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;
    const projectChatIds = [
      targetProject.default_chatroom_id,
      targetProject.chatroom_id,
      ...chats.filter((chat) => chat.project_id === projectId).map((chat) => chat.id),
    ];

    try {
      setError("");
      await api.deleteProject(projectId);
      clearManyChatLocalCaches(projectChatIds);

      const remainingProjects = projects.filter((project) => project.id !== projectId);
      const remainingChats = chats.filter((chat) => chat.project_id !== projectId);
      commitProjects(remainingProjects);
      commitChats(remainingChats);

      if (selectedProjectId === projectId || selectedChatId === targetProject.default_chatroom_id) {
        const fallbackProject = remainingProjects[0] ?? null;
        const nextProjectId = fallbackProject?.id ?? null;
        const nextChatId = fallbackProject?.default_chatroom_id ?? remainingChats[0]?.id ?? null;
        applyChatSelection(nextChatId, nextProjectId);
        if (!fallbackProject && remainingChats.length === 0) {
          setMessages([]);
          setChatCards([]);
        }
      }

      setNotice(`Deleted project "${targetProject.name}".`);
      pushEvent(`Deleted project "${targetProject.name}"`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to delete project");
    }
  }

  function handleOpenProjectCreator() {
    setError("");
    setActiveTab("projects");
    setSidebarDrawerOpen(false);
    pushEvent("Opened GitHub import flow", "info");
  }

  async function handleSaveGlobal(payload: GlobalConfigPayload) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveGlobalConfig(payload);
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice("Global config saved.");
      pushEvent("Global config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save global config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveOrchestration(payload: { sidecar_agent_types: string[] }) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveOrchestrationConfig(payload);
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice("Orchestration config saved.");
      pushEvent("Orchestration config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save orchestration config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSavePermissions(payload: PermissionsConfigPayload) {
    try {
      setSavingConfig(true);
      setError("");
      await api.savePermissionsConfig(payload);
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice("Permissions config saved.");
      pushEvent("Permissions config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save permissions config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveContext(payload: ContextConfigPayload) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveContextConfig(payload);
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice("Context config saved.");
      pushEvent("Context config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save context config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveAgent(
    agentName: string,
    payload: AgentConfigPayload,
  ) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveAgentConfig(agentName, payload);
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice(`${agentName} config saved.`);
      pushEvent(`${agentName} config saved`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save agent config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleReloadConfig() {
    try {
      setSavingConfig(true);
      setError("");
      await api.reloadConfig();
      const [refreshed, refreshedRules] = await Promise.all([api.getConfig(), api.getToolAuthorizationRules()]);
      setConfig(refreshed);
      setAuthorizationRules(refreshedRules);
      setNotice("Config reloaded.");
      pushEvent("Configuration reloaded", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to reload config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleRevokeAuthorizationRule(ruleId: number) {
    try {
      setSavingConfig(true);
      setError("");
      await api.revokeToolAuthorizationRule(ruleId);
      const refreshedRules = await api.getToolAuthorizationRules();
      setAuthorizationRules(refreshedRules);
      setNotice("Authorization rule revoked.");
      pushEvent("Authorization rule revoked", "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to revoke authorization rule");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleTestAgentConfig(agentName: string) {
    try {
      setSavingConfig(true);
      setError("");
      const result = await api.testConfig(agentName);
      setNotice(`Connection successful for ${result.agent} (${result.model}).`);
      pushEvent(`Connection test succeeded for ${result.agent}`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to test agent config");
      pushEvent(`Connection test failed for ${agentName}`, "error");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleApproveGate(pipelineId: number) {
    try {
      setError("");
      await api.approvePipeline(pipelineId);
      setNotice(`Approved pipeline gate for #${pipelineId}.`);
      pushEvent(`Approved pipeline gate #${pipelineId}`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to approve gate";
      setError(message);
      pushEvent(`Approve failed for pipeline #${pipelineId}`, "error");
      throw nextError;
    }
  }

  async function handleRejectGate(pipelineId: number) {
    try {
      setError("");
      await api.rejectPipeline(pipelineId);
      setNotice(`Rejected pipeline gate for #${pipelineId}.`);
      pushEvent(`Rejected pipeline gate #${pipelineId}`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to reject gate";
      setError(message);
      pushEvent(`Reject failed for pipeline #${pipelineId}`, "error");
      throw nextError;
    }
  }

  return (
    <div className={`app-shell ${activeTab === "chat" ? "app-shell--chat" : ""}`} style={appShellStyle}>
      <AppSidebar
        mode={activeTab === "config" ? "settings" : "workspace"}
        chats={chats}
        selectedChatId={selectedChatId}
        onSelectChat={(chatId) => {
          const nextChat = chats.find((chat) => chat.id === chatId) ?? null;
          applyChatSelection(chatId, nextChat?.project_id ?? null);
          setActiveTab("chat");
          setSidebarDrawerOpen(false);
          pushEvent(`Opened chat #${chatId}`, "info");
        }}
        onCreateChat={handleCreateChat}
        onRenameChat={handleRenameChat}
        onDeleteChat={handleDeleteChat}
        projects={projects}
        selectedProjectId={selectedProjectId}
        onSelectProject={(projectId) => {
          const nextProject = projects.find((project) => project.id === projectId) ?? null;
          applyChatSelection(nextProject?.default_chatroom_id ?? null, nextProject?.id ?? null);
          setActiveTab("chat");
          setSidebarDrawerOpen(false);
          pushEvent(`Opened project "${nextProject?.name || projectId}"`, "info");
        }}
        onOpenProjectCreator={handleOpenProjectCreator}
        onCreateProjectChat={handleCreateProjectChat}
        onReorderProjects={handleReorderProjects}
        onRenameProject={handleRenameProject}
        onDeleteProject={handleDeleteProject}
        settingsSections={settingsSections}
        selectedSettingsSection={activeConfigSection}
        onSelectSettingsSection={(section) => {
          setNotice("");
          setActiveConfigSection(section);
          setActiveTab("config");
          setSidebarDrawerOpen(false);
        }}
        drawerOpen={sidebarDrawerOpen}
        onCloseDrawer={() => setSidebarDrawerOpen(false)}
      />
      <button
        type="button"
        className="sidebar-resize-handle sidebar-resize-handle--left"
        onPointerDown={handleAppSidebarResizeStart}
        aria-label="Resize navigation sidebar"
        title="Resize sidebar"
      />

      {activeTab === "chat" && sidebarDrawerOpen ? (
        <button
          type="button"
          className="mobile-drawer-backdrop mobile-drawer-backdrop--left"
          onClick={() => setSidebarDrawerOpen(false)}
          aria-label="Close navigation"
        />
      ) : null}

      <main className={`main-surface ${activeTab === "chat" ? "main-surface--chat" : ""}`}>
        {activeTab !== "chat" ? (
          <>
            <header className="surface-header surface-header--console">
              <div className="topnav-shell">
                <div className="topnav-shell__content">
                  <div className="dashboard-header">
                    <div className="dashboard-header__breadcrumb">
                      <span className="dashboard-header__breadcrumb-link">Catown</span>
                      <span className="dashboard-header__breadcrumb-sep">/</span>
                      <span className="dashboard-header__breadcrumb-current">
                        {activeTab === "projects" && "Projects"}
                        {activeTab === "config" && "Settings"}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="topnav-shell__actions topbar-status">
                  <span className="soft-pill">
                    <span className="status-dot topbar-status-dot" />
                    <span>{agents.filter((agent) => agent.is_active).length} agents</span>
                  </span>
                  <span className="soft-pill mono-pill">
                    {activeTab === "config" ? `${settingsSections.length} sections` : `${projects.length} rooms`}
                  </span>
                  <button
                    type="button"
                    className="btn btn--sm btn--icon settings-icon-btn"
                    onClick={toggleConfigTab}
                    aria-label={activeTab === "config" ? "Back to chat" : "Open settings"}
                    title={activeTab === "config" ? "Back to chat" : "Settings"}
                  >
                    <span className="settings-icon-glyph" aria-hidden="true">⚙</span>
                  </button>
                </div>
              </div>
            </header>

            <section className="content-header">
              <div className="surface-header-copy">
                <h2 className="page-title">
                  {activeTab === "projects" && "Project center"}
                  {activeTab === "config" && activeConfigMeta.title}
                </h2>
                <p className="page-sub">
                  {activeTab === "projects" && "Create rooms, assign agents, and switch sessions from one place."}
                  {activeTab === "config" && activeConfigMeta.subtitle}
                </p>
              </div>
              <div className="page-meta">
                <span className="soft-pill">V2 preview</span>
                <span className="soft-pill">React shell</span>
              </div>
            </section>
          </>
        ) : null}

        {notice ? <div className="notice-banner">{notice}</div> : null}
        {error ? (
          <div className="error-banner" role="alert">
            <span className="error-banner__message">{error}</span>
            <button
              type="button"
              className="error-banner__close"
              onClick={() => setError("")}
              aria-label="Dismiss error"
            >
              Dismiss
            </button>
          </div>
        ) : null}

        {activeTab === "chat" ? (
          <ChatTab
            chat={activeChat}
            project={selectedProject}
            agents={agents}
            messages={messages}
            optimisticMessages={optimisticMessages}
            loading={loadingMessages}
            sending={sendingMessage}
            refreshing={refreshingMessages}
            creatingProjectFromChat={creatingProjectFromChat}
            connectionState={connectionState}
            cards={chatCards}
            taskRuns={taskRuns}
            processes={chatProcesses}
            projectBrowserIndex={projectBrowserIndex}
            liveTaskRunDetailsById={liveTaskRunDetailsById}
            taskActivitiesById={taskActivitiesById}
            taskTimelinesById={taskTimelinesById}
            events={chatEvents}
            onSend={handleSendMessage}
            onOpenWorkspace={handleOpenWorkspace}
            onOpenSidebar={() => {
              setActivityDrawerOpen(false);
              setSidebarDrawerOpen(true);
            }}
            onOpenActivity={() => {
              setSidebarDrawerOpen(false);
              setActivityDrawerOpen(true);
            }}
            activityDrawerOpen={activityDrawerOpen}
            onCloseActivity={() => setActivityDrawerOpen(false)}
            onOpenSettings={toggleConfigTab}
            onRefresh={() => refreshMessages(true)}
            onRefreshRuntime={refreshRuntimeForTaskRun}
            onPatchSubagentRuntime={patchSubagentRuntime}
            onSyncProject={handleSyncProject}
            syncingProject={selectedProject ? syncingProjectId === selectedProject.id : false}
            onApproveGate={handleApproveGate}
            onRejectGate={handleRejectGate}
            onCreateProjectFromChat={handleCreateProjectFromCurrentChat}
          />
        ) : null}

        {activeTab === "projects" ? (
          <ProjectsTab
            projects={projects}
            agents={agents}
            selectedProjectId={selectedProjectId}
            creating={creatingProject}
            onCreateProject={handleCreateProject}
            onSelectProject={(projectId) => {
              const nextProject = projects.find((project) => project.id === projectId) ?? null;
              applyChatSelection(nextProject?.default_chatroom_id ?? null, nextProject?.id ?? null);
              setActiveTab("chat");
              pushEvent(`Opened project "${nextProject?.name || projectId}"`, "info");
            }}
          />
        ) : null}

        {activeTab === "config" ? (
          <ConfigTab
            config={config}
            activeSection={activeConfigSection}
            saving={savingConfig}
            onBackToChat={() => setActiveTab("chat")}
            onSaveGlobal={handleSaveGlobal}
            onSaveOrchestration={handleSaveOrchestration}
            onSavePermissions={handleSavePermissions}
            onSaveContext={handleSaveContext}
            authorizationRules={authorizationRules}
            onRevokeAuthorizationRule={handleRevokeAuthorizationRule}
            onSaveAgent={handleSaveAgent}
            onReload={handleReloadConfig}
            onTestAgentConfig={handleTestAgentConfig}
          />
        ) : null}
      </main>
    </div>
  );
}

export default App;
