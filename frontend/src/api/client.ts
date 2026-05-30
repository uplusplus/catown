import type {
  AgentMemoryResponse,
  AgentConfigPayload,
  AgentInfo,
  ChatSummary,
  ChatTimelineProjection,
  ChatProcessEntry,
  ConfigResponse,
  ContextConfigPayload,
  GlobalConfigPayload,
  GitHubProjectImportPayload,
  MonitorLogsResponse,
  MonitorFilesResponse,
  MonitorNetworkResponse,
  MonitorProcessesResponse,
  MonitorApprovalAuditResponse,
  MonitorApprovalQueueResponse,
  MonitorAuditOverviewResponse,
  ApprovalQueueItem,
  AuditLlmDetailResponse,
  AuditLlmListResponse,
  MonitorRuntimeDetail,
  MonitorTaskRunStepsResponse,
  MonitorTaskRunsResponse,
  MessageItem,
  MonitorOverview,
  MonitorOverviewActivity,
  MonitorOverviewSummary,
  MonitorContextCompactionsResponse,
  MonitorUsageResponse,
  OrchestrationConfigPayload,
  PermissionsConfigPayload,
  ProjectCreatePayload,
  ProjectFromChatPayload,
  ProjectBrowserIndex,
  ProjectBrowserStreamBatch,
  ProjectBrowserWatchEvent,
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
  UiConfigPayload,
} from "../types";
import { UI_VERSION } from "../uiVersion";
import { isAbortError } from "../utils/abort";
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
    try {
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
    } catch (error) {
      if (signal?.aborted && isAbortError(error)) {
        return;
      }
      throw error;
    }
  },
  async watchProjectBrowser(
    projectId: number,
    onEvent: (event: ProjectBrowserWatchEvent) => void,
    signal?: AbortSignal,
    pollInterval = 2,
  ) {
    const response = await fetch(`/api/projects/${projectId}/browser/watch?poll_interval=${encodeURIComponent(String(pollInterval))}`, {
      cache: "no-store",
      signal,
      headers: {
        "X-Catown-Client": getClientSource(),
        "X-Catown-UI-Version": UI_VERSION,
      },
    });
    handleServerVersionHeaders(response.headers, `api:/api/projects/${projectId}/browser/watch`);

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    if (!response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.forEach((line) => {
          const trimmed = line.trim();
          if (!trimmed) return;
          onEvent(JSON.parse(trimmed) as ProjectBrowserWatchEvent);
        });
      }
      buffer += decoder.decode();
      if (buffer.trim()) {
        onEvent(JSON.parse(buffer.trim()) as ProjectBrowserWatchEvent);
      }
    } catch (error) {
      if (signal?.aborted && isAbortError(error)) {
        return;
      }
      throw error;
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
  getChatroomTimeline(chatroomId: number, limit?: number) {
    const params = new URLSearchParams();
    if (typeof limit === "number") {
      params.set("limit", String(limit));
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<ChatTimelineProjection>(`/api/chatrooms/${chatroomId}/timeline${suffix}`);
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
    return request<ChatProcessEntry>(`/api/chatrooms/${chatroomId}/processes`);
  },
  getTaskRunDetail(taskRunId: number, eventLimit?: number) {
    const params = new URLSearchParams();
    if (eventLimit !== undefined) {
      params.set("event_limit", String(eventLimit));
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<TaskRunDetail>(`/api/task-runs/${taskRunId}${suffix}`);
  },
  getTaskRunActivity(taskRunId: number) {
    return request<TaskActivityProjection>(`/api/task-runs/${taskRunId}/activity`);
  },
  waitTaskRunSubagent(taskRunId: number, stepId: string, params?: { sinceEventIndex?: number; timeoutMs?: number }) {
    const search = new URLSearchParams();
    if (typeof params?.sinceEventIndex === "number") search.set("since_event_index", String(params.sinceEventIndex));
    if (typeof params?.timeoutMs === "number") search.set("timeout_ms", String(params.timeoutMs));
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request<Record<string, unknown>>(`/api/task-runs/${taskRunId}/subagents/${encodeURIComponent(stepId)}/wait${suffix}`);
  },
  cancelTaskRunSubagent(taskRunId: number, stepId: string, payload?: { note?: string; cancelled_by?: string }) {
    return request<Record<string, unknown>>(`/api/task-runs/${taskRunId}/subagents/${encodeURIComponent(stepId)}/cancel`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },
  closeTaskRunSubagent(taskRunId: number, stepId: string, payload?: { note?: string; cancelled_by?: string }) {
    return request<Record<string, unknown>>(`/api/task-runs/${taskRunId}/subagents/${encodeURIComponent(stepId)}/close`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
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
  getApprovalQueueItem(itemId: number) {
    return request<ApprovalQueueItem>(`/api/approval-queue/${itemId}`);
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
  getMonitorApprovalAudit(decision = "all", limit = 200) {
    const params = new URLSearchParams({
      decision,
      limit: String(limit),
    });
    return request<MonitorApprovalAuditResponse>(`/api/monitor/approval-audit?${params.toString()}`);
  },
  approveApprovalQueueItem(itemId: number, payload?: { note?: string; resolved_by?: string; remember?: boolean; remember_scope?: string; remember_matcher?: string }) {
    return request<ApprovalQueueItem>(`/api/approval-queue/${itemId}/approve`, {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    });
  },
  rejectApprovalQueueItem(itemId: number, payload?: { note?: string; rollback_to?: string | null; resolved_by?: string; remember?: boolean; remember_scope?: string; remember_matcher?: string }) {
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
  sendMessage(chatroomId: number, content: string, clientTurnId?: string, attachments?: Array<{ file_id?: string; file_path: string; file_name: string; file_size: number; mime_type?: string }>) {
    return request<MessageItem>(`/api/chatrooms/${chatroomId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, client_turn_id: clientTurnId, attachments }),
    });
  },
  streamMessage(chatroomId: number, content: string, signal?: AbortSignal, clientTurnId?: string, attachments?: Array<{ file_id?: string; file_path: string; file_name: string; file_size: number; mime_type?: string }>) {
    return fetch(`/api/chatrooms/${chatroomId}/messages/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Catown-Client": getClientSource(),
        "X-Catown-UI-Version": UI_VERSION,
      },
      body: JSON.stringify({ content, client_turn_id: clientTurnId, attachments }),
      signal,
    }).then((response) => {
      handleServerVersionHeaders(response.headers, `/api/chatrooms/${chatroomId}/messages/stream`);
      return response;
    });
  },
  async uploadFile(chatroomId: number, file: File): Promise<{ file_id: string; file_path: string; file_name: string; file_size: number; mime_type?: string; upload_time: string }> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch(`/api/chatrooms/${chatroomId}/upload`, {
      method: "POST",
      headers: {
        "X-Catown-Client": getClientSource(),
        "X-Catown-UI-Version": UI_VERSION,
      },
      body: formData,
    });
    handleServerVersionHeaders(response.headers, `api:/api/chatrooms/${chatroomId}/upload`);
    if (!response.ok) {
      let detail = `Upload failed: ${response.status}`;
      try {
        const data = await response.json();
        detail = data.detail || data.error || detail;
      } catch {}
      throw new Error(detail);
    }
    return response.json();
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
    return request<MonitorOverviewSummary>("/api/monitor/overview?range=24h");
  },
  getMonitorOverviewActivity() {
    return request<MonitorOverviewActivity>("/api/monitor/overview/activity?runtime_limit=80&summary_window=96&message_limit=40&compaction_limit=16");
  },
  getMonitorContextCompactions(limit = 120) {
    return request<MonitorContextCompactionsResponse>(`/api/monitor/context-compactions?limit=${limit}`);
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
  getMonitorFiles(limit = 200, tool = "all", query = "") {
    const params = new URLSearchParams({ limit: String(limit), tool });
    if (query.trim()) {
      params.set("query", query.trim());
    }
    return request<MonitorFilesResponse>(`/api/monitor/files?${params.toString()}`);
  },
  getMonitorTaskRuns(range = "24h", limit = 120) {
    const params = new URLSearchParams({
      range,
      limit: String(limit),
    });
    return request<MonitorTaskRunsResponse>(`/api/monitor/task-runs?${params.toString()}`);
  },
  getAuditOverview(params?: {
    run_id?: number;
    agent?: string;
    event_type?: string;
    tool_name?: string;
    limit?: number;
  }) {
    const search = new URLSearchParams();
    if (typeof params?.run_id === "number" && Number.isFinite(params.run_id)) {
      search.set("run_id", String(params.run_id));
    }
    if (params?.agent?.trim()) search.set("agent", params.agent.trim());
    if (params?.event_type?.trim()) search.set("event_type", params.event_type.trim());
    if (params?.tool_name?.trim()) search.set("tool_name", params.tool_name.trim());
    search.set("limit", String(params?.limit ?? 120));
    return request<MonitorAuditOverviewResponse>(`/api/audit/overview?${search.toString()}`);
  },
  getAuditLlmCalls(params?: {
    run_id?: number;
    agent?: string;
    stage_id?: number;
    limit?: number;
    offset?: number;
  }) {
    const search = new URLSearchParams();
    if (typeof params?.run_id === "number" && Number.isFinite(params.run_id)) {
      search.set("run_id", String(params.run_id));
    }
    if (params?.agent?.trim()) search.set("agent", params.agent.trim());
    if (typeof params?.stage_id === "number" && Number.isFinite(params.stage_id)) {
      search.set("stage_id", String(params.stage_id));
    }
    search.set("limit", String(params?.limit ?? 60));
    search.set("offset", String(params?.offset ?? 0));
    return request<AuditLlmListResponse>(`/api/audit/llm?${search.toString()}`);
  },
  getAuditLlmCall(callId: number) {
    return request<AuditLlmDetailResponse>(`/api/audit/llm/${callId}`);
  },
  getMonitorProcesses(limit = 30, tailChars = 0) {
    const params = new URLSearchParams({
      limit: String(limit),
      tail_chars: String(tailChars),
    });
    return request<MonitorProcessesResponse>(`/api/monitor/processes?${params.toString()}`);
  },
  getMonitorTaskRunSteps(taskRunId: number, limit?: number) {
    const params = new URLSearchParams();
    if (limit !== undefined) {
      params.set("limit", String(limit));
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<MonitorTaskRunStepsResponse>(`/api/monitor/task-runs/${taskRunId}/steps${suffix}`);
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
  saveContextConfig(payload: ContextConfigPayload) {
    return request<{ message: string }>("/api/config/context", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  saveUiConfig(payload: UiConfigPayload) {
    return request<{ message: string }>("/api/config/ui", {
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

  // --- Choice Box ---
  getChoiceBoxes(chatroomId?: number) {
    const qs = chatroomId != null ? `?chatroom_id=${chatroomId}` : "";
    return request<{ choice_boxes: import("../components/ChoiceBox").ChoiceBoxData[]; count: number }>(
      `/api/choice-boxes${qs}`,
    );
  },
  respondChoiceBox(boxId: string, value: string) {
    return request<import("../components/ChoiceBox").ChoiceBoxData>(
      `/api/choice-boxes/${boxId}/respond`,
      {
        method: "POST",
        body: JSON.stringify({ value }),
      },
    );
  },
  cancelChoiceBox(boxId: string) {
    return request<import("../components/ChoiceBox").ChoiceBoxData>(
      `/api/choice-boxes/${boxId}/cancel`,
      { method: "POST" },
    );
  },

  // --- Command System ---
  executeCommand(command: string, chatroomId?: number, projectId?: number) {
    return request<{
      success: boolean;
      command: string;
      category: string;
      title: string;
      content: string;
      error: string | null;
    }>("/api/commands/execute", {
      method: "POST",
      body: JSON.stringify({ command, chatroom_id: chatroomId, project_id: projectId }),
    });
  },

  getChatCommands() {
    return request<{ commands: { command: string; aliases: string[]; category: string; description: string; args?: string }[] }>(
      "/api/commands",
    );
  },

  getChatInputHistory(chatroomId: number) {
    return request<{ chatroom_id: number; history: string[] }>(
      `/api/chat/history/${chatroomId}`,
    );
  },

  saveChatInputHistory(chatroomId: number, entry: string) {
    return request<{ ok: boolean }>(
      `/api/chat/history/${chatroomId}`,
      {
        method: "POST",
        body: JSON.stringify({ entry }),
      },
    );
  },
};
