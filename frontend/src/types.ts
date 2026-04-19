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
  pipeline_id?: number;
  run_id?: number;
  agent?: string;
  model?: string;
  turn?: number;
  tokens_in?: number;
  tokens_out?: number;
  duration_ms?: number;
  system_prompt?: string;
  prompt_messages?: string;
  response?: string;
  raw_response?: string;
  tool_calls?: ChatCardToolPreview[];
  display_name?: string;
  summary?: string;
  active_skills?: string[];
  expected_artifacts?: string[];
  tool?: string;
  arguments?: string;
  success?: boolean;
  result?: string;
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
