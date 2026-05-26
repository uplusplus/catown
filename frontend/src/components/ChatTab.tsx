import { FormEvent, KeyboardEvent, MouseEvent, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { Archive, BookOpen, Bot, Boxes, Braces, CheckSquare, ChevronDown, ChevronRight, ClipboardCheck, File, FileText, Folder, FolderTree, Menu, Monitor, PackageCheck, PanelRightOpen, ScrollText, Search, SendHorizontal, Settings, Shell, Square, TestTube2, Workflow, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { api } from "../api/client";
import { ChoiceBox, type ChoiceBoxData } from "./ChoiceBox";
import { FormSuggestionStrip } from "./FormSuggestionStrip";
import { UI_VERSION } from "../uiVersion";
import { buildLlmTimingsMarkdown } from "../utils/llmTimings";
import {
  isCommandInput,
  matchCommands,
  matchHistory,
  type ChatCommandDef,
} from "../utils/chatCommands";
import {
  DEFAULT_AGENT_TYPE,
  defaultAgentName,
  findAgentByType,
  getAgentDisplayName,
  getAgentType,
} from "../utils/agents";
import { buildAgentThemeStyle, getAgentTheme, resolveAgentLabel } from "../utils/agentColors";
import {
  filterAgentSetSuggestions,
  filterTextSuggestions,
  readProjectFormSuggestionStore,
  rememberChatProjectSuggestion,
} from "../utils/projectFormSuggestions";
import type {
  ApprovalQueueItem,
  AgentInfo,
  ChatCardItem,
  ChatEventItem,
  ChatProcessEntry,
  ChatSummary,
  ChatTimelineProjection,
  ChatTimelineStep,
  MessageItem,
  MessageStreamStep,
  TaskActivityProjection,
  ProjectBrowserIndex,
  ProjectBrowserFileItem,
  ProjectFileReadResponse,
  ProjectSummary,
  TaskRunDetail,
  TaskRunEvent,
  TaskRunSummary,
} from "../types";

const LOCAL_OVERLAY_STORAGE_KEY = "catown:chat-local-overlay";
const DRAFT_HISTORY_STORAGE_KEY = "catown:chat-draft-history";
const THREAD_AUTO_SCROLL_THRESHOLD = 72;
const LARGE_MARKDOWN_HIGHLIGHT_LIMIT = 12000;
const OVERLAY_MAX_CHATS = 6;
const OVERLAY_MAX_MESSAGES_PER_CHAT = 2;
const OVERLAY_MAX_CONTENT_CHARS = 1200;
const OVERLAY_MAX_STEP_COUNT = 1;
const OVERLAY_MAX_STEP_LABEL_CHARS = 120;
const OVERLAY_MAX_STEP_DETAIL_CHARS = 180;
const STREAM_TRACE_RECENT_STEP_COUNT = 5;
const STREAM_TRACE_HISTORY_ID_PREFIX = "__stream-history__";
const TASK_RUN_SHELL_TAIL_MAX_CHARS = 5000;
const TASK_RUN_SHELL_TAIL_MAX_LINES = 28;
const DRAFT_HISTORY_MAX_CHATS = 30;
const DRAFT_HISTORY_MAX_ITEMS_PER_CHAT = 50;
const DRAFT_HISTORY_MAX_ITEM_CHARS = 4000;
const ACTIVITY_SIDEBAR_DEFAULT_WIDTH = 380;
const ACTIVITY_SIDEBAR_MIN_WIDTH = 320;
const ACTIVITY_SIDEBAR_MAX_WIDTH = 560;

type StepExpansionValue = string | null;

function overlayScopeKey(chatId: number | null) {
  return chatId === null ? "pending" : `chat:${chatId}`;
}

function createClientTurnId() {
  // Shared frontend/backend turn anchor: one user send => one stable chat card lineage.
  return `turn-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function readOverlayStore() {
  if (typeof window === "undefined") return {} as Record<string, MessageItem[]>;
  try {
    const raw = window.localStorage.getItem(LOCAL_OVERLAY_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, MessageItem[]>;
    return parsed && typeof parsed === "object" ? compactOverlayStore(parsed) : {};
  } catch {
    return {};
  }
}

function trimOverlayText(value: string | undefined, limit: number) {
  if (!value) return undefined;
  const normalized = value.trim();
  if (!normalized) return undefined;
  return normalized.length > limit ? `${normalized.slice(0, Math.max(limit - 3, 0))}...` : normalized;
}

function shouldPersistOverlayMessage(message: MessageItem) {
  return Boolean(message.localOnly || message.optimisticKind || message.isStreaming);
}

function pickOverlaySteps(message: MessageItem) {
  const streamSteps = message.streamSteps || [];
  const liveSteps = streamSteps.filter((step) => step.state === "live");
  const preferredSteps = liveSteps.length > 0 ? liveSteps.slice(-OVERLAY_MAX_STEP_COUNT) : streamSteps.slice(-OVERLAY_MAX_STEP_COUNT);
  return preferredSteps.map(sanitizeOverlayStep);
}

function sanitizeOverlayStep(step: MessageStreamStep): MessageStreamStep {
  return {
    id: step.id,
    label: trimOverlayText(step.label, OVERLAY_MAX_STEP_LABEL_CHARS) || "Step",
    detail: trimOverlayText(step.detail, OVERLAY_MAX_STEP_DETAIL_CHARS),
    state: step.state,
    kind: step.kind,
    agent: step.agent,
    tool: step.tool,
    toolCallIndex: step.toolCallIndex,
    toolCallId: step.toolCallId,
    runId: step.runId,
  };
}

function sanitizeOverlayMessage(message: MessageItem): MessageItem {
  return {
    id: message.id,
    agent_id: message.agent_id,
    content: trimOverlayText(message.content, OVERLAY_MAX_CONTENT_CHARS) || "",
    message_type: message.message_type,
    created_at: message.created_at,
    agent_name: message.agent_name,
    client_turn_id: message.client_turn_id,
    metadata: message.metadata ?? null,
    isStreaming: message.isStreaming,
    statusDetail: trimOverlayText(message.statusDetail, OVERLAY_MAX_STEP_DETAIL_CHARS),
    optimisticKind: message.optimisticKind,
    localOnly: message.localOnly,
    streamSteps: pickOverlaySteps(message),
  };
}

function compactOverlayStore(store: Record<string, MessageItem[]>, preferredKeys: string[] = []) {
  const normalizedEntries = Object.entries(store)
    .map(([key, value]) => {
      const sanitizedMessages = (Array.isArray(value) ? value : [])
        .filter(shouldPersistOverlayMessage)
        .slice(-OVERLAY_MAX_MESSAGES_PER_CHAT)
        .map(sanitizeOverlayMessage)
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

  return Object.fromEntries([...preferredEntries, ...remainingEntries].slice(0, OVERLAY_MAX_CHATS));
}

function writeOverlayStore(store: Record<string, MessageItem[]>, preferredKeys: string[] = []) {
  if (typeof window === "undefined") return;
  let nextStore = compactOverlayStore(store, preferredKeys);
  const nextEntries = Object.entries(nextStore).filter(([, value]) => Array.isArray(value) && value.length > 0);
  if (nextEntries.length === 0) {
    window.localStorage.removeItem(LOCAL_OVERLAY_STORAGE_KEY);
    return;
  }

  const persist = (value: Record<string, MessageItem[]>) =>
    window.localStorage.setItem(LOCAL_OVERLAY_STORAGE_KEY, JSON.stringify(value));

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
      const fallbackStore = compactOverlayStore(
        Object.fromEntries(
          preferredKeys
            .filter((key) => nextStore[key]?.length)
            .map((key) => [key, (nextStore[key] ?? []).slice(-2).map((message) => ({
              ...sanitizeOverlayMessage(message),
              streamSteps: (message.streamSteps || []).slice(-1).map((step) => ({
                ...sanitizeOverlayStep(step),
                detail: trimOverlayText(step.detail, 120),
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

    window.localStorage.removeItem(LOCAL_OVERLAY_STORAGE_KEY);
  }
}

function readOverlayMessages(chatId: number | null) {
  const store = readOverlayStore();
  return store[overlayScopeKey(chatId)] ?? [];
}

function writeOverlayMessages(chatId: number | null, messages: MessageItem[]) {
  const store = readOverlayStore();
  const key = overlayScopeKey(chatId);
  const pendingMessages = messages.filter(shouldPersistOverlayMessage);
  if (pendingMessages.length === 0) {
    delete store[key];
  } else {
    store[key] = pendingMessages;
  }
  writeOverlayStore(store, [key, "pending"]);
}

function migrateOverlayMessages(fromChatId: number | null, toChatId: number | null) {
  const store = readOverlayStore();
  const fromKey = overlayScopeKey(fromChatId);
  const toKey = overlayScopeKey(toChatId);
  if (fromKey === toKey) return store[toKey] ?? [];

  const fromMessages = store[fromKey] ?? [];
  if (fromMessages.length === 0) return store[toKey] ?? [];

  const nextMessages = [...(store[toKey] ?? []), ...fromMessages].sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
  store[toKey] = nextMessages;
  delete store[fromKey];
  writeOverlayStore(store, [toKey]);
  return nextMessages;
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function sanitizeDraftHistoryItems(value: unknown) {
  if (!Array.isArray(value)) return [];
  const items: string[] = [];
  for (const item of value) {
    const text = typeof item === "string" ? item.trim() : "";
    if (!text) continue;
    const clipped = text.length > DRAFT_HISTORY_MAX_ITEM_CHARS ? text.slice(0, DRAFT_HISTORY_MAX_ITEM_CHARS).trimEnd() : text;
    if (!clipped || items[items.length - 1] === clipped) continue;
    items.push(clipped);
  }
  return items.slice(-DRAFT_HISTORY_MAX_ITEMS_PER_CHAT);
}

function compactDraftHistoryStore(store: Record<string, unknown>, preferredKeys: string[] = []) {
  const normalizedEntries = Object.entries(store)
    .map(([key, value]) => [key, sanitizeDraftHistoryItems(value)] as const)
    .filter(([, value]) => value.length > 0);
  const preferredSet = new Set(preferredKeys.filter(Boolean));
  const preferredEntries = normalizedEntries.filter(([key]) => preferredSet.has(key));
  const remainingEntries = normalizedEntries.filter(([key]) => !preferredSet.has(key));
  return Object.fromEntries([...preferredEntries, ...remainingEntries].slice(0, DRAFT_HISTORY_MAX_CHATS));
}

function readDraftHistoryStore() {
  if (typeof window === "undefined") return {} as Record<string, string[]>;
  try {
    const raw = window.localStorage.getItem(DRAFT_HISTORY_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? compactDraftHistoryStore(parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function writeDraftHistoryStore(store: Record<string, string[]>, preferredKeys: string[] = []) {
  if (typeof window === "undefined") return;
  const nextStore = compactDraftHistoryStore(store, preferredKeys);
  if (Object.keys(nextStore).length === 0) {
    window.localStorage.removeItem(DRAFT_HISTORY_STORAGE_KEY);
    return;
  }
  try {
    window.localStorage.setItem(DRAFT_HISTORY_STORAGE_KEY, JSON.stringify(nextStore));
  } catch {
    window.localStorage.removeItem(DRAFT_HISTORY_STORAGE_KEY);
  }
}

type ChatTabProps = {
  chat: ChatSummary | null;
  project: ProjectSummary | null;
  agents: AgentInfo[];
  messages: MessageItem[];
  optimisticMessages: MessageItem[];
  cards: ChatCardItem[];
  taskRuns: TaskRunSummary[];
  processes: ChatProcessEntry | null;
  projectBrowserIndex: ProjectBrowserIndex | null;
  projectBrowserAutoRefreshEnabled?: boolean;
  projectBrowserContentRefresh?: {
    snapshotId?: string | null;
    updatedFiles: ProjectBrowserFileItem[];
  } | null;
  liveTaskRunDetailsById: Record<number, TaskRunDetail>;
  taskActivitiesById: Record<number, TaskActivityProjection>;
  taskTimelinesById: Record<number, ChatTimelineProjection>;
  loading: boolean;
  sending: boolean;
  refreshing: boolean;
  creatingProjectFromChat: boolean;
  connectionState: "connected" | "connecting" | "disconnected";
  events: ChatEventItem[];
  expandCurrentStepByDefault: boolean;
  onSend: (content: string, options?: { clientTurnId?: string; attachments?: Array<{ file_path: string; file_name: string; file_size: number; mime_type?: string }> }) => Promise<void>;
  onOpenWorkspace: () => Promise<void>;
  onOpenSidebar: () => void;
  onOpenActivity: () => void;
  activityDrawerOpen: boolean;
  onCloseActivity: () => void;
  onOpenSettings: () => void;
  onRefresh: () => Promise<void>;
  onRefreshRuntime?: (taskRunId: number) => Promise<void>;
  onPatchSubagentRuntime?: (patch: {
    taskRunId: number;
    stepId: string;
    status?: string;
    availableActions?: string[];
    controlState?: string | null;
    terminal?: boolean;
    note?: string | null;
  }) => void;
  onSyncProject: () => Promise<void>;
  syncingProject: boolean;
  onApproveGate: (pipelineId: number) => Promise<void>;
  onRejectGate: (pipelineId: number) => Promise<void>;
  onCreateProjectFromChat: (payload: {
    name: string;
    description: string;
    agent_names: string[];
  }) => Promise<void>;
};

type SystemPromptPresentation = {
  label: string;
  markdown: string;
  copyText: string;
  mode: "full" | "delta" | "unchanged";
};

type FailureStepAnalysisContext = {
  message?: MessageItem;
  taskRun?: TaskRunSummary;
  taskRunDetail?: TaskRunDetail | null;
};

type FailureStepAnalysisHandler = (step: MessageStreamStep, context: FailureStepAnalysisContext) => void;
type TraceStepRenderMode = "message" | "task-run";

type DecoratedChatCardItem = ChatCardItem & {
  systemPromptPresentation?: SystemPromptPresentation;
};

type ToolMergeCard = {
  id: string;
  kind: "tool_merge";
  created_at: string;
  source?: string;
  agent?: string;
  tool?: string;
  count: number;
  items: DecoratedChatCardItem[];
};

type ThreadCard = DecoratedChatCardItem | ToolMergeCard;
type ProjectBrowserTab = "files" | "artifacts" | "processes" | "runtime";
type RuntimeMonitorActionState = "running" | "waiting" | "done" | "failed";
type RuntimeMonitorEventType = "message" | "delegate" | "approval" | "tool" | "background" | "handoff" | "consult" | "task";
type RuntimeMonitorEvent = {
  id: string;
  taskRunId?: number | null;
  from: string;
  action: string;
  to: string;
  result: string;
  type: RuntimeMonitorEventType;
  timestamp: string;
  state: RuntimeMonitorActionState;
};
type RuntimeMonitorSummary = {
  activeAgents: number;
  pendingApprovals: number;
  runningActions: number;
  backgroundTasks: number;
};
type RuntimeMonitorViewModel = {
  events: RuntimeMonitorEvent[];
  summary: RuntimeMonitorSummary;
};
type BrowserFileEntry = {
  id: string;
  path: string;
  source: string;
  detail: string;
  timestamp?: string;
};
type BrowserFileTreeNode = {
  id: string;
  name: string;
  path: string;
  kind: "directory" | "file";
  children: BrowserFileTreeNode[];
  source?: string;
  detail?: string;
  timestamp?: string;
};
type FileReaderState = {
  path: string;
  status: "loading" | "ready" | "error";
  data?: ProjectFileReadResponse;
  error?: string;
  mode?: "read" | "edit";
  draft?: string;
  saving?: boolean;
  saveMessage?: string;
  history?: string[];
  historyIndex?: number;
};

type InteractiveFileToolRequest = {
  path: string;
  mode: "read" | "edit";
  projectId?: number | null;
  reason?: string;
};

type FileReaderCardActions = {
  onClose?: () => void;
  onEdit: () => void;
  onDraftChange: (value: string) => void;
  onDiscard: () => void;
  onSave: () => void;
  onOpenLinkedFile?: (path: string) => void;
  onBack?: () => void;
  onForward?: () => void;
};

function isInternalToolPause(card: ThreadCard | DecoratedChatCardItem) {
  if (card.kind !== "tool_call") return false;
  if (!card.blocked) return false;
  const blockedKind = String(card.blocked_kind || "").trim().toLowerCase();
  const status = String(card.status || "").trim().toLowerCase();
  return blockedKind === "approval" || blockedKind === "timeout" || status === "approval_blocked" || status === "timeout_waiting";
}

function parseInteractiveToolJsonObject(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function parseInteractiveFileToolRequest(card: ThreadCard): InteractiveFileToolRequest | null {
  if (card.kind !== "tool_call" || card.tool !== "open_file_for_user") return null;
  const resultPayload = parseInteractiveToolJsonObject(card.result);
  const argsPayload = parseInteractiveToolJsonObject(card.arguments);
  const payload = resultPayload?.catown_interactive_tool === "file_reader_editor" ? resultPayload : argsPayload;
  const rawPath = payload?.path;
  if (typeof rawPath !== "string" || rawPath.trim() === "") return null;
  const rawMode = typeof payload?.mode === "string" ? payload.mode.toLowerCase() : "";
  const rawProjectId = typeof payload?.project_id === "number" ? payload.project_id : null;
  return {
    path: rawPath,
    mode: rawMode === "edit" ? "edit" : "read",
    projectId: rawProjectId,
    reason: typeof payload?.reason === "string" ? payload.reason : undefined,
  };
}

function isToolCardFailure(card: ThreadCard | DecoratedChatCardItem) {
  if (isInternalToolPause(card)) return false;
  return "success" in card && card.success === false;
}
type BrowserArtifactEntry = {
  id: string;
  name: string;
  type: string;
  agentName?: string | null;
  path?: string;
  stage: string;
  detail: string;
  status: string;
  timestamp?: string;
};
function ArtifactIcon({ type }: { type: string }) {
  const normalized = type.trim().toLowerCase();
  if (normalized === "adr") return <ScrollText size={14} />;
  if (normalized === "prd") return <BookOpen size={14} />;
  if (normalized === "spec") return <FileText size={14} />;
  if (normalized === "test") return <ClipboardCheck size={14} />;
  if (normalized === "report") return <FileText size={14} />;
  if (normalized === "release") return <PackageCheck size={14} />;
  if (normalized === "doc") return <FileText size={14} />;
  return <Boxes size={14} />;
}

function ProcessIcon({ kind }: { kind: ChatProcessEntry["kind"] }) {
  if (kind === "project") return <FolderTree size={15} />;
  if (kind === "chat") return <Monitor size={15} />;
  if (kind === "command") return <Shell size={15} />;
  if (kind === "subagent") return <Bot size={15} />;
  return <Workflow size={15} />;
}

function processKindLabel(kind: ChatProcessEntry["kind"]) {
  if (kind === "project") return "Project";
  if (kind === "chat") return "Chat";
  if (kind === "command") return "Shell command";
  if (kind === "subagent") return "Subagent";
  return "Task run";
}

function countRuntimeProcessNodes(node: ChatProcessEntry | null): number {
  if (!node) return 0;
  const selfCount = node.kind === "task" || node.kind === "command" || node.kind === "subagent" ? 1 : 0;
  return selfCount + node.children.reduce((total, child) => total + countRuntimeProcessNodes(child), 0);
}

function flattenRuntimeProcessNodes(node: ChatProcessEntry | null): ChatProcessEntry[] {
  if (!node) return [];
  return [node, ...node.children.flatMap((child) => flattenRuntimeProcessNodes(child))];
}

function processStatusLabel(status: ChatProcessEntry["status"]) {
  return String(status || "running").trim().toLowerCase() === "terminated" ? "Terminated" : "Running";
}

function processNodeDetail(node: ChatProcessEntry) {
  if (node.kind !== "subagent") return node.detail;
  return runtimeHandleRichLabel(node.metadata) || node.detail;
}

function ProcessTreeNode({
  node,
  agents,
  depth = 0,
  activeActionKey,
  onInspectTaskRun,
  onWaitSubagent,
  onCancelSubagent,
  onCloseSubagent,
}: {
  node: ChatProcessEntry;
  agents: AgentInfo[];
  depth?: number;
  activeActionKey?: string | null;
  onInspectTaskRun?: (taskRunId: number) => void;
  onWaitSubagent?: (taskRunId: number, stepId: string) => void;
  onCancelSubagent?: (taskRunId: number, stepId: string) => void;
  onCloseSubagent?: (taskRunId: number, stepId: string) => void;
}) {
  const runtimeChildCount = node.children.reduce((total, child) => total + countRuntimeProcessNodes(child), 0);
  const isRuntimeNode = node.kind === "task" || node.kind === "command" || node.kind === "subagent";
  const statusLabel = processStatusLabel(node.status);
  const isTerminated = statusLabel === "Terminated";
  const hasOutputDetails = node.kind === "command" && Boolean(node.output?.trim());
  const metadata = node.metadata && typeof node.metadata === "object" ? node.metadata : null;
  const resolvedProcessAgentName =
    node.agent_name
    || (typeof metadata?.["agent_name"] === "string" ? metadata["agent_name"] : null)
    || (
      node.kind === "subagent"
        ? typeof metadata?.["requested_name"] === "string"
          ? metadata["requested_name"]
          : null
        : null
    )
    || null;
  const processThemeStyle = buildAgentThemeStyle(resolvedProcessAgentName, agents);
  const processMetaChips = [
    typeof metadata?.["dispatch_kind"] === "string" ? String(metadata["dispatch_kind"]) : "",
    typeof metadata?.["control_state"] === "string" ? String(metadata["control_state"]).replace(/_/g, " ") : "",
    typeof metadata?.["source"] === "string" ? String(metadata["source"]) : "",
  ].filter(Boolean);
  const processActions = Array.isArray(metadata?.["available_actions"])
    ? (metadata?.["available_actions"] as unknown[]).filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    : [];
  const processDetail = processNodeDetail(node);
  const taskRunId =
    typeof metadata?.["task_run_id"] === "number"
      ? metadata["task_run_id"]
      : typeof node.parent_id === "string" && node.parent_id.startsWith("task-run:")
        ? Number(node.parent_id.slice("task-run:".length))
        : typeof node.id === "string" && node.id.startsWith("task-run:")
          ? Number(node.id.slice("task-run:".length))
          : null;
  const stepId = typeof metadata?.["step_id"] === "string" ? metadata["step_id"] : null;
  const canInspect = typeof taskRunId === "number" && Number.isFinite(taskRunId);
  const actionKeyBase = `${taskRunId ?? "na"}:${stepId ?? node.id}`;
  const rowContent = (
    <>
      <div className="browser-entry__icon"><ProcessIcon kind={node.kind} /></div>
      <div className="process-tree__main">
        <span className="process-tree__label" title={node.label}>{node.label}</span>
        <span className="process-tree__kind">{processKindLabel(node.kind)}</span>
        {isRuntimeNode ? <span className={`process-tree__state ${isTerminated ? "process-tree__state--terminated" : ""}`}>{statusLabel}</span> : null}
        {node.kind === "subagent" && processDetail ? <span className="process-tree__meta">{oneLinePreview(processDetail, "", 140)}</span> : null}
        {typeof node.pid === "number" ? <span className="process-tree__meta">pid {node.pid}</span> : null}
        {runtimeChildCount > 0 ? <span className="process-tree__meta">{runtimeChildCount} child</span> : null}
        {node.timestamp ? <span className="process-tree__meta">{formatTime(node.timestamp)}</span> : null}
        {processMetaChips.map((chip) => <span key={chip} className="process-tree__meta process-tree__meta--chip">{chip}</span>)}
        {processActions.length > 0 ? <span className="process-tree__meta process-tree__meta--actions">{processActions.join(" · ")}</span> : null}
      </div>
      <div className="process-tree__actions">
        {canInspect ? (
          <button
            type="button"
            className="chat-copy-inline-btn"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onInspectTaskRun?.(taskRunId as number);
            }}
            title="Inspect task run"
          >
            Inspect
          </button>
        ) : null}
        {typeof taskRunId === "number" && stepId && processActions.includes("wait") ? (
          <button
            type="button"
            className="chat-copy-inline-btn"
            disabled={activeActionKey === `${actionKeyBase}:wait`}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onWaitSubagent?.(taskRunId, stepId);
            }}
            title="Wait for subagent update"
          >
            {activeActionKey === `${actionKeyBase}:wait` ? "Waiting" : "Wait"}
          </button>
        ) : null}
        {typeof taskRunId === "number" && stepId && processActions.includes("cancel") ? (
          <button
            type="button"
            className="chat-copy-inline-btn"
            disabled={activeActionKey === `${actionKeyBase}:cancel`}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onCancelSubagent?.(taskRunId, stepId);
            }}
            title="Cancel subagent handle"
          >
            {activeActionKey === `${actionKeyBase}:cancel` ? "Cancelling" : "Cancel"}
          </button>
        ) : null}
        {typeof taskRunId === "number" && stepId && processActions.includes("close") ? (
          <button
            type="button"
            className="chat-copy-inline-btn"
            disabled={activeActionKey === `${actionKeyBase}:close`}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onCloseSubagent?.(taskRunId, stepId);
            }}
            title="Close subagent handle"
          >
            {activeActionKey === `${actionKeyBase}:close` ? "Closing" : "Close"}
          </button>
        ) : null}
        {isRuntimeNode ? <CopyTextButton content={node.label} title="Copy process label" /> : <span aria-hidden="true" />}
      </div>
    </>
  );
  return (
    <div
      className={`process-tree__node ${resolvedProcessAgentName ? "process-tree__node--agent" : ""}`}
      style={{ "--process-depth": depth, ...(processThemeStyle || {}) } as CSSProperties}
    >
      {hasOutputDetails ? (
        <details className="browser-entry browser-entry--process process-tree__details">
          <summary className="process-tree__row process-tree__summary">
            {rowContent}
          </summary>
          <div className="process-tree__output-panel">
            <p>{oneLinePreview(processNodeDetail(node), "Shell process is running.", 160)}</p>
            <pre className="browser-entry__output">{node.output}</pre>
          </div>
        </details>
      ) : (
        <div className="browser-entry browser-entry--process process-tree__row">
          {rowContent}
        </div>
      )}
      {node.children.length > 0 ? (
        <div className="process-tree__children">
          {node.children.map((child) => (
            <ProcessTreeNode
              key={child.id}
              node={child}
              agents={agents}
              depth={depth + 1}
              activeActionKey={activeActionKey}
              onInspectTaskRun={onInspectTaskRun}
              onWaitSubagent={onWaitSubagent}
              onCancelSubagent={onCancelSubagent}
              onCloseSubagent={onCloseSubagent}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
function browserFileTypeLabel(path: string) {
  const normalized = normalizeBrowserPath(path).toLowerCase();
  const name = browserPathBaseName(normalized);
  if (name === "dockerfile" || name.endsWith(".dockerfile")) return "Dockerfile";
  if (name === "makefile") return "Makefile";
  if (name === ".gitignore") return "Git ignore";
  if (name === ".env" || name.startsWith(".env.")) return "Environment";
  if (name === "package.json") return "Node package";
  if (name === "tsconfig.json") return "TypeScript config";
  if (name === "vite.config.ts" || name === "vite.config.js") return "Vite config";
  if (/\.(test|spec)\.(ts|tsx|js|jsx)$/.test(normalized)) return "Frontend test";
  if (/\.(test|spec)\.py$|(^|\/)test_.*\.py$|_test\.py$/.test(normalized)) return "Python test";
  if (/\.tsx$/.test(normalized)) return "React TypeScript";
  if (/\.jsx$/.test(normalized)) return "React JavaScript";
  if (/\.ts$/.test(normalized)) return "TypeScript";
  if (/\.js$/.test(normalized)) return "JavaScript";
  if (/\.py$/.test(normalized)) return "Python";
  if (/\.sh$/.test(normalized)) return "Shell script";
  if (/\.css$/.test(normalized)) return "CSS";
  if (/\.html?$/.test(normalized)) return "HTML";
  if (/\.mdx$/.test(normalized)) return "MDX";
  if (/\.md$/.test(normalized)) return "Markdown";
  if (/\.ya?ml$/.test(normalized)) return "YAML";
  if (/\.json$/.test(normalized)) return "JSON";
  if (/\.toml$/.test(normalized)) return "TOML";
  if (/\.sql$/.test(normalized)) return "SQL";
  if (/\.lock$/.test(normalized) || name.endsWith("-lock.json")) return "Lockfile";
  if (/\.svg$/.test(normalized)) return "SVG";
  if (/\.(png|jpe?g|gif|webp|ico)$/.test(normalized)) return "Image";
  if (/\.pdf$/.test(normalized)) return "PDF";
  if (/\.(zip|tar|tgz|gz)$/.test(normalized)) return "Archive";
  if (/\.txt$/.test(normalized)) return "Text";
  return "File";
}

function formatFileSize(bytes: number | null | undefined) {
  const value = Math.max(bytes ?? 0, 0);
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(2)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function FileTreeIcon({ node }: { node: BrowserFileTreeNode }) {
  if (node.kind === "directory") return <Folder size={14} />;
  const path = normalizeBrowserPath(node.path).toLowerCase();
  if (/(^|\/)(readme|guide|docs?)\b|\.mdx?$|\.txt$/.test(path)) return <FileText size={14} />;
  if (/\badr[-_.]|\bprd\b|\bspec\b|requirements?|proposal|report|release[-_]?notes?/.test(path)) {
    return <ArtifactIcon type={classifyBrowserArtifact(path) || "Doc"} />;
  }
  if (/\.(json|ya?ml|toml|sql|lock)$/i.test(path)) return <ScrollText size={14} />;
  if (/\.(test|spec)\.(ts|tsx|js|jsx|py)$|test_|_test\.py$/.test(path)) return <ClipboardCheck size={14} />;
  if (/\.(ts|tsx|js|jsx|py|sh|css|html)$/.test(path)) return <FileText size={14} />;
  return <File size={14} />;
}
type ParsedLlmConversation = {
  meta: string;
  outbound: string;
  inbound: string;
};

type ThreadItem =
  | {
      id: string;
      sortKey: string;
      kind: "message";
      message: MessageItem;
    }
  | {
      id: string;
      sortKey: string;
      kind: "task_run";
      taskRun: TaskRunSummary;
      detail: TaskRunDetail | null;
      cards: ThreadCard[];
    }
  | {
      id: string;
      sortKey: string;
      kind: "card";
      card: ThreadCard;
    }
  | {
      id: string;
      sortKey: string;
      kind: "activity_batch";
      cards: ThreadCard[];
    };

function threadItemAgentName(item: ThreadItem, agents: AgentInfo[]) {
  switch (item.kind) {
    case "message":
      return item.message.agent_name || null;
    case "task_run":
      return item.taskRun.target_agent_name || resolveTaskRunActorName(item.taskRun, agents);
    case "card":
      return cardActorName(item.card);
    case "activity_batch":
      return (
        [...item.cards]
          .reverse()
          .map((card) => cardActorName(card))
          .find((name) => name !== "system") || null
      );
    default:
      return null;
  }
}

const EMPTY_THREAD_CARDS: ThreadCard[] = [];
const llmConversationMarkdownCache = new Map<string, ParsedLlmConversation>();

function rememberLlmConversationMarkdown(content: string, parsed: ParsedLlmConversation) {
  if (content.length < 256) return;
  if (llmConversationMarkdownCache.size >= 120) {
    const oldestKey = llmConversationMarkdownCache.keys().next().value;
    if (typeof oldestKey === "string") {
      llmConversationMarkdownCache.delete(oldestKey);
    }
  }
  llmConversationMarkdownCache.set(content, parsed);
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isKnownMonitorActor(value: string | null | undefined) {
  const normalized = (value || "").trim().toLowerCase();
  return normalized.length > 0 && normalized !== "system" && normalized !== "user";
}

function runtimeMonitorActorLabel(value: string | null | undefined, agents: AgentInfo[]) {
  const normalized = (value || "").trim();
  if (!normalized) return "System";
  if (normalized.toLowerCase() === "user") return "User";
  return resolveAgentLabel(normalized, agents);
}

function runtimeMonitorActionStateLabel(state: RuntimeMonitorActionState) {
  switch (state) {
    case "running":
      return "running";
    case "waiting":
      return "waiting";
    case "failed":
      return "failed";
    default:
      return "done";
  }
}

function runtimeMonitorStateFromTimeline(step: ChatTimelineStep): RuntimeMonitorActionState {
  if (step.state === "error" || step.phase === "failed") return "failed";
  if (step.kind === "approval" && ["requested", "continued"].includes(step.phase)) return "waiting";
  if (["started", "request", "response_started", "dispatched", "resumed", "waiting", "created"].includes(step.phase)) return "running";
  return "done";
}

function runtimeMonitorStateFromCard(card: ThreadCard): RuntimeMonitorActionState {
  const state = compactCardState(card, false);
  if (state === "error") return "failed";
  if (state === "blocked") return "waiting";
  if (state === "live") return isInternalToolPause(card) ? "waiting" : "running";
  return "done";
}

function buildRuntimeMonitorViewModel({
  agents,
  taskRuns,
  taskActivitiesById,
  taskTimelinesById,
  cards,
  processes,
  pendingApprovalItemsByTaskRunId,
}: {
  agents: AgentInfo[];
  taskRuns: TaskRunSummary[];
  taskActivitiesById: Record<number, TaskActivityProjection>;
  taskTimelinesById: Record<number, ChatTimelineProjection>;
  cards: ThreadCard[];
  processes: ChatProcessEntry | null;
  pendingApprovalItemsByTaskRunId: Record<number, ApprovalQueueItem[]>;
}): RuntimeMonitorViewModel {
  const events: RuntimeMonitorEvent[] = [];
  const seenEventIds = new Set<string>();

  const pushEvent = (event: RuntimeMonitorEvent | null) => {
    if (!event || seenEventIds.has(event.id)) return;
    seenEventIds.add(event.id);
    events.push(event);
  };

  const normalizeEventFrom = (value: string | null | undefined) => value?.trim() || "System";
  const normalizeEventTo = (value: string | null | undefined) => value?.trim() || "Runtime";

  for (const run of taskRuns) {
    const timeline = taskTimelinesById[run.id];
    const activity = taskActivitiesById[run.id];
    const pendingApprovals = pendingApprovalItemsByTaskRunId[run.id] ?? [];
    const fallbackActor = resolveTaskRunActorName(run, agents);

    for (const step of timeline?.steps ?? []) {
      const actor = (step.actor || fallbackActor || "").trim();
      const facts = readRecord(step.facts);
      const timestamp = step.occurred_at || step.recorded_at || run.updated_at || run.created_at || new Date().toISOString();

      if (step.event_type === "agent_message" || step.event_type === "handoff_created") {
        const fromAgent =
          readTextField(facts, "from_agent")
          || readTextField(facts, "agent_name")
          || actor
          || fallbackActor;
        const toAgent =
          readTextField(facts, "to_agent")
          || readTextField(facts, "target_agent_name")
          || readTextField(facts, "agent")
          || "Agent";
        pushEvent({
          id: `timeline:${run.id}:${step.id}:message`,
          taskRunId: run.id,
          from: normalizeEventFrom(fromAgent),
          action: step.event_type === "handoff_created" ? "delegate" : "msg",
          to: normalizeEventTo(toAgent),
          result: oneLinePreview(
            readTextField(facts, "content_preview")
            || step.summary
            || step.detail_content
            || timelineStepDetail(step),
            step.event_type === "handoff_created" ? "Delegated work dispatched." : "Agent message recorded.",
            180,
          ),
          type: step.event_type === "handoff_created" ? "delegate" : "message",
          timestamp,
          state: step.event_type === "handoff_created" ? "running" : "done",
        });
      }

      if (step.event_type === "delegated_task_dispatched") {
        const fromAgent = readTextField(facts, "from_agent") || actor || fallbackActor;
        const toAgent = readTextField(facts, "to_agent") || readTextField(facts, "target_agent_name") || "Agent";
        const taskTitle = readTextField(facts, "task_title") || "Delegated task";
        pushEvent({
          id: `timeline:${run.id}:${step.id}:delegate`,
          taskRunId: run.id,
          from: normalizeEventFrom(fromAgent),
          action: "delegate",
          to: normalizeEventTo(toAgent),
          result: oneLinePreview(step.summary || readTextField(facts, "task_description") || taskTitle, "Delegated work dispatched.", 180),
          type: "delegate",
          timestamp,
          state: step.phase === "waiting" ? "waiting" : runtimeMonitorStateFromTimeline(step),
        });
      }

      if (step.kind === "tool") {
        const toolName = readTextField(facts, "tool_name") || readTextField(facts, "tool") || "Tool";
        pushEvent({
          id: `timeline:${run.id}:${step.id}:tool`,
          taskRunId: run.id,
          from: normalizeEventFrom(actor || fallbackActor),
          action: toolName.toLowerCase() === "run_shell" ? "background" : "tool",
          to: normalizeEventTo(toolName),
          result: oneLinePreview(step.summary || step.detail_content || timelineStepDetail(step), `${toolName} recorded.`, 180),
          type: toolName.toLowerCase() === "run_shell" ? "background" : "tool",
          timestamp,
          state: runtimeMonitorStateFromTimeline(step),
        });
      }

      if (step.kind === "approval") {
        const toolName = readTextField(facts, "tool_name") || readTextField(facts, "target_name") || "Approval";
        pushEvent({
          id: `timeline:${run.id}:${step.id}:approval`,
          taskRunId: run.id,
          from: normalizeEventFrom(actor || fallbackActor),
          action: "approval",
          to: "User",
          result: oneLinePreview(step.summary || step.detail_content || timelineStepDetail(step), "Approval requested.", 180),
          type: "approval",
          timestamp,
          state: runtimeMonitorStateFromTimeline(step),
        });
      }

      if (step.event_type === "task_run_waiting_for_delegated_work") {
        pushEvent({
          id: `timeline:${run.id}:${step.id}:waiting`,
          taskRunId: run.id,
          from: normalizeEventFrom(actor || fallbackActor),
          action: "wait",
          to: "Delegated work",
          result: oneLinePreview(step.summary || "Waiting for delegated work.", "Waiting for delegated work.", 180),
          type: "task",
          timestamp,
          state: "waiting",
        });
      }
    }

    for (const item of pendingApprovals) {
      const actor = item.agent_name?.trim() || fallbackActor;
      const timestamp = item.updated_at || item.created_at || run.updated_at || run.created_at || new Date().toISOString();
      const title = item.target_name?.trim() || item.title || "Approval";
      pushEvent({
        id: `approval-item:${item.id}`,
        taskRunId: run.id,
        from: normalizeEventFrom(actor),
        action: "approval",
        to: "User",
        result: oneLinePreview(item.summary || item.title || "Approval requested.", "Approval requested.", 180),
        type: "approval",
        timestamp,
        state: "waiting",
      });
    }

    for (const row of taskActivityBackgroundRows(activity)) {
      const timestamp = activity?.updated_at || run.updated_at || run.created_at || new Date().toISOString();
      const actionType: RuntimeMonitorEventType =
        row.key.includes("consult")
          ? "consult"
          : row.key.includes("subagent")
            ? "handoff"
            : row.key.includes("pipeline") || row.key.includes("handoff")
              ? "task"
              : "background";
      pushEvent({
        id: `activity:${run.id}:${row.key}`,
        taskRunId: run.id,
        from: normalizeEventFrom(fallbackActor),
        action: row.label.toLowerCase(),
        to: row.label,
        result: oneLinePreview(row.detail, `${row.label} active.`, 180),
        type: actionType,
        timestamp,
        state: actionType === "task" ? "waiting" : "running",
      });
    }
  }

  for (const card of cards) {
    const actor = cardActorName(card);
    if (!isKnownMonitorActor(actor)) continue;
    const timestamp = card.created_at || new Date().toISOString();
    if (card.kind === "agent_message") {
      pushEvent({
        id: `card:${card.id}:message`,
        taskRunId: typeof card.run_id === "number" ? card.run_id : null,
        from: normalizeEventFrom(card.from_agent || actor),
        action: "msg",
        to: normalizeEventTo(card.to_agent || "Agent"),
        result: oneLinePreview(card.content || card.summary, "Agent handoff.", 180),
        type: "message",
        timestamp,
        state: "done",
      });
      continue;
    }
    if (card.kind === "consult_call") {
      pushEvent({
        id: `card:${card.id}:consult`,
        taskRunId: typeof card.run_id === "number" ? card.run_id : null,
        from: normalizeEventFrom(actor),
        action: "consult",
        to: normalizeEventTo(card.target_agent || "Consult"),
        result: oneLinePreview(consultCardBody(card), "Consult request recorded.", 180),
        type: "consult",
        timestamp,
        state: runtimeMonitorStateFromCard(card),
      });
      continue;
    }
    if (card.kind === "tool_call" || card.kind === "tool_merge") {
      const toolName = card.kind === "tool_merge" ? card.tool || "Tool" : card.tool || "Tool";
      pushEvent({
        id: `card:${card.id}:tool`,
        taskRunId: typeof card.run_id === "number" ? card.run_id : null,
        from: normalizeEventFrom(actor),
        action: toolName.toLowerCase() === "run_shell" ? "background" : "tool",
        to: normalizeEventTo(toolName),
        result: oneLinePreview(compactCardSummary(card), `${toolName} recorded.`, 180),
        type: toolName.toLowerCase() === "run_shell" ? "background" : "tool",
        timestamp,
        state: runtimeMonitorStateFromCard(card),
      });
      continue;
    }
    if (card.kind === "agent_error") {
      pushEvent({
        id: `card:${card.id}:error`,
        taskRunId: typeof card.run_id === "number" ? card.run_id : null,
        from: normalizeEventFrom(actor),
        action: "error",
        to: "Runtime",
        result: oneLinePreview(card.error || card.summary || card.content, "Agent error recorded.", 180),
        type: "task",
        timestamp,
        state: "failed",
      });
    }
  }

  for (const node of flattenRuntimeProcessNodes(processes)) {
    if (!["task", "command", "subagent"].includes(node.kind)) continue;
    const actor =
      (node.agent_name || "").trim()
      || (readTextField(readRecord(node.metadata), "agent_name") || "")
      || (node.kind === "subagent" ? readTextField(readRecord(node.metadata), "requested_name") || "" : "")
      || "System";
    const metadata = readRecord(node.metadata);
    const timestamp = node.timestamp || new Date().toISOString();
    const state = String(node.status || "").trim().toLowerCase() === "terminated" ? "done" : "running";
    const type: RuntimeMonitorEventType =
      node.kind === "command"
        ? "background"
        : node.kind === "subagent"
          ? "handoff"
          : "task";
    pushEvent({
      id: `process:${node.id}`,
      taskRunId:
        readNumber(metadata?.task_run_id)
        ?? (typeof node.id === "string" && node.id.startsWith("task-run:") ? Number(node.id.slice("task-run:".length)) : null),
      from: normalizeEventFrom(actor),
      action: node.kind === "command" ? "background" : node.kind,
      to: normalizeEventTo(node.label),
      result: oneLinePreview(processNodeDetail(node), node.detail || "Runtime process active.", 180),
      type,
      timestamp,
      state,
    });
  }

  const orderedEvents = [...events].sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime());
  const summary = orderedEvents.reduce<RuntimeMonitorSummary>((acc, event) => {
    if (event.state === "running" || event.state === "waiting") acc.runningActions += 1;
    if (event.type === "approval" && event.state === "waiting") acc.pendingApprovals += 1;
    if (event.type === "background" && event.state === "running") acc.backgroundTasks += 1;
    return acc;
  }, {
    activeAgents: new Set(orderedEvents.filter((event) => event.from && event.from !== "System").map((event) => event.from)).size,
    pendingApprovals: 0,
    runningActions: 0,
    backgroundTasks: 0,
  });

  return {
    events: orderedEvents.slice(0, 40),
    summary,
  };
}

function RuntimeMonitorSection({
  viewModel,
  agents,
  onInspectTaskRun,
}: {
  viewModel: RuntimeMonitorViewModel;
  agents: AgentInfo[];
  onInspectTaskRun?: (taskRunId: number) => void;
}) {
  if (viewModel.events.length === 0) {
    return <div className="empty-card">Runtime relationships will appear after agents start exchanging messages, tools, approvals, or background work.</div>;
  }

  return (
    <>
      <div className="runtime-monitor__summary" aria-label="Runtime summary">
        {[
          { key: "agents", label: "Active agents", value: viewModel.summary.activeAgents },
          { key: "approvals", label: "Pending approvals", value: viewModel.summary.pendingApprovals },
          { key: "actions", label: "Running actions", value: viewModel.summary.runningActions },
          { key: "background", label: "Background tasks", value: viewModel.summary.backgroundTasks },
        ].map((item) => (
          <div key={item.key} className="runtime-monitor__summary-card">
            <strong>{item.value}</strong>
            <span>{item.label}</span>
          </div>
        ))}
      </div>

      <div className="runtime-monitor__timeline" aria-label="Runtime timeline">
        {viewModel.events.map((event) => (
          <article
            key={event.id}
            className={`runtime-monitor__timeline-item runtime-monitor__timeline-item--${event.state} runtime-monitor__timeline-item--${event.type}`}
            style={buildAgentThemeStyle(event.from, agents)}
          >
            <div className="runtime-monitor__timeline-time">
              <strong>{formatTime(event.timestamp)}</strong>
              {typeof event.taskRunId === "number" ? (
                <button
                  type="button"
                  className="chat-copy-inline-btn"
                  onClick={() => onInspectTaskRun?.(event.taskRunId as number)}
                  title="Inspect task run"
                >
                  Run {event.taskRunId}
                </button>
              ) : null}
            </div>
            <div className="runtime-monitor__timeline-grid">
              <div className="runtime-monitor__quad">
                <span>from</span>
                <strong>{runtimeMonitorActorLabel(event.from, agents)}</strong>
              </div>
              <div className="runtime-monitor__quad">
                <span>action</span>
                <strong>{event.action}</strong>
              </div>
              <div className="runtime-monitor__quad">
                <span>to</span>
                <strong>{runtimeMonitorActorLabel(event.to, agents)}</strong>
              </div>
              <div className="runtime-monitor__quad runtime-monitor__quad--result">
                <span>result</span>
                <strong>{event.result}</strong>
              </div>
            </div>
            <div className="runtime-monitor__timeline-footer">
              <span className={`runtime-monitor__state-pill runtime-monitor__state-pill--${event.state}`}>{runtimeMonitorActionStateLabel(event.state)}</span>
              <span className="runtime-monitor__timeline-type">{event.type}</span>
            </div>
          </article>
        ))}
      </div>
    </>
  );
}

function formatMessageCopyBlock(message: MessageItem) {
  const sender = message.agent_name || "You";
  const timestamp = new Date(message.created_at).toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `[${timestamp}] ${sender}\n${message.content.trim()}`;
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function renderAgentAvatarContent(agentName: string, agents: AgentInfo[]) {
  const theme = getAgentTheme(agentName, agents);
  const iconProps = { size: 16, strokeWidth: 1.9, "aria-hidden": true as const };

  switch (theme?.agentKey) {
    case "analyst":
      return <Search {...iconProps} />;
    case "architect":
      return <Workflow {...iconProps} />;
    case "developer":
      return <Braces {...iconProps} />;
    case "tester":
      return <TestTube2 {...iconProps} />;
    case "release":
      return <PackageCheck {...iconProps} />;
    case "valet":
      return <Bot {...iconProps} />;
    default:
      return <span>{initials(agentName)}</span>;
  }
}

function toneLabel(tone: ChatEventItem["tone"]) {
  switch (tone) {
    case "success":
      return "Success";
    case "warning":
      return "Warning";
    case "error":
      return "Error";
    case "info":
      return "Info";
    default:
      return "Event";
  }
}

function formatTaskRunKind(value: string | undefined) {
  if (!value) return "Task run";
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function formatTaskRunStatus(value: string | undefined) {
  if (!value) return "Unknown";
  return value[0]?.toUpperCase() + value.slice(1);
}

function taskRunStatusTone(status: string | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "completed") return "success";
  if (normalized === "failed") return "error";
  if (normalized === "running") return "info";
  return "neutral";
}

function taskRunEventTone(eventType: string | undefined) {
  const normalized = (eventType || "").toLowerCase();
  if (normalized.includes("failed") || normalized.includes("error")) return "error";
  if (normalized.includes("completed")) return "success";
  if (normalized.includes("handoff") || normalized.includes("tool_round")) return "warning";
  return "neutral";
}

function formatTaskRunEventType(value: string | undefined) {
  if (!value) return "Event";
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function isInternalContinuationSummary(value: string | null | undefined) {
  const normalized = (value || "").toLowerCase();
  if (!normalized) return false;
  return (
    normalized.includes("rebuild_turn_state_from_tool_round") ||
    normalized.includes("protocol_tail") ||
    normalized.includes("prior_round_summaries") ||
    normalized.includes("continue agent turn · via") ||
    normalized.includes("continue agent turn - via") ||
    normalized.includes(" · via ") ||
    normalized.includes(" - via ")
  );
}

function isInternalTaskRunEventSummary(value: string | null | undefined) {
  const normalized = (value || "").trim().toLowerCase();
  if (!normalized) return false;
  return (
    isInternalContinuationSummary(normalized) ||
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
  if (!normalized || isInternalTaskRunEventSummary(normalized)) return null;
  return normalized;
}

function userFacingTaskRunFailureSummary(value: string | null | undefined) {
  const normalized = value?.trim();
  if (!normalized || isInternalTaskRunEventSummary(normalized)) return null;
  return normalized;
}

function readTextField(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readTaskRunEventPayload(event: TaskRunEvent | null | undefined) {
  return event?.payload && typeof event.payload === "object" ? event.payload as Record<string, unknown> : null;
}

function eventSummary(event: TaskRunEvent | null | undefined) {
  return userFacingTaskRunSummary(typeof event?.summary === "string" ? event.summary : null);
}

function latestTaskRunEvent(
  detail: TaskRunDetail | null,
  predicate: (event: TaskRunEvent, payload: Record<string, unknown> | null) => boolean,
) {
  const events = detail?.events ?? [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (predicate(event, readTaskRunEventPayload(event))) return event;
  }
  return null;
}

function latestAgentResponse(detail: TaskRunDetail | null) {
  const checkpointPreview =
    typeof detail?.checkpoint_snapshot?.latest_agent_turn?.response_preview === "string"
      ? detail.checkpoint_snapshot.latest_agent_turn.response_preview.trim()
      : "";
  const event = latestTaskRunEvent(
    detail,
    (candidate, payload) => candidate.event_type === "agent_turn_completed" && Boolean(readTextField(payload, "response_preview")),
  );
  const payload = readTaskRunEventPayload(event);
  const response = readTextField(payload, "response_preview") || checkpointPreview || null;
  return response
    ? {
        agent: event?.agent_name || detail?.checkpoint_snapshot?.latest_agent_turn?.agent_name || null,
        response,
      }
    : null;
}

function latestToolRoundResult(detail: TaskRunDetail | null) {
  const event = latestTaskRunEvent(detail, (candidate, payload) => {
    const turnState = payload?.turn_local_state;
    return candidate.event_type === "tool_round_recorded" && Boolean(turnState && typeof turnState === "object");
  });
  const payload = readTaskRunEventPayload(event);
  const turnState = payload?.turn_local_state && typeof payload.turn_local_state === "object"
    ? payload.turn_local_state as Record<string, unknown>
    : null;
  const toolResults = Array.isArray(turnState?.tool_results)
    ? turnState.tool_results.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
  const latest = toolResults[toolResults.length - 1] ?? null;
  if (!latest) return null;
  return {
    agent: event?.agent_name || null,
    toolName: readTextField(latest, "tool_name") || readTextField(payload, "tool_name"),
    result: readTextField(latest, "result") || readTextField(latest, "blocked_reason"),
    status: readTextField(latest, "status") || (latest.success === false ? "failed" : latest.success === true ? "succeeded" : null),
    success: typeof latest.success === "boolean" ? latest.success : null,
  };
}

function latestStartedToolCall(detail: TaskRunDetail | null) {
  const event = latestTaskRunEvent(detail, (candidate) => candidate.event_type === "tool_call_started");
  const payload = readTaskRunEventPayload(event);
  if (!event && !payload) return null;
  return {
    agent: event?.agent_name || null,
    toolName: readTextField(payload, "tool_name"),
    argumentsText: readTextField(payload, "arguments"),
  };
}

function latestSubtaskDispatch(detail: TaskRunDetail | null) {
  const event = latestTaskRunEvent(detail, (candidate) =>
    ["scheduler_step_dispatched", "scheduler_step_resumed", "handoff_created"].includes(candidate.event_type),
  );
  const payload = readTaskRunEventPayload(event);
  if (!event) return null;
  const stepState = payload?.step_state && typeof payload.step_state === "object"
    ? payload.step_state as Record<string, unknown>
    : null;
  return {
    eventType: event.event_type,
    agent: event.agent_name || readTextField(payload, "agent_name") || readTextField(stepState, "agent_name"),
    dispatchKind: readTextField(payload, "dispatch_kind") || readTextField(stepState, "dispatch_kind"),
    summary: eventSummary(event),
  };
}

function latestSubtaskResult(detail: TaskRunDetail | null) {
  const event = latestTaskRunEvent(detail, (candidate) => candidate.event_type === "scheduler_step_completed");
  const payload = readTaskRunEventPayload(event);
  if (!event) return null;
  return {
    agent: event.agent_name || readTextField(payload, "agent_name"),
    summary: eventSummary(event),
    completedWithOutput: payload?.completed_with_output === true,
  };
}

function chatFacingTaskRunText(taskRun: TaskRunSummary, detail: TaskRunDetail | null, actorName = "Agent") {
  const agentResponse = latestAgentResponse(detail);
  const toolResult = latestToolRoundResult(detail);
  const toolCall = latestStartedToolCall(detail);
  const subtaskResult = latestSubtaskResult(detail);
  const subtaskDispatch = latestSubtaskDispatch(detail);
  const userRequest = typeof taskRun.user_request === "string" && taskRun.user_request.trim()
    ? taskRun.user_request.trim()
    : null;
  const taskSummary = userFacingTaskRunSummary(taskRun.summary);

  return {
    userRequest,
    agentResponse,
    toolResult,
    toolCall,
    subtaskResult,
    subtaskDispatch,
  terminalSummary: taskSummary || agentResponse?.response || userRequest,
    actor: agentResponse?.agent || actorName || "Agent",
  };
}

function timelineStepLabel(step: ChatTimelineStep) {
  const actor = step.actor?.trim();
  if (step.kind === "llm") {
    const resolvedActor = actor || "Agent";
    if (step.phase === "request") return llmOutboundStepLabel(resolvedActor);
    return llmInboundStepLabel(resolvedActor);
  }
  const kind = (step.kind || "event").replace(/_/g, " ");
  const phase = (step.phase || step.event_type || "recorded").replace(/_/g, " ");
  if (actor) return `${actor} ${phase}`;
  return `${kind} ${phase}`.trim();
}

function timelineStepDetail(step: ChatTimelineStep) {
  const facts = step.facts || {};
  if (step.kind === "llm" && step.phase === "request") {
    const parts: string[] = [];
    const model = typeof facts["model"] === "string" ? facts["model"].trim() : "";
    const messageCount = typeof facts["prompt_message_count"] === "number" ? facts["prompt_message_count"] : null;
    const promptPreview = typeof facts["prompt_preview"] === "string" ? facts["prompt_preview"].trim() : "";
    if (model) parts.push(`Model: ${model}`);
    if (messageCount !== null) parts.push(`Messages: ${messageCount}`);
    if (promptPreview) parts.push(`User: ${oneLinePreview(promptPreview, "", 120)}`);
    if (parts.length > 0) return parts.join(" · ");
  }
  return step.event_type;
}

function timelineStepToStreamStep(step: ChatTimelineStep, taskRunId: number): MessageStreamStep {
  const toolName =
    typeof step.facts?.["tool_name"] === "string"
      ? step.facts["tool_name"]
      : typeof step.facts?.["tool"] === "string"
        ? step.facts["tool"]
        : undefined;
  const toolCallId = typeof step.facts?.["tool_call_id"] === "string" ? step.facts["tool_call_id"] : null;
  const kind = step.kind === "llm"
    ? step.phase === "request"
      ? ("llm_outbound" as const)
      : ("llm_inbound" as const)
    : step.kind === "tool"
      ? ("tool_call" as const)
      : undefined;

  return {
    id: step.id,
    label: step.summary || timelineStepLabel(step),
    detail: timelineStepDetail(step),
    detailContent: step.detail_content || step.summary || undefined,
    state: step.state,
    kind,
    agent: step.actor || undefined,
    tool: toolName,
    toolCallId,
    runId: taskRunId,
  };
}

function readTaskRunContinuationToolName(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  fallbackToolName?: string | null,
) {
  const checkpointCursor = detail?.checkpoint_snapshot?.continuation_cursor;
  const cursor = detail?.continuation_cursor ?? taskRun.continuation_cursor ?? checkpointCursor;
  const toolName =
    fallbackToolName ||
    cursor?.tool_name ||
    checkpointCursor?.tool_name ||
    null;
  if (toolName) return toolName;

  const toolNames = cursor?.tool_names ?? checkpointCursor?.tool_names ?? null;
  if (Array.isArray(toolNames) && toolNames.length > 0) {
    return toolNames[toolNames.length - 1] || null;
  }
  return null;
}

function describeRunningTaskRunFallback(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  activity: TaskActivityProjection | null,
  actorName = "Agent",
  options?: {
    latestEventType?: string | null;
    latestToolName?: string | null;
  },
) {
  const projectedLatestTurn = activity?.latest_agent_turn_preview?.trim();
  if (projectedLatestTurn) {
    return oneLinePreview(projectedLatestTurn, "", 132);
  }
  const projectedContinuation = activity?.continuation_state_summary?.trim();
  if (projectedContinuation) {
    return oneLinePreview(projectedContinuation, "", 132);
  }
  const projectedSummary = activity?.summary?.trim();
  if (projectedSummary) {
    return oneLinePreview(projectedSummary, "", 132);
  }
  const eventType = (options?.latestEventType || detail?.checkpoint_snapshot?.latest_event_type || taskRun.latest_continuation_event_type || "")
    .toLowerCase();
  const toolName = readTaskRunContinuationToolName(taskRun, detail, options?.latestToolName);

  if (eventType === "tool_round_recorded" || eventType === "approval_queue_item_followup_triggered") {
    return [`Event: ${eventType}`, toolName ? `Tool: ${toolName}` : ""].filter(Boolean).join("\n");
  }
  if (eventType === "tool_call_started") {
    return [`Event: ${eventType}`, toolName ? `Tool: ${toolName}` : ""].filter(Boolean).join("\n");
  }
  if (eventType.includes("agent_turn")) {
    return `Event: ${eventType}`;
  }
  if (toolName) return `Tool: ${toolName}`;
  return "";
}

function compareTaskRunsForSidebarSelection(left: TaskRunSummary, right: TaskRunSummary) {
  const leftPending = Number(left.pending_approval_count || 0);
  const rightPending = Number(right.pending_approval_count || 0);
  if (leftPending !== rightPending) return rightPending - leftPending;

  const leftRunning = (left.status || "").toLowerCase() === "running" ? 1 : 0;
  const rightRunning = (right.status || "").toLowerCase() === "running" ? 1 : 0;
  if (leftRunning !== rightRunning) return rightRunning - leftRunning;

  const leftUpdated = new Date(left.updated_at || left.created_at || 0).getTime();
  const rightUpdated = new Date(right.updated_at || right.created_at || 0).getTime();
  return rightUpdated - leftUpdated;
}

function shouldRenderInlineTaskRun(taskRun: TaskRunSummary) {
  const pendingApprovalCount = Number(taskRun.pending_approval_count || 0);
  if (pendingApprovalCount > 0) return true;

  const clientTurnId = (taskRun.client_turn_id || "").trim().toLowerCase();
  if (clientTurnId.startsWith("delegate-")) return true;

  const runKind = (taskRun.run_kind || "").trim().toLowerCase();
  const isWorkflowRun = runKind.includes("pipeline") || runKind.includes("orchestration");
  if (isWorkflowRun) return true;

  const status = (taskRun.status || "").trim().toLowerCase();
  return ["paused", "blocked", "failed", "cancelled"].includes(status);
}

function shouldRenderTaskRunLiveChatCard(taskRun: TaskRunSummary) {
  if (shouldRenderInlineTaskRun(taskRun)) return false;
  const status = (taskRun.status || "").trim().toLowerCase();
  return status === "pending" || status === "running";
}

function buildTaskRunLiveMessage(taskRun: TaskRunSummary, agents: AgentInfo[]): MessageItem {
  const actorName = resolveTaskRunActorName(taskRun, agents);
  const status = (taskRun.status || "running").trim() || "running";
  return {
    id: -900000000 - taskRun.id,
    content: "",
    message_type: "text",
    created_at: taskRun.created_at || taskRun.updated_at || new Date().toISOString(),
    agent_name: actorName,
    client_turn_id: taskRun.client_turn_id || undefined,
    isStreaming: true,
    statusDetail: `Run: #${taskRun.id}\nStatus: ${status}`,
    localOnly: true,
    runtime_summary: {
      task_run_id: taskRun.id,
      status: taskRun.status,
      run_kind: taskRun.run_kind,
      continuation_state_summary: taskRun.continuation_state_summary,
      subagent_handles_summary: taskRun.subagent_handles_summary,
    },
  };
}

function formatApprovalQueueStatus(value: string | undefined) {
  if (!value) return "Unknown";
  return value[0]?.toUpperCase() + value.slice(1);
}

function approvalQueueStatusTone(status: string | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "approved") return "success";
  if (normalized === "rejected") return "error";
  if (normalized === "pending") return "warning";
  return "neutral";
}

function isTimeoutWaitQueueItem(item: ApprovalQueueItem) {
  return String(item.request_payload?.blocked_kind || "").toLowerCase() === "timeout";
}

function approvalQueueActionLabels(item: ApprovalQueueItem) {
  if (isTimeoutWaitQueueItem(item)) {
    return {
      approve: "Continue waiting",
      reject: "Stop waiting",
      busy: "Working...",
    };
  }
  return {
    approve: "Approve",
    reject: "Reject",
    busy: "Working...",
  };
}

function taskRunDetailPendingApprovalCount(detail: TaskRunDetail | null) {
  if (!detail) return null;
  if (Array.isArray(detail.approval_queue_items)) {
    return detail.approval_queue_items.filter((item) => (item.status || "").toLowerCase() === "pending").length;
  }
  if (typeof detail.pending_approval_count === "number") return detail.pending_approval_count;
  if (typeof detail.checkpoint_snapshot?.pending_approval_count === "number") {
    return detail.checkpoint_snapshot.pending_approval_count;
  }
  return null;
}

function isTaskRunDetailFresh(summary: TaskRunSummary, detail: TaskRunDetail | null | undefined) {
  if (!detail || detail.id !== summary.id) return false;
  if ((detail.status || "").toLowerCase() !== (summary.status || "").toLowerCase()) return false;
  if (typeof detail.event_count === "number" && detail.event_count < summary.event_count) return false;

  const detailPendingApprovalCount = taskRunDetailPendingApprovalCount(detail);
  if (detailPendingApprovalCount !== null && detailPendingApprovalCount !== Number(summary.pending_approval_count || 0)) {
    return false;
  }
  return true;
}

function resolveFreshTaskRunDetail(summary: TaskRunSummary, ...details: Array<TaskRunDetail | null | undefined>) {
  return details.find((detail) => isTaskRunDetailFresh(summary, detail)) ?? null;
}

function approvalRequestLabel(item: ApprovalQueueItem) {
  return `Approval #${item.id}`;
}

function summarizeTaskRunInlineStatus(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  activity: TaskActivityProjection | null,
  pendingApprovalOverride?: number,
  actorName = "Agent",
) {
  const chatProjection = chatFacingTaskRunText(taskRun, detail, actorName);
  const pendingApprovalItems = detail?.approval_queue_items?.filter((item) => (item.status || "").toLowerCase() === "pending") ?? [];
  const pendingApprovalCount =
    typeof pendingApprovalOverride === "number"
      ? pendingApprovalOverride
      : detail
        ? pendingApprovalItems.length
        : Number(taskRun.pending_approval_count || 0);
  const pendingTimeoutItem =
    pendingApprovalCount > 0 ? pendingApprovalItems.find((item) => isTimeoutWaitQueueItem(item)) ?? null : null;
  const pendingTimeoutCommand =
    pendingTimeoutItem && typeof pendingTimeoutItem.request_payload?.arguments === "string"
      ? (() => {
          try {
            const parsed = JSON.parse(pendingTimeoutItem.request_payload.arguments);
            return typeof parsed?.command === "string" ? parsed.command : null;
          } catch {
            return null;
          }
        })()
      : null;
  const blockedToolName =
    typeof detail?.checkpoint_snapshot?.turn_local_state?.blocked_tool?.["tool_name"] === "string"
      ? String(detail?.checkpoint_snapshot?.turn_local_state?.blocked_tool?.["tool_name"])
      : null;
  const events = detail?.events ?? [];
  const latestEvent = events[events.length - 1] ?? null;
  const latestPayload = latestEvent?.payload && typeof latestEvent.payload === "object"
    ? latestEvent.payload as Record<string, unknown>
    : null;
  const latestToolRoundPayload =
    latestEvent?.event_type === "tool_round_recorded" && latestPayload
      ? latestPayload
      : (() => {
          const roundEvent = [...events].reverse().find((event) => event.event_type === "tool_round_recorded");
          return roundEvent?.payload && typeof roundEvent.payload === "object"
            ? roundEvent.payload as Record<string, unknown>
            : null;
        })();
  const latestToolResult =
    Array.isArray(latestToolRoundPayload?.turn_local_state?.tool_results)
      ? latestToolRoundPayload?.turn_local_state?.tool_results?.[
          (latestToolRoundPayload.turn_local_state.tool_results as unknown[]).length - 1
        ] as Record<string, unknown> | undefined
      : undefined;
  const latestEventType =
    detail?.checkpoint_snapshot?.latest_event_type ||
    taskRun.latest_continuation_event_type ||
    null;
  const latestToolName =
    (latestToolResult && typeof latestToolResult.tool_name === "string" ? latestToolResult.tool_name : null)
    || (typeof latestPayload?.tool_name === "string" ? latestPayload.tool_name : null)
    || blockedToolName;
  const latestStartedToolName =
    (latestEventType || "").toLowerCase() === "tool_call_started"
      ? (typeof latestPayload?.tool_name === "string" ? latestPayload.tool_name : null)
      : null;
  const latestEventTypeValue = String(latestEvent?.event_type || latestEventType || "").toLowerCase();
  const latestResolutionAction = typeof latestPayload?.action_taken === "string"
    ? latestPayload.action_taken.toLowerCase()
    : "";
  const latestReplayStatus = typeof latestPayload?.replay_status === "string"
    ? latestPayload.replay_status.toLowerCase()
    : "";
  const latestResumeSupported = latestPayload?.resume_supported === true;

  if (pendingApprovalCount > 0) {
    if (pendingTimeoutItem) {
      return {
        tone: "info" as const,
        label: blockedToolName ? `Running · ${blockedToolName}` : "Running command",
        detail:
          pendingTimeoutCommand
            ? oneLinePreview(pendingTimeoutCommand, "Command still running.", 132)
            : "Command is still running.",
      };
    }
    return {
      tone: "warning" as const,
      label: blockedToolName ? `Waiting for approval · ${blockedToolName}` : "Waiting for approval",
      detail: `${pendingApprovalCount} pending approval request${pendingApprovalCount === 1 ? "" : "s"}.`,
    };
  }

  const normalizedStatus = (taskRun.status || "").toLowerCase();
  if (normalizedStatus === "running") {
    if (activity?.latest_agent_turn_preview?.trim()) {
      return {
        tone: "info" as const,
        label: "LLM",
        detail: oneLinePreview(activity.latest_agent_turn_preview, "", 132),
      };
    }
    if (activity?.summary?.trim()) {
      return {
        tone: "info" as const,
        label: "Runtime",
        detail: oneLinePreview(activity.summary, "", 132),
      };
    }
    if (latestEventTypeValue === "approval_queue_item_resolved") {
      if (latestResolutionAction === "run_shell_continued_after_approval") {
        return {
          tone: "info" as const,
          label: latestToolName ? `Tool: ${latestToolName}` : "Tool",
          detail: latestReplayStatus === "failed" ? "Status: failed" : "Status: continued",
        };
      }
      if (latestResolutionAction === "tool_replayed") {
        return {
          tone: "info" as const,
          label: latestToolName ? `Tool: ${latestToolName}` : "Tool",
          detail:
            latestReplayStatus === "failed"
              ? "Status: failed"
              : latestResumeSupported
                ? "Status: tool_replayed"
                : "Status: tool_replayed",
        };
      }
      return {
        tone: "info" as const,
        label: latestToolName ? `Tool: ${latestToolName}` : "Runtime",
        detail: latestResumeSupported
          ? "Status: resolved"
          : "Status: resolved",
      };
    }
    if (latestStartedToolName) {
      const latestStartedArguments = typeof latestPayload?.arguments === "string" ? latestPayload.arguments : "";
      return {
        tone: "info" as const,
        label: `Tool: ${latestStartedToolName}`,
        detail: latestStartedArguments
          ? oneLinePreview(latestStartedArguments, "", 132)
          : "Status: started",
      };
    }
    if (latestEventTypeValue === "approval_queue_item_followup_triggered") {
      return {
        tone: "info" as const,
        label: latestToolName ? `Tool: ${latestToolName}` : "Tool",
        detail: "Event: approval_queue_item_followup_triggered",
      };
    }
    if (latestToolResult) {
      const latestResultStatus = String(latestToolResult.status || "").toLowerCase();
      const latestResultText = typeof latestToolResult.result === "string" ? latestToolResult.result : "";
      if (latestResultStatus === "succeeded" && latestToolName) {
        return {
          tone: "info" as const,
          label: `Tool: ${latestToolName}`,
          detail: oneLinePreview(latestResultText, "Status: succeeded", 132),
        };
      }
      if (latestResultStatus === "failed" && latestToolName) {
        return {
          tone: "info" as const,
          label: `Tool: ${latestToolName}`,
          detail: oneLinePreview(latestResultText, "Status: failed", 132),
        };
      }
    }
    if ((latestEventType || "").toLowerCase() === "agent_turn_started") {
      return {
        tone: "info" as const,
        label: "LLM",
        detail: "Event: agent_turn_started",
      };
    }
    if (chatProjection.agentResponse) {
      return {
        tone: "info" as const,
        label: `Agent: ${chatProjection.agentResponse.agent || actorName}`,
        detail: oneLinePreview(chatProjection.agentResponse.response, "", 132),
      };
    }
    if (chatProjection.subtaskDispatch) {
      return {
        tone: "info" as const,
        label: chatProjection.subtaskDispatch.agent
          ? `Subtask: ${chatProjection.subtaskDispatch.agent}`
          : "Subtask",
        detail: chatProjection.subtaskDispatch.summary ||
          (chatProjection.subtaskDispatch.agent
            ? `Agent: ${chatProjection.subtaskDispatch.agent}`
            : "Event: subtask_dispatch"),
      };
    }
    return {
      tone: "info" as const,
      label: latestToolName ? `Tool: ${latestToolName}` : "Runtime",
      detail: describeRunningTaskRunFallback(taskRun, detail, activity, actorName, {
        latestEventType: latestEventType || latestEvent?.event_type || null,
        latestToolName,
      }),
      };
  }
  if (normalizedStatus === "completed") {
    return {
      tone: "success" as const,
      label: "Completed",
      detail:
        chatProjection.terminalSummary
          ? oneLinePreview(chatProjection.terminalSummary, "Task finished.", 132)
          : "Task finished.",
    };
  }
  if (normalizedStatus === "failed") {
    return {
      tone: "error" as const,
      label: "Failed",
      detail:
        chatProjection.terminalSummary
          ? oneLinePreview(chatProjection.terminalSummary, "Task failed.", 132)
          : "Task failed.",
    };
  }
  return {
    tone: "neutral" as const,
    label: formatTaskRunStatus(taskRun.status),
    detail:
      chatProjection.terminalSummary
        ? oneLinePreview(chatProjection.terminalSummary, "Task update.", 132)
        : "Task update.",
  };
}

function backgroundText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function runtimeHandleLabel(value: unknown) {
  if (!value || typeof value !== "object") return "";
  const handle = value as Record<string, unknown>;
  const agentName = typeof handle["agent_name"] === "string" ? handle["agent_name"] : "";
  const dispatchKind = typeof handle["dispatch_kind"] === "string" ? handle["dispatch_kind"] : "";
  const controlState = typeof handle["control_state"] === "string" ? String(handle["control_state"]).replace(/_/g, " ") : "";
  return [agentName, dispatchKind, controlState].filter(Boolean).join(" · ");
}

function consultCardBody(card: ThreadCard) {
  if (card.kind !== "consult_call") return "";
  return card.summary || card.response_preview || card.error || "";
}

function runtimeHandleRichLabel(value: unknown) {
  if (!value || typeof value !== "object") return "";
  const handle = value as Record<string, unknown>;
  const summaryText = typeof handle["summary_text"] === "string" ? handle["summary_text"].trim() : "";
  if (summaryText) return summaryText;
  const agentName =
    typeof handle["agent_name"] === "string"
      ? handle["agent_name"]
      : typeof handle["requested_name"] === "string"
        ? handle["requested_name"]
        : typeof handle["agent_type"] === "string"
          ? handle["agent_type"]
          : "";
  const dispatchKind = typeof handle["dispatch_kind"] === "string" ? handle["dispatch_kind"] : "";
  const controlState = typeof handle["control_state"] === "string" ? String(handle["control_state"]).replace(/_/g, " ") : "";
  const responsePreview = typeof handle["response_preview"] === "string" ? handle["response_preview"].trim() : "";
  const dependencyStepId = typeof handle["dependency_step_id"] === "string" ? handle["dependency_step_id"].trim() : "";
  const availableActions = Array.isArray(handle["available_actions"])
    ? handle["available_actions"].filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  return [
    [agentName, dispatchKind, controlState].filter(Boolean).join(" "),
    responsePreview,
    dependencyStepId ? `waiting on ${dependencyStepId}` : "",
    availableActions.length > 0 ? `actions: ${availableActions.join(", ")}` : "",
  ].filter(Boolean).join(" | ");
}

function renderMessageRuntimeSummary(message: MessageItem) {
  const summary = message.runtime_summary;
  if (!summary) return null;

  const rows = [
    summary.active_consult_handle
      ? { key: "consult", label: "Consult", detail: runtimeHandleRichLabel(summary.active_consult_handle) }
      : null,
    summary.active_subagent_handle
      ? { key: "subagent", label: "Subagent", detail: runtimeHandleRichLabel(summary.active_subagent_handle) }
      : null,
    summary.subagent_handles_summary
      ? { key: "handles", label: "Handles", detail: summary.subagent_handles_summary }
      : null,
    summary.continuation_state_summary
      ? { key: "continuation", label: "Continuation", detail: summary.continuation_state_summary }
      : null,
  ].filter((row): row is { key: string; label: string; detail: string } => Boolean(row && row.detail));

  if (rows.length === 0) return null;

  return (
    <div className="message-runtime-summary" aria-label="Message runtime summary">
      {rows.map((row) => (
        <div className="message-runtime-summary__row" key={row.key}>
          <span className="message-runtime-summary__label">{row.label}</span>
          <span className="message-runtime-summary__detail">{row.detail}</span>
        </div>
      ))}
    </div>
  );
}

function taskActivityBackgroundRows(activity: TaskActivityProjection | null) {
  const background = activity?.background;
  if (!background) return [];

  const rows = [
    {
      key: "active_consult_handle",
      label: "Consult",
      detail: runtimeHandleRichLabel(background.active_consult_handle),
    },
    {
      key: "active_subagent_handle",
      label: "Subagent",
      detail: runtimeHandleRichLabel(background.active_subagent_handle),
    },
    { key: "scheduler_runtime_summary", label: "Scheduler", detail: activity?.scheduler_runtime_summary || backgroundText(background.scheduler_runtime_summary) },
    { key: "continuation_state_summary", label: "Continuation", detail: activity?.continuation_state_summary || "" },
    { key: "continuation_cursor_summary", label: "Cursor", detail: activity?.continuation_cursor_summary || "" },
    { key: "latest_agent_turn_preview", label: "Latest Turn", detail: activity?.latest_agent_turn_preview || "" },
    { key: "subagent_lifecycle_summary", label: "Agents", detail: backgroundText(background.subagent_lifecycle_summary) },
    { key: "subagent_handles_summary", label: "Handles", detail: backgroundText(background.subagent_handles_summary) },
    { key: "pipeline_inbox_summary", label: "Pipeline", detail: backgroundText(background.pipeline_inbox_summary) },
    { key: "orchestration_handoff_inbox_summary", label: "Handoffs", detail: backgroundText(background.orchestration_handoff_inbox_summary) },
  ];

  return rows.filter((row) => row.detail.length > 0);
}

function renderTaskActivityBackground(activity: TaskActivityProjection | null) {
  const rows = taskActivityBackgroundRows(activity);
  if (rows.length === 0) return null;

  return (
    <div className="task-activity-background" aria-label="Task background activity">
      {rows.map((row) => (
        <div className="task-activity-background__row" key={row.key}>
          <span className="task-activity-background__label">{row.label}</span>
          <span className="task-activity-background__detail">{row.detail}</span>
        </div>
      ))}
    </div>
  );
}

function buildTaskRunCardSummary(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  activity: TaskActivityProjection | null,
  pendingApprovalOverride?: number,
  actorName = "Agent",
) {
  if ((taskRun.status || "").toLowerCase() === "running") {
    const projectedSummary = activity?.summary?.trim();
    if (projectedSummary) {
      return oneLinePreview(projectedSummary, "Task update.", 160);
    }
  }
  const chatProjection = chatFacingTaskRunText(taskRun, detail, actorName);
  const events = detail?.events ?? [];
  const latestEvent = events[events.length - 1] ?? null;
  const latestPayload = (latestEvent?.payload && typeof latestEvent.payload === "object")
    ? latestEvent.payload as Record<string, unknown>
    : null;
  const latestEventTypeValue = String(latestEvent?.event_type || "").toLowerCase();
  const latestResolutionAction = typeof latestPayload?.action_taken === "string"
    ? latestPayload.action_taken.toLowerCase()
    : "";
  const latestReplayStatus = typeof latestPayload?.replay_status === "string"
    ? latestPayload.replay_status.toLowerCase()
    : "";
  const latestResumeSupported = latestPayload?.resume_supported === true;
  const latestToolName = typeof latestPayload?.tool_name === "string" ? latestPayload.tool_name : null;
  const latestResponsePreview = typeof latestPayload?.response_preview === "string" ? latestPayload.response_preview : null;
  const blockedTool = detail?.checkpoint_snapshot?.turn_local_state?.blocked_tool;
  const blockedToolName =
    blockedTool && typeof blockedTool["tool_name"] === "string"
      ? String(blockedTool["tool_name"])
      : null;

  const normalizedStatus = (taskRun.status || "").toLowerCase();
  if (normalizedStatus === "running") {
    const pendingApprovalItems = detail?.approval_queue_items?.filter((item) => (item.status || "").toLowerCase() === "pending") ?? [];
    const pendingApprovalCount =
      typeof pendingApprovalOverride === "number"
        ? pendingApprovalOverride
        : detail
          ? pendingApprovalItems.length
          : Number(taskRun.pending_approval_count || 0);
    const pendingTimeoutItem =
      pendingApprovalCount > 0 ? pendingApprovalItems.find((item) => isTimeoutWaitQueueItem(item)) ?? null : null;
    if (pendingApprovalCount > 0 && blockedToolName && !pendingTimeoutItem) {
      return `Approval: ${blockedToolName}\nStatus: pending`;
    }
    if (pendingTimeoutItem) {
      const pendingTimeoutCommand =
        typeof pendingTimeoutItem.request_payload?.arguments === "string"
          ? (() => {
              try {
                const parsed = JSON.parse(pendingTimeoutItem.request_payload.arguments);
                return typeof parsed?.command === "string" ? parsed.command : null;
              } catch {
                return null;
              }
            })()
          : null;
      return pendingTimeoutCommand
        ? `Tool: ${blockedToolName || "run_shell"}\nCommand: ${oneLinePreview(pendingTimeoutCommand, "command", 120)}\nStatus: running`
        : `Tool: ${blockedToolName || "command"}\nStatus: running`;
    }
    if (latestEventTypeValue === "approval_queue_item_resolved") {
      if (latestResolutionAction === "run_shell_continued_after_approval") {
        return [`Event: approval_queue_item_resolved`, latestToolName ? `Tool: ${latestToolName}` : "", latestReplayStatus === "failed" ? "Status: failed" : "Status: continued"].filter(Boolean).join("\n");
      }
      if (latestResolutionAction === "tool_replayed") {
        if (latestReplayStatus === "failed") {
          return [`Event: approval_queue_item_resolved`, latestToolName ? `Tool: ${latestToolName}` : "", "Status: failed"].filter(Boolean).join("\n");
        }
        return [`Event: approval_queue_item_resolved`, latestToolName ? `Tool: ${latestToolName}` : "", "Status: tool_replayed"].filter(Boolean).join("\n");
      }
      return [`Event: approval_queue_item_resolved`, latestToolName ? `Tool: ${latestToolName}` : "", "Status: resolved"].filter(Boolean).join("\n");
    }
    if (latestEventTypeValue === "approval_queue_item_followup_triggered") {
      return [`Event: approval_queue_item_followup_triggered`, latestToolName ? `Tool: ${latestToolName}` : ""].filter(Boolean).join("\n");
    }
    if (chatProjection.subtaskResult) {
      return chatProjection.subtaskResult.summary ||
        (chatProjection.subtaskResult.agent
          ? `Agent: ${chatProjection.subtaskResult.agent}\nEvent: subtask_result`
          : "Event: subtask_result");
    }
    if (chatProjection.subtaskDispatch) {
      return chatProjection.subtaskDispatch.summary ||
        (chatProjection.subtaskDispatch.agent
          ? `Agent: ${chatProjection.subtaskDispatch.agent}\nEvent: subtask_dispatch`
          : "Event: subtask_dispatch");
    }
    if ((latestEvent?.event_type || "").includes("tool")) {
      if ((latestEvent?.event_type || "").toLowerCase() === "tool_call_started") {
        const latestStartedArguments = typeof latestPayload?.arguments === "string" ? latestPayload.arguments : "";
        return latestToolName
          ? `Tool: ${latestToolName}\nArgs: ${oneLinePreview(latestStartedArguments, "", 120)}`
          : "Event: tool_call_started";
      }
      return latestToolName
        ? `Tool: ${latestToolName}\nEvent: ${latestEvent?.event_type}`
        : `Event: ${latestEvent?.event_type || "tool"}`;
    }
    if ((latestEvent?.event_type || "").includes("agent_turn")) {
      return chatProjection.agentResponse?.response
        ? oneLinePreview(chatProjection.agentResponse.response, "", 140)
        : latestResponsePreview
        ? oneLinePreview(latestResponsePreview, "", 140)
        : `Agent: ${actorName}\nEvent: ${latestEvent?.event_type}`;
    }
    return describeRunningTaskRunFallback(taskRun, detail, activity, actorName, {
      latestEventType: latestEvent?.event_type || taskRun.latest_continuation_event_type || null,
      latestToolName,
    });
  }

  if (normalizedStatus === "completed") {
    return oneLinePreview(
      chatProjection.terminalSummary || "Task completed.",
      "Background task completed.",
      160,
    );
  }

  if (normalizedStatus === "failed") {
    return oneLinePreview(
      userFacingTaskRunFailureSummary(taskRun.summary) || chatProjection.terminalSummary || "Background task failed.",
      "Background task failed.",
      160,
    );
  }

  return oneLinePreview(
    chatProjection.terminalSummary || "Task update.",
    "Background task update.",
    160,
  );
}

function summarizeTaskRunRuntimeCards(cards: ThreadCard[]) {
  if (cards.length === 0) return null;
  const shellCards = cards.filter(isRunShellRuntimeCard);
  const latestCard = shellCards[shellCards.length - 1] ?? cards[cards.length - 1];
  const latestActor = cardActorName(latestCard);
  const state = compactCardState(latestCard, true);
  const detail = compactCardSummary(latestCard);
  return {
    actor: latestActor,
    title: cardTitle(latestCard),
    detail,
    state,
  };
}

function isRunShellRuntimeCard(card: ThreadCard): card is DecoratedChatCardItem {
  return card.kind === "tool_call" && (card.tool || "").toLowerCase() === "run_shell";
}

function readShellCommandPreview(argumentsText: string | undefined) {
  const raw = argumentsText?.trim();
  if (!raw) return "";

  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const command = typeof parsed.command === "string" ? parsed.command.trim() : "";
    const cwd = typeof parsed.cwd === "string" && parsed.cwd.trim() !== "." ? parsed.cwd.trim() : "";
    if (command && cwd) return `${oneLinePreview(command, "", 160)} · cwd ${oneLinePreview(cwd, "", 60)}`;
    if (command) return oneLinePreview(command, "", 180);
  } catch {
    return oneLinePreview(raw, "", 180);
  }

  return oneLinePreview(raw, "", 180);
}

function trimShellOutputTail(output: string | undefined) {
  const normalized = output?.replace(/\r\n/g, "\n").trimEnd() ?? "";
  if (!normalized) return "";

  const charClipped =
    normalized.length > TASK_RUN_SHELL_TAIL_MAX_CHARS
      ? normalized.slice(-TASK_RUN_SHELL_TAIL_MAX_CHARS)
      : normalized;
  const lines = charClipped.split("\n");
  const lineClipped = lines.length > TASK_RUN_SHELL_TAIL_MAX_LINES;
  const tail = lineClipped ? lines.slice(-TASK_RUN_SHELL_TAIL_MAX_LINES).join("\n") : charClipped;
  const clipped = normalized.length > charClipped.length || lineClipped;
  return clipped ? `... showing latest shell output\n${tail}` : tail;
}

function parseJsonObject(value: string | undefined): Record<string, unknown> | null {
  const raw = value?.trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function collectStringValues(value: unknown, keys: string[], output: Set<string>) {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item) => collectStringValues(item, keys, output));
    return;
  }

  const record = value as Record<string, unknown>;
  Object.entries(record).forEach(([key, child]) => {
    const normalizedKey = key.toLowerCase();
    if (keys.includes(normalizedKey)) {
      if (typeof child === "string") output.add(child);
      if (Array.isArray(child)) {
        child.forEach((item) => {
          if (typeof item === "string") output.add(item);
        });
      }
    }
    if (child && typeof child === "object") collectStringValues(child, keys, output);
  });
}

function normalizeBrowserPath(path: string) {
  return path.replace(/\\/g, "/").replace(/^["'`]+|["'`.,;:)]+$/g, "").trim();
}

function browserPathBaseName(path: string) {
  const normalized = normalizeBrowserPath(path).replace(/\/+$/g, "");
  return normalized.split("/").filter(Boolean).pop() || normalized || "workspace";
}

function relativeBrowserPath(path: string, workspacePath?: string | null) {
  const normalized = normalizeBrowserPath(path).replace(/\/+$/g, "");
  const workspace = workspacePath ? normalizeBrowserPath(workspacePath).replace(/\/+$/g, "") : "";
  if (!normalized) return "";
  if (workspace && normalized === workspace) return "";
  if (workspace && normalized.startsWith(`${workspace}/`)) return normalized.slice(workspace.length + 1);
  return normalized.replace(/^\.\//, "").replace(/^\/+/, "");
}

function isAbsoluteBrowserPath(path: string) {
  const normalized = normalizeBrowserPath(path);
  return normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized);
}

function isRuntimePathInsideWorkspace(path: string, workspacePath?: string | null) {
  const normalized = normalizeBrowserPath(path).replace(/\/+$/g, "");
  const workspace = workspacePath ? normalizeBrowserPath(workspacePath).replace(/\/+$/g, "") : "";
  if (!isAbsoluteBrowserPath(normalized)) return true;
  if (!workspace) return false;
  return normalized === workspace || normalized.startsWith(`${workspace}/`);
}

function compareBrowserFileTreeNodes(left: BrowserFileTreeNode, right: BrowserFileTreeNode) {
  if (left.kind !== right.kind) return left.kind === "directory" ? -1 : 1;
  return left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" });
}

function sortBrowserFileTree(node: BrowserFileTreeNode) {
  node.children.sort(compareBrowserFileTreeNodes);
  node.children.forEach(sortBrowserFileTree);
}

function buildBrowserFileTree(entries: BrowserFileEntry[], workspacePath?: string | null, projectName?: string) {
  const workspace = workspacePath ? normalizeBrowserPath(workspacePath) : "";
  const rootName = browserPathBaseName(workspace) || projectName?.trim() || "workspace";
  const root: BrowserFileTreeNode = {
    id: "file-tree:root",
    name: rootName,
    path: workspace || rootName,
    kind: "directory",
    children: [],
    source: "Workspace",
  };
  const directories = new Map<string, BrowserFileTreeNode>([["", root]]);
  const files = new Map<string, BrowserFileTreeNode>();

  entries.forEach((entry) => {
    if (entry.source === "Workspace") return;
    const relativePath = relativeBrowserPath(entry.path, workspacePath);
    if (!relativePath || relativePath === "." || relativePath === root.name) return;
    const parts = relativePath.split("/").map((part) => part.trim()).filter(Boolean);
    if (parts.length === 0) return;

    let parent = root;
    let currentPath = "";
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const isLeaf = index === parts.length - 1;
      if (isLeaf) {
        const existing = files.get(currentPath);
        if (existing) {
          if (!existing.timestamp || new Date(entry.timestamp || 0).getTime() > new Date(existing.timestamp || 0).getTime()) {
            existing.source = entry.source;
            existing.detail = entry.detail;
            existing.timestamp = entry.timestamp;
          }
          return;
        }
        const fileNode: BrowserFileTreeNode = {
          id: `file-tree:file:${currentPath}`,
          name: part,
          path: currentPath,
          kind: "file",
          children: [],
          source: entry.source,
          detail: entry.detail,
          timestamp: entry.timestamp,
        };
        files.set(currentPath, fileNode);
        parent.children.push(fileNode);
        return;
      }

      let directory = directories.get(currentPath);
      if (!directory) {
        directory = {
          id: `file-tree:dir:${currentPath}`,
          name: part,
          path: currentPath,
          kind: "directory",
          children: [],
        };
        directories.set(currentPath, directory);
        parent.children.push(directory);
      }
      parent = directory;
    });
  });

  sortBrowserFileTree(root);
  return root;
}

function looksLikeProjectPath(value: string) {
  const path = normalizeBrowserPath(value);
  if (!path || path.length < 3 || path.length > 180) return false;
  if (path.includes("://")) return false;
  if (/\s/.test(path)) return false;
  return (
    path.startsWith("./") ||
    path.startsWith("/") ||
    path.includes("/") ||
    /\.(md|txt|json|ya?ml|toml|py|ts|tsx|js|jsx|css|html|sh|sql|lock)$/i.test(path)
  );
}

function extractPathsFromText(text: string | undefined) {
  const raw = text || "";
  if (!raw) return [];
  const matches = raw.match(/(?:\.{1,2}\/|\/|[\w.-]+\/)[\w./@+-]+\.[A-Za-z0-9]+/g) ?? [];
  return Array.from(new Set(matches.map(normalizeBrowserPath).filter(looksLikeProjectPath))).slice(0, 12);
}

function dedupeById<T extends { id: string }>(items: T[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function formatTaskRunShellDuration(durationMs: number | undefined) {
  if (typeof durationMs !== "number") return "";
  if (durationMs >= 1000) return `${(durationMs / 1000).toFixed(durationMs >= 10000 ? 0 : 1)}s`;
  return `${durationMs}ms`;
}

function renderTaskRunShellOutput(taskRun: TaskRunSummary, cards: ThreadCard[]) {
  const shellCards = cards.filter(isRunShellRuntimeCard);
  const latestCard = shellCards[shellCards.length - 1] ?? null;
  if (!latestCard) return null;

  const output = trimShellOutputTail(latestCard.result);
  if (!output) return null;

  const taskStatus = (taskRun.status || "").toLowerCase();
  const isRunning = latestCard.status === "running" && taskStatus === "running";
  const isError = isToolCardFailure(latestCard) || taskStatus === "failed";
  const commandPreview = readShellCommandPreview(latestCard.arguments);
  const meta = [
    isRunning ? "running" : isError ? "failed" : taskStatus || "captured",
    typeof latestCard.pid === "number" ? `pid ${latestCard.pid}` : "",
    formatTaskRunShellDuration(latestCard.duration_ms),
    shellCards.length > 1 ? `${shellCards.length} updates` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section className={`task-run-shell-output ${isRunning ? "is-live" : ""} ${isError ? "is-error" : ""}`}>
      <div className="task-run-shell-output__header">
        <div className="task-run-shell-output__title">
          <span className="chat-json-badge">SHELL</span>
          <span>Live run_shell output</span>
        </div>
        <span className="task-run-shell-output__meta">{meta}</span>
      </div>
      {commandPreview ? <div className="task-run-shell-output__command">{commandPreview}</div> : null}
      <pre className="task-run-shell-output__body">{output}</pre>
    </section>
  );
}

function summarizeTaskRunShellStatus(cards: ThreadCard[]) {
  const shellCards = cards.filter(isRunShellRuntimeCard);
  const latestCard = shellCards[shellCards.length - 1] ?? null;
  if (!latestCard) return null;

  const output = trimShellOutputTail(latestCard.result);
  const lines = output.split("\n").map((line) => line.trim()).filter(Boolean);
  const latestLine = lines[lines.length - 1] ?? "";
  const detail = oneLinePreview(latestLine || latestCard.result, `${latestCard.tool || "run_shell"} is running.`, 160);
  const state = compactCardState(latestCard, true);

  return {
    actor: cardActorName(latestCard),
    title: cardTitle(latestCard),
    detail,
    state,
  };
}

function buildBrowserFileEntries(project: ProjectSummary | null, cards: ChatCardItem[], taskRuns: TaskRunSummary[]) {
  const entries: BrowserFileEntry[] = [];

  if (project?.workspace_path) {
    entries.push({
      id: `workspace:${project.workspace_path}`,
      path: normalizeBrowserPath(project.workspace_path),
      source: "Workspace",
      detail: project.repo_full_name || project.repo_url || project.name,
      timestamp: project.created_from_chatroom_id ? undefined : undefined,
    });
  }

  cards.forEach((card) => {
    const parsedArgs = parseJsonObject(card.arguments);
    const structuredPaths = new Set<string>();
    collectStringValues(parsedArgs, ["path", "file", "file_path", "filepath", "directory", "cwd", "target_path"], structuredPaths);
    structuredPaths.forEach((path) => {
      const normalized = normalizeBrowserPath(path);
      if (!looksLikeProjectPath(normalized)) return;
      entries.push({
        id: `card:${card.id}:${normalized}`,
        path: normalized,
        source: card.tool || card.kind,
        detail: card.summary || card.display_name || card.agent || "Referenced by runtime card",
        timestamp: card.created_at,
      });
    });

    extractPathsFromText(`${card.result || ""}\n${card.summary || ""}\n${card.content_preview || ""}`).forEach((path) => {
      entries.push({
        id: `text:${card.id}:${path}`,
        path,
        source: card.tool || card.kind,
        detail: "Mentioned in output",
        timestamp: card.created_at,
      });
    });
  });

  taskRuns.forEach((run) => {
    extractPathsFromText(`${run.summary || ""}\n${run.user_request || ""}`).forEach((path) => {
      entries.push({
        id: `run:${run.id}:${path}`,
        path,
        source: formatTaskRunKind(run.run_kind),
        detail: run.title,
        timestamp: run.updated_at || run.created_at || undefined,
      });
    });
  });

  return dedupeById(entries)
    .sort((left, right) => new Date(right.timestamp || 0).getTime() - new Date(left.timestamp || 0).getTime())
    .slice(0, 24);
}

function buildWorkspaceBrowserFileEntries(projectBrowserIndex: ProjectBrowserIndex | null) {
  if (!projectBrowserIndex) return [];
  return projectBrowserIndex.files.map((file) => {
    const fileType = browserFileTypeLabel(file.path);
    return {
      id: `workspace-file:${file.path}`,
      path: file.path,
      source: fileType,
      detail: file.size === null || typeof file.size === "undefined" ? fileType : `${fileType} - ${file.size} bytes`,
      timestamp: typeof file.mtime === "number" ? new Date(file.mtime * 1000).toISOString() : undefined,
    };
  });
}

function mergeBrowserFileEntries(workspaceEntries: BrowserFileEntry[], runtimeEntries: BrowserFileEntry[], workspacePath?: string | null) {
  const merged = new Map<string, BrowserFileEntry>();
  workspaceEntries.forEach((entry) => merged.set(normalizeBrowserPath(entry.path), entry));
  runtimeEntries.forEach((entry) => {
    if (!isRuntimePathInsideWorkspace(entry.path, workspacePath)) return;
    const key = relativeBrowserPath(entry.path, workspacePath);
    const existing = merged.get(key) ?? merged.get(normalizeBrowserPath(entry.path));
    merged.set(existing ? normalizeBrowserPath(existing.path) : key, existing ? { ...existing, ...entry, path: existing.path } : entry);
  });
  return Array.from(merged.values());
}

function classifyBrowserArtifact(value: string) {
  const normalized = normalizeBrowserPath(value).toLowerCase();
  if (!normalized) return null;
  if (/^@[\w.-]+\b/.test(normalized)) return null;
  if (/(^|\/)adr[-_./]|\badr[-_ ]?\d+|\barchitecture[-_ ]decision/.test(normalized)) return "ADR";
  if (/\bprd\b|product[-_ ]requirements?|requirements?[-_ ]doc/.test(normalized)) return "PRD";
  if (/\btech[-_ ]?spec\b|\bspecification\b|\bspec\b|design[-_ ]doc|proposal/.test(normalized)) return "Spec";
  if (/(^|\/)reports\/tests(\/|$)|test[-_ ]?(plan|report|result|summary)|qa[-_ ]?report|verification/.test(normalized)) return "Test";
  if (/\breport\b|audit|review/.test(normalized)) return "Report";
  if (/changelog|change[-_ ]?log|release[-_ ]?notes?/.test(normalized)) return "Release";
  if (/readme|docs?\//.test(normalized)) return "Doc";
  if (/artifact/.test(normalized)) return "Artifact";
  if (/\.(md|mdx|pdf|docx?)$/i.test(normalized) && /(plan|summary|guide|notes|decision|migration|deploy)/.test(normalized)) return "Doc";
  return null;
}

function artifactDisplayName(value: string) {
  const normalized = normalizeBrowserPath(value);
  return browserPathBaseName(normalized) || normalized;
}

function resolveArtifactWorkspacePath(entry: BrowserArtifactEntry, workspacePath?: string | null) {
  const rawPath = (entry.path || "").trim();
  if (!rawPath) return "";
  if (!isRuntimePathInsideWorkspace(rawPath, workspacePath)) return "";
  return relativeBrowserPath(rawPath, workspacePath);
}

function buildBrowserArtifactEntries(cards: ChatCardItem[], taskRuns: TaskRunSummary[]) {
  const entries: BrowserArtifactEntry[] = [];

  cards.forEach((card) => {
    card.expected_artifacts?.forEach((artifact) => {
      const artifactType = classifyBrowserArtifact(artifact);
      if (!artifactType) return;
      entries.push({
        id: `expected:${card.id}:${artifact}`,
        name: artifactDisplayName(artifact),
        type: artifactType,
        agentName: card.agent || null,
        path: artifact,
        stage: card.stage || card.display_name || "Expected artifact",
        detail: artifact,
        status: "expected",
        timestamp: card.created_at,
      });
    });

    extractPathsFromText(`${card.summary || ""}\n${card.result || ""}`).forEach((path) => {
      const artifactType = classifyBrowserArtifact(path);
      if (!artifactType) return;
      entries.push({
        id: `artifact-path:${card.id}:${path}`,
        name: artifactDisplayName(path),
        type: artifactType,
        agentName: card.agent || null,
        path,
        stage: card.stage || card.tool || card.kind,
        detail: path,
        status: isToolCardFailure(card) ? "needs review" : "referenced",
        timestamp: card.created_at,
      });
    });
  });

  taskRuns.forEach((run) => {
    extractPathsFromText(`${run.summary || ""}\n${run.user_request || ""}`).forEach((path) => {
      const artifactType = classifyBrowserArtifact(path);
      if (!artifactType) return;
      entries.push({
        id: `run-artifact:${run.id}:${path}`,
        name: artifactDisplayName(path),
        type: artifactType,
        agentName: run.target_agent_name || null,
        path,
        stage: formatTaskRunKind(run.run_kind),
        detail: path,
        status: formatTaskRunStatus(run.status),
        timestamp: run.completed_at || run.updated_at || run.created_at || undefined,
      });
    });
  });

  return dedupeById(entries)
    .sort((left, right) => new Date(right.timestamp || 0).getTime() - new Date(left.timestamp || 0).getTime())
    .slice(0, 24);
}

function buildWorkspaceBrowserArtifactEntries(projectBrowserIndex: ProjectBrowserIndex | null) {
  if (!projectBrowserIndex) return [];
  return projectBrowserIndex.artifacts.map((artifact) => ({
    id: `workspace-artifact:${artifact.path}`,
    name: artifact.name || artifactDisplayName(artifact.path),
    type: artifact.type,
    agentName: artifact.agent_name || null,
    path: artifact.path,
    stage: "Workspace",
    detail: artifact.path,
    status: "file",
    timestamp: typeof artifact.mtime === "number" ? new Date(artifact.mtime * 1000).toISOString() : undefined,
  }));
}

function mergeBrowserArtifactEntries(
  workspaceEntries: BrowserArtifactEntry[],
  runtimeEntries: BrowserArtifactEntry[],
  workspacePath?: string | null,
) {
  const merged = new Map<string, BrowserArtifactEntry>();
  const fallbackEntries: BrowserArtifactEntry[] = [];

  workspaceEntries.forEach((entry) => {
    const key = relativeBrowserPath(entry.path || "", workspacePath) || normalizeBrowserPath(entry.path || entry.name);
    merged.set(key, entry);
  });

  runtimeEntries.forEach((entry) => {
    const resolvedPath = resolveArtifactWorkspacePath(entry, workspacePath);
    if (!resolvedPath) {
      fallbackEntries.push(entry);
      return;
    }

    const existing = merged.get(resolvedPath) ?? merged.get(normalizeBrowserPath(entry.path || ""));
    if (!existing) {
      merged.set(resolvedPath, {
        ...entry,
        path: resolvedPath,
        detail: resolvedPath,
      });
      return;
    }

    merged.set(resolvedPath, {
      ...existing,
      ...entry,
      path: existing.path || resolvedPath,
      detail: existing.detail || resolvedPath,
      status: existing.status === "file" && entry.status === "expected" ? existing.status : entry.status,
    } as BrowserArtifactEntry);
  });

  return [...Array.from(merged.values()), ...fallbackEntries]
    .sort((left, right) => {
      const leftTime = new Date(left.timestamp || 0).getTime();
      const rightTime = new Date(right.timestamp || 0).getTime();
      if (leftTime !== rightTime) return rightTime - leftTime;
      return left.name.localeCompare(right.name);
    });
}

function groupBrowserArtifactEntries(entries: BrowserArtifactEntry[]) {
  const groups = new Map<string, BrowserArtifactEntry[]>();
  entries.forEach((entry) => {
    groups.set(entry.type, [...(groups.get(entry.type) ?? []), entry]);
  });

  return Array.from(groups.entries())
    .map(([type, items]) => ({
      type,
      items: items.sort((left, right) => left.name.localeCompare(right.name)),
    }))
    .sort((left, right) => left.type.localeCompare(right.type));
}

function taskRunPayloadPreview(payload: Record<string, unknown> | undefined) {
  if (!payload || Object.keys(payload).length === 0) return "";
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return "";
  }
}

function resolveTaskRunActorName(taskRun: TaskRunSummary, agents: AgentInfo[]) {
  const target = (taskRun.target_agent_name || "").trim();
  if (!target) return "Agent";
  const normalizedTarget = target.toLowerCase();
  const matched = agents.find((agent) => {
    const normalizedType = (agent.type || "").trim().toLowerCase();
    const normalizedName = (agent.name || "").trim().toLowerCase();
    const normalizedAgentType = getAgentType(agent).trim().toLowerCase();
    return (
      normalizedTarget === normalizedType
      || normalizedTarget === normalizedName
      || normalizedTarget === normalizedAgentType
    );
  });
  return matched ? getAgentDisplayName(matched) : target;
}

function buildTaskRunTraceDetailContent(event: TaskRunEvent) {
  if (event.event_type === "context_compaction") {
    const compactionDetail = buildContextCompactionDetail(event.payload, event.summary);
    if (compactionDetail) return compactionDetail;
  }

  const sections = [markdownSection("Event", formatTaskRunEventType(event.event_type), { asMarkdown: true })];
  if (event.summary?.trim()) {
    sections.push(markdownSection("Summary", event.summary, { asMarkdown: true }));
  }
  if (event.continuation_state_summary?.trim()) {
    sections.push(markdownSection("Continuation", event.continuation_state_summary, { asMarkdown: true }));
  }
  const payloadPreview = taskRunPayloadPreview(event.payload);
  if (payloadPreview) {
    sections.push(markdownSection("Payload", payloadPreview, { language: "json" }));
  }
  return sections.join("\n\n");
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readStringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0) : [];
}

function readObjectArray(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

type AgentStripStatus = "online" | "waiting" | "working";

function taskRunAgentStripStatus(taskRun: TaskRunSummary): AgentStripStatus {
  const status = (taskRun.status || "").trim().toLowerCase();
  if (status !== "running") return "online";

  const latestEventType = (
    taskRun.latest_event_type
    || taskRun.checkpoint_snapshot?.latest_event_type
    || taskRun.continuation_cursor?.source_event_type
    || taskRun.checkpoint_snapshot?.continuation_cursor?.source_event_type
    || ""
  ).trim().toLowerCase();
  if (latestEventType === "task_run_waiting_for_delegated_work") {
    return "waiting";
  }

  const checkpoint = readRecord(taskRun.checkpoint_snapshot);
  const handles = readRecord(checkpoint?.subagent_handles);
  const entries = readObjectArray(handles?.entries);
  const hasAwaitingSubagent = entries.some((entry) => {
    const controlState = (readTextField(entry, "control_state") || "").toLowerCase();
    return controlState === "await_completion" || controlState === "await_dependency";
  });
  if (hasAwaitingSubagent) {
    return "waiting";
  }

  const cursor = taskRun.continuation_cursor ?? taskRun.checkpoint_snapshot?.continuation_cursor ?? null;
  const runningStepCount = readNumber(cursor?.running_step_count);
  const waitingStepCount = readNumber(cursor?.waiting_step_count);
  if ((waitingStepCount ?? 0) > 0 && (runningStepCount ?? 0) > 0) {
    return "waiting";
  }

  return "working";
}

function formatSourceList(values: string[]) {
  return values.length > 0 ? values.map((value) => `\`${value}\``).join(", ") : "none";
}

function buildContextCompactionDetail(payload: Record<string, unknown> | undefined, fallbackSummary?: string | null) {
  const root = readRecord(payload);
  const diagnostics = readRecord(root?.selector_diagnostics);
  if (!diagnostics) return "";

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
  const developerDropped = readStringArray(developer?.dropped_sources);
  const developerTruncated = readStringArray(developer?.truncated_sources);
  const userDropped = readStringArray(user?.dropped_sources);
  const userTruncated = readStringArray(user?.truncated_sources);
  const droppedSources = [...developerDropped, ...userDropped];
  const truncatedSources = [...developerTruncated, ...userTruncated];

  const budget = [
    maxFragments !== null ? `${maxFragments} fragments` : "",
    maxTokens !== null ? `${maxTokens} tokens` : "",
  ].filter(Boolean).join(" / ");
  const candidateText = candidateCount !== null && selectedCount !== null
    ? `${candidateCount} candidate fragments were reduced to ${selectedCount} selected fragments`
    : "The context selector reduced the prompt context";
  const tokenText = candidateTokens !== null && selectedTokens !== null
    ? `Token estimate changed from ${candidateTokens} to ${selectedTokens}.`
    : "";

  const sections = [
    markdownSection(
      "Why",
      [
        `${candidateText}${budget ? ` to fit the ${budget} budget` : ""}.`,
        `${droppedCount} source${droppedCount === 1 ? "" : "s"} dropped; ${truncatedCount} source${truncatedCount === 1 ? "" : "s"} truncated.`,
        tokenText,
      ].filter(Boolean).join("\n\n"),
      { asMarkdown: true },
    ),
    markdownSection(
      "What Changed",
      [
        `- Dropped sources: ${formatSourceList(droppedSources)}`,
        `- Truncated sources: ${formatSourceList(truncatedSources)}`,
        roleBudgets ? `- Role budgets: developer ${readNumber(roleBudgets?.developer) ?? "?"} / user ${readNumber(roleBudgets?.user) ?? "?"}` : "",
        scopeBudgets ? `- Scope budgets: ${Object.entries(scopeBudgets).map(([scope, value]) => `${scope} ${value}`).join(" / ")}` : "",
        scopeUsage ? `- Scope usage: ${Object.entries(scopeUsage).map(([scope, report]) => {
          const scopeReport = readRecord(report);
          return `${scope} ${readNumber(scopeReport?.selected_count) ?? "?"}/${readNumber(scopeReport?.candidate_count) ?? "?"} fragments, ${readNumber(scopeReport?.selected_tokens) ?? "?"}/${readNumber(scopeReport?.candidate_tokens) ?? "?"} tokens`;
        }).join(" / ")}` : "",
        developer ? `- Developer context: ${readNumber(developer.selected_count) ?? "?"}/${readNumber(developer.candidate_count) ?? "?"} selected, ${readNumber(developer.selected_tokens) ?? "?"}/${readNumber(developer.candidate_tokens) ?? "?"} tokens` : "",
        user ? `- User context: ${readNumber(user.selected_count) ?? "?"}/${readNumber(user.candidate_count) ?? "?"} selected, ${readNumber(user.selected_tokens) ?? "?"}/${readNumber(user.candidate_tokens) ?? "?"} tokens` : "",
      ].filter(Boolean).join("\n"),
      { asMarkdown: true },
    ),
  ];
  if (fallbackSummary?.trim()) {
    sections.unshift(markdownSection("Summary", fallbackSummary, { asMarkdown: true }));
  }
  return sections.join("\n\n");
}

function clipFailureAnalysisText(value: string | undefined | null, limit = 1800) {
  const normalized = (value || "").trim();
  if (!normalized) return "";
  return normalized.length > limit ? `${normalized.slice(0, limit).trimEnd()}\n...[truncated]` : normalized;
}

function buildFailureStepAnalysisPrompt(step: MessageStreamStep, context: FailureStepAnalysisContext) {
  const sections = [
    `@${DEFAULT_AGENT_TYPE} Please analyze this failed step and provide verifiable debugging steps and fix suggestions.`,
    [
      "## Failed Step",
      `- Label: ${step.label}`,
      `- State: ${step.state}`,
      step.agent ? `- Agent: ${step.agent}` : "",
      step.tool ? `- Tool: ${step.tool}` : "",
      step.detail ? `- Detail: ${step.detail}` : "",
    ].filter(Boolean).join("\n"),
  ];

  if (step.detailContent) {
    sections.push(`## Step Detail\n\n${clipFailureAnalysisText(step.detailContent)}`);
  }

  if (context.message) {
    sections.push(
      [
        "## Chat Message Context",
        `- Message ID: ${context.message.id}`,
        context.message.agent_name ? `- Message Agent: ${context.message.agent_name}` : "- Message Agent: user",
        context.message.client_turn_id ? `- Client Turn ID: ${context.message.client_turn_id}` : "",
        context.message.content ? `\n${clipFailureAnalysisText(context.message.content, 1200)}` : "",
      ].filter(Boolean).join("\n"),
    );
  }

  if (context.taskRun) {
    sections.push(
      [
        "## Task Run Context",
        `- Run ID: ${context.taskRun.id}`,
        `- Title: ${context.taskRun.title}`,
        `- Status: ${context.taskRun.status}`,
        context.taskRun.target_agent_name ? `- Target Agent: ${context.taskRun.target_agent_name}` : "",
        context.taskRun.client_turn_id ? `- Client Turn ID: ${context.taskRun.client_turn_id}` : "",
        context.taskRun.summary ? `- Summary: ${context.taskRun.summary}` : "",
      ].filter(Boolean).join("\n"),
    );
  }

  const taskRunEvents = context.taskRunDetail?.events ?? [];
  if (taskRunEvents.length > 0) {
    sections.push(
      `## Recent Task Events\n\n${clipFailureAnalysisText(
        taskRunEvents
          .slice(-6)
          .map((event) => {
            const bits = [
              `${event.created_at || ""} ${event.event_type}`,
              event.agent_name ? `agent=${event.agent_name}` : "",
              event.summary || "",
            ].filter(Boolean);
            return `- ${bits.join(" | ")}`;
          })
          .join("\n"),
        1600,
      )}`,
    );
  }

  sections.push("Focus on the most likely root cause, which logs or settings still need inspection, and the next validation to run.");
  return sections.join("\n\n");
}

function renderFailureStepAction(
  step: MessageStreamStep,
  context: FailureStepAnalysisContext,
  onAnalyzeFailureStep?: FailureStepAnalysisHandler,
) {
  if (step.state !== "error" || !onAnalyzeFailureStep) return null;

  return (
    <button
      type="button"
      className="message-stream-step__analysis"
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onAnalyzeFailureStep(step, context);
      }}
      title="Ask Valet to analyze this failure"
    >
      Valet
    </button>
  );
}

function readMessageTestReportArtifact(message: MessageItem) {
  const metadata = message.metadata && typeof message.metadata === "object" ? message.metadata : null;
  const artifact = metadata?.["test_report_artifact"];
  if (!artifact || typeof artifact !== "object") return null;
  const asset = artifact as Record<string, unknown>;
  const assetId = typeof asset["asset_id"] === "number" ? asset["asset_id"] : null;
  const assetType = typeof asset["asset_type"] === "string" ? asset["asset_type"] : "";
  if (assetType !== "document.test_report") return null;
  const title = typeof asset["title"] === "string" && asset["title"].trim()
    ? asset["title"].trim()
    : "Test report";
  const storagePath = typeof asset["storage_path"] === "string" ? asset["storage_path"] : "";
  return {
    assetId,
    assetType,
    title,
    storagePath,
  };
}

function renderMessageArtifactSummary(message: MessageItem) {
  const artifact = readMessageTestReportArtifact(message);
  if (!artifact) return null;
  return (
    <div className="message-runtime-summary" aria-label="Message artifacts">
      <div className="message-runtime-summary__row">
        <span className="message-runtime-summary__label">Artifact</span>
        <span className="message-runtime-summary__detail">
          <FileText size={14} />
          <span style={{ marginLeft: 6 }}>{artifact.title}</span>
          {artifact.storagePath ? <span style={{ marginLeft: 8, opacity: 0.75 }}>{artifact.storagePath}</span> : null}
        </span>
      </div>
    </div>
  );
}

function streamTraceHistoryId(scope: string | number) {
  return `${STREAM_TRACE_HISTORY_ID_PREFIX}${scope}`;
}

function splitTraceSteps(steps: MessageStreamStep[]) {
  if (steps.length <= STREAM_TRACE_RECENT_STEP_COUNT) {
    return { historySteps: [] as MessageStreamStep[], recentSteps: steps };
  }
  return {
    historySteps: steps.slice(0, -STREAM_TRACE_RECENT_STEP_COUNT),
    recentSteps: steps.slice(-STREAM_TRACE_RECENT_STEP_COUNT),
  };
}

function renderTraceStepDetail(step: MessageStreamStep, mode: TraceStepRenderMode) {
  const detailSource = step.detailContent || step.detail || "";
  if (!detailSource) return null;

  if (mode === "task-run") {
    return renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
  }

  const isLlmStep = isLikelyLlmStep(step.label, detailSource);
  if (isLlmStep) {
    const llmDetail = parseLlmConversationMarkdown(detailSource);
    const structuredDetail = renderLlmDetailLayout(inferAgentNameFromLlmStepLabel(step.label), llmDetail, {
      className: "message-stream-step__detail-content llm-exchange-stack",
    });
    return structuredDetail || renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
  }

  if (step.kind === "tool_call") {
    return renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
  }

  if (step.state === "live") {
    return renderStreamingTextContent(detailSource, "message-stream-step__detail-content");
  }

  return renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
}

function renderTraceStep({
  step,
  isExpanded,
  onToggle,
  detailSummary,
  analysisContext,
  onAnalyzeFailureStep,
  mode,
}: {
  step: MessageStreamStep;
  isExpanded: boolean;
  onToggle: (stepId: string, isExpanded: boolean) => void;
  detailSummary?: ReactNode;
  analysisContext: FailureStepAnalysisContext;
  onAnalyzeFailureStep?: FailureStepAnalysisHandler;
  mode: TraceStepRenderMode;
}) {
  return (
    <details
      key={step.id}
      className={`message-stream-step message-stream-step--${step.state}`}
      open={isExpanded}
    >
      <summary
        className="message-stream-step__summary"
        onClick={(event) => {
          event.preventDefault();
          onToggle(step.id, isExpanded);
        }}
      >
        <span className="message-stream-step__state" aria-hidden="true">
          {step.state === "done" ? "✓" : step.state === "error" ? "!" : ""}
        </span>
        <span className="message-stream-step__copy">
          <span className="message-stream-step__title-line">
            <strong>{step.label}</strong>
            {renderStepIdBadges(step)}
          </span>
          {detailSummary}
        </span>
        <span className="message-stream-step__actions">
          {renderFailureStepAction(step, analysisContext, onAnalyzeFailureStep)}
          <span className="message-stream-step__toggle" aria-hidden="true">
            &gt;
          </span>
        </span>
      </summary>
      {isExpanded && (step.detailContent || step.detail) ? (
        <div className="message-stream-step__detail">
          {renderTraceStepDetail(step, mode)}
        </div>
      ) : null}
    </details>
  );
}

function renderTraceHistoryCard({
  historyId,
  steps,
  expandedStepId,
  onToggleStep,
  analysisContext,
  onAnalyzeFailureStep,
  mode,
}: {
  historyId: string;
  steps: MessageStreamStep[];
  expandedStepId: StepExpansionValue;
  onToggleStep: (stepId: string, isExpanded: boolean) => void;
  analysisContext: FailureStepAnalysisContext;
  onAnalyzeFailureStep?: FailureStepAnalysisHandler;
  mode: TraceStepRenderMode;
}) {
  if (steps.length === 0) return null;
  const isHistoryExpanded = expandedStepId === historyId || steps.some((step) => step.id === expandedStepId);
  const errorCount = steps.filter((step) => step.state === "error").length;
  const liveCount = steps.filter((step) => step.state === "live").length;
  const firstStep = steps[0];
  const lastStep = steps[steps.length - 1];
  const summary = [
    `${steps.length} older steps`,
    liveCount > 0 ? `${liveCount} live` : "",
    errorCount > 0 ? `${errorCount} failed` : "",
    firstStep && lastStep ? `${firstStep.label} -> ${lastStep.label}` : "",
  ].filter(Boolean).join(" · ");

  return (
    <details
      key={historyId}
      className="message-stream-step message-stream-step--history"
      open={isHistoryExpanded}
    >
      <summary
        className="message-stream-step__summary"
        onClick={(event) => {
          event.preventDefault();
          onToggleStep(historyId, isHistoryExpanded);
        }}
      >
        <span className="message-stream-step__state" aria-hidden="true">
          {steps.length}
        </span>
        <span className="message-stream-step__copy">
          <span className="message-stream-step__title-line">
            <strong>History</strong>
            <span className="message-stream-step__id">older steps</span>
          </span>
          <small>{summary}</small>
        </span>
        <span className="message-stream-step__actions">
          <span className="message-stream-step__toggle" aria-hidden="true">
            &gt;
          </span>
        </span>
      </summary>
      {isHistoryExpanded ? (
        <div className="message-stream-step__detail message-stream-step__detail--history">
          <div className="message-stream-history-list">
            {steps.map((step) =>
              renderTraceStep({
                step,
                isExpanded: expandedStepId === step.id,
                onToggle: onToggleStep,
                detailSummary:
                  mode === "task-run"
                    ? step.detail
                      ? <small>{step.detail}</small>
                      : null
                    : (() => {
                        const detail = summarizeStreamingDetail(step.detail);
                        return detail ? <small>{detail}</small> : null;
                      })(),
                analysisContext,
                onAnalyzeFailureStep,
                mode,
              }),
            )}
          </div>
        </div>
      ) : null}
    </details>
  );
}

function resolveTraceExpandedStepId(
  steps: MessageStreamStep[],
  expandedStepId: StepExpansionValue | undefined,
  currentStepId: string | null,
  historyId: string,
) {
  if (expandedStepId === undefined) return currentStepId;
  if (expandedStepId === historyId) return historyId;
  return expandedStepId && steps.some((step) => step.id === expandedStepId) ? expandedStepId : null;
}

function renderTaskRunTrace(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  activity: TaskActivityProjection | null,
  timeline: ChatTimelineProjection | null,
  expandedStepId: StepExpansionValue | undefined,
  onToggleStep: (taskRunId: number, stepId: string, isExpanded: boolean) => void,
  onAnalyzeFailureStep?: FailureStepAnalysisHandler,
) {
  const timelineTraceSteps = timeline?.steps.length
    ? [...timeline.steps]
      .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0))
      .map((step) => timelineStepToStreamStep(step, taskRun.id))
    : [];
  const traceSteps = timelineTraceSteps;
  if (traceSteps.length === 0) return null;
  const currentStepId =
    (
      timeline?.current_step_id && traceSteps.some((step) => step.id === timeline.current_step_id)
        ? timeline.current_step_id
        : null
    )
    ?? [...traceSteps].reverse().find((step) => step.state === "live")?.id
    ?? traceSteps[traceSteps.length - 1]?.id
    ?? null;
  const historyId = streamTraceHistoryId(`task-run:${taskRun.id}`);
  const resolvedExpandedStepId = resolveTraceExpandedStepId(traceSteps, expandedStepId, currentStepId, historyId);
  const { historySteps, recentSteps } = splitTraceSteps(traceSteps);
  const renderStep = (step: MessageStreamStep) =>
    renderTraceStep({
      step,
      isExpanded: resolvedExpandedStepId === step.id,
      onToggle: (stepId, isExpanded) => onToggleStep(taskRun.id, stepId, isExpanded),
      detailSummary: step.detail ? <small>{step.detail}</small> : null,
      analysisContext: { taskRun, taskRunDetail: detail },
      onAnalyzeFailureStep,
      mode: "task-run",
    });

  return (
    <div className="message-stream-trace message-stream-trace--task-run">
      {renderTraceHistoryCard({
        historyId,
        steps: historySteps,
        expandedStepId: resolvedExpandedStepId,
        onToggleStep: (stepId, isExpanded) => onToggleStep(taskRun.id, stepId, isExpanded),
        analysisContext: { taskRun, taskRunDetail: detail },
        onAnalyzeFailureStep,
        mode: "task-run",
      })}
      {recentSteps.map(renderStep)}
    </div>
  );
}

function prettyJson(value: string | undefined) {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function fileReaderPreviewKind(path: string, content: string | undefined) {
  const normalizedPath = path.toLowerCase();
  if (/\.(md|mdx|markdown)$/.test(normalizedPath)) return "markdown";
  if (/\.(json|jsonc|jsonl)$/.test(normalizedPath) && isJsonContent(content)) return "json";
  return "raw";
}

function resolveMarkdownFileLink(currentPath: string, href: string | undefined) {
  const rawHref = href?.trim() || "";
  if (!rawHref) return null;
  if (rawHref.startsWith("#")) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(rawHref)) return null;
  if (rawHref.startsWith("//")) return null;

  const withoutHash = rawHref.split("#", 1)[0];
  const withoutQuery = withoutHash.split("?", 1)[0];
  const decodedHref = (() => {
    try {
      return decodeURIComponent(withoutQuery);
    } catch {
      return withoutQuery;
    }
  })();
  const normalizedHref = normalizeBrowserPath(decodedHref);
  if (!normalizedHref || normalizedHref.endsWith("/")) return null;

  const baseParts = normalizeBrowserPath(currentPath).split("/");
  baseParts.pop();
  const parts = normalizedHref.startsWith("/")
    ? normalizedHref.split("/")
    : [...baseParts, ...normalizedHref.split("/")];
  const resolved: string[] = [];

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed || trimmed === ".") continue;
    if (trimmed === "..") {
      resolved.pop();
      continue;
    }
    resolved.push(trimmed);
  }

  return resolved.join("/");
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

function oneLinePreview(value: string | undefined, fallback: string, limit = 96) {
  const normalized = value?.replace(/\s+/g, " ").trim() ?? "";
  if (!normalized) return fallback;
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit)}...`;
}

function normalizePromptText(value: string) {
  return value.replace(/\r\n/g, "\n").trimEnd();
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

function extractDisplayableSystemPrompt(value: string) {
  const normalized = normalizePromptText(value);
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

function buildSystemPromptPresentation(current: string | undefined, _previous: string | undefined): SystemPromptPresentation | undefined {
  if (!current?.trim()) return undefined;

  const normalizedCurrent = normalizePromptText(current);
  const displayCurrent = extractDisplayableSystemPrompt(normalizedCurrent);
  if (!displayCurrent) return undefined;

  return {
    label: "System prompt context",
    markdown: markdownSection("System Prompt Context", displayCurrent, { asMarkdown: true }),
    copyText: normalizedCurrent,
    mode: "full",
  };
}

function decorateCardsWithSystemPromptPresentation(cards: ChatCardItem[]): DecoratedChatCardItem[] {
  const previousPromptByAgent = new Map<string, string>();
  const decoratedById = new Map<string, DecoratedChatCardItem>();

  const orderedCards = [...cards].sort((left, right) => {
    const timeDiff = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
    if (timeDiff !== 0) return timeDiff;
    return left.id.localeCompare(right.id);
  });

  for (const card of orderedCards) {
    if (card.kind !== "llm_call") {
      decoratedById.set(card.id, card);
      continue;
    }

    const agentName = card.agent || defaultAgentName(DEFAULT_AGENT_TYPE);
    const previousPrompt = previousPromptByAgent.get(agentName);
    const systemPromptPresentation = buildSystemPromptPresentation(card.system_prompt, previousPrompt);

    decoratedById.set(card.id, {
      ...card,
      systemPromptPresentation,
    });

    if (card.system_prompt?.trim()) {
      previousPromptByAgent.set(agentName, card.system_prompt);
    }
  }

  return cards.map((card) => decoratedById.get(card.id) ?? card);
}

function threadItemSortWeight(item: Extract<ThreadItem, { kind: "message" | "card" }>) {
  if (item.kind === "card") return 1;
  if (item.message.message_type === "user" || !item.message.agent_name) return 0;
  return 2;
}

function threadItemClientTurnId(item: Extract<ThreadItem, { kind: "message" | "card" }>) {
  if (item.kind === "card") return item.card.client_turn_id ?? null;
  return item.message.client_turn_id ?? null;
}

function compareThreadTimelineItems(
  left: Extract<ThreadItem, { kind: "message" | "card" | "task_run" }>,
  right: Extract<ThreadItem, { kind: "message" | "card" | "task_run" }>,
) {
  if (left.kind !== "task_run" && right.kind !== "task_run") {
    const leftTurnId = threadItemClientTurnId(left);
    const rightTurnId = threadItemClientTurnId(right);
    if (leftTurnId && rightTurnId && leftTurnId === rightTurnId) {
      const weightDiff = threadItemSortWeight(left) - threadItemSortWeight(right);
      if (weightDiff !== 0) return weightDiff;
    }
  } else if (left.kind === "task_run" && right.kind !== "task_run") {
    const rightTurnId = threadItemClientTurnId(right);
    if (left.taskRun.client_turn_id && rightTurnId && left.taskRun.client_turn_id === rightTurnId) {
      return 1;
    }
  } else if (left.kind !== "task_run" && right.kind === "task_run") {
    const leftTurnId = threadItemClientTurnId(left);
    if (right.taskRun.client_turn_id && leftTurnId && right.taskRun.client_turn_id === leftTurnId) {
      return -1;
    }
  }

  const timeDiff = new Date(left.sortKey).getTime() - new Date(right.sortKey).getTime();
  if (timeDiff !== 0) return timeDiff;
  if (left.kind === "task_run" || right.kind === "task_run") {
    if (left.kind === right.kind) return 0;
    return left.kind === "task_run" ? 1 : -1;
  }
  return threadItemSortWeight(left) - threadItemSortWeight(right);
}

function compareThreadCards(left: ThreadCard, right: ThreadCard) {
  const timeDiff = new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
  if (timeDiff !== 0) return timeDiff;
  return left.id.localeCompare(right.id);
}

function appendMappedCards(target: Map<number, ThreadCard[]>, messageId: number, cards: ThreadCard[]) {
  if (cards.length === 0) return;
  const existing = target.get(messageId) ?? [];
  const merged = [...existing, ...cards].sort(compareThreadCards);
  target.set(messageId, merged);
}

function normalizeClientTurnId(value: string | null | undefined) {
  const normalized = (value || "").trim();
  return normalized ? normalized : null;
}

function sameClientTurn(left?: string | null, right?: string | null) {
  const normalizedLeft = normalizeClientTurnId(left);
  const normalizedRight = normalizeClientTurnId(right);
  if (!normalizedLeft || !normalizedRight) return false;
  return normalizedLeft === normalizedRight;
}

function visibleMessageKey(message: MessageItem) {
  if (message.client_turn_id) {
    return `${message.agent_name ? "agent" : "user"}:${message.client_turn_id}`;
  }
  return `id:${message.id}`;
}

function mergeVisibleMessagePair(left: MessageItem, right: MessageItem): MessageItem {
  const server = !left.localOnly ? left : !right.localOnly ? right : null;
  const local = left.localOnly ? left : right.localOnly ? right : null;
  const primary = server ?? right;
  const secondary = primary === left ? right : left;
  const primarySteps = primary.streamSteps ?? [];
  const secondarySteps = secondary.streamSteps ?? [];

  return {
    ...primary,
    id: server?.id ?? primary.id,
    created_at:
      new Date(left.created_at).getTime() <= new Date(right.created_at).getTime() ? left.created_at : right.created_at,
    content: primary.content || secondary.content,
    agent_name: primary.agent_name ?? secondary.agent_name,
    message_type: primary.message_type || secondary.message_type,
    client_turn_id: primary.client_turn_id ?? secondary.client_turn_id,
    isStreaming: server ? Boolean(primary.isStreaming) : Boolean(left.isStreaming || right.isStreaming),
    statusDetail: primary.statusDetail ?? secondary.statusDetail,
    streamSteps:
      primarySteps.length >= secondarySteps.length
        ? primarySteps
        : secondarySteps,
    optimisticKind: server ? undefined : primary.optimisticKind ?? secondary.optimisticKind,
    localOnly: server ? false : Boolean(primary.localOnly || secondary.localOnly),
  };
}

function cardBadge(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      return "LLM";
    case "tool_call":
      return "TOOL";
    case "consult_call":
      return "ASK";
    case "agent_error":
      return "ERR";
    case "tool_merge":
      return "TOOLS";
    case "stage_start":
      return "STAGE";
    case "stage_end":
      return "DONE";
    case "gate_blocked":
      return "GATE";
    case "gate_approved":
      return "OK";
    case "gate_rejected":
      return "BACK";
    case "skill_inject":
      return "SKILL";
    case "agent_message":
      return "MSG";
    case "boss_instruction":
      return "BOSS";
    default:
      return "CARD";
  }
}

function cardTitle(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      if (card.finish_reason === "tool_calls") {
        return `LLM requested tools for ${card.agent || defaultAgentName(DEFAULT_AGENT_TYPE)}`;
      }
      if (card.finish_reason === "stop") {
        return `LLM returned final answer to ${card.agent || defaultAgentName(DEFAULT_AGENT_TYPE)}`;
      }
      if (card.finish_reason === "length") {
        return `LLM returned partial answer to ${card.agent || defaultAgentName(DEFAULT_AGENT_TYPE)}`;
      }
      return `${card.agent || defaultAgentName(DEFAULT_AGENT_TYPE)} contacting LLM`;
    case "tool_call":
      if (parseInteractiveFileToolRequest(card)) {
        return "Open file for user";
      }
      return card.tool || "Tool call";
    case "consult_call":
      return `${card.agent || "agent"} consulting ${card.target_agent || "agent"}`;
    case "agent_error":
      return `${card.agent || defaultAgentName(DEFAULT_AGENT_TYPE)} stream failed`;
    case "tool_merge":
      return `${card.tool || "tool"} x${card.count}`;
    case "stage_start":
      return card.display_name || card.stage || "Stage started";
    case "stage_end":
      return `${card.stage || "Stage"} completed`;
    case "gate_blocked":
      return `Manual gate · ${card.display_name || card.stage || "approval needed"}`;
    case "gate_approved":
      return `Gate approved · ${card.stage || "pipeline"}`;
    case "gate_rejected":
      return `Gate rejected · ${card.from_stage || "stage"} -> ${card.to_stage || "rollback"}`;
    case "skill_inject":
      return `${card.agent || "agent"} skill injection`;
    case "agent_message":
      return `${card.from_agent || "agent"} -> ${card.to_agent || "team"}`;
    case "boss_instruction":
      return `Instruction for ${card.agent || "agent"}`;
    default:
      return "Runtime event";
  }
}

function cardSummary(card: ThreadCard) {
  const rawSummary = (() => {
    switch (card.kind) {
      case "llm_call":
        if (card.finish_reason === "tool_calls") {
          return card.tool_calls && card.tool_calls.length > 0
            ? `Requested tools: ${card.tool_calls.map((tool) => tool.name || "tool").join(", ")}`
            : "Model requested tool calls before answering.";
        }
        if (card.finish_reason === "length") {
          return "Model returned a partial answer because the output hit a length limit.";
        }
        return card.response || "Model response captured.";
      case "tool_call":
        {
          const fileRequest = parseInteractiveFileToolRequest(card);
          if (fileRequest) return fileRequest.path;
        }
        return card.result || "Tool execution recorded.";
      case "consult_call":
        if (card.summary) {
          return card.summary;
        }
        if (card.status === "failed") {
          return card.error || `Consult with ${card.target_agent || "agent"} failed.`;
        }
        if (card.response_preview) {
          return `${card.target_agent || "agent"}: ${card.response_preview}`;
        }
        return card.question_preview || `Consulting ${card.target_agent || "agent"}.`;
      case "agent_error":
        return card.error || card.summary || "Agent stream failed before a final reply was saved.";
      case "tool_merge":
        return `${card.count} consecutive ${card.tool || "tool"} calls from ${card.agent || "agent"}.`;
      case "stage_start":
        return card.summary || "A pipeline stage has started.";
      case "stage_end":
        return card.summary || "A pipeline stage has completed.";
      case "gate_blocked":
        return "Waiting for manual approval before continuing.";
      case "gate_approved":
        return "Manual gate approved.";
      case "gate_rejected":
        return `Rollback to ${card.to_stage || "previous stage"} requested.`;
      case "skill_inject":
        return card.skills?.map((skill) => skill.name).filter(Boolean).join(", ") || "Skills injected.";
      case "agent_message":
        return card.content || "Agent handoff message.";
      case "boss_instruction":
        return card.content_preview || "Boss instruction recorded.";
      default:
        return "";
    }
  })();

  if (rawSummary.length <= 180) return rawSummary;
  return `${rawSummary.slice(0, 180)}...`;
}

function cardActorName(card: ThreadCard) {
  if ("agent" in card && typeof card.agent === "string" && card.agent) {
    return card.agent;
  }

  if ("from_agent" in card && typeof card.from_agent === "string" && card.from_agent) {
    return card.from_agent;
  }

  if ("to_agent" in card && typeof card.to_agent === "string" && card.to_agent) {
    return card.to_agent;
  }

  return "system";
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

function compactToolCallId(toolCallId?: string | null) {
  const normalized = (toolCallId || "").trim();
  if (!normalized) return "";
  if (normalized.length <= 12) return normalized;
  return `${normalized.slice(0, 6)}...${normalized.slice(-4)}`;
}

function renderStepIdBadges(step: MessageStreamStep) {
  const badges: string[] = [];
  if (typeof step.runId === "number" && Number.isFinite(step.runId)) {
    badges.push(`task #${step.runId}`);
  }
  const callId = compactToolCallId(step.toolCallId);
  if (callId) {
    badges.push(`call ${callId}`);
  } else if (typeof step.toolCallIndex === "number" && Number.isFinite(step.toolCallIndex)) {
    badges.push(`call #${step.toolCallIndex + 1}`);
  }
  if (badges.length === 0) return null;
  return (
    <span className="message-stream-step__ids" aria-label={badges.join(", ")}>
      {badges.map((badge) => (
        <span key={badge} className="message-stream-step__id">
          {badge}
        </span>
      ))}
    </span>
  );
}

function buildLlmMetaSummary(model?: string, turn?: number) {
  return [
    model,
    typeof turn === "number" ? `turn ${turn}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

type LlmUsageSummary = {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  contextWindow?: number;
  contextUsageRatio?: number;
  toolCalls?: number;
  llmCalls: number;
};

function formatUsageNumber(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return value.toLocaleString("en-US");
}

function formatCompactTokenCount(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  if (Math.abs(value) < 1000) return `${Math.round(value)}`;
  const units = [
    { value: 1_000_000_000, suffix: "b" },
    { value: 1_000_000, suffix: "m" },
    { value: 1_000, suffix: "k" },
  ];
  for (const unit of units) {
    if (Math.abs(value) < unit.value) continue;
    const scaled = value / unit.value;
    const digits = Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 1;
    return `${scaled.toFixed(digits).replace(/\.0$/, "")}${unit.suffix}`;
  }
  return formatUsageNumber(value);
}

function formatUsagePercent(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const percent = value * 100;
  return `${percent.toFixed(percent >= 10 ? 0 : 1)}%`;
}

function summarizeLlmUsage(cards: ThreadCard[]): LlmUsageSummary | null {
  let llmCalls = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  let totalTokens = 0;
  let toolCalls = 0;
  let hasInput = false;
  let hasOutput = false;
  let hasTotal = false;
  let hasToolCalls = false;
  let contextWindow: number | undefined;
  let contextUsageRatio: number | undefined;

  cards.forEach((card) => {
    if (card.kind === "tool_call") {
      toolCalls += 1;
      hasToolCalls = true;
      return;
    }

    if (card.kind !== "llm_call") return;
    llmCalls += 1;

    if (typeof card.tokens_in === "number" && Number.isFinite(card.tokens_in) && card.tokens_in > 0) {
      inputTokens += card.tokens_in;
      hasInput = true;
    }
    if (typeof card.tokens_out === "number" && Number.isFinite(card.tokens_out) && card.tokens_out > 0) {
      outputTokens += card.tokens_out;
      hasOutput = true;
    }
    if (typeof card.tokens_total === "number" && Number.isFinite(card.tokens_total) && card.tokens_total > 0) {
      totalTokens += card.tokens_total;
      hasTotal = true;
    }

    if (typeof card.context_window === "number" && Number.isFinite(card.context_window) && card.context_window > 0) {
      contextWindow = card.context_window;
    }
    if (typeof card.context_usage_ratio === "number" && Number.isFinite(card.context_usage_ratio) && card.context_usage_ratio > 0) {
      contextUsageRatio = card.context_usage_ratio;
    } else if (
      typeof card.tokens_in === "number" &&
      Number.isFinite(card.tokens_in) &&
      card.tokens_in > 0 &&
      typeof card.context_window === "number" &&
      Number.isFinite(card.context_window) &&
      card.context_window > 0
    ) {
      contextUsageRatio = card.tokens_in / card.context_window;
    }

    if (!hasToolCalls && Array.isArray(card.tool_calls) && card.tool_calls.length > 0) {
      toolCalls += card.tool_calls.length;
    }
  });

  if (llmCalls === 0) return null;
  if (!hasTotal && (hasInput || hasOutput)) {
    totalTokens = inputTokens + outputTokens;
    hasTotal = totalTokens > 0;
  }

  return {
    llmCalls,
    inputTokens: hasInput ? inputTokens : undefined,
    outputTokens: hasOutput ? outputTokens : undefined,
    totalTokens: hasTotal ? totalTokens : undefined,
    contextWindow,
    contextUsageRatio,
    toolCalls: toolCalls > 0 ? toolCalls : undefined,
  };
}

function renderLlmUsageFooter(summary: LlmUsageSummary | null, className = "chat-usage-footer") {
  if (!summary) return null;

  const items: string[] = [];
  if (typeof summary.inputTokens === "number") items.push(`in ${formatCompactTokenCount(summary.inputTokens)}`);
  if (typeof summary.outputTokens === "number") items.push(`out ${formatCompactTokenCount(summary.outputTokens)}`);
  if (typeof summary.totalTokens === "number") items.push(`tok ${formatCompactTokenCount(summary.totalTokens)}`);
  if (summary.contextUsageRatio !== undefined && summary.contextWindow !== undefined) {
    items.push(`ctx ${formatUsagePercent(summary.contextUsageRatio)} · ${formatCompactTokenCount(summary.contextWindow)}`);
  } else if (summary.contextUsageRatio !== undefined) {
    items.push(`ctx ${formatUsagePercent(summary.contextUsageRatio)}`);
  } else if (summary.contextWindow !== undefined) {
    items.push(`ctxwin ${formatCompactTokenCount(summary.contextWindow)}`);
  }
  if (typeof summary.toolCalls === "number") items.push(`tools ${summary.toolCalls}`);
  items.push(`llm ${summary.llmCalls}`);

  if (items.length === 0) return null;

  return (
    <div className={className}>
      {items.map((item) => (
        <span key={item} className="chat-usage-footer__item">
          {item}
        </span>
      ))}
    </div>
  );
}

function messageStepStateFromCard(card: ThreadCard): MessageStreamStep["state"] {
  if (isInternalToolPause(card)) return "live";
  if (isToolCardFailure(card)) return "error";
  if (card.kind === "agent_error") return "error";
  if (card.kind === "gate_blocked" || card.kind === "gate_rejected") return "error";
  return "done";
}

function messageStepSummaryFromCard(card: ThreadCard) {
  return compactCardSummary(card);
}

function messageStepDetailFromCard(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call": {
      const sections: string[] = [];
      const meta = [
        card.model,
        typeof card.turn === "number" ? `turn ${card.turn}` : "",
        typeof card.duration_ms === "number" ? `${card.duration_ms}ms` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      if (meta) sections.push(`### Meta\n\n- ${meta}`);
      {
        const timingsMarkdown = buildLlmTimingsMarkdown(card.timings);
        if (timingsMarkdown) sections.push(timingsMarkdown);
      }
      if (card.systemPromptPresentation?.markdown) sections.push(card.systemPromptPresentation.markdown);
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
    case "tool_merge":
      return card.items
        .map(
          (item, index) =>
            `### #${index + 1} ${item.tool || card.tool || "tool"}\n\n${item.arguments ? `${markdownSection("Arguments", prettyJson(item.arguments), { language: "json" })}\n\n` : ""}${item.result ? toolResultMarkdownSection("Result", item.result) : ""}`,
        )
        .join("\n\n");
    case "stage_start":
    case "stage_end":
      return card.summary || card.content || card.stage || "";
    case "skill_inject":
      return card.skills?.map((skill) => `${skill.name || "skill"}${skill.hint ? `\n${skill.hint}` : ""}`).join("\n\n") || "";
    case "agent_message":
      return card.content || "";
    case "boss_instruction":
      return card.content_preview || "";
    case "gate_blocked":
      return `Waiting for approval${card.display_name ? `: ${card.display_name}` : ""}`;
    case "gate_rejected":
      return `Rollback target: ${card.to_stage || "previous stage"}`;
    default:
      return cardSummary(card);
  }
}

function messageLlmPromptDetailFromCard(card: DecoratedChatCardItem) {
  const sections: string[] = [];
  const meta = buildLlmMetaSummary(card.model, card.turn);
  if (meta) sections.push(`### Meta\n\n- ${meta}`);
  {
    const timingsMarkdown = buildLlmTimingsMarkdown(card.timings);
    if (timingsMarkdown) sections.push(timingsMarkdown);
  }
  if (card.systemPromptPresentation?.markdown) sections.push(card.systemPromptPresentation.markdown);
  if (card.prompt_messages) {
    sections.push(markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }));
  }
  return sections.join("\n\n");
}

function messageLlmResponseDetailFromCard(card: DecoratedChatCardItem) {
  const sections: string[] = [];
  {
    const timingsMarkdown = buildLlmTimingsMarkdown(card.timings);
    if (timingsMarkdown) sections.push(timingsMarkdown);
  }
  if (card.finish_reason === "tool_calls") {
    sections.push("### Status\n\nModel requested more tool output before it could produce a final answer.");
  } else if (card.finish_reason === "length") {
    sections.push("### Status\n\nModel returned a partial answer because the output hit a length limit.");
  }
  const plannedToolsMarkdown = buildPlannedToolsMarkdown(card.tool_calls);
  if (plannedToolsMarkdown) sections.push(plannedToolsMarkdown);
  if (card.response) sections.push(markdownSection("Response", card.response, { asMarkdown: true }));
  if (card.raw_response) sections.push(markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }));
  return sections.join("\n\n");
}

function messageToolCallDetailFromCard(card: DecoratedChatCardItem) {
  if (!card.arguments) return "";
  return markdownSection("Arguments", prettyJson(card.arguments), { language: "json" });
}

function toolResultMarkdownSection(title: string, content: string) {
  return isJsonContent(content)
    ? markdownSection(title, prettyJson(content), { language: "json" })
    : markdownSection(title, content, { language: "text" });
}

function messageToolResultDetailFromCard(card: DecoratedChatCardItem) {
  if (!card.result) return "";
  return toolResultMarkdownSection(isToolCardFailure(card) ? "Error" : "Tool Result", card.result);
}

function buildMessageStepsFromCard(card: ThreadCard, messageId: number, index: number): MessageStreamStep[] {
  const actor = cardActorName(card);

  switch (card.kind) {
    case "llm_call":
      return [
        {
          id: `${messageId}-card-${card.id}-${index}-prompt`,
          label: llmOutboundStepLabel(actor),
          detail: buildLlmMetaSummary(card.model, card.turn),
          detailContent: messageLlmPromptDetailFromCard(card),
          state: "done",
          kind: "llm_outbound",
          agent: actor,
        },
        {
          id: `${messageId}-card-${card.id}-${index}-response`,
          label: llmInboundStepLabel(actor, card.finish_reason),
          detail: compactCardSummary(card),
          detailContent: messageLlmResponseDetailFromCard(card),
          state: "done",
          kind: "llm_inbound",
          agent: actor,
        },
      ];
    case "tool_call": {
      const toolName = card.tool || "tool";
      return [
          {
            id: `${messageId}-card-${card.id}-${index}-tool`,
            label: toolCallStepLabel(actor, toolName),
            detail: compactCardSummary(card) || (card.arguments ? `args: ${oneLinePreview(card.arguments, "prepared")}` : "Calling tool."),
          detailContent: [
            messageToolCallDetailFromCard(card),
            messageToolResultDetailFromCard(card),
          ].filter(Boolean).join("\n\n"),
          state: messageStepStateFromCard(card),
          kind: "tool_call",
            agent: actor,
            tool: toolName,
            runId: typeof card.run_id === "number" ? card.run_id : undefined,
            toolCallIndex: typeof card.tool_call_index === "number" ? card.tool_call_index : undefined,
            toolCallId: card.tool_call_id ?? null,
          },
        ];
    }
    case "agent_error":
      return [
        {
          id: `${messageId}-card-${card.id}-${index}-error`,
          label: `${actor} failed before finishing the turn`,
          detail: compactCardSummary(card),
          detailContent: messageStepDetailFromCard(card),
          state: "error",
          kind: "agent_message",
          agent: actor,
        },
      ];
    default:
      return [
        {
          id: `${messageId}-card-${card.id}-${index}`,
          label: cardTitle(card),
          detail: messageStepSummaryFromCard(card),
          detailContent: messageStepDetailFromCard(card),
          state: messageStepStateFromCard(card),
        },
      ];
  }
}

function CopyTextButton({ content, title }: { content: string; title: string }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    },
    [],
  );

  const handleClick = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      className={`chat-copy-inline-btn ${copied ? "is-copied" : ""}`}
      onClick={(event) => void handleClick(event)}
      title={title}
      aria-label={title}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function renderFileReaderPreview(path: string, content: string, onOpenLinkedFile?: (path: string) => void) {
  const kind = fileReaderPreviewKind(path, content);
  if (kind === "markdown") {
    return (
      <div className="file-reader-card__rendered file-reader-card__rendered--markdown">
        <div className="message-markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={content.length <= LARGE_MARKDOWN_HIGHLIGHT_LIMIT ? [rehypeHighlight] : []}
            components={{
              a: ({ node: _node, href, children, ...props }) => {
                const linkedPath = resolveMarkdownFileLink(path, href);
                if (!linkedPath || !onOpenLinkedFile) {
                  return <a href={href} {...props} target="_blank" rel="noreferrer">{children}</a>;
                }
                return (
                  <a
                    href={href}
                    {...props}
                    onClick={(event) => {
                      event.preventDefault();
                      onOpenLinkedFile(linkedPath);
                    }}
                    title={`Open ${linkedPath}`}
                  >
                    {children}
                  </a>
                );
              },
              table: ({ node: _node, ...props }) => (
                <div className="message-markdown__table-wrap">
                  <table {...props} />
                </div>
              ),
              input: ({ node: _node, ...props }) =>
                props.type === "checkbox" ? <input {...props} disabled readOnly className="message-markdown__checkbox" /> : <input {...props} />,
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    );
  }
  if (kind === "json") {
    return <pre className="file-reader-card__content">{prettyJson(content)}</pre>;
  }
  return <pre className="file-reader-card__content">{content}</pre>;
}

function FileReaderCard({
  state,
  onClose,
  onEdit,
  onDraftChange,
  onDiscard,
  onSave,
  onOpenLinkedFile,
  onBack,
  onForward,
}: { state: FileReaderState } & FileReaderCardActions) {
  const data = state.data;
  const path = data?.path || state.path;
  const isEditable = Boolean(data && !data.binary && !data.truncated);
  const isEditing = state.mode === "edit" && isEditable;
  const resolvedContent = isEditing ? state.draft ?? data?.content ?? "" : data?.content ?? "";
  const lineCount = resolvedContent ? resolvedContent.split("\n").length : 0;
  const dirty = isEditing && (state.draft ?? "") !== (data?.content ?? "");
  const canGoBack = (state.historyIndex ?? 0) > 0;
  const canGoForward = (state.historyIndex ?? 0) < (state.history?.length ?? 0) - 1;

  return (
    <article className="file-reader-card">
      <div className="file-reader-card__header">
        <div className="file-reader-card__title">
          <span className="file-reader-card__icon" aria-hidden="true"><FileText size={16} /></span>
          <div>
            <strong>{path}</strong>
            <div className="file-reader-card__meta">
              {data ? <span>{formatFileSize(data.size)}</span> : null}
              {data?.mtime ? <span>{formatTime(new Date(data.mtime * 1000).toISOString())}</span> : null}
              {data?.encoding ? <span>{data.encoding}</span> : null}
              {data?.truncated ? <span>truncated at {formatFileSize(data.preview_limit)}</span> : null}
            </div>
          </div>
        </div>
        <div className="file-reader-card__actions">
          <button type="button" className="chat-copy-inline-btn" onClick={onBack} disabled={!canGoBack} title="Previous file">
            Back
          </button>
          <button type="button" className="chat-copy-inline-btn" onClick={onForward} disabled={!canGoForward} title="Next file">
            Next
          </button>
          <CopyTextButton content={path} title="Copy path" />
          {data && !data.binary ? <CopyTextButton content={data.content} title="Copy file content" /> : null}
          {isEditable && !isEditing ? (
            <button type="button" className="chat-copy-inline-btn" onClick={onEdit} title="Edit file">
              Edit
            </button>
          ) : null}
          {isEditing ? (
            <>
              <button type="button" className="chat-copy-inline-btn" onClick={onDiscard} disabled={state.saving} title="Discard changes">
                Discard
              </button>
              <button type="button" className="chat-copy-inline-btn" onClick={onSave} disabled={!dirty || state.saving} title="Save file">
                {state.saving ? "Saving" : "Save"}
              </button>
            </>
          ) : null}
          {onClose ? (
            <button type="button" className="chat-copy-inline-btn" onClick={onClose} title="Close file reader">
              Close
            </button>
          ) : null}
        </div>
      </div>

      {state.status === "loading" ? (
        <div className="file-reader-card__empty">Loading file...</div>
      ) : state.status === "error" ? (
        <div className="file-reader-card__empty is-error">{state.error || "Unable to read file."}</div>
      ) : data?.binary ? (
        <div className="file-reader-card__empty">
          Binary file preview is not available. Size: {formatFileSize(data.size)}.
        </div>
      ) : isEditing ? (
        <div className="file-reader-card__body">
          <div className="file-reader-card__body-meta">
            {lineCount} lines{state.saveMessage ? ` - ${state.saveMessage}` : ""}
          </div>
          <textarea
            className="file-reader-card__editor"
            value={state.draft ?? ""}
            onChange={(event) => onDraftChange(event.target.value)}
            spellCheck={false}
          />
        </div>
      ) : (
        <div className="file-reader-card__body">
          <div className="file-reader-card__body-meta">
            {lineCount} lines{state.saveMessage ? ` - ${state.saveMessage}` : ""}
          </div>
          {renderFileReaderPreview(path, resolvedContent, onOpenLinkedFile)}
        </div>
      )}
    </article>
  );
}

function ToolFileReaderCard({
  request,
  fallbackProjectId,
}: {
  request: InteractiveFileToolRequest;
  fallbackProjectId?: number | null;
}) {
  const projectId = request.projectId ?? fallbackProjectId ?? null;
  const [state, setState] = useState<FileReaderState>({
    path: request.path,
    status: "loading",
    mode: request.mode,
    history: [request.path],
    historyIndex: 0,
  });

  const openToolProjectFilePath = useCallback((path: string, options?: { history?: string[]; historyIndex?: number }) => {
    let cancelled = false;
    if (!projectId) {
      setState({
        path,
        status: "error",
        error: "No project is available for this file interaction.",
      });
      return () => {
        cancelled = true;
      };
    }

    let nextHistory = options?.history;
    let nextHistoryIndex = options?.historyIndex;
    setState((current) => {
      if (!nextHistory) {
        const currentHistory = current.history?.length ? current.history : [current.path].filter(Boolean);
        const currentIndex = typeof current.historyIndex === "number" ? current.historyIndex : currentHistory.length - 1;
        if (currentHistory[currentIndex] === path) {
          nextHistory = currentHistory;
          nextHistoryIndex = currentIndex;
        } else {
          nextHistory = [...currentHistory.slice(0, currentIndex + 1), path];
          nextHistoryIndex = nextHistory.length - 1;
        }
      }
      return {
        path,
        status: "loading",
        mode: request.mode,
        history: nextHistory,
        historyIndex: nextHistoryIndex,
      };
    });
    api.readProjectFile(projectId, path)
      .then((data) => {
        if (cancelled) return;
        const canEdit = request.mode === "edit" && !data.binary && !data.truncated;
        setState((current) => {
          const history = [...(current.history ?? [data.path])];
          const historyIndex = typeof current.historyIndex === "number" ? current.historyIndex : history.length - 1;
          if (historyIndex >= 0) history[historyIndex] = data.path;
          return {
            path: data.path,
            status: "ready",
            data,
            mode: canEdit ? "edit" : "read",
            draft: data.content,
            history,
            historyIndex,
          };
        });
      })
      .catch((error) => {
        if (cancelled) return;
        setState((current) => ({
          ...current,
          path,
          status: "error",
          error: error instanceof Error ? error.message : "Unable to read file.",
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, request.mode]);

  useEffect(() => {
    return openToolProjectFilePath(request.path, { history: [request.path], historyIndex: 0 });
  }, [projectId, request.mode, request.path]);

  const saveDraft = useCallback(async () => {
    if (!projectId || !state.data || state.mode !== "edit") return;
    const content = state.draft ?? "";
    setState((current) => ({ ...current, saving: true, error: "", saveMessage: "" }));
    try {
      const data = await api.writeProjectFile(projectId, {
        path: state.data.path,
        content,
        expected_mtime: state.data.mtime,
      });
      setState({
        path: data.path,
        status: "ready",
        data,
        mode: "read",
        draft: data.content,
        saving: false,
        saveMessage: "Saved",
        history: state.history,
        historyIndex: state.historyIndex,
      });
    } catch (error) {
      setState((current) => ({
        ...current,
        saving: false,
        status: "ready",
        saveMessage: error instanceof Error ? error.message : "Unable to save file.",
      }));
    }
  }, [projectId, state]);

  return (
    <FileReaderCard
      state={state}
      onEdit={() => {
        setState((current) => current.data
          ? { ...current, mode: "edit", draft: current.data.content, saveMessage: "" }
          : current);
      }}
      onDraftChange={(value) => {
        setState((current) => ({ ...current, draft: value, saveMessage: "" }));
      }}
      onDiscard={() => {
        setState((current) => current.data
          ? { ...current, mode: "read", draft: current.data.content, saveMessage: "" }
          : current);
      }}
      onSave={() => void saveDraft()}
      onOpenLinkedFile={(path) => {
        openToolProjectFilePath(path);
      }}
      onBack={() => {
        const history = state.history ?? [];
        const nextIndex = Math.max((state.historyIndex ?? 0) - 1, 0);
        const path = history[nextIndex];
        if (path) openToolProjectFilePath(path, { history, historyIndex: nextIndex });
      }}
      onForward={() => {
        const history = state.history ?? [];
        const nextIndex = Math.min((state.historyIndex ?? 0) + 1, history.length - 1);
        const path = history[nextIndex];
        if (path) openToolProjectFilePath(path, { history, historyIndex: nextIndex });
      }}
    />
  );
}

function renderJsonCollapse(
  badge: string,
  label: string,
  content: string | undefined,
  options?: { pretty?: boolean; open?: boolean; copyLabel?: string },
) {
  if (!content) return null;
  const prepared = options?.pretty === false ? content : prettyJson(content);
  return (
    <details className="chat-json-collapse" open={options?.open}>
      <summary className="chat-json-summary">
        <span className="chat-json-summary__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-json-label">{label}</span>
        </span>
        <CopyTextButton content={prepared} title={options?.copyLabel || `Copy ${label}`} />
      </summary>
      <pre className="chat-json-content">{prepared}</pre>
    </details>
  );
}

function renderMarkdownCollapse(
  badge: string,
  label: string,
  content: string | undefined,
  options?: { open?: boolean; copyLabel?: string; copyText?: string },
) {
  if (!content?.trim()) return null;
  return (
    <details className="chat-json-collapse" open={options?.open}>
      <summary className="chat-json-summary">
        <span className="chat-json-summary__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-json-label">{label}</span>
        </span>
        <CopyTextButton content={options?.copyText || content} title={options?.copyLabel || `Copy ${label}`} />
      </summary>
      {renderMarkdownContent(content, "chat-json-markdown-content")}
    </details>
  );
}

function renderProgressTextBlock(badge: string, label: string, content: string | undefined) {
  if (!content) return null;
  return (
    <section className="chat-progress-detail-block">
      <div className="chat-progress-detail-block__header">
        <span className="chat-progress-detail-block__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-progress-detail-block__label">{label}</span>
        </span>
        <CopyTextButton content={content} title={`Copy ${label}`} />
      </div>
      {renderMarkdownContent(content, "chat-progress-detail-block__content")}
    </section>
  );
}

function renderProgressJsonBlock(badge: string, label: string, content: string | undefined) {
  if (!content) return null;
  return (
    <section className="chat-progress-detail-block">
      <div className="chat-progress-detail-block__header">
        <span className="chat-progress-detail-block__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-progress-detail-block__label">{label}</span>
        </span>
        <CopyTextButton content={prettyJson(content)} title={`Copy ${label}`} />
      </div>
      {renderMarkdownContent(`\`\`\`json\n${prettyJson(content)}\n\`\`\``, "chat-progress-detail-block__content")}
    </section>
  );
}

function buildMarkdownHeadingSection(title: string | null, body: string) {
  const normalizedBody = body.trim();
  if (!normalizedBody) return "";
  if (!title) return normalizedBody;
  return `### ${title}\n\n${normalizedBody}`;
}

function splitMarkdownHeadingSections(content: string) {
  const normalized = content.trim();
  if (!normalized) return [] as Array<{ title: string | null; body: string }>;

  const headingMatches = [...normalized.matchAll(/^###\s+(.+?)\s*$/gm)];
  if (headingMatches.length === 0) {
    return [{ title: null, body: normalized }];
  }

  const sections: Array<{ title: string | null; body: string }> = [];
  let cursor = 0;
  let activeTitle: string | null = null;

  for (const match of headingMatches) {
    const matchIndex = match.index ?? 0;
    const body = normalized.slice(cursor, matchIndex).trim();
    if (activeTitle !== null && body) {
      sections.push({ title: activeTitle, body });
    }
    activeTitle = match[1]?.trim() ?? null;
    cursor = matchIndex + match[0].length;
  }

  const tail = normalized.slice(cursor).trim();
  if (activeTitle !== null && tail) {
    sections.push({ title: activeTitle, body: tail });
  }

  return sections;
}

function isLikelyLlmStep(label: string, detailContent: string | undefined) {
  const sample = `${label}\n${detailContent ?? ""}`.toLowerCase();
  return (
    sample.includes("contacting llm") ||
    sample.includes("-> llm") ||
    sample.includes("llm ->") ||
    sample.includes("full prompt payload") ||
    sample.includes("raw response") ||
    sample.includes("current response draft") ||
    sample.includes("prompt sent to llm")
  );
}

function inferAgentNameFromLlmStepLabel(label: string) {
  const outboundMatch = label.match(/^(.+?)\s*->\s*llm(?:\s*\(|$)/i);
  if (outboundMatch?.[1]) return outboundMatch[1].trim();
  const inboundMatch = label.match(/^llm\s*->\s*(.+)$/i);
  if (inboundMatch?.[1]) return inboundMatch[1].trim();
  const matched = label.match(/^(.+?)\s+contacting\s+llm$/i);
  if (matched?.[1]) return matched[1].trim();
  const firstWord = label.trim().split(/\s+/)[0];
  return firstWord || "agent";
}

function buildPlannedToolsMarkdown(toolCalls: ChatCardItem["tool_calls"]) {
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

function parseLlmConversationMarkdown(content: string) {
  const cached = llmConversationMarkdownCache.get(content);
  if (cached) return cached;

  const sections = splitMarkdownHeadingSections(content);
  const metaSections: string[] = [];
  const outboundSections: string[] = [];
  const inboundSections: string[] = [];
  const uncategorizedSections: string[] = [];

  for (const section of sections) {
    const title = section.title?.trim().toLowerCase() ?? "";
    const rendered = buildMarkdownHeadingSection(section.title, section.body);
    if (!rendered) continue;

    if (title === "meta") {
      metaSections.push(rendered);
      continue;
    }

    if (
      title === "user message" ||
      title === "system prompt" ||
      title === "system prompt context" ||
      title === "full prompt payload" ||
      title === "prompt sent to llm" ||
      title === "tool result"
    ) {
      outboundSections.push(rendered);
      continue;
    }

    if (
      title === "planned tools" ||
      title === "current response draft" ||
      title === "response" ||
      title === "llm response" ||
      title === "raw response" ||
      title === "status"
    ) {
      inboundSections.push(rendered);
      continue;
    }

    uncategorizedSections.push(rendered);
  }

  if (sections.length === 1 && sections[0]?.title === null) {
    inboundSections.push(sections[0].body.trim());
  } else if (uncategorizedSections.length > 0) {
    if (outboundSections.length === 0) {
      outboundSections.push(...uncategorizedSections);
    } else {
      inboundSections.push(...uncategorizedSections);
    }
  }

  const parsed = {
    meta: metaSections.join("\n\n"),
    outbound: outboundSections.join("\n\n"),
    inbound: inboundSections.join("\n\n"),
  };
  rememberLlmConversationMarkdown(content, parsed);
  return parsed;
}

function renderLlmExchangePanel(
  direction: "outbound" | "inbound",
  actorName: string,
  content: string,
  metaLabel?: string,
) {
  if (!content.trim()) return null;

  const isOutbound = direction === "outbound";
  const badge = isOutbound ? "Prompt" : "Response";
  const title = isOutbound ? `Prompt · ${actorName} -> LLM` : `Response · LLM -> ${actorName}`;
  const avatarLabel = initials(actorName) || "AG";

  return (
    <section className={`llm-exchange-card llm-exchange-card--${direction}`}>
      <div className="llm-exchange-card__header">
        <div className="llm-exchange-card__heading">
          <span className={`llm-exchange-card__avatar llm-exchange-card__avatar--${direction}`} aria-hidden="true">
            {isOutbound ? (
              <>
                <svg viewBox="0 0 24 24" fill="none">
                  <path
                    d="M12 12a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5ZM6 18.25c0-2.68 2.69-4.75 6-4.75s6 2.07 6 4.75"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                <span className="llm-exchange-card__avatar-tag">{avatarLabel}</span>
              </>
            ) : (
              <svg viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 3.5 14.3 8l4.95.72-3.58 3.49.84 4.92L12 14.88 7.49 17.13l.86-4.92L4.76 8.72 9.7 8 12 3.5Z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </span>
          <span className={`llm-exchange-card__badge llm-exchange-card__badge--${direction}`}>
            {isOutbound ? (
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M3.25 8h8.5M8.75 3.5 12.5 8l-3.75 4.5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : (
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="m8 2 1.24 2.76L12 6l-2.76 1.24L8 10 6.76 7.24 4 6l2.76-1.24L8 2Z"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinejoin="round"
                />
              </svg>
            )}
            <span>{badge}</span>
          </span>
          <strong>{title}</strong>
        </div>
        {metaLabel ? <span className="llm-exchange-card__meta">{metaLabel}</span> : null}
      </div>
      <div className="llm-exchange-card__body">
        {renderMarkdownContent(content, "llm-exchange-card__markdown", { highlight: false })}
      </div>
    </section>
  );
}

function renderLlmDetailLayout(
  agentName: string,
  content: {
    meta?: string;
    outbound?: string;
    inbound?: string;
  },
  options?: {
    responseMetaLabel?: string;
    className?: string;
  },
) {
  if (!content.meta && !content.outbound && !content.inbound) return null;

  const outboundContent = [content.meta?.trim(), content.outbound?.trim()].filter(Boolean).join("\n\n");

  return (
    <div className={options?.className ?? "llm-exchange-stack"}>
      {outboundContent ? renderLlmExchangePanel("outbound", agentName, outboundContent) : null}
      {content.inbound
        ? renderLlmExchangePanel("inbound", agentName, content.inbound, options?.responseMetaLabel)
        : null}
    </div>
  );
}

function renderMarkdownContent(content: string | undefined, className: string, options?: { highlight?: boolean }) {
  if (!content) return null;
  const enableHighlight = options?.highlight ?? content.length <= LARGE_MARKDOWN_HIGHLIGHT_LIMIT;
  return (
    <div className={className}>
      <ReactMarkdown
        className="message-markdown"
        remarkPlugins={[remarkGfm]}
        rehypePlugins={enableHighlight ? [rehypeHighlight] : []}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node: _node, ...props }) => (
            <div className="message-markdown__table-wrap">
              <table {...props} />
            </div>
          ),
          input: ({ node: _node, ...props }) =>
            props.type === "checkbox" ? <input {...props} disabled readOnly className="message-markdown__checkbox" /> : <input {...props} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function renderStreamingTextContent(content: string | undefined, className: string) {
  if (!content) return null;
  return (
    <div className={`${className} message-streaming-plain`}>
      <pre>{content}</pre>
    </div>
  );
}

type StreamingStatusDescriptor = {
  mode: "queued" | "llm" | "tool" | "handoff";
  title?: string;
  subtitle?: string;
};

function summarizeStreamingDetail(detail: string | undefined) {
  if (!detail?.trim()) return undefined;
  return oneLinePreview(detail, "", 112) || undefined;
}

function summarizeStreamingStepOutput(step: MessageStreamStep) {
  const output = step.detailContent?.trim();
  if (!output) return undefined;
  return oneLinePreview(output, "", 180) || undefined;
}

function summarizeStreamingStepCurrentDetail(step: MessageStreamStep) {
  return summarizeStreamingStepOutput(step) || summarizeStreamingDetail(step.detail);
}

function buildStreamingStatusDescriptor(message: MessageItem): StreamingStatusDescriptor {
  const steps = message.streamSteps ?? [];
  const activeStep = [...steps].reverse().find((step) => step.state === "live") ?? steps[steps.length - 1];

  if (!activeStep) {
    return {
      mode: "queued",
      subtitle: message.statusDetail,
    };
  }

  if (/queued/i.test(activeStep.label)) {
    return {
      mode: "queued",
      subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
    };
  }

  switch (activeStep.kind) {
    case "llm_outbound":
      if (/^Waiting on model\b/i.test(activeStep.detail ?? "")) {
        return {
          mode: "llm",
          subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
        };
      }
      return {
        mode: "llm",
        subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
      };
    case "llm_inbound":
      return {
        mode: "llm",
        subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
      };
    case "tool_call":
      if (/^Planning\b/i.test(activeStep.detail ?? "")) {
        return {
          mode: "tool",
          subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
        };
      }
      return {
        mode: "tool",
        subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
      };
    case "tool_result_to_llm":
      return {
        mode: "handoff",
        subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
      };
    default:
      return {
        mode: "handoff",
        subtitle: message.statusDetail || summarizeStreamingStepCurrentDetail(activeStep),
      };
  }
}

function renderStreamingStatusSubtitle(subtitle: string | undefined) {
  if (!subtitle) return null;
  const lines = subtitle
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 5);
  if (lines.length === 0) return null;
  return (
    <small>
      {lines.map((line, index) => (
        <span key={`${line}-${index}`}>{line}</span>
      ))}
    </small>
  );
}

function renderStreamingStatusIcon(mode: StreamingStatusDescriptor["mode"]) {
  switch (mode) {
    case "llm":
      return (
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path
            d="m10 2.5 1.52 3.48L15 7.5l-3.48 1.52L10 12.5 8.48 9.02 5 7.5l3.48-1.52L10 2.5Z"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinejoin="round"
          />
          <path
            d="m4.5 11 1 2.25L7.75 14.25 5.5 15.25 4.5 17.5l-1-2.25L1.25 14.25 3.5 13.25 4.5 11Zm11-1.5.8 1.8 1.8.8-1.8.8-.8 1.8-.8-1.8-1.8-.8 1.8-.8.8-1.8Z"
            stroke="currentColor"
            strokeWidth="1.15"
            strokeLinejoin="round"
          />
        </svg>
      );
    case "tool":
      return (
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path
            d="M12.9 3.6a3.25 3.25 0 0 0 3.5 4.36l-6.76 6.76a1.8 1.8 0 1 1-2.54-2.55l6.75-6.75A3.25 3.25 0 0 0 12.9 3.6Z"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="m11.4 5.15 3.45 3.45"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinecap="round"
          />
        </svg>
      );
    case "handoff":
      return (
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path
            d="M4 10h9m0 0-3.1-3.1M13 10l-3.1 3.1M16 5v10"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
    default:
      return (
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <circle cx="10" cy="10" r="6.25" stroke="currentColor" strokeWidth="1.35" />
          <path
            d="M10 6.8v3.5l2.35 1.45"
            stroke="currentColor"
            strokeWidth="1.35"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      );
  }
}

function renderStreamingStatusContent(message: MessageItem, className: string) {
  const status = buildStreamingStatusDescriptor(message);
  if (!status.title && !status.subtitle) return null;
  return (
    <div className={`${className} message-streaming-status`} aria-live="polite">
      <span className={`message-streaming-status__icon message-streaming-status__icon--${status.mode}`}>
        {renderStreamingStatusIcon(status.mode)}
      </span>
      <span className="message-streaming-status__copy">
        {status.title ? <strong>{status.title}</strong> : null}
        {renderStreamingStatusSubtitle(status.subtitle)}
      </span>
    </div>
  );
}

function isConsultCard(card: ThreadCard): card is DecoratedChatCardItem {
  return card.kind === "consult_call";
}

function renderCardBody(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      return (
        <>
          {renderMarkdownContent(card.response, "message-body chat-card-preview")}
          {card.tool_calls && card.tool_calls.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.tool_calls.map((toolCall, index) => (
                <span key={`${toolCall.name || "tool"}-${index}`} className="chat-card-chip">
                  {toolCall.name || "tool"}
                  {toolCall.args_preview ? `(${toolCall.args_preview})` : ""}
                </span>
              ))}
            </div>
          ) : null}
          {renderMarkdownCollapse(
            "PROMPT",
            card.systemPromptPresentation?.label || "System prompt",
            card.systemPromptPresentation?.markdown,
            {
              copyLabel: "Copy full system prompt",
              copyText: card.systemPromptPresentation?.copyText || card.system_prompt,
            },
          )}
          {renderJsonCollapse("INPUT", "Full prompt payload", card.prompt_messages, {
            copyLabel: "Copy full prompt payload",
          })}
          {renderMarkdownCollapse("TRACE", "LLM timings", buildLlmTimingsMarkdown(card.timings))}
          {renderJsonCollapse("RAW", "Raw LLM response", card.raw_response, {
            copyLabel: "Copy raw LLM response",
          })}
          {renderLlmUsageFooter(summarizeLlmUsage([card]), "chat-usage-footer chat-usage-footer--card")}
        </>
      );
    case "tool_call":
      {
        const fileRequest = parseInteractiveFileToolRequest(card);
        if (fileRequest) {
          return <ToolFileReaderCard request={fileRequest} fallbackProjectId={null} />;
        }
      }
      return (
        <>
          {renderJsonCollapse("ARGS", `${card.tool || "tool"} input`, card.arguments, {
            copyLabel: `Copy ${card.tool || "tool"} input`,
          })}
          {renderJsonCollapse(isToolCardFailure(card) ? "ERROR" : "RESULT", `${card.tool || "tool"} output`, card.result, {
            pretty: false,
            open: isToolCardFailure(card),
            copyLabel: `Copy ${card.tool || "tool"} output`,
          })}
          {isInternalToolPause(card) ? null : card.blocked_kind === "approval" ? (
            <div className="chat-card-summary">
              Approval pending. Open Activity to approve or reject this blocked tool request.
            </div>
          ) : null}
        </>
      );
    case "consult_call":
      return (
        <>
          <div className="chat-card-chip-row">
            <span className="chat-card-chip chat-card-chip--accent">{card.status || "running"}</span>
            {card.target_agent ? <span className="chat-card-chip">@{card.target_agent}</span> : null}
          </div>
          {!card.summary && card.question_preview ? (
            <div className="chat-card-summary">
              {card.agent || "agent"} asked {card.target_agent || "another agent"}: {card.question_preview}
            </div>
          ) : null}
          {renderMarkdownContent(consultCardBody(card), "message-body chat-card-preview")}
          {card.consult_step_id ? (
            <div className="chat-card-chip-row">
              <span className="chat-card-chip chat-card-chip--accent">{card.consult_step_id}</span>
              {card.available_actions?.map((action) => (
                <span key={action} className="chat-card-chip">{action}</span>
              ))}
            </div>
          ) : null}
        </>
      );
    case "agent_error":
      return (
        <>
          {renderMarkdownContent(card.error || card.summary, "message-body chat-card-preview")}
          {renderMarkdownCollapse("DETAIL", `${card.agent || "agent"} failure detail`, card.content, {
            copyLabel: "Copy failure detail",
            copyText: card.content,
          })}
        </>
      );
    case "tool_merge":
      return (
        <details className="chat-json-collapse">
          <summary className="chat-json-summary">
            <span className="chat-json-summary__main">
              <span className="chat-json-badge">LIST</span>
              <span className="chat-json-label">Merged tool calls</span>
            </span>
          </summary>
          <div className="chat-tool-merge-list">
            {card.items.map((item, index) => (
              <div key={`${item.id}-${index}`} className="chat-tool-merge-item">
                <div className="chat-tool-merge-item__meta">
                  <span>#{index + 1}</span>
                  <span>{typeof item.duration_ms === "number" ? `${item.duration_ms}ms` : "n/a"}</span>
                  <span>{isToolCardFailure(item) ? "failed" : isInternalToolPause(item) ? "paused" : "ok"}</span>
                </div>
                {item.arguments ? (
                  <div className="chat-json-block">
                    <div className="chat-json-block__header">
                      <span>Arguments</span>
                      <CopyTextButton content={prettyJson(item.arguments)} title="Copy tool arguments" />
                    </div>
                    <pre className="chat-json-content">{prettyJson(item.arguments)}</pre>
                  </div>
                ) : null}
                {item.result ? (
                  <div className="chat-json-block">
                    <div className="chat-json-block__header">
                      <span>Result</span>
                      <CopyTextButton content={item.result} title="Copy tool result" />
                    </div>
                    <pre className="chat-json-content">{item.result}</pre>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </details>
      );
    case "stage_start":
      return (
        <>
          {card.active_skills && card.active_skills.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.active_skills.map((skill) => (
                <span key={skill} className="chat-card-chip chat-card-chip--accent">
                  {skill}
                </span>
              ))}
            </div>
          ) : null}
          {card.expected_artifacts && card.expected_artifacts.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.expected_artifacts.map((artifact) => (
                <span key={artifact} className="chat-card-chip">
                  {artifact}
                </span>
              ))}
            </div>
          ) : null}
        </>
      );
    case "stage_end":
      return renderMarkdownContent(card.summary, "message-body chat-card-preview");
    case "gate_blocked":
      return renderMarkdownContent("This stage is blocked until someone approves or rejects it.", "message-body chat-card-preview");
    case "gate_approved":
      return renderMarkdownContent("Pipeline can continue to the next stage.", "message-body chat-card-preview");
    case "gate_rejected":
      return renderMarkdownContent(`Rollback target: ${card.to_stage || "previous stage"}`, "message-body chat-card-preview");
    case "skill_inject":
      return (
        <>
          {card.skills && card.skills.length > 0 ? (
            <div className="chat-skill-list">
              {card.skills.map((skill, index) => (
                <div key={`${skill.name || "skill"}-${index}`} className="chat-skill-card">
                  <strong>{skill.name || "Unnamed skill"}</strong>
                  {renderMarkdownContent(skill.hint, "message-body chat-card-preview")}
                  {skill.guide ? (
                    renderJsonCollapse(
                      "GUIDE",
                      typeof skill.guide_tokens === "number" ? `~${skill.guide_tokens} words` : "Skill guide",
                      skill.guide,
                      {
                        pretty: false,
                        copyLabel: "Copy skill guide",
                      },
                    )
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          {renderJsonCollapse("ALL", "Agent skill set", card.agent_all_skills?.join("\n"), {
            pretty: false,
            copyLabel: "Copy agent skill set",
          })}
        </>
      );
    case "agent_message":
      return renderMarkdownContent(card.content, "message-body chat-card-preview");
    case "boss_instruction":
      return renderMarkdownContent(card.content_preview, "message-body chat-card-preview");
    default:
      return null;
  }
}

function renderCompactCardBody(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call": {
      const metaBits = [
        card.model,
        typeof card.turn === "number" ? `turn ${card.turn}` : "",
        typeof card.duration_ms === "number" ? `${card.duration_ms}ms` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      const outboundSections = [
        buildLlmTimingsMarkdown(card.timings),
        card.systemPromptPresentation?.markdown || "",
        card.prompt_messages ? markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }) : "",
        buildPlannedToolsMarkdown(card.tool_calls),
      ]
        .filter(Boolean)
        .join("\n\n");
      const inboundSections = [
        card.response ? markdownSection("Response", card.response, { asMarkdown: true }) : "",
        card.raw_response ? markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }) : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      const llmLayout = renderLlmDetailLayout(card.agent || "agent", {
        meta: metaBits ? `### Meta\n\n- ${metaBits}` : "",
        outbound: outboundSections,
        inbound: inboundSections,
      });
      if (llmLayout) return llmLayout;
      return (
        <div className="chat-progress-detail-stack">
          {renderProgressTextBlock("PROMPT", "Prompt sent to LLM", card.system_prompt)}
          {renderProgressJsonBlock("INPUT", "Full prompt payload", card.prompt_messages)}
          {card.tool_calls && card.tool_calls.length > 0 ? (
            <section className="chat-progress-detail-block">
              <div className="chat-progress-detail-block__header">
                <span className="chat-json-badge">TOOLS</span>
                <span className="chat-progress-detail-block__label">Tools requested by the model</span>
              </div>
              <div className="chat-progress-tool-list">
                {card.tool_calls.map((toolCall, index) => (
                  <div key={`${toolCall.name || "tool"}-${index}`} className="chat-progress-tool-list__item">
                    <div className="chat-progress-tool-list__title">{toolCall.name || "tool"}</div>
                    {toolCall.args_preview ? (
                      <pre className="chat-progress-detail-block__content chat-progress-detail-block__content--inline">
                        {toolCall.args_preview}
                      </pre>
                    ) : (
                      <div className="chat-progress-tool-list__empty">No arguments preview</div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {renderProgressTextBlock("OUTPUT", "LLM response", card.response)}
          {renderProgressJsonBlock("RAW", "Raw LLM response", card.raw_response)}
        </div>
      );
    }
    case "tool_call":
      {
        const fileRequest = parseInteractiveFileToolRequest(card);
        if (fileRequest) {
          return <ToolFileReaderCard request={fileRequest} fallbackProjectId={null} />;
        }
      }
      return (
        <div className="chat-progress-detail-stack">
          {renderProgressJsonBlock("ARGS", `${card.tool || "tool"} input`, card.arguments)}
          {renderProgressTextBlock(
            isToolCardFailure(card) ? "ERROR" : "RESULT",
            `${card.tool || "tool"} output`,
            card.result,
          )}
        </div>
      );
    case "agent_error":
      return (
        <div className="chat-progress-detail-stack">
          {renderProgressTextBlock("ERROR", `${card.agent || "agent"} failure`, card.error || card.summary || card.content)}
          {renderProgressTextBlock("DETAIL", "Failure detail", card.content)}
        </div>
      );
    case "tool_merge":
      return (
        <div className="chat-progress-detail-stack">
          {card.items.map((item, index) => (
            <section key={`${item.id}-${index}`} className="chat-progress-detail-block">
              <div className="chat-progress-detail-block__header">
                <span className="chat-json-badge">CALL</span>
                <span className="chat-progress-detail-block__label">
                  #{index + 1} · {item.tool || card.tool || "tool"}
                  {typeof item.duration_ms === "number" ? ` · ${item.duration_ms}ms` : ""}
                  {isToolCardFailure(item) ? " · failed" : isInternalToolPause(item) ? " · paused" : ""}
                </span>
              </div>
              <div className="chat-progress-detail-stack chat-progress-detail-stack--nested">
                {renderProgressJsonBlock("ARGS", "Tool input", item.arguments)}
                {renderProgressTextBlock(
                  isToolCardFailure(item) ? "ERROR" : "RESULT",
                  "Tool output",
                  item.result,
                )}
              </div>
            </section>
          ))}
        </div>
      );
    case "stage_start":
    case "stage_end":
    case "gate_blocked":
    case "gate_approved":
    case "gate_rejected":
    case "skill_inject":
    case "agent_message":
    case "boss_instruction":
      return renderCardBody(card);
    default:
      return renderCardBody(card);
  }
}

function renderCardSurface(
  card: ThreadCard,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
  onInspectTaskRun?: (taskRunId: number) => void,
  onWaitSubagent?: (taskRunId: number, stepId: string) => void,
  onCancelSubagent?: (taskRunId: number, stepId: string) => void,
  onCloseSubagent?: (taskRunId: number, stepId: string) => void,
  activeActionKey?: string | null,
) {
  const gatePipelineId =
    "pipeline_id" in card && typeof card.pipeline_id === "number" ? card.pipeline_id : null;
  const isBlockingGate = card.kind === "gate_blocked" && gatePipelineId !== null;
  const gateBusy = gatePipelineId !== null && gateActionPipelineId === gatePipelineId;
  const isConsult = isConsultCard(card);
  const consultRunId = typeof card.run_id === "number" ? card.run_id : null;
  const consultStepId = isConsult && typeof card.consult_step_id === "string" ? card.consult_step_id : null;
  const consultActionKeyBase = `${consultRunId ?? "na"}:${consultStepId ?? card.id}`;

  return (
    <article className="chat-tool-card">
      <div className="chat-tool-card__header">
        <div>
          <div className="chat-tool-card__title">
            <span className="chat-json-badge">{cardBadge(card)}</span>
            <span>{cardTitle(card)}</span>
          </div>
          <div className="chat-tool-card__detail">
            {card.source || "chatroom"}
            {"model" in card && card.model ? ` · ${card.model}` : ""}
            {"turn" in card && card.turn ? ` · turn ${card.turn}` : ""}
            {"duration_ms" in card && typeof card.duration_ms === "number" ? ` · ${card.duration_ms}ms` : ""}
            {"success" in card && typeof card.success === "boolean" ? ` · ${card.success ? "success" : "failed"}` : ""}
          </div>
        </div>
        <div className="chat-tool-card__detail">{formatTime(card.created_at)}</div>
      </div>

      {cardSummary(card) ? <div className="chat-card-summary">{cardSummary(card)}</div> : null}
      {renderCardBody(card)}
      {isBlockingGate ? (
        <div className="chat-card-actions">
          <button
            type="button"
            className="chat-card-action-btn chat-card-action-btn--approve"
            disabled={gateBusy}
            onClick={() => void onApproveGate(gatePipelineId)}
          >
            {gateBusy ? "Working..." : "Approve"}
          </button>
          <button
            type="button"
            className="chat-card-action-btn chat-card-action-btn--reject"
            disabled={gateBusy}
            onClick={() => void onRejectGate(gatePipelineId)}
          >
            Reject
          </button>
        </div>
      ) : null}
      {isConsult && consultRunId && consultStepId ? (
        <div className="chat-card-actions">
          <button
            type="button"
            className="chat-card-action-btn"
            onClick={() => onInspectTaskRun?.(consultRunId)}
          >
            Inspect
          </button>
          {card.available_actions?.includes("wait") ? (
            <button
              type="button"
              className="chat-card-action-btn"
              disabled={activeActionKey === `${consultActionKeyBase}:wait`}
              onClick={() => onWaitSubagent?.(consultRunId, consultStepId)}
            >
              {activeActionKey === `${consultActionKeyBase}:wait` ? "Waiting..." : "Wait"}
            </button>
          ) : null}
          {card.available_actions?.includes("cancel") ? (
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--reject"
              disabled={activeActionKey === `${consultActionKeyBase}:cancel`}
              onClick={() => onCancelSubagent?.(consultRunId, consultStepId)}
            >
              {activeActionKey === `${consultActionKeyBase}:cancel` ? "Cancelling..." : "Cancel"}
            </button>
          ) : null}
          {card.available_actions?.includes("close") ? (
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--approve"
              disabled={activeActionKey === `${consultActionKeyBase}:close`}
              onClick={() => onCloseSubagent?.(consultRunId, consultStepId)}
            >
              {activeActionKey === `${consultActionKeyBase}:close` ? "Closing..." : "Close"}
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function compactCardMeta(card: ThreadCard) {
  const bits: string[] = [];
  if ("model" in card && card.model) bits.push(card.model);
  if (card.kind !== "tool_merge" && "tool" in card && card.tool) bits.push(card.tool);
  if ("duration_ms" in card && typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
  if ("turn" in card && card.turn) bits.push(`turn ${card.turn}`);
  if ("success" in card && typeof card.success === "boolean") {
    bits.push(card.success ? "ok" : "failed");
  }
  return bits.join(" · ");
}

function compactCardDefaultOpen(card: ThreadCard, isLive: boolean) {
  return false;
}

function compactCardState(card: ThreadCard, isLive: boolean) {
  if (isLive) return "live";
  if ("status" in card && card.status === "running") return "live";
  if (isInternalToolPause(card)) return "live";
  if (card.kind === "gate_blocked") return "blocked";
  if (card.kind === "agent_error") return "error";
  if (isToolCardFailure(card)) return "error";
  return "done";
}

function compactCardStateLabel(card: ThreadCard, isLive: boolean) {
  const state = compactCardState(card, isLive);
  switch (state) {
    case "live":
      return "running";
    case "blocked":
      return "blocked";
    case "error":
      return "failed";
    default:
      return "done";
  }
}

function compactCardSummary(card: ThreadCard) {
  switch (card.kind) {
    case "tool_call":
      if (card.status === "running") {
        return oneLinePreview(card.result, `${card.tool || "Tool"} is running.`);
      }
      return oneLinePreview(card.result, `${card.tool || "Tool"} finished.`);
    case "agent_error":
      return oneLinePreview(card.error || card.summary || card.content, "Agent flow failed.");
    case "tool_merge":
      return `${card.count} calls · ${card.tool || "tool"} · latest ${oneLinePreview(card.items[card.items.length - 1]?.result, "completed")}`;
    case "llm_call":
      if (card.finish_reason === "tool_calls") {
        return oneLinePreview(
          card.tool_calls?.map((tool) => tool.name || "tool").join(", "),
          "Model requested more tool calls.",
        );
      }
      if (card.finish_reason === "length") {
        return "Model returned a partial answer.";
      }
      return oneLinePreview(card.response, "Model responded.");
    case "stage_start":
      return oneLinePreview(card.summary || card.content, card.display_name || card.stage || "Stage started");
    case "stage_end":
      return oneLinePreview(card.summary, card.stage || "Stage completed");
    case "skill_inject":
      return oneLinePreview(
        card.skills?.map((skill) => skill.name).filter(Boolean).join(", "),
        "Skills injected.",
      );
    case "agent_message":
      return oneLinePreview(card.content, "Agent handoff");
    case "boss_instruction":
      return oneLinePreview(card.content_preview, "Instruction recorded");
    default:
      return oneLinePreview(cardSummary(card), "Runtime event");
  }
}

function compactCardCurrentDetail(card: ThreadCard, isCurrent: boolean) {
  const summary = compactCardSummary(card);
  if (!isCurrent) return summary;

  switch (card.kind) {
    case "tool_call":
      return oneLinePreview(card.result, summary || `${card.tool || "Tool"} output`);
    case "tool_merge":
      return oneLinePreview(
        card.items[card.items.length - 1]?.result,
        summary || `${card.tool || "tool"} output`,
        180,
      );
    case "llm_call":
      return oneLinePreview(card.response, summary || "Model output", 180);
    case "agent_error":
      return oneLinePreview(card.error || card.summary || card.content, summary || "Agent flow failed.", 180);
    case "stage_start":
      return oneLinePreview(card.content || card.summary, summary || card.display_name || card.stage || "Stage started", 180);
    case "stage_end":
      return oneLinePreview(card.summary, summary || card.stage || "Stage completed", 180);
    case "agent_message":
      return oneLinePreview(card.content, summary || "Agent handoff", 180);
    case "boss_instruction":
      return oneLinePreview(card.content_preview, summary || "Instruction recorded", 180);
    default:
      return summary;
  }
}

function renderCompactCard(
  card: ThreadCard,
  groupKey: string,
  itemIndex: number,
  isLive: boolean,
  isCurrent: boolean,
  isExpanded: boolean,
  onToggle: (groupKey: string, cardId: string, isExpanded: boolean) => void,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const detail = compactCardCurrentDetail(card, isCurrent);
  const meta = compactCardMeta(card);
  const state = compactCardState(card, isLive);
  const gatePipelineId =
    "pipeline_id" in card && typeof card.pipeline_id === "number" ? card.pipeline_id : null;
  const isBlockingGate = card.kind === "gate_blocked" && gatePipelineId !== null;
  const gateBusy = gatePipelineId !== null && gateActionPipelineId === gatePipelineId;

  return (
    <details
      key={card.id}
      className={`chat-progress-item chat-progress-item--${state} ${isCurrent ? "is-current" : ""}`}
      open={isExpanded}
    >
      <summary
        className="chat-progress-item__summary"
        onClick={(event) => {
          event.preventDefault();
          onToggle(groupKey, card.id, isExpanded);
        }}
      >
        <span className={`chat-progress-item__state chat-progress-item__state--${state}`} aria-hidden="true">
          {state === "done" ? "✓" : state === "error" ? "!" : state === "blocked" ? "!" : ""}
        </span>
        <span className="chat-progress-item__index">{itemIndex + 1}</span>
        <span className="chat-progress-item__copy">
          <span className="chat-progress-item__heading">
            <span className="chat-json-badge">{cardBadge(card)}</span>
            <strong>{cardTitle(card)}</strong>
          </span>
          <small>{detail}</small>
        </span>
        <span className="chat-progress-item__meta">
          {meta ? <span>{meta}</span> : null}
          <span>{formatTime(card.created_at)}</span>
          <span className={`chat-progress-item__label chat-progress-item__label--${state}`}>
            {compactCardStateLabel(card, isLive)}
          </span>
        </span>
        <span className="chat-progress-item__toggle" aria-hidden="true">
          &gt;
        </span>
      </summary>

      <div className="chat-progress-item__body">
        {renderCompactCardBody(card)}
        {isBlockingGate ? (
          <div className="chat-card-actions">
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--approve"
              disabled={gateBusy}
              onClick={() => void onApproveGate(gatePipelineId)}
            >
              {gateBusy ? "Working..." : "Approve"}
            </button>
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--reject"
              disabled={gateBusy}
              onClick={() => void onRejectGate(gatePipelineId)}
            >
              Reject
            </button>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function renderCard(
  card: ThreadCard,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
  onInspectTaskRun?: (taskRunId: number) => void,
  onWaitSubagent?: (taskRunId: number, stepId: string) => void,
  onCancelSubagent?: (taskRunId: number, stepId: string) => void,
  onCloseSubagent?: (taskRunId: number, stepId: string) => void,
  activeActionKey?: string | null,
) {
  const sender = cardActorName(card);
  const avatarThemeStyle = buildAgentThemeStyle(sender, agents);

  return (
    <div key={card.id} className="chat-card-row">
      <div className="chat-avatar assistant chat-avatar--agent" style={avatarThemeStyle}>
        {renderAgentAvatarContent(sender, agents)}
      </div>
      <div className="chat-group-messages chat-card-stack">
        {renderCardSurface(
          card,
          gateActionPipelineId,
          onApproveGate,
          onRejectGate,
          onInspectTaskRun,
          onWaitSubagent,
          onCancelSubagent,
          onCloseSubagent,
          activeActionKey,
        )}
      </div>
    </div>
  );
}

function renderActivityBatch(
  batchId: string,
  cards: ThreadCard[],
  agents: AgentInfo[],
  isCurrentBatch: boolean,
  currentActivityAgentName: string | null,
  expandedProgressCards: Record<string, StepExpansionValue>,
  autoExpandCurrentStep: boolean,
  onToggleProgressCard: (groupKey: string, cardId: string, isExpanded: boolean) => void,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const groups = new Map<
    string,
    {
      name: string;
      cards: ThreadCard[];
      latestAt: string;
    }
  >();

  for (const card of cards) {
    const actor = cardActorName(card);
    const existing = groups.get(actor);
    if (existing) {
      existing.cards.push(card);
      existing.latestAt = card.created_at;
      continue;
    }
    groups.set(actor, {
      name: actor,
      cards: [card],
      latestAt: card.created_at,
    });
  }

  const orderedGroups = Array.from(groups.values());
  const fallbackActiveName =
    currentActivityAgentName ||
    [...cards]
      .reverse()
      .map((card) => cardActorName(card))
      .find((name) => name !== "system") ||
    orderedGroups[orderedGroups.length - 1]?.name ||
    "system";
  const avatarThemeStyle = buildAgentThemeStyle(fallbackActiveName, agents);

  return (
    <div key={batchId} className="chat-card-row">
      <div className="chat-avatar assistant chat-avatar--agent" style={avatarThemeStyle}>
        {renderAgentAvatarContent(fallbackActiveName, agents)}
      </div>
      <div className="chat-group-messages chat-card-stack">
        <section className="chat-activity-batch">
          <div className="chat-activity-batch__header">
            <div>
              <div className="chat-tool-card__title">
                <span className="chat-json-badge">FLOW</span>
                <span>Agent activity</span>
              </div>
              <div className="chat-tool-card__detail">
                {orderedGroups.length} agent{orderedGroups.length === 1 ? "" : "s"} · {cards.length} action
                {cards.length === 1 ? "" : "s"}
              </div>
            </div>
            <div className="chat-tool-card__detail">
              {isCurrentBatch ? "live" : formatTime(cards[cards.length - 1]?.created_at || new Date().toISOString())}
            </div>
          </div>

          <div className="chat-activity-batch__groups">
            {orderedGroups.map((group) => {
              const isActiveGroup = group.name === fallbackActiveName;
              const orderedCards = group.cards;
              const groupKey = `${batchId}:${group.name}`;
              const currentCardId =
                isActiveGroup && isCurrentBatch
                  ? orderedCards[orderedCards.length - 1]?.id ?? null
                  : null;
              const expandedCardId = Object.prototype.hasOwnProperty.call(expandedProgressCards, groupKey)
                ? expandedProgressCards[groupKey]
                : autoExpandCurrentStep
                  ? currentCardId
                  : null;
              return (
                <section
                  key={`${batchId}-${group.name}`}
                  className={`chat-agent-activity ${isActiveGroup ? "is-active" : ""}`}
                  style={buildAgentThemeStyle(group.name, agents)}
                >
                  <div className="chat-agent-activity__summary">
                    <span className={`chat-agent-activity__status ${isActiveGroup ? "is-live" : ""}`} />
                    <span className="chat-agent-activity__avatar">{initials(group.name)}</span>
                    <span className="chat-agent-activity__copy">
                      <strong>{group.name}</strong>
                      <small>
                        {group.cards.length} step{group.cards.length === 1 ? "" : "s"} · {formatTime(group.latestAt)}
                      </small>
                    </span>
                    <span className={`chat-agent-activity__pill ${isActiveGroup ? "is-live" : ""}`}>
                      {isActiveGroup ? "active" : "summary"}
                    </span>
                  </div>

                  <div className="chat-agent-activity__body">
                    {orderedCards.map((card, index) =>
                      renderCompactCard(
                        card,
                        groupKey,
                        index,
                        card.id === currentCardId,
                        card.id === currentCardId,
                        expandedCardId === card.id,
                        onToggleProgressCard,
                        gateActionPipelineId,
                        onApproveGate,
                        onRejectGate,
                      ),
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

function renderMessage(
  message: MessageItem,
  agents: AgentInfo[],
  copiedMessageId: number | null,
  onCopyMessage: (message: MessageItem) => Promise<void>,
  selectionMode: boolean,
  selected: boolean,
  onToggleSelected: (message: MessageItem) => void,
  selectedMessageCount: number,
  copiedSelection: boolean,
  onCancelSelection: () => void,
  onCopySelectedMessages: () => Promise<void>,
  expandedStepId: StepExpansionValue | undefined,
  onToggleStep: (messageId: number, stepId: string, isExpanded: boolean) => void,
  messageTimeline: ChatTimelineProjection | null = null,
  fallbackCards: ThreadCard[] = [],
  onAnalyzeFailureStep?: FailureStepAnalysisHandler,
) {
  const isAssistant = Boolean(message.agent_name);
  const sender = message.agent_name || "You";
  const messageThemeStyle = isAssistant ? buildAgentThemeStyle(message.agent_name, agents) : undefined;
  const timelineTaskRunId =
    messageTimeline && typeof messageTimeline.task_run_id === "number"
      ? messageTimeline.task_run_id
      : message.runtime_summary?.task_run_id ?? 0;
  const timelineSteps = messageTimeline?.steps.length
    ? [...messageTimeline.steps]
      .sort((left, right) => Number(left.sequence || 0) - Number(right.sequence || 0))
      .map((step) => timelineStepToStreamStep(step, timelineTaskRunId))
    : [];
  const streamSteps =
    message.streamSteps && message.streamSteps.length > 0
      ? message.streamSteps.map((step) => ({ ...step }))
      : timelineSteps.length > 0
        ? timelineSteps
      : fallbackCards.flatMap((card, index) => buildMessageStepsFromCard(card, message.id, index));
  const hasStreamSteps = streamSteps.length > 0;
  const currentStreamStepId =
    [...streamSteps].reverse().find((step) => step.state === "live")?.id ?? streamSteps[streamSteps.length - 1]?.id ?? null;
  const historyId = streamTraceHistoryId(`message:${message.id}`);
  const resolvedExpandedStepId = resolveTraceExpandedStepId(streamSteps, expandedStepId, currentStreamStepId, historyId);
  const { historySteps, recentSteps } = splitTraceSteps(streamSteps);
  const renderMessageTraceStep = (step: MessageStreamStep) => {
    const detail =
      step.id === currentStreamStepId
        ? summarizeStreamingStepCurrentDetail(step)
        : summarizeStreamingDetail(step.detail);
    return renderTraceStep({
      step,
      isExpanded: resolvedExpandedStepId === step.id,
      onToggle: (stepId, isExpanded) => onToggleStep(message.id, stepId, isExpanded),
      detailSummary: detail ? <small>{detail}</small> : null,
      analysisContext: { message },
      onAnalyzeFailureStep,
      mode: "message",
    });
  };
  const showReplyAfterTrace = isAssistant && hasStreamSteps;
  const messageBodyContent = message.content;
  const messageBodyClassName = `message-body ${message.isStreaming ? "message-body--streaming" : ""} ${
    showReplyAfterTrace ? "message-body--after-trace" : ""
  }`;
  const messageBody = message.isStreaming
    ? messageBodyContent
      ? renderStreamingTextContent(messageBodyContent, messageBodyClassName)
      : renderStreamingStatusContent(message, messageBodyClassName)
    : renderMarkdownContent(messageBodyContent, messageBodyClassName);
  // Choice Box: interactive decision component embedded in chat
  const choiceBoxData = message.metadata?.choice_box as ChoiceBoxData | undefined;
  const choiceBoxElement = choiceBoxData && choiceBoxData.status === "pending" ? (
    <div className="message-choice-box" style={{ marginTop: 8 }}>
      <ChoiceBox
        data={choiceBoxData}
        onRespond={async (boxId: string, value: string) => {
          try {
            const { api } = await import("../api/client");
            await api.respondChoiceBox(boxId, value);
          } catch (err) {
            console.error("[ChoiceBox] respond failed:", err);
          }
        }}
      />
    </div>
  ) : null;
  const messageTrace = hasStreamSteps ? (
    <div className={`message-stream-trace ${showReplyAfterTrace ? "message-stream-trace--top" : ""}`}>
      {renderTraceHistoryCard({
        historyId,
        steps: historySteps,
        expandedStepId: resolvedExpandedStepId,
        onToggleStep: (stepId, isExpanded) => onToggleStep(message.id, stepId, isExpanded),
        analysisContext: { message },
        onAnalyzeFailureStep,
        mode: "message",
      })}
      {recentSteps.map(renderMessageTraceStep)}
    </div>
  ) : null;
  const messageUsageFooter =
    isAssistant && fallbackCards.length > 0
      ? renderLlmUsageFooter(summarizeLlmUsage(fallbackCards), "chat-usage-footer chat-usage-footer--message")
      : null;
  const avatarThemeStyle = isAssistant ? buildAgentThemeStyle(sender, agents) : undefined;

  return (
    <div
      className={`chat-group ${isAssistant ? "chat-group--agent" : "user"} ${selectionMode ? "is-selecting" : ""} ${selected ? "is-selected" : ""}`}
      style={messageThemeStyle}
    >
      {selectionMode ? (
        <button
          type="button"
          className="chat-message-select-btn"
          onClick={() => onToggleSelected(message)}
          aria-pressed={selected}
          aria-label={selected ? "Deselect message" : "Select message"}
          title={selected ? "Deselect message" : "Select message"}
        >
          {selected ? <CheckSquare size={16} /> : <Square size={16} />}
        </button>
      ) : null}
      <div
        className={`chat-avatar ${isAssistant ? "assistant chat-avatar--agent" : "user"}`}
        style={avatarThemeStyle}
      >
        {isAssistant ? renderAgentAvatarContent(sender, agents) : initials(sender)}
      </div>

      <div className="chat-group-messages">
        <div className={`chat-bubble ${message.isStreaming ? "chat-bubble--streaming" : ""}`}>
          {showReplyAfterTrace ? (
            <>
              {messageTrace}
              {messageBody}
              {choiceBoxElement}
              {messageUsageFooter}
            </>
          ) : (
            <>
              {messageBody}
              {choiceBoxElement}
              {messageTrace}
              {messageUsageFooter}
            </>
          )}
        </div>

        <div className="chat-group-footer">
          <span className={`chat-sender-name ${isAssistant ? "chat-sender-name--agent" : ""}`}>{sender}</span>
          <span className="chat-message-id" title={`Message ID: ${message.id}`}>#{message.id}</span>
          <span className="chat-group-timestamp">{formatTime(message.created_at)}</span>
          {message.isStreaming ? <span className="soft-pill">streaming</span> : null}
          {!message.localOnly ? (
            <>
              <button type="button" className="chat-footer-btn" onClick={() => void onCopyMessage(message)}>
                {copiedMessageId === message.id ? "Copied" : "Copy"}
              </button>
              <button
                type="button"
                className={`chat-footer-btn ${selectionMode ? "is-active" : ""}`}
                onClick={selectionMode ? () => onToggleSelected(message) : () => onToggleSelected(message)}
                aria-pressed={selectionMode ? selected : false}
                title={selectionMode ? (selected ? "Deselect message" : "Select message") : "Select messages"}
              >
                {selectionMode ? (selected ? "Selected" : "Select") : "Select"}
              </button>
              {selectionMode ? (
                <>
                  <span className="chat-footer-selection-count">{selectedMessageCount} selected</span>
                  <button
                    type="button"
                    className="chat-footer-btn chat-footer-btn--primary"
                    onClick={() => void onCopySelectedMessages()}
                    disabled={selectedMessageCount === 0}
                  >
                    {copiedSelection ? "Copied" : "Copy selected"}
                  </button>
                  <button type="button" className="chat-footer-btn" onClick={onCancelSelection}>
                    Cancel
                  </button>
                </>
              ) : null}
            </>
          ) : (
            <span className="soft-pill">local</span>
          )}
        </div>
        {renderMessageArtifactSummary(message)}
        {renderMessageRuntimeSummary(message)}
      </div>
    </div>
  );
}

function renderTaskRunInlineCard(
  taskRun: TaskRunSummary,
  detail: TaskRunDetail | null,
  activity: TaskActivityProjection | null,
  timeline: ChatTimelineProjection | null,
  cards: ThreadCard[],
  agents: AgentInfo[],
  approvalItems: ApprovalQueueItem[],
  approvalQueueLoaded: boolean,
  approvalActionItemId: number | null,
  expandedStepId: StepExpansionValue | undefined,
  onToggleStep: (taskRunId: number, stepId: string, isExpanded: boolean) => void,
  onResolveApprovalQueueItem: (
    item: ApprovalQueueItem,
    action: "approve" | "reject",
    remember?: boolean,
    rememberScope?: string,
  ) => Promise<void>,
  onAnalyzeFailureStep?: FailureStepAnalysisHandler,
) {
  const pendingItems = approvalItems.filter((item) => (item.status || "").toLowerCase() === "pending");
  const pendingApprovalOverride = approvalQueueLoaded ? pendingItems.length : undefined;
  const hasPendingApprovals =
    pendingItems.length > 0 || (!approvalQueueLoaded && Number(taskRun.pending_approval_count || 0) > 0);
  const normalizedTaskRunStatus = (taskRun.status || "").toLowerCase();
  const shouldUseLiveActivity = normalizedTaskRunStatus === "running";
  const shellStatus = shouldUseLiveActivity ? summarizeTaskRunShellStatus(cards) : null;
  const liveActivity = shouldUseLiveActivity ? shellStatus ?? summarizeTaskRunRuntimeCards(cards) : null;
  const actorName = resolveTaskRunActorName(taskRun, agents);
  const taskThemeStyle = buildAgentThemeStyle(taskRun.target_agent_name || actorName, agents);
  const summary = liveActivity?.detail || buildTaskRunCardSummary(taskRun, detail, activity, pendingApprovalOverride, actorName);
  const inlineStatus = liveActivity
    ? {
        tone:
          liveActivity.state === "error"
            ? ("error" as const)
            : liveActivity.state === "blocked"
              ? ("warning" as const)
              : ("info" as const),
        label: liveActivity.actor ? `${liveActivity.actor} · ${liveActivity.title}` : liveActivity.title,
        detail: liveActivity.detail,
      }
    : summarizeTaskRunInlineStatus(taskRun, detail, activity, pendingApprovalOverride, actorName);
  const trace = renderTaskRunTrace(taskRun, detail, activity, timeline, expandedStepId, onToggleStep, onAnalyzeFailureStep);
  const shellOutput = renderTaskRunShellOutput(taskRun, cards);
  const taskIdLabel = (taskRun.client_turn_id || "").trim().toLowerCase().startsWith("delegate-")
    ? (taskRun.client_turn_id || "").trim().slice("delegate-".length)
    : null;
  const shouldHideSummary = Boolean(liveActivity?.detail && summary === liveActivity.detail);
  const shouldShowInlineStatus = !(shellOutput && shellStatus);
  const avatarThemeStyle = buildAgentThemeStyle(taskRun.target_agent_name || actorName, agents);

  return (
    <div className="chat-card-row" key={`task-run-inline-${taskRun.id}`}>
      <div className="chat-avatar assistant chat-avatar--agent" style={avatarThemeStyle}>
        {renderAgentAvatarContent(taskRun.target_agent_name || actorName, agents)}
      </div>
      <div className="chat-group-messages chat-card-stack">
        <article
          className={`chat-tool-card chat-tool-card--task-run ${pendingItems.length > 0 ? "has-pending-approval" : ""}`}
          style={taskThemeStyle}
        >
          <div className="chat-tool-card__header">
            <div>
              <div className="chat-tool-card__title">
                <span className="chat-json-badge">TASK</span>
                <span className="chat-tool-card__agent-name">{taskIdLabel ? `TASK ${taskIdLabel}` : actorName}</span>
                <span className="soft-pill">run #{taskRun.id}</span>
              </div>
              <div className="chat-tool-card__detail">
                {taskIdLabel ? `${actorName} · ${taskRun.title}` : taskRun.title}
              </div>
            </div>
            <div className="chat-tool-card__detail">{taskRun.updated_at ? formatTime(taskRun.updated_at) : "--"}</div>
          </div>

          {renderTaskActivityBackground(activity)}

          {trace ? trace : (taskRun.status || "").toLowerCase() === "running" && !shellOutput ? (
            <div className="task-run-inline-approvals">
              <div className="task-run-inline-approval">
                <div className="task-run-detail__summary">Loading live activity...</div>
              </div>
            </div>
          ) : null}

          {shouldShowInlineStatus ? (
            <div className={`task-run-inline-status task-run-inline-status--${inlineStatus.tone}`}>
              <strong>{inlineStatus.label}</strong>
              <span>{inlineStatus.detail}</span>
            </div>
          ) : null}

          {shellOutput}

          {!shouldHideSummary ? <div className="chat-card-summary">{summary}</div> : null}

          <div className="task-run-card__footer">
            <span>{taskRun.event_count} events</span>
            {pendingItems.length > 0 ? (
              <span className="task-run-card__approval-pill">
                {pendingItems.length} pending approval{pendingItems.length === 1 ? "" : "s"}
              </span>
            ) : hasPendingApprovals && !approvalQueueLoaded ? (
              <span className="task-run-card__approval-pill">loading approvals...</span>
            ) : null}
          </div>

          {pendingItems.length > 0 ? (
            <div className="task-run-inline-approvals">
              {pendingItems.map((item) => {
                const isBusy = approvalActionItemId === item.id;
                const labels = approvalQueueActionLabels(item);
                return (
                  <div key={item.id} className="task-run-inline-approval">
                    <div className="task-run-inline-approval__header">
                      <span className="task-run-approval-id">{approvalRequestLabel(item)}</span>
                      <span className="task-run-detail__summary">
                        {item.summary || item.title || item.target_name || "Approval request"}
                      </span>
                    </div>
                    <div className="task-run-approval-card__actions">
                      <button
                        type="button"
                        className="chat-card-action-btn chat-card-action-btn--approve"
                        disabled={isBusy}
                        onClick={() => void onResolveApprovalQueueItem(item, "approve")}
                      >
                        {isBusy ? labels.busy : labels.approve}
                      </button>
                      {!isTimeoutWaitQueueItem(item) ? (
                        <button
                          type="button"
                          className="chat-card-action-btn"
                          disabled={isBusy}
                          onClick={() => void onResolveApprovalQueueItem(item, "approve", true)}
                        >
                          Approve + Remember
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="chat-card-action-btn chat-card-action-btn--reject"
                        disabled={isBusy}
                        onClick={() => void onResolveApprovalQueueItem(item, "reject")}
                      >
                        {labels.reject}
                      </button>
                      {!isTimeoutWaitQueueItem(item) ? (
                        <button
                          type="button"
                          className="chat-card-action-btn"
                          disabled={isBusy}
                          onClick={() => void onResolveApprovalQueueItem(item, "reject", true)}
                        >
                          Reject + Remember
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : hasPendingApprovals && !approvalQueueLoaded ? (
            <div className="task-run-inline-approvals">
              <div className="task-run-inline-approval">
                <div className="task-run-detail__summary">
                  Preparing inline approval controls...
                </div>
              </div>
            </div>
          ) : null}
        </article>
      </div>
    </div>
  );
}

function renderProjectFileTreeNode(
  node: BrowserFileTreeNode,
  depth: number,
  expandedPaths: Record<string, boolean>,
  onToggle: (path: string) => void,
  onOpenFile: (node: BrowserFileTreeNode) => void,
) {
  const isDirectory = node.kind === "directory";
  const isExpanded = isDirectory && expandedPaths[node.path] === true;
  const hasChildren = node.children.length > 0;

  return (
    <div key={node.id} className="file-tree__item">
      <button
        type="button"
        className={`file-tree__row ${isDirectory ? "is-directory" : "is-file"}`}
        style={{ "--file-tree-depth": depth } as CSSProperties}
        onClick={isDirectory ? () => onToggle(node.path) : () => onOpenFile(node)}
        title={node.path}
      >
        <span className="file-tree__twisty" aria-hidden="true">
          {isDirectory && hasChildren ? (
            isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
          ) : null}
        </span>
        <span className="file-tree__icon" aria-hidden="true">
          <FileTreeIcon node={node} />
        </span>
        <span className="file-tree__name">{node.name}</span>
        {!isDirectory && (node.source || node.timestamp) ? (
          <span className="file-tree__meta">
            {node.source ? <span>{node.source}</span> : null}
            {node.timestamp ? <span>{formatTime(node.timestamp)}</span> : null}
          </span>
        ) : null}
      </button>
      {isDirectory && isExpanded && hasChildren ? (
        <div className="file-tree__children">
          {node.children.map((child) => renderProjectFileTreeNode(child, depth + 1, expandedPaths, onToggle, onOpenFile))}
        </div>
      ) : null}
    </div>
  );
}

type MessageRowProps = {
  message: MessageItem;
  agents: AgentInfo[];
  copiedMessageId: number | null;
  onCopyMessage: (message: MessageItem) => void | Promise<void>;
  selectionMode: boolean;
  selected: boolean;
  onToggleSelected: (message: MessageItem) => void;
  selectedMessageCount: number;
  copiedSelection: boolean;
  onCancelSelection: () => void;
  onCopySelectedMessages: () => Promise<void>;
  expandedStepId: StepExpansionValue | undefined;
  onToggleStep: (messageId: number, stepId: string, isExpanded: boolean) => void;
  messageTimeline: ChatTimelineProjection | null;
  fallbackStepCards: ThreadCard[];
  onAnalyzeFailureStep?: FailureStepAnalysisHandler;
};

const MessageRow = memo(
  function MessageRow({
    message,
    agents,
    copiedMessageId,
    onCopyMessage,
    selectionMode,
    selected,
    onToggleSelected,
    selectedMessageCount,
    copiedSelection,
    onCancelSelection,
    onCopySelectedMessages,
    expandedStepId,
    onToggleStep,
    messageTimeline,
    fallbackStepCards,
    onAnalyzeFailureStep,
  }: MessageRowProps) {
    return renderMessage(
      message,
      agents,
      copiedMessageId,
      onCopyMessage,
      selectionMode,
      selected,
      onToggleSelected,
      selectedMessageCount,
      copiedSelection,
      onCancelSelection,
      onCopySelectedMessages,
      expandedStepId,
      onToggleStep,
      messageTimeline,
      fallbackStepCards,
      onAnalyzeFailureStep,
    );
  },
  (prev, next) => {
    const prevIsCopied = prev.copiedMessageId === prev.message.id;
    const nextIsCopied = next.copiedMessageId === next.message.id;
    return (
      prev.message === next.message &&
      prev.agents === next.agents &&
      prev.selectionMode === next.selectionMode &&
      prev.selected === next.selected &&
      prev.selectedMessageCount === next.selectedMessageCount &&
      prev.copiedSelection === next.copiedSelection &&
      prev.expandedStepId === next.expandedStepId &&
      prev.messageTimeline === next.messageTimeline &&
      prev.fallbackStepCards === next.fallbackStepCards &&
      prev.onToggleSelected === next.onToggleSelected &&
      prev.onCancelSelection === next.onCancelSelection &&
      prev.onCopySelectedMessages === next.onCopySelectedMessages &&
      prev.onAnalyzeFailureStep === next.onAnalyzeFailureStep &&
      prevIsCopied === nextIsCopied
    );
  },
);

export function ChatTab({
  chat,
  project,
  agents,
  messages,
  optimisticMessages,
  cards,
  taskRuns,
  processes,
  projectBrowserIndex,
  projectBrowserAutoRefreshEnabled = false,
  projectBrowserContentRefresh = null,
  liveTaskRunDetailsById,
  taskActivitiesById,
  taskTimelinesById,
  loading,
  sending,
  refreshing,
  creatingProjectFromChat,
  connectionState,
  events,
  expandCurrentStepByDefault,
  onSend,
  onOpenWorkspace,
  onOpenSidebar,
  onOpenActivity,
  activityDrawerOpen,
  onCloseActivity,
  onOpenSettings,
  onRefresh,
  onRefreshRuntime,
  onPatchSubagentRuntime,
  onSyncProject,
  syncingProject,
  onApproveGate,
  onRejectGate,
  onCreateProjectFromChat,
}: ChatTabProps) {
  const [draft, setDraft] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<number | null>(null);
  const [messageSelectionMode, setMessageSelectionMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<Set<number>>(() => new Set());
  const [copiedSelection, setCopiedSelection] = useState(false);
  const [showProjectCreateConfirm, setShowProjectCreateConfirm] = useState(false);
  const [gateActionPipelineId, setGateActionPipelineId] = useState<number | null>(null);
  const [localOverlayMessages, setLocalOverlayMessages] = useState<MessageItem[]>([]);
  const [expandedMessageSteps, setExpandedMessageSteps] = useState<Record<number, StepExpansionValue>>({});
  const [expandedTaskRunSteps, setExpandedTaskRunSteps] = useState<Record<number, StepExpansionValue>>({});
  const [expandedProgressCards, setExpandedProgressCards] = useState<Record<string, StepExpansionValue>>({});
  const [stepAutoExpansionDisabled, setStepAutoExpansionDisabled] = useState(false);
  const [showMentionPicker, setShowMentionPicker] = useState(false);
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const [selectedTaskRunId, setSelectedTaskRunId] = useState<number | null>(null);
  const [projectBrowserTab, setProjectBrowserTab] = useState<ProjectBrowserTab>("artifacts");
  const [expandedFileTreePaths, setExpandedFileTreePaths] = useState<Record<string, boolean>>({});
  const [fileReader, setFileReader] = useState<FileReaderState | null>(null);
  const [activitySidebarWidth, setActivitySidebarWidth] = useState(ACTIVITY_SIDEBAR_DEFAULT_WIDTH);
  const [taskRunDetailsById, setTaskRunDetailsById] = useState<Record<number, TaskRunDetail>>({});
  const [loadingTaskRunId, setLoadingTaskRunId] = useState<number | null>(null);
  const [taskRunDetailError, setTaskRunDetailError] = useState("");
  const [approvalActionItemId, setApprovalActionItemId] = useState<number | null>(null);
  const [approvalActionError, setApprovalActionError] = useState("");
  const [commandSuggestions, setCommandSuggestions] = useState<ChatCommandDef[]>([]);
  const [historySuggestions, setHistorySuggestions] = useState<string[]>([]);
  const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(0);
  const [serverHistory, setServerHistory] = useState<string[]>([]);
  const [approvalActionMessage, setApprovalActionMessage] = useState("");
  const [subagentActionKey, setSubagentActionKey] = useState<string | null>(null);
  const [subagentActionMessage, setSubagentActionMessage] = useState("");
  const [subagentActionError, setSubagentActionError] = useState("");
  const [pendingApprovalItems, setPendingApprovalItems] = useState<ApprovalQueueItem[]>([]);
  const [approvalQueueLoaded, setApprovalQueueLoaded] = useState(false);
  const composerRef = useRef<HTMLDivElement | null>(null);

  // Attachment state for image/file uploads
  type PendingAttachment = {
    file: File;
    previewUrl: string;
    uploading: boolean;
    error: string | null;
    uploaded: { file_path: string; file_name: string; file_size: number; mime_type?: string } | null;
  };
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const draftHistoryByChatRef = useRef<Record<string, string[]>>(readDraftHistoryStore());
  const draftHistoryIndexRef = useRef<number | null>(null);
  const draftHistoryPendingDraftRef = useRef("");
  const isComposingRef = useRef(false);
  const pendingComposerCaretRef = useRef<number | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const currentScopeRef = useRef<string>(overlayScopeKey(chat?.id ?? null));
  const shouldStickThreadToBottomRef = useRef(true);
  const lastAutoScrolledChatIdRef = useRef<number | null>(chat?.id ?? null);
  const chatShellStyle = useMemo(
    () => ({ "--activity-sidebar-width": `${activitySidebarWidth}px` }) as CSSProperties,
    [activitySidebarWidth],
  );
  const handleActivitySidebarResizeStart = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = activitySidebarWidth;
    const pointerId = event.pointerId;
    event.currentTarget.setPointerCapture(pointerId);

    function handlePointerMove(moveEvent: PointerEvent) {
      setActivitySidebarWidth(clampNumber(startWidth + startX - moveEvent.clientX, ACTIVITY_SIDEBAR_MIN_WIDTH, ACTIVITY_SIDEBAR_MAX_WIDTH));
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
  const activeAgents = useMemo(() => agents.filter((agent) => agent.is_active), [agents]);
  const agentRunState = useMemo(() => {
    const state = new Map<string, { status: AgentStripStatus; runningCount: number; waitingCount: number }>();
    for (const agent of activeAgents) {
      const keys = [
        getAgentType(agent).trim().toLowerCase(),
        agent.name.trim().toLowerCase(),
        getAgentDisplayName(agent).trim().toLowerCase(),
      ].filter(Boolean);
      for (const key of keys) {
        state.set(key, { status: "online", runningCount: 0, waitingCount: 0 });
      }
    }

    for (const run of taskRuns) {
      if ((run.status || "").toLowerCase() !== "running") continue;
      const target = (run.target_agent_name || "").trim().toLowerCase();
      if (!target) continue;
      const current = state.get(target) ?? { status: "online" as AgentStripStatus, runningCount: 0, waitingCount: 0 };
      const nextStatus = taskRunAgentStripStatus(run);
      const nextRunningCount = current.runningCount + (nextStatus === "working" ? 1 : 0);
      const nextWaitingCount = current.waitingCount + (nextStatus === "waiting" ? 1 : 0);
      const mergedStatus =
        nextRunningCount > 0 ? "working" : nextWaitingCount > 0 ? "waiting" : current.status;
      state.set(target, { status: mergedStatus, runningCount: nextRunningCount, waitingCount: nextWaitingCount });
    }

    return state;
  }, [activeAgents, taskRuns]);
  const orderedActiveAgents = useMemo(() => {
    return [...activeAgents].sort((left, right) => {
      const leftState =
        agentRunState.get(getAgentType(left).trim().toLowerCase()) ??
        agentRunState.get(left.name.trim().toLowerCase()) ??
        agentRunState.get(getAgentDisplayName(left).trim().toLowerCase());
      const rightState =
        agentRunState.get(getAgentType(right).trim().toLowerCase()) ??
        agentRunState.get(right.name.trim().toLowerCase()) ??
        agentRunState.get(getAgentDisplayName(right).trim().toLowerCase());
      const rank = (status: AgentStripStatus | undefined) => (status === "working" ? 2 : status === "waiting" ? 1 : 0);
      const leftRank = rank(leftState?.status);
      const rightRank = rank(rightState?.status);
      if (leftRank !== rightRank) return rightRank - leftRank;
      return getAgentDisplayName(left).localeCompare(getAgentDisplayName(right));
    });
  }, [activeAgents, agentRunState]);
  const mentionableAgents = useMemo(
    () => [...agents].sort((left, right) => left.name.localeCompare(right.name)),
    [agents],
  );
  const defaultProjectName = chat?.title?.trim() || (chat ? `Project ${chat.id}` : "");
  const primaryAgent = useMemo(
    () => findAgentByType(activeAgents, DEFAULT_AGENT_TYPE) ?? activeAgents[0] ?? null,
    [activeAgents],
  );
  const draftHistoryKey = useMemo(() => `chat:${chat?.id ?? "global"}`, [chat?.id]);
  const globalDraftHistoryKey = "chat:global";
  const defaultAgentNames = useMemo(() => {
    if (primaryAgent) {
      return [getAgentType(primaryAgent)];
    }
    return [DEFAULT_AGENT_TYPE];
  }, [primaryAgent]);
  const [projectNameDraft, setProjectNameDraft] = useState(defaultProjectName);
  const [projectDescriptionDraft, setProjectDescriptionDraft] = useState("");
  const [projectAgentNames, setProjectAgentNames] = useState<string[]>(defaultAgentNames);
  const [projectSuggestionStore, setProjectSuggestionStore] = useState(() => readProjectFormSuggestionStore());
  const visibleMessages = useMemo(() => {
    const merged = new Map<string, MessageItem>();
    [...messages, ...localOverlayMessages]
      .sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime())
      .forEach((message) => {
        const key = visibleMessageKey(message);
        const existing = merged.get(key);
        merged.set(key, existing ? mergeVisibleMessagePair(existing, message) : message);
      });

    return Array.from(merged.values()).sort(
      (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
    );
  }, [localOverlayMessages, messages]);
  const selectableMessages = useMemo(
    () => visibleMessages.filter((message) => !message.localOnly && message.content.trim() !== ""),
    [visibleMessages],
  );
  const selectedMessages = useMemo(() => {
    if (selectedMessageIds.size === 0) return [];
    return selectableMessages.filter((message) => selectedMessageIds.has(message.id));
  }, [selectableMessages, selectedMessageIds]);
  const selectedMessageCount = selectedMessages.length;
  const cardsWithPromptPresentation = useMemo(() => decorateCardsWithSystemPromptPresentation(cards), [cards]);
  const runtimeBrowserFileEntries = useMemo(
    () => buildBrowserFileEntries(project, cards, taskRuns),
    [cards, project, taskRuns],
  );
  const workspaceBrowserFileEntries = useMemo(
    () => buildWorkspaceBrowserFileEntries(projectBrowserIndex),
    [projectBrowserIndex],
  );
  const browserFileEntries = useMemo(
    () => mergeBrowserFileEntries(workspaceBrowserFileEntries, runtimeBrowserFileEntries, project?.workspace_path),
    [project?.workspace_path, runtimeBrowserFileEntries, workspaceBrowserFileEntries],
  );
  const browserFileTree = useMemo(
    () => buildBrowserFileTree(browserFileEntries, project?.workspace_path, project?.name || chat?.title),
    [browserFileEntries, chat?.title, project?.name, project?.workspace_path],
  );
  const runtimeBrowserArtifactEntries = useMemo(
    () => buildBrowserArtifactEntries(cards, taskRuns),
    [cards, taskRuns],
  );
  const workspaceBrowserArtifactEntries = useMemo(
    () => buildWorkspaceBrowserArtifactEntries(projectBrowserIndex),
    [projectBrowserIndex],
  );
  const browserArtifactEntries = useMemo(
    () => mergeBrowserArtifactEntries(workspaceBrowserArtifactEntries, runtimeBrowserArtifactEntries, project?.workspace_path),
    [project?.workspace_path, runtimeBrowserArtifactEntries, workspaceBrowserArtifactEntries],
  );
  const browserArtifactGroups = useMemo(
    () => groupBrowserArtifactEntries(browserArtifactEntries),
    [browserArtifactEntries],
  );
  const browserProcessTree = processes;
  const browserProcessCount = useMemo(
    () => countRuntimeProcessNodes(browserProcessTree),
    [browserProcessTree],
  );
  useEffect(() => {
    const nextExpanded: Record<string, boolean> = { [browserFileTree.path]: true };
    setExpandedFileTreePaths((current) => ({ ...nextExpanded, ...current }));
  }, [browserFileTree.path]);
  const toggleFileTreePath = useCallback((path: string) => {
    setExpandedFileTreePaths((current) => ({ ...current, [path]: !current[path] }));
  }, []);
  const openProjectFilePath = useCallback(async (path: string, options?: { history?: string[]; historyIndex?: number }) => {
    if (!project?.id || !path.trim()) return;
    shouldStickThreadToBottomRef.current = true;
    let nextHistory = options?.history;
    let nextHistoryIndex = options?.historyIndex;
    setFileReader((current) => {
      if (!nextHistory) {
        const currentHistory = current?.history?.length ? current.history : [current?.path].filter((item): item is string => Boolean(item));
        const currentIndex = typeof current?.historyIndex === "number" ? current.historyIndex : currentHistory.length - 1;
        if (currentHistory[currentIndex] === path) {
          nextHistory = currentHistory;
          nextHistoryIndex = currentIndex;
        } else {
          nextHistory = [...currentHistory.slice(0, currentIndex + 1), path];
          nextHistoryIndex = nextHistory.length - 1;
        }
      }
      return {
        path,
        status: "loading",
        history: nextHistory,
        historyIndex: nextHistoryIndex,
      };
    });
    try {
      const data = await api.readProjectFile(project.id, path);
      setFileReader((current) => {
        const history = [...(current?.history ?? [data.path])];
        const historyIndex = typeof current?.historyIndex === "number" ? current.historyIndex : history.length - 1;
        if (historyIndex >= 0) history[historyIndex] = data.path;
        return {
          path: data.path,
          status: "ready",
          data,
          history,
          historyIndex,
        };
      });
      requestAnimationFrame(() => {
        threadEndRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
      });
    } catch (error) {
      setFileReader((current) => ({
        ...current,
        path,
        status: "error",
        error: error instanceof Error ? error.message : "Unable to read file.",
      }));
    }
  }, [project?.id]);
  const navigateFileReaderHistory = useCallback((direction: -1 | 1) => {
    const history = fileReader?.history ?? [];
    const currentIndex = fileReader?.historyIndex ?? 0;
    const nextIndex = Math.min(Math.max(currentIndex + direction, 0), history.length - 1);
    const path = history[nextIndex];
    if (!path || nextIndex === currentIndex) return;
    void openProjectFilePath(path, { history, historyIndex: nextIndex });
  }, [fileReader?.history, fileReader?.historyIndex, openProjectFilePath]);
  useEffect(() => {
    if (!fileReader) return undefined;

    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.isComposing) return;
      if (event.key !== "Escape" && event.code !== "Escape") return;
      event.preventDefault();
      setFileReader(null);
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [fileReader]);
  const openFileReader = useCallback((node: BrowserFileTreeNode) => {
    if (node.kind !== "file") return;
    void openProjectFilePath(node.path);
  }, [openProjectFilePath]);
  const openArtifactReader = useCallback((entry: BrowserArtifactEntry) => {
    const path = resolveArtifactWorkspacePath(entry, project?.workspace_path);
    if (!path) return;
    void openProjectFilePath(path);
  }, [openProjectFilePath, project?.workspace_path]);
  const saveFileReaderDraft = useCallback(async () => {
    if (!project?.id || !fileReader?.data || fileReader.mode !== "edit") return;
    const content = fileReader.draft ?? "";
    setFileReader((current) => current ? { ...current, saving: true, error: "", saveMessage: "" } : current);
    try {
      const data = await api.writeProjectFile(project.id, {
        path: fileReader.data.path,
        content,
        expected_mtime: fileReader.data.mtime,
      });
      setFileReader({
        path: data.path,
        status: "ready",
        data,
        mode: "read",
        draft: data.content,
        saving: false,
        saveMessage: "Saved",
      });
    } catch (error) {
      setFileReader((current) => current ? {
        ...current,
        saving: false,
        status: "ready",
        saveMessage: error instanceof Error ? error.message : "Unable to save file.",
      } : current);
    }
  }, [fileReader, project?.id]);
  useEffect(() => {
    if (!projectBrowserAutoRefreshEnabled) return;
    if (!project?.id || !fileReader?.path || fileReader.status !== "ready") return;
    if (fileReader.mode === "edit" || fileReader.saving) return;
    const currentEntry = projectBrowserContentRefresh?.updatedFiles.find((item) => item.path === fileReader.path);
    if (!currentEntry || typeof currentEntry.mtime !== "number" || !fileReader.data?.mtime) return;
    if (currentEntry.mtime <= fileReader.data.mtime) return;
    void openProjectFilePath(fileReader.path, {
      history: fileReader.history,
      historyIndex: fileReader.historyIndex,
    });
  }, [
    fileReader?.data?.mtime,
    fileReader?.history,
    fileReader?.historyIndex,
    fileReader?.mode,
    fileReader?.path,
    fileReader?.saving,
    fileReader?.status,
    openProjectFilePath,
    project?.id,
    projectBrowserAutoRefreshEnabled,
    projectBrowserContentRefresh,
  ]);
  const selectedTaskRunSummary = useMemo(() => {
    if (taskRuns.length === 0) return null;
    const preferredRuns = [...taskRuns].sort(compareTaskRunsForSidebarSelection);
    return taskRuns.find((run) => run.id === selectedTaskRunId) ?? preferredRuns[0] ?? null;
  }, [selectedTaskRunId, taskRuns]);
  const selectedTaskRunDetail = selectedTaskRunSummary
    ? resolveFreshTaskRunDetail(
        selectedTaskRunSummary,
        liveTaskRunDetailsById[selectedTaskRunSummary.id],
        taskRunDetailsById[selectedTaskRunSummary.id],
      )
    : null;
  const pendingApprovalItemsByTaskRunId = useMemo(() => {
    const grouped: Record<number, ApprovalQueueItem[]> = {};
    pendingApprovalItems.forEach((item) => {
      const runId = item.task_run_id;
      if (typeof runId !== "number") return;
      grouped[runId] = [...(grouped[runId] ?? []), item];
    });
    return grouped;
  }, [pendingApprovalItems]);
  const runtimeMonitorViewModel = useMemo(
    () =>
      buildRuntimeMonitorViewModel({
        agents,
        taskRuns,
        taskActivitiesById,
        taskTimelinesById,
        cards: cardsWithPromptPresentation,
        processes: browserProcessTree,
        pendingApprovalItemsByTaskRunId,
      }),
    [
      agents,
      browserProcessTree,
      cardsWithPromptPresentation,
      pendingApprovalItemsByTaskRunId,
      taskActivitiesById,
      taskRuns,
      taskTimelinesById,
    ],
  );
  const selectedApprovalItems = useMemo(
    () => (selectedTaskRunSummary?.id ? pendingApprovalItemsByTaskRunId[selectedTaskRunSummary.id] ?? [] : []),
    [pendingApprovalItemsByTaskRunId, selectedTaskRunSummary?.id],
  );

  useEffect(() => {
    if (draftHistoryKey !== globalDraftHistoryKey) {
      migrateGlobalDraftHistory(draftHistoryKey);
    }
    setTaskRunDetailError("");
    setLoadingTaskRunId(null);
    setTaskRunDetailsById({});
    setSelectedTaskRunId(null);
    setExpandedTaskRunSteps({});
    setApprovalActionItemId(null);
    setApprovalActionError("");
    setApprovalActionMessage("");
    setSubagentActionKey(null);
    setSubagentActionError("");
    setSubagentActionMessage("");
    setPendingApprovalItems([]);
    setApprovalQueueLoaded(false);
    setMessageSelectionMode(false);
    setSelectedMessageIds(new Set());
    setCopiedSelection(false);
    setCommandSuggestions([]);
    setHistorySuggestions([]);

    // Load server-side input history for cross-session recall
    if (chat?.id) {
      void api.getChatInputHistory(chat.id).then((result) => {
        setServerHistory(result.history ?? []);
      }).catch(() => {});
    } else {
      setServerHistory([]);
    }
    draftHistoryIndexRef.current = null;
    draftHistoryPendingDraftRef.current = "";
  }, [chat?.id, draftHistoryKey]);

  useEffect(() => {
    if (draftHistoryIndexRef.current === null) {
      draftHistoryPendingDraftRef.current = draft;
    }
  }, [draft]);

  useEffect(() => {
    if (taskRuns.length === 0) {
      if (selectedTaskRunId !== null) {
        setSelectedTaskRunId(null);
      }
      return;
    }
    if (selectedTaskRunId === null || !taskRuns.some((run) => run.id === selectedTaskRunId)) {
      const preferredRun = [...taskRuns].sort(compareTaskRunsForSidebarSelection)[0] ?? null;
      setSelectedTaskRunId(preferredRun?.id ?? null);
    }
  }, [selectedTaskRunId, taskRuns]);

  useEffect(() => {
    const runId = selectedTaskRunSummary?.id;
    if (!runId || selectedTaskRunDetail || loadingTaskRunId === runId) {
      return;
    }

    let cancelled = false;
    setTaskRunDetailError("");
    setLoadingTaskRunId(runId);

    api.getTaskRunDetail(runId)
      .then((detail) => {
        if (cancelled) return;
        setTaskRunDetailsById((current) => ({ ...current, [runId]: detail }));
      })
      .catch((error) => {
        if (cancelled) return;
        setTaskRunDetailError(error instanceof Error ? error.message : "Failed to load task run detail");
      })
      .finally(() => {
        if (cancelled) return;
        setLoadingTaskRunId((current) => (current === runId ? null : current));
      });

    return () => {
      cancelled = true;
    };
  }, [loadingTaskRunId, selectedTaskRunDetail, selectedTaskRunSummary]);

  useEffect(() => {
    const candidateRuns = taskRuns.filter((run) => shouldRenderInlineTaskRun(run));
    const missingRunIds = candidateRuns
      .filter((run) => !resolveFreshTaskRunDetail(run, liveTaskRunDetailsById[run.id], taskRunDetailsById[run.id]))
      .map((run) => run.id);
    if (missingRunIds.length === 0) return;

    let cancelled = false;
    void Promise.all(
      missingRunIds.map(async (runId) => {
        try {
          const detail = await api.getTaskRunDetail(runId);
          if (cancelled) return;
          setTaskRunDetailsById((current) => ({ ...current, [runId]: detail }));
        } catch {
          // Best-effort prefetch; selected-run loader already surfaces errors.
        }
      }),
    );
    return () => {
      cancelled = true;
    };
  }, [liveTaskRunDetailsById, taskRunDetailsById, taskRuns]);

  useEffect(() => {
    if (!chat?.id) return;
    let cancelled = false;

    const load = async () => {
      try {
        const items = await api.getApprovalQueue({
          chatroom_id: chat.id,
          status: "pending",
          limit: 100,
        });
        if (cancelled) return;
        setPendingApprovalItems(items);
        setApprovalQueueLoaded(true);
      } catch {
        if (cancelled) return;
        setApprovalQueueLoaded(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [chat?.id, taskRuns]);

  useEffect(() => {
    if (!chat?.id) return;
    const hasActiveBackgroundWork =
      taskRuns.some((run) => (run.status || "").toLowerCase() === "running")
      || pendingApprovalItems.length > 0;
    if (!hasActiveBackgroundWork) return;

    let cancelled = false;
    const intervalId = window.setInterval(() => {
      if (cancelled) return;
      void onRefresh();
    }, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [chat?.id, onRefresh, pendingApprovalItems.length, taskRuns]);

  const invalidateTaskRunDetail = useCallback((taskRunId: number | null) => {
    if (!taskRunId) return;
    setTaskRunDetailsById((current) => {
      if (!(taskRunId in current)) return current;
      const next = { ...current };
      delete next[taskRunId];
      return next;
    });
  }, []);

  const inspectTaskRun = useCallback((taskRunId: number) => {
    setSelectedTaskRunId(taskRunId);
    onOpenActivity();
  }, [onOpenActivity]);

  const refreshSubagentRuntime = useCallback(async (taskRunId: number) => {
    invalidateTaskRunDetail(taskRunId);
    if (onRefreshRuntime) {
      await onRefreshRuntime(taskRunId);
      return;
    }
    await onRefresh();
  }, [invalidateTaskRunDetail, onRefresh, onRefreshRuntime]);

  const handleWaitSubagent = useCallback(async (taskRunId: number, stepId: string) => {
    const actionKey = `${taskRunId}:${stepId}:wait`;
    setSubagentActionKey(actionKey);
    setSubagentActionError("");
    setSubagentActionMessage("");
    try {
      onPatchSubagentRuntime?.({
        taskRunId,
        stepId,
        status: "waiting",
        controlState: "waiting",
        note: "Waiting for subagent update.",
      });
      const result = await api.waitTaskRunSubagent(taskRunId, stepId, { timeoutMs: 1500 });
      await refreshSubagentRuntime(taskRunId);
      const waitResult = result["wait_result"] as Record<string, unknown> | undefined;
      const suggestedPoll = typeof waitResult?.["suggested_poll"] === "string" ? waitResult["suggested_poll"] : "continue";
      const terminal = waitResult?.["terminal"] === true;
      setSubagentActionMessage(terminal ? `Subagent ${stepId} reached a terminal state.` : `Wait checked ${stepId} (${suggestedPoll}).`);
    } catch (error) {
      await refreshSubagentRuntime(taskRunId);
      setSubagentActionError(error instanceof Error ? error.message : "Failed to wait for subagent.");
    } finally {
      setSubagentActionKey((current) => current === actionKey ? null : current);
    }
  }, [onPatchSubagentRuntime, refreshSubagentRuntime]);

  const handleCancelSubagent = useCallback(async (taskRunId: number, stepId: string) => {
    const actionKey = `${taskRunId}:${stepId}:cancel`;
    setSubagentActionKey(actionKey);
    setSubagentActionError("");
    setSubagentActionMessage("");
    try {
      onPatchSubagentRuntime?.({
        taskRunId,
        stepId,
        status: "cancelled",
        availableActions: [],
        controlState: "cancelled",
        terminal: true,
        note: "Cancelled from chat process tree.",
      });
      await api.cancelTaskRunSubagent(taskRunId, stepId, { cancelled_by: "home", note: "Cancelled from chat process tree." });
      await refreshSubagentRuntime(taskRunId);
      setSubagentActionMessage(`Cancelled subagent handle ${stepId}.`);
    } catch (error) {
      await refreshSubagentRuntime(taskRunId);
      setSubagentActionError(error instanceof Error ? error.message : "Failed to cancel subagent.");
    } finally {
      setSubagentActionKey((current) => current === actionKey ? null : current);
    }
  }, [onPatchSubagentRuntime, refreshSubagentRuntime]);

  const handleCloseSubagent = useCallback(async (taskRunId: number, stepId: string) => {
    const actionKey = `${taskRunId}:${stepId}:close`;
    setSubagentActionKey(actionKey);
    setSubagentActionError("");
    setSubagentActionMessage("");
    try {
      onPatchSubagentRuntime?.({
        taskRunId,
        stepId,
        status: "closed",
        availableActions: [],
        controlState: "closed",
        terminal: true,
        note: "Closed from chat process tree.",
      });
      await api.closeTaskRunSubagent(taskRunId, stepId, { cancelled_by: "home", note: "Closed from chat process tree." });
      await refreshSubagentRuntime(taskRunId);
      setSubagentActionMessage(`Closed subagent handle ${stepId}.`);
    } catch (error) {
      await refreshSubagentRuntime(taskRunId);
      setSubagentActionError(error instanceof Error ? error.message : "Failed to close subagent.");
    } finally {
      setSubagentActionKey((current) => current === actionKey ? null : current);
    }
  }, [onPatchSubagentRuntime, refreshSubagentRuntime]);

  const handleResolveApprovalQueueItem = useCallback(
    async (item: ApprovalQueueItem, action: "approve" | "reject", remember = false) => {
      if (approvalActionItemId === item.id) return;
      setApprovalActionItemId(item.id);
      setApprovalActionError("");
      setApprovalActionMessage("");
      try {
        let updated: ApprovalQueueItem;
        if (action === "approve") {
          updated = await api.approveApprovalQueueItem(item.id, {
            resolved_by: "home",
            remember: Boolean(remember),
          });
        } else {
          updated = await api.rejectApprovalQueueItem(item.id, {
            resolved_by: "home",
            remember: Boolean(remember),
          });
        }
        setPendingApprovalItems((current) => current.filter((pendingItem) => pendingItem.id !== item.id));
        setApprovalQueueLoaded(true);
        setApprovalActionMessage(
          action === "approve"
            ? updated.status === "approved"
              ? `${remember ? "Approved and remembered" : "Approved"} ${item.target_name || item.target_kind || "request"}.`
              : "Approval updated."
            : updated.status === "rejected"
              ? `${remember ? "Rejected and remembered" : "Rejected"} ${item.target_name || item.target_kind || "request"}.`
              : "Approval updated.",
        );
        if (chat?.id) {
          void api.getApprovalQueue({
            chatroom_id: chat.id,
            status: "pending",
            limit: 100,
          }).then((nextPending) => {
            setPendingApprovalItems(nextPending);
            setApprovalQueueLoaded(true);
          }).catch((error) => {
            setApprovalActionError(error instanceof Error ? error.message : "Failed to refresh approvals");
          });
        }
        invalidateTaskRunDetail(item.task_run_id ?? selectedTaskRunSummary?.id ?? null);
        const refreshTaskRunId = item.task_run_id ?? selectedTaskRunSummary?.id ?? null;
        if (onRefreshRuntime && typeof refreshTaskRunId === "number") {
          void onRefreshRuntime(refreshTaskRunId);
        } else {
          void onRefresh();
        }
      } catch (error) {
        setApprovalActionError(error instanceof Error ? error.message : `Failed to ${action} request`);
      } finally {
        setApprovalActionItemId((current) => (current === item.id ? null : current));
      }
    },
    [approvalActionItemId, chat?.id, invalidateTaskRunDetail, onRefresh, onRefreshRuntime, selectedTaskRunSummary?.id],
  );

  const fallbackStepCardsByMessageId = useMemo(() => {
    const mapped = new Map<number, ThreadCard[]>();
    const orderedCards = [...cardsWithPromptPresentation].sort(compareThreadCards);
    const assistantAnchorByTurnId = new Map<string, MessageItem>();
    const unassignedCards: ThreadCard[] = [];
    const taskRunTurnIds = new Set(
      taskRuns
        .filter((run) => shouldRenderInlineTaskRun(run))
        .map((run) => run.client_turn_id)
        .filter((value): value is string => Boolean(value && value.trim())),
    );

    // Prefer the frontend-generated client_turn_id as the single card lineage key.
    for (const message of visibleMessages) {
      if (!message.agent_name || !message.client_turn_id) continue;
      assistantAnchorByTurnId.set(message.client_turn_id, message);
    }

    for (const card of orderedCards) {
      const turnId = card.client_turn_id;
      if (turnId && taskRunTurnIds.has(turnId)) {
        unassignedCards.push(card);
        continue;
      }
      const anchor = turnId ? assistantAnchorByTurnId.get(turnId) : null;
      if (!anchor) {
        unassignedCards.push(card);
        continue;
      }
      appendMappedCards(mapped, anchor.id, [card]);
    }

    const pendingByActor = new Map<string, ThreadCard[]>();
    const actorMatchedCardIds = new Set<string>();

    const timeline = [
      ...unassignedCards.map((card) => ({
        sortKey: card.created_at,
        kind: "card" as const,
        card,
      })),
      ...visibleMessages.map((message) => ({
        sortKey: message.created_at,
        kind: "message" as const,
        message,
      })),
    ].sort(compareThreadTimelineItems);

    for (const item of timeline) {
      if (item.kind === "card") {
        const actor = cardActorName(item.card);
        pendingByActor.set(actor, [...(pendingByActor.get(actor) ?? []), item.card]);
        continue;
      }

      if (!item.message.agent_name) {
        continue;
      }

      const actor = item.message.agent_name;
      const pending = pendingByActor.get(actor) ?? [];
      if (pending.length === 0) continue;
      appendMappedCards(mapped, item.message.id, pending);
      pending.forEach((card) => actorMatchedCardIds.add(card.id));
      pendingByActor.delete(actor);
    }

    const remainingActorCards = unassignedCards.filter((card) => !actorMatchedCardIds.has(card.id));
    if (remainingActorCards.length > 0) {
      const groupedCardsByActor = new Map<string, ThreadCard[][]>();
      let currentActor: string | null = null;
      let currentGroup: ThreadCard[] = [];

      const flushGroup = () => {
        if (!currentActor || currentGroup.length === 0) return;
        groupedCardsByActor.set(currentActor, [...(groupedCardsByActor.get(currentActor) ?? []), currentGroup]);
        currentGroup = [];
      };

      for (const card of remainingActorCards) {
        const actor = cardActorName(card);
        if (actor !== currentActor) {
          flushGroup();
          currentActor = actor;
        }
        currentGroup = [...currentGroup, card];
      }
      flushGroup();

      for (const message of visibleMessages) {
        if (!message.agent_name) continue;
        const actorGroups = groupedCardsByActor.get(message.agent_name) ?? [];
        if (actorGroups.length === 0) continue;
        const [nextGroup, ...rest] = actorGroups;
        appendMappedCards(mapped, message.id, nextGroup);
        groupedCardsByActor.set(message.agent_name, rest);
      }
    }

    return mapped;
  }, [cardsWithPromptPresentation, taskRuns, visibleMessages]);
  const consumedFallbackCardIds = useMemo(
    () => new Set(Array.from(fallbackStepCardsByMessageId.values()).flatMap((group) => group.map((card) => card.id))),
    [fallbackStepCardsByMessageId],
  );

  const threadItems = useMemo<ThreadItem[]>(() => {
    const anchoredAssistantTurnIds = new Set(
      visibleMessages
        .filter((message) => Boolean(message.agent_name) && Boolean(message.client_turn_id))
        .map((message) => normalizeClientTurnId(message.client_turn_id))
        .filter((value): value is string => Boolean(value)),
    );
    const liveTaskRunMessages = taskRuns
      .filter((run) => shouldRenderTaskRunLiveChatCard(run))
      .filter((run) => {
        const turnId = normalizeClientTurnId(run.client_turn_id);
        return !turnId || !anchoredAssistantTurnIds.has(turnId);
      })
      .map((run) => buildTaskRunLiveMessage(run, agents));
    const hasLiveMessage =
      visibleMessages.some((message) => Boolean(message.isStreaming)) || liveTaskRunMessages.length > 0;
    const taskRunCardsById = new Map<number, ThreadCard[]>();
    const claimedTaskCardIds = new Set<string>();

    const taskRunsByPriority = [...taskRuns]
      .filter((run) => shouldRenderInlineTaskRun(run))
      .sort(compareTaskRunsForSidebarSelection);

    const attachCardToTaskRun = (runId: number, card: ThreadCard) => {
      taskRunCardsById.set(runId, [...(taskRunCardsById.get(runId) ?? []), card].sort(compareThreadCards));
      claimedTaskCardIds.add(card.id);
    };

    for (const card of cardsWithPromptPresentation) {
      if (consumedFallbackCardIds.has(card.id)) continue;

      const matchedByRunId =
        typeof card.run_id === "number"
          ? taskRunsByPriority.find((run) => run.id === card.run_id) ?? null
          : null;
      if (matchedByRunId) {
        attachCardToTaskRun(matchedByRunId.id, card);
        continue;
      }

      const matchedByTurn =
        normalizeClientTurnId(card.client_turn_id)
          ? taskRunsByPriority.find((run) => sameClientTurn(run.client_turn_id, card.client_turn_id)) ?? null
          : null;
      if (matchedByTurn) {
        attachCardToTaskRun(matchedByTurn.id, card);
      }
    }

    const orderedItems: ThreadItem[] = [
      ...visibleMessages.map((message) => ({
        id: `message-${message.id}`,
        sortKey: message.created_at,
        kind: "message" as const,
        message,
      })),
      ...liveTaskRunMessages.map((message) => ({
        id: `message-${message.id}`,
        sortKey: message.created_at,
        kind: "message" as const,
        message,
      })),
      ...taskRuns
        .filter((run) => shouldRenderInlineTaskRun(run))
        .map((run) => ({
          id: `task-run-${run.id}`,
          sortKey: run.updated_at || run.created_at || new Date().toISOString(),
          kind: "task_run" as const,
          taskRun: run,
          detail: resolveFreshTaskRunDetail(run, liveTaskRunDetailsById[run.id], taskRunDetailsById[run.id]),
          cards: taskRunCardsById.get(run.id) ?? EMPTY_THREAD_CARDS,
        })),
      ...cardsWithPromptPresentation
        .filter(
          (card) =>
            !consumedFallbackCardIds.has(card.id) &&
            !claimedTaskCardIds.has(card.id) &&
            !(card.client_turn_id && anchoredAssistantTurnIds.has(card.client_turn_id)),
        )
        .map((card) => ({
          id: `card-${card.id}`,
          sortKey: card.created_at,
          kind: "card" as const,
          card,
        })),
    ];

    orderedItems.sort(compareThreadTimelineItems);

    const mergedItems: ThreadItem[] = [];
    let streak: DecoratedChatCardItem[] = [];

    const flushStreak = () => {
      if (streak.length === 0) return;
      if (streak.length === 1) {
        mergedItems.push(
          ...streak.map((card) => ({
            id: `card-${card.id}`,
            sortKey: card.created_at,
            kind: "card" as const,
            card,
          })),
        );
      } else {
        mergedItems.push({
          id: `card-merge-${streak[0].id}`,
          sortKey: streak[0].created_at,
          kind: "card",
          card: {
            id: `tool-merge-${streak[0].id}`,
            kind: "tool_merge",
            created_at: streak[0].created_at,
            source: streak[0].source,
            agent: streak[0].agent,
            tool: streak[0].tool,
            count: streak.length,
            items: streak,
          },
        });
      }
      streak = [];
    };

    for (const item of orderedItems) {
      if (
        item.kind === "card" &&
        item.card.kind === "tool_call" &&
        !parseInteractiveFileToolRequest(item.card) &&
        streak.length > 0 &&
        streak[streak.length - 1].kind === "tool_call" &&
        !parseInteractiveFileToolRequest(streak[streak.length - 1]) &&
        streak[streak.length - 1].tool === item.card.tool &&
        streak[streak.length - 1].agent === item.card.agent
      ) {
        streak.push(item.card);
        continue;
      }

      if (item.kind === "card" && item.card.kind === "tool_call" && !parseInteractiveFileToolRequest(item.card)) {
        flushStreak();
        streak.push(item.card);
        continue;
      }

      flushStreak();
      mergedItems.push(item);
    }

    flushStreak();
    const batchedItems: ThreadItem[] = [];
    let activityBatch: ThreadCard[] = [];

    const flushActivityBatch = () => {
      if (activityBatch.length === 0) return;
      if (!hasLiveMessage) {
        batchedItems.push(
          ...activityBatch.map((card) => ({
            id: `card-${card.id}`,
            sortKey: card.created_at,
            kind: "card" as const,
            card,
          })),
        );
        activityBatch = [];
        return;
      }
      batchedItems.push({
        id: `activity-${activityBatch[0].id}`,
        sortKey: activityBatch[0].created_at,
        kind: "activity_batch",
        cards: activityBatch,
      });
      activityBatch = [];
    };

    for (const item of mergedItems) {
      if (item.kind === "card") {
        activityBatch.push(item.card);
        continue;
      }

      flushActivityBatch();
      batchedItems.push(item);
    }

    flushActivityBatch();
    return batchedItems;
  }, [
    agents,
    cardsWithPromptPresentation,
    consumedFallbackCardIds,
    liveTaskRunDetailsById,
    taskRunDetailsById,
    taskRuns,
    visibleMessages,
  ]);

  const currentActivityAgentName = useMemo(() => {
    const streamingAgent =
      [...visibleMessages]
        .reverse()
        .find((message) => message.isStreaming && Boolean(message.agent_name))?.agent_name ?? null;
    if (streamingAgent) return streamingAgent;

    return (
      [...cardsWithPromptPresentation]
        .reverse()
        .map((card) => cardActorName(card))
        .find((name) => name !== "system") ?? null
    );
  }, [cardsWithPromptPresentation, visibleMessages]);

  const latestActivityBatchId = useMemo(
    () =>
      [...threadItems]
        .reverse()
        .find((item) => item.kind === "activity_batch")?.id ?? null,
    [threadItems],
  );

  const trailingMentionMatch = useMemo(
    () => draft.match(/(?:^|\s)@([a-zA-Z0-9_-]*)$/),
    [draft],
  );
  const mentionQuery = trailingMentionMatch?.[1]?.toLowerCase() ?? "";
  const mentionOptions = useMemo(() => {
    if (!showMentionPicker) return [];
    if (mentionQuery === "") return mentionableAgents;
    return mentionableAgents.filter((agent) => {
      const displayName = getAgentDisplayName(agent).toLowerCase();
      const type = getAgentType(agent).toLowerCase();
      const role = agent.role.toLowerCase();
      return displayName.includes(mentionQuery) || type.includes(mentionQuery) || role.includes(mentionQuery);
    });
  }, [mentionQuery, mentionableAgents, showMentionPicker]);
  const projectNameSuggestions = useMemo(
    () =>
      filterTextSuggestions(projectSuggestionStore.chat.names, projectNameDraft).map((value) => ({
        key: `chat-project-name-${value}`,
        label: value,
        value,
        title: value,
      })),
    [projectNameDraft, projectSuggestionStore.chat.names],
  );
  const projectDescriptionSuggestions = useMemo(
    () =>
      filterTextSuggestions(projectSuggestionStore.chat.descriptions, projectDescriptionDraft, 3).map((value) => ({
        key: `chat-project-description-${value}`,
        label: value.length > 48 ? `${value.slice(0, 48)}...` : value,
        value,
        title: value,
      })),
    [projectDescriptionDraft, projectSuggestionStore.chat.descriptions],
  );
  const projectAgentPresetSuggestions = useMemo(
    () =>
      filterAgentSetSuggestions(projectSuggestionStore.chat.agentSets, projectAgentNames).map((agentTypes) => ({
        key: `chat-project-agents-${agentTypes.join("-")}`,
        label: agentTypes
          .map((agentType) => getAgentDisplayName(activeAgents.find((agent) => getAgentType(agent) === agentType) ?? null))
          .join(" + "),
        value: JSON.stringify(agentTypes),
        title: agentTypes.map((agentType) => `@${agentType}`).join(", "),
      })),
    [activeAgents, projectAgentNames, projectSuggestionStore.chat.agentSets],
  );

  useEffect(() => {
    setProjectNameDraft(defaultProjectName);
    setProjectDescriptionDraft("");
    setProjectAgentNames(defaultAgentNames);
    setShowProjectCreateConfirm(false);
  }, [chat?.id, defaultProjectName, defaultAgentNames]);

  useEffect(() => {
    setExpandedMessageSteps({});
    setExpandedTaskRunSteps({});
    setExpandedProgressCards({});
    setStepAutoExpansionDisabled(false);
  }, [chat?.id]);

  useEffect(() => {
    function handleStepCollapseKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      setExpandedMessageSteps({});
      setExpandedTaskRunSteps({});
      setExpandedProgressCards({});
      setStepAutoExpansionDisabled(true);
    }

    document.addEventListener("keydown", handleStepCollapseKeyDown);
    return () => document.removeEventListener("keydown", handleStepCollapseKeyDown);
  }, []);

  useEffect(() => {
    const chatId = chat?.id ?? null;
    currentScopeRef.current = overlayScopeKey(chatId);

    if (chatId !== null) {
      const migrated = migrateOverlayMessages(null, chatId);
      if (migrated.length > 0) {
        setLocalOverlayMessages(migrated);
        return;
      }
    }

    setLocalOverlayMessages(readOverlayMessages(chatId));
  }, [chat?.id]);

  useEffect(() => {
    setLocalOverlayMessages((current) => {
      let changed = false;
      const next = current.flatMap((item) => {
        if (item.optimisticKind === "user") {
          const hasServerCopy = messages.some(
            (message) =>
              !message.agent_name &&
              (
                (item.client_turn_id && message.client_turn_id === item.client_turn_id) ||
                (
                  !item.client_turn_id &&
                  message.message_type === item.message_type &&
                  message.content === item.content
                )
              ),
          );
          if (hasServerCopy) {
            changed = true;
            return [];
          }
          return [item];
        }

        if (item.optimisticKind === "assistant_placeholder") {
          const matchedServerReply =
            [...messages]
              .filter(
                (message) =>
                  Boolean(message.agent_name) &&
                  (
                    (item.client_turn_id && message.client_turn_id === item.client_turn_id) ||
                    (
                      !item.client_turn_id &&
                      new Date(message.created_at).getTime() >= new Date(item.created_at).getTime() - 1000
                    )
                  ),
              )
              .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())[0] ??
            null;
          const hasServerReply = Boolean(matchedServerReply);
          if (!hasServerReply) {
            return [item];
          }
          changed = true;
          return [];
        }

        return [item];
      });

      if (!changed) return current;
      writeOverlayMessages(chat?.id ?? null, next);
      return next;
    });
  }, [chat?.id, messages]);

  useEffect(() => {
    if (!chat?.id || loading || sending) return;
    if (messages.length > 0 || optimisticMessages.length > 0 || cards.length > 0 || localOverlayMessages.length === 0) return;

    writeOverlayMessages(chat.id, []);
    setLocalOverlayMessages([]);
  }, [cards.length, chat?.id, loading, localOverlayMessages.length, messages.length, optimisticMessages.length, sending]);

  useEffect(() => {
    if (optimisticMessages.length === 0) return;

    setLocalOverlayMessages((current) => {
      let changed = false;
      const usedMatches = new Set<number>();
      const seeded = [...current];

      optimisticMessages.forEach((candidate) => {
        const alreadyExists = seeded.some(
          (item) =>
            item.optimisticKind === candidate.optimisticKind &&
            (
              (item.client_turn_id && candidate.client_turn_id && item.client_turn_id === candidate.client_turn_id) ||
              (
                !item.client_turn_id &&
                !candidate.client_turn_id &&
                item.message_type === candidate.message_type &&
                item.content === candidate.content &&
                Math.abs(new Date(item.created_at).getTime() - new Date(candidate.created_at).getTime()) < 30_000
              )
            ),
        );
        if (alreadyExists) return;
        changed = true;
        seeded.push({
          ...candidate,
          localOnly: true,
        });
      });

      const next = seeded.map((item) => {
        if (item.optimisticKind === "user") {
          const matchedIndex = optimisticMessages.findIndex(
            (candidate, index) =>
              !usedMatches.has(index) &&
              !candidate.agent_name &&
              (
                (item.client_turn_id && candidate.client_turn_id && candidate.client_turn_id === item.client_turn_id) ||
                (
                  !item.client_turn_id &&
                  !candidate.client_turn_id &&
                  candidate.message_type === item.message_type &&
                  candidate.content === item.content &&
                  Math.abs(new Date(candidate.created_at).getTime() - new Date(item.created_at).getTime()) < 30_000
                )
              ) &&
              candidate.message_type === item.message_type,
          );
          if (matchedIndex === -1) {
            return item;
          }
          usedMatches.add(matchedIndex);
          return item;
        }

        if (item.optimisticKind === "assistant_placeholder") {
          const matchedIndex = optimisticMessages.findIndex(
            (candidate, index) =>
              !usedMatches.has(index) &&
              Boolean(candidate.agent_name) &&
              (
                (item.client_turn_id && candidate.client_turn_id && candidate.client_turn_id === item.client_turn_id) ||
                (
                  !item.client_turn_id &&
                  !candidate.client_turn_id &&
                  Math.abs(new Date(candidate.created_at).getTime() - new Date(item.created_at).getTime()) < 30_000
                )
              ),
          );
          if (matchedIndex === -1) {
            return item;
          }

          usedMatches.add(matchedIndex);
          const matched = optimisticMessages[matchedIndex];
          const nextItem: MessageItem = {
            ...item,
            agent_name: matched.agent_name || item.agent_name,
            content: matched.content || item.content,
            isStreaming: matched.isStreaming ?? item.isStreaming,
            streamSteps: matched.streamSteps ?? item.streamSteps,
          };

          if (
            nextItem.agent_name !== item.agent_name ||
            nextItem.content !== item.content ||
            nextItem.isStreaming !== item.isStreaming ||
            JSON.stringify(nextItem.streamSteps ?? []) !== JSON.stringify(item.streamSteps ?? [])
          ) {
            changed = true;
            return nextItem;
          }
        }

        return item;
      });

      if (!changed) return current;
      next.sort((left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime());
      writeOverlayMessages(chat?.id ?? null, next);
      return next;
    });
  }, [chat?.id, optimisticMessages]);

  useEffect(() => {
    if (copiedMessageId === null) return;
    const timer = window.setTimeout(() => setCopiedMessageId(null), 1200);
    return () => window.clearTimeout(timer);
  }, [copiedMessageId]);

  useEffect(() => {
    if (trailingMentionMatch) {
      setShowMentionPicker(true);
      return;
    }
    setShowMentionPicker(false);
    setSelectedMentionIndex(0);
  }, [trailingMentionMatch]);

  useEffect(() => {
    if (!showMentionPicker) {
      setSelectedMentionIndex(0);
      return;
    }
    setSelectedMentionIndex((current) => Math.min(current, Math.max(mentionOptions.length - 1, 0)));
  }, [mentionOptions.length, showMentionPicker]);

  useLayoutEffect(() => {
    const nextCaret = pendingComposerCaretRef.current;
    if (nextCaret === null) return;

    pendingComposerCaretRef.current = null;
    const input = composerInputRef.current;
    if (!input) return;

    input.focus();
    input.setSelectionRange(nextCaret, nextCaret);
  }, [draft]);

  useEffect(() => {
    if (!showMentionPicker) return;

    function handlePointerDown(event: MouseEvent) {
      if (!composerRef.current?.contains(event.target as Node)) {
        setShowMentionPicker(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [showMentionPicker]);

  function updateThreadStickiness() {
    const thread = threadRef.current;
    if (!thread) return;
    const distanceToBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    shouldStickThreadToBottomRef.current = distanceToBottom <= THREAD_AUTO_SCROLL_THRESHOLD;
  }

  useLayoutEffect(() => {
    const activeChatId = chat?.id ?? null;
    const shouldForceScroll = lastAutoScrolledChatIdRef.current !== activeChatId;
    if (!shouldForceScroll && !shouldStickThreadToBottomRef.current) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      threadEndRef.current?.scrollIntoView({ block: "end" });
      shouldStickThreadToBottomRef.current = true;
      lastAutoScrolledChatIdRef.current = activeChatId;
    });

    return () => window.cancelAnimationFrame(frame);
  }, [chat?.id, loading, threadItems, localOverlayMessages]);

  function submitContent(rawContent: string) {
    const next = rawContent.trim();
    if (!next || sending) return;

    // Clear autocomplete
    setCommandSuggestions([]);
    setHistorySuggestions([]);

    // Save to local draft history
    const history = getDraftHistory();
    draftHistoryByChatRef.current[draftHistoryKey] =
      history[history.length - 1] === next ? history : mergeDraftHistory(history, [next]);
    writeDraftHistoryStore(draftHistoryByChatRef.current, [draftHistoryKey, globalDraftHistoryKey]);
    draftHistoryIndexRef.current = null;
    draftHistoryPendingDraftRef.current = "";
    shouldStickThreadToBottomRef.current = true;
    setShowMentionPicker(false);
    setDraft("");

    // Save to server-side history (async, fire-and-forget)
    if (chat?.id) {
      void api.saveChatInputHistory(chat.id, next).catch(() => {});
    }

    // Command detection: if input starts with /, execute as command
    if (isCommandInput(next)) {
      const now = new Date();
      const baseId = -Math.floor(now.getTime());
      const clientTurnId = createClientTurnId();
      const userLocalMessage: MessageItem = {
        id: baseId,
        content: next,
        message_type: "user",
        created_at: now.toISOString(),
        agent_name: null,
        client_turn_id: clientTurnId,
        optimisticKind: "user",
        localOnly: true,
      };
      flushSync(() => {
        setLocalOverlayMessages((current) => {
          const nextMessages = [...current, userLocalMessage].sort(
            (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
          );
          writeOverlayMessages(chat?.id ?? null, nextMessages);
          return nextMessages;
        });
      });

      // Execute command via API
      void api.executeCommand(next, chat?.id, undefined).then((result) => {
        const cmdMsg: MessageItem = {
          id: -Math.floor(Date.now()) - 2,
          content: result.content || "指令执行完成",
          message_type: "command_result",
          created_at: new Date().toISOString(),
          agent_name: "system",
          client_turn_id: clientTurnId,
          metadata: {
            command_result: true,
            command: result.command,
            success: result.success,
            category: result.category,
            title: result.title,
          },
          optimisticKind: "assistant_placeholder",
          localOnly: true,
        };
        setLocalOverlayMessages((current) => {
          const nextMessages = [...current, cmdMsg].sort(
            (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
          );
          writeOverlayMessages(chat?.id ?? null, nextMessages);
          return nextMessages;
        });
      }).catch((error) => {
        const errMsg: MessageItem = {
          id: -Math.floor(Date.now()) - 3,
          content: `指令执行失败: ${error instanceof Error ? error.message : "未知错误"}`,
          message_type: "text",
          created_at: new Date().toISOString(),
          agent_name: "system",
          client_turn_id: clientTurnId,
          optimisticKind: "assistant_placeholder",
          localOnly: true,
        };
        setLocalOverlayMessages((current) => [...current, errMsg]);
      });
      return;
    }
    const now = new Date();
    const baseId = -Math.floor(now.getTime());
    const clientTurnId = createClientTurnId();
    const userLocalMessage: MessageItem = {
      id: baseId,
      content: next,
      message_type: "user",
      created_at: now.toISOString(),
      agent_name: null,
      client_turn_id: clientTurnId,
      optimisticKind: "user",
      localOnly: true,
    };
    flushSync(() => {
      setLocalOverlayMessages((current) => {
        const nextMessages = [...current, userLocalMessage].sort(
          (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
        );
        writeOverlayMessages(chat?.id ?? null, nextMessages);
        return nextMessages;
      });
    });

    window.requestAnimationFrame(() => {
      // Collect uploaded attachments
      const uploadedAttachments = pendingAttachments
        .filter((a) => a.uploaded !== null)
        .map((a) => a.uploaded!);
      // Clear pending attachments after collecting
      if (uploadedAttachments.length > 0) {
        clearAllAttachments();
      }

      void onSend(next, { clientTurnId, attachments: uploadedAttachments.length > 0 ? uploadedAttachments : undefined }).catch((error) => {
        const message = error instanceof Error ? error.message : "Send failed";
        setLocalOverlayMessages((current) => {
          const failedAt = new Date();
          const failureMessage: MessageItem = {
            id: -Math.floor(failedAt.getTime()) - 1,
            content: `Error: ${message}`,
            message_type: "text",
            created_at: failedAt.toISOString(),
            agent_name: getAgentDisplayName(primaryAgent),
            client_turn_id: clientTurnId,
            optimisticKind: "assistant_placeholder",
            localOnly: true,
          };
          const nextMessages = [...current, failureMessage].sort(
            (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
          );
          writeOverlayMessages(chat?.id ?? null, nextMessages);
          return nextMessages;
        });
      });
    });
  }

  function submitDraft() {
    submitContent(draft);
  }

  // --- Attachment handlers ---
  function handleFileSelect(files: FileList | null) {
    if (!files || files.length === 0) return;
    const imageFiles = Array.from(files).filter((f) =>
      f.type.startsWith("image/") || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(f.name)
    );
    if (imageFiles.length === 0) return;

    const newAttachments: PendingAttachment[] = imageFiles.map((file) => ({
      file,
      previewUrl: URL.createObjectURL(file),
      uploading: false,
      error: null,
      uploaded: null,
    }));
    setPendingAttachments((current) => [...current, ...newAttachments]);

    // Auto-upload each file
    for (const attachment of newAttachments) {
      void uploadAttachment(attachment);
    }
  }

  async function uploadAttachment(attachment: PendingAttachment) {
    if (!chat?.id) return;
    setPendingAttachments((current) =>
      current.map((a) => (a.file === attachment.file ? { ...a, uploading: true, error: null } : a))
    );
    try {
      const result = await api.uploadFile(chat.id, attachment.file);
      setPendingAttachments((current) =>
        current.map((a) => (a.file === attachment.file ? { ...a, uploading: false, uploaded: result } : a))
      );
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : "Upload failed";
      setPendingAttachments((current) =>
        current.map((a) => (a.file === attachment.file ? { ...a, uploading: false, error: errorMsg } : a))
      );
    }
  }

  function removeAttachment(index: number) {
    setPendingAttachments((current) => {
      const removed = current[index];
      if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((_, i) => i !== index);
    });
  }

  function clearAllAttachments() {
    setPendingAttachments((current) => {
      for (const a of current) {
        if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
      }
      return [];
    });
  }

  // Drag-and-drop handler for the compose area
  function handleComposeDragOver(event: React.DragEvent) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleComposeDrop(event: React.DragEvent) {
    event.preventDefault();
    handleFileSelect(event.dataTransfer.files);
  }

  // Paste handler for images
  useEffect(() => {
    function handlePaste(event: ClipboardEvent) {
      const items = event.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (const item of Array.from(items)) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) imageFiles.push(file);
        }
      }
      if (imageFiles.length > 0) {
        event.preventDefault();
        handleFileSelect(imageFiles as unknown as FileList);
      }
    }
    document.addEventListener("paste", handlePaste);
    return () => document.removeEventListener("paste", handlePaste);
  }, [chat?.id]);

  function handleAnalyzeFailureStep(step: MessageStreamStep, context: FailureStepAnalysisContext) {
    if (sending) return;
    submitContent(buildFailureStepAnalysisPrompt(step, context));
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submitDraft();
  }

  const toggleProgressCard = useCallback((groupKey: string, cardId: string, isExpanded: boolean) => {
    setStepAutoExpansionDisabled(false);
    setExpandedProgressCards((current) => ({
      ...current,
      [groupKey]: isExpanded ? null : cardId,
    }));
  }, []);

  const toggleMessageStep = useCallback((messageId: number, stepId: string, isExpanded: boolean) => {
    setStepAutoExpansionDisabled(false);
    setExpandedMessageSteps((current) => ({
      ...current,
      [messageId]: isExpanded ? null : stepId,
    }));
  }, []);

  const toggleTaskRunStep = useCallback((taskRunId: number, stepId: string, isExpanded: boolean) => {
    setStepAutoExpansionDisabled(false);
    setExpandedTaskRunSteps((current) => ({
      ...current,
      [taskRunId]: isExpanded ? null : stepId,
    }));
  }, []);

  function queueComposerCaret(caret: number) {
    pendingComposerCaretRef.current = caret;
    window.requestAnimationFrame(() => {
      const nextCaret = pendingComposerCaretRef.current;
      if (nextCaret === null) return;

      pendingComposerCaretRef.current = null;
      const input = composerInputRef.current;
      if (!input) return;

      input.focus();
      input.setSelectionRange(nextCaret, nextCaret);
    });
  }

  function mergeDraftHistory(...sources: string[][]) {
    const merged: string[] = [];
    for (const source of sources) {
      for (const item of source) {
        if (!item.trim()) continue;
        if (merged[merged.length - 1] === item) continue;
        merged.push(item);
      }
    }
    return merged.slice(-50);
  }

  function migrateGlobalDraftHistory(targetKey: string) {
    if (targetKey === globalDraftHistoryKey) return;

    const globalHistory = draftHistoryByChatRef.current[globalDraftHistoryKey] ?? [];
    if (globalHistory.length === 0) return;

    const targetHistory = draftHistoryByChatRef.current[targetKey] ?? [];
    draftHistoryByChatRef.current[targetKey] = mergeDraftHistory(globalHistory, targetHistory);
    delete draftHistoryByChatRef.current[globalDraftHistoryKey];
    writeDraftHistoryStore(draftHistoryByChatRef.current, [targetKey]);
  }

  function getDraftHistory() {
    const history = draftHistoryByChatRef.current[draftHistoryKey] ?? [];
    if (history.length > 0 || draftHistoryKey === globalDraftHistoryKey) return history;
    return draftHistoryByChatRef.current[globalDraftHistoryKey] ?? [];
  }

  function insertMention(agentType: string) {
    setDraft((current) => {
      if (/(?:^|\s)@([a-zA-Z0-9_-]*)$/.test(current)) {
        const next = current.replace(/(^|\s)@([a-zA-Z0-9_-]*)$/, `$1@${agentType} `);
        queueComposerCaret(next.length);
        return next;
      }

      const separator = current.length === 0 || /\s$/.test(current) ? "" : " ";
      const next = `${current}${separator}@${agentType} `;
      queueComposerCaret(next.length);
      return next;
    });
    setShowMentionPicker(false);
    setSelectedMentionIndex(0);
  }

  function handleMentionTriggerClick() {
    if (sending) return;
    setDraft((current) => {
      if (/(?:^|\s)@([a-zA-Z0-9_-]*)$/.test(current)) {
        queueComposerCaret(current.length);
        return current;
      }
      const separator = current.length === 0 || /\s$/.test(current) ? "" : " ";
      const next = `${current}${separator}@`;
      queueComposerCaret(next.length);
      return next;
    });
    setShowMentionPicker(true);
    setSelectedMentionIndex(0);
  }

  function navigateDraftHistory(direction: -1 | 1) {
    const history = getDraftHistory();
    if (history.length === 0) return;

    const currentIndex = draftHistoryIndexRef.current;
    if (currentIndex === null) {
      if (direction !== -1) return;
      draftHistoryPendingDraftRef.current = draft;
      draftHistoryIndexRef.current = history.length - 1;
      setDraft(history[history.length - 1] ?? "");
      return;
    }

    const nextIndex = currentIndex + direction;
    if (nextIndex < 0) {
      draftHistoryIndexRef.current = 0;
      setDraft(history[0] ?? "");
      return;
    }
    if (nextIndex >= history.length) {
      draftHistoryIndexRef.current = null;
      setDraft(draftHistoryPendingDraftRef.current);
      return;
    }

    draftHistoryIndexRef.current = nextIndex;
    setDraft(history[nextIndex] ?? "");
  }

  function applySuggestion(value: string) {
    setDraft(value);
    setCommandSuggestions([]);
    setHistorySuggestions([]);
    setSelectedSuggestionIndex(0);
    // Move caret to end
    window.requestAnimationFrame(() => {
      const input = composerInputRef.current;
      if (input) {
        input.focus();
        input.setSelectionRange(value.length, value.length);
      }
    });
  }

  const hasSuggestions = commandSuggestions.length > 0 || historySuggestions.length > 0;
  const allSuggestions: string[] = commandSuggestions.length > 0
    ? commandSuggestions.map((c) => c.command + (c.args ? " " + c.args : ""))
    : historySuggestions;

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || isComposingRef.current) return;

    const keyCode = "keyCode" in event ? event.keyCode : event.which;
    const isEnterKey = event.key === "Enter" || event.code === "Enter" || keyCode === 13 || event.which === 13;
    const isArrowDownKey = event.key === "ArrowDown" || event.code === "ArrowDown";
    const isArrowUpKey = event.key === "ArrowUp" || event.code === "ArrowUp";
    const isTabKey = event.key === "Tab" || event.code === "Tab";
    const isEscapeKey = event.key === "Escape" || event.code === "Escape";

    // Autocomplete panel navigation
    if (hasSuggestions && !showMentionPicker) {
      if (isArrowDownKey) {
        event.preventDefault();
        setSelectedSuggestionIndex((current) => (current + 1) % allSuggestions.length);
        return;
      }
      if (isArrowUpKey) {
        event.preventDefault();
        setSelectedSuggestionIndex((current) => (current - 1 + allSuggestions.length) % allSuggestions.length);
        return;
      }
      if (isTabKey) {
        event.preventDefault();
        applySuggestion(allSuggestions[selectedSuggestionIndex] ?? allSuggestions[0]);
        return;
      }
      if (isEscapeKey) {
        event.preventDefault();
        setCommandSuggestions([]);
        setHistorySuggestions([]);
        return;
      }
    }

    if (showMentionPicker && mentionOptions.length > 0) {
      if (isArrowDownKey) {
        event.preventDefault();
        setSelectedMentionIndex((current) => (current + 1) % mentionOptions.length);
        return;
      }

      if (isArrowUpKey) {
        event.preventDefault();
        setSelectedMentionIndex((current) => (current - 1 + mentionOptions.length) % mentionOptions.length);
        return;
      }

      if ((isEnterKey && !event.shiftKey) || isTabKey) {
        event.preventDefault();
        insertMention(getAgentType(mentionOptions[selectedMentionIndex] ?? mentionOptions[0]));
        return;
      }
    }

    if (isEscapeKey && showMentionPicker) {
      event.preventDefault();
      setShowMentionPicker(false);
      return;
    }

    const selectionStart = event.currentTarget.selectionStart ?? 0;
    const selectionEnd = event.currentTarget.selectionEnd ?? 0;
    const hasSelection = selectionStart !== selectionEnd;
    const caretAtStart = selectionStart === 0 && selectionEnd === 0;
    const caretAtEnd = selectionStart === draft.length && selectionEnd === draft.length;
    const singleLineDraft = !draft.includes("\n");

    if (!showMentionPicker && !hasSelection && isArrowUpKey && (draft.length === 0 || caretAtStart || (singleLineDraft && caretAtEnd))) {
      event.preventDefault();
      navigateDraftHistory(-1);
      return;
    }

    if (!showMentionPicker && !hasSelection && isArrowDownKey && draftHistoryIndexRef.current !== null && caretAtEnd) {
      event.preventDefault();
      navigateDraftHistory(1);
      return;
    }

    if (!isEnterKey || event.shiftKey) return;
    event.preventDefault();
    void submitDraft();
  }

  const handleCopyMessage = useCallback(async (message: MessageItem) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
    } catch {
      setCopiedMessageId(null);
    }
  }, []);

  const cancelMessageSelection = useCallback(() => {
    setCopiedSelection(false);
    setSelectedMessageIds(new Set());
    setMessageSelectionMode(false);
  }, []);

  const toggleSelectedMessage = useCallback((message: MessageItem) => {
    if (message.localOnly || message.content.trim() === "") return;
    setCopiedSelection(false);
    setMessageSelectionMode(true);
    setSelectedMessageIds((current) => {
      const next = new Set(current);
      if (next.has(message.id)) {
        next.delete(message.id);
      } else {
        next.add(message.id);
      }
      return next;
    });
  }, []);

  const handleCopySelectedMessages = useCallback(async () => {
    if (selectedMessages.length === 0) return;
    try {
      await navigator.clipboard.writeText(selectedMessages.map(formatMessageCopyBlock).join("\n\n"));
      setCopiedSelection(false);
      setSelectedMessageIds(new Set());
      setMessageSelectionMode(false);
    } catch {
      setCopiedSelection(false);
    }
  }, [selectedMessages]);

  async function handleConfirmCreateProject() {
    const nextProjectName = projectNameDraft.trim() || defaultProjectName;
    if (!nextProjectName || projectAgentNames.length === 0) return;
    const payload = {
      name: nextProjectName,
      description: projectDescriptionDraft.trim(),
      agent_names: projectAgentNames,
    };
    setShowProjectCreateConfirm(false);
    await onCreateProjectFromChat(payload);
    setProjectSuggestionStore((current) => rememberChatProjectSuggestion(current, payload));
  }

  const handleApproveGate = useCallback(async (pipelineId: number) => {
    try {
      setGateActionPipelineId(pipelineId);
      await onApproveGate(pipelineId);
    } finally {
      setGateActionPipelineId(null);
    }
  }, [onApproveGate]);

  const handleRejectGate = useCallback(async (pipelineId: number) => {
    try {
      setGateActionPipelineId(pipelineId);
      await onRejectGate(pipelineId);
    } finally {
      setGateActionPipelineId(null);
    }
  }, [onRejectGate]);

  function toggleProjectAgent(agentName: string) {
    setProjectAgentNames((current) =>
      current.includes(agentName)
        ? current.filter((value) => value !== agentName)
        : [...current, agentName],
    );
  }

  const connectionCopy =
    connectionState === "connected"
      ? "Realtime connected"
      : connectionState === "connecting"
        ? "Connecting realtime"
        : "Realtime offline";
  const workspaceCopy = project?.workspace_path?.replace(/\\/g, "/") ?? "";
  const workspaceLabel =
    workspaceCopy.length > 54 ? `...${workspaceCopy.slice(-54)}` : workspaceCopy;
  const repoLabel = project?.repo_full_name
    ? `${project.repo_full_name}${project.clone_ref ? ` @ ${project.clone_ref}` : ""}`
    : "";
  const chatHeading =
    project && chat && chat.id !== project.default_chatroom_id
      ? chat.title
      : project?.name ?? chat?.title ?? "New conversation";
  const chatSubheading = project ? "" : chat ? "standalone chat" : "Your first message will create a chat";
  const threadContent = useMemo(() => {
    const resolveExpandedMessageStepId = (message: MessageItem) => {
      return Object.prototype.hasOwnProperty.call(expandedMessageSteps, message.id)
        ? expandedMessageSteps[message.id]
        : !expandCurrentStepByDefault || stepAutoExpansionDisabled
          ? null
          : undefined;
    };

    if (!chat && threadItems.length === 0) {
      return (
        <div className="agent-chat__welcome">
          <div className="agent-chat__avatar--logo">CA</div>
          <h2>Start with a message</h2>
          <p className="agent-chat__hint">Say anything below. Catown will create a chat automatically and pin it under Chats.</p>
          <div className="agent-chat__badges">
            <span className="agent-chat__badge">chat first</span>
            <span className="agent-chat__badge">project later</span>
            <span className="agent-chat__badge">multi-agent ready</span>
          </div>
        </div>
      );
    }

    if (threadItems.length > 0) {
      return (
        <>
          {threadItems.map((item, index) => {
            const currentAgentName = threadItemAgentName(item, agents);
            const previousAgentName = index > 0 ? threadItemAgentName(threadItems[index - 1], agents) : null;
            const hasAgentBreak =
              index > 0 &&
              Boolean(currentAgentName) &&
              Boolean(previousAgentName) &&
              currentAgentName !== previousAgentName;

            const content =
              item.kind === "message"
                ? (
                    <MessageRow
                      key={`message-${item.message.id}`}
                      message={item.message}
                      agents={agents}
                      copiedMessageId={copiedMessageId}
                      onCopyMessage={handleCopyMessage}
                      selectionMode={messageSelectionMode}
                      selected={selectedMessageIds.has(item.message.id)}
                      onToggleSelected={toggleSelectedMessage}
                      selectedMessageCount={selectedMessageCount}
                      copiedSelection={copiedSelection}
                      onCancelSelection={cancelMessageSelection}
                      onCopySelectedMessages={handleCopySelectedMessages}
                      expandedStepId={resolveExpandedMessageStepId(item.message)}
                      onToggleStep={toggleMessageStep}
                      messageTimeline={
                        item.message.agent_name && typeof item.message.runtime_summary?.task_run_id === "number"
                          ? taskTimelinesById[item.message.runtime_summary.task_run_id] ?? null
                          : null
                      }
                      fallbackStepCards={fallbackStepCardsByMessageId.get(item.message.id) ?? EMPTY_THREAD_CARDS}
                      onAnalyzeFailureStep={handleAnalyzeFailureStep}
                    />
                  )
                : item.kind === "activity_batch"
                  ? renderActivityBatch(
                      item.id,
                      item.cards,
                      agents,
                      item.id === latestActivityBatchId,
                      item.id === latestActivityBatchId ? currentActivityAgentName : null,
                      expandedProgressCards,
                      expandCurrentStepByDefault && !stepAutoExpansionDisabled,
                      toggleProgressCard,
                      gateActionPipelineId,
                      handleApproveGate,
                      handleRejectGate,
                    )
                  : item.kind === "task_run"
                    ? renderTaskRunInlineCard(
                        item.taskRun,
                        item.detail,
                        taskActivitiesById[item.taskRun.id] ?? null,
                        taskTimelinesById[item.taskRun.id] ?? null,
                        item.cards,
                        agents,
                        pendingApprovalItemsByTaskRunId[item.taskRun.id] ?? [],
                        approvalQueueLoaded,
                        approvalActionItemId,
                        Object.prototype.hasOwnProperty.call(expandedTaskRunSteps, item.taskRun.id)
                          ? expandedTaskRunSteps[item.taskRun.id]
                          : !expandCurrentStepByDefault || stepAutoExpansionDisabled
                            ? null
                            : undefined,
                        toggleTaskRunStep,
                        handleResolveApprovalQueueItem,
                        handleAnalyzeFailureStep,
                      )
                    : renderCard(
                        item.card,
                        gateActionPipelineId,
                        handleApproveGate,
                        handleRejectGate,
                        inspectTaskRun,
                        handleWaitSubagent,
                        handleCancelSubagent,
                        handleCloseSubagent,
                        subagentActionKey,
                      );

            return (
              <div key={item.id} className={hasAgentBreak ? "chat-turn-gap" : undefined}>
                {content}
              </div>
            );
          })}
        </>
      );
    }

    if (loading) {
      return (
        <div className="agent-chat__welcome">
          <h2>Loading conversation</h2>
          <p className="agent-chat__hint">Fetching the latest thread.</p>
        </div>
      );
    }

    return (
      <div className="agent-chat__welcome">
        <div className="agent-chat__avatar--logo">CA</div>
        <h2>No messages yet</h2>
        <p className="agent-chat__hint">
          {project
            ? "Send the first instruction to start this project session."
            : "Use this standalone chat to explore before turning it into a project."}
        </p>
      </div>
    );
  }, [
    approvalActionItemId,
    approvalQueueLoaded,
    chat,
    cancelMessageSelection,
    copiedMessageId,
    copiedSelection,
    currentActivityAgentName,
    expandedMessageSteps,
    expandedProgressCards,
    expandCurrentStepByDefault,
    expandedTaskRunSteps,
    fallbackStepCardsByMessageId,
    gateActionPipelineId,
    handleAnalyzeFailureStep,
    handleApproveGate,
    handleCopyMessage,
    handleCopySelectedMessages,
    handleRejectGate,
    handleResolveApprovalQueueItem,
    latestActivityBatchId,
    loading,
    localOverlayMessages,
    messageSelectionMode,
    pendingApprovalItemsByTaskRunId,
    project,
    selectedMessageCount,
    selectedMessageIds,
    stepAutoExpansionDisabled,
    taskActivitiesById,
    threadItems,
    toggleSelectedMessage,
    toggleMessageStep,
    toggleProgressCard,
    toggleTaskRunStep,
  ]);

  return (
    <section className="chat-shell chat" style={chatShellStyle}>
      <header className="chat-header">
        <div className="chat-header__left">
          <div className="chat-session">
            <h2>{chatHeading}</h2>
            {project ? (
              <div className="chat-session__meta">
                {workspaceLabel ? (
                  <button
                    type="button"
                    className="chat-session__workspace"
                    onClick={() => void onOpenWorkspace()}
                    title={workspaceCopy}
                  >
                    <span className="chat-session__workspace-icon" aria-hidden="true">
                      <svg viewBox="0 0 16 16" fill="none">
                        <path
                          d="M1.75 4.25A1.5 1.5 0 0 1 3.25 2.75H6.1c.34 0 .66.14.9.38l.92.92c.23.24.56.37.9.37h3.93a1.5 1.5 0 0 1 1.5 1.5v5.88a1.5 1.5 0 0 1-1.5 1.5H3.25a1.5 1.5 0 0 1-1.5-1.5V4.25Z"
                          stroke="currentColor"
                          strokeWidth="1.2"
                          strokeLinejoin="round"
                        />
                        <path
                          d="M1.75 5.5h12.5"
                          stroke="currentColor"
                          strokeWidth="1.2"
                          strokeLinecap="round"
                        />
                      </svg>
                    </span>
                    <span>{workspaceLabel}</span>
                  </button>
                ) : null}
                {project.source_type === "github" && repoLabel ? (
                  <span className="chat-session__repo" title={project.repo_url || repoLabel}>
                    <span className="chat-session__repo-icon" aria-hidden="true">
                      <svg viewBox="0 0 16 16" fill="none">
                        <path
                          d="M5.25 3.25h5.5a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5h-5.5a1.5 1.5 0 0 1-1.5-1.5v-6.5a1.5 1.5 0 0 1 1.5-1.5Z"
                          stroke="currentColor"
                          strokeWidth="1.2"
                        />
                        <path
                          d="M6 6.25h4M6 8h4M6 9.75h2.5"
                          stroke="currentColor"
                          strokeWidth="1.2"
                          strokeLinecap="round"
                        />
                      </svg>
                    </span>
                    <span>{repoLabel}</span>
                  </span>
                ) : null}
                {project.source_type === "github" ? (
                  <button
                    type="button"
                    className="chat-session__sync"
                    onClick={() => void onSyncProject()}
                    disabled={syncingProject}
                    title="Fetch and fast-forward the managed GitHub workspace"
                  >
                    {syncingProject ? "Syncing..." : "Sync"}
                  </button>
                ) : null}
              </div>
            ) : chatSubheading ? (
              <span>{chatSubheading}</span>
            ) : null}
          </div>
        </div>

        <div className="chat-header__right">
          <span className={`chat-live-pill is-${connectionState}`} title={`UI version ${UI_VERSION}`}>
            <span className="status-dot chat-live-pill__dot" />
            <span>v{UI_VERSION}</span>
          </span>
          <a
            className="btn btn--sm chat-monitor-link"
            href="/monitor"
            target="_blank"
            rel="noreferrer"
            aria-label="Open monitor"
            title="Open monitor"
          >
            <Monitor size={15} aria-hidden="true" />
            <span>Monitor</span>
          </a>
          <button
            type="button"
            className="btn btn--sm btn--icon mobile-sidebar-toggle mobile-sidebar-toggle--left"
            onClick={onOpenSidebar}
            aria-label="Open chats and projects"
            title="Open sidebar"
          >
            <Menu size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn--sm btn--icon mobile-sidebar-toggle mobile-sidebar-toggle--right"
            onClick={onOpenActivity}
            aria-label="Open activity"
            title="Open activity"
          >
            <PanelRightOpen size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="btn btn--sm btn--icon settings-icon-btn"
            onClick={onOpenSettings}
            aria-label="Open settings"
            title="Settings"
          >
            <Settings size={16} aria-hidden="true" />
          </button>
        </div>
      </header>

      <div className="chat-split-container">
        <div className="chat-main">
          <div
            ref={threadRef}
            className="chat-thread"
            onScroll={() => {
              updateThreadStickiness();
            }}
          >
            <div className="chat-thread-inner">
              {!project && chat && showProjectCreateConfirm ? (
                <div className="chat-inline-decision">
                  <div className="chat-inline-decision__copy">
                    <strong>Create project from this chat?</strong>
                    <p>
                      This keeps the current standalone chat in the sidebar, creates a hidden main project chat, and copies
                      the recent conversation into it.
                    </p>
                  </div>
                  <div className="project-form chat-inline-decision__form settings-form">
                    <label className="settings-form__field settings-form__field--full">
                      <span>Name</span>
                      <input
                        value={projectNameDraft}
                        onChange={(event) => setProjectNameDraft(event.target.value)}
                        placeholder={defaultProjectName}
                      />
                      <FormSuggestionStrip
                        label="Recent"
                        items={projectNameSuggestions}
                        onSelect={setProjectNameDraft}
                      />
                    </label>
                    <label className="settings-form__field settings-form__field--full">
                      <span>Description</span>
                      <textarea
                        value={projectDescriptionDraft}
                        onChange={(event) => setProjectDescriptionDraft(event.target.value)}
                        placeholder="Optional project brief"
                        rows={3}
                      />
                      <FormSuggestionStrip
                        label="Recent"
                        items={projectDescriptionSuggestions}
                        onSelect={setProjectDescriptionDraft}
                      />
                    </label>
                    <div className="settings-form__field settings-form__field--full">
                      <span className="field-label">Agents</span>
                      {activeAgents.length === 0 ? (
                        <p className="chat-inline-decision__hint">No active agents are available yet.</p>
                      ) : (
                        <div className="agent-selector-grid settings-chip-row">
                          {activeAgents.map((agent) => (
                            <button
                              key={agent.id}
                              type="button"
                              className={`agent-select-chip ${projectAgentNames.includes(getAgentType(agent)) ? "is-selected" : ""}`}
                              onClick={() => toggleProjectAgent(getAgentType(agent))}
                            >
                              <strong>{getAgentDisplayName(agent)}</strong>
                              <small>@{getAgentType(agent)}</small>
                              <span>{agent.role}</span>
                            </button>
                          ))}
                        </div>
                      )}
                      <FormSuggestionStrip
                        label="Preset"
                        items={projectAgentPresetSuggestions}
                        onSelect={(value) => {
                          try {
                            const parsed = JSON.parse(value) as string[];
                            setProjectAgentNames(parsed);
                          } catch {
                            // Ignore malformed local suggestion payloads.
                          }
                        }}
                      />
                    </div>
                  </div>
                  <div className="chat-inline-decision__actions">
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => void handleConfirmCreateProject()}
                      disabled={creatingProjectFromChat || projectAgentNames.length === 0 || projectNameDraft.trim() === ""}
                    >
                      {creatingProjectFromChat ? "Creating..." : "Confirm"}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => setShowProjectCreateConfirm(false)}
                      disabled={creatingProjectFromChat}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}

              {threadContent}
              {fileReader ? (
                <FileReaderCard
                  state={fileReader}
                  onClose={() => setFileReader(null)}
                  onEdit={() => {
                    setFileReader((current) => current?.data
                      ? { ...current, mode: "edit", draft: current.data.content, saveMessage: "" }
                      : current);
                  }}
                  onDraftChange={(value) => {
                    setFileReader((current) => current ? { ...current, draft: value, saveMessage: "" } : current);
                  }}
                  onDiscard={() => {
                    setFileReader((current) => current?.data
                      ? { ...current, mode: "read", draft: current.data.content, saveMessage: "" }
                      : current);
                  }}
                  onSave={() => void saveFileReaderDraft()}
                  onOpenLinkedFile={(path) => void openProjectFilePath(path)}
                  onBack={() => navigateFileReaderHistory(-1)}
                  onForward={() => navigateFileReaderHistory(1)}
                />
              ) : null}
              <div ref={threadEndRef} />
            </div>
          </div>

          <form className="chat-compose" onSubmit={handleSubmit} onDragOver={handleComposeDragOver} onDrop={handleComposeDrop}>
            {pendingAttachments.length > 0 ? (
              <div className="attachment-preview-strip">
                {pendingAttachments.map((att, index) => (
                  <div key={index} className="attachment-preview-item">
                    <img src={att.previewUrl} alt={att.file.name} className="attachment-preview-img" />
                    {att.uploading ? (
                      <span className="attachment-status uploading">⬆</span>
                    ) : att.error ? (
                      <span className="attachment-status error" title={att.error}>✕</span>
                    ) : att.uploaded ? (
                      <span className="attachment-status ok">✓</span>
                    ) : null}
                    <button
                      type="button"
                      className="attachment-remove-btn"
                      onClick={() => removeAttachment(index)}
                      title="Remove"
                    >
                      <X size={10} aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
            {orderedActiveAgents.length > 0 ? (
              <div className="agent-strip">
                {orderedActiveAgents.map((agent) => {
                  const state =
                    agentRunState.get(getAgentType(agent).trim().toLowerCase()) ??
                    agentRunState.get(agent.name.trim().toLowerCase()) ??
                    agentRunState.get(getAgentDisplayName(agent).trim().toLowerCase());
                  const chipStatus = state?.status ?? "online";
                  const isWorking = chipStatus === "working";
                  const isWaiting = chipStatus === "waiting";
                  const statusLabel = isWorking
                    ? state && state.runningCount > 1
                      ? `working x${state.runningCount}`
                      : "working"
                    : isWaiting
                      ? state && state.waitingCount > 1
                        ? `waiting x${state.waitingCount}`
                        : "waiting"
                      : "online";
                  return (
                    <button
                      key={agent.id}
                      type="button"
                      className={`agent-strip__chip ${isWorking ? "is-working" : isWaiting ? "is-waiting" : ""}`}
                      disabled={sending}
                      onClick={() => insertMention(getAgentType(agent))}
                      title={`Mention ${getAgentDisplayName(agent)} (@${getAgentType(agent)})`}
                      style={buildAgentThemeStyle(getAgentType(agent), agents)}
                    >
                      <span className={`agent-dot ${isWorking ? "is-working" : isWaiting ? "is-waiting" : "is-active"}`} />
                      <div>
                        <strong>{getAgentDisplayName(agent)}</strong>
                        <small>{statusLabel}</small>
                        <small>@{getAgentType(agent)}</small>
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : null}
            <div ref={composerRef} className="agent-chat__input">
              {showMentionPicker ? (
                <div className="agent-chat__mention-menu" role="listbox" aria-label="Select an agent to mention">
                  <div className="agent-chat__mention-header">
                    <div>
                      <div className="agent-chat__mention-kicker">Mention agent</div>
                      <div className="agent-chat__mention-title">
                        {mentionQuery ? `Results for @${mentionQuery}` : "Choose a teammate"}
                      </div>
                    </div>
                    <div className="agent-chat__mention-shortcut">Up/Down move - Enter insert</div>
                  </div>
                  {mentionOptions.length > 0 ? (
                    mentionOptions.map((agent, index) => (
                      <button
                        key={agent.id}
                        type="button"
                        className={`agent-chat__mention-item ${index === selectedMentionIndex ? "is-selected" : ""}`}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => insertMention(getAgentType(agent))}
                        role="option"
                        aria-selected={index === selectedMentionIndex}
                      >
                        <span className="agent-chat__mention-avatar">{initials(getAgentDisplayName(agent))}</span>
                        <span className="agent-chat__mention-copy">
                          <span className="agent-chat__mention-name">@{getAgentType(agent)}</span>
                          <span className="agent-chat__mention-role">{getAgentDisplayName(agent)} · {agent.role}</span>
                        </span>
                        <span className={`agent-chat__mention-state ${agent.is_active ? "is-active" : ""}`}>
                          {agent.is_active ? "online" : "idle"}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="agent-chat__mention-empty">No matching agents.</div>
                  )}
                </div>
              ) : null}
              {hasSuggestions && !showMentionPicker ? (
                <div className="agent-chat__autocomplete" role="listbox" aria-label="Suggestions">
                  <div className="agent-chat__autocomplete-header">
                    <span>{commandSuggestions.length > 0 ? "指令" : "历史记录"}</span>
                    <span className="agent-chat__autocomplete-hint">Tab 选择 · Esc 关闭</span>
                  </div>
                  <div className="agent-chat__autocomplete-list">
                    {commandSuggestions.length > 0 ? (
                      commandSuggestions.map((cmd, index) => (
                        <button
                          key={cmd.command}
                          type="button"
                          className={`agent-chat__autocomplete-item ${index === selectedSuggestionIndex ? "is-selected" : ""}`}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => applySuggestion(cmd.command + (cmd.args ? " " + cmd.args : ""))}
                          role="option"
                          aria-selected={index === selectedSuggestionIndex}
                        >
                          <span className="agent-chat__autocomplete-cmd">{cmd.command}</span>
                          {cmd.args ? <span className="agent-chat__autocomplete-args">{cmd.args}</span> : null}
                          <span className="agent-chat__autocomplete-desc">{cmd.description}</span>
                        </button>
                      ))
                    ) : (
                      historySuggestions.map((entry, index) => (
                        <button
                          key={`${entry}-${index}`}
                          type="button"
                          className={`agent-chat__autocomplete-item ${index === selectedSuggestionIndex ? "is-selected" : ""}`}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => applySuggestion(entry)}
                          role="option"
                          aria-selected={index === selectedSuggestionIndex}
                        >
                          <span className="agent-chat__autocomplete-text">{entry}</span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
              ) : null}
              <textarea
                ref={composerInputRef}
                value={draft}
                onChange={(event) => {
                  const value = event.target.value;
                  setDraft(value);
                  // Update autocomplete suggestions
                  if (isCommandInput(value)) {
                    const cmds = matchCommands(value);
                    setCommandSuggestions(cmds);
                    setHistorySuggestions([]);
                  } else if (value.trim().length > 0) {
                    const localHistory = getDraftHistory();
                    const merged = [...serverHistory, ...localHistory];
                    const unique = [...new Set(merged)];
                    const matches = matchHistory(value, unique);
                    setCommandSuggestions([]);
                    setHistorySuggestions(matches);
                  } else {
                    setCommandSuggestions([]);
                    setHistorySuggestions([]);
                  }
                  setSelectedSuggestionIndex(0);
                }}
                onKeyDownCapture={handleComposerKeyDown}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                }}
                placeholder={chat ? "Send a message..." : "Start a conversation..."}
                rows={1}
                disabled={sending}
              />
              <div className="agent-chat__toolbar">
                <div className="agent-chat__toolbar-left">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    style={{ display: "none" }}
                    onChange={(e) => {
                      handleFileSelect(e.target.files);
                      e.target.value = "";
                    }}
                  />
                  <button
                    type="button"
                    className="agent-chat__input-btn attachment-btn"
                    disabled={sending}
                    onClick={() => fileInputRef.current?.click()}
                    title="Attach image (📎)"
                  >
                    📎
                  </button>
                  <button
                    type="button"
                    className="agent-chat__input-btn"
                    disabled={sending}
                    onClick={handleMentionTriggerClick}
                    title="Mention an agent"
                  >
                    @
                  </button>
                  <button
                    type="button"
                    className="agent-chat__input-btn"
                    disabled={sending}
                    onClick={() => {
                      draftHistoryIndexRef.current = null;
                      draftHistoryPendingDraftRef.current = "";
                      setDraft("");
                      setShowMentionPicker(false);
                    }}
                    title="Clear draft"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </div>

                <div className="agent-chat__toolbar-right">
                  <span className="compose-status">{sending ? `${getAgentDisplayName(primaryAgent)} thinking...` : connectionCopy}</span>
                  <button
                    type="submit"
                    className="chat-send-btn"
                    disabled={sending || draft.trim() === ""}
                    aria-label="Send message"
                  >
                    {sending ? "..." : <SendHorizontal size={16} aria-hidden="true" />}
                  </button>
                </div>
              </div>
            </div>
          </form>
        </div>

        {activityDrawerOpen ? <button type="button" className="mobile-drawer-backdrop" onClick={onCloseActivity} aria-label="Close browser panel" /> : null}
        <aside className={`chat-sidebar ${activityDrawerOpen ? "is-mobile-open" : ""}`}>
          <button
            type="button"
            className="sidebar-resize-handle sidebar-resize-handle--right"
            onPointerDown={handleActivitySidebarResizeStart}
            aria-label="Resize activity sidebar"
            title="Resize sidebar"
          />
          <div className="sidebar-panel">
            <div className="sidebar-header sidebar-header--browser-actions">
              <button
                type="button"
                className="chat-sidebar__close"
                onClick={onCloseActivity}
                aria-label="Close browser"
                title="Close browser"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="sidebar-content project-browser">
              <div className="project-browser__tabs" role="tablist" aria-label="Project browser sections">
                {[
                  {
                    id: "files" as const,
                    label: "Files",
                    count: projectBrowserIndex?.truncated ? `${browserFileEntries.length}+` : browserFileEntries.length,
                    Icon: FolderTree,
                  },
                  { id: "artifacts" as const, label: "Artifacts", count: browserArtifactEntries.length, Icon: Archive },
	                  { id: "processes" as const, label: "Processes", count: browserProcessCount, Icon: Monitor },
	                  { id: "runtime" as const, label: "Runtime", count: runtimeMonitorViewModel.summary.runningActions, Icon: Workflow },
                ].map(({ id, label, count, Icon }) => (
                  <button
                    key={id}
                    type="button"
                    className={`project-browser__tab ${projectBrowserTab === id ? "is-active" : ""}`}
                    onClick={() => setProjectBrowserTab(id)}
                    role="tab"
                    aria-selected={projectBrowserTab === id}
                  >
                    <Icon size={14} aria-hidden="true" />
                    <span>{label}</span>
                    <strong>{count}</strong>
                  </button>
                ))}
              </div>

              {projectBrowserTab === "files" ? (
                <div className="project-browser__section project-browser__section--files">
                  {browserFileTree.children.length === 0 ? (
                    <div className="empty-card">Files will appear after tools reference workspace paths.</div>
                  ) : (
                    <div className="file-tree" role="tree" aria-label="Workspace files">
                      {renderProjectFileTreeNode(browserFileTree, 0, expandedFileTreePaths, toggleFileTreePath, openFileReader)}
                    </div>
                  )}
                </div>
              ) : null}

              {projectBrowserTab === "artifacts" ? (
                <div className="project-browser__section project-browser__section--artifacts">
                  {browserArtifactEntries.length === 0 ? (
                    <div className="empty-card">Deliverables such as PRDs, ADRs, specs, reports, and release notes will appear here.</div>
                  ) : (
                    <div className="artifact-list" aria-label="Project artifacts">
                      {browserArtifactGroups.map((group) => (
                        <details key={group.type} className="artifact-group">
                          <summary className="artifact-group__header">
                            <span>{group.type}</span>
                            <strong>{group.items.length}</strong>
                          </summary>
                          {group.items.map((entry) => (
                            <button
                              key={entry.id}
                              type="button"
                              className={`artifact-row ${entry.agentName ? "artifact-row--agent" : ""}`}
                              title={entry.detail || entry.name}
                              onClick={() => openArtifactReader(entry)}
                              style={buildAgentThemeStyle(entry.agentName, agents)}
                            >
                              <span className="artifact-row__icon" aria-hidden="true"><ArtifactIcon type={entry.type} /></span>
                              <span className="artifact-row__name">{entry.name}</span>
                              <span className="artifact-row__meta">
                                {entry.agentName ? <span>{resolveAgentLabel(entry.agentName, agents)}</span> : null}
                                <span>{entry.status}</span>
                                {entry.timestamp ? <span>{formatTime(entry.timestamp)}</span> : null}
                              </span>
                            </button>
                          ))}
                        </details>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}

	              {projectBrowserTab === "processes" ? (
	                <div className="project-browser__section project-browser__section--processes">
	                  {subagentActionMessage ? <div className="empty-card">{subagentActionMessage}</div> : null}
	                  {subagentActionError ? <div className="empty-card">{subagentActionError}</div> : null}
	                  {browserProcessCount === 0 || !browserProcessTree ? (
                    <div className="empty-card">Running background tasks will appear here.</div>
                  ) : (
                    <ProcessTreeNode
                      node={browserProcessTree}
                      agents={agents}
                      activeActionKey={subagentActionKey}
                      onInspectTaskRun={inspectTaskRun}
                      onWaitSubagent={handleWaitSubagent}
                      onCancelSubagent={handleCancelSubagent}
                      onCloseSubagent={handleCloseSubagent}
                    />
	                  )}
	                </div>
	              ) : null}

	              {projectBrowserTab === "runtime" ? (
	                <div className="project-browser__section project-browser__section--runtime">
	                  <RuntimeMonitorSection
	                    viewModel={runtimeMonitorViewModel}
	                    agents={agents}
	                    onInspectTaskRun={inspectTaskRun}
	                  />
	                </div>
	              ) : null}
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
