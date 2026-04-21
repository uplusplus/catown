import type {
  AgentConfigPayload,
  AgentInfo,
  ChatSummary,
  ConfigResponse,
  GlobalConfigPayload,
  MonitorLogsResponse,
  MonitorRuntimeDetail,
  MessageItem,
  MonitorOverview,
  ProjectCreatePayload,
  ProjectFromChatPayload,
  ProjectSummary,
} from "../types";
import { UI_VERSION } from "../uiVersion";
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
  getConfig() {
    return request<ConfigResponse>("/api/config");
  },
  getMonitorOverview() {
    return request<MonitorOverview>("/api/monitor/overview?runtime_limit=80&summary_window=400&message_limit=40");
  },
  getMonitorLogs(limit = 250) {
    return request<MonitorLogsResponse>(`/api/monitor/logs?limit=${limit}`);
  },
  getMonitorRuntimeCardDetail(messageId: number) {
    return request<MonitorRuntimeDetail>(`/api/monitor/runtime-cards/${messageId}`);
  },
  saveGlobalConfig(payload: GlobalConfigPayload) {
    return request<{ message: string }>("/api/config/global", {
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
  testConfig(agentName = "assistant") {
    return request<{ status: string; agent: string; model: string; baseUrl: string }>(
      `/api/config/test?agent_name=${encodeURIComponent(agentName)}`,
      {
        method: "POST",
      },
    );
  },
};
