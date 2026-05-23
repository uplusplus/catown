import { useMemo } from "react";

import type { AgentInfo, MonitorOverview } from "../types";
import { buildAgentThemeStyle, resolveAgentLabel } from "../utils/agentColors";

type RuntimeRelationType = "message" | "delegate" | "approval";
type RuntimeActionType = "tool" | "background" | "approval" | "handoff" | "consult" | "task";
type RuntimeActionState = "running" | "waiting" | "done" | "failed";

type RuntimeRelation = {
  id: string;
  actor: string;
  target: string;
  type: RuntimeRelationType;
  label: string;
  detail: string;
  timestamp: string;
};

type RuntimeAction = {
  id: string;
  actor: string;
  type: RuntimeActionType;
  title: string;
  detail: string;
  timestamp: string;
  state: RuntimeActionState;
};

type RuntimeLane = {
  actor: string;
  lastSeenAt: string;
  relations: RuntimeRelation[];
  actions: RuntimeAction[];
};

type RuntimeMapViewModel = {
  lanes: RuntimeLane[];
  summary: {
    activeAgents: number;
    pendingApprovals: number;
    runningActions: number;
    backgroundTasks: number;
  };
};

function compactText(value: unknown, limit = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function actorLabel(value: string | null | undefined, agents: AgentInfo[]) {
  const normalized = (value || "").trim();
  if (!normalized) return "System";
  if (normalized.toLowerCase() === "user") return "User";
  return resolveAgentLabel(normalized, agents);
}

function initials(value: string) {
  return value
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function relationVerb(type: RuntimeRelationType) {
  switch (type) {
    case "delegate":
      return "-delegate->";
    case "approval":
      return "-approval->";
    default:
      return "--msg->";
  }
}

function actionVerb(type: RuntimeActionType) {
  switch (type) {
    case "background":
      return "-> Background";
    case "approval":
      return "-> Approval";
    case "handoff":
      return "-> Handoff";
    case "consult":
      return "-> Consult";
    case "task":
      return "-> Task";
    default:
      return "-> ToolCall";
  }
}

function actionStateLabel(state: RuntimeActionState) {
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

function stateFromRuntime(item: MonitorOverview["recent_runtime"][number]): RuntimeActionState {
  const type = (item.type || "").toLowerCase();
  if (item.success === false || type.includes("error") || type.includes("rejected") || type.includes("failed")) return "failed";
  if (type.includes("blocked") || type.includes("approval")) return "waiting";
  if (type.includes("started") || type === "tool_call") return "running";
  return "done";
}

function buildRuntimeMapViewModel(overview: MonitorOverview | null): RuntimeMapViewModel {
  const lanes = new Map<string, RuntimeLane>();

  const pushRelation = (relation: RuntimeRelation | null) => {
    if (!relation) return;
    const key = relation.actor.trim() || "System";
    const existing = lanes.get(key);
    if (existing) {
      existing.relations.push(relation);
      if (new Date(relation.timestamp).getTime() > new Date(existing.lastSeenAt).getTime()) existing.lastSeenAt = relation.timestamp;
      return;
    }
    lanes.set(key, {
      actor: key,
      lastSeenAt: relation.timestamp,
      relations: [relation],
      actions: [],
    });
  };

  const pushAction = (action: RuntimeAction | null) => {
    if (!action) return;
    const key = action.actor.trim() || "System";
    const existing = lanes.get(key);
    if (existing) {
      existing.actions.push(action);
      if (new Date(action.timestamp).getTime() > new Date(existing.lastSeenAt).getTime()) existing.lastSeenAt = action.timestamp;
      return;
    }
    lanes.set(key, {
      actor: key,
      lastSeenAt: action.timestamp,
      relations: [],
      actions: [action],
    });
  };

  for (const item of overview?.recent_runtime ?? []) {
    const actor = String(item.from_entity || item.agent || "System").trim() || "System";
    const target = String(item.to_entity || "").trim();
    const timestamp = item.created_at || new Date().toISOString();
    const detail = compactText(item.preview || item.response_preview || item.arguments_preview || item.prompt_preview || item.title);
    const state = stateFromRuntime(item);
    const type = (item.type || "").toLowerCase();

    if (type === "agent_message") {
      pushRelation({
        id: `runtime:${item.id}:message`,
        actor,
        target: target || "Agent",
        type: "message",
        label: item.title || `${actor} -> ${target || "Agent"}`,
        detail: detail || "Agent message recorded.",
        timestamp,
      });
      continue;
    }

    if (type === "boss_instruction") {
      pushRelation({
        id: `runtime:${item.id}:boss`,
        actor: "Boss",
        target: target || actor,
        type: "message",
        label: item.title || "Boss instruction",
        detail: detail || "Boss instruction recorded.",
        timestamp,
      });
      continue;
    }

    if (type.includes("approval") || type.includes("gate_")) {
      pushRelation({
        id: `runtime:${item.id}:approval-relation`,
        actor,
        target: "User",
        type: "approval",
        label: item.operation_label || item.tool_name || item.title || "Approval",
        detail: detail || "Approval transition recorded.",
        timestamp,
      });
      pushAction({
        id: `runtime:${item.id}:approval-action`,
        actor,
        type: "approval",
        title: item.operation_label || item.tool_name || item.title || "Approval",
        detail: detail || "Approval transition recorded.",
        timestamp,
        state,
      });
      continue;
    }

    if (type === "tool_call") {
      const title = item.tool_name || item.operation_label || item.title || "Tool";
      pushAction({
        id: `runtime:${item.id}:tool`,
        actor,
        type: title.toLowerCase() === "run_shell" ? "background" : "tool",
        title,
        detail: detail || "Tool call recorded.",
        timestamp,
        state,
      });
      continue;
    }

    if (type === "llm_call") {
      pushAction({
        id: `runtime:${item.id}:llm`,
        actor,
        type: "consult",
        title: item.model || item.operation_label || "LLM",
        detail: detail || "LLM interaction recorded.",
        timestamp,
        state,
      });
      continue;
    }

    if (type.includes("stage_") || type.includes("pipeline") || type.includes("skill")) {
      pushAction({
        id: `runtime:${item.id}:task`,
        actor,
        type: "task",
        title: item.operation_label || item.stage || item.title || "Task",
        detail: detail || "Runtime task recorded.",
        timestamp,
        state,
      });
      continue;
    }

    pushAction({
      id: `runtime:${item.id}:generic`,
      actor,
      type: target && target !== "User" ? "handoff" : "task",
      title: item.operation_label || item.title || "Runtime",
      detail: detail || "Runtime activity recorded.",
      timestamp,
      state,
    });
  }

  const orderedLanes = Array.from(lanes.values())
    .map((lane) => ({
      ...lane,
      relations: [...lane.relations]
        .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())
        .slice(0, 8),
      actions: [...lane.actions]
        .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime())
        .slice(-8),
    }))
    .sort((left, right) => new Date(right.lastSeenAt).getTime() - new Date(left.lastSeenAt).getTime());

  const summary = orderedLanes.reduce(
    (acc, lane) => {
      const hasLive = lane.actions.some((action) => action.state === "running" || action.state === "waiting")
        || lane.relations.some((relation) => relation.type === "approval");
      if (hasLive) acc.activeAgents += 1;
      acc.pendingApprovals += lane.actions.filter((action) => action.type === "approval" && action.state === "waiting").length;
      acc.runningActions += lane.actions.filter((action) => action.state === "running" || action.state === "waiting").length;
      acc.backgroundTasks += lane.actions.filter((action) => action.type === "background" && action.state === "running").length;
      return acc;
    },
    { activeAgents: 0, pendingApprovals: 0, runningActions: 0, backgroundTasks: 0 },
  );

  return { lanes: orderedLanes, summary };
}

export function MonitorRuntimeMap({
  overview,
  agents,
}: {
  overview: MonitorOverview | null;
  agents: AgentInfo[];
}) {
  const viewModel = useMemo(() => buildRuntimeMapViewModel(overview), [overview]);

  if (viewModel.lanes.length === 0) {
    return <div className="empty-state">Runtime relationship lanes will appear here after monitor captures agent activity.</div>;
  }

  return (
    <div className="page-grid">
      <div className="card">
        <div className="section-title">Preserved Runtime Map</div>
        <p className="small-note" style={{ marginTop: 0 }}>
          This keeps the previous lane-based runtime relationship view inside Monitor for comparison and deeper inspection.
        </p>
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
      </div>

      <div className="card">
        <div className="section-title">Agent Lanes</div>
        <div className="runtime-monitor__lanes" aria-label="Runtime monitor lanes">
          {viewModel.lanes.map((lane) => {
            const label = actorLabel(lane.actor, agents);
            return (
              <section key={lane.actor} className="runtime-monitor__lane" style={buildAgentThemeStyle(lane.actor, agents)}>
                <div className="runtime-monitor__lane-header">
                  <div className="runtime-monitor__lane-avatar">{initials(label)}</div>
                  <div className="runtime-monitor__lane-copy">
                    <strong>{label}</strong>
                    <small>{formatTime(lane.lastSeenAt)}</small>
                  </div>
                </div>

                <div className="runtime-monitor__lane-body">
                  <div className="runtime-monitor__relations">
                    {lane.relations.length === 0 ? (
                      <div className="runtime-monitor__placeholder">No recent agent-to-agent edges.</div>
                    ) : (
                      lane.relations.map((relation) => (
                        <article key={relation.id} className={`runtime-monitor__relation runtime-monitor__relation--${relation.type}`}>
                          <div className="runtime-monitor__relation-line">
                            <span>{actorLabel(relation.actor, agents)}</span>
                            <span className="runtime-monitor__relation-verb">{relationVerb(relation.type)}</span>
                            <span>{actorLabel(relation.target, agents)}</span>
                          </div>
                          <p title={relation.detail}>{relation.label || relation.detail}</p>
                          <small>{formatTime(relation.timestamp)}</small>
                        </article>
                      ))
                    )}
                  </div>

                  <div className="runtime-monitor__actions">
                    {lane.actions.length === 0 ? (
                      <div className="runtime-monitor__placeholder runtime-monitor__placeholder--inline">No recent runtime actions.</div>
                    ) : (
                      lane.actions.map((action) => (
                        <article
                          key={action.id}
                          className={`runtime-monitor__action runtime-monitor__action--${action.state} runtime-monitor__action--${action.type}`}
                          title={action.detail}
                        >
                          <span className="runtime-monitor__action-verb">{actionVerb(action.type)}</span>
                          <strong>{action.title}</strong>
                          <p>{action.detail}</p>
                          <small>
                            {actionStateLabel(action.state)} · {formatTime(action.timestamp)}
                          </small>
                        </article>
                      ))
                    )}
                  </div>
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
