import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Boxes, BrainCircuit, Crown, Globe, Monitor, Server, UserRound, Wrench } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "../api/client";
import type {
  ApprovalQueueItem,
  AgentInfo,
  ConfigResponse,
  MonitorTaskRunSummary,
  MonitorTaskRunsResponse,
  MonitorApprovalQueueEntry,
  MonitorApprovalQueueResponse,
  MonitorLogEntry,
  MonitorNetworkEvent,
  MonitorOverview,
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

type MonitorPage = {
  id: string;
  label: string;
};

const PRIMARY_PAGES = [
  { id: "overview", label: "Overview" },
  { id: "flow", label: "Flow" },
  { id: "network", label: "Network" },
  { id: "usage", label: "Usage" },
  { id: "transcripts", label: "Transcripts" },
  { id: "logs", label: "Logs" },
  { id: "memory", label: "Memory" },
  { id: "brain", label: "Brain" },
] as const satisfies readonly MonitorPage[];

const MORE_PAGES = [
  { id: "skills", label: "Skills" },
  { id: "models", label: "Models" },
  { id: "context", label: "Context" },
  { id: "subagents", label: "Subagents" },
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

type SkillRow = {
  name: string;
  agents: string[];
  projects: string[];
  detail: string;
  alwaysLoadedHint: string;
};

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
  if (normalized === "approved") return "success";
  if (normalized === "rejected") return "error";
  return runtimeTone(normalized);
}

function taskRunStatusTone(status: string | null | undefined) {
  const normalized = (status || "").toLowerCase();
  if (normalized === "completed") return "success";
  if (normalized === "failed") return "error";
  if (normalized === "running") return "warning";
  return "neutral";
}

function taskRunEventTone(eventType: string | null | undefined) {
  const normalized = (eventType || "").toLowerCase();
  if (normalized.includes("failed") || normalized.includes("error")) return "error";
  if (normalized.includes("completed")) return "success";
  if (normalized.includes("tool") || normalized.includes("handoff")) return "warning";
  return "neutral";
}

function titleCaseLabel(value: string | null | undefined) {
  if (!value) return "unknown";
  return value
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
    .find((event) => {
      const payload = asMonitorRecord(event.payload);
      return Boolean(asMonitorRecord(payload?.runtime));
    });
  return asMonitorRecord(asMonitorRecord(runtimeEvent?.payload)?.runtime);
}

function extractRunSchedulePlan(detail: TaskRunDetail | null): RunSchedulePlan | null {
  if (!detail) return null;
  const scheduleEvent = detail.events.find((event) => event.event_type === "scheduler_plan_created");
  const payload = asMonitorRecord(scheduleEvent?.payload);
  if (!payload) return null;

  const rawSteps = Array.isArray(payload.steps) ? payload.steps : [];
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
    mode: monitorStringField(payload.mode) ?? "linear_blocking_chain",
    stepCount: monitorNumberField(payload.step_count) ?? mergedSteps.length,
    blockingStepCount:
      monitorNumberField(payload.blocking_step_count) ??
      mergedSteps.filter((step) => step.dispatchKind === "blocking").length,
    sidecarStepCount:
      monitorNumberField(payload.sidecar_step_count) ??
      mergedSteps.filter((step) => step.dispatchKind === "sidecar").length,
    sidecarAgentTypes: Array.isArray(payload.sidecar_agent_types)
      ? payload.sidecar_agent_types
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
      const payload = asMonitorRecord(event.payload);
      return {
        id: `${event.id}`,
        fromAgent: monitorStringField(payload?.from_agent) ?? event.agent_name ?? "agent",
        toAgent: monitorStringField(payload?.to_agent) ?? "agent",
        fromStepId: monitorStringField(payload?.from_step_id),
        toStepId: monitorStringField(payload?.to_step_id),
        attachedToStepId: monitorStringField(payload?.attached_to_step_id),
        dispatchKind: monitorStringField(payload?.dispatch_kind) ?? "blocking",
        contentPreview: monitorStringField(payload?.content_preview) ?? "",
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
    .slice(0, 800);
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
  return {
    ...current,
    ...detail,
    chat_title: current.chat_title,
    project_name: current.project_name,
    latest_event_type: detail.events[detail.events.length - 1]?.event_type ?? current.latest_event_type,
    event_count: detail.events.length,
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

function buildHttpWireDump(entry: MonitorNetworkEvent) {
  const method = entry.method || "GET";
  const path = entry.path || "/";
  const version = httpVersion(entry);
  const statusCode = entry.status_code ?? 200;
  const frameType = String(entry.metadata?.frame_type || "").toLowerCase();
  const lines: string[] = [];

  if (entry.raw_request || frameType === "request") {
    lines.push(`${method} ${path} ${version}`);
    lines.push(...renderHttpHeaders(entry.request_headers, entry.host));
    lines.push("");
    if (entry.raw_request) {
      lines.push(entry.raw_request);
    }
    return lines.join("\n").trimEnd();
  }

  lines.push(`${version} ${statusCode}`);
  lines.push(...renderHttpHeaders(entry.response_headers));
  lines.push("");
  if (entry.raw_response) {
    lines.push(entry.raw_response);
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
  const merged = new Map<number, MonitorOverview["recent_runtime"][number]>();
  current.forEach((item) => {
    merged.set(item.id, item);
  });
  incoming.forEach((item) => {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  });
  return [...merged.values()]
    .sort(compareMonitorItemsNewest)
    .slice(0, MONITOR_RUNTIME_LIMIT);
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
      null,
    resume_supported: Boolean(payload.resume_supported),
    action_taken: monitorStringField(resolution.action_taken),
    replay_status: monitorStringField(resolution.replay_status),
    replay_success: typeof resolution.replay_success === "boolean" ? resolution.replay_success : null,
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
  const exists = current.recent_runtime.some((entry) => entry.id === item.id);
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

function looksLikeJson(value: string) {
  const trimmed = value.trim();
  return trimmed.startsWith("{") || trimmed.startsWith("[");
}

function markdownCodeFence(content: string, language = "") {
  const normalized = content.replace(/\n+$/g, "");
  return `\`\`\`${language}\n${normalized}\n\`\`\``;
}

function readCardString(card: Record<string, unknown>, key: string) {
  const value = card[key];
  return typeof value === "string" ? value : "";
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

  const card = detail.card ?? {};
  const sections: BrainEventSection[] = [];
  const cardType = typeof card.type === "string" ? card.type : event.runtimeType || "";
  const exchangeMeta = formatUnknownDetail({
    from: event.fromEntity || event.source,
    to: event.toEntity || null,
    type: cardType || "runtime",
    model: card.model ?? null,
    tool: card.tool ?? null,
    turn: card.turn ?? null,
    tokens_in: card.tokens_in ?? null,
    tokens_out: card.tokens_out ?? null,
    duration_ms: card.duration_ms ?? null,
    success: card.success ?? null,
    client_turn_id: event.clientTurnId || (typeof card.client_turn_id === "string" ? card.client_turn_id : null),
  });

  if (cardType === "llm_call") {
    const systemPrompt = readCardString(card, "system_prompt");
    const promptMessages = formatUnknownDetail(card.prompt_messages);
    const plannedTools = formatUnknownDetail(card.tool_calls);
    const response = readCardString(card, "response");
    const rawResponse = formatUnknownDetail(card.raw_response);

    if (event.phase === "outbound") {
      if (systemPrompt) {
        sections.push({
          label: `System Prompt · ${routeLabel}`,
          content: systemPrompt,
          tone: "accent",
          format: "text",
          variant: "meta",
        });
      }
      if (promptMessages) {
        sections.push({
          label: `Raw Prompt Payload · ${routeLabel}`,
          content: promptMessages,
          tone: "accent",
          format: "json",
          variant: "raw",
        });
      }
      if (plannedTools) {
        sections.push({
          label: `Planned Tools · ${routeLabel}`,
          content: plannedTools,
          tone: "warning",
          format: "json",
          variant: "raw",
        });
      }
    } else {
      if (response) {
        sections.push({
          label: `Response · ${routeLabel}`,
          content: response,
          tone: "success",
          format: "text",
          variant: "result",
        });
      }
      if (rawResponse) {
        sections.push({
          label: `Raw LLM Response · ${routeLabel}`,
          content: rawResponse,
          tone: "neutral",
          format: "json",
          variant: "raw",
        });
      }
    }
  } else if (cardType === "tool_call") {
    const argumentsPayload = formatUnknownDetail(card.arguments);
    const resultPayload = formatUnknownDetail(card.result);
    if (event.phase === "outbound") {
      if (argumentsPayload) {
        sections.push({
          label: `Raw Tool Input · ${routeLabel}`,
          content: argumentsPayload,
          tone: "accent",
          format: "json",
          variant: "raw",
        });
      }
    } else if (resultPayload) {
      sections.push({
        label: `${(card.success as boolean | undefined) === false ? "Tool Error" : "Tool Output"} · ${routeLabel}`,
        content: resultPayload,
        tone: (card.success as boolean | undefined) === false ? "error" : "success",
        format: looksLikeJson(resultPayload) ? "json" : "text",
        variant: "result",
      });
    }
  } else if (cardType === "agent_error") {
    const summary = readCardString(card, "summary");
    const errorText = readCardString(card, "error");
    const detailMarkdown = readCardString(card, "content");
    if (summary) {
      sections.push({
        label: `Failure Summary · ${routeLabel}`,
        content: summary,
        tone: "warning",
        format: "text",
        variant: "result",
      });
    }
    if (errorText) {
      sections.push({
        label: `Error · ${routeLabel}`,
        content: errorText,
        tone: "error",
        format: "text",
        variant: "result",
      });
    }
    if (detailMarkdown) {
      sections.push({
        label: `Failure Detail · ${routeLabel}`,
        content: detailMarkdown,
        tone: "error",
        format: "text",
        variant: "raw",
      });
    }
  } else {
    const candidates: Array<
      [string, unknown, BrainEventSection["tone"], BrainEventSection["format"], BrainEventSection["variant"]]
    > = [
      ["Content", card.content, "neutral", "text", "result"],
      ["Preview", card.content_preview, "neutral", "text", "result"],
      ["Summary", card.summary, "neutral", "text", "result"],
      ["Arguments", card.arguments, "accent", "json", "raw"],
      ["Result", card.result, "success", "text", "result"],
    ];
    for (const [label, value, tone, format, variant] of candidates) {
      const content = formatUnknownDetail(value);
      if (content) {
        sections.push({ label, content, tone, format, variant });
      }
    }
  }

  if (exchangeMeta) {
    sections.push({ label: `Exchange Meta · ${routeLabel}`, content: exchangeMeta, tone: "neutral", format: "json", variant: "meta" });
  }

  const rawCard = formatUnknownDetail(card);
  if (rawCard) {
    sections.push({ label: `Raw Event Payload · ${routeLabel}`, content: rawCard, tone: "neutral", format: "json", variant: "raw" });
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

function FlowSvg({ llmCalls, toolCalls, totalTokens }: { llmCalls: number; toolCalls: number; totalTokens: number }) {
  return (
    <svg className="flow-svg" viewBox="0 0 980 550" preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="flow-grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#edf1f5" strokeWidth="0.5" />
        </pattern>
      </defs>
      <rect width="980" height="550" fill="#ffffff" rx="12" />
      <rect width="980" height="550" fill="url(#flow-grid)" />

      <path d="M 60 56 C 60 70, 65 85, 75 100" fill="none" stroke="#d1d5db" strokeWidth="3" />
      <path d="M 60 56 C 55 90, 60 140, 75 170" fill="none" stroke="#d1d5db" strokeWidth="3" />
      <path d="M 130 120 C 150 120, 160 165, 180 170" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 130 190 C 150 190, 160 185, 180 183" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 290 183 C 305 183, 315 175, 330 175" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 510 160 C 530 150, 545 143, 560 139" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 510 175 C 530 175, 545 189, 560 189" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 510 185 C 530 200, 545 230, 560 239" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 510 215 C 530 290, 545 370, 560 389" fill="none" stroke="#cbd5e1" strokeWidth="3" />
      <path d="M 380 220 C 300 350, 150 400, 95 450" fill="none" stroke="#d1d5db" strokeDasharray="6 4" strokeWidth="2" />
      <path d="M 615 408 C 550 420, 470 435, 425 450" fill="none" stroke="#d1d5db" strokeDasharray="6 4" strokeWidth="2" />

      <g>
        <circle cx="60" cy="30" r="22" fill="#7c3aed" />
        <text x="60" y="68" fill="#7c3aed" fontSize="13" fontWeight="800" textAnchor="middle">
          You
        </text>
      </g>

      <g>
        <rect x="20" y="100" width="110" height="40" rx="10" fill="#2196f3" />
        <text x="75" y="125" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          TUI / Web
        </text>
      </g>
      <g>
        <rect x="20" y="170" width="110" height="40" rx="10" fill="#2e8b7a" />
        <text x="75" y="195" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          API / WS
        </text>
      </g>
      <g>
        <rect x="180" y="160" width="110" height="45" rx="10" fill="#37474f" />
        <text x="235" y="188" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          Gateway
        </text>
      </g>
      <g>
        <rect x="330" y="115" width="180" height="120" rx="12" fill="#c62828" />
        <text x="345" y="133" fill="#ffccbc" fontSize="8" style={{ textTransform: "uppercase", letterSpacing: "1px" }}>
          Agent Runtime
        </text>
        <text x="420" y="155" fontSize="20" textAnchor="middle">
          Brain
        </text>
        <text x="356" y="176" fill="#ffd54f" fontSize="12" fontWeight="700">
          LLM calls: {formatNumber(llmCalls)}
        </text>
        <text x="356" y="190" fill="#ffccbc" fontSize="10">
          Tokens: {formatNumber(totalTokens)}
        </text>
        <text x="356" y="206" fill="#ffccbc" fontSize="10">
          Tool actions: {formatNumber(toolCalls)}
        </text>
      </g>
      <g>
        <rect x="560" y="120" width="110" height="38" rx="10" fill="#e65100" />
        <text x="615" y="144" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          Exec
        </text>
      </g>
      <g>
        <rect x="560" y="170" width="110" height="38" rx="10" fill="#6a1b9a" />
        <text x="615" y="194" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          Web
        </text>
      </g>
      <g>
        <rect x="560" y="220" width="110" height="38" rx="10" fill="#00695c" />
        <text x="615" y="244" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          Search
        </text>
      </g>
      <g>
        <rect x="560" y="370" width="110" height="38" rx="10" fill="#283593" />
        <text x="615" y="394" fill="#ffffff" fontSize="13" fontWeight="700" textAnchor="middle">
          Memory
        </text>
      </g>
      <g>
        <rect x="20" y="450" width="160" height="54" rx="12" fill="#0f172a" />
        <text x="100" y="476" fill="#cbd5e1" fontSize="10" textAnchor="middle">
          Runtime feed mirrored from Catown
        </text>
        <text x="100" y="492" fill="#ffffff" fontSize="14" fontWeight="700" textAnchor="middle">
          Self-bootstrap monitor
        </text>
      </g>
      <text x="480" y="520" fill="#667085" fontSize="10" textAnchor="middle">
        {"Channels -> Gateway -> Brain -> Tools / Memory"}
      </text>
    </svg>
  );
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
        detail: item.preview || "Approval rejected before action execution.",
        createdAt: item.created_at,
      });
    } else if (type === "gate_blocked") {
      events.push({
        id: `sec-${item.id}`,
        severity: "medium",
        title: item.title,
        detail: item.preview || "Action is waiting for operator approval.",
        createdAt: item.created_at,
      });
    } else if (type === "tool_call" && item.success === false) {
      events.push({
        id: `sec-${item.id}`,
        severity: "critical",
        title: item.title,
        detail: item.preview || "Tool execution failed.",
        createdAt: item.created_at,
      });
    } else if (type.includes("error")) {
      events.push({
        id: `sec-${item.id}`,
        severity: "high",
        title: item.title,
        detail: item.preview || "Runtime error detected.",
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

  if (item.type === "llm_call") {
    const llmTarget = "LLM";
    const outboundDetail =
      compactMonitorText(item.prompt_preview) ||
      [item.model, typeof item.turn === "number" ? `turn ${item.turn}` : ""].filter(Boolean).join(" · ") ||
      "Prompt payload captured.";
    const inboundDetail =
      compactMonitorText(item.response_preview || item.preview) ||
      "Model response captured.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-outbound`,
        source: fromEntity,
        operationLabel: "llm",
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
        operationLabel: "llm",
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
    const outboundDetail = compactMonitorText(item.arguments_preview) || "Tool call issued.";
    const inboundDetail = compactMonitorText(item.response_preview || item.preview) || "Tool output returned.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-outbound`,
        source: fromEntity,
        operationLabel: toolEntity,
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
        operationLabel: toolEntity,
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
    const failureDetail = compactMonitorText(item.response_preview || item.preview) || "Agent stream failed before a final reply was saved.";
    return [
      {
        ...common,
        id: `runtime-${item.id}-error`,
        source: fromEntity,
        operationLabel: "error",
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
      operationLabel: item.stage || runtimeLabel(item.type),
      fromEntity,
      toEntity: toEntity || undefined,
      phase: "state",
      category: "runtime",
      label: toEntity ? buildCommunicationLabel(fromEntity, toEntity) : item.title,
      detail: item.preview || `${item.chat_title} · ${item.stage || runtimeLabel(item.type)}`,
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

function buildAgentDirectory(projects: ProjectSummary[], agents: AgentInfo[]) {
  const directory = new Map<string, AgentInfo & { projects: string[] }>();

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
  const [approvalQueueResponse, setApprovalQueueResponse] = useState<MonitorApprovalQueueResponse | null>(null);
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
  const [taskRunDetailErrors, setTaskRunDetailErrors] = useState<Record<number, string>>({});
  const [taskRunDetailLoading, setTaskRunDetailLoading] = useState<Record<number, boolean>>({});
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
      const [nextOverview, nextUsage, nextProjects, nextAgents, nextConfig, nextNetwork, nextTaskRuns, nextApprovalQueue] = await Promise.all([
        api.getMonitorOverview(),
        api.getMonitorUsage(historyRange),
        api.getProjects(),
        api.getAgents(),
        api.getConfig(),
        api.getMonitorNetwork(300, networkCategory, networkFilter),
        api.getMonitorTaskRuns(historyRange),
        api.getMonitorApprovalQueue("all", 120),
      ]);
      setOverview(nextOverview);
      setUsage(nextUsage);
      setTaskRunsResponse(nextTaskRuns);
      setApprovalQueueResponse(nextApprovalQueue);
      setProjects(nextProjects);
      setAgents(nextAgents);
      setConfig(nextConfig);
      setNetworkEntries(nextNetwork.entries);
      setConnectionState("connected");
      setError("");
    } catch (nextError) {
      setConnectionState("disconnected");
      setError(nextError instanceof Error ? nextError.message : "Failed to load Catown monitor");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [historyRange, networkCategory, networkFilter]);

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
        const response = await api.getMonitorNetwork(300, networkCategory, networkFilter);
        if (cancelled) return;

        setNetworkEntries(mergeMonitorNetwork([], response.entries));
        networkCursorRef.current = response.latest_id;

        streamAbortController = new AbortController();
        const params = new URLSearchParams({
          cursor: String(response.latest_id),
          category: networkCategory,
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
  }, [networkCategory, networkFilter]);

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

  const selectedTaskRunSummary = useMemo(
    () => visibleTaskRuns.find((entry) => entry.id === selectedTaskRunId) ?? visibleTaskRuns[0] ?? null,
    [selectedTaskRunId, visibleTaskRuns],
  );
  const selectedTaskRunDetail = selectedTaskRunSummary ? taskRunDetails[selectedTaskRunSummary.id] ?? null : null;
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
        const detail = await api.getTaskRunDetail(taskRunId);
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

  useEffect(() => {
    if (!selectedTaskRunSummary) return;
    if (taskRunDetails[selectedTaskRunSummary.id] || taskRunDetailLoading[selectedTaskRunSummary.id]) return;
    void loadTaskRunDetail(selectedTaskRunSummary.id);
  }, [loadTaskRunDetail, selectedTaskRunSummary, taskRunDetailLoading, taskRunDetails]);

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
  const contextWindow = config?.global_llm?.default_model?.includes("128") ? 128000 : DEFAULT_CONTEXT_WINDOW;
  const contextUsage = overview ? clamp((overview.usage_window.total_tokens / contextWindow) * 100, 0, 100) : 0;

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

  const approvalsPending = useMemo(
    () => approvalQueueResponse?.entries.filter((item) => item.status === "pending") ?? [],
    [approvalQueueResponse],
  );
  const approvalsHistory = useMemo(
    () => approvalQueueResponse?.entries.filter((item) => item.status !== "pending") ?? [],
    [approvalQueueResponse],
  );

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
    return entries.filter((entry) => entry.aggregated === false || !entry.flow_id);
  }, [networkEntries, showInternalNetwork]);

  async function refreshMonitor() {
    await loadMonitor(true);
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
      const response = await api.getMonitorNetwork(300, networkCategory, networkFilter);
      setNetworkEntries(mergeMonitorNetwork([], response.entries));
      networkCursorRef.current = Math.max(networkCursorRef.current, response.latest_id);
      setNetworkStreamState((current) => (current === "connected" ? current : "connecting"));
    } catch {
      setNetworkStreamState("disconnected");
      // Keep the current snapshot on fetch failures.
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
      await loadMonitor(true);
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

      <section className={`page ${activePage === "overview" ? "active" : ""}`} id="page-overview">
        <div className="card" style={{ marginBottom: 14, display: "grid", gridTemplateColumns: "1fr auto", gap: 16, alignItems: "start" }}>
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
          <div style={{ textAlign: "right", minWidth: 180 }}>
            <div className="small-note">Last monitor window</div>
            <div className="mono" style={{ fontSize: 13, marginTop: 6 }}>
              {formatNumber(overview?.usage_window.llm_calls)} llm / {formatNumber(overview?.usage_window.tool_calls)} tools
            </div>
            <div className="small-note" style={{ marginTop: 6 }}>
              {formatNumber(overview?.usage_window.tool_errors)} tool errors · {formatTimeAgo(overview?.captured_at)}
            </div>
          </div>
        </div>

        <div className="refresh-bar" style={{ marginBottom: 8 }}>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻
          </button>
          <span className="pulse" />
          <span className="live-badge">Live</span>
          <span className="refresh-time">Live subscription · Runtime cards window {overview?.usage_window.runtime_cards_considered ?? 0}</span>
        </div>

        <div className="stats-footer">
          <div className="stats-footer-item">
            <span className="stats-footer-icon">$</span>
            <div>
              <div className="stats-footer-label">Spending</div>
              <div className="stats-footer-value">{formatCost(overview?.usage_window.estimated_cost_usd)}</div>
              <div className="stats-footer-sub">input {formatNumber(overview?.usage_window.input_tokens)} · output {formatNumber(overview?.usage_window.output_tokens)}</div>
            </div>
          </div>
          <div className="stats-footer-item">
            <span className="stats-footer-icon">AI</span>
            <div>
              <div className="stats-footer-label">Model</div>
              <div className="stats-footer-value">{modelPrimary}</div>
              <div className="stats-footer-sub">{formatNumber(modelRows.length)} distinct models observed</div>
            </div>
          </div>
          <div className="stats-footer-item">
            <span className="stats-footer-icon">Tok</span>
            <div>
              <div className="stats-footer-label">Tokens</div>
              <div className="stats-footer-value">{formatNumber(overview?.usage_window.total_tokens)}</div>
              <div className="stats-footer-sub">context window usage {formatPercent(contextUsage, 1)}</div>
            </div>
          </div>
          <div className="stats-footer-item">
            <span className="stats-footer-icon">Chat</span>
            <div>
              <div className="stats-footer-label">Sessions</div>
              <div className="stats-footer-value">{formatNumber(overview?.system.stats.chatrooms)}</div>
              <div className="stats-footer-sub">{formatNumber(clusters.length)} hot chats in runtime window</div>
            </div>
          </div>
          <div className="stats-footer-item">
            <span className="stats-footer-icon">OK</span>
            <div>
              <div className="stats-footer-label">Reliability</div>
              <div className="stats-footer-value">{formatPercent(securityScore)}</div>
              <div className="stats-footer-sub">{passedChecks}/{securityChecks.length || 0} checks passed</div>
            </div>
          </div>
          <div className="stats-footer-item">
            <span className="stats-footer-icon">Q</span>
            <div>
              <div className="stats-footer-label">Approvals</div>
              <div className="stats-footer-value">{formatNumber(approvalQueueResponse?.counts.pending ?? overview?.system.stats.approval_queue_pending)}</div>
              <div className="stats-footer-sub">{formatNumber(approvalQueueResponse?.counts.all ?? overview?.system.stats.approval_queue_total)} queued decisions</div>
            </div>
          </div>
        </div>

        <div className="overview-split">
          <div>
            <div className="overview-flow-pane">
              <div className="flow-container" id="overview-flow-container">
                <FlowSvg
                  llmCalls={overview?.usage_window.llm_calls ?? 0}
                  toolCalls={overview?.usage_window.tool_calls ?? 0}
                  totalTokens={overview?.usage_window.total_tokens ?? 0}
                />
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

      <section className={`page ${activePage === "flow" ? "active" : ""}`} id="page-flow">
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
          <FlowSvg
            llmCalls={overview?.usage_window.llm_calls ?? 0}
            toolCalls={overview?.usage_window.tool_calls ?? 0}
            totalTokens={overview?.usage_window.total_tokens ?? 0}
          />
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
                {item.preview ? <div className="feed-preview">{item.preview}</div> : null}
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className={`page ${activePage === "network" ? "active" : ""}`} id="page-network">
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
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
            marginTop: 0,
            marginBottom: 10,
            width: "100%",
          }}
        >
          <p className="small-note" style={{ margin: 0, minWidth: 0 }}>
            Debug view only. No aggregation; each record is shown as one title line plus one raw HTTP wire block.
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto", flexWrap: "wrap", width: "min(100%, 560px)" }}>
            <label className="small-note" style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap", flexShrink: 0 }}>
              <input
                type="checkbox"
                checked={showInternalNetwork}
                onChange={(event) => setShowInternalNetwork(event.target.checked)}
              />
              Show heartbeat / internal requests
            </label>
            <input
              type="text"
              className="search-input"
              placeholder="Filter by host, path, peer, raw text..."
              value={networkFilter}
              onChange={(event) => setNetworkFilter(event.target.value)}
              style={{ flex: "1 1 280px", width: "100%", minWidth: 180, maxWidth: "100%" }}
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
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", width: "100%", minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0, flex: "1 1 420px", flexWrap: "wrap", width: "100%" }}>
                        <span
                          title={direction}
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            justifyContent: "center",
                            width: 28,
                            height: 28,
                            borderRadius: 999,
                            background: `${flowColor}1a`,
                            color: flowColor,
                            flexShrink: 0,
                          }}
                        >
                          <fromVisual.Icon size={16} strokeWidth={2.2} />
                        </span>
                        <strong style={{ minWidth: 0, maxWidth: "100%", overflowWrap: "anywhere", wordBreak: "break-word" }}>
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
                  <pre className="monitor-pre" style={{ marginTop: 8, padding: "8px 10px", fontSize: 12, lineHeight: 1.45 }}>
                    {buildNetworkRawDump(entry)}
                  </pre>
                </details>
              );
            })}
          </div>
        ) : (
          <div className="muted-block">No network events captured for the current filter yet.</div>
        )}
      </section>

      <section className={`page ${activePage === "usage" ? "active" : ""}`} id="page-usage">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
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
                      <div className="feed-preview">{item.preview || "Tool execution returned an error."}</div>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        ) : null}

        <div className="usage-panels">
          <div className="card">
            <SectionTitle title="Cost by Plugin / Skill" subtitle="UX copied first. Attribution remains heuristic for now." />
            <div className="skill-grid">
              {skills.slice(0, 6).map((skill) => (
                <div key={skill.name} className="skill-card">
                  <strong>{skill.name}</strong>
                  <div className="small-note">{skill.projects.length} projects · {skill.agents.length} agents</div>
                  <div className="skill-card__meta">
                    {skill.agents.slice(0, 3).map((agent) => (
                      <span key={agent} className="tag">
                        {agent}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
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
            <div className="cluster-grid">
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
            </div>
          </div>
        </div>
      </section>

      <section className={`page ${activePage === "transcripts" ? "active" : ""}`} id="page-transcripts">
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

      <section className={`page ${activePage === "logs" ? "active" : ""}`} id="page-logs">
        <div className="refresh-bar">
          <button type="button" className="refresh-btn" onClick={() => void refreshLogs()}>
            ↻ Refresh
          </button>
          <input
            value={logFilter}
            onChange={(event) => setLogFilter(event.target.value)}
            placeholder="Filter logs..."
            style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border-primary)", minWidth: 220 }}
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

      <section className={`page ${activePage === "memory" ? "active" : ""}`} id="page-memory">
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

      <section className={`page ${activePage === "brain" ? "active" : ""}`} id="page-brain">
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

      <section className={`page ${activePage === "skills" ? "active" : ""}`} id="page-skills">
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
          <div className="skill-grid">
            {skills.map((skill) => (
              <div key={skill.name} className="skill-card">
                <strong>{skill.name}</strong>
                <div className="small-note">{skill.detail}</div>
                <div className="skill-card__meta">
                  {skill.agents.map((agent) => (
                    <span key={agent} className="tag">
                      {agent}
                    </span>
                  ))}
                </div>
                <div className="small-note" style={{ marginTop: 10 }}>Projects: {skill.projects.join(", ") || "Standalone only"}</div>
              </div>
            ))}
          </div>
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

      <section className={`page ${activePage === "models" ? "active" : ""}`} id="page-models">
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

      <section className={`page ${activePage === "context" ? "active" : ""}`} id="page-context">
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
                <div className="kpi-card__value">0</div>
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
            <div className="muted-block">No compactions yet — Catown is not emitting context compaction events to the monitor API.</div>
          </div>
        </div>
      </section>

      <section className={`page ${activePage === "subagents" ? "active" : ""}`} id="page-subagents">
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

      <section className={`page ${activePage === "history" ? "active" : ""}`} id="page-history">
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
                      {run.summary || run.user_request || "No summary recorded for this run."}
                    </div>
                    <div className="run-history-item__foot">
                      <span>{run.event_count} events</span>
                      {run.latest_event_type ? <span>{titleCaseLabel(run.latest_event_type)}</span> : null}
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
                <div className="simple-list" style={{ marginTop: 12 }}>
                  <div className="simple-row">
                    <strong>Target Agent</strong>
                    <div className="small-note">{selectedTaskRunSummary.target_agent_name || "not pinned"}</div>
                  </div>
                  <div className="simple-row">
                    <strong>Summary</strong>
                    <div className="small-note">{selectedTaskRunSummary.summary || selectedTaskRunSummary.user_request || "No summary recorded."}</div>
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
                {selectedTaskRunDetail ? (
                  <div className="simple-list" style={{ marginTop: 12 }}>
                    {selectedTaskRunDetail.events.map((event) => (
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
                        {event.payload && Object.keys(event.payload).length > 0 ? (
                          <details className="run-event-row__payload">
                            <summary>Payload</summary>
                            <pre>{formatRawMonitorValue(event.payload)}</pre>
                          </details>
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

      <section className={`page ${activePage === "limits" ? "active" : ""}`} id="page-limits">
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

      <section className={`page ${activePage === "approvals" ? "active" : ""}`} id="page-approvals">
        <div className="refresh-bar" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title">Approvals</div>
            <div className="section-subtitle">Visual policy builder + approval queue baseline copied from ClawMetry.</div>
          </div>
          <div className="inline-actions">
            <span className="small-note">{approvalQueueResponse?.counts.pending ?? approvalsPending.length} pending</span>
            <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
              ↻ Refresh
            </button>
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
                    <div className={`feed-badge feed-badge--${approvalStatusTone(item.status)}`}>{item.status}</div>
                    <div className="small-note">{formatTimeAgo(item.created_at)}</div>
                  </div>
                  <div className="small-note" style={{ marginTop: 6 }}>
                    {item.chat_title || "Unknown chat"} {item.project_name ? `· ${item.project_name}` : ""}
                    {item.task_run_title ? ` · ${item.task_run_title}` : ""}
                  </div>
                  <div className="muted-block" style={{ marginTop: 8 }}>
                    {item.request_preview || item.summary || "Awaiting operator decision."}
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
          <div className="approval-grid">
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
          </div>
        </div>
        <div className="split-panels">
          <div className="card">
            <SectionTitle title="Get Notified" />
            <div className="integration-grid">
              {APPROVAL_INTEGRATIONS.map((integration) => (
                <div key={integration.name} className="integration-card">
                  <strong>{integration.name}</strong>
                  <div className="small-note">{integration.description}</div>
                  <div className="integration-card__meta">
                    <span className="tag">{integration.status}</span>
                  </div>
                </div>
              ))}
            </div>
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

      <section className={`page ${activePage === "clusters" ? "active" : ""}`} id="page-clusters">
        <div className="refresh-bar">
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, flex: 1 }}>Session Clusters</h2>
          <button type="button" className="refresh-btn" onClick={() => void refreshMonitor()} disabled={refreshing}>
            ↻ Refresh
          </button>
        </div>
        <div className="cluster-grid">
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
        </div>
      </section>

      <section className={`page ${activePage === "security" ? "active" : ""}`} id="page-security">
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
            <div className="security-grid">
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
            </div>
          ) : null}
        </div>
      </section>

      <section className={`page ${activePage === "crons" ? "active" : ""}`} id="page-crons">
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

      <section className={`page ${activePage === "nemoclaw" ? "active" : ""}`} id="page-nemoclaw">
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

      <section className={`page ${activePage === "version-impact" ? "active" : ""}`} id="page-version-impact">
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
