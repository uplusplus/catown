export type AppTab = "chat" | "projects" | "config";

export type AgentSoul = {
  identity?: string;
  values?: string[];
  style?: string;
  quirks?: string;
};

export type AgentInfo = {
  id: number;
  name: string;
  role: string;
  is_active: boolean;
  soul?: AgentSoul;
  tools?: string[];
  skills?: string[];
  system_prompt_preview?: string | null;
};

export type ChatSummary = {
  id: number;
  title: string;
  session_type: "standalone" | "project-bound";
  is_visible_in_chat_list: boolean;
  project_id?: number | null;
  agent_count?: number;
  updated_at?: string;
};

export type ProjectSummary = {
  id: number;
  name: string;
  description?: string | null;
  status: string;
  display_order: number;
  chatroom_id: number;
  default_chatroom_id: number;
  workspace_path?: string | null;
  created_from_chatroom_id?: number | null;
  agents: AgentInfo[];
};

export type MessageItem = {
  id: number;
  agent_id?: number | null;
  content: string;
  message_type: string;
  created_at: string;
  agent_name?: string | null;
  client_turn_id?: string;
  isStreaming?: boolean;
  streamSteps?: MessageStreamStep[];
  optimisticKind?: "user" | "assistant_placeholder";
  localOnly?: boolean;
};

export type MessageStreamStep = {
  id: string;
  label: string;
  detail?: string;
  detailContent?: string;
  state: "live" | "done" | "error";
  kind?: "llm_outbound" | "llm_inbound" | "tool_call" | "tool_result_to_llm";
  agent?: string;
  tool?: string;
  toolCallIndex?: number;
  toolCallId?: string | null;
};

export type ChatEventTone = "neutral" | "success" | "warning" | "error" | "info";

export type ChatEventItem = {
  id: string;
  message: string;
  tone: ChatEventTone;
  created_at: string;
};

export type ChatCardKind =
  | "llm_call"
  | "tool_call"
  | "stage_start"
  | "stage_end"
  | "gate_blocked"
  | "gate_approved"
  | "gate_rejected"
  | "skill_inject"
  | "agent_message"
  | "boss_instruction";

export type ChatCardToolPreview = {
  name?: string;
  args_preview?: string;
  index?: number;
  id?: string | null;
};

export type ChatCardLlmTimings = {
  request_sent_ms?: number;
  first_chunk_ms?: number;
  first_content_ms?: number;
  first_tool_call_ms?: number;
  tool_call_ready_ms?: number;
  completed_ms?: number;
};

export type ChatCardSkillDetail = {
  name?: string;
  hint?: string;
  guide?: string;
  guide_tokens?: number;
};

export type ChatCardItem = {
  id: string;
  kind: ChatCardKind;
  created_at: string;
  source?: string;
  client_turn_id?: string;
  pipeline_id?: number;
  run_id?: number;
  agent?: string;
  model?: string;
  turn?: number;
  tokens_in?: number;
  tokens_out?: number;
  tokens_total?: number;
  context_window?: number;
  context_usage_ratio?: number;
  duration_ms?: number;
  system_prompt?: string;
  prompt_messages?: string;
  response?: string;
  raw_response?: string;
  tool_calls?: ChatCardToolPreview[];
  timings?: ChatCardLlmTimings;
  display_name?: string;
  summary?: string;
  active_skills?: string[];
  expected_artifacts?: string[];
  tool?: string;
  arguments?: string;
  success?: boolean;
  result?: string;
  tool_call_index?: number;
  tool_call_id?: string | null;
  stage?: string;
  skills?: ChatCardSkillDetail[];
  agent_all_skills?: string[];
  from_agent?: string;
  to_agent?: string;
  content?: string;
  content_preview?: string;
  from_stage?: string;
  to_stage?: string;
};

export type ConfigAgentDefinition = {
  provider?: {
    baseUrl?: string;
    apiKey?: string;
    models?: Array<{ id: string; name?: string }>;
  };
  default_model?: string;
  role?: {
    title?: string;
    responsibilities?: string[];
    rules?: string[];
  };
  soul?: AgentSoul;
  tools?: string[];
  skills?: string[];
};

export type ConfigResponse = {
  global_llm?: {
    provider?: {
      baseUrl?: string;
      apiKey?: string;
      models?: Array<{ id: string; name?: string }>;
    };
    default_model?: string;
  };
  agents?: Record<string, ConfigAgentDefinition>;
  agent_llm_configs?: Record<
    string,
    {
      baseUrl?: string;
      model?: string;
      source?: string;
      hasApiKey?: boolean;
      models?: string[];
    }
  >;
  server?: {
    host?: string;
    port?: number;
  };
  features?: Record<string, unknown>;
};

export type ProjectCreatePayload = {
  name: string;
  description?: string;
  agent_names: string[];
  workspace_path?: string | null;
};

export type ProjectFromChatPayload = ProjectCreatePayload & {
  source_chatroom_id: number;
};

export type GlobalConfigPayload = {
  provider: {
    baseUrl: string;
    apiKey: string;
    models: Array<{ id: string; name: string }>;
  };
  default_model: string;
};

export type AgentConfigPayload = {
  provider?: {
    baseUrl: string;
    apiKey: string;
    models: Array<{ id: string; name: string }>;
  };
  default_model?: string;
  role?: {
    title?: string;
    responsibilities?: string[];
    rules?: string[];
  };
  soul?: AgentSoul;
  tools?: string[];
  skills?: string[];
};

export type MonitorToolSummary = {
  tool_name: string;
  call_count: number;
  failure_count: number;
  avg_duration_ms: number;
};

export type MonitorAgentUsage = {
  agent_name: string;
  llm_calls: number;
  tool_calls: number;
  errors: number;
  token_input: number;
  token_output: number;
  token_total: number;
  estimated_cost_usd: number;
};

export type MonitorRuntimeItem = {
  id: number;
  type: string;
  title: string;
  preview: string;
  created_at: string;
  chatroom_id: number;
  chat_title: string;
  project_id?: number | null;
  project_name?: string | null;
  agent?: string | null;
  from_entity?: string | null;
  to_entity?: string | null;
  model?: string | null;
  tool_name?: string | null;
  success?: boolean | null;
  tokens_in?: number;
  tokens_out?: number;
  duration_ms?: number;
  turn?: number | null;
  client_turn_id?: string | null;
  prompt_preview?: string | null;
  response_preview?: string | null;
  arguments_preview?: string | null;
  stage?: string | null;
};

export type MonitorMessageItem = {
  id: number;
  chatroom_id: number;
  chat_title: string;
  project_id?: number | null;
  project_name?: string | null;
  agent_name?: string | null;
  content: string;
  content_preview: string;
  message_type: string;
  created_at: string;
  client_turn_id?: string | null;
};

export type MonitorRuntimeDetail = {
  id: number;
  created_at: string;
  chatroom_id: number;
  chat_title: string;
  project_id?: number | null;
  project_name?: string | null;
  card: Record<string, unknown>;
};

export type MonitorLogEntry = {
  id: number;
  created_at: string;
  level: string;
  logger: string;
  message: string;
  line: string;
  pathname?: string | null;
  lineno?: number | null;
  thread_name?: string | null;
  process?: number | null;
};

export type MonitorLogsResponse = {
  captured_at: string;
  latest_id: number;
  entries: MonitorLogEntry[];
};

export type MonitorOverview = {
  captured_at: string;
  system: {
    status: string;
    version: string;
    stats: {
      agents: number;
      active_agents: number;
      projects: number;
      chatrooms: number;
      visible_chats: number;
      messages: number;
      runtime_cards: number;
    };
    features: Record<string, boolean>;
    collaboration: {
      active_collaborators: number;
      chatrooms: number;
      pending_tasks: number;
      status: string;
    };
    last_message_at?: string | null;
  };
  usage_window: {
    runtime_cards_considered: number;
    llm_calls: number;
    tool_calls: number;
    tool_errors: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number;
    pricing: {
      input_per_1k: number;
      output_per_1k: number;
    };
    by_agent: MonitorAgentUsage[];
    top_tools: MonitorToolSummary[];
  };
  recent_runtime: MonitorRuntimeItem[];
  recent_messages: MonitorMessageItem[];
};
