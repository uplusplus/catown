import type {
  AgentMemoryResponse,
  AgentConfigPayload,
  AgentInfo,
  ChatSummary,
  ChatProcessEntry,
  ConfigResponse,
  GlobalConfigPayload,
  GitHubProjectImportPayload,
  MonitorLogsResponse,
  MonitorNetworkResponse,
  MonitorApprovalQueueResponse,
  ApprovalQueueItem,
  MonitorRuntimeDetail,
  MonitorTaskRunStepsResponse,
  MonitorTaskRunsResponse,
  MessageItem,
  MonitorOverview,
  MonitorUsageResponse,
  OrchestrationConfigPayload,
  PermissionsConfigPayload,
  ProjectCreatePayload,
  ProjectFromChatPayload,
  ProjectBrowserIndex,
  ProjectBrowserStreamBatch,
  ProjectFileReadResponse,
  ProjectFileWritePayload,
  ProjectSyncResponse,
  SkillMarketplacesResponse,
  SkillMarketplaceUpdateResponse,
  ProjectSummary,
  TaskActivityProjection,
  TaskRunDetail,
  TaskRunResumeResponse,
  TaskRunSummary,
  ToolAuthorizationRule,
} from "../types";
import { UI_VERSION } from "../uiVersion";
import { DEFAULT_AGENT_TYPE } from "../utils/agents";
import { handleServerVersionHeaders } from "../versionGuard";

function getClientSource() {
  if (typeof window === "undefined") return "unknown";
  const path = window.location.pathname.toLowerCase();
  if (path === "/monitor" || path === "/monitor/" || path.endsWith("/monitor.html")) {
    return "monitor";
  }
  return "home";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-Catown-Client": getClientSource(),
      "X-Catown-UI-Version": UI_VERSION,
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  handleServerVersionHeaders(response.headers, `api:${path}`);

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const data = await response.json();
      detail = data.detail || data.error || detail;
    } catch {
      // Ignore JSON parse failures for plain-text error bodies.
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

export const api = {
  getChats() {
    return request<ChatSummary[]>("/api/chats");
  },
  createChat(title?: string) {
    return request<ChatSummary>("/api/chats", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
  },
  renameChat(chatId: number, title: string) {
    return request<ChatSummary>(`/api/chats/${chatId}`, {
      method: "PUT",
      body: JSON.stringify({ title }),
    });
  },
  deleteChat(chatId: number) {
    return request<{ message: string }>(`/api/chats/${chatId}`, {
      method: "DELETE",
    });
  },
  getProjects() {
    return request<ProjectSummary[]>("/api/projects");
  },
  reorderProjects(projectIds: number[]) {
    return request<ProjectSummary[]>("/api/projects/reorder", {
      method: "PUT",
      body: JSON.stringify({ project_ids: projectIds }),
    });
  },
  createProject(payload: ProjectCreatePayload) {
    return request<ProjectSummary>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  createProjectFromGithub(payload: GitHubProjectImportPayload) {
    return request<ProjectSummary>("/api/projects/from-github", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getOrCreateSelfBootstrapProject() {
    return request<ProjectSummary>("/api/projects/self-bootstrap", {
      method: "POST",
    });
  },
  renameProject(projectId: number, name: string) {
    return request<ProjectSummary>(`/api/projects/${projectId}`, {
      method: "PUT",
      body: JSON.stringify({ name }),
    });
  },
  createProjectFromChat(payload: ProjectFromChatPayload) {
    return request<ProjectSummary>("/api/projects/from-chat", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  approvePipeline(pipelineId: number) {
    return request<{ status: string }>(`/api/pipelines/${pipelineId}/approve`, {
      method: "POST",
    });
  },
  rejectPipeline(pipelineId: number, rollbackTo?: string) {
    return request<{ status: string }>(`/api/pipelines/${pipelineId}/reject`, {
      method: "POST",
      body: JSON.stringify(rollbackTo ? { rollback_to: rollbackTo } : {}),
    });
  },
  getProjectChat(projectId: number) {
    return request<ChatSummary>(`/api/projects/${projectId}/chat`);
  },
  getProjectBrowser(projectId: number) {
    return request<ProjectBrowserIndex>(`/api/projects/${projectId}/browser`);
  },
  readProjectFile(projectId: number, path: string) {
    const params = new URLSearchParams({ path });
    return request<ProjectFileReadResponse>(`/api/projects/${projectId}/files/read?${params.toString()}`);
  },
  writeProjectFile(projectId: number, payload: ProjectFileWritePayload) {
    return request<ProjectFileReadResponse>(`/api/projects/${projectId}/files/write`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  async streamProjectBrowser(
    projectId: number,
    onBatch: (batch: ProjectBrowserStreamBatch) => void,
    signal?: AbortSignal,
  ) {
    const response = await fetch(`/api/projects/${projectId}/browser/stream`, {
      cache: "no-store",
      signal,
      headers: {
        "X-Catown-Client": getClientSource(),
        "X-Catown-UI-Version": UI_VERSION,
      },
    });
    handleServerVersionHeaders(response.headers, `api:/api/projects/${projectId}/browser/stream`);

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    if (!response.body) {
      onBatch({ ...(await this.getProjectBrowser(projectId)), type: "done" });
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach((line) => {
        const trimmed = line.trim();
        if (!trimmed) return;
        onBatch(JSON.parse(trimmed) as ProjectBrowserStreamBatch);
      });
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      onBatch(JSON.parse(buffer.trim()) as ProjectBrowserStreamBatch);
    }
  },
  createProjectSubchat(projectId: number, title?: string) {
    return request<ChatSummary>(`/api/projects/${projectId}/subchats`, {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    });
  },
  openProjectWorkspace(projectId: number) {
    return request<{ message: string }>(`/api/projects/${projectId}/open-workspace`, {
      method: "POST",
    });
  },
  syncProject(projectId: number) {
    return request<ProjectSyncResponse>(`/api/projects/${projectId}/sync`, {
      method: "POST",
    });
  },
  deleteProject(projectId: number) {
    return request<{ message: string }>(`/api/projects/${projectId}`, {
      method: "DELETE",
    });
  },
  getMessages(chatroomId: number) {
    return request<MessageItem[]>(`/api/chatrooms/${chatroomId}/messages`);
  },
  getRuntimeCards(chatroomId: number) {
    return request<Record<string, unknown>[]>(`/api/chatrooms/${chatroomId}/runtime-cards`);
  },
  getTaskRuns(chatroomId: number, clientTurnId?: string) {
    const params = new URLSearchParams();
    if (clientTurnId?.trim()) {
      params.set("client_turn_id", clientTurnId.trim());
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<TaskRunSummary[]>(`/api/chatrooms/${chatroomId}/task-runs${suffix}`);
  },
  getChatProcesses(chatroomId: number) {
    return request<ChatProcessEntry[]>(`/api/chatrooms/${chatroomId}/processes`);
  },
  getTaskRunDetail(taskRunId: number) {
    return request<TaskRunDetail>(`/api/task-runs/${taskRunId}`);
  },
  getTaskRunActivity(taskRunId: number) {
    return request<TaskActivityProjection>(`/api/task-runs/${taskRunId}/activity`);
  },
  getApprovalQueue(params?: {
    status?: string;
    queue_kind?: string;
    chatroom_id?: number;
    project_id?: number;
    task_run_id?: number;
    limit?: number;
  }) {
    const search = new URLSearchParams();
    if (params?.status?.trim()) search.set("status", params.status.trim());
    if (params?.queue_kind?.trim()) search.set("queue_kind", params.queue_kind.trim());
    if (typeof params?.chatroom_id === "number") search.set("chatroom_id", String(params.chatroom_id));
    if (typeof params?.project_id === "number") search.set("project_id", String(params.project_id));
    if (typeof params?.task_run_id === "number") search.set("task_run_id", String(params.task_run_id));
    search.set("limit", String(params?.limit ?? 50));
    return request<ApprovalQueueItem[]>(`/api/approval-queue?${search.toString()}`);
  },
  resumeTaskRun(taskRunId: number) {
    return request<TaskRunResumeResponse>(`/api/task-runs/${taskRunId}/resume`, {
      method: "POST",
    });
  },
  getMonitorApprovalQueue(status = "all", limit = 120) {
    const params = new URLSearchParams({
      status,
      limit: String(limit),
    });
    return request<MonitorApprovalQueueResponse>(`/api/monitor/approval-queue?${params.toString()}`);
  },
  approveApprovalQueueItem(itemId: number, payload?: { note?: string; resolved_by?: string; remember_scope?: string }) {
    return request<ApprovalQueueItem>(`/api/approval-queue/${itemId}/approve`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },
  rejectApprovalQueueItem(itemId: number, payload?: { note?: string; rollback_to?: string | null; resolved_by?: string; remember_scope?: string }) {
    return request<ApprovalQueueItem>(`/api/approval-queue/${itemId}/reject`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },
  getToolAuthorizationRules(params?: {
    project_id?: number;
    chatroom_id?: number;
    tool_name?: string;
    include_revoked?: boolean;
  }) {
    const search = new URLSearchParams();
    if (typeof params?.project_id === "number") search.set("project_id", String(params.project_id));
    if (typeof params?.chatroom_id === "number") search.set("chatroom_id", String(params.chatroom_id));
    if (params?.tool_name?.trim()) search.set("tool_name", params.tool_name.trim());
    if (params?.include_revoked) search.set("include_revoked", "true");
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request<ToolAuthorizationRule[]>(`/api/tool-authorization-rules${suffix}`);
  },
  revokeToolAuthorizationRule(ruleId: number) {
    return request<{ message: string; rule: ToolAuthorizationRule }>(`/api/tool-authorization-rules/${ruleId}`, {
      method: "DELETE",
    });
  },
  sendMessage(chatroomId: number, content: string, clientTurnId?: string) {
    return request<MessageItem>(`/api/chatrooms/${chatroomId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, client_turn_id: clientTurnId }),
    });
  },
  streamMessage(chatroomId: number, content: string, signal?: AbortSignal, clientTurnId?: string) {
    return fetch(`/api/chatrooms/${chatroomId}/messages/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Catown-Client": getClientSource(),
        "X-Catown-UI-Version": UI_VERSION,
      },
      body: JSON.stringify({ content, client_turn_id: clientTurnId }),
      signal,
    }).then((response) => {
      handleServerVersionHeaders(response.headers, `/api/chatrooms/${chatroomId}/messages/stream`);
      return response;
    });
  },
  getAgents() {
    return request<AgentInfo[]>("/api/agents");
  },
  getAgentMemory(agentId: number) {
    return request<AgentMemoryResponse>(`/api/agents/${agentId}/memory`);
  },
  getConfig() {
    return request<ConfigResponse>("/api/config");
  },
  getSkillMarketplaces() {
    return request<SkillMarketplacesResponse>("/api/skills/marketplaces");
  },
  updateSkillMarketplace(marketplaceId: string, enabled: boolean, bootstrap = true) {
    return request<SkillMarketplaceUpdateResponse>(`/api/skills/marketplaces/${encodeURIComponent(marketplaceId)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled, bootstrap }),
    });
  },
  getMonitorOverview() {
    return request<MonitorOverview>("/api/monitor/overview?runtime_limit=80&summary_window=400&message_limit=40");
  },
  getMonitorLogs(limit = 250) {
    return request<MonitorLogsResponse>(`/api/monitor/logs?limit=${limit}`);
  },
  getMonitorNetwork(limit = 300, category = "all", query = "", includeInternal = false) {
    const params = new URLSearchParams({ limit: String(limit), category });
    params.set("include_internal", includeInternal ? "true" : "false");
    if (query.trim()) {
      params.set("query", query.trim());
    }
    return request<MonitorNetworkResponse>(`/api/monitor/network?${params.toString()}`);
  },
  getMonitorRuntimeCardDetail(messageId: number) {
    return request<MonitorRuntimeDetail>(`/api/monitor/runtime-cards/${messageId}`);
  },
  getMonitorTaskRuns(range = "24h", limit = 120) {
    const params = new URLSearchParams({
      range,
      limit: String(limit),
    });
    return request<MonitorTaskRunsResponse>(`/api/monitor/task-runs?${params.toString()}`);
  },
  getMonitorTaskRunSteps(taskRunId: number) {
    return request<MonitorTaskRunStepsResponse>(`/api/monitor/task-runs/${taskRunId}/steps`);
  },
  getMonitorUsage(range = "24h") {
    return request<MonitorUsageResponse>(`/api/monitor/usage?range=${encodeURIComponent(range)}`);
  },
  saveGlobalConfig(payload: GlobalConfigPayload) {
    return request<{ message: string }>("/api/config/global", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  saveOrchestrationConfig(payload: OrchestrationConfigPayload) {
    return request<{ message: string }>("/api/config/orchestration", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  savePermissionsConfig(payload: PermissionsConfigPayload) {
    return request<{ message: string }>("/api/config/permissions", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  saveAgentConfig(agentName: string, payload: AgentConfigPayload) {
    return request<{ message: string }>(`/api/config/agent/${agentName}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  reloadConfig() {
    return request<{ message: string }>("/api/config/reload", {
      method: "POST",
    });
  },
  testConfig(agentName = DEFAULT_AGENT_TYPE) {
    return request<{ status: string; agent: string; model: string; baseUrl: string }>(
      `/api/config/test?agent_name=${encodeURIComponent(agentName)}`,
      {
        method: "POST",
      },
    );
  },
};
