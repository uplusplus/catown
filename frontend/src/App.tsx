import { useEffect, useMemo, useRef, useState } from "react";

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
  ChatCardItem,
  ChatCardLlmTimings,
  ChatEventItem,
  ChatEventTone,
  ChatSummary,
  ConfigSection,
  ConfigResponse,
  MessageItem,
  MessageStreamStep,
  ProjectSummary,
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
  memory: {
    sidebarLabel: "Memory",
    sidebarDescription: "Long-term memory, retained context, and summaries",
    title: "Memory management",
    subtitle: "Inspect retained memory footprint by agent and understand what long-term context already exists.",
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
  };
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
    optimisticKind: message.optimisticKind,
    localOnly: message.localOnly,
    streamSteps: (message.streamSteps || []).slice(-OPTIMISTIC_MAX_STEP_COUNT).map(sanitizePersistedStep),
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
        result: typeof payload.result === "string" ? payload.result : undefined,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        tool_call_index: typeof payload.tool_call_index === "number" ? payload.tool_call_index : undefined,
        tool_call_id: typeof payload.tool_call_id === "string" ? payload.tool_call_id : null,
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

function buildStreamStep(
  label: string,
  detail?: string,
  state: MessageStreamStep["state"] = "live",
  detailContent?: string,
  meta?: Partial<Pick<MessageStreamStep, "kind" | "agent" | "tool" | "toolCallIndex" | "toolCallId">>,
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
  meta?: Partial<Pick<MessageStreamStep, "kind" | "agent" | "tool">>,
) {
  const nextSteps = [...settleLiveStreamSteps(message.streamSteps), buildStreamStep(label, detail, state, detailContent, meta)];
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
      }),
    ]),
  };
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

  return {
    ...message,
    streamSteps: trimStreamSteps([...settled, buildStreamStep(finalLabel, finalDetail, state)]),
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
      isJsonContent(result)
        ? markdownSection(failed ? "Error" : "Tool Result", prettyJson(result), { language: "json" })
        : markdownSection(failed ? "Error" : "Tool Result", result.trim(), { asMarkdown: true }),
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
      isJsonContent(card.result)
        ? markdownSection(card.success === false ? "Error" : "Tool Result", prettyJson(card.result), { language: "json" })
        : markdownSection(card.success === false ? "Error" : "Tool Result", card.result, { asMarkdown: true }),
    );
  }
  return sections.join("\n\n");
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
  if (card.arguments) bits.push(`args: ${summarizeStepDetail(card.arguments, 90)}`);
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
          isJsonContent(card.result)
            ? markdownSection(card.success === false ? "Error" : "Result", prettyJson(card.result), { language: "json" })
            : markdownSection(card.success === false ? "Error" : "Result", card.result, { asMarkdown: true }),
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

function applyRuntimeCardStep(message: MessageItem, card: ChatCardItem) {
  const actor =
    card.agent ||
    card.from_agent ||
    card.to_agent ||
    message.agent_name ||
    "agent";

  switch (card.kind) {
    case "llm_call": {
      const outboundLabel = llmOutboundStepLabel(actor);
      const nextMessage = patchMatchingStreamingStep(
        message,
        (step) => isActorOutboundStep(step, actor),
        {
          detailContent: buildCardLlmPromptDetailContent(card),
          state: "done",
          kind: "llm_outbound",
          agent: actor,
        },
        outboundLabel,
      );

      return patchMatchingStreamingStep(
        nextMessage,
        (step) => isActorInboundStep(step, actor),
        {
          detail: buildLlmResponseStepDetail(card),
          detailContent: buildCardLlmResponseDetailContent(card),
          state: "done",
          kind: "llm_inbound",
          agent: actor,
        },
        llmInboundStepLabel(actor, card.finish_reason),
      );
    }
    case "tool_call": {
      const toolName = card.tool || "tool";
      const toolCallIndex = card.tool_call_index;
      const toolCallId = card.tool_call_id;
      const nextMessage = patchMatchingStreamingStep(
        message,
        (step) => isActorToolCallStepByRef(step, actor, toolName, toolCallIndex, toolCallId),
        {
          detail: buildToolCallStepDetail(card),
          detailContent: buildCardToolCallDetailContent(card),
          state: card.success === false ? "error" : "done",
          kind: "tool_call",
          agent: actor,
          tool: toolName,
          toolCallIndex,
          toolCallId,
        },
        toolCallStepLabel(actor, toolName),
      );

      return patchMatchingStreamingStep(
        nextMessage,
        (step) => isActorToolResultStep(step, actor, toolName, toolCallIndex),
        {
          detail: buildToolResultStepDetail(card),
          detailContent: buildCardToolResultDetailContent(card),
          state: "done",
          kind: "tool_result_to_llm",
          agent: actor,
          tool: toolName,
          toolCallIndex,
          toolCallId,
        },
        toolOutputStepLabel(actor, toolName),
      );
    }
    case "agent_error": {
      return pushStreamingStep(
        message,
        `${card.agent || actor} failed before finishing the turn`,
        buildCardStepDetail(card),
        "error",
        buildCardStepDetailContent(card),
      );
    }
    case "stage_start": {
      const label = `${actor} started ${card.display_name || card.stage || "stage"}`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "stage_end": {
      const label = `${actor} completed ${card.stage || "stage"}`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "skill_inject": {
      return pushStreamingStep(
        message,
        `${actor} loaded skills`,
        buildCardStepDetail(card),
        "done",
        buildCardStepDetailContent(card),
      );
    }
    case "agent_message": {
      const label = `${card.from_agent || actor} sent a team message`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "boss_instruction": {
      return pushStreamingStep(
        message,
        `Boss instruction for ${card.agent || actor}`,
        buildCardStepDetail(card),
        "done",
        buildCardStepDetailContent(card),
      );
    }
    case "gate_blocked": {
      return pushStreamingStep(
        message,
        `${actor} is waiting for approval`,
        buildCardStepDetail(card),
        "error",
        buildCardStepDetailContent(card),
      );
    }
    case "gate_approved": {
      return pushStreamingStep(message, `${actor} approved the gate`, buildCardStepDetail(card), "done");
    }
    case "gate_rejected": {
      return pushStreamingStep(
        message,
        `${actor} rejected the gate`,
        buildCardStepDetail(card),
        "error",
        buildCardStepDetailContent(card),
      );
    }
    default:
      return message;
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

function replayRuntimeCardsForTurn(message: MessageItem, cards: ChatCardItem[]) {
  if (cards.length === 0) return message;
  let nextMessage = message;
  for (const card of cards) {
    nextMessage = applyRuntimeCardStep(nextMessage, card);
  }
  const lastActor =
    [...cards]
      .reverse()
      .map((card) => card.agent || card.from_agent || card.to_agent)
      .find((actor): actor is string => Boolean(actor)) ?? null;
  if (lastActor && nextMessage.agent_name !== lastActor) {
    nextMessage = { ...nextMessage, agent_name: lastActor };
  }
  return nextMessage;
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
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [selectedChatId, setSelectedChatId] = useState<number | null>(() => readLastChatId());
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [optimisticMessages, setOptimisticMessages] = useState<MessageItem[]>([]);
  const [chatCards, setChatCards] = useState<ChatCardItem[]>([]);
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
        id: "memory" as const,
        label: CONFIG_SECTION_META.memory.sidebarLabel,
        description: CONFIG_SECTION_META.memory.sidebarDescription,
      },
    ],
    [agents],
  );
  const activeConfigMeta = CONFIG_SECTION_META[activeConfigSection];

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

  function pushEvent(message: string, tone: ChatEventTone = "info") {
    setChatEvents((current) => [...current.slice(-79), buildEvent(message, tone)]);
  }

  function pushCard(card: ChatCardItem) {
    setChatCards((current) => mergeCards(current, [card]).slice(-40));
  }

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
    setOptimisticMessages((current) => (typeof updater === "function" ? updater(current) : updater));
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

  function applyChatSelection(nextChatId: number | null, nextProjectId: number | null) {
    debugConsole("info", "applyChatSelection", {
      previousChatId: selectedChatIdRef.current,
      previousProjectId: selectedProjectIdRef.current,
      nextChatId,
      nextProjectId,
      lastChatStorage: readLastChatId(),
    });
    selectedChatIdRef.current = nextChatId;
    selectedProjectIdRef.current = nextProjectId;
    writeLastChatId(nextChatId);
    setSelectedChatId(nextChatId);
    setSelectedProjectId(nextProjectId);
  }

  function toggleConfigTab() {
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
      const [agentRows, configData, chatRows] = await Promise.all([
        api.getAgents(),
        api.getConfig(),
        api.getChats(),
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
    if (!bootstrapped) return;
    if (!selectedChatId) {
      setMessages([]);
      optimisticScopeRef.current = optimisticScopeKey(null);
      setOptimisticMessages(readOptimisticMessages(null));
      setChatCards([]);
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
        setChatEvents([]);
        const [rows, runtimeRows] = await Promise.all([
          api.getMessages(activeChatId),
          api.getRuntimeCards(activeChatId),
        ]);
        if (!cancelled) {
          const nextCards = runtimeRows
            .map((payload) => buildCard(payload))
            .filter((card): card is ChatCardItem => card !== null);
          setMessages((current) => mergeMessages(current, rows));
          setChatCards(nextCards);
          commitOptimisticMessages((current) => reconcileOptimisticMessagesWithServer(current, rows, nextCards));
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
              if (streamingAssistantIdRef.current !== null) {
                commitOptimisticMessages((current) =>
                  updateMessage(current, streamingAssistantIdRef.current ?? 0, (message) =>
                    applyRuntimeCardStep(
                      {
                        ...message,
                        agent_name:
                          card.agent ||
                          card.from_agent ||
                          card.to_agent ||
                          message.agent_name,
                      },
                      card,
                    ),
                  ),
                );
              }
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
              if (streamingAssistantIdRef.current !== null) {
                commitOptimisticMessages((current) =>
                  updateMessage(current, streamingAssistantIdRef.current ?? 0, (message) =>
                    applyRuntimeCardStep(
                      {
                        ...message,
                        agent_name:
                          card.agent ||
                          card.from_agent ||
                          card.to_agent ||
                          message.agent_name,
                      },
                      card,
                    ),
                  ),
                );
              }
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
      const [rows, runtimeRows] = await Promise.all([
        api.getMessages(chatId),
        api.getRuntimeCards(chatId),
      ]);
      const nextCards = runtimeRows
        .map((payload) => buildCard(payload))
        .filter((card): card is ChatCardItem => card !== null);
      setMessages((current) => mergeMessages(current, rows));
      commitOptimisticMessages((current) => reconcileOptimisticMessagesWithServer(current, rows, nextCards));
      setChatCards(nextCards);
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

  async function handleSendMessage(content: string, options?: { clientTurnId?: string }) {
    let chatId = selectedChatId;
    let streamReceivedEvent = false;
    const userTempId = nextTempMessageId();
    const assistantTempId = nextTempMessageId();
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
          {
            id: assistantTempId,
            content: "",
            message_type: "text",
            created_at: new Date(Date.now() + 1).toISOString(),
            agent_name: activeAgentName,
            client_turn_id: clientTurnId,
            isStreaming: true,
            optimisticKind: "assistant_placeholder",
            streamSteps: [buildStreamStep("Queued", "Waiting to start agent response.")],
          },
        ]),
      );
      streamingAssistantIdRef.current = assistantTempId;
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

      const readAssistantMessageId = () => streamingAssistantIdRef.current ?? assistantTempId;

      const flushPendingContent = () => {
        if (!pendingContentDelta) return;
        const delta = pendingContentDelta;
        pendingContentDelta = "";
        assistantDraftContent = `${assistantDraftContent}${delta}`;
        commitOptimisticMessages((current) =>
          updateMessage(current, readAssistantMessageId(), (message) => {
            const settledOutboundMessage = patchMatchingStreamingStep(
              {
                ...message,
                agent_name: message.agent_name || activeAgentName,
              },
              (step) => step.state === "live" && isActorOutboundStep(step, activeAgentName),
              {
                state: "done",
              },
            );

            const nextMessage = patchMatchingStreamingStep(
              settledOutboundMessage,
              (step) => isActorInboundStep(step, activeAgentName),
              {
                detail: summarizeStepDetail(assistantDraftContent) || "Streaming tokens from the model.",
                detailContent: buildLiveLlmResponseDetailContent(assistantDraftContent, undefined, liveLlmTimings),
                state: "live",
                kind: "llm_inbound",
                agent: activeAgentName,
              },
              llmInboundStepLabel(activeAgentName),
            );

            return {
              ...nextMessage,
              content: `${nextMessage.content}${delta}`,
            };
          }),
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

        if (typeof data.type === "string") {
          const card = buildCard(data);
          if (card) {
            // Keep runtime cards single-sourced from websocket/refresh so
            // llm/tool counters do not double count the same persisted card.
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => {
                const nextAgentName =
                  card.agent ||
                  card.from_agent ||
                  card.to_agent ||
                  message.agent_name;
                return applyRuntimeCardStep(
                  {
                    ...message,
                    agent_name: nextAgentName || message.agent_name,
                  },
                  card,
                );
              }),
            );
          }
        }

        switch (data.type) {
          case "user_saved":
            if (typeof data.id === "number") {
              commitOptimisticMessages((current) => replaceMessageId(current, userTempId, data.id));
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
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: activeAgentName,
                  },
                  llmOutboundStepLabel(activeAgentName),
                  buildLlmMetaSummary(liveLlmModel, liveLlmTurn) || "Preparing context and sending prompt.",
                  "live",
                  buildLiveLlmPromptDetailContent(content, {
                    systemPrompt: liveLlmSystemPrompt,
                    promptMessages: liveLlmPromptMessages,
                    model: liveLlmModel,
                    turn: liveLlmTurn,
                    timings: liveLlmTimings,
                  }),
                  {
                    kind: "llm_outbound",
                    agent: activeAgentName,
                  },
                ),
              })),
            );
            pushEvent(`${activeAgentName} is responding`, "info");
            break;
          case "collab_start": {
            const agentNames = readStringArray(data.agents);
            activeAgentName = "pipeline";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: "pipeline",
                    content:
                      agentNames.length > 0
                        ? `Pipeline: ${agentNames.join(" -> ")}\n\n`
                        : message.content,
                  },
                  "Routing multi-agent flow",
                  agentNames.length > 0 ? agentNames.join(" -> ") : "Selecting agents.",
                ),
              })),
            );
            if (agentNames.length > 0) {
              pushEvent(`Multi-agent pipeline: ${agentNames.join(" -> ")}`, "info");
            }
            break;
          }
          case "collab_step": {
            const agentName = typeof data.agent === "string" && data.agent ? data.agent : activeAgentName;
            activeAgentName = agentName;
            const step = typeof data.step === "number" ? data.step : "?";
            const total = typeof data.total === "number" ? data.total : "?";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: "pipeline",
                    content: `${message.content}\nStep ${step}/${total}: ${agentName}\n\n`,
                  },
                  `${agentName} is working`,
                  `Step ${step}/${total}`,
                ),
              })),
            );
            pushEvent(`Step ${step}/${total}: ${agentName}`, "info");
            break;
          }
          case "collab_step_done":
            if (typeof data.agent === "string") {
              pushEvent(`${data.agent} completed their step`, "success");
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
            const elapsedText = formatStreamingElapsed(elapsedMs);
            if (data.type === "request_sent" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, request_sent_ms: elapsedMs };
            }
            if (data.type === "first_chunk" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_chunk_ms: elapsedMs };
            }
            if (data.type === "first_content" && elapsedMs !== undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_content_ms: elapsedMs };
            }

            const detail =
              data.type === "request_sent"
                ? elapsedText
                  ? `Request sent · ${elapsedText}`
                  : "Request sent"
                : data.type === "first_chunk"
                  ? elapsedText
                    ? `First stream chunk · ${elapsedText}`
                    : "First stream chunk"
                  : elapsedText
                    ? `First content token · ${elapsedText}`
                    : "First content token";

            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) =>
                patchMatchingStreamingStep(
                  {
                    ...message,
                    agent_name: message.agent_name || activeAgentName,
                  },
                  (step) => step.state === "live" && isActorOutboundStep(step, activeAgentName),
                  {
                    detail,
                    detailContent: buildLiveLlmPromptDetailContent(content, {
                      systemPrompt: liveLlmSystemPrompt,
                      promptMessages: liveLlmPromptMessages,
                      model: liveLlmModel,
                      turn: liveLlmTurn,
                      timings: liveLlmTimings,
                    }),
                    state: "live",
                    kind: "llm_outbound",
                    agent: activeAgentName,
                  },
                  llmOutboundStepLabel(activeAgentName),
                ),
              ),
            );
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
            const elapsedText = formatStreamingElapsed(elapsedMs);
            if (elapsedMs !== undefined && liveLlmTimings.first_tool_call_ms === undefined) {
              liveLlmTimings = { ...liveLlmTimings, first_tool_call_ms: elapsedMs };
            }
            liveToolArgs.set(buildToolWaitKey(activeAgentName, toolCallIndex, toolName), rawToolArgs);
            const detail = elapsedText
              ? `Planning ${toolName} · ${elapsedText}`
              : `Planning ${toolName}`;
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => {
                const baseMessage = patchMatchingStreamingStep(
                  {
                    ...message,
                    agent_name: message.agent_name || activeAgentName,
                  },
                  (step) => step.state === "live" && isActorOutboundStep(step, activeAgentName),
                  {
                    state: "done",
                    detail: elapsedText ? `Tool planning started · ${elapsedText}` : "Tool planning started",
                    detailContent: buildLiveLlmPromptDetailContent(content, {
                      systemPrompt: liveLlmSystemPrompt,
                      promptMessages: liveLlmPromptMessages,
                      model: liveLlmModel,
                      turn: liveLlmTurn,
                      timings: liveLlmTimings,
                    }),
                    kind: "llm_outbound",
                    agent: activeAgentName,
                  },
                );

                const nextMessage = patchMatchingStreamingStep(
                  baseMessage,
                  (step) => step.state === "live" && isActorToolCallStepByRef(step, activeAgentName, toolName, toolCallIndex, toolCallId),
                  {
                    label: toolCallStepLabel(activeAgentName, toolName),
                    detail,
                    detailContent: buildLiveToolCallDetailContent(rawToolArgs, detail),
                    state: "live",
                    kind: "tool_call",
                    agent: activeAgentName,
                    tool: toolName,
                    toolCallIndex,
                    toolCallId,
                  },
                );

                if (nextMessage !== baseMessage) {
                  return nextMessage;
                }

                return pushStreamingStep(
                  baseMessage,
                  toolCallStepLabel(activeAgentName, toolName),
                  detail,
                  "live",
                  buildLiveToolCallDetailContent(rawToolArgs, detail),
                  {
                    kind: "tool_call",
                    agent: activeAgentName,
                    tool: toolName,
                    toolCallIndex,
                    toolCallId,
                  },
                );
              }),
            );
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
              const toolArgs = rawToolArgs ? rawToolArgs.slice(0, 120) : "Calling tool.";
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) => {
                  const settledResponseMessage = patchMatchingStreamingStep(
                    {
                      ...message,
                      agent_name: message.agent_name || activeAgentName,
                    },
                    (step) => step.state === "live" && isActorInboundStep(step, activeAgentName),
                    {
                      state: "done",
                    },
                  );

                  const nextMessage = patchMatchingStreamingStep(
                    settledResponseMessage,
                    (step) => step.state === "live" && isActorToolCallStepByRef(step, activeAgentName, toolName, toolCallIndex, toolCallId),
                    {
                      label: toolCallStepLabel(activeAgentName, toolName),
                      detail: toolArgs,
                      detailContent: buildLiveToolCallDetailContent(rawToolArgs),
                      state: "live",
                      kind: "tool_call",
                      agent: activeAgentName,
                      tool: toolName,
                      toolCallIndex,
                      toolCallId,
                    },
                  );

                  if (nextMessage !== settledResponseMessage) {
                    return nextMessage;
                  }

                  return {
                    ...pushStreamingStep(
                      settledResponseMessage,
                      toolCallStepLabel(activeAgentName, toolName),
                      toolArgs,
                      "live",
                      buildLiveToolCallDetailContent(rawToolArgs),
                      {
                        kind: "tool_call",
                        agent: activeAgentName,
                        tool: toolName,
                        toolCallIndex,
                        toolCallId,
                      },
                    ),
                  };
                }),
              );
              pushEvent(`${activeAgentName} is running ${toolName}`, "warning");
            }
            break;
          case "llm_wait": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            const elapsedText = formatStreamingElapsed(
              typeof data.elapsed_ms === "number" ? data.elapsed_ms : undefined,
            );
            const waitStatus = elapsedText ? `Waiting on model · ${elapsedText}` : "Waiting on model";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => {
                const baseMessage = {
                  ...message,
                  agent_name: message.agent_name || activeAgentName,
                };
                const inboundMessage = patchMatchingStreamingStep(
                  baseMessage,
                  (step) => step.state === "live" && isActorInboundStep(step, activeAgentName),
                  {
                    detail: waitStatus,
                    detailContent: buildLiveLlmResponseDetailContent(assistantDraftContent, waitStatus, liveLlmTimings),
                    state: "live",
                    kind: "llm_inbound",
                    agent: activeAgentName,
                  },
                );
                if (inboundMessage !== baseMessage) {
                  return inboundMessage;
                }

                return patchMatchingStreamingStep(
                  baseMessage,
                  (step) => step.state === "live" && isActorOutboundStep(step, activeAgentName),
                  {
                    detail: waitStatus,
                    detailContent: buildLiveLlmPromptDetailContent(content, {
                      systemPrompt: liveLlmSystemPrompt,
                      promptMessages: liveLlmPromptMessages,
                      model: liveLlmModel,
                      turn: liveLlmTurn,
                      waitStatus,
                      timings: liveLlmTimings,
                    }),
                    state: "live",
                    kind: "llm_outbound",
                    agent: activeAgentName,
                  },
                  llmOutboundStepLabel(activeAgentName),
                );
              }),
            );
            break;
          }
          case "tool_wait":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const toolName = data.tool;
              const toolCallIndex = typeof data.tool_call_index === "number" ? data.tool_call_index : undefined;
              const toolCallId = typeof data.tool_call_id === "string" ? data.tool_call_id : null;
              const elapsedText = formatStreamingElapsed(
                typeof data.elapsed_ms === "number" ? data.elapsed_ms : undefined,
              );
              const waitStatus = elapsedText ? `Running tool · ${elapsedText}` : "Running tool";
              const rawToolArgs = liveToolArgs.get(buildToolWaitKey(activeAgentName, toolCallIndex, toolName)) ?? "";
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) =>
                  patchMatchingStreamingStep(
                    {
                      ...message,
                      agent_name: message.agent_name || activeAgentName,
                    },
                    (step) => step.state === "live" && isActorToolCallStepByRef(step, activeAgentName, toolName, toolCallIndex, toolCallId),
                    {
                      detail: waitStatus,
                      detailContent: buildLiveToolCallDetailContent(rawToolArgs, waitStatus),
                      state: "live",
                      kind: "tool_call",
                      agent: activeAgentName,
                      tool: toolName,
                      toolCallIndex,
                      toolCallId,
                    },
                    toolCallStepLabel(activeAgentName, toolName),
                  ),
                ),
              );
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
              const failed =
                typeof data.success === "boolean" ? data.success === false : isToolResultFailure(rawResult);
              const resultPreview =
                rawResult.trim()
                  ? rawResult.replace(/\s+/g, " ").trim().slice(0, 140)
                  : `${toolName} completed.`;
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) => {
                  const nextMessage = patchMatchingStreamingStep(
                    message,
                    (step) => isActorToolCallStepByRef(step, activeAgentName, toolName, toolCallIndex, toolCallId),
                    {
                      state: failed ? "error" : "done",
                      kind: "tool_call",
                      agent: activeAgentName,
                      tool: toolName,
                      toolCallIndex,
                      toolCallId,
                    },
                    toolCallStepLabel(activeAgentName, toolName),
                  );

                  return pushStreamingStep(
                    nextMessage,
                    toolOutputStepLabel(activeAgentName, toolName),
                    resultPreview,
                    "live",
                    buildToolResultToLlmDetailContent(rawResult, failed),
                    {
                      kind: "tool_result_to_llm",
                      agent: activeAgentName,
                      tool: toolName,
                      toolCallIndex,
                      toolCallId,
                    },
                  );
                }),
              );
              pushEvent(`${activeAgentName} finished ${toolName}`, failed ? "error" : "success");
            }
            break;
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
              commitOptimisticMessages((current) =>
                replaceMessageId(
                  current,
                  readAssistantMessageId(),
                  savedMessageId,
                  finalizeStreamingTrace(
                    {
                      id: savedMessageId,
                      content: "",
                      created_at: new Date().toISOString(),
                      message_type: "text",
                      ...current.find((item) => item.id === readAssistantMessageId()),
                      agent_name: finalAgentName,
                      client_turn_id: savedClientTurnId,
                      isStreaming: false,
                    },
                    "done",
                    "Completed",
                    "Agent reply saved.",
                  ),
                ),
              );
              streamingAssistantIdRef.current = savedMessageId;
            } else {
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) =>
                  finalizeStreamingTrace(
                    {
                      ...message,
                      agent_name: finalAgentName,
                      isStreaming: false,
                    },
                    "done",
                    "Completed",
                    "Agent flow finished.",
                  ),
                ),
              );
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
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (item) =>
                finalizeStreamingTrace(
                  {
                    ...item,
                    content: item.content || `Error: ${message}`,
                    agent_name: activeAgentName,
                    isStreaming: false,
                  },
                  "error",
                  "Failed",
                  message,
                ),
              ),
            );
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
        commitOptimisticMessages((current) =>
          updateMessage(current, readAssistantMessageId(), (message) =>
            finalizeStreamingTrace(
              {
                ...message,
                isStreaming: false,
              },
              "done",
            ),
          ),
        );
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
      setNotice(`Opened self-bootstrap project "${project.name}".`);
      pushEvent(`Opened self-bootstrap project "${project.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
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
      setNotice(`Created sub chat "${created.title}" in "${targetProject.name}".`);
      pushEvent(`Created sub chat in "${targetProject.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
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

  async function handleSaveGlobal(payload: {
    provider: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
    default_model: string;
  }) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveGlobalConfig(payload);
      const refreshed = await api.getConfig();
      setConfig(refreshed);
      setNotice("Global config saved.");
      pushEvent("Global config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save global config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveAgent(
    agentName: string,
    payload: {
      provider?: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
      default_model?: string;
      role?: {
        title?: string;
        responsibilities?: string[];
        rules?: string[];
      };
      soul?: {
        identity?: string;
        values?: string[];
        style?: string;
        quirks?: string;
      };
      tools?: string[];
      skills?: string[];
    },
  ) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveAgentConfig(agentName, payload);
      const refreshed = await api.getConfig();
      setConfig(refreshed);
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
      const refreshed = await api.getConfig();
      setConfig(refreshed);
      setNotice("Config reloaded.");
      pushEvent("Configuration reloaded", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to reload config");
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
    <div className={`app-shell ${activeTab === "chat" ? "app-shell--chat" : ""}`}>
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
          setActiveConfigSection(section);
          setActiveTab("config");
          setSidebarDrawerOpen(false);
        }}
        drawerOpen={sidebarDrawerOpen}
        onCloseDrawer={() => setSidebarDrawerOpen(false)}
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
        {error ? <div className="error-banner">{error}</div> : null}

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
