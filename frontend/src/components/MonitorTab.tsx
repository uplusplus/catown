import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Boxes, BrainCircuit, CheckCircle2, CircleDashed, Crown, FileText, Globe, Monitor, Server, UserRound, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "../api/client";
import { AdaptiveCardDeck } from "./AdaptiveCardDeck";
import { FlowTopologyView } from "./FlowTopologyView";
import type { FlowTopologyGraph, FlowTopologyNode, FlowTopologyStatus } from "./FlowTopologyView";
import type {
  ApprovalQueueItem,
  AgentInfo,
  ConfigResponse,
  MonitorTaskRunSummary,
  MonitorTaskRunStep,
  MonitorTaskRunStepsResponse,
  MonitorTaskRunsResponse,
  MonitorApprovalAuditEntry,
  MonitorApprovalAuditResponse,
  MonitorApprovalQueueEntry,
  MonitorApprovalQueueResponse,
  MonitorFileEvent,
  MonitorFilesResponse,
  MonitorLogEntry,
  MonitorNetworkEvent,
  MonitorOverview,
  MonitorCompactionItem,
  MonitorContextCompactionsResponse,
  MonitorProcessEntry,
  MonitorProcessesResponse,
  MonitorRuntimeDetail,
  MonitorUsageResponse,
  ProjectSummary,
  TaskRunDetail,
  TaskRunResumeResponse,
} from "../types";
import { UI_VERSION } from "../uiVersion";

const DEFAULT_CONTEXT_WINDOW = 128000;
const MONITOR_RUNTIME_LIMIT = 80;
const MONITOR_MESSAGE_LIMIT = 40;
const MONITOR_USAGE_WINDOW_LIMIT = 400;
const MONITOR_STREAM_RECONNECT_DELAY_MS = 3000;
const ERROR_AUTO_DISMISS_MS = 8000;
const MONITOR_NETWORK_RETAIN_LIMIT = 300;
const MONITOR_NETWORK_RENDER_LIMIT = 120;
const TASK_RUN_EVENT_RENDER_LIMIT = 80;
const TASK_RUN_STEP_RENDER_LIMIT = 80;

type MonitorPage = {
  id: string;
  label: string;
};

const PRIMARY_PAGES = [
  { id: "overview", label: "Overview" },
  { id: "flow", label: "Flow" },
  { id: "network", label: "Network" },
  { id: "files", label: "Files" },
  { id: "usage", label: "Usage" },
  { id: "processes", label: "Processes" },
  { id: "transcripts", label: "Transcripts" },
  { id: "logs", label: "Logs" },
  { id: "memory", label: "Memory" },
  { id: "brain", label: "Brain" },
] as const satisfies readonly MonitorPage[];

const MORE_PAGES = [
  { id: "skills", label: "Skills" },
  { id: "models", label: "Models" },
  { id: "compactions", label: "Compactions" },
  { id: "context", label: "Context" },
  { id: "subagents", label: "Subagents" },
  { id: "tasks", label: "Tasks" },
  { id: "history", label: "History" },
  { id: "limits", label: "Limits" },
  { id: "approvals", label: "Approvals" },
  { id: "clusters", label: "Clusters" },
  { id: "security", label: "Security" },
  { id: "crons", label: "Crons" },
  { id: "nemoclaw", label: "NemoClaw" },
  { id: "version-impact", label: "Version Impact" },
] as const satisfies readonly MonitorPage[];

const ALL_PAGES = [...PRIMARY_PAGES, ...MORE_PAGES] as const;

type MonitorPageId = (typeof ALL_PAGES)[number]["id"];
type MemoryView = "summary" | "all";
type SkillsView = "grid" | "browser";
type SecuritySeverity = "all" | "critical" | "high" | "medium" | "low";
type BrainFilter = "all" | "runtime" | "tool" | "llm" | "message";
type BrainTimelineUnit = "minute" | "hour" | "day" | "month";
type HistoryRange = "1h" | "6h" | "24h" | "7d" | "30d";
type LogLevel = "all" | "info" | "warn" | "error";
type TaskRunStatusFilter = "all" | "running" | "completed" | "failed";
type ProcessStatusFilter = "all" | "running" | "finished" | "failed";
type FileToolFilter = "all" | "read_file" | "write_file" | "list_files" | "search_files" | "delete_file";

const RESUMABLE_TASK_RUN_KINDS = new Set([
  "multi_agent_orchestration",
  "multi_agent_orchestration_stream",
]);

type BrainEvent = {
  id: string;
  kind: "runtime" | "message";
  runtimeId?: number;
  source: string;
  runtimeType?: string;
  operationLabel?: string;
  phase?: "outbound" | "inbound" | "state";
  fromEntity?: string;
  toEntity?: string;
  category: BrainFilter;
  label: string;
  detail: string;
  createdAt: string | null | undefined;
  tone: "neutral" | "success" | "warning" | "error";
  clientTurnId?: string | null;
  messageType?: string;
  messageContent?: string;
  projectName?: string;
  chatTitle?: string;
};

type BrainEventSection = {
  label: string;
  content: string;
  tone?: "neutral" | "accent" | "success" | "warning" | "error";
  format?: "text" | "json";
  variant?: "result" | "raw" | "meta";
};

type ClusterItem = {
  chatroomId: number;
  chatTitle: string;
  projectName: string;
  runtimeCount: number;
  llmCalls: number;
  toolCalls: number;
  tokenTotal: number;
  latestAt: string | null;
  agents: string[];
};

function resolveConfiguredContextWindow(config: ConfigResponse | null, modelId: string | undefined) {
  const normalizedModelId = (modelId || "").trim();
  if (!config) return DEFAULT_CONTEXT_WINDOW;

  const candidateProviders = [
    config.global_llm?.provider,
    ...Object.values(config.agents ?? {}).map((agent) => agent.provider),
  ];

  for (const provider of candidateProviders) {
    const models = provider?.models ?? [];
    const matchedModel = normalizedModelId
      ? models.find((model) => model.id === normalizedModelId)
      : undefined;
    const fallbackModel = matchedModel ?? models[0];
    const contextWindow = fallbackModel?.contextWindow;
    if (typeof contextWindow === "number" && Number.isFinite(contextWindow) && contextWindow > 0) {
      return contextWindow;
    }
  }

  return DEFAULT_CONTEXT_WINDOW;
}

type SkillRow = {
  name: string;
  agents: string[];
  projects: string[];
  detail: string;
  alwaysLoadedHint: string;
};

type AgentDirectoryEntry = AgentInfo & { projects: string[] };

type ModelRow = {
  name: string;
  calls: number;
  tokens: number;
  chats: number;
};

type SecurityEvent = {
  id: string;
  severity: Exclude<SecuritySeverity, "all">;
  title: string;
  detail: string;
  createdAt: string | null | undefined;
};

type ApprovalPreset = {
  key: string;
  name: string;
  description: string;
  tool: string;
  pattern: string;
  color: string;
};

const APPROVAL_PRESETS: ApprovalPreset[] = [
  {
    key: "rm_rf",
    name: "Block destructive deletes",
    description: "Stop rm -rf and similar destructive filesystem actions.",
    tool: "exec",
    pattern: "rm -rf | shred | unlink",
    color: "#ef4444",
  },
  {
    key: "force_push",
    name: "Block force pushes",
    description: "Require manual review before git push --force.",
    tool: "exec",
    pattern: "git push --force",
    color: "#f59e0b",
  },
  {
    key: "db_mutation",
    name: "Block database mutations",
    description: "Catch DROP TABLE, TRUNCATE and risky destructive SQL.",
    tool: "exec",
    pattern: "DROP TABLE | TRUNCATE | DELETE FROM",
    color: "#f97316",
  },
  {
    key: "network",
    name: "Review outbound network calls",
    description: "Pause curl, wget, fetch, requests and similar egress actions.",
    tool: "exec",
    pattern: "curl | wget | httpx | requests.get",
    color: "#0ea5e9",
  },
];

const APPROVAL_INTEGRATIONS = [
  { name: "Slack", status: "TODO", description: "Alert channel card only. Wiring comes later." },
  { name: "Email", status: "TODO", description: "Notification UX copied first, delivery not wired yet." },
  { name: "PagerDuty", status: "TODO", description: "Escalation target placeholder." },
  { name: "Telegram", status: "TODO", description: "Direct approval bot can be added after backend support." },
];

function pageExists(value: string | null | undefined): value is MonitorPageId {
  return Boolean(value && ALL_PAGES.some((page) => page.id === value));
}

function readInitialPage(): MonitorPageId {
  if (typeof window === "undefined") return "overview";
  const hash = window.location.hash.replace(/^#/, "");
  if (pageExists(hash)) return hash;
  const stored = window.localStorage.getItem("catown.monitor.page");
  if (pageExists(stored)) return stored;
  return "overview";
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function formatNumber(value: number | undefined) {
  return new Intl.NumberFormat("en-US").format(value ?? 0);
}

function formatCost(value: number | undefined) {
  return `$${(value ?? 0).toFixed(4)}`;
}

function formatPercent(value: number | undefined, digits = 0) {
  return `${(value ?? 0).toFixed(digits)}%`;
}

function formatDuration(value: number | undefined) {
  if (!value) return "--";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value)}ms`;
}

function formatBytes(value: number | undefined) {
  const bytes = Math.max(value ?? 0, 0);
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${bytes} B`;
}

function normalizeLogLevel(level: string) {
  const normalized = level.toLowerCase();
  if (normalized === "warning") return "warn";
  return normalized;
}

function logClientSource(entry: MonitorLogEntry) {
  const text = `${entry.message} ${entry.line}`;
  const match = text.match(/\bsource=([a-z0-9_-]{1,32})\b/i);
  return match?.[1]?.toLowerCase() ?? "unknown";
}

function formatTimeAgo(value: string | null | undefined) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const delta = Date.now() - date.getTime();
  const minutes = Math.round(delta / 60000);
  if (minutes <= 0) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function shortDate(value: string | null | undefined) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function preciseSystemTime(value: string | null | undefined) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const datePart = date.toLocaleDateString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const timePart = date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return `${datePart} ${timePart}.${String(date.getMilliseconds()).padStart(3, "0")}`;
}

function runtimeTone(type: string, success?: boolean | null) {
  if (type === "tool_call") return success === false ? "error" : "neutral";
  if (type.includes("rejected") || type.includes("error")) return "error";
  if (type.includes("approved") || type.includes("completed")) return "success";
  if (type.includes("blocked")) return "warning";
  return "neutral";
}

function runtimeLabel(type: string) {
  return type.replace(/_/g, " ");
}

function approvalStatusTone(status: string | null | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "pending") return "warning";
  if (["approved", "approve", "allow", "allow_no_timeout"].includes(normalized)) return "success";
  if (["rejected", "reject", "deny"].includes(normalized)) return "error";
  return runtimeTone(normalized);
}

function approvalFollowupTone(status: string | null | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "continued") return "success";
  if (normalized === "failed") return "error";
  if (normalized === "skipped") return "warning";
  return "neutral";
}

function taskRunStatusTone(status: string | null | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "completed") return "success";
  if (normalized === "failed") return "error";
  if (normalized === "running") return "warning";
  return "neutral";
}

function processStatusTone(process: MonitorProcessEntry | null | undefined) {
  if (!process) return "neutral";
  const normalized = (process.status || "").toLowerCase();
  if (normalized === "failed") return "error";
  if (process.is_active || !process.is_terminal || normalized === "running") return "warning";
  if (process.is_terminal || normalized === "completed" || normalized === "succeeded") return "success";
  return "neutral";
}

function ProcessStatusIcon({ process }: { process: MonitorProcessEntry }) {
  const isRunning = process.is_active || !process.is_terminal || (process.status || "").toLowerCase() === "running";
  const Icon = isRunning ? CircleDashed : CheckCircle2;
  return (
    <Icon
      className={`process-status-icon process-status-icon--${isRunning ? "running" : "finished"}`}
      aria-label={isRunning ? "running" : "finished"}
      size={18}
    />
  );
}

function fileActionTone(entry: MonitorFileEvent): "success" | "warning" | "error" | "neutral" {
  if (entry.success === false) return "error";
  if (entry.blocked) return "warning";
  if (entry.action === "write" || entry.action === "delete") return "warning";
  if (entry.success === true) return "success";
  return "neutral";
}

function fileActionLabel(action: string) {
  if (!action) return "access";
  return action.replace(/_/g, " ");
}

function taskRunEventTone(eventType: string | null | undefined) {
  const normalized = (eventType || "").toLowerCase();
  if (normalized.includes("failed") || normalized.includes("error")) return "error";
  if (normalized.includes("completed")) return "success";
  if (normalized.includes("tool") || normalized.includes("handoff")) return "warning";
  return "neutral";
}

function taskRunStepTone(step: MonitorTaskRunStep | null | undefined) {
  if (!step) return "neutral";
  if (step.step_kind === "tool") {
    if (step.blocked) return "warning";
    if (step.success === false) return "error";
    return "success";
  }
  if (step.step_kind === "llm") {
    return step.success === false ? "error" : "neutral";
  }
  return taskRunEventTone(step.event_type);
}

function titleCaseLabel(value: string | null | undefined) {
  if (!value) return "unknown";
  const normalized = value
    .replace(/^replay_tool_then_continue_turn$/, "resume_original_tool_call")
    .replace(/^resume_pipeline_stage_after_replay$/, "resume_pipeline_stage");
  return normalized
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function isFutureDate(value: string | null | undefined) {
  if (!value) return false;
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return false;
  return timestamp > Date.now();
}

function hasActiveRecoveryLease(taskRun: {
  status?: string | null;
  recovery_owner?: string | null;
  recovery_lease_expires_at?: string | null;
}) {
  return (taskRun.status || "").toLowerCase() === "running"
    && Boolean((taskRun.recovery_owner || "").trim())
    && isFutureDate(taskRun.recovery_lease_expires_at);
}

function compactOwnerLabel(value: string | null | undefined) {
  const text = (value || "").trim();
  if (!text) return "unknown";
  if (text.length <= 40) return text;
  return `${text.slice(0, 18)}...${text.slice(-10)}`;
}

function continuationStateSummary(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const state = value as {
    consumed?: boolean;
    next_action?: string | null;
    resume_strategy?: string | null;
    consumed_layers?: string[] | null;
    protocol_tail_message_count?: number | null;
    prior_round_summary_count?: number | null;
  };
  if (!state.consumed) return null;
  return [
    state.next_action ? titleCaseLabel(state.next_action) : null,
    state.resume_strategy ? `via ${state.resume_strategy}` : null,
    state.protocol_tail_message_count ? `${state.protocol_tail_message_count} tail messages` : null,
    state.prior_round_summary_count ? `${state.prior_round_summary_count} prior summaries` : null,
    Array.isArray(state.consumed_layers) && state.consumed_layers.length
      ? state.consumed_layers.join(", ")
      : null,
  ].filter(Boolean).join(" · ");
}

function continuationCursorSummary(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const cursor = value as {
    next_action?: string | null;
    resume_strategy?: string | null;
    tool_name?: string | null;
    turn?: number | null;
    ready_step_count?: number | null;
    running_step_count?: number | null;
    waiting_step_count?: number | null;
  };
  if (!cursor.next_action || cursor.next_action === "none") return null;
  return [
    titleCaseLabel(cursor.next_action),
    cursor.resume_strategy ? `via ${cursor.resume_strategy}` : null,
    cursor.tool_name ? `tool ${cursor.tool_name}` : null,
    cursor.turn !== null && cursor.turn !== undefined ? `turn ${cursor.turn}` : null,
    cursor.ready_step_count !== null && cursor.ready_step_count !== undefined ? `${cursor.ready_step_count} ready` : null,
    cursor.running_step_count !== null && cursor.running_step_count !== undefined ? `${cursor.running_step_count} running` : null,
    cursor.waiting_step_count !== null && cursor.waiting_step_count !== undefined ? `${cursor.waiting_step_count} waiting` : null,
  ].filter(Boolean).join(" · ");
}

function schedulerRuntimeSummary(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const runtime = value as Record<string, unknown>;
  const parts = [
    ["completed_step_count", "completed"],
    ["ready_step_count", "ready"],
    ["running_step_count", "running"],
    ["waiting_step_count", "waiting"],
    ["step_count", "total"],
  ].map(([key, label]) => {
    const raw = runtime[key];
    if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
    return `${raw} ${label}`;
  }).filter(Boolean) as string[];
  return parts.length ? parts.join(" · ") : null;
}

type RunScheduleStep = {
  stepId: string;
  position: number;
  requestedName: string;
  agentId: number | null;
  agentName: string;
  agentType: string;
  dispatchKind: string;
  waitForStepId: string | null;
  attachedToStepId: string | null;
  source: string;
  status: string;
  releasedByStepId: string | null;
  dispatchCount: number;
  completionCount: number;
};

type RunSchedulePlan = {
  mode: string;
  stepCount: number;
  blockingStepCount: number;
  sidecarStepCount: number;
  sidecarAgentTypes: string[];
  readyStepCount: number;
  waitingStepCount: number;
  runningStepCount: number;
  completedStepCount: number;
  steps: RunScheduleStep[];
};

type RunHandoffRelation = {
  id: string;
  fromAgent: string;
  toAgent: string;
  fromStepId: string | null;
  toStepId: string | null;
  attachedToStepId: string | null;
  dispatchKind: string;
  contentPreview: string;
  createdAt: string | null;
};

function asMonitorRecord(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function monitorStringField(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function monitorNumberField(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function compactMonitorJson(value: unknown, limit = 420) {
  if (value === null || value === undefined) return "";
  let text = "";
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
  }
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 3).trimEnd()}...`;
}

function parseRunScheduleStep(value: unknown): RunScheduleStep | null {
  const payload = asMonitorRecord(value);
  if (!payload) return null;

  const stepId = monitorStringField(payload.step_id);
  const agentName = monitorStringField(payload.agent_name);
  if (!stepId || !agentName) return null;

  return {
    stepId,
    position: monitorNumberField(payload.position) ?? 0,
    requestedName: monitorStringField(payload.requested_name) ?? agentName,
    agentId: monitorNumberField(payload.agent_id),
    agentName,
    agentType: monitorStringField(payload.agent_type) ?? agentName.toLowerCase(),
    dispatchKind: monitorStringField(payload.dispatch_kind) ?? "blocking",
    waitForStepId: monitorStringField(payload.wait_for_step_id),
    attachedToStepId: monitorStringField(payload.attached_to_step_id),
    source: monitorStringField(payload.source) ?? "runtime",
    status: monitorStringField(payload.status) ?? "planned",
    releasedByStepId: monitorStringField(payload.released_by_step_id),
    dispatchCount: monitorNumberField(payload.dispatch_count) ?? 0,
    completionCount: monitorNumberField(payload.completion_count) ?? 0,
  };
}

function extractLatestRunScheduleRuntime(detail: TaskRunDetail | null) {
  if (!detail) return null;
  const runtimeEvent = [...detail.events]
    .reverse()
    .find((event) => Boolean(asMonitorRecord(event.runtime_snapshot)));
  return asMonitorRecord(runtimeEvent?.runtime_snapshot);
}

function extractRunSchedulePlan(detail: TaskRunDetail | null): RunSchedulePlan | null {
  if (!detail) return null;
  const scheduleEvent = detail.events.find((event) => event.event_type === "scheduler_plan_created");
  if (!scheduleEvent) return null;

  const rawSteps = Array.isArray(scheduleEvent.schedule_steps) ? scheduleEvent.schedule_steps : [];
  const steps = rawSteps
    .map((step) => parseRunScheduleStep(step))
    .filter((step): step is RunScheduleStep => Boolean(step))
    .sort((left, right) => left.position - right.position);
  const runtimePayload = extractLatestRunScheduleRuntime(detail);
  const runtimeSteps = Array.isArray(runtimePayload?.steps) ? runtimePayload.steps : [];
  const runtimeStepMap = new Map(
    runtimeSteps
      .map((step) => parseRunScheduleStep(step))
      .filter((step): step is RunScheduleStep => Boolean(step))
      .map((step) => [step.stepId, step]),
  );
  const mergedSteps = steps.map((step) => {
    const runtimeStep = runtimeStepMap.get(step.stepId);
    return runtimeStep ? { ...step, ...runtimeStep } : step;
  });

  return {
    mode: monitorStringField(scheduleEvent.schedule_mode) ?? "linear_blocking_chain",
    stepCount: monitorNumberField(scheduleEvent.schedule_step_count) ?? mergedSteps.length,
    blockingStepCount:
      monitorNumberField(scheduleEvent.schedule_blocking_step_count) ??
      mergedSteps.filter((step) => step.dispatchKind === "blocking").length,
    sidecarStepCount:
      monitorNumberField(scheduleEvent.schedule_sidecar_step_count) ??
      mergedSteps.filter((step) => step.dispatchKind === "sidecar").length,
    sidecarAgentTypes: Array.isArray(scheduleEvent.schedule_sidecar_agent_types)
      ? scheduleEvent.schedule_sidecar_agent_types
          .map((value) => monitorStringField(value))
          .filter((value): value is string => Boolean(value))
      : [],
    readyStepCount:
      monitorNumberField(runtimePayload?.ready_step_count) ??
      mergedSteps.filter((step) => step.status === "ready").length,
    waitingStepCount:
      monitorNumberField(runtimePayload?.waiting_step_count) ??
      mergedSteps.filter((step) => step.status === "waiting").length,
    runningStepCount:
      monitorNumberField(runtimePayload?.running_step_count) ??
      mergedSteps.filter((step) => step.status === "running").length,
    completedStepCount:
      monitorNumberField(runtimePayload?.completed_step_count) ??
      mergedSteps.filter((step) => step.status === "completed").length,
    steps: mergedSteps,
  };
}

function extractRunHandoffs(detail: TaskRunDetail | null): RunHandoffRelation[] {
  if (!detail) return [];
  return detail.events
    .filter((event) => event.event_type === "handoff_created")
    .map((event) => {
      return {
        id: `${event.id}`,
        fromAgent: monitorStringField(event.from_agent) ?? event.agent_name ?? "agent",
        toAgent: monitorStringField(event.to_agent) ?? "agent",
        fromStepId: monitorStringField(event.from_step_id),
        toStepId: monitorStringField(event.to_step_id),
        attachedToStepId: monitorStringField(event.attached_to_step_id),
        dispatchKind: monitorStringField(event.dispatch_kind) ?? "blocking",
        contentPreview: monitorStringField(event.content_preview) ?? "",
        createdAt: monitorStringField(event.created_at),
      };
    });
}

function monitorPageLabel(pageId: MonitorPageId) {
  return ALL_PAGES.find((page) => page.id === pageId)?.label ?? pageId;
}

function mergeMonitorLogs(current: MonitorLogEntry[], incoming: MonitorLogEntry[]) {
  const merged = new Map<number, MonitorLogEntry>();
  current.forEach((entry) => {
    merged.set(entry.id, entry);
  });
  incoming.forEach((entry) => {
    merged.set(entry.id, entry);
  });
  return [...merged.values()]
    .sort((left, right) => right.id - left.id)
    .slice(0, 500);
}

function mergeMonitorNetwork(current: MonitorNetworkEvent[], incoming: MonitorNetworkEvent[]) {
  const merged = new Map<number, MonitorNetworkEvent>();
  current.forEach((entry) => {
    merged.set(entry.id, entry);
  });
  incoming.forEach((entry) => {
    merged.set(entry.id, entry);
  });
  return [...merged.values()]
    .sort((left, right) => right.id - left.id)
    .slice(0, MONITOR_NETWORK_RETAIN_LIMIT);
}

function historyRangeStart(range: HistoryRange) {
  const now = Date.now();
  if (range === "1h") return now - 60 * 60 * 1000;
  if (range === "6h") return now - 6 * 60 * 60 * 1000;
  if (range === "24h") return now - 24 * 60 * 60 * 1000;
  if (range === "7d") return now - 7 * 24 * 60 * 60 * 1000;
  return now - 30 * 24 * 60 * 60 * 1000;
}

function mergeMonitorTaskRunResponse(
  current: MonitorTaskRunsResponse | null,
  incoming: MonitorTaskRunSummary,
  range: HistoryRange,
  capturedAt?: string | null,
): MonitorTaskRunsResponse {
  const merged = new Map<number, MonitorTaskRunSummary>();
  (current?.entries ?? []).forEach((entry) => {
    merged.set(entry.id, entry);
  });

  const createdAtMs = incoming.created_at ? new Date(incoming.created_at).getTime() : 0;
  if (createdAtMs && createdAtMs >= historyRangeStart(range)) {
    merged.set(incoming.id, incoming);
  } else {
    merged.delete(incoming.id);
  }

  return {
    captured_at: capturedAt ?? new Date().toISOString(),
    range,
    entries: [...merged.values()].sort((left, right) => {
      const rightMs = right.created_at ? new Date(right.created_at).getTime() : 0;
      const leftMs = left.created_at ? new Date(left.created_at).getTime() : 0;
      return rightMs - leftMs || right.id - left.id;
    }),
  };
}

function mergeTaskRunDetailIntoMonitorSummary(
  current: MonitorTaskRunSummary,
  detail: TaskRunDetail,
): MonitorTaskRunSummary {
  const latestDetailEvent = detail.events[detail.events.length - 1];
  return {
    ...current,
    ...detail,
    chat_title: current.chat_title,
    project_name: current.project_name,
    latest_event_type: latestDetailEvent?.event_type ?? detail.latest_event_type ?? current.latest_event_type,
    event_count: detail.event_count ?? detail.events.length,
  };
}

function formatRawMonitorValue(value: unknown) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function httpVersion(entry: MonitorNetworkEvent) {
  const version = String(entry.metadata?.http_version || "1.1").trim();
  if (!version) return "HTTP/1.1";
  return version.toUpperCase().startsWith("HTTP/") ? version : `HTTP/${version}`;
}

function renderHttpHeaders(headers: Record<string, string> | undefined, host?: string) {
  const lines: string[] = [];
  const seenHost = new Set<string>();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === "host") {
      seenHost.add("host");
    }
    lines.push(`${key}: ${value}`);
  }
  if (!seenHost.has("host") && host) {
    lines.unshift(`Host: ${host}`);
  }
  return lines;
}

function httpHeaderValue(headers: Record<string, string> | undefined, name: string) {
  const target = name.toLowerCase();
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === target) return value;
  }
  return "";
}

function httpContentEncoding(headers: Record<string, string> | undefined) {
  const value = httpHeaderValue(headers, "content-encoding").trim().toLowerCase();
  if (!value || value === "identity") return "";
  return value;
}

function looksLikeBinaryMonitorText(value: string) {
  return /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(value) || value.includes("\uFFFD");
}

function formatEncodedMonitorBody(
  raw: string | undefined,
  headers: Record<string, string> | undefined,
  byteCount: number | undefined,
  scope: "request" | "response",
) {
  const text = raw || "";
  if (!text) return text;
  const encoding = httpContentEncoding(headers);
  if (!encoding) return text;
  const normalized = text.trimStart().toLowerCase();
  if (normalized.startsWith("[") && normalized.includes("-compressed")) {
    return text;
  }
  if (!looksLikeBinaryMonitorText(text)) {
    return text;
  }
  const label = scope === "request" ? "request body" : "response body";
  return `[${encoding}-compressed ${label} captured by an older monitor build; ${formatBytes(byteCount)}]`;
}

function buildHttpWireDump(entry: MonitorNetworkEvent) {
  const method = entry.method || "GET";
  const path = entry.path || "/";
  const version = httpVersion(entry);
  const statusCode = entry.status_code ?? 200;
  const frameType = String(entry.metadata?.frame_type || "").toLowerCase();
  const requestBody = formatEncodedMonitorBody(entry.raw_request, entry.request_headers, entry.request_bytes, "request");
  const responseBody = formatEncodedMonitorBody(entry.raw_response, entry.response_headers, entry.response_bytes, "response");
  const lines: string[] = [];

  if (entry.raw_request || frameType === "request") {
    lines.push(`${method} ${path} ${version}`);
    lines.push(...renderHttpHeaders(entry.request_headers, entry.host));
    lines.push("");
    if (requestBody) {
      lines.push(requestBody);
    }
    return lines.join("\n").trimEnd();
  }

  lines.push(`${version} ${statusCode}`);
  lines.push(...renderHttpHeaders(entry.response_headers));
  lines.push("");
  if (responseBody) {
    lines.push(responseBody);
  }
  return lines.join("\n").trimEnd();
}

function buildNetworkRawDump(entry: MonitorNetworkEvent) {
  if ((entry.protocol || "").toLowerCase().includes("http")) {
    return buildHttpWireDump(entry);
  }
  const requestDirection = entry.request_direction || `${entry.from_entity} -> ${entry.to_entity}`;
  const responseDirection = entry.response_direction || `${entry.to_entity} -> ${entry.from_entity}`;
  const lines = [
    `category: ${entry.category}`,
    `protocol: ${entry.protocol || "unknown"}`,
    `method: ${entry.method || "NET"}`,
    `url: ${entry.url || ""}`,
    `request-direction: ${requestDirection}`,
    `response-direction: ${responseDirection}`,
  ];
  if (entry.flow_id) {
    lines.push(`flow-id: ${entry.flow_id}`);
  }
  if (entry.flow_kind) {
    lines.push(`flow-kind: ${entry.flow_kind}`);
  }
  if (entry.flow_seq !== undefined && entry.flow_seq !== null) {
    lines.push(`flow-seq: ${entry.flow_seq}`);
  }

  if (entry.status_code !== undefined && entry.status_code !== null) {
    lines.push(`status: ${entry.status_code}`);
  }
  if (entry.content_type) {
    lines.push(`content-type: ${entry.content_type}`);
  }
  if (entry.request_headers && Object.keys(entry.request_headers).length > 0) {
    lines.push("", "[request headers]", formatRawMonitorValue(entry.request_headers));
  }
  if (entry.response_headers && Object.keys(entry.response_headers).length > 0) {
    lines.push("", "[response headers]", formatRawMonitorValue(entry.response_headers));
  }
  if (entry.raw_request) {
    lines.push("", "[raw request]", entry.raw_request);
  }
  if (entry.raw_response) {
    lines.push("", "[raw response]", entry.raw_response);
  }
  if (!entry.raw_request && !entry.raw_response) {
    if (entry.preview) {
      lines.push("", "[preview]", entry.preview);
    }
    if (entry.error) {
      lines.push("", "[error]", entry.error);
    }
  } else if (entry.error) {
    lines.push("", "[error]", entry.error);
  }

  return lines.join("\n").trim();
}

function NetworkRawDump({ entry }: { entry: MonitorNetworkEvent }) {
  const [expanded, setExpanded] = useState(false);
  const dump = useMemo(() => (expanded ? buildNetworkRawDump(entry) : ""), [entry, expanded]);

  return (
    <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="small-note" style={{ cursor: "pointer", marginTop: 8 }}>
        Request/response details
      </summary>
      <pre className="monitor-pre" style={{ marginTop: 8, padding: "8px 10px", fontSize: 12, lineHeight: 1.45 }}>
        {dump}
      </pre>
    </details>
  );
}

function LazyRawPayload({
  label,
  value,
  className,
  style,
}: {
  label: string;
  value: unknown;
  className?: string;
  style?: CSSProperties;
}) {
  const [expanded, setExpanded] = useState(false);
  const payload = useMemo(() => (expanded ? formatRawMonitorValue(value) : ""), [expanded, value]);

  return (
    <details className={className} style={style} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>{label}</summary>
      {expanded ? <pre>{payload}</pre> : null}
    </details>
  );
}

function hashFlowColor(flowId: string) {
  let hash = 0;
  for (let index = 0; index < flowId.length; index += 1) {
    hash = (hash * 31 + flowId.charCodeAt(index)) >>> 0;
  }
  return `hsl(${hash % 360} 70% 58%)`;
}

function isMonitorPageNetwork(entry: MonitorNetworkEvent) {
  const path = (entry.path || "").toLowerCase();
  const url = (entry.url || "").toLowerCase();
  const clientSource = (entry.client_source || "").toLowerCase();
  const fromEntity = (entry.from_entity || "").toLowerCase();
  return (
    clientSource === "monitor" ||
    fromEntity.includes("frontend (monitor)") ||
    path.startsWith("/api/monitor") ||
    path === "/monitor" ||
    path === "/monitor/" ||
    path.endsWith("/monitor.html") ||
    url.includes("/monitor") ||
    url.includes("/api/monitor")
  );
}

function isFrontendBackendHeartbeat(entry: MonitorNetworkEvent) {
  const protocol = (entry.protocol || "").toLowerCase();
  const rawResponse = (entry.raw_response || "").trim();
  const preview = (entry.preview || "").toLowerCase();
  const path = (entry.path || "").toLowerCase();

  if (!protocol.includes("http")) return false;

  if (rawResponse && /^(: ping\s*)+$/m.test(rawResponse.replace(/\r/g, ""))) {
    return true;
  }

  if (
    (path.endsWith("/stream") || preview.includes(" ping")) &&
    rawResponse &&
    !rawResponse.includes("\"type\": \"content\"") &&
    !rawResponse.includes("\"type\":\"content\"") &&
    !rawResponse.includes("\"type\": \"done\"") &&
    !rawResponse.includes("\"type\":\"done\"") &&
    (
      rawResponse.includes(": ping") ||
      rawResponse.includes("\"type\": \"llm_wait\"") ||
      rawResponse.includes("\"type\":\"llm_wait\"") ||
      rawResponse.includes("\"type\": \"tool_wait\"") ||
      rawResponse.includes("\"type\":\"tool_wait\"")
    )
  ) {
    return true;
  }

  return false;
}

function isFrontendMetaRequest(entry: MonitorNetworkEvent) {
  const path = (entry.path || "").toLowerCase();
  const url = (entry.url || "").toLowerCase();
  return path === "/api/frontend-meta" || url.includes("/api/frontend-meta");
}

function isFrontendBackendTraffic(entry: MonitorNetworkEvent) {
  return (entry.category || "").toLowerCase() === "frontend_backend";
}

function isLegacyBackendLlmAppEvent(entry: MonitorNetworkEvent) {
  if ((entry.category || "").toLowerCase() !== "backend_llm") return false;
  const flowKind = (entry.flow_kind || "").toLowerCase();
  const frameType = String(entry.metadata?.frame_type || "").toLowerCase();
  if (flowKind === "llm_http") return false;
  return (
    flowKind === "llm_stream" ||
    frameType === "request_sent" ||
    frameType === "first_chunk" ||
    frameType === "first_content" ||
    frameType === "content" ||
    frameType === "tool_call_delta" ||
    frameType === "tool_call_ready" ||
    frameType === "done"
  );
}

function isRequestFrame(entry: MonitorNetworkEvent) {
  const frameType = String(entry.metadata?.frame_type || "").toLowerCase();
  return frameType === "request" || Boolean(entry.raw_request);
}

function activeDirection(entry: MonitorNetworkEvent) {
  return isRequestFrame(entry)
    ? entry.request_direction || `${entry.from_entity} -> ${entry.to_entity}`
    : entry.response_direction || `${entry.to_entity} -> ${entry.from_entity}`;
}

function parseDirection(direction: string) {
  const [fromRaw, ...rest] = direction.split("->");
  const from = (fromRaw || "").trim();
  const to = rest.join("->").trim();
  return { from, to };
}

type NetworkEntity = "frontend" | "backend" | "llm" | "web";

function normalizeNetworkEntity(value: string): NetworkEntity | null {
  const text = value.toLowerCase();
  if (!text) return null;
  if (text.includes("frontend")) return "frontend";
  if (text.includes("backend")) return "backend";
  if (text.includes("llm")) return "llm";
  if (
    text.includes("openai") ||
    text.includes("deepseek") ||
    text.includes("qwen") ||
    text.includes("gpt") ||
    text.includes("moonshot") ||
    text.includes("claude")
  ) {
    return "llm";
  }
  if (
    text.includes("http://") ||
    text.includes("https://") ||
    text.includes("www") ||
    text.includes(".com") ||
    text.includes(".cn") ||
    text.includes(".net") ||
    text.includes(".org")
  ) {
    return "web";
  }
  return null;
}

function getNetworkEntities(entry: MonitorNetworkEvent): { from: NetworkEntity; to: NetworkEntity } {
  const category = (entry.category || "").toLowerCase();
  const fromEntity = (entry.from_entity || "").toLowerCase();
  const toEntity = (entry.to_entity || "").toLowerCase();
  const text = `${category} ${fromEntity} ${toEntity} ${(entry.host || "").toLowerCase()} ${(entry.url || "").toLowerCase()}`;

  if (
    category === "backend_llm" ||
    text.includes("openai") ||
    text.includes("deepseek") ||
    text.includes("qwen") ||
    text.includes("gpt") ||
    text.includes("moonshot") ||
    text.includes("claude")
  ) {
    return { from: "backend", to: "llm" };
  }

  if (category === "frontend_backend") {
    return { from: "frontend", to: "backend" };
  }

  if (category === "frontend_other") {
    return { from: "frontend", to: "web" };
  }

  if (category === "backend_other") {
    return { from: "backend", to: "web" };
  }

  return {
    from: normalizeNetworkEntity(fromEntity) || (text.includes("frontend") ? "frontend" : "backend"),
    to: normalizeNetworkEntity(toEntity) || (text.includes("llm") ? "llm" : "web"),
  };
}

function getNetworkEntityVisual(entity: NetworkEntity) {
  switch (entity) {
    case "frontend":
      return { Icon: Monitor, color: "#0ea5e9", label: "Frontend" };
    case "backend":
      return { Icon: Server, color: "#22c55e", label: "Backend" };
    case "llm":
      return { Icon: BrainCircuit, color: "#8b5cf6", label: "LLM" };
    case "web":
      return { Icon: Globe, color: "#f59e0b", label: "Web" };
    default:
      return { Icon: Boxes, color: "#94a3b8", label: "Network" };
  }
}

function getDirectionVisual(label: string) {
  const entity = normalizeNetworkEntity(label) || "web";
  return getNetworkEntityVisual(entity);
}

function monitorCreatedAtMs(value: string | null | undefined) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function compareMonitorItemsNewest<T extends { id: number; created_at: string | null | undefined }>(left: T, right: T) {
  return monitorCreatedAtMs(right.created_at) - monitorCreatedAtMs(left.created_at) || right.id - left.id;
}

function mergeMonitorMessages(
  current: MonitorOverview["recent_messages"],
  incoming: MonitorOverview["recent_messages"],
) {
  const merged = new Map<number, MonitorOverview["recent_messages"][number]>();
  current.forEach((item) => {
    merged.set(item.id, item);
  });
  incoming.forEach((item) => {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  });
  return [...merged.values()]
    .sort(compareMonitorItemsNewest)
    .slice(0, MONITOR_MESSAGE_LIMIT);
}

function mergeMonitorRuntime(
  current: MonitorOverview["recent_runtime"],
  incoming: MonitorOverview["recent_runtime"],
) {
  const mergeKey = (item: MonitorOverview["recent_runtime"][number]) => {
    const toolCallId = (item.tool_call_id || "").trim();
    if (item.type === "tool_call" && toolCallId) {
      return [
        "tool_call",
        item.chatroom_id,
        item.client_turn_id || "",
        toolCallId,
      ].join(":");
    }
    return `runtime:${item.id}`;
  };
  const merged = new Map<string, MonitorOverview["recent_runtime"][number]>();
  current.forEach((item) => {
    merged.set(mergeKey(item), item);
  });
  incoming.forEach((item) => {
    const key = mergeKey(item);
    merged.set(key, { ...merged.get(key), ...item });
  });
  return [...merged.values()]
    .sort(compareMonitorItemsNewest)
    .slice(0, MONITOR_RUNTIME_LIMIT);
}

function normalizeMonitorOverviewRuntime(overview: MonitorOverview): MonitorOverview {
  return {
    ...overview,
    recent_runtime: mergeMonitorRuntime([], overview.recent_runtime),
  };
}

function compareMonitorApprovalQueueItemsNewest(
  left: MonitorApprovalQueueEntry,
  right: MonitorApprovalQueueEntry,
) {
  return monitorCreatedAtMs(right.created_at) - monitorCreatedAtMs(left.created_at) || right.id - left.id;
}

function normalizeMonitorApprovalQueueEntries(entries: MonitorApprovalQueueEntry[]) {
  const pending = entries.filter((item) => (item.status || "").toLowerCase() === "pending").length;
  const approved = entries.filter((item) => (item.status || "").toLowerCase() === "approved").length;
  const rejected = entries.filter((item) => (item.status || "").toLowerCase() === "rejected").length;
  return {
    pending,
    approved,
    rejected,
    total: entries.length,
  };
}

function mergeMonitorApprovalQueue(
  current: MonitorApprovalQueueResponse | null,
  incoming: MonitorApprovalQueueResponse,
): MonitorApprovalQueueResponse {
  const merged = new Map<number, MonitorApprovalQueueEntry>();
  (current?.entries ?? []).forEach((item) => {
    merged.set(item.id, item);
  });
  incoming.entries.forEach((item) => {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  });
  const entries = [...merged.values()].sort(compareMonitorApprovalQueueItemsNewest).slice(0, 300);
  return {
    captured_at: incoming.captured_at,
    status: incoming.status,
    counts: normalizeMonitorApprovalQueueEntries(entries),
    entries,
  };
}

function mergeMonitorApprovalQueueItem(
  current: MonitorApprovalQueueResponse | null,
  incoming: MonitorApprovalQueueEntry,
): MonitorApprovalQueueResponse | null {
  if (!current) return current;
  return mergeMonitorApprovalQueue(current, {
    captured_at: new Date().toISOString(),
    status: "all",
    counts: normalizeMonitorApprovalQueueEntries([incoming, ...current.entries.filter((item) => item.id !== incoming.id)]),
    entries: [incoming],
  });
}

function compareMonitorApprovalAuditNewest(
  left: MonitorApprovalAuditEntry,
  right: MonitorApprovalAuditEntry,
) {
  return monitorCreatedAtMs(right.created_at) - monitorCreatedAtMs(left.created_at) || right.id - left.id;
}

function normalizeMonitorApprovalAuditEntries(entries: MonitorApprovalAuditEntry[]) {
  return {
    all: entries.length,
    approve: entries.filter((item) => (item.decision || "").toLowerCase() === "approve").length,
    reject: entries.filter((item) => (item.decision || "").toLowerCase() === "reject").length,
    allow: entries.filter((item) => (item.decision || "").toLowerCase() === "allow").length,
    deny: entries.filter((item) => (item.decision || "").toLowerCase() === "deny").length,
    remembered: entries.filter((item) => (item.event_kind || "").toLowerCase() === "authorization_rule_saved").length,
    automatic: entries.filter((item) => (item.event_kind || "").toLowerCase() === "authorization_rule_matched").length,
  };
}

function mergeMonitorApprovalAudit(
  current: MonitorApprovalAuditResponse | null,
  incoming: MonitorApprovalAuditResponse,
): MonitorApprovalAuditResponse {
  const merged = new Map<number, MonitorApprovalAuditEntry>();
  (current?.entries ?? []).forEach((item) => {
    merged.set(item.id, item);
  });
  incoming.entries.forEach((item) => {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  });
  const entries = [...merged.values()].sort(compareMonitorApprovalAuditNewest).slice(0, 500);
  return {
    ...incoming,
    counts: { ...incoming.counts, ...normalizeMonitorApprovalAuditEntries(entries) },
    entries,
  };
}

function enrichApprovalQueueItemFromTaskRun(
  item: ApprovalQueueItem,
  detail: TaskRunDetail | null,
  taskRunSummary?: MonitorTaskRunSummary | null,
): MonitorApprovalQueueEntry {
  const payload = (item.request_payload && typeof item.request_payload === "object" ? item.request_payload : {}) as Record<string, unknown>;
  const resolution = (item.resolution_payload && typeof item.resolution_payload === "object" ? item.resolution_payload : {}) as Record<string, unknown>;
  const latestEventType = detail?.events?.length ? detail.events[detail.events.length - 1]?.event_type ?? null : taskRunSummary?.latest_event_type ?? null;
  return {
    ...item,
    chat_title: taskRunSummary?.chat_title ?? null,
    project_name: taskRunSummary?.project_name ?? null,
    task_run_title: detail?.title ?? taskRunSummary?.title ?? null,
    task_run_status: detail?.status ?? taskRunSummary?.status ?? null,
    run_kind: detail?.run_kind ?? taskRunSummary?.run_kind ?? null,
    latest_event_type: latestEventType,
    request_preview:
      item.summary ||
      (monitorStringField(payload.blocked_reason) ?? null) ||
      (typeof item.title === "string" ? item.title : null),
    resolution_preview:
      item.resolution_note ||
      (monitorStringField(resolution.replay_result_preview) ?? null) ||
      (monitorStringField(resolution.followup_error) ?? null) ||
      null,
    resume_supported: Boolean(payload.resume_supported),
    action_taken: monitorStringField(resolution.action_taken),
    replay_status: monitorStringField(resolution.replay_status),
    replay_success: typeof resolution.replay_success === "boolean" ? resolution.replay_success : null,
    followup_attempted: typeof resolution.followup_attempted === "boolean" ? resolution.followup_attempted : null,
    followup_status: monitorStringField(resolution.followup_status),
    followup_reason: monitorStringField(resolution.followup_reason),
    followup_error: monitorStringField(resolution.followup_error),
    followup_message_id:
      typeof resolution.followup_message_id === "number"
        ? resolution.followup_message_id
        : null,
  };
}

function mergeMonitorApprovalQueueFromTaskRun(
  current: MonitorApprovalQueueResponse | null,
  detail: TaskRunDetail,
  taskRunSummary?: MonitorTaskRunSummary | null,
): MonitorApprovalQueueResponse | null {
  if (!current) return current;
  const taskRunItems = Array.isArray(detail.approval_queue_items) ? detail.approval_queue_items : [];
  if (taskRunItems.length === 0) return current;

  const incomingEntries = taskRunItems.map((item) => enrichApprovalQueueItemFromTaskRun(item, detail, taskRunSummary));
  return mergeMonitorApprovalQueue(current, {
    captured_at: new Date().toISOString(),
    status: "all",
    counts: normalizeMonitorApprovalQueueEntries([
      ...incomingEntries,
      ...current.entries.filter((entry) => !incomingEntries.some((incoming) => incoming.id === entry.id)),
    ]),
    entries: incomingEntries,
  });
}

function maxIsoTimestamp(current: string | null | undefined, candidate: string | null | undefined) {
  if (!current) return candidate ?? current ?? null;
  if (!candidate) return current;
  return new Date(candidate).getTime() > new Date(current).getTime() ? candidate : current;
}

function updateMonitorAgentUsage(
  current: MonitorOverview["usage_window"]["by_agent"],
  item: MonitorOverview["recent_runtime"][number],
  pricing: MonitorOverview["usage_window"]["pricing"],
) {
  const agentName = item.agent || item.from_entity || "system";
  const next = [...current];
  const index = next.findIndex((entry) => entry.agent_name === agentName);
  const base =
    index >= 0
      ? { ...next[index] }
      : {
          agent_name: agentName,
          llm_calls: 0,
          tool_calls: 0,
          errors: 0,
          token_input: 0,
          token_output: 0,
          token_total: 0,
          estimated_cost_usd: 0,
        };

  if (item.type === "llm_call") {
    base.llm_calls += 1;
    base.token_input += item.tokens_in ?? 0;
    base.token_output += item.tokens_out ?? 0;
  } else if (item.type === "tool_call") {
    base.tool_calls += 1;
    if (item.success === false) {
      base.errors += 1;
    }
  } else if (item.type.includes("rejected") || item.type.includes("error")) {
    base.errors += 1;
  }

  base.token_total = base.token_input + base.token_output;
  base.estimated_cost_usd = Number(
    (
      (base.token_input / 1000) * pricing.input_per_1k +
      (base.token_output / 1000) * pricing.output_per_1k
    ).toFixed(4),
  );

  if (index >= 0) {
    next[index] = base;
  } else {
    next.push(base);
  }

  return next.sort(
    (left, right) =>
      right.llm_calls + right.tool_calls - (left.llm_calls + left.tool_calls) ||
      right.token_total - left.token_total ||
      left.agent_name.localeCompare(right.agent_name),
  );
}

function updateMonitorTopTools(
  current: MonitorOverview["usage_window"]["top_tools"],
  item: MonitorOverview["recent_runtime"][number],
) {
  if (item.type !== "tool_call") return current;
  const toolName = item.tool_name || "tool";
  const next = [...current];
  const index = next.findIndex((entry) => entry.tool_name === toolName);
  const base =
    index >= 0
      ? { ...next[index] }
      : {
          tool_name: toolName,
          call_count: 0,
          failure_count: 0,
          avg_duration_ms: 0,
        };

  const totalDuration = base.avg_duration_ms * base.call_count + (item.duration_ms ?? 0);
  base.call_count += 1;
  if (item.success === false) {
    base.failure_count += 1;
  }
  base.avg_duration_ms = Number((totalDuration / Math.max(base.call_count, 1)).toFixed(1));

  if (index >= 0) {
    next[index] = base;
  } else {
    next.push(base);
  }

  return next
    .sort((left, right) => right.call_count - left.call_count || left.tool_name.localeCompare(right.tool_name))
    .slice(0, 8);
}

function applyMonitorMessageUpdate(
  current: MonitorOverview | null,
  item: MonitorOverview["recent_messages"][number],
) {
  if (!current) return current;
  const exists = current.recent_messages.some((entry) => entry.id === item.id);
  return {
    ...current,
    system: {
      ...current.system,
      stats: {
        ...current.system.stats,
        messages: exists ? current.system.stats.messages : current.system.stats.messages + 1,
      },
      last_message_at: maxIsoTimestamp(current.system.last_message_at, item.created_at),
    },
    recent_messages: mergeMonitorMessages(current.recent_messages, [item]),
  };
}

function applyMonitorRuntimeUpdate(
  current: MonitorOverview | null,
  item: MonitorOverview["recent_runtime"][number],
) {
  if (!current) return current;
  const itemToolCallId = (item.tool_call_id || "").trim();
  const exists = current.recent_runtime.some((entry) => {
    if (entry.id === item.id) return true;
    return (
      item.type === "tool_call" &&
      entry.type === "tool_call" &&
      itemToolCallId !== "" &&
      entry.chatroom_id === item.chatroom_id &&
      (entry.client_turn_id || "") === (item.client_turn_id || "") &&
      (entry.tool_call_id || "").trim() === itemToolCallId
    );
  });
  if (exists) {
    return {
      ...current,
      recent_runtime: mergeMonitorRuntime(current.recent_runtime, [item]),
      system: {
        ...current.system,
        last_message_at: maxIsoTimestamp(current.system.last_message_at, item.created_at),
      },
    };
  }

  const pricing = current.usage_window.pricing;
  const nextInputTokens = current.usage_window.input_tokens + (item.tokens_in ?? 0);
  const nextOutputTokens = current.usage_window.output_tokens + (item.tokens_out ?? 0);
  const nextTotalTokens = nextInputTokens + nextOutputTokens;
  const nextEstimatedCost = Number(
    (
      (nextInputTokens / 1000) * pricing.input_per_1k +
      (nextOutputTokens / 1000) * pricing.output_per_1k
    ).toFixed(4),
  );

  return {
    ...current,
    system: {
      ...current.system,
      stats: {
        ...current.system.stats,
        runtime_cards: current.system.stats.runtime_cards + 1,
      },
      last_message_at: maxIsoTimestamp(current.system.last_message_at, item.created_at),
    },
    usage_window: {
      ...current.usage_window,
      runtime_cards_considered: Math.min(
        current.usage_window.runtime_cards_considered + 1,
        MONITOR_USAGE_WINDOW_LIMIT,
      ),
      llm_calls: current.usage_window.llm_calls + (item.type === "llm_call" ? 1 : 0),
      tool_calls: current.usage_window.tool_calls + (item.type === "tool_call" ? 1 : 0),
      tool_errors:
        current.usage_window.tool_errors + (item.type === "tool_call" && item.success === false ? 1 : 0),
      input_tokens: nextInputTokens,
      output_tokens: nextOutputTokens,
      total_tokens: nextTotalTokens,
      estimated_cost_usd: nextEstimatedCost,
      by_agent: updateMonitorAgentUsage(current.usage_window.by_agent, item, pricing),
      top_tools: updateMonitorTopTools(current.usage_window.top_tools, item),
    },
    recent_runtime: mergeMonitorRuntime(current.recent_runtime, [item]),
  };
}

function formatUnknownDetail(value: unknown) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "";
    try {
      const parsed = JSON.parse(trimmed);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return value;
    }
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function compactMonitorText(value: unknown, limit = 180) {
  const text = formatUnknownDetail(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function runtimePrimaryPreview(item: MonitorOverview["recent_runtime"][number], limit = 180) {
  return compactMonitorText(item.preview || item.response_preview || item.arguments_preview || item.prompt_preview, limit);
}

function runtimeRequestPreview(item: MonitorOverview["recent_runtime"][number], limit = 180) {
  return compactMonitorText(item.arguments_preview || item.prompt_preview || item.preview, limit);
}

function runtimeResponsePreview(item: MonitorOverview["recent_runtime"][number], limit = 180) {
  return compactMonitorText(item.response_preview || item.preview, limit);
}

function approvalQueuePreview(item: MonitorApprovalQueueEntry, limit = 180) {
  return compactMonitorText(item.request_preview || item.resolution_preview || item.summary, limit);
}

function approvalAuditPreview(item: MonitorApprovalAuditEntry, limit = 180) {
  return compactMonitorText(item.preview || item.command_preview || item.reason, limit);
}

function taskRunPrimarySummary(run: MonitorTaskRunSummary | TaskRunDetail | null | undefined) {
  if (!run) return null;
  const summary = "summary" in run && typeof run.summary === "string" && run.summary.trim() ? run.summary.trim() : "";
  if (summary) return summary;
  const userRequest = "user_request" in run && typeof run.user_request === "string" && run.user_request.trim() ? run.user_request.trim() : "";
  return userRequest || null;
}

function taskRunContinuationSummary(run: MonitorTaskRunSummary | TaskRunDetail | null | undefined) {
  if (!run) return null;
  return (
    run.continuation_state_summary ||
    run.checkpoint_snapshot?.continuation_state_summary ||
    continuationStateSummary(run.continuation_state ?? run.checkpoint_snapshot?.continuation_state)
  );
}

function taskRunCursorSummary(run: MonitorTaskRunSummary | TaskRunDetail | null | undefined) {
  if (!run) return null;
  return run.continuation_cursor_summary || continuationCursorSummary(run.continuation_cursor ?? run.checkpoint_snapshot?.continuation_cursor);
}

function taskRunSchedulerSummary(run: MonitorTaskRunSummary | TaskRunDetail | null | undefined) {
  if (!run) return null;
  return run.scheduler_runtime_summary || schedulerRuntimeSummary(run.latest_scheduler_runtime ?? run.checkpoint_snapshot?.latest_scheduler_runtime);
}

function latestAgentTurnPreview(detail: TaskRunDetail | null | undefined) {
  const latestTurn = detail?.checkpoint_snapshot?.latest_agent_turn;
  if (!latestTurn?.response_preview) return null;
  return `${latestTurn.agent_name || "agent"} · ${latestTurn.response_preview}`;
}

function formatCompactionScopeUsage(
  usage: Record<string, { candidate_count?: number | null; selected_count?: number | null; candidate_tokens?: number | null; selected_tokens?: number | null }> | null | undefined,
) {
  if (!usage) return "";
  const parts = Object.entries(usage).map(([scope, report]) => {
    if (!report || typeof report !== "object") return "";
    return `${scope} ${report.selected_count ?? "?"}/${report.candidate_count ?? "?"} fragments, ${report.selected_tokens ?? "?"}/${report.candidate_tokens ?? "?"} tokens`;
  }).filter(Boolean);
  return parts.join(" / ");
}

function monitorCompactionPreview(item: MonitorCompactionItem) {
  return item.detail_summary
    || [
      `Candidates ${formatNumber(item.candidate_count ?? 0)} -> selected ${formatNumber(item.selected_count ?? 0)}`,
      item.max_fragments ? `max fragments ${item.max_fragments}` : "",
      item.max_tokens ? `max tokens ${formatNumber(item.max_tokens)}` : "",
      item.budget_summary || "",
      item.scope_usage_summary || formatCompactionScopeUsage(item.scope_usage),
    ].filter(Boolean).join(" | ");
}

function compactionComponentLabel(name: string) {
  const labels: Record<string, string> = {
    system: "System prompt",
    developer: "Developer context",
    user_context: "User context",
    history: "Recent history",
    current_input: "Current input",
  };
  return labels[name] ?? name.replace(/_/g, " ");
}

function compactionComponentEntries(item: MonitorCompactionItem) {
  const components = item.prompt_components;
  if (!components) return [];
  const order = ["system", "developer", "user_context", "history", "current_input"];
  return Object.entries(components).sort(([left], [right]) => {
    const leftIndex = order.indexOf(left);
    const rightIndex = order.indexOf(right);
    if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
    if (leftIndex === -1) return 1;
    if (rightIndex === -1) return -1;
    return leftIndex - rightIndex;
  });
}

function readCompactionReasonNumber(reason: Record<string, unknown>, key: string) {
  const value = reason[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readCompactionReasonText(reason: Record<string, unknown>, key: string) {
  const value = reason[key];
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function readCompactionReasonSources(reason: Record<string, unknown>) {
  const sources = reason.sources;
  if (!Array.isArray(sources)) return [];
  return sources.map((source) => String(source)).filter(Boolean);
}

function describeCompactionReason(reason: Record<string, unknown>) {
  const kind = readCompactionReasonText(reason, "kind") || "reason";
  const limit = readCompactionReasonNumber(reason, "limit");
  const candidate = readCompactionReasonNumber(reason, "candidate");
  const selected = readCompactionReasonNumber(reason, "selected");
  const count = readCompactionReasonNumber(reason, "count");
  const role = readCompactionReasonText(reason, "role");
  const scope = readCompactionReasonText(reason, "scope");
  const subject = role || scope;

  if (kind === "max_fragments") {
    return {
      label: "Fragment cap",
      detail: `${formatNumber(candidate)} candidate fragments exceeded the ${formatNumber(limit)} fragment limit; ${formatNumber(selected)} were selected.`,
    };
  }
  if (kind === "max_tokens") {
    return {
      label: "Token budget",
      detail: `${formatNumber(candidate)} candidate tokens exceeded the ${formatNumber(limit)} token cap; ${formatNumber(selected)} were selected.`,
    };
  }
  if (kind === "role_tokens") {
    return {
      label: `${subject || "Role"} budget`,
      detail: `${formatNumber(candidate)} candidate tokens for ${subject || "this role"} exceeded the ${formatNumber(limit)} token cap; ${formatNumber(selected)} were selected.`,
    };
  }
  if (kind === "scope_tokens") {
    return {
      label: `${subject || "Scope"} budget`,
      detail: `${formatNumber(candidate)} candidate tokens in ${subject || "this scope"} exceeded the ${formatNumber(limit)} token cap; ${formatNumber(selected)} were selected.`,
    };
  }
  if (kind === "truncated") {
    return {
      label: "Truncated fragments",
      detail: `${formatNumber(count)} selected fragments were shortened to fit the active budget.`,
    };
  }
  if (kind === "dropped") {
    return {
      label: "Dropped fragments",
      detail: `${formatNumber(count)} candidate fragments were left out after ranking and budget checks.`,
    };
  }
  return {
    label: kind.replace(/_/g, " "),
    detail: compactMonitorJson(reason, 240),
  };
}

function CompactionEventDetail({ item }: { item: MonitorCompactionItem }) {
  const componentEntries = compactionComponentEntries(item);
  const fragmentEntries = item.prompt_fragments ?? [];
  const visibleFragments = fragmentEntries.slice(0, 18);
  const reasons = item.reasons ?? [];
  const promptTokens = item.prompt_total?.tokens ?? 0;
  const selectionPercent = item.candidate_tokens
    ? Math.round(((item.selected_tokens ?? 0) / Math.max(item.candidate_tokens, 1)) * 100)
    : undefined;
  const usageBand = item.usage_band;
  const usageBandLabel = usageBand?.band ? usageBand.band.toUpperCase() : "--";
  const usageRatio = typeof usageBand?.ratio === "number" ? usageBand.ratio * 100 : undefined;

  return (
    <div className="compaction-detail">
      <div className="compaction-detail__context">
        <span>{item.chat_title || "Unknown chat"}</span>
        {item.project_name ? <span>{item.project_name}</span> : null}
        {item.task_run_title ? <span>{item.task_run_title}</span> : null}
      </div>

      <div className="compaction-detail__summary-grid">
        <div className="compaction-detail__metric">
          <span>Prompt total</span>
          <strong>{formatNumber(promptTokens)} tok</strong>
          <small>{formatBytes(item.prompt_total?.bytes)}</small>
        </div>
        <div className="compaction-detail__metric">
          <span>Selection</span>
          <strong>{formatNumber(item.selected_tokens)} / {formatNumber(item.candidate_tokens)}</strong>
          <small>{selectionPercent !== undefined ? `${selectionPercent}% kept` : "tokens"}</small>
        </div>
        <div className="compaction-detail__metric">
          <span>Fragments</span>
          <strong>{formatNumber(item.selected_count)} / {formatNumber(item.candidate_count)}</strong>
          <small>{formatNumber(item.dropped_count)} dropped, {formatNumber(item.truncated_count)} truncated</small>
        </div>
        <div className="compaction-detail__metric">
          <span>Limits</span>
          <strong>{item.max_tokens ? `${formatNumber(item.max_tokens)} tok` : "--"}</strong>
          <small>{item.max_fragments ? `${formatNumber(item.max_fragments)} fragments` : "no fragment cap"}</small>
        </div>
        <div className="compaction-detail__metric">
          <span>Usage band</span>
          <strong>{usageBandLabel}</strong>
          <small>
            {usageRatio !== undefined ? `${formatPercent(usageRatio, 1)} of input window` : "not captured"}
          </small>
        </div>
        <div className="compaction-detail__metric">
          <span>Model window</span>
          <strong>{formatNumber(item.input_window || item.context_window)}</strong>
          <small>{item.reserved_completion_tokens ? `${formatNumber(item.reserved_completion_tokens)} reserved` : "input/context tokens"}</small>
        </div>
      </div>

      <div className="compaction-detail__section">
        <div className="compaction-detail__section-head">
          <strong>Reasons</strong>
          <span>{reasons.length ? `${formatNumber(reasons.length)} trigger${reasons.length === 1 ? "" : "s"}` : "legacy summary"}</span>
        </div>
        {reasons.length ? (
          <div className="compaction-reason-list">
            {reasons.map((reason, index) => {
              const described = describeCompactionReason(reason);
              const sources = readCompactionReasonSources(reason);
              return (
                <div key={`${item.id}-reason-${index}`} className="compaction-reason">
                  <div className="compaction-reason__kind">{described.label}</div>
                  <div className="compaction-reason__text">{described.detail}</div>
                  {sources.length ? (
                    <div className="compaction-source-list">
                      {sources.slice(0, 5).map((source) => <span key={`${item.id}-${index}-${source}`}>{source}</span>)}
                      {sources.length > 5 ? <span>+{formatNumber(sources.length - 5)} more</span> : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="muted-block">{item.reason_summary || monitorCompactionPreview(item)}</div>
        )}
      </div>

      {componentEntries.length ? (
        <div className="compaction-detail__section">
          <div className="compaction-detail__section-head">
            <strong>Prompt Modules</strong>
            <span>estimated tokens and UTF-8 bytes</span>
          </div>
          <div className="usage-table usage-table--compact compaction-table">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Tokens</th>
                  <th>Bytes</th>
                  <th>Share</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {componentEntries.map(([name, size]) => (
                  <tr key={`${item.id}-${name}`}>
                    <td>{compactionComponentLabel(name)}</td>
                    <td>{formatNumber(size.tokens)}</td>
                    <td>{formatBytes(size.bytes)}</td>
                    <td>{promptTokens ? formatPercent(((size.tokens ?? 0) / promptTokens) * 100) : "--"}</td>
                    <td>{formatNumber(size.fragment_count ?? size.message_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {visibleFragments.length ? (
        <div className="compaction-detail__section">
          <div className="compaction-detail__section-head">
            <strong>Selected Fragments</strong>
            <span>{formatNumber(visibleFragments.length)} shown{fragmentEntries.length > visibleFragments.length ? ` of ${formatNumber(fragmentEntries.length)}` : ""}</span>
          </div>
          <div className="usage-table usage-table--compact compaction-table">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Role</th>
                  <th>Scope</th>
                  <th>Tokens</th>
                  <th>Bytes</th>
                </tr>
              </thead>
              <tbody>
                {visibleFragments.map((fragment, index) => (
                  <tr key={`${item.id}-fragment-${index}`}>
                    <td>{fragment.source || "--"}</td>
                    <td>{fragment.role || "--"}</td>
                    <td>{fragment.scope || "--"}</td>
                    <td>{formatNumber(fragment.tokens)}</td>
                    <td>{formatBytes(fragment.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function checkpointCompactionPreview(detail: TaskRunDetail | null | undefined) {
  const latestCompaction = detail?.checkpoint_snapshot?.latest_compaction;
  if (!latestCompaction?.event_id) return "No compaction event recorded.";
  return latestCompaction.detail_summary
    || latestCompaction.summary_text
    || [
      `Dropped ${latestCompaction.dropped_count ?? 0}`,
      `Truncated ${latestCompaction.truncated_count ?? 0}`,
      latestCompaction.max_tokens ? `Budget ${latestCompaction.max_tokens} tokens` : "",
      latestCompaction.budget_summary || "",
      latestCompaction.scope_usage_summary || "",
      latestCompaction.max_tokens_by_scope
        ? `Scope budgets ${Object.entries(latestCompaction.max_tokens_by_scope).map(([scope, value]) => `${scope} ${value}`).join(" / ")}`
        : "",
      formatCompactionScopeUsage(latestCompaction.scope_usage),
    ].filter(Boolean).join(" | ");
}

function taskRunEventDetailPreview(event: TaskRunEvent) {
  return event.detail_summary || event.continuation_state_summary;
}

function normalizeEntity(value: unknown, fallback: string) {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function buildCommunicationLabel(fromEntity: string, toEntity?: string | null) {
  return toEntity ? `${fromEntity} -> ${toEntity}` : fromEntity;
}

function brainOperationLabel(event: BrainEvent) {
  if (event.operationLabel && event.operationLabel.trim()) return event.operationLabel.trim();
  if (event.kind === "message") {
    return event.messageType && event.messageType !== "text" ? event.messageType : "msg";
  }
  if (event.runtimeType === "llm_call") return "llm";
  if (event.runtimeType === "tool_call") return "tool";
  if (event.runtimeType === "agent_error") return "error";
  return event.runtimeType ? event.runtimeType.replace(/_/g, " ") : "runtime";
}

function brainSummaryTarget(event: BrainEvent) {
  if (event.toEntity && event.toEntity.trim()) return event.toEntity.trim();
  if (event.phase === "state") return "state";
  if (event.kind === "message") return "assistant";
  return "runtime";
}

function brainEntityKind(event: BrainEvent, entity: string, position: "from" | "to") {
  const normalized = entity.trim().toLowerCase();
  if (normalized === "user") return "user";
  if (normalized === "llm") return "llm";
  if (normalized === "boss") return "boss";
  if (normalized === "state" || normalized === "runtime" || normalized === "system") return "system";
  if (event.runtimeType === "tool_call") {
    const isToolEntity =
      (position === "to" && event.phase === "outbound") ||
      (position === "from" && event.phase === "inbound");
    if (isToolEntity) return "tool";
  }
  return "agent";
}

function brainEntityIconLabel(kind: ReturnType<typeof brainEntityKind>) {
  switch (kind) {
    case "user":
      return "User";
    case "llm":
      return "LLM";
    case "tool":
      return "Tool";
    case "boss":
      return "Boss";
    case "system":
      return "System";
    default:
      return "Agent";
  }
}

function brainEntityIcon(kind: ReturnType<typeof brainEntityKind>): LucideIcon {
  switch (kind) {
    case "user":
      return UserRound;
    case "llm":
      return BrainCircuit;
    case "tool":
      return Wrench;
    case "boss":
      return Crown;
    case "system":
      return Boxes;
    default:
      return Bot;
  }
}

function hashTurnId(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function colorForTurnId(turnId: string | null | undefined) {
  if (!turnId) return null;
  const hue = hashTurnId(turnId) % 360;
  return `hsl(${hue} 68% 46%)`;
}

function turnCardStyle(turnId: string | null | undefined): CSSProperties | undefined {
  const color = colorForTurnId(turnId);
  if (!color) return undefined;
  return { ["--brain-turn-color" as "--brain-turn-color"]: color };
}

function shortTurnToken(turnId: string | null | undefined) {
  if (!turnId) return "";
  const normalized = turnId.replace(/^turn-/, "");
  return normalized.length > 10 ? normalized.slice(-10) : normalized;
}

function markdownCodeFence(content: string, language = "") {
  const normalized = content.replace(/\n+$/g, "");
  return `\`\`\`${language}\n${normalized}\n\`\`\``;
}

function buildBrainEventSections(event: BrainEvent, detail: MonitorRuntimeDetail | null): BrainEventSection[] {
  const routeLabel = buildCommunicationLabel(event.fromEntity || event.source, event.toEntity || undefined);

  if (event.kind === "message") {
    const sections: BrainEventSection[] = [];
    if (event.messageContent) {
      sections.push({
        label: event.messageType === "text" ? `Message · ${routeLabel}` : `Message · ${event.messageType || "text"} · ${routeLabel}`,
        content: event.messageContent,
        format: "text",
        tone: "success",
        variant: "result",
      });
    }
    sections.push({
      label: "Raw Message Envelope",
      content: formatUnknownDetail({
        from: event.fromEntity || event.source,
        to: event.toEntity || null,
        source: event.source,
        category: event.category,
        message_type: event.messageType || "text",
        project_name: event.projectName || "Standalone",
        chat_title: event.chatTitle || "",
        created_at: event.createdAt || null,
        client_turn_id: event.clientTurnId || null,
      }),
      tone: "neutral",
      format: "json",
      variant: "raw",
    });
    return sections;
  }

  if (!detail) return [];

  if (Array.isArray(detail.detail_sections) && detail.detail_sections.length > 0) {
    const projectedSections = detail.detail_sections
      .filter((section) => section.phase === "all" || section.phase === event.phase)
      .map((section) => ({
        label: section.label,
        content: section.content,
        tone: section.tone,
        format: section.format,
        variant: section.variant,
      }));
    if (projectedSections.length > 0) {
      return projectedSections;
    }
  }

  const card = detail.card ?? {};
  const sections: BrainEventSection[] = [];
  const exchangeMeta = formatUnknownDetail({
    from: event.fromEntity || event.source,
    to: event.toEntity || null,
    type: typeof card.type === "string" ? card.type : event.runtimeType || "runtime",
    model: card.model ?? null,
    tool: card.tool ?? null,
    turn: card.turn ?? null,
    tokens_in: card.tokens_in ?? null,
    tokens_out: card.tokens_out ?? null,
    duration_ms: card.duration_ms ?? null,
    success: card.success ?? null,
    client_turn_id: event.clientTurnId || (typeof card.client_turn_id === "string" ? card.client_turn_id : null),
  });
  if (exchangeMeta) {
    sections.push({
      label: `Exchange Meta · ${routeLabel}`,
      content: exchangeMeta,
      tone: "neutral",
      format: "json",
      variant: "meta",
    });
  }

  const rawCard = formatUnknownDetail(card);
  if (rawCard) {
    sections.push({
      label: `Raw Event Payload · ${routeLabel}`,
      content: rawCard,
      tone: "neutral",
      format: "json",
      variant: "raw",
    });
  }
  return sections;
}

function renderMonitorMarkdown(content: string, className: string) {
  return (
    <div className={className}>
      <ReactMarkdown
        className="message-markdown"
        remarkPlugins={[remarkGfm]}
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

function SectionTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <>
      <div className="section-title">{title}</div>
      {subtitle ? <div className="section-subtitle">{subtitle}</div> : null}
    </>
  );
}

function EmptyCard({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="placeholder-card">
      <div className="section-title">TODO</div>
      <div style={{ fontWeight: 800, marginBottom: 8 }}>{title}</div>
      <div className="small-note">{detail}</div>
    </div>
  );
}

function MetricBar({ value, max = 100 }: { value: number; max?: number }) {
  const width = max > 0 ? clamp((value / max) * 100, 0, 100) : 0;
  return (
    <div className="metric-bar">
      <span style={{ width: `${width}%` }} />
    </div>
  );
}

function BarChart({
  items,
  className = "",
  barWidth,
  labelEvery = 1,
  autoLabels = false,
}: {
  items: Array<{ label: string; value: number; accent?: string }>;
  className?: string;
  barWidth?: number;
  labelEvery?: number;
  autoLabels?: boolean;
}) {
  const chartRef = useRef<HTMLDivElement | null>(null);
  const [chartWidth, setChartWidth] = useState(0);
  const max = Math.max(1, ...items.map((item) => item.value));
  const effectiveLabelEvery = autoLabels && chartWidth > 0
    ? Math.max(1, Math.ceil((items.length * 24) / chartWidth))
    : labelEvery;

  useEffect(() => {
    const element = chartRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(([entry]) => {
      setChartWidth(entry.contentRect.width);
    });
    observer.observe(element);
    setChartWidth(element.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={chartRef}
      className={`bar-chart ${className}`.trim()}
      style={barWidth ? { gridTemplateColumns: `repeat(${items.length}, ${barWidth}px)` } : undefined}
    >
      {items.map((item, index) => {
        const height = clamp((item.value / max) * 100, 6, 100);
        const showLabel = effectiveLabelEvery <= 1 || index % effectiveLabelEvery === 0 || index === items.length - 1;
        return (
          <div key={`${item.label}-${index}`} className="bar-column">
            <div className="bar-column__value">{formatNumber(item.value)}</div>
            <div className="bar-column__plot">
              <div
                className="bar-column__bar"
                style={{
                  height: `${height}%`,
                  background: item.accent ?? "linear-gradient(180deg, #71b6ff, #0f6fff)",
                }}
              />
            </div>
            <div className="bar-column__label">{showLabel ? item.label : ""}</div>
          </div>
        );
      })}
    </div>
  );
}

type FlowCapabilityKey = "subagents" | "exec" | "browser" | "search" | "memory" | "approval" | "toolbox";

type FlowCapabilitySummary = {
  key: FlowCapabilityKey;
  calls: number;
  errors: number;
  durationTotal: number;
  durationCount: number;
  lastAt: number;
  configuredAgents: Set<string>;
  toolCounts: Map<string, number>;
};

type FlowExternalCapabilitySummary = {
  key: FlowCapabilityKey;
  requests: number;
  errors: number;
  bytes: number;
  durationTotal: number;
  durationCount: number;
  lastAt: number;
};

const FLOW_CAPABILITY_PRESETS: Record<
  FlowCapabilityKey,
  { nodeId: string; title: string; subtitle: string; kind: FlowTopologyNode["kind"]; order: number }
> = {
  subagents: {
    nodeId: "flow-capability-subagents",
    title: "Subagent Bus",
    subtitle: "Parallel workers and handoffs",
    kind: "collaboration",
    order: 1,
  },
  exec: {
    nodeId: "flow-capability-exec",
    title: "Code + Shell",
    subtitle: "Sandboxed code and workspace commands",
    kind: "tool",
    order: 2,
  },
  browser: {
    nodeId: "flow-capability-browser",
    title: "Browser Session",
    subtitle: "Open, click and page capture",
    kind: "web",
    order: 3,
  },
  search: {
    nodeId: "flow-capability-search",
    title: "Search + Fetch",
    subtitle: "Search APIs and remote lookups",
    kind: "tool",
    order: 4,
  },
  memory: {
    nodeId: "flow-capability-memory",
    title: "Memory Store",
    subtitle: "Recall, summaries and compaction",
    kind: "memory",
    order: 5,
  },
  approval: {
    nodeId: "flow-capability-approval",
    title: "Approval Queue",
    subtitle: "Human checkpoints and guardrails",
    kind: "approval",
    order: 6,
  },
  toolbox: {
    nodeId: "flow-capability-toolbox",
    title: "Toolbox",
    subtitle: "Other attached tools",
    kind: "tool",
    order: 7,
  },
};

function incrementCounter(map: Map<string, number>, key: string, amount = 1) {
  map.set(key, (map.get(key) ?? 0) + amount);
}

function sortedCounterKeys(map: Map<string, number>, limit: number) {
  return [...map.entries()]
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .slice(0, limit)
    .map(([value]) => value);
}

function flowHasRecentActivity(lastAt: number, windowMs = 2 * 60 * 1000) {
  return lastAt > 0 && Date.now() - lastAt <= windowMs;
}

function flowStatusFromActivity(activityCount: number, errorCount: number, lastAt: number): FlowTopologyStatus {
  if (errorCount > 0) {
    return errorCount >= Math.max(2, Math.ceil(activityCount / 2)) ? "error" : "warning";
  }
  if (flowHasRecentActivity(lastAt)) return "active";
  return activityCount > 0 ? "idle" : "idle";
}

function flowStatusFromFlag(active: boolean, warning = false): FlowTopologyStatus {
  if (warning) return "warning";
  return active ? "active" : "idle";
}

function flowCapabilityKeyForTool(value: string) {
  const text = value.toLowerCase().trim();
  if (!text) return null;
  if (
    text.includes("spawn_agent") ||
    text.includes("wait_agent") ||
    text.includes("send_input") ||
    text.includes("resume_agent") ||
    text.includes("close_agent")
  ) {
    return "subagents" as const;
  }
  if (
    text.includes("exec") ||
    text.includes("command") ||
    text.includes("stdin") ||
    text.includes("shell") ||
    text.includes("patch")
  ) {
    return "exec" as const;
  }
  if (
    text.includes("browser") ||
    text.includes("web.") ||
    text === "open" ||
    text === "click" ||
    text.includes("screenshot") ||
    text.includes("image_query")
  ) {
    return "browser" as const;
  }
  if (
    text.includes("search") ||
    text.includes("find") ||
    text.includes("finance") ||
    text.includes("weather") ||
    text.includes("sports") ||
    text.includes("time")
  ) {
    return "search" as const;
  }
  if (text.includes("memory") || text.includes("recall") || text.includes("context") || text.includes("vector")) {
    return "memory" as const;
  }
  if (text.includes("approval") || text.includes("approve") || text.includes("reject")) {
    return "approval" as const;
  }
  return "toolbox" as const;
}

function flowCapabilityKeyForNetworkEntry(entry: MonitorNetworkEvent) {
  const metadata = entry.metadata ?? {};
  const explicitCapability = typeof metadata["tool_capability"] === "string" ? metadata["tool_capability"].trim().toLowerCase() : "";
  if (explicitCapability === "search") return "search" as const;
  if (explicitCapability === "browser") return "browser" as const;
  if (explicitCapability === "exec") return "exec" as const;
  if (explicitCapability === "memory") return "memory" as const;
  if (explicitCapability === "approval") return "approval" as const;
  if (explicitCapability === "subagents") return "subagents" as const;
  if (explicitCapability === "toolbox") return "toolbox" as const;

  const toolName = typeof metadata["tool_name"] === "string" ? metadata["tool_name"] : "";
  if (toolName.trim()) {
    return flowCapabilityKeyForTool(toolName);
  }
  return null;
}

function ensureFlowCapability(map: Map<FlowCapabilityKey, FlowCapabilitySummary>, key: FlowCapabilityKey) {
  const existing = map.get(key);
  if (existing) return existing;
  const created: FlowCapabilitySummary = {
    key,
    calls: 0,
    errors: 0,
    durationTotal: 0,
    durationCount: 0,
    lastAt: 0,
    configuredAgents: new Set<string>(),
    toolCounts: new Map<string, number>(),
  };
  map.set(key, created);
  return created;
}

function averageDuration(durationTotal: number, durationCount: number) {
  if (durationCount <= 0) return "--";
  return formatDuration(durationTotal / durationCount);
}

function formatLastActive(lastAt: number) {
  return lastAt > 0 ? formatTimeAgo(new Date(lastAt).toISOString()) : "--";
}

function flowNodeWidthBounds(kind: FlowTopologyNode["kind"], compact: boolean) {
  if (compact) {
    return {
      min: {
        entry: 156,
        client: 170,
        gateway: 176,
        runtime: 196,
        agent: 184,
        llm: 180,
        tool: 176,
        memory: 180,
        web: 180,
        approval: 176,
        collaboration: 184,
      }[kind],
      max: {
        entry: 232,
        client: 248,
        gateway: 256,
        runtime: 312,
        agent: 280,
        llm: 272,
        tool: 264,
        memory: 272,
        web: 272,
        approval: 256,
        collaboration: 280,
      }[kind],
    };
  }

  return {
    min: {
      entry: 168,
      client: 182,
      gateway: 188,
      runtime: 216,
      agent: 198,
      llm: 194,
      tool: 188,
      memory: 194,
      web: 194,
      approval: 188,
      collaboration: 198,
    }[kind],
    max: {
      entry: 276,
      client: 292,
      gateway: 304,
      runtime: 368,
      agent: 336,
      llm: 318,
      tool: 296,
      memory: 316,
      web: 316,
      approval: 300,
      collaboration: 336,
    }[kind],
  };
}

function estimateFlowNodeLayout(node: FlowTopologyNode, compact: boolean) {
  const metrics = node.metrics ?? [];
  const chips = node.chips ?? [];
  const headerWidth = 82 + node.title.length * (compact ? 7.2 : 8.2) + (node.badge?.length ?? 0) * (compact ? 6 : 6.8);
  const subtitleWidth = node.subtitle ? 112 + Math.min(node.subtitle.length * (compact ? 1.1 : 1.4), compact ? 28 : 42) : 0;
  const metricWidths = metrics
    .map((metric) => 28 + metric.label.length * 3.2 + metric.value.length * 6.4)
    .sort((left, right) => right - left);
  const metricRowWidth = metricWidths.length
    ? metricWidths
        .slice(0, compact ? 2 : 3)
        .reduce((total, metricWidth) => total + metricWidth, 0) + Math.max(0, Math.min(metricWidths.length, compact ? 2 : 3) - 1) * 8
    : 0;
  const chipWidths = chips
    .map((chip) => 18 + chip.length * (compact ? 5.2 : 6))
    .sort((left, right) => right - left);
  const chipRowWidth = chipWidths.length
    ? chipWidths
        .slice(0, compact ? 2 : 3)
        .reduce((total, chipWidth) => total + chipWidth, 0) + Math.max(0, Math.min(chipWidths.length, compact ? 2 : 3) - 1) * 6
    : 0;
  const previewWidth = node.preview ? 132 + Math.min(node.preview.length * (compact ? 0.32 : 0.46), compact ? 32 : 84) : 0;
  const kindBounds = flowNodeWidthBounds(node.kind, compact);
  const kindMinWidth = node.minWidthOverride ?? kindBounds.min;
  const kindMaxWidth = node.maxWidthOverride ?? kindBounds.max;
  const preferredWidth = clamp(
    Math.round(Math.max(headerWidth, subtitleWidth, metricRowWidth, chipRowWidth, previewWidth, kindMinWidth)),
    kindMinWidth,
    kindMaxWidth,
  );
  const contentWidthBudget = preferredWidth - (compact ? 24 : 28);
  const singleRowMetrics = metrics.length > 0 && metrics.length <= 3 && metricRowWidth <= contentWidthBudget;

  return {
    preferredWidth,
    singleRowMetrics,
  };
}

function buildFlowTopologyGraph({
  overview,
  networkEntries,
  agentDirectory,
  modelPrimary,
  connectionState,
  pendingApprovalCount,
  approvalQueueTotal,
  compact,
}: {
  overview: MonitorOverview | null;
  networkEntries: MonitorNetworkEvent[];
  agentDirectory: AgentDirectoryEntry[];
  modelPrimary: string;
  connectionState: "connected" | "disconnected";
  pendingApprovalCount: number;
  approvalQueueTotal: number;
  compact: boolean;
}): FlowTopologyGraph {
  const runtimeEntries = overview?.recent_runtime ?? [];
  const recentMessages = overview?.recent_messages ?? [];
  const filteredNetworkEntries = networkEntries.filter((entry) => !isMonitorPageNetwork(entry) && !isFrontendBackendHeartbeat(entry) && !isFrontendMetaRequest(entry));

  let frontendRequestCount = 0;
  let frontendRequestErrors = 0;
  let frontendBytes = 0;
  let frontendDurationTotal = 0;
  let frontendDurationCount = 0;
  let frontendLastAt = 0;

  let llmNetworkCount = 0;
  let llmNetworkErrors = 0;
  let llmNetworkDurationTotal = 0;
  let llmNetworkDurationCount = 0;
  let llmNetworkLastAt = 0;

  let externalRequestCount = 0;
  let externalRequestErrors = 0;
  let externalBytes = 0;
  let externalDurationTotal = 0;
  let externalDurationCount = 0;
  let externalLastAt = 0;
  const externalHosts = new Map<string, number>();
  const externalCapabilitySummaries = new Map<FlowCapabilityKey, FlowExternalCapabilitySummary>();

  filteredNetworkEntries.forEach((entry) => {
    const lastAt = monitorCreatedAtMs(entry.created_at);
    const failed = entry.success === false || ((entry.status_code ?? 0) >= 400 && (entry.status_code ?? 0) < 600);
    const totalBytes = entry.total_bytes || entry.request_bytes + entry.response_bytes;
    const entities = getNetworkEntities(entry);

    if (isFrontendBackendTraffic(entry)) {
      frontendRequestCount += 1;
      if (failed) frontendRequestErrors += 1;
      frontendBytes += totalBytes;
      if (entry.duration_ms > 0) {
        frontendDurationTotal += entry.duration_ms;
        frontendDurationCount += 1;
      }
      frontendLastAt = Math.max(frontendLastAt, lastAt);
    }

    if (entities.from === "llm" || entities.to === "llm") {
      llmNetworkCount += 1;
      if (failed) llmNetworkErrors += 1;
      if (entry.duration_ms > 0) {
        llmNetworkDurationTotal += entry.duration_ms;
        llmNetworkDurationCount += 1;
      }
      llmNetworkLastAt = Math.max(llmNetworkLastAt, lastAt);
    }

    if (entities.from === "web" || entities.to === "web") {
      externalRequestCount += 1;
      if (failed) externalRequestErrors += 1;
      externalBytes += totalBytes;
      if (entry.duration_ms > 0) {
        externalDurationTotal += entry.duration_ms;
        externalDurationCount += 1;
      }
      externalLastAt = Math.max(externalLastAt, lastAt);
      const host = (entry.host || "").trim() || compactMonitorText(entry.url, 48);
      if (host) incrementCounter(externalHosts, host);
      const capabilityKey = flowCapabilityKeyForNetworkEntry(entry);
      if (capabilityKey) {
        const summary = externalCapabilitySummaries.get(capabilityKey) ?? {
          key: capabilityKey,
          requests: 0,
          errors: 0,
          bytes: 0,
          durationTotal: 0,
          durationCount: 0,
          lastAt: 0,
        };
        summary.requests += 1;
        if (failed) summary.errors += 1;
        summary.bytes += totalBytes;
        if (entry.duration_ms > 0) {
          summary.durationTotal += entry.duration_ms;
          summary.durationCount += 1;
        }
        summary.lastAt = Math.max(summary.lastAt, lastAt);
        externalCapabilitySummaries.set(capabilityKey, summary);
      }
    }
  });

  const capabilitySummaries = new Map<FlowCapabilityKey, FlowCapabilitySummary>();
  agentDirectory.forEach((agent) => {
    (agent.tools ?? []).forEach((tool) => {
      const key = flowCapabilityKeyForTool(tool);
      if (!key) return;
      const summary = ensureFlowCapability(capabilitySummaries, key);
      summary.configuredAgents.add(agent.name);
      incrementCounter(summary.toolCounts, tool, 0);
    });
  });

  let runtimeErrorCount = 0;
  let runtimeLastAt = 0;
  let latestRuntimeAt = 0;
  let latestRuntimePreview = "";

  let llmCallCount = 0;
  let llmErrorCount = 0;
  let llmDurationTotal = 0;
  let llmDurationCount = 0;
  let llmLastAt = 0;
  let llmTokenTotal = 0;

  const activeAgents = new Map<string, { calls: number; llm: number; tool: number; errors: number; lastAt: number }>();
  const activeModels = new Map<string, number>();

  runtimeEntries.forEach((item) => {
    const lastAt = monitorCreatedAtMs(item.created_at);
    runtimeLastAt = Math.max(runtimeLastAt, lastAt);
    if (lastAt >= latestRuntimeAt) {
      latestRuntimeAt = lastAt;
      latestRuntimePreview = runtimePrimaryPreview(item, compact ? 88 : 160) || compactMonitorText(item.title, compact ? 88 : 160);
    }

    const agentName = normalizeEntity(item.agent || item.from_entity, "runtime");
    const agentSummary = activeAgents.get(agentName) ?? { calls: 0, llm: 0, tool: 0, errors: 0, lastAt: 0 };
    agentSummary.calls += 1;
    agentSummary.lastAt = Math.max(agentSummary.lastAt, lastAt);

    const failed = item.success === false || item.type === "agent_error";
    if (failed) {
      runtimeErrorCount += 1;
      agentSummary.errors += 1;
    }

    if (item.type === "llm_call") {
      llmCallCount += 1;
      agentSummary.llm += 1;
      llmTokenTotal += runtimeTokenTotal(item);
      llmLastAt = Math.max(llmLastAt, lastAt);
      if (item.duration_ms) {
        llmDurationTotal += item.duration_ms;
        llmDurationCount += 1;
      }
      if (item.success === false) {
        llmErrorCount += 1;
      }
      if (item.model) incrementCounter(activeModels, item.model);
    }

    if (item.type === "tool_call") {
      agentSummary.tool += 1;
      const key = flowCapabilityKeyForTool(item.tool_name || item.to_entity || "");
      const summary = ensureFlowCapability(capabilitySummaries, key ?? "toolbox");
      summary.calls += 1;
      summary.lastAt = Math.max(summary.lastAt, lastAt);
      summary.configuredAgents.add(agentName);
      if (item.duration_ms) {
        summary.durationTotal += item.duration_ms;
        summary.durationCount += 1;
      }
      if (item.success === false) {
        summary.errors += 1;
      }
      incrementCounter(summary.toolCounts, item.tool_name || item.to_entity || "tool");
    }

    activeAgents.set(agentName, agentSummary);
  });

  (overview?.usage_window.top_tools ?? []).forEach((tool) => {
    const key = flowCapabilityKeyForTool(tool.tool_name);
    const summary = ensureFlowCapability(capabilitySummaries, key ?? "toolbox");
    summary.calls = Math.max(summary.calls, tool.call_count);
    summary.errors = Math.max(summary.errors, tool.failure_count);
    if (tool.avg_duration_ms > 0) {
      const syntheticCount = Math.max(tool.call_count, 1);
      summary.durationTotal = Math.max(summary.durationTotal, tool.avg_duration_ms * syntheticCount);
      summary.durationCount = Math.max(summary.durationCount, syntheticCount);
    }
    incrementCounter(summary.toolCounts, tool.tool_name, tool.call_count);
  });

  const collaboration = overview?.system.collaboration;
  const activeCollaborators = collaboration?.active_collaborators ?? 0;
  const pendingTasks = collaboration?.pending_tasks ?? 0;
  if (agentDirectory.length > 1 || activeCollaborators > 0 || pendingTasks > 0) {
    const summary = ensureFlowCapability(capabilitySummaries, "subagents");
    summary.calls = Math.max(summary.calls, activeCollaborators + pendingTasks);
    summary.lastAt = Math.max(summary.lastAt, runtimeLastAt);
    agentDirectory.forEach((agent) => summary.configuredAgents.add(agent.name));
  }

  const contextCompactions = overview?.system.stats.context_compactions ?? overview?.recent_compactions?.length ?? 0;
  if (contextCompactions > 0) {
    const summary = ensureFlowCapability(capabilitySummaries, "memory");
    summary.calls = Math.max(summary.calls, contextCompactions);
    summary.lastAt = Math.max(summary.lastAt, runtimeLastAt);
  }

  if (approvalQueueTotal > 0 || pendingApprovalCount > 0) {
    const summary = ensureFlowCapability(capabilitySummaries, "approval");
    summary.calls = Math.max(summary.calls, approvalQueueTotal);
    summary.errors = Math.max(summary.errors, pendingApprovalCount > 0 ? 1 : 0);
    summary.lastAt = Math.max(summary.lastAt, monitorCreatedAtMs(overview?.captured_at));
  }

  const llmCallsWindow = overview?.usage_window.llm_calls ?? llmCallCount;
  const toolCallsWindow = overview?.usage_window.tool_calls ?? (overview?.usage_window.top_tools.reduce((total, item) => total + item.call_count, 0) ?? 0);
  const totalTokensWindow = overview?.usage_window.total_tokens ?? llmTokenTotal;
  const activeAgentNames = [...activeAgents.entries()]
    .sort((left, right) => right[1].calls - left[1].calls || right[1].lastAt - left[1].lastAt)
    .map(([name]) => name);

  const llmStatus = flowStatusFromActivity(llmCallsWindow || llmCallCount, llmErrorCount + llmNetworkErrors, Math.max(llmLastAt, llmNetworkLastAt));
  const runtimeStatus = flowStatusFromActivity(runtimeEntries.length, runtimeErrorCount, runtimeLastAt);
  const frontendStatus = connectionState === "disconnected"
    ? "warning"
    : flowStatusFromActivity(frontendRequestCount + recentMessages.length, frontendRequestErrors, Math.max(frontendLastAt, monitorCreatedAtMs(overview?.system.last_message_at)));
  const gatewayStatus = flowStatusFromActivity(frontendRequestCount + runtimeEntries.length, frontendRequestErrors + runtimeErrorCount, Math.max(frontendLastAt, runtimeLastAt));
  const userStatus = flowStatusFromFlag(recentMessages.length > 0 || Boolean(overview?.system.stats.visible_chats));
  const externalStatus = flowStatusFromActivity(externalRequestCount, externalRequestErrors, externalLastAt);

  const nodes: FlowTopologyNode[] = [
    {
      id: "flow-user",
      lane: 0,
      order: 0,
      kind: "entry",
      title: "User / Chats",
      subtitle: "Incoming prompts and chat surfaces",
      badge: `${formatNumber(overview?.system.stats.visible_chats)} chats`,
      status: userStatus,
      metrics: [
        { label: "Msgs", value: formatNumber(recentMessages.length) },
        { label: "Rooms", value: formatNumber(overview?.system.stats.chatrooms) },
      ],
      preview: `Last message ${formatTimeAgo(overview?.system.last_message_at || recentMessages[0]?.created_at)}`,
    },
    {
      id: "flow-frontend",
      lane: 1,
      order: 0,
      kind: "client",
      title: "Web Client",
      subtitle: connectionState === "connected" ? "Realtime client transport online" : "Realtime transport needs attention",
      badge: connectionState === "connected" ? "WS live" : "offline",
      status: frontendStatus,
      metrics: [
        { label: "Msgs", value: formatNumber(recentMessages.length) },
        { label: "HTTP", value: formatNumber(frontendRequestCount) },
        { label: "Avg", value: averageDuration(frontendDurationTotal, frontendDurationCount) },
      ],
      preview: `Frontend -> backend ${formatBytes(frontendBytes)} in current monitor window.`,
    },
    {
      id: "flow-gateway",
      lane: 2,
      order: 0,
      kind: "gateway",
      title: "API Gateway",
      subtitle: "HTTP, SSE and runtime bridge",
      badge: overview?.system.status || "runtime",
      status: gatewayStatus,
      metrics: [
        { label: "Reqs", value: formatNumber(frontendRequestCount) },
        { label: "Errs", value: formatNumber(frontendRequestErrors) },
        { label: "Bytes", value: formatBytes(frontendBytes) },
      ],
      preview: `Status ${overview?.system.status ?? "unknown"} · last traffic ${formatLastActive(Math.max(frontendLastAt, runtimeLastAt))}.`,
    },
    {
      id: "flow-runtime",
      lane: 3,
      order: 0,
      kind: "runtime",
      title: "Agent Runtime",
      subtitle: `${formatNumber(agentDirectory.length)} configured agents across current workspace`,
      badge: `${formatNumber(activeAgentNames.length)} hot`,
      status: runtimeStatus,
      metrics: [
        { label: "Actions", value: formatNumber(runtimeEntries.length) },
        { label: "LLM", value: formatNumber(llmCallsWindow) },
        { label: "Tools", value: formatNumber(toolCallsWindow) },
      ],
      chips: activeAgentNames.slice(0, compact ? 3 : 6),
      preview: latestRuntimePreview || "Runtime events will surface here as soon as the agent starts talking to tools or models.",
    },
    {
      id: "flow-llm",
      lane: 4,
      order: 0,
      kind: "llm",
      title: "LLM Router",
      subtitle: modelPrimary,
      badge: `${formatNumber(llmCallsWindow)} calls`,
      status: llmStatus,
      metrics: [
        { label: "Calls", value: formatNumber(llmCallsWindow) },
        { label: "Tokens", value: formatNumber(totalTokensWindow) },
        {
          label: "Avg",
          value: averageDuration(
            llmDurationTotal || llmNetworkDurationTotal,
            llmDurationCount || llmNetworkDurationCount,
          ),
        },
      ],
      chips: sortedCounterKeys(activeModels, compact ? 2 : 4),
      preview:
        sortedCounterKeys(activeModels, compact ? 2 : 4).join(" · ") ||
        "No recent model activity captured in the current runtime window.",
    },
  ];

  const edges: FlowTopologyGraph["edges"] = [
    {
      id: "flow-edge-user-frontend",
      from: "flow-user",
      to: "flow-frontend",
      label: "chat input",
      detail: `${formatNumber(recentMessages.length)} recent messages`,
      volume: Math.max(recentMessages.length, 1),
      status: userStatus,
      active: recentMessages.length > 0,
    },
    {
      id: "flow-edge-frontend-gateway",
      from: "flow-frontend",
      to: "flow-gateway",
      label: "http / ws",
      detail: `${averageDuration(frontendDurationTotal, frontendDurationCount)} avg`,
      volume: Math.max(frontendRequestCount, 1),
      status: connectionState === "disconnected" ? "warning" : flowStatusFromActivity(frontendRequestCount, frontendRequestErrors, frontendLastAt),
      active: flowHasRecentActivity(frontendLastAt),
    },
    {
      id: "flow-edge-gateway-runtime",
      from: "flow-gateway",
      to: "flow-runtime",
      label: "runtime cards",
      detail: `${formatNumber(overview?.usage_window.runtime_cards_considered ?? runtimeEntries.length)} observed`,
      volume: Math.max(runtimeEntries.length, 1),
      status: runtimeStatus,
      active: flowHasRecentActivity(runtimeLastAt),
    },
    {
      id: "flow-edge-runtime-llm",
      from: "flow-runtime",
      to: "flow-llm",
      label: "llm calls",
      detail: `${formatNumber(totalTokensWindow)} tok`,
      volume: Math.max(llmCallsWindow, 1),
      status: llmStatus,
      active: flowHasRecentActivity(Math.max(llmLastAt, llmNetworkLastAt)),
    },
  ];

  const capabilityCandidates = [...capabilitySummaries.values()]
    .filter((summary) => {
      if (summary.key === "approval") return approvalQueueTotal > 0 || pendingApprovalCount > 0;
      if (summary.key === "subagents") return agentDirectory.length > 1 || activeCollaborators > 0 || pendingTasks > 0;
      if (summary.key === "memory") return summary.calls > 0 || summary.configuredAgents.size > 0 || contextCompactions > 0;
      return summary.calls > 0 || summary.configuredAgents.size > 0;
    })
    .sort((left, right) => {
      const leftScore =
        left.calls * 100 +
        left.errors * 40 +
        left.configuredAgents.size * 12 +
        (flowHasRecentActivity(left.lastAt) ? 20 : 0) +
        (left.key === "approval" && pendingApprovalCount > 0 ? 120 : 0) +
        (left.key === "subagents" && activeCollaborators > 0 ? 80 : 0);
      const rightScore =
        right.calls * 100 +
        right.errors * 40 +
        right.configuredAgents.size * 12 +
        (flowHasRecentActivity(right.lastAt) ? 20 : 0) +
        (right.key === "approval" && pendingApprovalCount > 0 ? 120 : 0) +
        (right.key === "subagents" && activeCollaborators > 0 ? 80 : 0);
      return rightScore - leftScore || FLOW_CAPABILITY_PRESETS[left.key].order - FLOW_CAPABILITY_PRESETS[right.key].order;
    });

  const selectedCapabilityKeys = capabilityCandidates
    .slice(0, compact ? 3 : 6)
    .map((summary) => summary.key);

  selectedCapabilityKeys.forEach((key) => {
    const summary = capabilitySummaries.get(key);
    if (!summary) return;
    const preset = FLOW_CAPABILITY_PRESETS[key];
    const topTools = sortedCounterKeys(summary.toolCounts, compact ? 2 : 4);
    const metrics =
      key === "subagents"
        ? [
            { label: "Live", value: formatNumber(activeCollaborators) },
            { label: "Queue", value: formatNumber(pendingTasks) },
            { label: "Agents", value: formatNumber(agentDirectory.length) },
          ]
        : key === "approval"
          ? [
              { label: "Pending", value: formatNumber(pendingApprovalCount) },
              { label: "Queued", value: formatNumber(approvalQueueTotal) },
              { label: "State", value: pendingApprovalCount > 0 ? "hold" : "clear" },
            ]
          : key === "memory"
            ? [
                { label: "Calls", value: formatNumber(summary.calls) },
                { label: "Compacts", value: formatNumber(contextCompactions) },
                { label: "Agents", value: formatNumber(summary.configuredAgents.size) },
              ]
            : [
                { label: "Calls", value: formatNumber(summary.calls) },
                { label: "Errs", value: formatNumber(summary.errors) },
                { label: "Avg", value: averageDuration(summary.durationTotal, summary.durationCount) },
              ];

    const status =
      key === "approval"
        ? (pendingApprovalCount > 0 ? "warning" : approvalQueueTotal > 0 ? "active" : "idle")
        : key === "subagents"
          ? (activeCollaborators > 0 || pendingTasks > 0 ? "active" : flowStatusFromFlag(agentDirectory.length > 1))
          : flowStatusFromActivity(summary.calls, summary.errors, summary.lastAt);

    nodes.push({
      id: preset.nodeId,
      lane: 5,
      order: preset.order,
      kind: preset.kind,
      title: preset.title,
      subtitle: preset.subtitle,
      badge:
        key === "approval"
          ? `${formatNumber(pendingApprovalCount)} pending`
          : key === "subagents"
            ? `${formatNumber(agentDirectory.length)} total`
            : `${formatNumber(Math.max(summary.calls, summary.configuredAgents.size))}`,
      status,
      metrics,
      chips:
        key === "subagents"
          ? activeAgentNames.slice(0, compact ? 2 : 5)
          : topTools.length
            ? topTools
            : [...summary.configuredAgents].slice(0, compact ? 2 : 4),
      preview:
        key === "subagents"
          ? `Active ${formatNumber(activeCollaborators)} · pending ${formatNumber(pendingTasks)} · last runtime ${formatLastActive(runtimeLastAt)}.`
          : key === "approval"
            ? pendingApprovalCount > 0
              ? "Manual decisions are currently blocking one or more runtime actions."
              : "Approval rail is configured and currently clear."
            : key === "memory"
              ? `Recent memory activity ${formatLastActive(summary.lastAt)}.`
              : `${topTools.join(" · ") || "Configured tools present."} · last active ${formatLastActive(summary.lastAt)}.`,
    });

    edges.push({
      id: `flow-edge-runtime-${key}`,
      from: "flow-runtime",
      to: preset.nodeId,
      label:
        key === "subagents"
          ? "handoff"
          : key === "approval"
            ? "guardrail"
            : key === "memory"
              ? "memory ops"
              : "tool calls",
      detail:
        key === "approval"
          ? `${formatNumber(pendingApprovalCount)} pending`
          : key === "subagents"
            ? `${formatNumber(activeCollaborators)} live`
            : key === "memory"
              ? `${formatNumber(contextCompactions)} compactions`
              : averageDuration(summary.durationTotal, summary.durationCount),
      volume: Math.max(summary.calls, key === "subagents" ? activeCollaborators + pendingTasks : 1),
      status,
      active: key === "approval" ? pendingApprovalCount > 0 : flowHasRecentActivity(summary.lastAt) || (key === "subagents" && activeCollaborators > 0),
    });
  });

  const externalHostChips = sortedCounterKeys(externalHosts, compact ? 2 : 4);
  nodes.push({
    id: "flow-external",
    lane: 6,
    order: 0,
    kind: "web",
    minWidthOverride: compact ? 150 : 170,
    maxWidthOverride: compact ? 360 : 520,
    title: "External APIs",
    subtitle: externalHostChips.length ? "Remote endpoints" : "External traffic",
    badge: `${formatNumber(externalRequestCount)} reqs`,
    status: externalStatus,
    metrics: [
      { label: "Reqs", value: formatNumber(externalRequestCount) },
      { label: "Errs", value: formatNumber(externalRequestErrors) },
      { label: "Bytes", value: formatBytes(externalBytes) },
    ],
    chips: externalHostChips,
    preview: externalHostChips.length
      ? undefined
      : "No recent outbound traffic captured in the current monitor window.",
  });

  const capabilityNodeIds = new Map(selectedCapabilityKeys.map((key) => [key, FLOW_CAPABILITY_PRESETS[key].nodeId]));
  const externalCapabilityEdges = [...externalCapabilitySummaries.values()]
    .filter((summary) => capabilityNodeIds.has(summary.key))
    .sort((left, right) => right.requests - left.requests || right.lastAt - left.lastAt);

  if (externalCapabilityEdges.length > 0) {
    externalCapabilityEdges.forEach((summary) => {
      const fromNodeId = capabilityNodeIds.get(summary.key) ?? "flow-runtime";
      edges.push({
        id: `flow-edge-${summary.key}-external`,
        from: fromNodeId,
        to: "flow-external",
        label:
          summary.key === "search"
            ? "search http"
            : summary.key === "browser"
              ? "browser nav"
              : summary.key === "exec"
                ? "shell egress"
                : "tool net",
        detail: averageDuration(summary.durationTotal, summary.durationCount),
        volume: Math.max(summary.requests, 1),
        status: flowStatusFromActivity(summary.requests, summary.errors, summary.lastAt),
        active: flowHasRecentActivity(summary.lastAt),
      });
    });
  } else {
    edges.push({
      id: "flow-edge-runtime-external",
      from: "flow-runtime",
      to: "flow-external",
      label: "outbound net",
      detail: averageDuration(externalDurationTotal, externalDurationCount),
      volume: Math.max(externalRequestCount, 1),
      status: externalStatus,
      active: flowHasRecentActivity(externalLastAt),
    });
  }

  return {
    laneLabels: ["Entry", "Client", "Platform", "Runtime", "LLM", "Capabilities", "Outside"],
    nodes: nodes.map((node) => {
      const layout = estimateFlowNodeLayout(node, compact);
      return {
        ...node,
        preferredWidth: layout.preferredWidth,
        singleRowMetrics: layout.singleRowMetrics,
      };
    }),
    edges,
  };
}

function collectSkills(projects: ProjectSummary[], agents: AgentInfo[]) {
  const skills = new Map<string, SkillRow>();
  const projectByAgent = new Map<string, Set<string>>();

  projects.forEach((project) => {
    project.agents.forEach((agent) => {
      const bucket = projectByAgent.get(agent.name) ?? new Set<string>();
      bucket.add(project.name);
      projectByAgent.set(agent.name, bucket);
    });
  });

  agents.forEach((agent) => {
    (agent.skills ?? []).forEach((skill) => {
      const row = skills.get(skill) ?? {
        name: skill,
        agents: [],
        projects: [],
        detail: agent.system_prompt_preview ?? "TODO: skill file browser wiring not connected yet.",
        alwaysLoadedHint: agent.soul?.style ?? agent.role,
      };
      if (!row.agents.includes(agent.name)) row.agents.push(agent.name);
      for (const projectName of projectByAgent.get(agent.name) ?? []) {
        if (!row.projects.includes(projectName)) row.projects.push(projectName);
      }
      skills.set(skill, row);
    });
  });

  return [...skills.values()].sort((left, right) => left.name.localeCompare(right.name));
}

function SkillCard({ skill, compact = false }: { skill: SkillRow; compact?: boolean }) {
  const visibleAgents = compact ? skill.agents.slice(0, 3) : skill.agents;
  const hiddenAgents = Math.max(skill.agents.length - visibleAgents.length, 0);
  const visibleProjects = compact ? skill.projects.slice(0, 2) : skill.projects;
  const hiddenProjects = Math.max(skill.projects.length - visibleProjects.length, 0);
  const projectSummary = visibleProjects.join(", ");

  return (
    <div className={`skill-card ${compact ? "skill-card--compact" : ""}`}>
      <div className="skill-card__header">
        <div className="skill-card__title">
          <strong>{skill.name}</strong>
          <div className="small-note skill-card__hint" title={skill.alwaysLoadedHint || "No always-loaded hint."}>
            Hint: {skill.alwaysLoadedHint || "No always-loaded hint."}
          </div>
        </div>
        <div className="skill-card__counts">
          <span>{skill.agents.length} agents</span>
          <span>{skill.projects.length} projects</span>
        </div>
      </div>
      {!compact ? (
        <div className="small-note skill-card__detail" title={skill.detail}>
          {skill.detail}
        </div>
      ) : null}
      <div className="skill-card__meta">
        {visibleAgents.map((agent) => (
          <span key={agent} className="tag">
            {agent}
          </span>
        ))}
        {hiddenAgents > 0 ? <span className="tag">+{hiddenAgents} more</span> : null}
      </div>
      <div className="small-note skill-card__projects">
        Projects: {projectSummary || "Standalone only"}
        {hiddenProjects > 0 ? ` +${hiddenProjects}` : ""}
      </div>
    </div>
  );
}

function groupRuntimeByChat(overview: MonitorOverview | null) {
  if (!overview) return [] as ClusterItem[];
  const buckets = new Map<number, ClusterItem>();
  overview.recent_runtime.forEach((item) => {
    const entry = buckets.get(item.chatroom_id) ?? {
      chatroomId: item.chatroom_id,
      chatTitle: item.chat_title,
      projectName: item.project_name || "Standalone",
      runtimeCount: 0,
      llmCalls: 0,
      toolCalls: 0,
      tokenTotal: 0,
      latestAt: item.created_at,
      agents: [],
    };
    entry.runtimeCount += 1;
    if (item.type === "llm_call") entry.llmCalls += 1;
    if (item.type === "tool_call") entry.toolCalls += 1;
    entry.tokenTotal += (item.tokens_in ?? 0) + (item.tokens_out ?? 0);
    if (item.agent && !entry.agents.includes(item.agent)) entry.agents.push(item.agent);
    if (!entry.latestAt || new Date(item.created_at).getTime() > new Date(entry.latestAt).getTime()) {
      entry.latestAt = item.created_at;
    }
    buckets.set(item.chatroom_id, entry);
  });
  return [...buckets.values()].sort((left, right) => right.runtimeCount - left.runtimeCount);
}

function buildModelRows(overview: MonitorOverview | null) {
  if (!overview) return [] as ModelRow[];
  const buckets = new Map<string, ModelRow>();
  overview.recent_runtime.forEach((item) => {
    const name = item.model || "unknown";
    const row = buckets.get(name) ?? { name, calls: 0, tokens: 0, chats: 0 };
    row.calls += item.type === "llm_call" ? 1 : 0;
    row.tokens += (item.tokens_in ?? 0) + (item.tokens_out ?? 0);
    buckets.set(name, row);
  });
  groupRuntimeByChat(overview).forEach((cluster) => {
    const firstModel = overview.recent_runtime.find((item) => item.chatroom_id === cluster.chatroomId)?.model || "unknown";
    const row = buckets.get(firstModel) ?? { name: firstModel, calls: 0, tokens: 0, chats: 0 };
    row.chats += 1;
    buckets.set(firstModel, row);
  });
  return [...buckets.values()].sort((left, right) => right.calls - left.calls || right.tokens - left.tokens);
}

function buildSecurityEvents(overview: MonitorOverview | null) {
  if (!overview) return [] as SecurityEvent[];
  const events: SecurityEvent[] = [];
  overview.recent_runtime.forEach((item) => {
    const type = item.type;
    if (type === "gate_rejected") {
      events.push({
        id: `sec-${item.id}`,
        severity: "high",
        title: item.title,
        detail: runtimePrimaryPreview(item) || "Approval rejected before action execution.",
        createdAt: item.created_at,
      });
    } else if (type === "gate_blocked") {
      events.push({
        id: `sec-${item.id}`,
        severity: "medium",
        title: item.title,
        detail: runtimePrimaryPreview(item) || "Action is waiting for operator approval.",
        createdAt: item.created_at,
      });
    } else if (type === "tool_call" && item.success === false) {
      events.push({
        id: `sec-${item.id}`,
        severity: "critical",
        title: item.title,
        detail: runtimeResponsePreview(item) || "Tool execution failed.",
        createdAt: item.created_at,
      });
    } else if (type.includes("error")) {
      events.push({
        id: `sec-${item.id}`,
        severity: "high",
        title: item.title,
        detail: runtimeResponsePreview(item) || runtimePrimaryPreview(item) || "Runtime error detected.",
        createdAt: item.created_at,
      });
    }
  });
  return events;
}

function buildRuntimeBrainEvents(item: MonitorOverview["recent_runtime"][number]): BrainEvent[] {
  const runtimeToneValue = runtimeTone(item.type, item.success);
  const defaultTone: BrainEvent["tone"] =
    runtimeToneValue === "success"
      ? "success"
      : runtimeToneValue === "warning"
        ? "warning"
        : runtimeToneValue === "error"
          ? "error"
          : "neutral";
  const fromEntity = normalizeEntity(item.from_entity || item.agent, "runtime");
  const toEntity = item.to_entity ? normalizeEntity(item.to_entity, "target") : null;
  const common = {
    kind: "runtime" as const,
    runtimeId: item.id,
    runtimeType: item.type,
    createdAt: item.created_at,
    projectName: item.project_name || "Standalone",
    chatTitle: item.chat_title,
    clientTurnId: item.client_turn_id || null,
  };

  if (Array.isArray(item.brain_events) && item.brain_events.length > 0) {
    return item.brain_events.map((event, index) => ({
      ...common,
      id: typeof event.id === "string" && event.id.trim() ? event.id : `runtime-${item.id}-${index}`,
      source: normalizeEntity(event.from_entity || fromEntity, "runtime"),
      operationLabel: typeof event.operation_label === "string" ? event.operation_label : item.operation_label,
      fromEntity: normalizeEntity(event.from_entity || fromEntity, "runtime"),
      toEntity: event.to_entity ? normalizeEntity(event.to_entity, "target") : undefined,
      phase: event.phase === "outbound" || event.phase === "inbound" || event.phase === "state" ? event.phase : "state",
      category:
        event.category === "llm" || event.category === "tool" || event.category === "message" || event.category === "runtime"
          ? event.category
          : "runtime",
      label:
        typeof event.label === "string" && event.label.trim()
          ? event.label
          : (item.title || buildCommunicationLabel(fromEntity, toEntity || undefined)),
      detail:
        typeof event.detail === "string" && event.detail.trim()
          ? event.detail
          : (runtimePrimaryPreview(item) || item.title),
      tone:
        event.tone === "neutral" || event.tone === "success" || event.tone === "warning" || event.tone === "error"
          ? event.tone
          : defaultTone,
    }));
  }

  if (item.type === "llm_call") {
    const llmTarget = "LLM";
    const outboundDetail =
      runtimeRequestPreview(item) ||
      [item.model, typeof item.turn === "number" ? `turn ${item.turn}` : ""].filter(Boolean).join(" · ") ||
      "Prompt payload captured.";
    const inboundDetail =
      runtimeResponsePreview(item) ||
      "Model response captured.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-outbound`,
        source: fromEntity,
        operationLabel: item.operation_label || "llm",
        fromEntity,
        toEntity: llmTarget,
        phase: "outbound",
        category: "llm",
        label: buildCommunicationLabel(fromEntity, llmTarget),
        detail: outboundDetail,
        tone: "neutral",
      },
      {
        ...common,
        id: `runtime-${item.id}-inbound`,
        source: llmTarget,
        operationLabel: item.operation_label || "llm",
        fromEntity: llmTarget,
        toEntity: fromEntity,
        phase: "inbound",
        category: "llm",
        label: buildCommunicationLabel(llmTarget, fromEntity),
        detail: inboundDetail,
        tone: "success",
      },
    ];
  }

  if (item.type === "tool_call") {
    const toolEntity = normalizeEntity(item.to_entity || item.tool_name, "tool");
    const outboundDetail = runtimeRequestPreview(item) || "Tool call issued.";
    const inboundDetail = runtimeResponsePreview(item) || "Tool output returned.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-outbound`,
        source: fromEntity,
        operationLabel: item.operation_label || toolEntity,
        fromEntity,
        toEntity: toolEntity,
        phase: "outbound",
        category: "tool",
        label: buildCommunicationLabel(fromEntity, toolEntity),
        detail: outboundDetail,
        tone: "neutral",
      },
      {
        ...common,
        id: `runtime-${item.id}-inbound`,
        source: toolEntity,
        operationLabel: item.operation_label || toolEntity,
        fromEntity: toolEntity,
        toEntity: fromEntity,
        phase: "inbound",
        category: "tool",
        label: buildCommunicationLabel(toolEntity, fromEntity),
        detail: inboundDetail,
        tone: item.success === false ? "error" : "success",
      },
    ];
  }

  if (item.type === "agent_error") {
    const targetEntity = "User";
    const failureDetail = runtimeResponsePreview(item) || "Agent stream failed before a final reply was saved.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-error`,
        source: fromEntity,
        operationLabel: item.operation_label || "error",
        fromEntity,
        toEntity: targetEntity,
        phase: "inbound",
        category: "runtime",
        label: buildCommunicationLabel(fromEntity, targetEntity),
        detail: failureDetail,
        tone: "error",
      },
    ];
  }

  return [
    {
      ...common,
      id: `runtime-${item.id}`,
      source: fromEntity,
      operationLabel: item.operation_label || item.stage || runtimeLabel(item.type),
      fromEntity,
      toEntity: toEntity || undefined,
      phase: "state",
      category: "runtime",
      label: toEntity ? buildCommunicationLabel(fromEntity, toEntity) : item.title,
      detail: runtimePrimaryPreview(item) || item.title,
      tone: defaultTone,
    },
  ];
}

const BRAIN_PHASE_SORT_RANK: Record<NonNullable<BrainEvent["phase"]>, number> = {
  inbound: 0,
  state: 1,
  outbound: 2,
};

function brainEventSortId(event: BrainEvent) {
  if (typeof event.runtimeId === "number") return event.runtimeId;
  const match = /-(\d+)(?:-|$)/.exec(event.id);
  return match ? Number(match[1]) : 0;
}

function compareBrainEventsNewest(left: BrainEvent, right: BrainEvent) {
  const timeDiff = monitorCreatedAtMs(right.createdAt) - monitorCreatedAtMs(left.createdAt);
  if (timeDiff !== 0) return timeDiff;

  const idDiff = brainEventSortId(right) - brainEventSortId(left);
  if (idDiff !== 0) return idDiff;

  const phaseDiff = BRAIN_PHASE_SORT_RANK[left.phase ?? "state"] - BRAIN_PHASE_SORT_RANK[right.phase ?? "state"];
  if (phaseDiff !== 0) return phaseDiff;

  return right.id.localeCompare(left.id);
}

function buildBrainEvents(overview: MonitorOverview | null): BrainEvent[] {
  if (!overview) return [];
  const runtimeEvents: BrainEvent[] = overview.recent_runtime.flatMap((item) => buildRuntimeBrainEvents(item));

  const messageEvents: BrainEvent[] = overview.recent_messages.map((item) => ({
    id: `message-${item.id}`,
    kind: "message",
    source: item.agent_name || "User",
    operationLabel: item.message_type === "text" ? "msg" : item.message_type,
    phase: item.agent_name ? "inbound" : "outbound",
    fromEntity: item.agent_name || "User",
    toEntity: item.agent_name ? "User" : (item.project_name ? "Project Chat" : "Assistant"),
    category: "message",
    label: buildCommunicationLabel(item.agent_name || "User", item.agent_name ? "User" : (item.project_name ? "Project Chat" : "Assistant")),
    detail: item.content_preview || "(empty message)",
    createdAt: item.created_at,
    tone: item.agent_name ? "neutral" : "success",
    clientTurnId: item.client_turn_id || null,
    messageType: item.message_type,
    messageContent: item.content || item.content_preview || "",
    projectName: item.project_name || "Standalone",
    chatTitle: item.chat_title,
  }));

  return [...runtimeEvents, ...messageEvents].sort(compareBrainEventsNewest);
}

function buildHourlyBuckets(events: BrainEvent[], range: HistoryRange) {
  const bucketCount = range === "1h" ? 6 : range === "6h" ? 6 : range === "24h" ? 8 : range === "7d" ? 7 : 10;
  const now = Date.now();
  const spanMs =
    range === "1h"
      ? 60 * 60 * 1000
      : range === "6h"
        ? 6 * 60 * 60 * 1000
        : range === "24h"
          ? 24 * 60 * 60 * 1000
          : range === "7d"
            ? 7 * 24 * 60 * 60 * 1000
            : 30 * 24 * 60 * 60 * 1000;
  const bucketSize = spanMs / bucketCount;
  const buckets = Array.from({ length: bucketCount }, (_, index) => ({
    label:
      range === "7d" || range === "30d"
        ? `${index + 1}`
        : `${Math.round(((index + 1) * spanMs) / bucketCount / 3600000)}h`,
    value: 0,
  }));

  events.forEach((event) => {
    if (!event.createdAt) return;
    const delta = now - new Date(event.createdAt).getTime();
    if (delta < 0 || delta > spanMs) return;
    const bucketIndex = clamp(Math.floor((spanMs - delta) / bucketSize), 0, bucketCount - 1);
    buckets[bucketIndex].value += 1;
  });

  return buckets;
}

function buildBrainTimelineBuckets(events: BrainEvent[], unit: BrainTimelineUnit) {
  const bucketCount = unit === "minute" ? 30 : unit === "hour" ? 24 : unit === "day" ? 30 : 12;
  if (unit === "month") {
    const now = new Date();
    const buckets = Array.from({ length: bucketCount }, (_, index) => {
      const bucketStart = new Date(now.getFullYear(), now.getMonth() - (bucketCount - 1 - index), 1);
      return {
        label: String(bucketStart.getMonth() + 1),
        year: bucketStart.getFullYear(),
        month: bucketStart.getMonth(),
        value: 0,
      };
    });

    events.forEach((event) => {
      if (!event.createdAt) return;
      const eventDate = new Date(event.createdAt);
      if (!Number.isFinite(eventDate.getTime())) return;
      const bucket = buckets.find(
        (item) => item.year === eventDate.getFullYear() && item.month === eventDate.getMonth(),
      );
      if (bucket) bucket.value += 1;
    });

    return buckets.map(({ label, value }) => ({ label, value }));
  }

  const now = new Date();
  const currentBucketStart =
    unit === "minute"
      ? new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes())
      : unit === "hour"
        ? new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours())
        : new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const buckets = Array.from({ length: bucketCount }, (_, index) => {
    const offset = index - (bucketCount - 1);
    const bucketStart =
      unit === "minute"
        ? new Date(
            currentBucketStart.getFullYear(),
            currentBucketStart.getMonth(),
            currentBucketStart.getDate(),
            currentBucketStart.getHours(),
            currentBucketStart.getMinutes() + offset,
          )
        : unit === "hour"
          ? new Date(
              currentBucketStart.getFullYear(),
              currentBucketStart.getMonth(),
              currentBucketStart.getDate(),
              currentBucketStart.getHours() + offset,
            )
          : new Date(
              currentBucketStart.getFullYear(),
              currentBucketStart.getMonth(),
              currentBucketStart.getDate() + offset,
            );
    const bucketEnd =
      unit === "minute"
        ? new Date(
            bucketStart.getFullYear(),
            bucketStart.getMonth(),
            bucketStart.getDate(),
            bucketStart.getHours(),
            bucketStart.getMinutes() + 1,
          )
        : unit === "hour"
          ? new Date(
              bucketStart.getFullYear(),
              bucketStart.getMonth(),
              bucketStart.getDate(),
              bucketStart.getHours() + 1,
            )
          : new Date(bucketStart.getFullYear(), bucketStart.getMonth(), bucketStart.getDate() + 1);

    return {
      label:
        unit === "minute"
          ? String(bucketStart.getMinutes()).padStart(2, "0")
          : unit === "hour"
            ? String(bucketStart.getHours()).padStart(2, "0")
            : String(bucketStart.getDate()),
      start: bucketStart.getTime(),
      end: bucketEnd.getTime(),
      value: 0,
    };
  });

  events.forEach((event) => {
    if (!event.createdAt) return;
    const eventTime = new Date(event.createdAt).getTime();
    if (!Number.isFinite(eventTime)) return;
    const bucket = buckets.find((item) => eventTime >= item.start && eventTime < item.end);
    if (bucket) bucket.value += 1;
  });

  return buckets.map(({ label, value }) => ({ label, value }));
}

function brainTimelineLabelStep(unit: BrainTimelineUnit) {
  if (unit === "minute") return 5;
  if (unit === "hour") return 3;
  if (unit === "day") return 5;
  return 2;
}

function runtimeTokenTotal(item: MonitorOverview["recent_runtime"][number]) {
  return (item.tokens_in ?? 0) + (item.tokens_out ?? 0);
}

function runtimeCost(
  item: MonitorOverview["recent_runtime"][number],
  pricing: MonitorOverview["usage_window"]["pricing"],
) {
  return ((item.tokens_in ?? 0) / 1000) * pricing.input_per_1k + ((item.tokens_out ?? 0) / 1000) * pricing.output_per_1k;
}

function isWithinSystemPeriod(value: string | null | undefined, period: "day" | "week" | "month") {
  if (!value) return false;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return false;

  const now = new Date();
  const start =
    period === "day"
      ? new Date(now.getFullYear(), now.getMonth(), now.getDate())
      : period === "week"
        ? new Date(now.getFullYear(), now.getMonth(), now.getDate() - ((now.getDay() + 6) % 7))
        : new Date(now.getFullYear(), now.getMonth(), 1);
  const end =
    period === "day"
      ? new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1)
      : period === "week"
        ? new Date(start.getFullYear(), start.getMonth(), start.getDate() + 7)
        : new Date(start.getFullYear(), start.getMonth() + 1, 1);

  return date.getTime() >= start.getTime() && date.getTime() < end.getTime();
}

function buildUsageBuckets(overview: MonitorOverview | null, range: HistoryRange) {
  const pricing = overview?.usage_window.pricing ?? { input_per_1k: 0, output_per_1k: 0 };
  const runtime = overview?.recent_runtime ?? [];
  const bucketCount = range === "1h" ? 12 : range === "6h" ? 6 : range === "24h" ? 24 : range === "7d" ? 7 : 30;
  const now = new Date();
  const currentBucketStart =
    range === "1h"
      ? new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), Math.floor(now.getMinutes() / 5) * 5)
      : range === "6h" || range === "24h"
        ? new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours())
        : new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const buckets = Array.from({ length: bucketCount }, (_, index) => {
    const offset = index - (bucketCount - 1);
    const bucketStart =
      range === "1h"
        ? new Date(
            currentBucketStart.getFullYear(),
            currentBucketStart.getMonth(),
            currentBucketStart.getDate(),
            currentBucketStart.getHours(),
            currentBucketStart.getMinutes() + offset * 5,
          )
        : range === "6h" || range === "24h"
          ? new Date(
              currentBucketStart.getFullYear(),
              currentBucketStart.getMonth(),
              currentBucketStart.getDate(),
              currentBucketStart.getHours() + offset,
            )
          : new Date(
              currentBucketStart.getFullYear(),
              currentBucketStart.getMonth(),
              currentBucketStart.getDate() + offset,
            );
    const bucketEnd =
      range === "1h"
        ? new Date(
            bucketStart.getFullYear(),
            bucketStart.getMonth(),
            bucketStart.getDate(),
            bucketStart.getHours(),
            bucketStart.getMinutes() + 5,
          )
        : range === "6h" || range === "24h"
          ? new Date(bucketStart.getFullYear(), bucketStart.getMonth(), bucketStart.getDate(), bucketStart.getHours() + 1)
          : new Date(bucketStart.getFullYear(), bucketStart.getMonth(), bucketStart.getDate() + 1);

    return {
      label:
        range === "1h"
          ? String(bucketStart.getMinutes()).padStart(2, "0")
          : range === "6h" || range === "24h"
            ? String(bucketStart.getHours()).padStart(2, "0")
            : String(bucketStart.getDate()),
      start: bucketStart.getTime(),
      end: bucketEnd.getTime(),
      tokens: 0,
      cost: 0,
    };
  });

  runtime.forEach((item) => {
    const eventTime = new Date(item.created_at).getTime();
    if (!Number.isFinite(eventTime)) return;
    const bucket = buckets.find((candidate) => eventTime >= candidate.start && eventTime < candidate.end);
    if (!bucket) return;
    bucket.tokens += runtimeTokenTotal(item);
    bucket.cost += runtimeCost(item, pricing);
  });

  return buckets.map(({ label, tokens, cost }) => ({
    label,
    tokens,
    cost: Number(cost.toFixed(4)),
  }));
}

function usageTokenTotal(overview: MonitorOverview | null, period: "day" | "week" | "month") {
  return (overview?.recent_runtime ?? [])
    .filter((item) => isWithinSystemPeriod(item.created_at, period))
    .reduce((total, item) => total + runtimeTokenTotal(item), 0);
}

function usageCostTotal(overview: MonitorOverview | null, period: "day" | "week" | "month") {
  const pricing = overview?.usage_window.pricing ?? { input_per_1k: 0, output_per_1k: 0 };
  return (overview?.recent_runtime ?? [])
    .filter((item) => isWithinSystemPeriod(item.created_at, period))
    .reduce((total, item) => total + runtimeCost(item, pricing), 0);
}

function buildAgentDirectory(projects: ProjectSummary[], agents: AgentInfo[]): AgentDirectoryEntry[] {
  const directory = new Map<string, AgentDirectoryEntry>();

  agents.forEach((agent) => {
    directory.set(agent.name, { ...agent, projects: [] });
  });

  projects.forEach((project) => {
    project.agents.forEach((agent) => {
      const existing = directory.get(agent.name);
      if (existing) {
        existing.projects = [...new Set([...existing.projects, project.name])];
        existing.skills = [...new Set([...(existing.skills ?? []), ...(agent.skills ?? [])])];
        existing.tools = [...new Set([...(existing.tools ?? []), ...(agent.tools ?? [])])];
      } else {
        directory.set(agent.name, { ...agent, projects: [project.name] });
      }
    });
  });

  return [...directory.values()].sort((left, right) => left.name.localeCompare(right.name));
}

export function MonitorTab() {
  const [activePage, setActivePage] = useState<MonitorPageId>(readInitialPage);
  const [overview, setOverview] = useState<MonitorOverview | null>(null);
  const [usage, setUsage] = useState<MonitorUsageResponse | null>(null);
  const [taskRunsResponse, setTaskRunsResponse] = useState<MonitorTaskRunsResponse | null>(null);
  const [processesResponse, setProcessesResponse] = useState<MonitorProcessesResponse | null>(null);
  const [filesResponse, setFilesResponse] = useState<MonitorFilesResponse | null>(null);
  const [contextCompactionsResponse, setContextCompactionsResponse] = useState<MonitorContextCompactionsResponse | null>(null);
  const [approvalQueueResponse, setApprovalQueueResponse] = useState<MonitorApprovalQueueResponse | null>(null);
  const [approvalAuditResponse, setApprovalAuditResponse] = useState<MonitorApprovalAuditResponse | null>(null);
  const [approvalAuditFilter, setApprovalAuditFilter] = useState("all");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [connectionState, setConnectionState] = useState<"connected" | "disconnected">("connected");
  const [memoryView, setMemoryView] = useState<MemoryView>("summary");
  const [skillsView, setSkillsView] = useState<SkillsView>("grid");
  const [securityFilter, setSecurityFilter] = useState<SecuritySeverity>("all");
  const [brainFilter, setBrainFilter] = useState<BrainFilter>("all");
  const [brainTimelineUnit, setBrainTimelineUnit] = useState<BrainTimelineUnit>("minute");
  const [brainActivityFilter, setBrainActivityFilter] = useState("");
  const [historyRange, setHistoryRange] = useState<HistoryRange>("24h");
  const [taskRunStatusFilter, setTaskRunStatusFilter] = useState<TaskRunStatusFilter>("all");
  const [processStatusFilter, setProcessStatusFilter] = useState<ProcessStatusFilter>("all");
  const [fileToolFilter, setFileToolFilter] = useState<FileToolFilter>("all");
  const [fileFilter, setFileFilter] = useState("");
  const [logLevel, setLogLevel] = useState<LogLevel>("all");
  const [logFilter, setLogFilter] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedTranscriptChatId, setSelectedTranscriptChatId] = useState<number | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null);
  const [showSecurityCatalog, setShowSecurityCatalog] = useState(false);
  const [showCreateRuleForm, setShowCreateRuleForm] = useState(false);
  const [logEntries, setLogEntries] = useState<MonitorLogEntry[]>([]);
  const [networkEntries, setNetworkEntries] = useState<MonitorNetworkEvent[]>([]);
  const [networkCategory, setNetworkCategory] = useState("all");
  const [networkFilter, setNetworkFilter] = useState("");
  const [showInternalNetwork, setShowInternalNetwork] = useState(false);
  const [logStreamState, setLogStreamState] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [networkStreamState, setNetworkStreamState] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [expandedBrainEventId, setExpandedBrainEventId] = useState<string | null>(null);
  const [brainRuntimeDetails, setBrainRuntimeDetails] = useState<Record<number, MonitorRuntimeDetail>>({});
  const [brainRuntimeDetailErrors, setBrainRuntimeDetailErrors] = useState<Record<number, string>>({});
  const [brainRuntimeDetailLoading, setBrainRuntimeDetailLoading] = useState<Record<number, boolean>>({});
  const [selectedTaskRunId, setSelectedTaskRunId] = useState<number | null>(null);
  const [taskRunDetails, setTaskRunDetails] = useState<Record<number, TaskRunDetail>>({});
  const [taskRunSteps, setTaskRunSteps] = useState<Record<number, MonitorTaskRunStepsResponse>>({});
  const [taskRunDetailErrors, setTaskRunDetailErrors] = useState<Record<number, string>>({});
  const [taskRunDetailLoading, setTaskRunDetailLoading] = useState<Record<number, boolean>>({});
  const [taskRunStepErrors, setTaskRunStepErrors] = useState<Record<number, string>>({});
  const [taskRunStepLoading, setTaskRunStepLoading] = useState<Record<number, boolean>>({});
  const [taskRunResumeLoading, setTaskRunResumeLoading] = useState<Record<number, boolean>>({});
  const [taskRunResumeErrors, setTaskRunResumeErrors] = useState<Record<number, string>>({});
  const [taskRunResumeMessages, setTaskRunResumeMessages] = useState<Record<number, string>>({});
  const [approvalQueueActionLoading, setApprovalQueueActionLoading] = useState<Record<number, boolean>>({});
  const [approvalQueueActionErrors, setApprovalQueueActionErrors] = useState<Record<number, string>>({});
  const [approvalQueueActionMessages, setApprovalQueueActionMessages] = useState<Record<number, string>>({});
  const logCursorRef = useRef(0);
  const networkCursorRef = useRef(0);
  const monitorSocketRef = useRef<WebSocket | null>(null);
  const loadMonitor = useCallback(async (silent = false) => {
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const [nextOverview, nextProjects, nextAgents, nextConfig] = await Promise.all([
        api.getMonitorOverview(),
        api.getProjects(),
        api.getAgents(),
        api.getConfig(),
      ]);
      setOverview(normalizeMonitorOverviewRuntime(nextOverview));
      setProjects(nextProjects);
      setAgents(nextAgents);
      setConfig(nextConfig);
      setConnectionState("connected");
      setError("");
    } catch (nextError) {
      setConnectionState("disconnected");
      setError(nextError instanceof Error ? nextError.message : "Failed to load Catown monitor");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (!error) return undefined;

    const timeoutId = window.setTimeout(() => setError(""), ERROR_AUTO_DISMISS_MS);
    return () => window.clearTimeout(timeoutId);
  }, [error]);

  useEffect(() => {
    function handleHashChange() {
      const hash = window.location.hash.replace(/^#/, "");
      if (pageExists(hash)) {
        setActivePage(hash);
      }
    }

    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  useEffect(() => {
    window.location.hash = activePage;
    window.localStorage.setItem("catown.monitor.page", activePage);
  }, [activePage]);

  useEffect(() => {
    void loadMonitor(false);
  }, [loadMonitor]);

  useEffect(() => {
    if (activePage !== "processes" || processesResponse) return;
    void refreshProcesses();
  }, [activePage, processesResponse]);

  useEffect(() => {
    if (activePage !== "usage") return;
    void refreshUsage();
  }, [activePage, historyRange]);

  useEffect(() => {
    if (activePage !== "network") return;
    void refreshNetwork();
  }, [activePage, networkCategory, networkFilter, showInternalNetwork]);

  useEffect(() => {
    if (activePage !== "tasks") return;
    void refreshTaskRuns();
  }, [activePage, historyRange]);

  useEffect(() => {
    if (activePage !== "compactions" || contextCompactionsResponse) return;
    void refreshContextCompactions();
  }, [activePage, contextCompactionsResponse]);

  useEffect(() => {
    if (activePage !== "approvals") return;
    void refreshApprovalQueue();
    void refreshApprovalAudit();
  }, [activePage, approvalAuditFilter]);

  useEffect(() => {
    if (activePage !== "files" || filesResponse) return;
    void refreshFiles();
  }, [activePage, filesResponse]);

  useEffect(() => {
    if (activePage !== "files" || !filesResponse) return;
    void refreshFiles();
  }, [activePage, fileFilter, fileToolFilter]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let cancelled = false;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (cancelled) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws`;
      const socket = new WebSocket(wsUrl);
      monitorSocketRef.current = socket;
      setConnectionState("disconnected");

      socket.onopen = () => {
        if (cancelled) return;
        socket.send(JSON.stringify({ type: "subscribe", topic: "monitor" }));
        setConnectionState("connected");
        void loadMonitor(true);
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          if (data.type === "monitor_task_run" && data.payload && typeof data.payload === "object") {
            const payload = data.payload as Record<string, unknown>;
            const entry = asMonitorRecord(payload.entry);
            const detail = asMonitorRecord(payload.detail);
            const capturedAt = monitorStringField(payload.captured_at);

            if (entry) {
              setTaskRunsResponse((current) =>
                mergeMonitorTaskRunResponse(
                  current,
                  entry as MonitorTaskRunSummary,
                  historyRange,
                  capturedAt,
                ),
              );
            }

            if (detail && typeof detail.id === "number") {
              const taskRunId = detail.id;
              setTaskRunDetails((current) => ({ ...current, [taskRunId]: detail as TaskRunDetail }));
              setTaskRunDetailErrors((current) => {
                const next = { ...current };
                delete next[taskRunId];
                return next;
              });
              setApprovalQueueResponse((current) =>
                mergeMonitorApprovalQueueFromTaskRun(
                  current,
                  detail as TaskRunDetail,
                  entry as MonitorTaskRunSummary | null,
                ),
              );
            }
            return;
          }

          if (data.type === "monitor_message" && data.payload && typeof data.payload === "object") {
            setOverview((current) =>
              applyMonitorMessageUpdate(
                current,
                data.payload as MonitorOverview["recent_messages"][number],
              ),
            );
            return;
          }

          if (data.type === "monitor_runtime" && data.payload && typeof data.payload === "object") {
            setOverview((current) =>
              applyMonitorRuntimeUpdate(
                current,
                data.payload as MonitorOverview["recent_runtime"][number],
              ),
            );
          }
        } catch {
          // Ignore malformed realtime frames and keep the socket alive.
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        setConnectionState("disconnected");
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
      monitorSocketRef.current?.close();
      monitorSocketRef.current = null;
    };
  }, [historyRange, loadMonitor]);

  useEffect(() => {
    let cancelled = false;
    let streamAbortController: AbortController | null = null;
    let reconnectTimer: number | null = null;

    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        if (!cancelled) {
          void loadAndStreamLogs();
        }
      }, MONITOR_STREAM_RECONNECT_DELAY_MS);
    };

    async function loadAndStreamLogs() {
      try {
        setLogStreamState("connecting");
        const response = await api.getMonitorLogs();
        if (cancelled) return;

        setLogEntries(mergeMonitorLogs([], response.entries));
        logCursorRef.current = response.latest_id;

        streamAbortController = new AbortController();
        const streamResponse = await fetch(`/api/monitor/logs/stream?cursor=${response.latest_id}`, {
          headers: { "X-Catown-Client": "monitor" },
          signal: streamAbortController.signal,
        });
        if (!streamResponse.ok || !streamResponse.body) {
          throw new Error("Failed to open monitor log stream");
        }

        if (!cancelled) {
          setLogStreamState("connected");
        }

        const reader = streamResponse.body.getReader();
        const decoder = new TextDecoder();
        let buffered = "";

        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;

          buffered += decoder.decode(value, { stream: true });
          const frames = buffered.split("\n\n");
          buffered = frames.pop() ?? "";

          for (const frame of frames) {
            const dataLine = frame
              .split("\n")
              .find((line) => line.startsWith("data: "));
            if (!dataLine) continue;

            try {
              const nextEntry = JSON.parse(dataLine.slice(6)) as MonitorLogEntry;
              logCursorRef.current = Math.max(logCursorRef.current, nextEntry.id);
              if (!cancelled) {
                setLogEntries((current) => mergeMonitorLogs(current, [nextEntry]));
              }
            } catch {
              // Ignore malformed frames without breaking the stream.
            }
          }
        }

        if (!cancelled) {
          setLogStreamState("disconnected");
          scheduleReconnect();
        }
      } catch {
        if (!cancelled) {
          setLogStreamState("disconnected");
          scheduleReconnect();
        }
      }
    }

    void loadAndStreamLogs();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      streamAbortController?.abort();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let streamAbortController: AbortController | null = null;
    let reconnectTimer: number | null = null;

    const scheduleReconnect = () => {
      if (cancelled || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        if (!cancelled) {
          void loadAndStreamNetwork();
        }
      }, MONITOR_STREAM_RECONNECT_DELAY_MS);
    };

    async function loadAndStreamNetwork() {
      try {
        setNetworkStreamState("connecting");
        const response = await api.getMonitorNetwork(300, networkCategory, networkFilter, showInternalNetwork);
        if (cancelled) return;

        setNetworkEntries(mergeMonitorNetwork([], response.entries));
        networkCursorRef.current = response.latest_id;

        streamAbortController = new AbortController();
        const params = new URLSearchParams({
          cursor: String(response.latest_id),
          category: networkCategory,
          include_internal: showInternalNetwork ? "true" : "false",
        });
        if (networkFilter.trim()) {
          params.set("query", networkFilter.trim());
        }

        const streamResponse = await fetch(`/api/monitor/network/stream?${params.toString()}`, {
          headers: { "X-Catown-Client": "monitor" },
          signal: streamAbortController.signal,
        });
        if (!streamResponse.ok || !streamResponse.body) {
          throw new Error("Failed to open monitor network stream");
        }

        if (!cancelled) {
          setNetworkStreamState("connected");
        }

        const reader = streamResponse.body.getReader();
        const decoder = new TextDecoder();
        let buffered = "";

        while (!cancelled) {
          const { value, done } = await reader.read();
          if (done) break;

          buffered += decoder.decode(value, { stream: true });
          const frames = buffered.split("\n\n");
          buffered = frames.pop() ?? "";

          for (const frame of frames) {
            const dataLine = frame
              .split("\n")
              .find((line) => line.startsWith("data: "));
            if (!dataLine) continue;

            try {
              const nextEntry = JSON.parse(dataLine.slice(6)) as MonitorNetworkEvent;
              networkCursorRef.current = Math.max(networkCursorRef.current, nextEntry.id);
              if (!cancelled) {
                setNetworkEntries((current) => mergeMonitorNetwork(current, [nextEntry]));
              }
            } catch {
              // Ignore malformed frames without breaking the stream.
            }
          }
        }

        if (!cancelled) {
          setNetworkStreamState("disconnected");
          scheduleReconnect();
        }
      } catch {
        if (!cancelled) {
          setNetworkStreamState("disconnected");
          scheduleReconnect();
        }
      }
    }

    void loadAndStreamNetwork();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      streamAbortController?.abort();
    };
  }, [networkCategory, networkFilter, showInternalNetwork]);

  const sortedProjects = useMemo(
    () => [...projects].sort((left, right) => left.display_order - right.display_order),
    [projects],
  );

  useEffect(() => {
    if (!selectedProjectId && sortedProjects[0]) {
      setSelectedProjectId(sortedProjects[0].id);
      return;
    }
    if (selectedProjectId && !sortedProjects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(sortedProjects[0]?.id ?? null);
    }
  }, [selectedProjectId, sortedProjects]);

  const groupedMessages = useMemo(() => {
    if (!overview) return [] as Array<{
      chatroomId: number;
      chatTitle: string;
      projectName: string;
      messages: MonitorOverview["recent_messages"];
    }>;

    const buckets = new Map<number, { chatroomId: number; chatTitle: string; projectName: string; messages: MonitorOverview["recent_messages"] }>();
    overview.recent_messages.forEach((message) => {
      const bucket = buckets.get(message.chatroom_id) ?? {
        chatroomId: message.chatroom_id,
        chatTitle: message.chat_title,
        projectName: message.project_name || "Standalone",
        messages: [],
      };
      bucket.messages.push(message);
      buckets.set(message.chatroom_id, bucket);
    });
    return [...buckets.values()].sort((left, right) => {
      const leftTime = left.messages[0]?.created_at ? new Date(left.messages[0].created_at).getTime() : 0;
      const rightTime = right.messages[0]?.created_at ? new Date(right.messages[0].created_at).getTime() : 0;
      return rightTime - leftTime;
    });
  }, [overview]);

  useEffect(() => {
    if (!selectedTranscriptChatId && groupedMessages[0]) {
      setSelectedTranscriptChatId(groupedMessages[0].chatroomId);
      return;
    }
    if (selectedTranscriptChatId && !groupedMessages.some((item) => item.chatroomId === selectedTranscriptChatId)) {
      setSelectedTranscriptChatId(groupedMessages[0]?.chatroomId ?? null);
    }
  }, [groupedMessages, selectedTranscriptChatId]);

  const selectedProject = useMemo(
    () => sortedProjects.find((project) => project.id === selectedProjectId) ?? null,
    [selectedProjectId, sortedProjects],
  );

  const selectedTranscript = useMemo(
    () => groupedMessages.find((item) => item.chatroomId === selectedTranscriptChatId) ?? groupedMessages[0] ?? null,
    [groupedMessages, selectedTranscriptChatId],
  );

  const agentDirectory = useMemo(() => buildAgentDirectory(sortedProjects, agents), [agents, sortedProjects]);
  const skills = useMemo(() => collectSkills(sortedProjects, agentDirectory), [agentDirectory, sortedProjects]);

  useEffect(() => {
    if (!selectedSkillName && skills[0]) {
      setSelectedSkillName(skills[0].name);
      return;
    }
    if (selectedSkillName && !skills.some((skill) => skill.name === selectedSkillName)) {
      setSelectedSkillName(skills[0]?.name ?? null);
    }
  }, [selectedSkillName, skills]);

  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.name === selectedSkillName) ?? skills[0] ?? null,
    [selectedSkillName, skills],
  );

  const clusters = useMemo(() => groupRuntimeByChat(overview), [overview]);
  const modelRows = useMemo(() => buildModelRows(overview), [overview]);
  const securityEvents = useMemo(() => buildSecurityEvents(overview), [overview]);
  const brainEvents = useMemo(() => buildBrainEvents(overview), [overview]);

  const filteredBrainEvents = useMemo(
    () => {
      const query = brainActivityFilter.trim().toLowerCase();
      return brainEvents.filter((event) => {
        if (brainFilter === "all") return true;
        return event.category === brainFilter;
      }).filter((event) => {
        if (!query) return true;
        return [
          event.label,
          event.detail,
          event.source,
          event.runtimeType,
          event.operationLabel,
          event.fromEntity,
          event.toEntity,
          event.category,
          event.phase,
          event.messageType,
          event.messageContent,
          event.projectName,
          event.chatTitle,
          event.clientTurnId,
        ]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(query));
      });
    },
    [brainActivityFilter, brainEvents, brainFilter],
  );

  const filteredSecurityEvents = useMemo(
    () =>
      securityEvents.filter((event) => {
        if (securityFilter === "all") return true;
        return event.severity === securityFilter;
      }),
    [securityEvents, securityFilter],
  );

  const filteredLogEntries = useMemo(
    () =>
      logEntries.filter((entry) => {
        const normalizedLevel = normalizeLogLevel(entry.level);
        if (logLevel !== "all" && normalizedLevel !== logLevel) return false;
        if (!logFilter) return true;
        const haystack = `${entry.logger} ${entry.message} ${entry.line}`.toLowerCase();
        return haystack.includes(logFilter.toLowerCase());
      }),
    [logEntries, logFilter, logLevel],
  );

  const loadBrainRuntimeDetail = useCallback(
    async (runtimeId: number) => {
      if (brainRuntimeDetails[runtimeId] || brainRuntimeDetailLoading[runtimeId]) return;

      setBrainRuntimeDetailLoading((current) => ({ ...current, [runtimeId]: true }));

      try {
        const detail = await api.getMonitorRuntimeCardDetail(runtimeId);
        setBrainRuntimeDetails((current) => ({ ...current, [runtimeId]: detail }));
        setBrainRuntimeDetailErrors((current) => {
          if (!current[runtimeId]) return current;
          const next = { ...current };
          delete next[runtimeId];
          return next;
        });
      } catch (nextError) {
        setBrainRuntimeDetailErrors((current) => ({
          ...current,
          [runtimeId]: nextError instanceof Error ? nextError.message : "Failed to load runtime detail",
        }));
      } finally {
        setBrainRuntimeDetailLoading((current) => ({ ...current, [runtimeId]: false }));
      }
    },
    [brainRuntimeDetailLoading, brainRuntimeDetails],
  );

  useEffect(() => {
    if (!filteredBrainEvents.length) {
      setExpandedBrainEventId(null);
      return;
    }
    if (expandedBrainEventId && !filteredBrainEvents.some((event) => event.id === expandedBrainEventId)) {
      setExpandedBrainEventId(null);
    }
  }, [expandedBrainEventId, filteredBrainEvents]);

  useEffect(() => {
    const expandedEvent = filteredBrainEvents.find((event) => event.id === expandedBrainEventId);
    if (!expandedEvent || expandedEvent.kind !== "runtime" || !expandedEvent.runtimeId) return;
    if (brainRuntimeDetails[expandedEvent.runtimeId] || brainRuntimeDetailLoading[expandedEvent.runtimeId]) return;
    void loadBrainRuntimeDetail(expandedEvent.runtimeId);
  }, [brainRuntimeDetailLoading, brainRuntimeDetails, expandedBrainEventId, filteredBrainEvents, loadBrainRuntimeDetail]);

  const visibleTaskRuns = useMemo(() => {
    const entries = taskRunsResponse?.entries ?? [];
    if (taskRunStatusFilter === "all") return entries;
    return entries.filter((entry) => (entry.status || "").toLowerCase() === taskRunStatusFilter);
  }, [taskRunStatusFilter, taskRunsResponse]);

  const visibleProcesses = useMemo(() => {
    const entries = processesResponse?.entries ?? [];
    return entries.filter((entry) => {
      if (processStatusFilter === "all") return true;
      if (processStatusFilter === "running") return entry.is_active || (entry.status || "").toLowerCase() === "running";
      if (processStatusFilter === "finished") return entry.is_terminal || ["completed", "succeeded"].includes((entry.status || "").toLowerCase());
      if (processStatusFilter === "failed") return (entry.status || "").toLowerCase() === "failed";
      return true;
    });
  }, [processStatusFilter, processesResponse]);

  const visibleFileEvents = filesResponse?.entries ?? [];

  const selectedTaskRunSummary = useMemo(
    () => visibleTaskRuns.find((entry) => entry.id === selectedTaskRunId) ?? visibleTaskRuns[0] ?? null,
    [selectedTaskRunId, visibleTaskRuns],
  );
  const selectedTaskRunDetail = selectedTaskRunSummary ? taskRunDetails[selectedTaskRunSummary.id] ?? null : null;
  const selectedTaskRunSteps = selectedTaskRunSummary ? taskRunSteps[selectedTaskRunSummary.id] ?? null : null;
  const selectedTaskRunRecoveryState = selectedTaskRunDetail ?? selectedTaskRunSummary;
  const selectedTaskRunSchedulePlan = useMemo(
    () => extractRunSchedulePlan(selectedTaskRunDetail),
    [selectedTaskRunDetail],
  );
  const selectedTaskRunStepMap = useMemo(
    () => new Map((selectedTaskRunSchedulePlan?.steps ?? []).map((step) => [step.stepId, step])),
    [selectedTaskRunSchedulePlan],
  );
  const selectedTaskRunHandoffs = useMemo(
    () => extractRunHandoffs(selectedTaskRunDetail),
    [selectedTaskRunDetail],
  );
  const selectedTaskRunVisibleEvents = useMemo(
    () => selectedTaskRunDetail?.events.slice(-TASK_RUN_EVENT_RENDER_LIMIT) ?? [],
    [selectedTaskRunDetail],
  );
  const selectedTaskRunVisibleSteps = useMemo(
    () => selectedTaskRunSteps?.steps.slice(-TASK_RUN_STEP_RENDER_LIMIT) ?? [],
    [selectedTaskRunSteps],
  );
  const selectedTaskRunEventTotalCount =
    selectedTaskRunDetail?.event_count ?? selectedTaskRunDetail?.events.length ?? 0;
  const selectedTaskRunStepTotalCount =
    selectedTaskRunSteps?.counts.total ?? selectedTaskRunSteps?.steps.length ?? 0;
  const selectedTaskRunHiddenEventCount = Math.max(
    0,
    selectedTaskRunEventTotalCount - selectedTaskRunVisibleEvents.length,
  );
  const selectedTaskRunHiddenStepCount = Math.max(
    0,
    selectedTaskRunStepTotalCount - selectedTaskRunVisibleSteps.length,
  );

  useEffect(() => {
    if (!visibleTaskRuns.length) {
      if (selectedTaskRunId !== null) {
        setSelectedTaskRunId(null);
      }
      return;
    }
    if (selectedTaskRunId === null || !visibleTaskRuns.some((entry) => entry.id === selectedTaskRunId)) {
      setSelectedTaskRunId(visibleTaskRuns[0].id);
    }
  }, [selectedTaskRunId, visibleTaskRuns]);

  const loadTaskRunDetail = useCallback(
    async (taskRunId: number) => {
      if (taskRunDetails[taskRunId] || taskRunDetailLoading[taskRunId]) return;
      setTaskRunDetailLoading((current) => ({ ...current, [taskRunId]: true }));
      setTaskRunDetailErrors((current) => {
        const next = { ...current };
        delete next[taskRunId];
        return next;
      });
      try {
        const detail = await api.getTaskRunDetail(taskRunId, TASK_RUN_EVENT_RENDER_LIMIT);
        setTaskRunDetails((current) => ({ ...current, [taskRunId]: detail }));
      } catch (nextError) {
        setTaskRunDetailErrors((current) => ({
          ...current,
          [taskRunId]: nextError instanceof Error ? nextError.message : "Failed to load task run detail",
        }));
      } finally {
        setTaskRunDetailLoading((current) => ({ ...current, [taskRunId]: false }));
      }
    },
    [taskRunDetailLoading, taskRunDetails],
  );

  const loadTaskRunSteps = useCallback(
    async (taskRunId: number) => {
      if (taskRunSteps[taskRunId] || taskRunStepLoading[taskRunId]) return;
      setTaskRunStepLoading((current) => ({ ...current, [taskRunId]: true }));
      setTaskRunStepErrors((current) => {
        const next = { ...current };
        delete next[taskRunId];
        return next;
      });
      try {
        const detail = await api.getMonitorTaskRunSteps(taskRunId, TASK_RUN_STEP_RENDER_LIMIT);
        setTaskRunSteps((current) => ({ ...current, [taskRunId]: detail }));
      } catch (nextError) {
        setTaskRunStepErrors((current) => ({
          ...current,
          [taskRunId]: nextError instanceof Error ? nextError.message : "Failed to load task run steps",
        }));
      } finally {
        setTaskRunStepLoading((current) => ({ ...current, [taskRunId]: false }));
      }
    },
    [taskRunStepLoading, taskRunSteps],
  );

  const selectedTaskRunCanResume = useMemo(() => {
    if (!selectedTaskRunSummary) return false;
    if ((selectedTaskRunSummary.status || "").toLowerCase() !== "running") return false;
    if (hasActiveRecoveryLease(selectedTaskRunRecoveryState)) return false;
    return RESUMABLE_TASK_RUN_KINDS.has(selectedTaskRunSummary.run_kind || "");
  }, [selectedTaskRunRecoveryState, selectedTaskRunSummary]);

  const resumeTaskRun = useCallback(
    async (summary: MonitorTaskRunSummary) => {
      if (taskRunResumeLoading[summary.id]) return;
      setTaskRunResumeLoading((current) => ({ ...current, [summary.id]: true }));
      setTaskRunResumeErrors((current) => {
        const next = { ...current };
        delete next[summary.id];
        return next;
      });
      setTaskRunResumeMessages((current) => {
        const next = { ...current };
        delete next[summary.id];
        return next;
      });

      try {
        const response: TaskRunResumeResponse = await api.resumeTaskRun(summary.id);
        const detail = response.detail;
        setTaskRunDetails((current) => ({ ...current, [summary.id]: detail }));
        setTaskRunsResponse((current) =>
          mergeMonitorTaskRunResponse(
            current,
            mergeTaskRunDetailIntoMonitorSummary(summary, detail),
            historyRange,
          ),
        );
        setTaskRunResumeMessages((current) => ({
          ...current,
          [summary.id]: response.message,
        }));
      } catch (nextError) {
        setTaskRunResumeErrors((current) => ({
          ...current,
          [summary.id]: nextError instanceof Error ? nextError.message : "Failed to resume task run",
        }));
      } finally {
        setTaskRunResumeLoading((current) => ({ ...current, [summary.id]: false }));
      }
    },
    [historyRange, taskRunResumeLoading],
  );

  const historyBuckets = useMemo(() => buildHourlyBuckets(brainEvents, historyRange), [brainEvents, historyRange]);
  const brainTimelineBuckets = useMemo(
    () => buildBrainTimelineBuckets(brainEvents, brainTimelineUnit),
    [brainEvents, brainTimelineUnit],
  );
  const usageBuckets = useMemo(
    () =>
      usage?.buckets.map((bucket) => ({
        label: bucket.label,
        tokens: bucket.total_tokens,
        cost: bucket.estimated_cost_usd,
      })) ?? buildUsageBuckets(overview, historyRange),
    [historyRange, overview, usage],
  );
  const tokenBuckets = useMemo(
    () =>
      usageBuckets.map((bucket) => ({
        label: bucket.label,
        value: bucket.tokens,
      })),
    [usageBuckets],
  );
  const costBuckets = useMemo(
    () =>
      usageBuckets.map((bucket) => ({
        label: bucket.label,
        value: bucket.cost,
        accent: "linear-gradient(180deg, #7dd3fc, #0ea5e9)",
      })),
    [usageBuckets],
  );
  const todayTokens = usage?.totals.day.total_tokens ?? usageTokenTotal(overview, "day");
  const weekTokens = usage?.totals.week.total_tokens ?? usageTokenTotal(overview, "week");
  const monthTokens = usage?.totals.month.total_tokens ?? usageTokenTotal(overview, "month");
  const todayCost = usage?.totals.day.estimated_cost_usd ?? usageCostTotal(overview, "day");
  const weekCost = usage?.totals.week.estimated_cost_usd ?? usageCostTotal(overview, "week");
  const monthCost = usage?.totals.month.estimated_cost_usd ?? usageCostTotal(overview, "month");
  const taskRunCounts = useMemo(() => {
    const counts = { total: 0, running: 0, completed: 0, failed: 0 };
    for (const entry of taskRunsResponse?.entries ?? []) {
      counts.total += 1;
      const status = (entry.status || "").toLowerCase();
      if (status === "running") counts.running += 1;
      if (status === "completed") counts.completed += 1;
      if (status === "failed") counts.failed += 1;
    }
    return counts;
  }, [taskRunsResponse]);

  const modelPrimary = modelRows[0]?.name ?? config?.global_llm?.default_model ?? "unknown";
  const autonomyScore = overview
    ? clamp(
        Math.round(
          ((1 - overview.usage_window.tool_errors / Math.max(overview.usage_window.tool_calls || 1, 1)) * 0.55 +
            Math.min(overview.usage_window.tool_calls / Math.max(overview.usage_window.llm_calls || 1, 1), 1) * 0.45) *
            100,
        ),
        0,
        100,
      )
    : 0;
  const contextWindow = resolveConfiguredContextWindow(config, modelRows[0]?.name ?? config?.global_llm?.default_model);
  const contextUsage = overview ? clamp((overview.usage_window.total_tokens / contextWindow) * 100, 0, 100) : 0;
  const contextCompactionEntries = contextCompactionsResponse?.entries ?? overview?.recent_compactions ?? [];
  const latestCompaction = contextCompactionEntries[0];
  const pendingApprovalCount = approvalQueueResponse?.counts.pending ?? overview?.system.stats.approval_queue_pending ?? 0;
  const approvalQueueTotal = approvalQueueResponse?.counts.all ?? overview?.system.stats.approval_queue_total ?? 0;
  const overviewFlowGraph = useMemo(
    () =>
      buildFlowTopologyGraph({
        overview,
        networkEntries,
        agentDirectory,
        modelPrimary,
        connectionState,
        pendingApprovalCount,
        approvalQueueTotal,
        compact: true,
      }),
    [agentDirectory, approvalQueueTotal, connectionState, modelPrimary, networkEntries, overview, pendingApprovalCount],
  );
  const detailFlowGraph = useMemo(
    () =>
      buildFlowTopologyGraph({
        overview,
        networkEntries,
        agentDirectory,
        modelPrimary,
        connectionState,
        pendingApprovalCount,
        approvalQueueTotal,
        compact: false,
      }),
    [agentDirectory, approvalQueueTotal, connectionState, modelPrimary, networkEntries, overview, pendingApprovalCount],
  );

  const securityChecks = useMemo(() => {
    if (!overview) return [] as Array<{ label: string; pass: boolean; detail: string }>;
    return [
      {
        label: "LLM routing online",
        pass: overview.system.features.llm_enabled,
        detail: "Global LLM configuration is reachable from the monitor snapshot.",
      },
      {
        label: "Realtime transport enabled",
        pass: overview.system.features.websocket_enabled,
        detail: "Realtime WS support is exposed by Catown.",
      },
      {
        label: "Tooling enabled",
        pass: overview.system.features.tools_enabled,
        detail: "Tool execution pipeline is turned on.",
      },
      {
        label: "Error pressure under control",
        pass: overview.usage_window.tool_errors <= Math.max(1, Math.floor(overview.usage_window.tool_calls * 0.2)),
        detail: `${formatNumber(overview.usage_window.tool_errors)} tool errors in the current runtime window.`,
      },
      {
        label: "Memory service available",
        pass: overview.system.features.memory_enabled,
        detail: "Project memory / workspace context service is enabled.",
      },
    ];
  }, [overview]);

  const passedChecks = securityChecks.filter((check) => check.pass).length;
  const failedChecks = securityChecks.length - passedChecks;
  const securityScore = securityChecks.length ? Math.round((passedChecks / securityChecks.length) * 100) : 0;
  const overviewRuntimeStats = useMemo(
    () => [
      {
        id: "spending",
        icon: "$",
        label: "Spending",
        value: formatCost(overview?.usage_window.estimated_cost_usd),
        sub: `input ${formatNumber(overview?.usage_window.input_tokens)} · output ${formatNumber(overview?.usage_window.output_tokens)}`,
      },
      {
        id: "model",
        icon: "AI",
        label: "Model",
        value: modelPrimary,
        sub: `${formatNumber(modelRows.length)} distinct models observed`,
      },
      {
        id: "tokens",
        icon: "Tok",
        label: "Tokens",
        value: formatNumber(overview?.usage_window.total_tokens),
        sub: `context window usage ${formatPercent(contextUsage, 1)}`,
      },
      {
        id: "sessions",
        icon: "Chat",
        label: "Sessions",
        value: formatNumber(overview?.system.stats.chatrooms),
        sub: `${formatNumber(clusters.length)} hot chats in runtime window`,
      },
      {
        id: "reliability",
        icon: "OK",
        label: "Reliability",
        value: formatPercent(securityScore),
        sub: `${passedChecks}/${securityChecks.length || 0} checks passed`,
      },
      {
        id: "approvals",
        icon: "Q",
        label: "Approvals",
        value: formatNumber(pendingApprovalCount),
        sub: `${formatNumber(approvalQueueTotal)} queued decisions`,
      },
    ],
    [
      approvalQueueTotal,
      clusters.length,
      contextUsage,
      modelPrimary,
      modelRows.length,
      overview,
      passedChecks,
      pendingApprovalCount,
      securityChecks.length,
      securityScore,
    ],
  );

  const approvalsPending = useMemo(
    () => approvalQueueResponse?.entries.filter((item) => item.status === "pending") ?? [],
    [approvalQueueResponse],
  );
  const approvalsHistory = useMemo(
    () => approvalQueueResponse?.entries.filter((item) => item.status !== "pending") ?? [],
    [approvalQueueResponse],
  );
  const approvalAuditEntries = approvalAuditResponse?.entries ?? [];

  const limitsRows = useMemo(() => {
    if (!overview) return [] as Array<{ label: string; value: number; max: number; detail: string }>;
    return [
      {
        label: "Provider token window",
        value: overview.usage_window.total_tokens,
        max: contextWindow,
        detail: "Temporary proxy using current context-window ceiling until OTLP rate metrics are wired.",
      },
      {
        label: "Tool action window",
        value: overview.usage_window.tool_calls,
        max: 300,
        detail: "Temporary cap for the last monitor window.",
      },
      {
        label: "LLM request window",
        value: overview.usage_window.llm_calls,
        max: 120,
        detail: "Temporary cap used for the copied ClawMetry UX skeleton.",
      },
    ];
  }, [contextWindow, overview]);

  const visibleNetworkEntries = useMemo(() => {
    let entries = networkEntries;
    if (!showInternalNetwork) {
      entries = entries.filter(
        (entry) =>
          !isFrontendBackendTraffic(entry) &&
          !isMonitorPageNetwork(entry) &&
          !isFrontendBackendHeartbeat(entry) &&
          !isFrontendMetaRequest(entry),
      );
    }
    entries = entries.filter((entry) => !isLegacyBackendLlmAppEvent(entry));
    return entries
      .filter((entry) => entry.aggregated === false || !entry.flow_id)
      .slice(0, MONITOR_NETWORK_RENDER_LIMIT);
  }, [networkEntries, showInternalNetwork]);

  async function refreshMonitor() {
    await loadMonitor(true);
  }

  async function refreshProcesses() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorProcesses();
      setProcessesResponse(response);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load monitor processes");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshUsage() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorUsage(historyRange);
      setUsage(response);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load monitor usage");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshTaskRuns() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorTaskRuns(historyRange);
      setTaskRunsResponse(response);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load monitor task runs");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshContextCompactions() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorContextCompactions(160);
      setContextCompactionsResponse(response);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load context compactions");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshApprovalQueue() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorApprovalQueue("all", 120);
      setApprovalQueueResponse(response);
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load monitor approval queue");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshApprovalAudit() {
    setRefreshing(true);
    try {
      const response = await api.getMonitorApprovalAudit(approvalAuditFilter, 240);
      setApprovalAuditResponse((current) => mergeMonitorApprovalAudit(current, response));
      setError("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load monitor approval audit");
    } finally {
      setRefreshing(false);
    }
  }

  async function refreshLogs() {
    try {
      const response = await api.getMonitorLogs();
      setLogEntries(mergeMonitorLogs([], response.entries));
      logCursorRef.current = Math.max(logCursorRef.current, response.latest_id);
      setLogStreamState((current) => (current === "connected" ? current : "connecting"));
    } catch {
      setLogStreamState("disconnected");
    }
  }

  async function refreshNetwork() {
    try {
      const response = await api.getMonitorNetwork(300, networkCategory, networkFilter, showInternalNetwork);
      setNetworkEntries(mergeMonitorNetwork([], response.entries));
      networkCursorRef.current = Math.max(networkCursorRef.current, response.latest_id);
      setNetworkStreamState((current) => (current === "connected" ? current : "connecting"));
    } catch {
      setNetworkStreamState("disconnected");
      // Keep the current snapshot on fetch failures.
    }
  }

  async function refreshFiles() {
    try {
      const response = await api.getMonitorFiles(200, fileToolFilter, fileFilter);
      setFilesResponse(response);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load file monitor events");
    }
  }

  async function decideApprovalQueueItem(item: MonitorApprovalQueueEntry, decision: "approve" | "reject") {
    setApprovalQueueActionLoading((current) => ({ ...current, [item.id]: true }));
    setApprovalQueueActionErrors((current) => {
      const next = { ...current };
      delete next[item.id];
      return next;
    });

    try {
      if (decision === "approve") {
        const updated = await api.approveApprovalQueueItem(item.id, {
          note: item.summary ? `Approved from monitor: ${item.summary}` : "Approved from monitor.",
          resolved_by: "monitor",
        });
        setApprovalQueueActionMessages((current) => ({
          ...current,
          [item.id]: updated.status === "approved" ? "Approved" : "Updated",
        }));
      } else {
        const updated = await api.rejectApprovalQueueItem(item.id, {
          note: item.summary ? `Rejected from monitor: ${item.summary}` : "Rejected from monitor.",
          resolved_by: "monitor",
        });
        setApprovalQueueActionMessages((current) => ({
          ...current,
          [item.id]: updated.status === "rejected" ? "Rejected" : "Updated",
        }));
      }
      await Promise.all([loadMonitor(true), refreshApprovalQueue(), refreshApprovalAudit()]);
    } catch (nextError) {
      setApprovalQueueActionErrors((current) => ({
        ...current,
        [item.id]: nextError instanceof Error ? nextError.message : "Failed to update approval queue item",
      }));
    } finally {
      setApprovalQueueActionLoading((current) => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
    }
  }

  const pageClass = (pageId: string, layoutClass: string) =>
    `page ${layoutClass} ${activePage === pageId ? "active" : ""}`;

  return (
    <div className="monitor-dashboard">
      <header className="nav">
        <div className="nav-brand">
          <div className="brand-badge">CM</div>
          <div>
            <div className="nav-title">
              <h1>
                Catown Monitor <span>ClawMetry UX</span>
              </h1>
            </div>
            <div className="nav-subtitle">
              Standalone backstage dashboard. Main Catown stays business-first; observability lives here.
            </div>
          </div>
        </div>
        <div className="nav-spacer" />
        <div className="nav-actions">
          <span className={`status-pill ${connectionState === "connected" ? "status-pill--live" : "status-pill--offline"}`}>
            {connectionState === "connected" ? "Realtime connected" : "Monitor offline"}
          </span>
          <span className="version-badge">Updated {formatTimeAgo(overview?.captured_at)}</span>
          <a className="fake-link-btn" href="/" target="_blank" rel="noreferrer">
            Open Catown
          </a>
          <a className="fake-link-btn" href="/docs" target="_blank" rel="noreferrer">
            API Docs
          </a>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>

      <div className="nav nav--tabs">
        <div className="nav-tabs">
          {ALL_PAGES.map((page) => (
            <button
              key={page.id}
              type="button"
              className={`nav-tab ${activePage === page.id ? "active" : ""}`}
              onClick={() => setActivePage(page.id)}
            >
              {page.label}
            </button>
          ))}
        </div>
      </div>

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
      {loading && !overview ? <div className="page active"><div className="empty-state">Loading Catown monitor...</div></div> : null}

      <section className={pageClass("overview", "page--detail-full")} id="page-overview">
        <div className="overview-summary-row">
          <div className="card overview-hero-card">
            <div>
              <div className="card-title">How independent is your agent?</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                <span className="card-value">{autonomyScore}%</span>
                <span className={`tag ${autonomyScore >= 75 ? "tag--success" : autonomyScore >= 45 ? "tag--warning" : "tag--error"}`}>
                  {autonomyScore >= 75 ? "healthy" : autonomyScore >= 45 ? "watch" : "needs work"}
                </span>
              </div>
              <div className="card-sub">Weighted from tool success, tool adoption and recent runtime resilience.</div>
            </div>
            <div className="overview-hero-card__meta">
              <div className="small-note">Last monitor window</div>
              <div className="mono" style={{ fontSize: 13, marginTop: 6 }}>
                {formatNumber(overview?.usage_window.llm_calls)} llm / {formatNumber(overview?.usage_window.tool_calls)} tools
              </div>
              <div className="small-note" style={{ marginTop: 6 }}>
                {formatNumber(overview?.usage_window.tool_errors)} tool errors · {formatTimeAgo(overview?.captured_at)}
              </div>
            </div>
          </div>

          <div className="card overview-runtime-window-card">
            <div className="card-title">Runtime Cards</div>
            <div className="refresh-bar overview-runtime-window-card__bar" style={{ marginBottom: 0 }}>
              <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
                ↻
              </button>
              <span className="pulse" />
              <span className="live-badge">Live</span>
              <span className="refresh-time">Live subscription · Runtime cards window {overview?.usage_window.runtime_cards_considered ?? 0}</span>
            </div>
            <div className="stats-footer overview-runtime-window-card__stats">
              {overviewRuntimeStats.map((item) => (
                <div key={item.id} className="stats-footer-item">
                  <span className="stats-footer-icon">{item.icon}</span>
                  <div>
                    <div className="stats-footer-label">{item.label}</div>
                    <div className="stats-footer-value">{item.value}</div>
                    <div className="stats-footer-sub">{item.sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="overview-split">
          <div>
            <div className="overview-flow-pane">
              <div className="flow-container" id="overview-flow-container">
                <FlowTopologyView graph={overviewFlowGraph} compact />
              </div>
            </div>
            <div className="system-health-panel">
              <div className="section-title">System Health</div>
              <div className="health-group">
                <div className="health-label">Services</div>
                <div className="badge-row">
                  {Object.entries(overview?.system.features ?? {}).map(([name, enabled]) => (
                    <span key={name} className={`tag ${enabled ? "tag--success" : "tag--error"}`}>
                      {name}
                    </span>
                  ))}
                </div>
              </div>
              <div className="health-group">
                <div className="health-label">Workspace</div>
                <div className="badge-row">
                  <span className="tag">{formatNumber(overview?.system.stats.projects)} projects</span>
                  <span className="tag">{formatNumber(overview?.system.stats.visible_chats)} visible chats</span>
                  <span className="tag">{formatNumber(overview?.system.stats.runtime_cards)} runtime cards</span>
                  <span className="tag">{formatNumber(overview?.system.stats.approval_queue_pending)} pending approvals</span>
                </div>
              </div>
              <div className="health-group">
                <div className="health-label">Collaboration</div>
                <div className="metric-list">
                  <div className="metric-row">
                    <span>Active collaborators</span>
                    <strong>{formatNumber(overview?.system.collaboration.active_collaborators)}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Pending tasks</span>
                    <strong>{formatNumber(overview?.system.collaboration.pending_tasks)}</strong>
                  </div>
                  <div className="metric-row">
                    <span>Monitor status</span>
                    <strong>{overview?.system.collaboration.status ?? "--"}</strong>
                  </div>
                </div>
              </div>
              <div className="health-group">
                <div className="health-label">Security posture</div>
                <div className="badge-row">
                  {securityChecks.map((check) => (
                    <span key={check.label} className={`tag ${check.pass ? "tag--success" : "tag--warning"}`}>
                      {check.label}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="overview-side-stack">
            <div className="card">
              <div className="section-title">Active Tasks</div>
              <div className="feed-list">
                {(overview?.recent_runtime.slice(0, 8) ?? []).map((item) => (
                  <div key={item.id} className="feed-item">
                    <div className={`feed-badge feed-badge--${runtimeTone(item.type, item.success)}`}>{runtimeLabel(item.type)}</div>
                    <div className="feed-body">
                      <div className="feed-head">
                        <strong>{item.title}</strong>
                        <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                      </div>
                      <div className="feed-meta">
                        <span>{item.project_name || "Standalone"}</span>
                        <span>{item.chat_title}</span>
                        {item.stage ? <span>{item.stage}</span> : null}
                      </div>
                      {item.preview ? <div className="feed-preview">{item.preview}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="card">
              <div className="section-title">Brain Preview</div>
              <div className="feed-list">
                {brainEvents.slice(0, 10).map((event) => (
                  <div key={event.id} className="simple-row">
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 4 }}>
                      <strong>{event.label}</strong>
                      <span className="small-note">{formatTimeAgo(event.createdAt)}</span>
                    </div>
                    <div className="small-note" style={{ marginBottom: 6 }}>
                      {event.source} · {event.category}
                    </div>
                    <div className="muted-block">{event.detail}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className={pageClass("flow", "page--detail-full")} id="page-flow">
        <div className="flow-stats">
          <div className="flow-stat">
            <span className="flow-stat-label">Messages / min</span>
            <span className="flow-stat-value">{formatNumber(overview?.recent_messages.length)}</span>
          </div>
          <div className="flow-stat">
            <span className="flow-stat-label">Actions Taken</span>
            <span className="flow-stat-value">{formatNumber(overview?.recent_runtime.length)}</span>
          </div>
          <div className="flow-stat">
            <span className="flow-stat-label">Active Tools</span>
            <span className="flow-stat-value">{formatNumber(overview?.usage_window.top_tools.length)}</span>
          </div>
          <div className="flow-stat">
            <span className="flow-stat-label">Tokens Used</span>
            <span className="flow-stat-value">{formatNumber(overview?.usage_window.total_tokens)}</span>
          </div>
        </div>

        <div className="card" style={{ padding: 8, marginBottom: 16 }}>
          <FlowTopologyView graph={detailFlowGraph} />
        </div>

        <div className="section-title">Runtime Feed</div>
        <div className="feed-list">
          {(overview?.recent_runtime ?? []).map((item) => (
            <div key={item.id} className="feed-item">
              <div className={`feed-badge feed-badge--${runtimeTone(item.type, item.success)}`}>{runtimeLabel(item.type)}</div>
              <div className="feed-body">
                <div className="feed-head">
                  <strong>{item.title}</strong>
                  <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                </div>
                <div className="feed-meta">
                  <span>{item.project_name || "Standalone"}</span>
                  <span>{item.chat_title}</span>
                  {item.agent ? <span>{item.agent}</span> : null}
                  {item.model ? <span>{item.model}</span> : null}
                  {item.tool_name ? <span>{item.tool_name}</span> : null}
                  {item.duration_ms ? <span>{formatDuration(item.duration_ms)}</span> : null}
                  {item.tokens_in || item.tokens_out ? <span>{formatNumber(item.tokens_in)} / {formatNumber(item.tokens_out)} tok</span> : null}
                </div>
                {runtimePrimaryPreview(item) ? <div className="feed-preview">{runtimePrimaryPreview(item)}</div> : null}
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className={pageClass("network", "page--detail-full")} id="page-network">
        <div className="refresh-bar" style={{ width: "100%" }}>
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>Network Transport</h2>
          <span className={`status-pill ${networkStreamState === "connected" ? "status-pill--live" : "status-pill--offline"}`}>
            {networkStreamState === "connected"
              ? "Network stream live"
              : networkStreamState === "connecting"
                ? "Network stream connecting"
                : "Network stream offline"}
          </span>
          <button type="button" className="refresh-btn" onClick={() => void refreshNetwork()}>
            Refresh
          </button>
        </div>
        <div className="network-toolbar">
          <p className="small-note network-toolbar__note">
            Debug view only. No aggregation; each record is shown as one title line plus one raw HTTP wire block.
          </p>
          <div className="network-toolbar__controls">
            <label className="small-note network-toolbar__toggle">
              <input
                type="checkbox"
                checked={showInternalNetwork}
                onChange={(event) => setShowInternalNetwork(event.target.checked)}
              />
              Show heartbeat / internal requests
            </label>
            <input
              type="text"
              className="search-input network-toolbar__search"
              placeholder="Filter by host, path, peer, raw text..."
              value={networkFilter}
              onChange={(event) => setNetworkFilter(event.target.value)}
            />
          </div>
        </div>
        {visibleNetworkEntries.length > 0 ? (
          <div className="feed-list" style={{ gap: 8 }}>
            {visibleNetworkEntries.map((entry) => {
              const direction = activeDirection(entry);
              const { from: directionFrom, to: directionTo } = parseDirection(direction);
              const fromVisual = getDirectionVisual(directionFrom);
              const toVisual = getDirectionVisual(directionTo);
              const flowColor = entry.flow_id ? hashFlowColor(entry.flow_id) : fromVisual.color;
              return (
                <details
                  key={entry.id}
                  className="card network-log-card"
                  style={{
                    marginBottom: 8,
                    padding: "10px 12px",
                    width: "100%",
                    minWidth: 0,
                    boxSizing: "border-box",
                    borderLeft: entry.flow_id ? `4px solid ${flowColor}` : undefined,
                  }}
                >
                  <summary className="network-log-card__summary" style={{ cursor: "pointer", listStyle: "none", width: "100%", minWidth: 0 }}>
                    <div className="network-log-card__head">
                      <div className="network-log-card__title">
                        <span
                          title={direction}
                          className="network-log-card__icon"
                          style={{
                            background: `${flowColor}1a`,
                            color: flowColor,
                          }}
                        >
                          <fromVisual.Icon size={16} strokeWidth={2.2} />
                        </span>
                        <strong>
                          {direction}
                          {" · "}
                          {entry.method || "NET"} {entry.path || entry.url}
                          {" · "}
                          {entry.protocol || "unknown"}
                          {" · "}
                          {formatBytes(entry.request_bytes)} out / {formatBytes(entry.response_bytes)} in
                          {" · "}
                          {formatDuration(entry.duration_ms)}
                          {entry.status_code ? ` · ${entry.status_code}` : ""}
                          {entry.flow_id ? ` · ${entry.flow_id}${entry.flow_seq ? `#${entry.flow_seq}` : ""}` : ""}
                        </strong>
                      </div>
                      <span className="small-note mono" title={entry.created_at}>{preciseSystemTime(entry.created_at)}</span>
                    </div>
                  </summary>
                  <NetworkRawDump entry={entry} />
                </details>
              );
            })}
          </div>
        ) : (
          <div className="muted-block">No network events captured for the current filter yet.</div>
        )}
      </section>

      <section className={pageClass("files", "page--detail-full")} id="page-files">
        {activePage === "files" ? (
          <>
            <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
              <div>
                <div className="section-title">Files</div>
                <div className="section-subtitle">Agent file tool activity captured from persisted runtime cards.</div>
              </div>
              <button type="button" className="refresh-btn" onClick={() => void refreshFiles()}>
                Refresh
              </button>
            </div>

            <div className="grid" style={{ marginBottom: 16 }}>
              <div className="card">
                <div className="card-title">Events</div>
                <div className="card-value">{formatNumber(filesResponse?.counts.total)}</div>
                <div className="card-sub">file tool calls in current window</div>
              </div>
              <div className="card">
                <div className="card-title">Read / Search</div>
                <div className="card-value">{formatNumber((filesResponse?.counts.reads ?? 0) + (filesResponse?.counts.searches ?? 0))}</div>
                <div className="card-sub">read_file plus search_files</div>
              </div>
              <div className="card">
                <div className="card-title">Writes</div>
                <div className="card-value">{formatNumber(filesResponse?.counts.writes)}</div>
                <div className="card-sub">write_file and delete_file</div>
              </div>
              <div className="card">
                <div className="card-title">Paths</div>
                <div className="card-value">{formatNumber(filesResponse?.counts.unique_paths)}</div>
                <div className="card-sub">{formatNumber(filesResponse?.counts.errors)} errors</div>
              </div>
            </div>

            <div className="filter-row" style={{ marginBottom: 12 }}>
              {(["all", "read_file", "write_file", "list_files", "search_files", "delete_file"] as const).map((toolName) => (
                <button
                  key={toolName}
                  type="button"
                  className={`time-btn ${fileToolFilter === toolName ? "active" : ""}`}
                  onClick={() => setFileToolFilter(toolName)}
                >
                  {toolName === "all" ? "all" : toolName.replace("_file", "")}
                </button>
              ))}
              <input
                className="activity-filter-input"
                type="search"
                value={fileFilter}
                onChange={(event) => setFileFilter(event.target.value)}
                placeholder="Filter path, agent, project..."
                aria-label="Filter file activity"
              />
              {fileFilter ? (
                <button type="button" className="time-btn" onClick={() => setFileFilter("")}>
                  Clear
                </button>
              ) : null}
            </div>

            <div className="grid" style={{ marginBottom: 16 }}>
              <div className="card">
                <SectionTitle title="By Agent" />
                <div className="simple-list">
                  {(filesResponse?.by_agent ?? []).slice(0, 10).map((item) => (
                    <div key={item.agent} className="metric-row">
                      <span className="file-agent-chip">{item.agent}</span>
                      <strong>{formatNumber(item.count)}</strong>
                    </div>
                  ))}
                  {filesResponse?.by_agent.length ? null : <div className="muted-block">No file activity by agent yet.</div>}
                </div>
              </div>
              <div className="card">
                <SectionTitle title="By Tool" />
                <div className="simple-list">
                  {(filesResponse?.by_tool ?? []).map((item) => (
                    <div key={item.tool_name} className="metric-row">
                      <span>{item.tool_name}</span>
                      <strong>{formatNumber(item.count)}</strong>
                    </div>
                  ))}
                  {filesResponse?.by_tool.length ? null : <div className="muted-block">No file tool calls captured.</div>}
                </div>
              </div>
            </div>

            {visibleFileEvents.length > 0 ? (
              <div className="feed-list">
                {visibleFileEvents.map((entry) => {
                  const tone = fileActionTone(entry);
                  return (
                    <details key={entry.id} className="file-event-card">
                      <summary className="file-event-card__summary">
                        <span className="file-event-card__icon" title={entry.tool_name}>
                          <FileText size={16} strokeWidth={2.2} />
                        </span>
                        <div className="file-event-card__main">
                          <div className="feed-head">
                            <strong className="mono">{entry.file_path || "(path unavailable)"}</strong>
                            <span className="small-note">{formatTimeAgo(entry.created_at)}</span>
                          </div>
                          <div className="feed-meta">
                            <span className={`feed-badge feed-badge--${tone}`}>{fileActionLabel(entry.action)}</span>
                            <span>{entry.tool_name}</span>
                            {entry.agent ? <span className="file-agent-chip">{entry.agent}</span> : null}
                            <span>{entry.project_name || "Standalone"}</span>
                            <span>{entry.chat_title}</span>
                            {entry.duration_ms ? <span>{formatDuration(entry.duration_ms)}</span> : null}
                            {entry.turn ? <span>turn {entry.turn}</span> : null}
                          </div>
                          {entry.result_preview ? <div className="feed-preview">{entry.result_preview}</div> : null}
                        </div>
                      </summary>
                      <div className="file-event-card__details">
                        <pre className="monitor-pre">{formatRawMonitorValue({
                          arguments: entry.arguments ?? entry.arguments_preview,
                          result_preview: entry.result_preview,
                          result_size: entry.result_size,
                          runtime_message_id: entry.runtime_message_id,
                          client_turn_id: entry.client_turn_id,
                          status: entry.status,
                          blocked: entry.blocked,
                          success: entry.success,
                        })}</pre>
                      </div>
                    </details>
                  );
                })}
              </div>
            ) : (
              <div className="muted-block">No Agent file read/write activity captured for the current filter yet.</div>
            )}
          </>
        ) : null}
      </section>

      <section className={pageClass("usage", "page--viz-readable")} id="page-usage">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshUsage()} disabled={refreshing}>
            ↻ Refresh
          </button>
          <button type="button" className="refresh-btn" disabled>
            TODO Export CSV
          </button>
        </div>

        <div className="grid">
          <div className="card">
            <div className="card-title">Today</div>
            <div className="card-value">{formatNumber(todayTokens)}</div>
            <div className="card-sub">{formatCost(todayCost)}</div>
          </div>
          <div className="card">
            <div className="card-title">This Week</div>
            <div className="card-value">{formatNumber(weekTokens)}</div>
            <div className="card-sub">{formatCost(weekCost)}</div>
          </div>
          <div className="card">
            <div className="card-title">This Month</div>
            <div className="card-value">{formatNumber(monthTokens)}</div>
            <div className="card-sub">{formatCost(monthCost)}</div>
          </div>
          <div className="card">
            <div className="card-title">Trend</div>
            <div className="card-value">{overview && overview.usage_window.tool_errors > 0 ? "Watch" : "Stable"}</div>
            <div className="card-sub">Tool error pressure and runtime activity trend</div>
          </div>
        </div>

        <div className="refresh-bar" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <SectionTitle
            title="Token Usage"
            subtitle={`Persisted runtime cards bucketed by system time${usage ? ` · scanned ${formatNumber(usage.scanned_runtime_cards)} LLM calls` : ""}.`}
          />
          <div className="inline-actions">
            {(["1h", "6h", "24h", "7d", "30d"] as const).map((range) => (
              <button
                key={range}
                type="button"
                className={`time-btn ${historyRange === range ? "active" : ""}`}
                onClick={() => setHistoryRange(range)}
              >
                {range}
              </button>
            ))}
          </div>
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <BarChart items={tokenBuckets} />
        </div>

        <SectionTitle title="Estimated Cost Over Time" subtitle="Calculated from bucketed input/output tokens and current pricing." />
        <div className="card" style={{ marginBottom: 16 }}>
          <BarChart items={costBuckets} />
        </div>

        <SectionTitle title="Cost Breakdown" />
        <div className="card" style={{ marginBottom: 16 }}>
          <table className="usage-table">
            <thead>
              <tr>
                <th>Bucket</th>
                <th>Value</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Input tokens</td>
                <td>{formatNumber(overview?.usage_window.input_tokens)}</td>
                <td>{formatCost((overview?.usage_window.input_tokens ?? 0) / 1000 * (overview?.usage_window.pricing.input_per_1k ?? 0))}</td>
              </tr>
              <tr>
                <td>Output tokens</td>
                <td>{formatNumber(overview?.usage_window.output_tokens)}</td>
                <td>{formatCost((overview?.usage_window.output_tokens ?? 0) / 1000 * (overview?.usage_window.pricing.output_per_1k ?? 0))}</td>
              </tr>
              <tr>
                <td>Tool executions</td>
                <td>{formatNumber(overview?.usage_window.tool_calls)}</td>
                <td>{formatNumber(overview?.usage_window.tool_errors)} errors in the same window</td>
              </tr>
            </tbody>
          </table>
        </div>

        {overview && overview.usage_window.tool_errors > 0 ? (
          <div className="card" style={{ marginBottom: 16 }}>
            <SectionTitle title="Anomaly Alerts" subtitle="Temporary anomaly detector: failed tool executions in the current runtime window." />
            <div className="feed-list">
              {overview.recent_runtime
                .filter((item) => item.type === "tool_call" && item.success === false)
                .slice(0, 5)
                .map((item) => (
                  <div key={item.id} className="feed-item">
                    <div className="feed-badge feed-badge--error">tool</div>
                    <div className="feed-body">
                      <div className="feed-head">
                        <strong>{item.title}</strong>
                        <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                      </div>
                      <div className="feed-preview">{runtimeResponsePreview(item) || "Tool execution returned an error."}</div>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ) : null}

        <div className="usage-panels">
          <div className="card">
            <SectionTitle title="Cost by Plugin / Skill" subtitle="UX copied first. Attribution remains heuristic for now." />
            <AdaptiveCardDeck className="skill-grid" itemCount={Math.min(skills.length, 6)} minCardWidth={220} idealCardWidth={260} maxCardWidth={300} maxColumns={3}>
              {skills.slice(0, 6).map((skill) => (
                <SkillCard key={skill.name} skill={skill} compact />
              ))}
            </AdaptiveCardDeck>
          </div>

          <div className="card">
            <SectionTitle title="Top Sessions by Cost" subtitle="Approximation from token totals grouped by chat." />
            <table className="usage-table">
              <thead>
                <tr>
                  <th>Chat</th>
                  <th>Tokens</th>
                  <th>Estimated</th>
                </tr>
              </thead>
              <tbody>
                {clusters.slice(0, 6).map((cluster) => (
                  <tr key={cluster.chatroomId}>
                    <td>{cluster.chatTitle}</td>
                    <td>{formatNumber(cluster.tokenTotal)}</td>
                    <td>{formatCost((cluster.tokenTotal / 1000) * 0.045)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="split-panels" style={{ marginTop: 16 }}>
          <div className="card">
            <SectionTitle title="Model Breakdown" />
            <table className="usage-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Calls</th>
                  <th>Tokens</th>
                </tr>
              </thead>
              <tbody>
                {modelRows.map((model) => (
                  <tr key={model.name}>
                    <td>{model.name}</td>
                    <td>{formatNumber(model.calls)}</td>
                    <td>{formatNumber(model.tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <SectionTitle title="Trace Clusters" subtitle="Behavior grouping copied from ClawMetry IA; currently backed by chat-level runtime grouping." />
            <AdaptiveCardDeck className="cluster-grid" itemCount={Math.min(clusters.length, 4)} minCardWidth={240} idealCardWidth={280} maxCardWidth={340} maxColumns={3}>
              {clusters.slice(0, 4).map((cluster) => (
                <div key={cluster.chatroomId} className="cluster-card">
                  <strong>{cluster.chatTitle}</strong>
                  <div className="small-note">{cluster.projectName}</div>
                  <div className="cluster-card__meta">
                    <span className="tag">{cluster.runtimeCount} events</span>
                    <span className="tag">{cluster.llmCalls} llm</span>
                    <span className="tag">{cluster.toolCalls} tools</span>
                  </div>
                </div>
              ))}
            </AdaptiveCardDeck>
          </div>
        </div>
      </section>

      <section className={pageClass("transcripts", "page--dashboard-wide")} id="page-transcripts">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
          <button type="button" className="refresh-btn" disabled>
            TODO Replay
          </button>
        </div>
        <div className="transcripts-shell">
          <div className="transcript-chat-list">
            {groupedMessages.map((group) => (
              <div
                key={group.chatroomId}
                className={`transcript-chat-row ${selectedTranscript?.chatroomId === group.chatroomId ? "active" : ""}`}
                onClick={() => setSelectedTranscriptChatId(group.chatroomId)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") setSelectedTranscriptChatId(group.chatroomId);
                }}
                role="button"
                tabIndex={0}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 4 }}>
                  <strong>{group.chatTitle}</strong>
                  <span className="small-note">{group.messages.length}</span>
                </div>
                <div className="small-note">{group.projectName}</div>
                <div className="small-note" style={{ marginTop: 6 }}>
                  {group.messages[0] ? formatTimeAgo(group.messages[0].created_at) : "--"}
                </div>
              </div>
            ))}
          </div>
          <div className="transcript-view">
            <div className="section-title">Transcript Viewer</div>
            {selectedTranscript ? (
              <>
                <div className="small-note">
                  {selectedTranscript.projectName} · {selectedTranscript.chatTitle} · {selectedTranscript.messages.length} recent messages
                </div>
                <div className="transcript-messages">
                  {selectedTranscript.messages.map((message) => (
                      <div
                        key={message.id}
                        className={`transcript-message ${message.agent_name ? "transcript-message--assistant" : "transcript-message--user"}`}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 6 }}>
                          <strong>{message.agent_name || "user"}</strong>
                          <span className="small-note">{shortDate(message.created_at)}</span>
                        </div>
                        <div className="small-note" style={{ marginBottom: 8 }}>
                          {message.message_type}
                        </div>
                        <div className="transcript-viewer" style={{ padding: 12 }}>{message.content_preview || "(empty message)"}</div>
                      </div>
                    ))}
                </div>
              </>
            ) : (
              <div className="empty-state">No transcript data yet.</div>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("logs", "page--detail-full")} id="page-logs">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshLogs()}>
            ↻ Refresh
          </button>
          <input
            value={logFilter}
            onChange={(event) => setLogFilter(event.target.value)}
            placeholder="Filter logs..."
            className="logs-filter-input"
          />
          <div className="inline-actions">
            {(["all", "info", "warn", "error"] as const).map((level) => (
              <button
                key={level}
                type="button"
                className={`time-btn ${logLevel === level ? "active" : ""}`}
                onClick={() => setLogLevel(level)}
              >
                {level}
              </button>
            ))}
          </div>
          <span className={`status-pill ${logStreamState === "connected" ? "status-pill--live" : "status-pill--offline"}`}>
            {logStreamState === "connected" ? "Live stream connected" : logStreamState === "connecting" ? "Connecting..." : "Stream offline"}
          </span>
          <span className="small-note">Backend in-memory log tail fed by real Python logging records and streamed over SSE.</span>
        </div>
        <div className="card" style={{ padding: 0 }}>
          <div className="log-viewer">
            {filteredLogEntries.length > 0 ? (
              filteredLogEntries.map((entry) => {
                const level = normalizeLogLevel(entry.level);
                const source = logClientSource(entry);
                return (
                <div key={entry.id} className="log-line">
                  <span className="ts">{shortDate(entry.created_at)}</span>
                  <span className={`level ${level}`}>{level}</span>
                  <span className={`log-source log-source--${source}`}>{source}</span>
                  <span>
                    <strong>{entry.logger}</strong> · {entry.message}
                    <br />
                    <span className="small-note mono">
                      {entry.pathname ? `${entry.pathname}:${entry.lineno ?? 0}` : "runtime"} · {entry.thread_name || "main"}
                    </span>
                  </span>
                </div>
                );
              })
            ) : (
              <div className="empty-state">No matching backend log lines.</div>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("memory", "page--dashboard-wide")} id="page-memory">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Memory</div>
            <div className="section-subtitle">Copied ClawMetry IDE shell. Catown data is currently project/workspace based.</div>
          </div>
          <div className="inline-actions">
            <button type="button" className={`time-btn ${memoryView === "summary" ? "active" : ""}`} onClick={() => setMemoryView("summary")}>
              Summary
            </button>
            <button type="button" className={`time-btn ${memoryView === "all" ? "active" : ""}`} onClick={() => setMemoryView("all")}>
              All files
            </button>
            <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
              ↻
            </button>
          </div>
        </div>

        {memoryView === "summary" ? (
          <div className="mem-ide">
            <div className="mem-ide-sidebar">
              <div className="mem-ide-section-header">Explorer</div>
              <div style={{ padding: "0 8px 8px" }}>
                {sortedProjects.map((project) => (
                  <div
                    key={project.id}
                    className={`memory-project-row ${selectedProject?.id === project.id ? "active" : ""}`}
                    onClick={() => setSelectedProjectId(project.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") setSelectedProjectId(project.id);
                    }}
                    role="button"
                    tabIndex={0}
                    style={{ marginBottom: 8 }}
                  >
                    <strong>{project.name}</strong>
                    <div className="small-note">{project.workspace_path || "No workspace path"}</div>
                  </div>
                ))}
              </div>
              <div className="mem-ide-section-header">History</div>
              <div style={{ padding: "0 8px 8px" }}>
                {clusters.slice(0, 6).map((cluster) => (
                  <div key={cluster.chatroomId} className="browser-row" style={{ marginBottom: 8 }}>
                    <strong>{cluster.chatTitle}</strong>
                    <div className="small-note">{shortDate(cluster.latestAt)}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="mem-ide-main">
              <div className="mem-ide-tabbar">
                <strong>{selectedProject?.name ?? "No project selected"}</strong>
                {selectedProject?.workspace_path ? <span className="tag mono">{selectedProject.workspace_path}</span> : null}
              </div>
              <div className="mem-ide-body">
                <div className="mem-ide-preview">
                  <SectionTitle title="Workspace Summary" />
                  {selectedProject ? (
                    <>
                      <div className="muted-block" style={{ marginBottom: 12 }}>
                        {selectedProject.description || "TODO: project summary is not filled yet."}
                      </div>
                      <div className="kpi-grid">
                        <div className="kpi-card">
                          <div className="kpi-card__label">Agents</div>
                          <div className="kpi-card__value">{selectedProject.agents.length}</div>
                        </div>
                        <div className="kpi-card">
                          <div className="kpi-card__label">Chatroom</div>
                          <div className="kpi-card__value">#{selectedProject.chatroom_id}</div>
                        </div>
                        <div className="kpi-card">
                          <div className="kpi-card__label">Status</div>
                          <div className="kpi-card__value">{selectedProject.status}</div>
                        </div>
                      </div>
                      <div className="section-title" style={{ marginTop: 18 }}>Workspace Path</div>
                      <div className="code-block">{selectedProject.workspace_path || "TODO: no workspace bound yet."}</div>
                    </>
                  ) : (
                    <div className="empty-state">No projects tracked yet.</div>
                  )}
                </div>
                <div className="mem-ide-inspector">
                  <SectionTitle title="Inspector" subtitle="Copied shell; file tree and markdown editor come later." />
                  {selectedProject ? (
                    <div className="simple-list">
                      <div className="simple-row">
                        <strong>Assigned agents</strong>
                        <div className="skill-card__meta" style={{ marginTop: 8 }}>
                          {selectedProject.agents.map((agent) => (
                            <span key={agent.id} className="tag">
                              {agent.name}
                            </span>
                          ))}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>TODO next</strong>
                        <ul className="todo-list small-note">
                          <li>Bind workspace file listing for the selected project directory.</li>
                          <li>Wire markdown preview / edit for project memory assets.</li>
                          <li>Attach version history to project-bound memory documents.</li>
                        </ul>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="mem-ide-statusbar">
                <span>{selectedProject?.workspace_path || "no-path"}</span>
                <span>{selectedProject ? selectedProject.status : "idle"}</span>
                <span style={{ marginLeft: "auto" }}>Markdown / TODO</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="grid">
            {sortedProjects.map((project) => (
              <div key={project.id} className="card">
                <div className="section-title">{project.name}</div>
                <div className="small-note">{project.workspace_path || "No workspace path yet"}</div>
                <div className="skill-card__meta" style={{ marginTop: 10 }}>
                  {project.agents.map((agent) => (
                    <span key={agent.id} className="tag">
                      {agent.name}
                    </span>
                  ))}
                </div>
                <div className="muted-block" style={{ marginTop: 12 }}>
                  TODO: full file list, git-tracked history, sensitive flags, preview + editor.
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className={pageClass("brain", "page--dashboard-wide")} id="page-brain">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Brain - Unified Activity Stream</div>
            <div className="section-subtitle">All runtime cards and recent messages flowing through one stream.</div>
          </div>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>

        <div className="card activity-timeline-card">
          <div className="activity-timeline-card__header">
            <div>
              <div className="section-title">Activity Timeline</div>
              <div className="section-subtitle">X-axis: time, grouped by selected unit. Y-axis: event count.</div>
            </div>
            <div className="inline-actions">
              {(["minute", "hour", "day", "month"] as const).map((unit) => (
                <button
                  key={unit}
                  type="button"
                  className={`time-btn ${brainTimelineUnit === unit ? "active" : ""}`}
                  onClick={() => setBrainTimelineUnit(unit)}
                >
                  {unit}
                </button>
              ))}
            </div>
          </div>
          <BarChart
            items={brainTimelineBuckets}
            className="bar-chart--compact bar-chart--timeline"
            labelEvery={brainTimelineLabelStep(brainTimelineUnit)}
            autoLabels
          />
        </div>

        <div className="filter-row" style={{ marginBottom: 10 }}>
          {(["all", "runtime", "tool", "llm", "message"] as const).map((filter) => (
            <button
              key={filter}
              type="button"
              className={`brain-chip ${brainFilter === filter ? "active" : ""}`}
              onClick={() => setBrainFilter(filter)}
            >
              {filter}
            </button>
          ))}
          <input
            className="activity-filter-input"
            type="search"
            value={brainActivityFilter}
            onChange={(event) => setBrainActivityFilter(event.target.value)}
            placeholder="Filter activity..."
            aria-label="Filter activity list"
          />
          {brainActivityFilter ? (
            <button type="button" className="time-btn" onClick={() => setBrainActivityFilter("")}>
              Clear
            </button>
          ) : null}
          <span className="small-note" style={{ marginLeft: "auto" }}>
            {formatNumber(filteredBrainEvents.length)} / {formatNumber(brainEvents.length)}
          </span>
        </div>

        <div className="card">
          <div className="brain-event-list">
            {filteredBrainEvents.map((event) => {
              const isExpanded = expandedBrainEventId === event.id;
              const runtimeId = event.runtimeId;
              const runtimeDetail = runtimeId ? brainRuntimeDetails[runtimeId] ?? null : null;
              const runtimeLoading = runtimeId ? Boolean(brainRuntimeDetailLoading[runtimeId]) : false;
              const runtimeError = runtimeId ? brainRuntimeDetailErrors[runtimeId] ?? "" : "";
              const sections = buildBrainEventSections(event, runtimeDetail);
              const turnToken = shortTurnToken(event.clientTurnId);
              const summaryFromEntity = event.fromEntity || event.source;
              const summaryToEntity = brainSummaryTarget(event);
              const operationLabel = brainOperationLabel(event);
              const fromKind = brainEntityKind(event, summaryFromEntity, "from");
              const toKind = brainEntityKind(event, summaryToEntity, "to");
              const FromIcon = brainEntityIcon(fromKind);
              const ToIcon = brainEntityIcon(toKind);

              return (
                <article
                  key={event.id}
                  className={`brain-event-card brain-event-card--${event.tone} ${event.clientTurnId ? "brain-event-card--turn" : ""} ${isExpanded ? "is-expanded" : ""}`}
                  style={turnCardStyle(event.clientTurnId)}
                >
                  <button
                    type="button"
                    className="brain-event-card__summary"
                    onClick={() => {
                      const nextExpandedId = isExpanded ? null : event.id;
                      if (!isExpanded && runtimeId && !brainRuntimeDetails[runtimeId] && !brainRuntimeDetailLoading[runtimeId]) {
                        void loadBrainRuntimeDetail(runtimeId);
                      }
                      setExpandedBrainEventId(nextExpandedId);
                    }}
                  >
                    <div className="brain-event-card__summary-main">
                      <div className="brain-event-card__header">
                        <div className="brain-event-card__flow">
                          <span className="brain-event-card__flow-group">
                            <span
                              className={`brain-event-card__entity-icon brain-event-card__entity-icon--${fromKind}`}
                              title={brainEntityIconLabel(fromKind)}
                            >
                              <FromIcon className="brain-event-card__entity-glyph" aria-hidden="true" />
                            </span>
                            <span className="brain-event-card__flow-name">{summaryFromEntity}</span>
                          </span>
                          <span className="brain-event-card__flow-arrow" aria-hidden="true">--&gt;</span>
                          <span className="brain-event-card__operation">{operationLabel}</span>
                          <span className="brain-event-card__flow-arrow" aria-hidden="true">--&gt;</span>
                          <span className="brain-event-card__flow-group">
                            <span
                              className={`brain-event-card__entity-icon brain-event-card__entity-icon--${toKind}`}
                              title={brainEntityIconLabel(toKind)}
                            >
                              <ToIcon className="brain-event-card__entity-glyph" aria-hidden="true" />
                            </span>
                            <span className="brain-event-card__flow-name">{summaryToEntity}</span>
                          </span>
                        </div>
                        <div className="brain-event-card__heading">
                          <span className={`brain-event-card__type brain-event-card__type--${event.category}`}>{event.category}</span>
                          {event.phase ? <span className={`brain-event-card__phase brain-event-card__phase--${event.phase}`}>{event.phase}</span> : null}
                          {turnToken ? <span className="brain-event-card__turn">turn {turnToken}</span> : null}
                          <span className="brain-event-card__timestamp">{shortDate(event.createdAt)}</span>
                        </div>
                      </div>
                      <div className="brain-event-card__meta">
                        <span>{event.runtimeType || event.messageType || "message"}</span>
                        {event.projectName ? <span>{event.projectName}</span> : null}
                        {event.chatTitle ? <span>{event.chatTitle}</span> : null}
                      </div>
                      <div className="brain-event-card__detail">{event.detail}</div>
                    </div>
                    <span className="brain-event-card__toggle" aria-hidden="true">
                      {isExpanded ? "−" : "+"}
                    </span>
                  </button>

                  {isExpanded ? (
                    <div className="brain-event-card__details">
                      {runtimeLoading ? <div className="muted-block">Loading raw runtime detail...</div> : null}

                      {!runtimeLoading && runtimeError ? (
                        <div className="brain-event-card__error">
                          <span>{runtimeError}</span>
                          {runtimeId ? (
                            <button
                              type="button"
                              className="refresh-btn"
                              onClick={() => {
                                setBrainRuntimeDetailErrors((current) => {
                                  const next = { ...current };
                                  delete next[runtimeId];
                                  return next;
                                });
                                void loadBrainRuntimeDetail(runtimeId);
                              }}
                            >
                              Retry
                            </button>
                          ) : null}
                        </div>
                      ) : null}

                      {!runtimeLoading && !runtimeError && sections.length === 0 ? (
                        <div className="empty-state">No raw communication payload was captured for this event yet.</div>
                      ) : null}

                      {!runtimeLoading && !runtimeError
                        ? sections.map((section) => (
                            <section
                              key={`${event.id}-${section.label}`}
                              className={`brain-event-section brain-event-section--${section.tone ?? "neutral"} brain-event-section--${section.variant ?? "meta"}`}
                            >
                              <div className="brain-event-section__label">{section.label}</div>
                              {renderMonitorMarkdown(
                                section.format === "json" ? markdownCodeFence(section.content, "json") : section.content,
                                "brain-event-section__body monitor-markdown",
                              )}
                            </section>
                          ))
                        : null}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section className={pageClass("skills", "page--detail-full")} id="page-skills">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Skills</div>
            <div className="section-subtitle">Shortcuts your agents can use. Browser shell copied from ClawMetry.</div>
          </div>
          <div className="inline-actions">
            <button type="button" className={`time-btn ${skillsView === "grid" ? "active" : ""}`} onClick={() => setSkillsView("grid")}>
              Grid
            </button>
            <button type="button" className={`time-btn ${skillsView === "browser" ? "active" : ""}`} onClick={() => setSkillsView("browser")}>
              Browser
            </button>
            <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
              ↻ Refresh
            </button>
          </div>
        </div>

        <div className="grid" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title">Skills</div>
            <div className="card-value">{skills.length}</div>
            <div className="card-sub">unique skills discovered from agent configs</div>
          </div>
          <div className="card">
            <div className="card-title">Agents using skills</div>
            <div className="card-value">{formatNumber(agentDirectory.filter((agent) => (agent.skills ?? []).length > 0).length)}</div>
            <div className="card-sub">assigned across current projects</div>
          </div>
          <div className="card">
            <div className="card-title">Projects with skills</div>
            <div className="card-value">{formatNumber(sortedProjects.filter((project) => project.agents.some((agent) => (agent.skills ?? []).length > 0)).length)}</div>
            <div className="card-sub">workspace-scoped skill usage</div>
          </div>
        </div>

        {skillsView === "grid" ? (
          <AdaptiveCardDeck className="skill-grid" itemCount={skills.length} minCardWidth={260} idealCardWidth={320} maxCardWidth={380} maxColumns={10}>
            {skills.map((skill) => (
              <SkillCard key={skill.name} skill={skill} />
            ))}
          </AdaptiveCardDeck>
        ) : (
          <div className="browser-shell">
            <div className="browser-tree">
              {skills.map((skill) => (
                <div
                  key={skill.name}
                  className={`browser-row ${selectedSkill?.name === skill.name ? "active" : ""}`}
                  onClick={() => setSelectedSkillName(skill.name)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") setSelectedSkillName(skill.name);
                  }}
                  role="button"
                  tabIndex={0}
                  style={{ marginBottom: 8 }}
                >
                  <strong>{skill.name}</strong>
                  <div className="small-note">{skill.agents.length} agents</div>
                </div>
              ))}
            </div>
            <div className="skill-preview">
              {selectedSkill ? (
                <>
                  <div className="section-title">{selectedSkill.name}</div>
                  <div className="small-note" style={{ marginBottom: 12 }}>
                    Always loaded hint: {selectedSkill.alwaysLoadedHint || "TODO"}
                  </div>
                  <div className="browser-preview" style={{ marginBottom: 12 }}>
                    {selectedSkill.detail || "TODO: skill guide rendering will be wired from on-disk skill files."}
                  </div>
                  <div className="section-title">Agents</div>
                  <div className="badge-row" style={{ marginBottom: 12 }}>
                    {selectedSkill.agents.map((agent) => (
                      <span key={agent} className="tag">
                        {agent}
                      </span>
                    ))}
                  </div>
                  <div className="section-title">Projects</div>
                  <div className="badge-row">
                    {selectedSkill.projects.map((project) => (
                      <span key={project} className="tag">
                        {project}
                      </span>
                    ))}
                  </div>
                </>
              ) : (
                <div className="empty-state">No skills discovered.</div>
              )}
            </div>
          </div>
        )}
      </section>

      <section className={pageClass("models", "page--viz-readable")} id="page-models">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="grid">
          <div className="card">
            <div className="card-title">Primary Model</div>
            <div className="card-value">{modelPrimary}</div>
            <div className="card-sub">Most visible model in recent runtime cards.</div>
          </div>
          <div className="card">
            <div className="card-title">Model Diversity</div>
            <div className="card-value">{modelRows.length}</div>
            <div className="card-sub">distinct models used</div>
          </div>
          <div className="card">
            <div className="card-title">Fallback Rate</div>
            <div className="card-value">{formatPercent(modelRows.length > 1 ? ((modelRows.length - 1) / modelRows.length) * 100 : 0)}</div>
            <div className="card-sub">temporary estimate from observed model mix</div>
          </div>
          <div className="card">
            <div className="card-title">Total Turns</div>
            <div className="card-value">{formatNumber(overview?.usage_window.llm_calls)}</div>
            <div className="card-sub">agent responses tracked</div>
          </div>
        </div>
        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Model Mix" />
            <BarChart items={modelRows.map((row) => ({ label: row.name, value: row.calls || row.tokens }))} />
          </div>
          <div className="card">
            <SectionTitle title="Per-Session Breakdown" />
            <table className="usage-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Sessions</th>
                  <th>Turns</th>
                  <th>Share</th>
                </tr>
              </thead>
              <tbody>
                {modelRows.map((row) => (
                  <tr key={row.name}>
                    <td>{row.name}</td>
                    <td>{formatNumber(row.chats)}</td>
                    <td>{formatNumber(row.calls)}</td>
                    <td>{formatPercent((row.calls / Math.max(overview?.usage_window.llm_calls || 1, 1)) * 100)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <EmptyCard title="Model switch history" detail="TODO: capture model handoffs and fallback transitions as first-class runtime events." />
        </div>
      </section>

      <section className={pageClass("compactions", "page--dashboard-wide")} id="page-compactions">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Context Compaction Ledger</div>
            <div className="section-subtitle">Why compaction happened, and how large each prompt module was at the time.</div>
          </div>
          <button type="button" className="refresh-btn" onClick={() => void refreshContextCompactions()} disabled={refreshing}>
            鈫?Refresh
          </button>
        </div>

        <div className="grid" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title">Compactions</div>
            <div className="card-value">{formatNumber(contextCompactionsResponse?.counts.total ?? overview?.system.stats.context_compactions)}</div>
            <div className="card-sub">{formatNumber(contextCompactionEntries.length)} shown</div>
          </div>
          <div className="card">
            <div className="card-title">Latest Prompt</div>
            <div className="card-value">{formatNumber(latestCompaction?.prompt_total?.tokens)}</div>
            <div className="card-sub">{formatBytes(latestCompaction?.prompt_total?.bytes)} · {formatNumber(latestCompaction?.prompt_total?.message_count)} messages</div>
          </div>
          <div className="card">
            <div className="card-title">Dropped / Truncated</div>
            <div className="card-value">{formatNumber(contextCompactionsResponse?.counts.dropped ?? contextCompactionEntries.reduce((total, item) => total + (item.dropped_count ?? 0), 0))}</div>
            <div className="card-sub">truncated {formatNumber(contextCompactionsResponse?.counts.truncated ?? contextCompactionEntries.reduce((total, item) => total + (item.truncated_count ?? 0), 0))}</div>
          </div>
          <div className="card">
            <div className="card-title">Configured Window</div>
            <div className="card-value">{formatNumber(contextWindow)}</div>
            <div className="card-sub">{modelPrimary}</div>
          </div>
        </div>

        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Latest Prompt Composition" />
            {latestCompaction?.prompt_components ? (
              <div className="simple-list">
                {Object.entries(latestCompaction.prompt_components).map(([name, size]) => (
                  <div key={name} className="metric-row">
                    <span>{name.replace(/_/g, " ")}</span>
                    <strong>{formatNumber(size.tokens)} tok · {formatBytes(size.bytes)}</strong>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted-block">No prompt-size diagnostics captured yet. New compaction events will include them.</div>
            )}
          </div>
          <div className="card">
            <SectionTitle title="Latest Reasons" />
            {latestCompaction?.reasons?.length ? (
              <div className="simple-list">
                {latestCompaction.reasons.map((reason, index) => (
                  <div key={`${latestCompaction.id}-${index}`} className="simple-row">
                    <strong>{String(reason.kind || "reason").replace(/_/g, " ")}</strong>
                    <div className="small-note">{compactMonitorJson(reason, 260)}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted-block">{latestCompaction?.reason_summary || "No structured reason diagnostics captured yet."}</div>
            )}
          </div>
        </div>

        <div className="card" style={{ marginTop: 16 }}>
          <SectionTitle title="Compaction Events" subtitle="Newest first. Expand an event to inspect module and fragment sizes." />
          {contextCompactionEntries.length > 0 ? (
            <div className="feed-list">
              {contextCompactionEntries.map((item) => (
                <details key={item.id} className="feed-item compaction-event-card">
                  <summary className="feed-head" style={{ cursor: "pointer" }}>
                    <div className={`feed-badge feed-badge--${item.truncated_count ? "warning" : "neutral"}`}>
                      drop {item.dropped_count ?? 0} / trunc {item.truncated_count ?? 0}
                    </div>
                    <span className="compaction-event-card__summary">
                      <strong>{item.summary || `${item.agent_name || "agent"} compacted context`}</strong>
                      <span className="small-note">
                        {formatTimeAgo(item.created_at)}
                        {item.prompt_total?.tokens ? ` | ${formatNumber(item.prompt_total.tokens)} prompt tokens` : ""}
                        {item.reason_summary ? ` | ${item.reason_summary}` : ""}
                      </span>
                    </span>
                  </summary>
                  <div className="feed-body compaction-event-card__body">
                    <CompactionEventDetail item={item} />
                    <div className="compaction-event-card__legacy">
                    <div className="small-note" style={{ marginBottom: 6 }}>
                      {item.chat_title || "Unknown chat"} {item.project_name ? `路 ${item.project_name}` : ""}
                      {item.task_run_title ? ` 路 ${item.task_run_title}` : ""}
                    </div>
                    <div className="feed-preview">
                      {item.reason_summary || monitorCompactionPreview(item)}
                      {"\n"}Prompt: {formatNumber(item.prompt_total?.tokens)} tokens · {formatBytes(item.prompt_total?.bytes)} · selected {formatNumber(item.selected_tokens)} / candidate {formatNumber(item.candidate_tokens)} tokens
                    </div>
                    {item.prompt_components ? (
                      <div className="usage-table" style={{ marginTop: 10 }}>
                        <table>
                          <thead>
                            <tr>
                              <th>Component</th>
                              <th>Tokens</th>
                              <th>Bytes</th>
                              <th>Count</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(item.prompt_components).map(([name, size]) => (
                              <tr key={`${item.id}-${name}`}>
                                <td>{name.replace(/_/g, " ")}</td>
                                <td>{formatNumber(size.tokens)}</td>
                                <td>{formatBytes(size.bytes)}</td>
                                <td>{formatNumber(size.fragment_count ?? size.message_count)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    {item.prompt_fragments?.length ? (
                      <div className="usage-table" style={{ marginTop: 10 }}>
                        <table>
                          <thead>
                            <tr>
                              <th>Source</th>
                              <th>Role</th>
                              <th>Scope</th>
                              <th>Tokens</th>
                              <th>Bytes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {item.prompt_fragments.slice(0, 18).map((fragment, index) => (
                              <tr key={`${item.id}-fragment-${index}`}>
                                <td>{fragment.source || "--"}</td>
                                <td>{fragment.role || "--"}</td>
                                <td>{fragment.scope || "--"}</td>
                                <td>{formatNumber(fragment.tokens)}</td>
                                <td>{formatBytes(fragment.bytes)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                    </div>
                  </div>
                </details>
              ))}
            </div>
          ) : (
            <div className="muted-block">No compactions captured yet.</div>
          )}
        </div>
      </section>

      <section className={pageClass("context", "page--dashboard-wide")} id="page-context">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">LLM Context Inspector</div>
            <div className="section-subtitle">See exactly what context is assembled and sent to the LLM each turn.</div>
          </div>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <strong>Context Window Usage</strong>
            <span className="small-note">{formatNumber(overview?.usage_window.total_tokens)} / {formatNumber(contextWindow)} tokens</span>
          </div>
          <MetricBar value={overview?.usage_window.total_tokens ?? 0} max={contextWindow} />
          <div className="small-note" style={{ marginTop: 8 }}>Temporary proxy using total tokens from the monitor window.</div>
        </div>
        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Context Composition" />
            <div className="simple-list">
              {[
                { label: "System prompt", value: Math.round((overview?.usage_window.total_tokens ?? 0) * 0.18) },
                { label: "Project context", value: Math.round((overview?.usage_window.total_tokens ?? 0) * 0.22) },
                { label: "Recent transcript", value: Math.round((overview?.usage_window.total_tokens ?? 0) * 0.34) },
                { label: "Tool outputs", value: Math.round((overview?.usage_window.total_tokens ?? 0) * 0.16) },
                { label: "Memory", value: Math.round((overview?.usage_window.total_tokens ?? 0) * 0.1) },
              ].map((row) => (
                <div key={row.label} className="metric-row">
                  <span>{row.label}</span>
                  <strong>{formatNumber(row.value)}</strong>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <SectionTitle title="Stats" />
            <div className="kpi-grid">
              <div className="kpi-card">
                <div className="kpi-card__label">Total turns</div>
                <div className="kpi-card__value">{formatNumber(overview?.usage_window.llm_calls)}</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-card__label">Compactions</div>
                <div className="kpi-card__value">{formatNumber(overview?.system.stats.context_compactions ?? overview?.recent_compactions?.length ?? 0)}</div>
              </div>
              <div className="kpi-card">
                <div className="kpi-card__label">Active model</div>
                <div className="kpi-card__value">{modelPrimary}</div>
              </div>
            </div>
          </div>
        </div>
        <div className="split-panels" style={{ marginTop: 16 }}>
          <div className="card">
            <SectionTitle title="System Prompt Sections" />
            <div className="simple-list">
              {agentDirectory.slice(0, 5).map((agent) => (
                <div key={agent.name} className="simple-row">
                  <strong>{agent.name}</strong>
                  <div className="small-note">{agent.system_prompt_preview || "TODO: wire agent system prompt preview into monitor API."}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <SectionTitle title="Compaction History" />
            {(overview?.recent_compactions ?? []).length > 0 ? (
              <div className="feed-list">
                {(overview?.recent_compactions ?? []).map((item) => (
                  <div key={item.id} className="feed-item">
                    <div className={`feed-badge feed-badge--${item.truncated_count ? "warning" : "neutral"}`}>
                      drop {item.dropped_count ?? 0} / trunc {item.truncated_count ?? 0}
                    </div>
                    <div className="feed-body">
                      <div className="feed-head">
                        <strong>{item.summary || `${item.agent_name || "agent"} compacted context`}</strong>
                        <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                      </div>
                      <div className="small-note" style={{ marginBottom: 6 }}>
                        {item.chat_title || "Unknown chat"} {item.project_name ? `· ${item.project_name}` : ""}
                        {item.task_run_title ? ` · ${item.task_run_title}` : ""}
                      </div>
                      <div className="feed-preview">{monitorCompactionPreview(item)}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted-block">No compactions captured yet.</div>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("subagents", "page--dashboard-wide")} id="page-subagents">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>Sub-Agent Tree</h2>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="grid">
          {sortedProjects.map((project) => (
            <div key={project.id} className="card">
              <div className="section-title">{project.name}</div>
              <div className="small-note" style={{ marginBottom: 10 }}>
                {project.workspace_path || "Standalone / no workspace"}
              </div>
              <div className="simple-list">
                {project.agents.map((agent) => {
                  const usage = overview?.usage_window.by_agent.find((item) => item.agent_name === agent.name);
                  return (
                    <div key={agent.id} className="simple-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                        <strong>{agent.name}</strong>
                        <span className="small-note">{agent.role}</span>
                      </div>
                      <div className="small-note" style={{ marginTop: 6 }}>
                        {formatNumber(usage?.llm_calls)} llm / {formatNumber(usage?.tool_calls)} tools / {formatNumber(usage?.token_total)} tok
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={pageClass("tasks", "page--dashboard-wide")} id="page-tasks">
        <div className="refresh-bar" style={{ marginBottom: 12, alignItems: "flex-start" }}>
          <div>
            <div className="section-title">Background Tasks</div>
            <div className="section-subtitle">
              平铺查看后台任务，以及当前任务内的执行步骤。
            </div>
          </div>
          <div className="inline-actions">
            {(["1h", "6h", "24h", "7d", "30d"] as const).map((range) => (
              <button
                key={range}
                type="button"
                className={`time-btn ${historyRange === range ? "active" : ""}`}
                onClick={() => setHistoryRange(range)}
              >
                {range}
              </button>
            ))}
            {(["all", "running", "completed", "failed"] as const).map((status) => (
              <button
                key={status}
                type="button"
                className={`time-btn ${taskRunStatusFilter === status ? "active" : ""}`}
                onClick={() => setTaskRunStatusFilter(status)}
              >
                {status === "all" ? "all" : titleCaseLabel(status)}
              </button>
            ))}
          </div>
        </div>

        <div className="split-panels">
          <div className="card">
            <div className="refresh-bar" style={{ marginBottom: 12, alignItems: "flex-start" }}>
              <div>
                <div className="section-title">Task List</div>
                <div className="small-note">
                  {historyRange} 内 {taskRunCounts.total} 个任务，当前筛选后 {visibleTaskRuns.length} 个。
                </div>
              </div>
              <button type="button" className="refresh-btn" onClick={() => void refreshTaskRuns()} disabled={refreshing}>
                {refreshing ? "Refreshing..." : "Refresh"}
              </button>
            </div>
            {visibleTaskRuns.length > 0 ? (
              <div className="run-history-list">
                {visibleTaskRuns.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    className={`run-history-item ${selectedTaskRunSummary?.id === run.id ? "is-active" : ""}`}
                    onClick={() => setSelectedTaskRunId(run.id)}
                  >
                    <div className="run-history-item__head">
                      <strong>{run.title}</strong>
                      <div className="run-history-item__badges">
                        <span className={`feed-badge feed-badge--${taskRunStatusTone(run.status)}`}>
                          {titleCaseLabel(run.status)}
                        </span>
                        <span className="feed-badge">{titleCaseLabel(run.run_kind)}</span>
                      </div>
                    </div>
                    <div className="feed-meta">
                      <span>{run.chat_title}</span>
                      {run.project_name ? <span>{run.project_name}</span> : null}
                      {run.target_agent_name ? <span>{run.target_agent_name}</span> : null}
                      <span>{formatTimeAgo(run.created_at)}</span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="muted-block">No task runs captured for the current range and filter.</div>
            )}
          </div>

          <div className="card">
            <div className="section-title">Task</div>
            {!selectedTaskRunSummary ? (
              <div className="muted-block">Pick a task to inspect its current state.</div>
            ) : (
              <>
                <div className="run-detail-hero">
                  <div>
                    <strong>{selectedTaskRunSummary.title}</strong>
                    <div className="feed-meta" style={{ marginTop: 6 }}>
                      <span>{selectedTaskRunSummary.chat_title}</span>
                      {selectedTaskRunSummary.project_name ? <span>{selectedTaskRunSummary.project_name}</span> : null}
                      {selectedTaskRunSummary.target_agent_name ? <span>{selectedTaskRunSummary.target_agent_name}</span> : null}
                      <span>{shortDate(selectedTaskRunSummary.created_at)}</span>
                    </div>
                  </div>
                  <div className="run-detail-hero__badges">
                    <span className={`feed-badge feed-badge--${taskRunStatusTone(selectedTaskRunSummary.status)}`}>
                      {titleCaseLabel(selectedTaskRunSummary.status)}
                    </span>
                    <span className="feed-badge">{titleCaseLabel(selectedTaskRunSummary.run_kind)}</span>
                  </div>
                </div>

                <div className="simple-list" style={{ marginTop: 12 }}>
                  <div className="simple-row">
                    <strong>User Request</strong>
                    <div className="small-note">{selectedTaskRunSummary.user_request || "No user request recorded."}</div>
                  </div>
                  <div className="simple-row">
                    <strong>Summary</strong>
                    <div className="small-note">{taskRunPrimarySummary(selectedTaskRunSummary) || "No summary recorded."}</div>
                  </div>
                </div>

                <div className="inline-actions" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="refresh-btn"
                    disabled={Boolean(taskRunStepLoading[selectedTaskRunSummary.id])}
                    onClick={() => void loadTaskRunSteps(selectedTaskRunSummary.id)}
                  >
                    {selectedTaskRunSteps
                      ? "Refresh Steps"
                      : taskRunStepLoading[selectedTaskRunSummary.id]
                        ? "Loading Steps..."
                        : "Load Steps"}
                  </button>
                </div>

                {taskRunStepLoading[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>Loading task steps…</div>
                ) : null}
                {taskRunStepErrors[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    {taskRunStepErrors[selectedTaskRunSummary.id]}
                  </div>
                ) : null}

                {selectedTaskRunSteps ? (
                  <>
                    <div className="run-detail-section">
                      <div className="run-detail-section__head">
                        <div>
                          <strong>Task Steps</strong>
                          <div className="small-note">当前任务内的标准化执行步骤。</div>
                        </div>
                        <div className="run-detail-hero__badges">
                          <span className="feed-badge">{selectedTaskRunSteps.counts.total} steps</span>
                          <span className="feed-badge">{selectedTaskRunSteps.counts.llm} LLM</span>
                          <span className="feed-badge">{selectedTaskRunSteps.counts.tool} Tools</span>
                          <span className="feed-badge">{selectedTaskRunSteps.counts.event} Events</span>
                        </div>
                      </div>
                      <div className="task-step-metrics">
                        <div className="task-step-metric">
                          <strong>{formatNumber(selectedTaskRunSteps.counts.tokens_in)}</strong>
                          <span>input tok</span>
                        </div>
                        <div className="task-step-metric">
                          <strong>{formatNumber(selectedTaskRunSteps.counts.tokens_out)}</strong>
                          <span>output tok</span>
                        </div>
                        <div className="task-step-metric">
                          <strong>{formatNumber(selectedTaskRunSteps.counts.tool_errors)}</strong>
                          <span>tool errors</span>
                        </div>
                        <div className="task-step-metric">
                          <strong>{formatNumber(selectedTaskRunSteps.counts.tool_blocked)}</strong>
                          <span>blocked tools</span>
                        </div>
                      </div>
                    </div>

                    <div className="task-step-list">
                      {selectedTaskRunHiddenStepCount > 0 ? (
                        <div className="muted-block">
                          Showing latest {selectedTaskRunVisibleSteps.length} of {selectedTaskRunStepTotalCount} steps.
                        </div>
                      ) : null}
                      {selectedTaskRunVisibleSteps.map((step) => {
                        return (
                          <div key={step.id} className={`task-step-card task-step-card--${taskRunStepTone(step)}`}>
                            <div className="task-step-card__head">
                              <div className="task-step-card__title">
                                <span className="task-step-card__index">#{step.sequence}</span>
                                <strong>{step.title}</strong>
                              </div>
                              <span className="small-note">{shortDate(step.created_at)}</span>
                            </div>
                            <div className="feed-meta" style={{ marginTop: 8 }}>
                              <span>{titleCaseLabel(step.step_kind)}</span>
                              {step.agent_name ? <span>{step.agent_name}</span> : null}
                              {step.turn !== null && step.turn !== undefined ? <span>turn {step.turn}</span> : null}
                              {step.model ? <span>{step.model}</span> : null}
                              {step.tool_name ? <span>{step.tool_name}</span> : null}
                              {step.duration_ms ? <span>{formatDuration(step.duration_ms)}</span> : null}
                              {step.tokens_in || step.tokens_out ? <span>{formatNumber(step.tokens_in)} / {formatNumber(step.tokens_out)} tok</span> : null}
                              {step.status ? <span>{step.status}</span> : null}
                              {step.blocked_kind ? <span>{step.blocked_kind}</span> : null}
                              <span>{step.source}</span>
                            </div>
                            {step.preview ? (
                              <div className="feed-preview" style={{ marginTop: 8 }}>
                                {step.preview}
                              </div>
                            ) : null}
                            {step.planned_tools && step.planned_tools.length > 0 ? (
                              <div className="task-step-card__tags">
                                {step.planned_tools.map((tool) => (
                                  <span key={`${step.id}-${tool}`} className="tag">{tool}</span>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </>
                ) : (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    Task steps are available on demand for this task.
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("processes", "page--dashboard-wide")} id="page-processes">
        <div className="refresh-bar" style={{ marginBottom: 12, alignItems: "flex-start" }}>
          <div>
            <div className="section-title">Processes</div>
            <div className="section-subtitle">
              最近 30 条后台 run_shell 进程记录，新任务按创建时间置顶。
            </div>
          </div>
          <div className="inline-actions">
            {(["all", "running", "finished", "failed"] as const).map((status) => (
              <button
                key={status}
                type="button"
                className={`time-btn ${processStatusFilter === status ? "active" : ""}`}
                onClick={() => setProcessStatusFilter(status)}
              >
                {status === "all" ? "all" : titleCaseLabel(status)}
              </button>
            ))}
            <button type="button" className="refresh-btn" onClick={() => void refreshProcesses()} disabled={refreshing}>
              {refreshing ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>

        <div className="stats-footer">
          <div className="stats-footer-item">
            <span>Total</span>
            <strong>{formatNumber(processesResponse?.counts.total)}</strong>
          </div>
          <div className="stats-footer-item">
            <span>Running</span>
            <strong>{formatNumber(processesResponse?.counts.running)}</strong>
          </div>
          <div className="stats-footer-item">
            <span>Finished</span>
            <strong>{formatNumber(processesResponse?.counts.finished)}</strong>
          </div>
          <div className="stats-footer-item">
            <span>Failed</span>
            <strong>{formatNumber(processesResponse?.counts.failed)}</strong>
          </div>
        </div>

        <div className="card">
          {!processesResponse ? (
            <div className="muted-block">Loading background processes...</div>
          ) : visibleProcesses.length > 0 ? (
            <div className="process-list">
              {visibleProcesses.map((process) => (
                <div key={process.id} className={`process-row process-row--${processStatusTone(process)}`}>
                  <div className="process-row__icon">
                    <ProcessStatusIcon process={process} />
                  </div>
                  <div className="process-row__main">
                    <div className="process-row__head">
                      <strong className="mono">{process.command || process.tool_name || process.token}</strong>
                      <div className="run-history-item__badges">
                        <span className={`feed-badge feed-badge--${processStatusTone(process)}`}>
                          {titleCaseLabel(process.status)}
                        </span>
                        {process.exit_code !== null && process.exit_code !== undefined ? (
                          <span className="feed-badge">exit {process.exit_code}</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="feed-meta">
                      <span>{process.tool_name || "process"}</span>
                      {process.agent_name ? <span>{process.agent_name}</span> : null}
                      {process.pid ? <span>pid {process.pid}</span> : null}
                      {process.task_run_id ? <span>task #{process.task_run_id}</span> : null}
                      {process.client_turn_id ? <span>{process.client_turn_id}</span> : null}
                      <span>{formatTimeAgo(process.created_at)}</span>
                      {process.finished_at ? <span>done {shortDate(process.finished_at)}</span> : null}
                    </div>
                    <div className="small-note mono">{process.cwd || "."}</div>
                    {process.tail_output || process.last_result_preview ? (
                      <pre className="process-row__output">{process.tail_output || process.last_result_preview}</pre>
                    ) : (
                      <div className="feed-preview">No process output captured yet.</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted-block">No background processes captured for the current filter.</div>
          )}
        </div>
      </section>

      <section className={pageClass("history", "page--viz-readable")} id="page-history">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>History</h2>
          <div className="inline-actions">
            {(["1h", "6h", "24h", "7d", "30d"] as const).map((range) => (
              <button
                key={range}
                type="button"
                className={`time-btn ${historyRange === range ? "active" : ""}`}
                onClick={() => setHistoryRange(range)}
              >
                {range}
              </button>
            ))}
          </div>
          <div className="small-note" style={{ marginLeft: "auto" }}>Synthetic historical view from the monitor snapshot.</div>
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <SectionTitle title="Token Usage Over Time" />
          <BarChart items={tokenBuckets} />
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <SectionTitle title="Cost Over Time" />
          <BarChart items={costBuckets} />
        </div>
        <div className="history-charts">
          <div className="card">
            <SectionTitle title="Active Sessions" />
            <BarChart items={clusters.map((cluster) => ({ label: cluster.chatTitle, value: cluster.runtimeCount }))} />
          </div>
          <div className="card">
            <SectionTitle title="Cron Runs" subtitle="No cron backend yet; surface kept for parity." />
            <div className="muted-block">TODO: hook Catown cron scheduler runs into monitor history.</div>
          </div>
        </div>
        <div className="split-panels" style={{ marginTop: 16 }}>
          <div className="card">
            <div className="refresh-bar" style={{ marginBottom: 12, alignItems: "flex-start" }}>
              <div>
                <div className="section-title">Run Ledger</div>
                <div className="small-note">
                  Global task runs captured in the last {historyRange}. {taskRunCounts.total} total / {taskRunCounts.running} running / {taskRunCounts.failed} failed.
                </div>
              </div>
              <div className="inline-actions">
                {(["all", "running", "completed", "failed"] as const).map((status) => (
                  <button
                    key={status}
                    type="button"
                    className={`time-btn ${taskRunStatusFilter === status ? "active" : ""}`}
                    onClick={() => setTaskRunStatusFilter(status)}
                  >
                    {status === "all" ? "all" : titleCaseLabel(status)}
                  </button>
                ))}
              </div>
            </div>
            {visibleTaskRuns.length > 0 ? (
              <div className="run-history-list">
                {visibleTaskRuns.map((run) => (
                  <button
                    key={run.id}
                    type="button"
                    className={`run-history-item ${selectedTaskRunSummary?.id === run.id ? "is-active" : ""}`}
                    onClick={() => setSelectedTaskRunId(run.id)}
                  >
                    <div className="run-history-item__head">
                      <strong>{run.title}</strong>
                      <div className="run-history-item__badges">
                        <span className={`feed-badge feed-badge--${taskRunStatusTone(run.status)}`}>
                          {titleCaseLabel(run.status)}
                        </span>
                        {hasActiveRecoveryLease(run) ? (
                          <span className="feed-badge feed-badge--warning">Lease Active</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="feed-meta">
                      <span>{titleCaseLabel(run.run_kind)}</span>
                      <span>{run.chat_title}</span>
                      {run.project_name ? <span>{run.project_name}</span> : null}
                      <span>{formatTimeAgo(run.created_at)}</span>
                    </div>
                    <div className="feed-preview">
                      {taskRunPrimarySummary(run) || "No summary recorded for this run."}
                    </div>
                    <div className="run-history-item__foot">
                      <span>{run.event_count} events</span>
                      {run.latest_event_type ? <span>{titleCaseLabel(run.latest_event_type)}</span> : null}
                      {taskRunContinuationSummary(run) ? (
                        <span>{taskRunContinuationSummary(run)}</span>
                      ) : null}
                      {taskRunCursorSummary(run) ? (
                        <span>{taskRunCursorSummary(run)}</span>
                      ) : null}
                      {taskRunSchedulerSummary(run) ? (
                        <span>{taskRunSchedulerSummary(run)}</span>
                      ) : null}
                      {run.latest_continuation_event_summary ? <span>{run.latest_continuation_event_summary}</span> : null}
                      {run.client_turn_id ? <span>{run.client_turn_id}</span> : null}
                      {hasActiveRecoveryLease(run) ? <span>{compactOwnerLabel(run.recovery_owner)}</span> : null}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="muted-block">No task runs captured for the current range and filter.</div>
            )}
          </div>
          <div className="card">
            <div className="section-title">Selected Run Detail</div>
            {!selectedTaskRunSummary ? (
              <div className="muted-block">Pick a task run to inspect its ordered event ledger.</div>
            ) : (
              <>
                <div className="run-detail-hero">
                  <div>
                    <strong>{selectedTaskRunSummary.title}</strong>
                    <div className="feed-meta" style={{ marginTop: 6 }}>
                      <span>{selectedTaskRunSummary.chat_title}</span>
                      {selectedTaskRunSummary.project_name ? <span>{selectedTaskRunSummary.project_name}</span> : null}
                      <span>{shortDate(selectedTaskRunSummary.created_at)}</span>
                      {selectedTaskRunSummary.completed_at ? <span>done {shortDate(selectedTaskRunSummary.completed_at)}</span> : null}
                    </div>
                  </div>
                  <div className="run-detail-hero__actions">
                    <div className="run-detail-hero__badges">
                      <span className={`feed-badge feed-badge--${taskRunStatusTone(selectedTaskRunSummary.status)}`}>
                        {titleCaseLabel(selectedTaskRunSummary.status)}
                      </span>
                      <span className="feed-badge">{titleCaseLabel(selectedTaskRunSummary.run_kind)}</span>
                    </div>
                    {selectedTaskRunCanResume ? (
                      <button
                        type="button"
                        className="refresh-btn"
                        disabled={Boolean(taskRunResumeLoading[selectedTaskRunSummary.id])}
                        onClick={() => void resumeTaskRun(selectedTaskRunSummary)}
                      >
                        {taskRunResumeLoading[selectedTaskRunSummary.id] ? "Resuming..." : "Resume Run"}
                      </button>
                    ) : null}
                  </div>
                </div>
                {taskRunResumeMessages[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    {taskRunResumeMessages[selectedTaskRunSummary.id]}
                  </div>
                ) : null}
                {taskRunResumeErrors[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    {taskRunResumeErrors[selectedTaskRunSummary.id]}
                  </div>
                ) : null}
                {hasActiveRecoveryLease(selectedTaskRunRecoveryState) ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    Recovery lease is active on <span className="mono">{compactOwnerLabel(selectedTaskRunRecoveryState.recovery_owner)}</span>
                    {" "}until {shortDate(selectedTaskRunRecoveryState.recovery_lease_expires_at)}.
                  </div>
                ) : null}
                <div className="inline-actions" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="refresh-btn"
                    disabled={Boolean(taskRunDetailLoading[selectedTaskRunSummary.id])}
                    onClick={() => void loadTaskRunDetail(selectedTaskRunSummary.id)}
                  >
                    {selectedTaskRunDetail
                      ? "Refresh Detail"
                      : taskRunDetailLoading[selectedTaskRunSummary.id]
                        ? "Loading Detail..."
                        : "Load Detail"}
                  </button>
                </div>
                <div className="simple-list" style={{ marginTop: 12 }}>
                  <div className="simple-row">
                    <strong>Target Agent</strong>
                    <div className="small-note">{selectedTaskRunSummary.target_agent_name || "not pinned"}</div>
                  </div>
                  <div className="simple-row">
                    <strong>Summary</strong>
                    <div className="small-note">{taskRunPrimarySummary(selectedTaskRunSummary) || "No summary recorded."}</div>
                  </div>
                  <div className="simple-row">
                    <strong>Latest Continuation Event</strong>
                    <div className="small-note">
                      {selectedTaskRunSummary.latest_continuation_event_summary
                        ? [
                            selectedTaskRunSummary.latest_continuation_event_type
                              ? titleCaseLabel(selectedTaskRunSummary.latest_continuation_event_type)
                              : null,
                            selectedTaskRunSummary.latest_continuation_event_summary,
                            selectedTaskRunSummary.latest_continuation_event_at
                              ? shortDate(selectedTaskRunSummary.latest_continuation_event_at)
                              : null,
                          ].filter(Boolean).join(" · ")
                        : "No continuation event summary recorded."}
                    </div>
                  </div>
                  <div className="simple-row">
                    <strong>Scheduler Runtime</strong>
                    <div className="small-note">
                      {taskRunSchedulerSummary(selectedTaskRunSummary) || "No scheduler runtime snapshot recorded."}
                    </div>
                  </div>
                  <div className="simple-row">
                    <strong>Continuation Cursor</strong>
                    <div className="small-note">
                      {taskRunCursorSummary(selectedTaskRunSummary) || "No continuation cursor derived."}
                    </div>
                  </div>
                  {RESUMABLE_TASK_RUN_KINDS.has(selectedTaskRunSummary.run_kind || "") || selectedTaskRunRecoveryState?.recovery_owner ? (
                    <>
                      <div className="simple-row">
                        <strong>Recovery Owner</strong>
                        <div className="small-note">
                          {selectedTaskRunRecoveryState?.recovery_owner
                            ? <span className="mono">{selectedTaskRunRecoveryState.recovery_owner}</span>
                            : "unclaimed"}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Recovery Lease</strong>
                        <div className="small-note">
                          {selectedTaskRunRecoveryState?.recovery_lease_expires_at
                            ? `expires ${shortDate(selectedTaskRunRecoveryState.recovery_lease_expires_at)}`
                            : "not leased"}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Recovery Claimed</strong>
                        <div className="small-note">
                          {selectedTaskRunRecoveryState?.recovery_claimed_at
                            ? shortDate(selectedTaskRunRecoveryState.recovery_claimed_at)
                            : "not claimed"}
                        </div>
                      </div>
                    </>
                  ) : null}
                </div>
                {selectedTaskRunSchedulePlan ? (
                  <div className="run-detail-section">
                    <div className="run-detail-section__head">
                      <div>
                        <strong>Scheduler Plan</strong>
                        <div className="small-note">
                          {titleCaseLabel(selectedTaskRunSchedulePlan.mode)} · {selectedTaskRunSchedulePlan.blockingStepCount} blocking / {selectedTaskRunSchedulePlan.sidecarStepCount} sidecar
                        </div>
                        <div className="small-note">
                          Sidecar policy: {selectedTaskRunSchedulePlan.sidecarAgentTypes.length > 0
                            ? selectedTaskRunSchedulePlan.sidecarAgentTypes.join(" · ")
                            : "disabled"}
                        </div>
                        <div className="small-note">
                          Runtime: {selectedTaskRunSchedulePlan.completedStepCount} completed · {selectedTaskRunSchedulePlan.runningStepCount} running · {selectedTaskRunSchedulePlan.waitingStepCount} waiting
                        </div>
                      </div>
                      <div className="run-detail-hero__badges">
                        <span className="feed-badge">{selectedTaskRunSchedulePlan.stepCount} steps</span>
                        {selectedTaskRunSchedulePlan.sidecarStepCount ? (
                          <span className="feed-badge feed-badge--warning">
                            {selectedTaskRunSchedulePlan.sidecarStepCount} sidecar
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="run-schedule-grid">
                      {selectedTaskRunSchedulePlan.steps.map((step) => {
                        const waitStep = step.waitForStepId ? selectedTaskRunStepMap.get(step.waitForStepId) ?? null : null;
                        const attachedStep = step.attachedToStepId ? selectedTaskRunStepMap.get(step.attachedToStepId) ?? null : null;
                        return (
                          <div
                            key={step.stepId}
                            className={`run-schedule-step run-schedule-step--${step.dispatchKind === "sidecar" ? "sidecar" : "blocking"}`}
                          >
                            <div className="run-schedule-step__head">
                              <span className="run-schedule-step__index">#{step.position}</span>
                              <span className={`feed-badge ${step.dispatchKind === "sidecar" ? "feed-badge--warning" : ""}`}>
                                {titleCaseLabel(step.dispatchKind)}
                              </span>
                              <span className={`feed-badge ${step.status === "completed" ? "feed-badge--success" : step.status === "running" ? "feed-badge--info" : step.status === "waiting" ? "feed-badge--warning" : ""}`}>
                                {titleCaseLabel(step.status)}
                              </span>
                            </div>
                            <strong>{step.agentName}</strong>
                            <div className="feed-meta" style={{ marginTop: 6, marginBottom: 0 }}>
                              <span>{titleCaseLabel(step.agentType)}</span>
                              <span>@{step.requestedName}</span>
                              <span>{titleCaseLabel(step.source)}</span>
                              {step.dispatchCount > 0 ? <span>dispatch {step.dispatchCount}</span> : null}
                              {step.completionCount > 0 ? <span>complete {step.completionCount}</span> : null}
                            </div>
                            <div className="small-note" style={{ marginTop: 8 }}>
                              {step.status === "waiting"
                                ? `Waiting on ${waitStep?.agentName || waitStep?.stepId || "a previous blocking step"}.`
                                : step.status === "running"
                                  ? `Currently executing after ${waitStep?.agentName || "scheduler release"}.`
                                  : step.status === "completed"
                                    ? step.releasedByStepId
                                      ? `Completed after resume from ${selectedTaskRunStepMap.get(step.releasedByStepId)?.agentName || step.releasedByStepId}.`
                                      : "Completed from the initial ready queue."
                                    : step.dispatchKind === "sidecar"
                                      ? `Attached to ${attachedStep?.agentName || attachedStep?.stepId || "blocking work"}.`
                                      : waitStep
                                        ? `Runs after ${waitStep.agentName}.`
                                        : "Entry step in this run."}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    {selectedTaskRunHandoffs.length > 0 ? (
                      <div className="run-schedule-links">
                        {selectedTaskRunHandoffs.map((handoff) => {
                          const fromStep = handoff.fromStepId ? selectedTaskRunStepMap.get(handoff.fromStepId) ?? null : null;
                          const toStep = handoff.toStepId ? selectedTaskRunStepMap.get(handoff.toStepId) ?? null : null;
                          const attachedStep = handoff.attachedToStepId
                            ? selectedTaskRunStepMap.get(handoff.attachedToStepId) ?? null
                            : null;
                          return (
                            <div
                              key={handoff.id}
                              className={`run-schedule-link run-schedule-link--${handoff.dispatchKind === "sidecar" ? "sidecar" : "blocking"}`}
                            >
                              <div className="run-schedule-link__head">
                                <strong>{handoff.fromAgent}{" -> "}{handoff.toAgent}</strong>
                                <span className={`feed-badge ${handoff.dispatchKind === "sidecar" ? "feed-badge--warning" : ""}`}>
                                  {titleCaseLabel(handoff.dispatchKind)}
                                </span>
                              </div>
                              <div className="small-note" style={{ marginTop: 6 }}>
                                {handoff.contentPreview || "No handoff preview recorded."}
                              </div>
                              <div className="feed-meta" style={{ marginTop: 8, marginBottom: 0 }}>
                                {fromStep ? <span>from {fromStep.stepId}</span> : null}
                                {toStep ? <span>to {toStep.stepId}</span> : null}
                                {attachedStep ? <span>attached to {attachedStep.agentName}</span> : null}
                                {handoff.createdAt ? <span>{shortDate(handoff.createdAt)}</span> : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {taskRunDetailLoading[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>Loading ordered task-run events…</div>
                ) : null}
                {taskRunDetailErrors[selectedTaskRunSummary.id] ? (
                  <div className="muted-block" style={{ marginTop: 12 }}>
                    {taskRunDetailErrors[selectedTaskRunSummary.id]}
                  </div>
                ) : null}
                {selectedTaskRunDetail?.checkpoint_snapshot ? (
                  <div className="run-detail-section">
                    <div className="run-detail-section__head">
                      <div>
                        <strong>Checkpoint Snapshot</strong>
                        <div className="small-note">
                          Latest resumable state derived from the task-run ledger.
                        </div>
                      </div>
                      <div className="run-detail-hero__badges">
                        <span className="feed-badge">
                          {selectedTaskRunDetail.checkpoint_snapshot.event_count ?? 0} events
                        </span>
                        {selectedTaskRunDetail.checkpoint_snapshot.pending_approval_count ? (
                          <span className="feed-badge feed-badge--warning">
                            {selectedTaskRunDetail.checkpoint_snapshot.pending_approval_count} pending approvals
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="simple-list">
                      <div className="simple-row">
                        <strong>Latest Event</strong>
                        <div className="small-note">
                          {selectedTaskRunDetail.checkpoint_snapshot.latest_event_type
                            ? `${titleCaseLabel(selectedTaskRunDetail.checkpoint_snapshot.latest_event_type)} · ${shortDate(selectedTaskRunDetail.checkpoint_snapshot.latest_event_at)}`
                            : "No events recorded."}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Latest Agent Turn</strong>
                        <div className="small-note">
                          {latestAgentTurnPreview(selectedTaskRunDetail) || "No completed agent turn captured yet."}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Latest Compaction</strong>
                        <div className="small-note">{checkpointCompactionPreview(selectedTaskRunDetail)}</div>
                      </div>
                      <div className="simple-row">
                        <strong>Scheduler Runtime</strong>
                        <div className="small-note">
                          {selectedTaskRunDetail.checkpoint_snapshot.latest_scheduler_runtime
                            ? "Latest scheduler runtime snapshot is available below."
                            : "No scheduler runtime snapshot recorded."}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Continuation Cursor</strong>
                        <div className="small-note">
                          {taskRunCursorSummary(selectedTaskRunDetail) || "No continuation cursor derived."}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Continuation State</strong>
                        <div className="small-note">
                          {taskRunContinuationSummary(selectedTaskRunDetail) || "No continuation-state consumption derived."}
                        </div>
                      </div>
                      <div className="simple-row">
                        <strong>Turn-Local State</strong>
                        <div className="small-note">
                          {selectedTaskRunDetail.checkpoint_snapshot.turn_local_state?.protocol_tail_messages?.length
                            ? [
                                selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.turn
                                  ? `turn ${selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.turn}`
                                  : null,
                                selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.tool_names?.length
                                  ? `${selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.tool_names.join(", ")}`
                                  : null,
                                selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.prior_round_summaries?.length
                                  ? `${selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.prior_round_summaries.length} prior summaries`
                                  : null,
                                selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.blocked_tool?.["tool_name"]
                                  ? `blocked ${String(selectedTaskRunDetail.checkpoint_snapshot.turn_local_state.blocked_tool["tool_name"])}`
                                  : null,
                              ].filter(Boolean).join(" · ")
                            : "No turn-local continuation payload derived."}
                        </div>
                      </div>
                    </div>
                    {selectedTaskRunDetail.checkpoint_snapshot.turn_local_state?.protocol_tail_messages?.length ? (
                      <LazyRawPayload
                        label="Turn-Local State Payload"
                        value={selectedTaskRunDetail.checkpoint_snapshot.turn_local_state}
                        className="run-event-row__payload"
                        style={{ marginTop: 12 }}
                      />
                    ) : null}
                    {selectedTaskRunDetail.checkpoint_snapshot.continuation_cursor ? (
                      <LazyRawPayload
                        label="Continuation Cursor Payload"
                        value={selectedTaskRunDetail.checkpoint_snapshot.continuation_cursor}
                        className="run-event-row__payload"
                        style={{ marginTop: 12 }}
                      />
                    ) : null}
                    {selectedTaskRunDetail.checkpoint_snapshot.continuation_state ? (
                      <LazyRawPayload
                        label="Continuation State Payload"
                        value={selectedTaskRunDetail.checkpoint_snapshot.continuation_state}
                        className="run-event-row__payload"
                        style={{ marginTop: 12 }}
                      />
                    ) : null}
                    {selectedTaskRunDetail.checkpoint_snapshot.latest_scheduler_runtime ? (
                      <LazyRawPayload
                        label="Runtime Payload"
                        value={selectedTaskRunDetail.checkpoint_snapshot.latest_scheduler_runtime}
                        className="run-event-row__payload"
                        style={{ marginTop: 12 }}
                      />
                    ) : null}
                  </div>
                ) : null}
                {selectedTaskRunDetail ? (
                  <div className="simple-list" style={{ marginTop: 12 }}>
                    {selectedTaskRunHiddenEventCount > 0 ? (
                      <div className="muted-block">
                        Showing latest {selectedTaskRunVisibleEvents.length} of {selectedTaskRunEventTotalCount} events.
                      </div>
                    ) : null}
                    {selectedTaskRunVisibleEvents.map((event) => (
                      <div key={event.id} className={`run-event-row run-event-row--${taskRunEventTone(event.event_type)}`}>
                        <div className="run-event-row__head">
                          <strong>
                            #{event.event_index} · {titleCaseLabel(event.event_type)}
                          </strong>
                          <span className="small-note">{shortDate(event.created_at)}</span>
                        </div>
                        <div className="small-note" style={{ marginTop: 6 }}>
                          {event.summary || "No summary recorded for this event."}
                        </div>
                        <div className="feed-meta" style={{ marginTop: 8, marginBottom: 0 }}>
                          {event.agent_name ? <span>{event.agent_name}</span> : null}
                          {event.message_id ? <span>message #{event.message_id}</span> : null}
                        </div>
                        {taskRunEventDetailPreview(event) ? (
                          <div className="small-note" style={{ marginTop: 8 }}>
                            {taskRunEventDetailPreview(event)}
                          </div>
                        ) : null}
                        {event.payload && Object.keys(event.payload).length > 0 ? (
                          <LazyRawPayload label="Payload" value={event.payload} className="run-event-row__payload" />
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("limits", "page--viz-readable")} id="page-limits">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>API Rate Limit Monitor</h2>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <p className="small-note" style={{ marginTop: 0, marginBottom: 14 }}>
          Rolling utilisation bars currently use Catown monitor-window approximations until OTLP rate-limit metrics land.
        </p>
        <div className="grid">
          {limitsRows.map((row) => (
            <div key={row.label} className="card">
              <div className="section-title">{row.label}</div>
              <div className="card-value">{formatPercent((row.value / row.max) * 100)}</div>
              <div className="card-sub">{formatNumber(row.value)} / {formatNumber(row.max)}</div>
              <MetricBar value={row.value} max={row.max} />
              <div className="small-note" style={{ marginTop: 8 }}>{row.detail}</div>
            </div>
          ))}
        </div>
        <EmptyCard title="Hourly rate-limit history" detail="TODO: expose provider-specific rolling minute/hour budgets from Catown backend metrics." />
      </section>

      <section className={pageClass("approvals", "page--dashboard-wide")} id="page-approvals">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Approvals</div>
            <div className="section-subtitle">Visual policy builder + approval queue baseline copied from ClawMetry.</div>
          </div>
          <div className="inline-actions">
            <span className="small-note">{approvalQueueResponse?.counts.pending ?? approvalsPending.length} pending</span>
            <button
              type="button"
              className="refresh-btn"
              onClick={() => void Promise.all([refreshApprovalQueue(), refreshApprovalAudit()])}
              disabled={refreshing}
            >
              ↻ Refresh
            </button>
          </div>
        </div>
        <div className="grid" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title">Approval Audit</div>
            <div className="card-value">{formatNumber(approvalAuditResponse?.counts.all ?? approvalAuditEntries.length)}</div>
            <div className="card-sub">
              {formatNumber(approvalAuditResponse?.counts.automatic ?? 0)} automatic · {formatNumber(approvalAuditResponse?.counts.remembered ?? 0)} remembered rules
            </div>
          </div>
          <div className="card">
            <div className="card-title">Manual Decisions</div>
            <div className="card-value">
              {formatNumber((approvalAuditResponse?.counts.approve ?? 0) + (approvalAuditResponse?.counts.reject ?? 0))}
            </div>
            <div className="card-sub">
              {formatNumber(approvalAuditResponse?.counts.approve ?? 0)} approved · {formatNumber(approvalAuditResponse?.counts.reject ?? 0)} rejected
            </div>
          </div>
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <SectionTitle title="Pending Approvals" />
          {approvalsPending.length > 0 ? (
            <div className="feed-list">
              {approvalsPending.map((item) => (
                <div key={item.id} className="approval-card">
                  <div className="simple-row">
                    <strong>{item.title}</strong>
                    <span className="tag mono">Approval #{item.id}</span>
                    <div className={`feed-badge feed-badge--${approvalStatusTone(item.status)}`}>{item.status}</div>
                    <div className="small-note">{formatTimeAgo(item.created_at)}</div>
                  </div>
                  <div className="small-note" style={{ marginTop: 6 }}>
                    {item.chat_title || "Unknown chat"} {item.project_name ? `· ${item.project_name}` : ""}
                    {item.task_run_title ? ` · ${item.task_run_title}` : ""}
                  </div>
                  <div className="muted-block" style={{ marginTop: 8 }}>
                    {approvalQueuePreview(item) || "Awaiting operator decision."}
                  </div>
                  <div className="approval-card__meta" style={{ marginTop: 10 }}>
                    <span className="tag">{item.queue_kind}</span>
                    <span className="tag">{item.target_kind}</span>
                    {item.target_name ? <span className="tag mono">{item.target_name}</span> : null}
                    {item.resume_supported ? <span className="tag">replayable</span> : <span className="tag">manual only</span>}
                  </div>
                  <div className="inline-actions" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      className="refresh-btn"
                      onClick={() => void decideApprovalQueueItem(item, "approve")}
                      disabled={Boolean(approvalQueueActionLoading[item.id])}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="refresh-btn"
                      onClick={() => void decideApprovalQueueItem(item, "reject")}
                      disabled={Boolean(approvalQueueActionLoading[item.id])}
                    >
                      Reject
                    </button>
                    {approvalQueueActionMessages[item.id] ? (
                      <span className="small-note">{approvalQueueActionMessages[item.id]}</span>
                    ) : null}
                    {approvalQueueActionErrors[item.id] ? (
                      <span className="small-note" style={{ color: "var(--danger, #ef4444)" }}>
                      {approvalQueueActionErrors[item.id]}
                      </span>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted-block">No pending approvals right now.</div>
          )}
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="refresh-bar" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <div>
              <SectionTitle title="Approval Audit Log" />
              <div className="small-note">All manual approval decisions, remembered rules, and automatic rule matches.</div>
            </div>
            <div className="inline-actions">
              {(["all", "approve", "reject", "allow", "deny"] as const).map((decision) => (
                <button
                  key={decision}
                  type="button"
                  className={`time-btn ${approvalAuditFilter === decision ? "active" : ""}`}
                  onClick={() => setApprovalAuditFilter(decision)}
                >
                  {decision === "all" ? "all" : titleCaseLabel(decision)}
                </button>
              ))}
            </div>
          </div>
          {approvalAuditEntries.length > 0 ? (
            <div className="feed-list">
              {approvalAuditEntries.map((item) => (
                <div key={item.id} className="feed-item">
                  <div className={`feed-badge feed-badge--${approvalStatusTone(item.decision)}`}>
                    {titleCaseLabel(item.decision)}
                  </div>
                  <div className="feed-body">
                    <div className="feed-head">
                      <strong>{titleCaseLabel(item.event_kind)}</strong>
                      <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                    </div>
                    <div className="feed-meta">
                      <span>{item.source}</span>
                      {item.resolved_by ? <span>{item.resolved_by}</span> : null}
                      {item.agent_name ? <span>{item.agent_name}</span> : null}
                      {item.tool_name ? <span>{item.tool_name}</span> : null}
                      {item.scope ? <span>{item.scope}</span> : null}
                    </div>
                    <div className="feed-preview">
                      {approvalAuditPreview(item) || "Approval event recorded."}
                    </div>
                    {item.approval_fingerprint ? (
                      <div className="muted-block" style={{ marginTop: 8 }}>
                        <strong>Fingerprint</strong>
                        <div className="mono" style={{ marginTop: 4, overflowWrap: "anywhere" }}>
                          {item.approval_fingerprint}
                        </div>
                        <div className="small-note" style={{ marginTop: 4 }}>
                          {item.approval_fingerprint_kind || item.matcher_type || "approval fingerprint"}
                        </div>
                      </div>
                    ) : null}
                    {item.approval_fingerprint_input && Object.keys(item.approval_fingerprint_input).length > 0 ? (
                      <details className="muted-block" style={{ marginTop: 8 }}>
                        <summary>Fingerprint input</summary>
                        <pre className="monitor-pre" style={{ marginTop: 8, maxHeight: 180, overflow: "auto" }}>
                          {compactMonitorJson(item.approval_fingerprint_input)}
                        </pre>
                      </details>
                    ) : null}
                    <div className="approval-card__meta" style={{ marginTop: 8 }}>
                      {item.queue_item_id ? <span className="tag">queue #{item.queue_item_id}</span> : null}
                      {item.preference_id ? <span className="tag">rule #{item.preference_id}</span> : null}
                      {item.project_id ? <span className="tag">project #{item.project_id}</span> : null}
                      {item.chatroom_id ? <span className="tag">chat #{item.chatroom_id}</span> : null}
                      {item.matcher_type ? <span className="tag">{item.matcher_type}</span> : null}
                      {item.matcher_value ? <span className="tag mono">matcher {item.matcher_value.slice(0, 10)}</span> : null}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted-block">No approval audit records captured yet.</div>
          )}
        </div>
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <SectionTitle title="Protection Rules" />
            <button type="button" className="refresh-btn" onClick={() => setShowCreateRuleForm((value) => !value)}>
              {showCreateRuleForm ? "Hide form" : "+ New Rule"}
            </button>
          </div>
          {showCreateRuleForm ? (
            <div className="grid" style={{ marginBottom: 16 }}>
              <div className="placeholder-card">
                <div className="section-title">Create Custom Rule</div>
                <div className="small-note">TODO: persist approval rules in Catown backend. UX is copied first.</div>
              </div>
            </div>
          ) : null}
          <AdaptiveCardDeck className="approval-grid" itemCount={APPROVAL_PRESETS.length} minCardWidth={260} idealCardWidth={300} maxCardWidth={360} maxColumns={3}>
            {APPROVAL_PRESETS.map((preset) => (
              <div key={preset.key} className="approval-card">
                <div className="simple-row">
                  <strong>{preset.name}</strong>
                  <div className="small-note">{preset.description}</div>
                  <div className="approval-card__meta">
                    <span className="tag">{preset.tool}</span>
                    <span className="tag mono">{preset.pattern}</span>
                  </div>
                </div>
              </div>
            ))}
          </AdaptiveCardDeck>
        </div>
        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Get Notified" />
            <AdaptiveCardDeck className="integration-grid" itemCount={APPROVAL_INTEGRATIONS.length} minCardWidth={240} idealCardWidth={280} maxCardWidth={320} maxColumns={2}>
              {APPROVAL_INTEGRATIONS.map((integration) => (
                <div key={integration.name} className="integration-card">
                  <strong>{integration.name}</strong>
                  <div className="small-note">{integration.description}</div>
                  <div className="integration-card__meta">
                    <span className="tag">{integration.status}</span>
                  </div>
                </div>
              ))}
            </AdaptiveCardDeck>
          </div>
          <div className="card">
            <SectionTitle title="Recent Decisions" />
            {approvalsHistory.length > 0 ? (
              <div className="feed-list">
                {approvalsHistory.map((item) => (
                  <div key={item.id} className="feed-item">
                    <div className={`feed-badge feed-badge--${approvalStatusTone(item.status)}`}>{item.status}</div>
                    <div className="feed-body">
                      <div className="feed-head">
                        <strong>{item.title}</strong>
                        <span className="small-note">{formatTimeAgo(item.created_at)}</span>
                      </div>
                      <div className="small-note" style={{ marginBottom: 6 }}>
                        {item.chat_title || "Unknown chat"} {item.project_name ? `· ${item.project_name}` : ""}
                        {item.task_run_title ? ` · ${item.task_run_title}` : ""}
                      </div>
                      {item.resolution_preview || item.summary ? (
                        <div className="feed-preview">{item.resolution_preview || item.summary}</div>
                      ) : null}
                      <div className="approval-card__meta" style={{ marginTop: 8 }}>
                        {item.action_taken ? <span className="tag">{item.action_taken}</span> : null}
                        {item.replay_status ? (
                          <span className={`feed-badge feed-badge--${runtimeTone(item.replay_status, item.replay_success ?? undefined)}`}>
                            continuation {item.replay_status}
                          </span>
                        ) : null}
                        {item.followup_status ? (
                          <span className={`feed-badge feed-badge--${approvalFollowupTone(item.followup_status)}`}>
                            follow-up {item.followup_status}
                          </span>
                        ) : null}
                        {item.followup_reason ? <span className="tag">{item.followup_reason}</span> : null}
                      </div>
                      {item.followup_error ? (
                        <div className="small-note" style={{ marginTop: 6, color: "var(--danger, #ef4444)" }}>
                          {item.followup_error}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted-block">No approval decisions captured yet.</div>
            )}
          </div>
        </div>
      </section>

      <section className={pageClass("clusters", "page--dashboard-wide")} id="page-clusters">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>Session Clusters</h2>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <AdaptiveCardDeck className="cluster-grid" itemCount={clusters.length} minCardWidth={280} idealCardWidth={320} maxCardWidth={380} maxColumns={4}>
          {clusters.map((cluster) => (
            <div key={cluster.chatroomId} className="cluster-card">
              <strong>{cluster.chatTitle}</strong>
              <div className="small-note">{cluster.projectName}</div>
              <div className="cluster-card__meta">
                <span className="tag">{cluster.runtimeCount} events</span>
                <span className="tag">{cluster.llmCalls} llm</span>
                <span className="tag">{cluster.toolCalls} tools</span>
              </div>
              <div className="muted-block" style={{ marginTop: 10 }}>
                Agents: {cluster.agents.join(", ") || "unattributed"}
                {"\n"}
                Tokens: {formatNumber(cluster.tokenTotal)}
              </div>
            </div>
          ))}
        </AdaptiveCardDeck>
      </section>

      <section className={pageClass("security", "page--viz-readable")} id="page-security">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Security</div>
            <div className="section-subtitle">Threat detection and posture summary copied from ClawMetry.</div>
          </div>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Scan
          </button>
        </div>

        <div className="card" style={{ marginBottom: 16 }}>
          <div className="security-posture">
            <div className="posture-score" style={{ background: securityScore >= 75 ? "#16a34a" : securityScore >= 45 ? "#f59e0b" : "#ef4444" }}>
              {securityScore}
            </div>
            <div>
              <strong>Security Posture</strong>
              <div className="small-note" style={{ marginTop: 4 }}>Snapshot-driven posture score from runtime health, tool error pressure and feature availability.</div>
              <MetricBar value={securityScore} />
            </div>
            <div className="posture-stats">
              <div className="posture-stat">
                <strong style={{ color: "var(--text-success)" }}>{passedChecks}</strong>
                <div className="small-note">Passed</div>
              </div>
              <div className="posture-stat">
                <strong style={{ color: "var(--text-warning)" }}>{failedChecks}</strong>
                <div className="small-note">Warnings</div>
              </div>
              <div className="posture-stat">
                <strong style={{ color: "var(--text-error)" }}>{securityEvents.filter((event) => event.severity === "critical").length}</strong>
                <div className="small-note">Critical</div>
              </div>
            </div>
          </div>
          <div className="simple-list" style={{ marginTop: 14 }}>
            {securityChecks.map((check) => (
              <div key={check.label} className="simple-row">
                <strong>{check.label}</strong>
                <div className="small-note">{check.detail}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="card-title">Critical</div>
            <div className="card-value">{securityEvents.filter((event) => event.severity === "critical").length}</div>
          </div>
          <div className="card">
            <div className="card-title">High</div>
            <div className="card-value">{securityEvents.filter((event) => event.severity === "high").length}</div>
          </div>
          <div className="card">
            <div className="card-title">Medium</div>
            <div className="card-value">{securityEvents.filter((event) => event.severity === "medium").length}</div>
          </div>
          <div className="card">
            <div className="card-title">Clean Sessions</div>
            <div className="card-value">{Math.max((overview?.system.stats.chatrooms ?? 0) - clusters.filter((cluster) => cluster.toolCalls > 0).length, 0)}</div>
          </div>
        </div>

        <div className="filter-row" style={{ marginBottom: 10 }}>
          {(["all", "critical", "high", "medium", "low"] as const).map((severity) => (
            <button
              key={severity}
              type="button"
              className={`brain-chip ${securityFilter === severity ? "active" : ""}`}
              onClick={() => setSecurityFilter(severity)}
            >
              {severity}
            </button>
          ))}
        </div>

        <div className="card" style={{ marginBottom: 16 }}>
          <SectionTitle title="Threat Timeline" subtitle="Newest first." />
          {filteredSecurityEvents.length > 0 ? (
            <div className="feed-list">
              {filteredSecurityEvents.map((event) => (
                <div key={event.id} className="feed-item">
                  <div className={`feed-badge feed-badge--${event.severity === "critical" || event.severity === "high" ? "error" : "warning"}`}>{event.severity}</div>
                  <div className="feed-body">
                    <div className="feed-head">
                      <strong>{event.title}</strong>
                      <span className="small-note">{formatTimeAgo(event.createdAt)}</span>
                    </div>
                    <div className="feed-preview">{event.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="muted-block">No threats in the current monitor window.</div>
          )}
        </div>

        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <SectionTitle title="Signature Catalog" />
            <button type="button" className="refresh-btn" onClick={() => setShowSecurityCatalog((value) => !value)}>
              {showSecurityCatalog ? "Hide" : "Show"}
            </button>
          </div>
          {showSecurityCatalog ? (
            <AdaptiveCardDeck className="security-grid" itemCount={4} minCardWidth={260} idealCardWidth={300} maxCardWidth={340} maxColumns={3}>
              {[
                "Dangerous shell command patterns",
                "Approval / gate failures",
                "Tool execution exceptions",
                "Workspace escape attempts",
              ].map((label) => (
                <div key={label} className="security-card">
                  <strong>{label}</strong>
                  <div className="small-note">TODO: back this with a signed catalog once Catown emits structured security events.</div>
                </div>
              ))}
            </AdaptiveCardDeck>
          ) : null}
        </div>
      </section>

      <section className={pageClass("crons", "page--dashboard-wide")} id="page-crons">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
          <button type="button" className="refresh-btn" disabled>
            + New Job
          </button>
          <button type="button" className="refresh-btn" disabled>
            Emergency Stop All
          </button>
          <span className="small-note">Cron UI copied first. Scheduler backend remains TODO.</span>
        </div>
        <div className="grid">
          <EmptyCard title="Cron list" detail="TODO: expose Catown scheduled jobs and run history." />
          <EmptyCard title="Cron health monitor" detail="TODO: show schedule drift, failures and next-run timing." />
        </div>
      </section>

      <section className={pageClass("nemoclaw", "page--dashboard-wide")} id="page-nemoclaw">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">NemoClaw</div>
            <div className="section-subtitle">Catown does not expose NemoClaw yet; page shell is copied for later integration.</div>
          </div>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Sandbox" />
            <div className="usage-table">
              <div className="simple-row"><strong>Status</strong><div className="small-note">TODO</div></div>
              <div className="simple-row"><strong>Blueprint</strong><div className="small-note">TODO</div></div>
              <div className="simple-row"><strong>Run ID</strong><div className="small-note">TODO</div></div>
            </div>
          </div>
          <div className="card">
            <SectionTitle title="Inference" />
            <div className="usage-table">
              <div className="simple-row"><strong>Provider</strong><div className="small-note">{config?.global_llm?.provider?.baseUrl || "TODO"}</div></div>
              <div className="simple-row"><strong>Model</strong><div className="small-note">{config?.global_llm?.default_model || "TODO"}</div></div>
              <div className="simple-row"><strong>Approvals</strong><div className="small-note">{approvalQueueResponse?.counts.pending ?? approvalsPending.length} pending</div></div>
            </div>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <EmptyCard title="Applied presets and egress approvals" detail="TODO: wire sandbox policy state and outbound approval queues when Catown grows a sandbox monitor." />
        </div>
      </section>

      <section className={pageClass("version-impact", "page--dashboard-wide")} id="page-version-impact">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>Upgrade Impact</h2>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="grid">
          <div className="card">
            <div className="card-title">Current Version</div>
            <div className="card-value">{overview?.system.version ?? "--"}</div>
            <div className="card-sub">Monitor shell copied first; release diffing is TODO.</div>
          </div>
          <div className="card">
            <div className="card-title">Potential Impact</div>
            <div className="card-value">TODO</div>
            <div className="card-sub">Track prompt, model and toolchain changes across versions.</div>
          </div>
        </div>
        <EmptyCard title="Version regression explorer" detail="TODO: diff runtime metrics, approval rules and model mix across deployments." />
      </section>

      <div className="monitor-floating-version" title={`Monitor UI version ${UI_VERSION}`}>
        <span className="monitor-floating-version__label">UI</span>
        <strong>v{UI_VERSION}</strong>
      </div>
    </div>
  );
}
