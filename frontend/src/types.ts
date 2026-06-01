export type AppTab = "chat" | "projects" | "config";
export type ConfigSection = "agents" | "skills" | "tools" | "memory" | "permissions" | "context" | "multimodal" | "interface";

export type AgentSoul = {
  identity?: string;
  values?: string[];
  style?: string;
  quirks?: string;
};

export type AgentInfo = {
  id: number;
  type: string;
  name: string;
  role: string;
  is_active: boolean;
  soul?: AgentSoul;
  tools?: string[];
  skills?: string[];
  system_prompt_preview?: string | null;
};

export type AgentMemoryItem = {
  id: number;
  type: string;
  content: string;
  importance: number;
  created_at: string;
};

export type AgentMemoryResponse = {
  agent_name: string;
  memory_count: number;
  memories: AgentMemoryItem[];
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
  source_type?: string | null;
  repo_url?: string | null;
  repo_full_name?: string | null;
  clone_ref?: string | null;
  created_from_chatroom_id?: number | null;
  agents: AgentInfo[];
};

export type ProjectBrowserFileItem = {
  path: string;
  name: string;
  kind: "file" | "directory";
  size?: number | null;
  mtime?: number | null;
};

export type ProjectBrowserArtifactItem = {
  path: string;
  name: string;
  type: string;
  agent_name?: string | null;
  size?: number | null;
  mtime?: number | null;
};

export type ProjectBrowserIndex = {
  workspace_path: string;
  files: ProjectBrowserFileItem[];
  artifacts: ProjectBrowserArtifactItem[];
  truncated: boolean;
};

export type ChatProcessEntry = {
  id: string;
  label: string;
  kind: "project" | "chat" | "task" | "command" | "subagent";
  detail: string;
  agent_name?: string | null;
  status?: "running" | "terminated" | string | null;
  parent_id?: string | null;
  timestamp?: string | null;
  pid?: number | null;
  output?: string | null;
  metadata?: Record<string, unknown> | null;
  children: ChatProcessEntry[];
};

export type ProjectBrowserStreamBatch = ProjectBrowserIndex & {
  type: "start" | "batch" | "done";
};

export type ProjectBrowserWatchOperation = {
  type: "add" | "delete" | "update" | "move" | "rename" | string;
  path?: string | null;
  from_path?: string | null;
  to_path?: string | null;
  file?: ProjectBrowserFileItem | null;
  artifact?: ProjectBrowserArtifactItem | null;
};

export type ProjectBrowserWatchEvent = {
  type: "ready" | "refresh_needed";
  workspace_path: string;
  changed: boolean;
  reason?: string | null;
  changed_paths: string[];
  added_paths: string[];
  updated_paths: string[];
  files: ProjectBrowserFileItem[];
  artifacts: ProjectBrowserArtifactItem[];
  removed_paths: string[];
  operations?: ProjectBrowserWatchOperation[];
  truncated: boolean;
  snapshot_id?: string | null;
};

export type ProjectFileReadResponse = {
  path: string;
  name: string;
  size: number;
  mtime: number;
  content: string;
  encoding: string;
  truncated: boolean;
  binary: boolean;
  preview_limit: number;
};

export type ProjectFileWritePayload = {
  path: string;
  content: string;
  expected_mtime?: number | null;
};

export type MessageItem = {
  id: number;
  agent_id?: number | null;
  content: string;
  message_type: string;
  created_at: string;
  agent_name?: string | null;
  client_turn_id?: string;
  metadata?: Record<string, unknown> | null;
  isStreaming?: boolean;
  statusDetail?: string;
  streamSteps?: MessageStreamStep[];
  optimisticKind?: "user" | "assistant_placeholder";
  localOnly?: boolean;
  runtime_summary?: {
    task_run_id?: number | null;
    status?: string | null;
    run_kind?: string | null;
    active_subagent_handle?: Record<string, unknown> | null;
    active_consult_handle?: Record<string, unknown> | null;
    continuation_state_summary?: string | null;
    subagent_handles_summary?: string | null;
  } | null;
};

export type TaskRunSummary = {
  id: number;
  chatroom_id: number;
  project_id?: number | null;
  origin_message_id?: number | null;
  client_turn_id?: string | null;
  run_kind: string;
  status: string;
  title: string;
  user_request?: string | null;
  initiator?: string | null;
  target_agent_name?: string | null;
  recovery_owner?: string | null;
  recovery_claimed_at?: string | null;
  recovery_lease_expires_at?: string | null;
  summary?: string | null;
  continuation_cursor?: {
    next_action?: string | null;
    resume_strategy?: string | null;
    source_event_type?: string | null;
    source_event_at?: string | null;
    turn?: number | null;
    tool_name?: string | null;
    tool_names?: string[] | null;
    blocked_kind?: string | null;
    blocked_tool_count?: number | null;
    queue_item_id?: number | null;
    pipeline_run_id?: number | null;
    pipeline_stage_id?: number | null;
    completed_step_count?: number | null;
    ready_step_count?: number | null;
    running_step_count?: number | null;
    waiting_step_count?: number | null;
  } | null;
  continuation_cursor_summary?: string | null;
  continuation_state?: {
    consumed?: boolean;
    next_action?: string | null;
    resume_strategy?: string | null;
    consumed_layers?: string[] | null;
    protocol_tail_message_count?: number | null;
    prior_round_summary_count?: number | null;
  } | null;
  continuation_state_summary?: string | null;
  latest_event_type?: string | null;
  latest_continuation_event_type?: string | null;
  latest_continuation_event_summary?: string | null;
  latest_continuation_event_at?: string | null;
  latest_scheduler_runtime?: Record<string, unknown> | null;
  scheduler_runtime_summary?: string | null;
  latest_subagent_step?: Record<string, unknown> | null;
  subagent_lifecycle_summary?: string | null;
  subagent_handles_summary?: string | null;
  checkpoint_snapshot?: TaskRunCheckpointSnapshot;
  approval_queue_count?: number;
  pending_approval_count?: number;
  event_count: number;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
};

export type TaskRunCheckpointSnapshot = {
  event_count?: number;
  latest_event_type?: string | null;
  latest_event_at?: string | null;
  latest_agent_turn?: {
    agent_name?: string | null;
    message_id?: number | null;
    response_preview?: string | null;
    created_at?: string | null;
  };
  latest_compaction?: {
    event_id?: number | null;
    compacted?: boolean;
    dropped_count?: number | null;
    truncated_count?: number | null;
    candidate_count?: number | null;
    selected_count?: number | null;
    candidate_tokens?: number | null;
    selected_tokens?: number | null;
    max_fragments?: number | null;
    max_tokens?: number | null;
    max_tokens_by_role?: Record<string, number> | null;
    max_tokens_by_scope?: Record<string, number> | null;
    scope_usage?: Record<string, { candidate_count?: number | null; selected_count?: number | null; candidate_tokens?: number | null; selected_tokens?: number | null }> | null;
    budget_summary?: string | null;
    scope_usage_summary?: string | null;
    detail_summary?: string | null;
    summary_text?: string | null;
    created_at?: string | null;
  };
  latest_scheduler_runtime?: Record<string, unknown> | null;
  latest_subagent_step?: Record<string, unknown> | null;
  subagent_lifecycle?: Record<string, unknown> | null;
  subagent_lifecycle_summary?: string | null;
  subagent_handles?: Record<string, unknown> | null;
  subagent_handles_summary?: string | null;
  continuation_cursor?: {
    next_action?: string | null;
    resume_strategy?: string | null;
    source_event_type?: string | null;
    source_event_at?: string | null;
    turn?: number | null;
    tool_name?: string | null;
    tool_names?: string[] | null;
    blocked_kind?: string | null;
    blocked_tool_count?: number | null;
    queue_item_id?: number | null;
    pipeline_run_id?: number | null;
    pipeline_stage_id?: number | null;
    completed_step_count?: number | null;
    ready_step_count?: number | null;
    running_step_count?: number | null;
    waiting_step_count?: number | null;
  };
  continuation_cursor_summary?: string | null;
  continuation_state?: {
    consumed?: boolean;
    next_action?: string | null;
    resume_strategy?: string | null;
    consumed_layers?: string[] | null;
    protocol_tail_message_count?: number | null;
    prior_round_summary_count?: number | null;
  };
  continuation_state_summary?: string | null;
  turn_local_state?: {
    turn?: number | null;
    tool_names?: string[] | null;
    blocked_tool_count?: number | null;
    assistant_content?: string | null;
    protocol_messages?: Record<string, unknown>[] | null;
    protocol_tail_messages?: Record<string, unknown>[] | null;
    prior_round_summaries?: Record<string, unknown>[] | null;
    tool_results?: Record<string, unknown>[] | null;
    blocked_tool?: Record<string, unknown> | null;
  };
  pending_approval_count?: number;
  approval_queue_count?: number;
  status?: string | null;
  summary?: string | null;
};

export type TaskRunEvent = {
  id: number;
  event_index: number;
  event_type: string;
  agent_name?: string | null;
  message_id?: number | null;
  summary?: string | null;
  compacted?: boolean;
  dropped_count?: number;
  truncated_count?: number;
  candidate_count?: number;
  selected_count?: number;
  candidate_tokens?: number;
  selected_tokens?: number;
  max_fragments?: number | null;
  max_tokens?: number | null;
  context_window?: number | null;
  input_window?: number | null;
  reserved_completion_tokens?: number | null;
  static_tokens?: number | null;
  usage_band?: {
    band?: string;
    ratio?: number;
    prompt_tokens?: number;
    input_window?: number;
  };
  max_tokens_by_role?: Record<string, number>;
  max_tokens_by_scope?: Record<string, number>;
  scope_usage?: Record<string, { candidate_count?: number; selected_count?: number; candidate_tokens?: number; selected_tokens?: number }>;
  budget_summary?: string | null;
  scope_usage_summary?: string | null;
  detail_summary?: string | null;
  summary_text?: string | null;
  from_agent?: string | null;
  to_agent?: string | null;
  from_step_id?: string | null;
  to_step_id?: string | null;
  attached_to_step_id?: string | null;
  dispatch_kind?: string | null;
  content_preview?: string | null;
  runtime_snapshot?: Record<string, unknown> | null;
  schedule_mode?: string | null;
  schedule_step_count?: number | null;
  schedule_blocking_step_count?: number | null;
  schedule_sidecar_step_count?: number | null;
  schedule_sidecar_agent_types?: string[] | null;
  schedule_steps?: unknown[] | null;
  payload?: Record<string, unknown>;
  continuation_state?: {
    consumed?: boolean;
    next_action?: string | null;
    resume_strategy?: string | null;
    consumed_layers?: string[] | null;
    protocol_tail_message_count?: number | null;
    prior_round_summary_count?: number | null;
  } | null;
  continuation_state_summary?: string | null;
  created_at?: string | null;
};

export type TaskRunDetail = TaskRunSummary & {
  events: TaskRunEvent[];
  approval_queue_items?: ApprovalQueueItem[];
};

export type TaskActivityStep = {
  id: string;
  event_index: number;
  event_type: string;
  label: string;
  state: "live" | "done" | "error";
  agent?: string | null;
  tool?: string | null;
  summary?: string | null;
  detail?: string | null;
  detail_content?: string | null;
  created_at?: string | null;
  refs?: Record<string, unknown>;
};

export type TaskActivityProjection = {
  task_run_id: number;
  status: string;
  title: string;
  run_kind: string;
  version: number;
  latest_event_index: number;
  updated_at?: string | null;
  current_step_id?: string | null;
  summary?: string | null;
  continuation_state_summary?: string | null;
  continuation_cursor_summary?: string | null;
  scheduler_runtime_summary?: string | null;
  latest_agent_turn_preview?: string | null;
  background?: Record<string, unknown>;
  steps: TaskActivityStep[];
  timeline?: ChatTimelineProjection | null;
  truncated?: boolean;
  total_step_count?: number;
};

export type ChatTimelineStep = {
  id: string;
  scope: "task_run" | "chatroom";
  task_run_id?: number | null;
  chatroom_id: number;
  sequence: number;
  scope_sequence?: number | null;
  occurred_at?: string | null;
  recorded_at?: string | null;
  event_type: string;
  step_id?: string | null;
  parent_step_id?: string | null;
  actor?: string | null;
  kind: string;
  phase: string;
  state: "live" | "done" | "error";
  facts?: Record<string, unknown>;
  summary?: string | null;
  detail_content?: string | null;
};

export type ChatTimelineProjection = {
  scope: "task_run" | "chatroom";
  task_run_id?: number;
  chatroom_id: number;
  version: number;
  current_step_id?: string | null;
  steps: ChatTimelineStep[];
  truncated?: boolean;
  total_step_count?: number;
};

export type ApprovalQueueItem = {
  id: number;
  task_run_id?: number | null;
  chatroom_id: number;
  project_id?: number | null;
  pipeline_run_id?: number | null;
  pipeline_stage_id?: number | null;
  queue_kind: string;
  status: string;
  source: string;
  title: string;
  summary?: string | null;
  agent_name?: string | null;
  target_kind: string;
  target_name?: string | null;
  request_key?: string | null;
  request_payload?: Record<string, unknown>;
  resolution_note?: string | null;
  resolution_payload?: Record<string, unknown>;
  resolved_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
};

export type PermissionRememberScope = "project" | "chatroom" | "global";

export type PermissionRememberMatcher = "command_fingerprint" | "shell_bin" | "tool_target" | "all_tools";

export type ToolAuthorizationRule = {
  id: number;
  project_id?: number | null;
  chatroom_id?: number | null;
  agent_name?: string | null;
  tool_name: string;
  scope: string;
  matcher_type: string;
  matcher_value?: string | null;
  decision_kind: string;
  preference_key?: string | null;
  preference_kind?: string | null;
  preference_value?: string | null;
  constraints?: Record<string, unknown>;
  command_preview?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type MonitorApprovalQueueEntry = ApprovalQueueItem & {
  chat_title?: string | null;
  project_name?: string | null;
  task_run_title?: string | null;
  task_run_status?: string | null;
  run_kind?: string | null;
  latest_event_type?: string | null;
  request_preview?: string | null;
  resolution_preview?: string | null;
  resume_supported?: boolean;
  action_taken?: string | null;
  replay_status?: string | null;
  replay_success?: boolean | null;
  followup_attempted?: boolean | null;
  followup_status?: string | null;
  followup_reason?: string | null;
  followup_error?: string | null;
  followup_message_id?: number | null;
};

export type MonitorApprovalQueueResponse = {
  captured_at: string;
  status: string;
  counts: {
    all: number;
    pending: number;
    approved: number;
    rejected: number;
  };
  entries: MonitorApprovalQueueEntry[];
};

export type MonitorApprovalAuditEntry = {
  id: number;
  event_kind: string;
  decision: string;
  source: string;
  resolved_by?: string | null;
  queue_item_id?: number | null;
  task_run_id?: number | null;
  chatroom_id?: number | null;
  project_id?: number | null;
  pipeline_run_id?: number | null;
  pipeline_stage_id?: number | null;
  preference_id?: number | null;
  agent_name?: string | null;
  target_kind?: string | null;
  target_name?: string | null;
  tool_name?: string | null;
  scope?: string | null;
  matcher_type?: string | null;
  matcher_value?: string | null;
  approval_fingerprint?: string | null;
  approval_fingerprint_kind?: string | null;
  approval_fingerprint_input?: Record<string, unknown>;
  command_preview?: string | null;
  reason?: string | null;
  preview?: string | null;
  request_payload?: Record<string, unknown>;
  resolution_payload?: Record<string, unknown>;
  created_at?: string | null;
};

export type MonitorApprovalAuditResponse = {
  captured_at: string;
  decision: string;
  event_kind: string;
  source: string;
  counts: {
    all: number;
    approve: number;
    reject: number;
    allow: number;
    deny: number;
    remembered: number;
    automatic: number;
  };
  entries: MonitorApprovalAuditEntry[];
};

export type MonitorAuditTimelineEntry = {
  kind: "llm" | "tool" | "event";
  id: number;
  run_id?: number | null;
  stage_id?: number | null;
  project_id?: number | null;
  stage_run_id?: number | null;
  asset_id?: number | null;
  agent_name?: string | null;
  model?: string | null;
  turn_index?: number | null;
  token_input?: number | null;
  token_output?: number | null;
  duration_ms?: number | null;
  error?: string | null;
  tool_name?: string | null;
  llm_call_id?: number | null;
  success?: boolean | null;
  result_length?: number | null;
  event_type?: string | null;
  stage_name?: string | null;
  summary?: string | null;
  created_at?: string | null;
};

export type MonitorAuditModelSummary = {
  name: string;
  calls: number;
  tokens: number;
};

export type MonitorAuditAgentSummary = {
  name: string;
  llm_calls: number;
  tool_calls: number;
  events: number;
  tokens: number;
};

export type MonitorAuditNamedCount = {
  name: string;
  count: number;
};

export type MonitorAuditOverviewResponse = {
  captured_at: string;
  filters: {
    run_id?: number | null;
    agent?: string | null;
    event_type?: string | null;
    tool_name?: string | null;
    limit: number;
  };
  counts: {
    llm_calls: number;
    tool_calls: number;
    events: number;
    timeline: number;
    errored_llm_calls: number;
  };
  tokens: {
    input: number;
    output: number;
    total: number;
  };
  durations: {
    llm_total_ms: number;
  };
  top_models: MonitorAuditModelSummary[];
  top_agents: MonitorAuditAgentSummary[];
  top_tools: MonitorAuditNamedCount[];
  top_events: MonitorAuditNamedCount[];
  timeline: MonitorAuditTimelineEntry[];
};

export type AuditLlmListItem = {
  id: number;
  run_id?: number | null;
  stage_id?: number | null;
  agent_name: string;
  turn_index: number;
  model?: string | null;
  token_input: number;
  token_output: number;
  duration_ms: number;
  error?: string | null;
  content_preview?: string | null;
  created_at?: string | null;
};

export type AuditLlmListResponse = {
  total: number;
  items: AuditLlmListItem[];
};

export type AuditLlmToolCall = {
  id: number;
  tool_name: string;
  success: boolean;
  duration_ms: number;
  result_preview?: string | null;
};

export type AuditLlmDetailResponse = {
  id: number;
  run_id?: number | null;
  stage_id?: number | null;
  agent_name: string;
  turn_index: number;
  model?: string | null;
  system_prompt?: string | null;
  messages?: unknown;
  response_content?: string | null;
  response_tool_calls?: unknown;
  token_input: number;
  token_output: number;
  duration_ms: number;
  error?: string | null;
  created_at?: string | null;
  tool_calls: AuditLlmToolCall[];
};

export type MonitorCompactionItem = {
  id: number;
  task_run_id?: number | null;
  chatroom_id?: number | null;
  project_id?: number | null;
  chat_title?: string | null;
  project_name?: string | null;
  run_kind?: string | null;
  task_run_title?: string | null;
  task_run_status?: string | null;
  agent_name?: string | null;
  event_type: string;
  summary?: string | null;
  created_at?: string | null;
  compacted?: boolean;
  dropped_count?: number;
  truncated_count?: number;
  candidate_count?: number;
  selected_count?: number;
  candidate_tokens?: number;
  selected_tokens?: number;
  max_fragments?: number | null;
  max_tokens?: number | null;
  max_tokens_by_role?: Record<string, number>;
  max_tokens_by_scope?: Record<string, number>;
  scope_usage?: Record<string, { candidate_count?: number; selected_count?: number; candidate_tokens?: number; selected_tokens?: number }>;
  budget_summary?: string | null;
  scope_usage_summary?: string | null;
  detail_summary?: string | null;
  reasons?: Array<Record<string, unknown>>;
  reason_summary?: string | null;
  prompt_total?: {
    bytes?: number;
    tokens?: number;
    message_count?: number;
  };
  prompt_components?: Record<string, {
    bytes?: number;
    tokens?: number;
    chars?: number;
    message_count?: number;
    fragment_count?: number;
  }>;
  prompt_fragments?: Array<{
    role?: string;
    scope?: string;
    visibility?: string;
    source?: string;
    priority?: number;
    selected?: boolean;
    chars?: number;
    bytes?: number;
    tokens?: number;
  }>;
  developer?: Record<string, unknown>;
  user?: Record<string, unknown>;
  payload?: Record<string, unknown>;
};

export type MonitorContextCompactionsResponse = {
  captured_at: string;
  limit: number;
  counts: {
    total: number;
    returned: number;
    dropped: number;
    truncated: number;
  };
  entries: MonitorCompactionItem[];
};

export type TaskRunResumeResponse = {
  message: string;
  resumed: boolean;
  status: string;
  task_run_id: number;
  detail: TaskRunDetail;
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
  runId?: number;
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
  | "consult_call"
  | "agent_error"
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
  pid?: number;
  system_prompt?: string;
  prompt_messages?: string;
  response?: string;
  raw_response?: string;
  finish_reason?: string;
  tool_calls?: ChatCardToolPreview[];
  timings?: ChatCardLlmTimings;
  display_name?: string;
  summary?: string;
  active_skills?: string[];
  expected_artifacts?: string[];
  tool?: string;
  arguments?: string;
  success?: boolean;
  status?: string;
  blocked?: boolean;
  blocked_kind?: string | null;
  blocked_reason?: string | null;
  result?: string;
  target_agent?: string;
  question_preview?: string;
  response_preview?: string;
  consult_step_id?: string;
  available_actions?: string[];
  error?: string;
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
  name?: string;
  provider?: {
    baseUrl?: string;
    apiKey?: string;
    models?: Array<{ id: string; name?: string; contextWindow?: number }>;
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

export type ConfigOrchestrationDefinition = {
  sidecar_agent_types?: string[];
};

export type ConfigPermissionsDefinition = {
  allow_read_only_tools_without_approval?: boolean;
  auto_approve_all?: boolean;
  remember_default_scope?: PermissionRememberScope;
  remember_default_matcher?: PermissionRememberMatcher;
};

export type ContextSelectorProfileConfig = {
  allowed_visibilities?: string[] | null;
  allowed_scopes?: string[] | null;
  max_fragments?: number | null;
  max_tokens_cap?: number | null;
  max_tokens_cap_ratio?: number | null;
  max_tokens_by_role?: Record<string, number>;
  max_tokens_by_role_ratio?: Record<string, number>;
  max_tokens_by_scope?: Record<string, number>;
  max_tokens_by_scope_ratio?: Record<string, number>;
  truncate_to_budget?: boolean;
  min_tokens_for_truncation?: number;
};

export type ConfigContextDefinition = {
  selector_profiles?: Record<string, ContextSelectorProfileConfig>;
  default_selector_profiles?: Record<string, ContextSelectorProfileConfig>;
};

export type ConfigUiDefinition = {
  chat_cards?: {
    expand_current_step_by_default?: boolean;
  };
};

export type ConfigMultimodalDefinition = {
  max_upload_size_bytes?: number;
  min_upload_size_bytes?: number;
  max_allowed_upload_size_bytes?: number;
};

export type ConfigResponse = {
  global_llm?: {
    provider?: {
      baseUrl?: string;
      apiKey?: string;
      models?: Array<{ id: string; name?: string; contextWindow?: number }>;
    };
    default_model?: string;
  };
  orchestration?: ConfigOrchestrationDefinition;
  permissions?: ConfigPermissionsDefinition;
  context?: ConfigContextDefinition;
  ui?: ConfigUiDefinition;
  multimodal?: ConfigMultimodalDefinition;
  tools?: {
    tool_names?: string[];
    tool_policies?: Array<{
      name: string;
      description?: string;
      risk_level?: string;
      approval?: {
        kind?: string;
        required?: boolean;
      };
      sandbox?: {
        mode?: string;
        workspace_scope?: string;
        network_access?: string;
      };
      side_effect_scope?: string;
      requires_credentials?: boolean;
    }>;
    tool_policy_summary?: Record<string, unknown>;
  };
  skills_catalog?: Record<
    string,
    {
      name?: string;
      description?: string;
      required_tools?: string[];
      category?: string;
      levels?: {
        hint?: string;
        guide?: string;
        full?: string;
      };
    }
  >;
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

export type SkillMarketplace = {
  id: string;
  name: string;
  adapter: string;
  enabled: boolean;
  command?: string | null;
  command_available?: boolean | null;
  install_url?: string | null;
  bootstrap_available?: boolean;
  description?: string;
};

export type SkillMarketplacesResponse = {
  marketplaces: SkillMarketplace[];
  config_file: string;
};

export type SkillMarketplaceUpdateResponse = {
  marketplace: SkillMarketplace;
  bootstrap?: {
    ok?: boolean;
    skipped?: boolean;
    command?: string;
    stdout?: string;
    stderr?: string;
  } | null;
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

export type GitHubProjectImportPayload = {
  repo_url: string;
  name?: string;
  description?: string;
  ref?: string | null;
  agent_names: string[];
};

export type ProjectSyncResponse = {
  project: ProjectSummary;
  updated: boolean;
  branch?: string | null;
  head_commit?: string | null;
  head_short?: string | null;
  previous_head_commit?: string | null;
  detached?: boolean;
  summary: string;
};

export type GlobalConfigPayload = {
  provider: {
    baseUrl: string;
    apiKey: string;
    models: Array<{ id: string; name: string; contextWindow?: number }>;
  };
  default_model: string;
};

export type AgentConfigPayload = {
  provider?: {
    baseUrl: string;
    apiKey: string;
    models: Array<{ id: string; name: string; contextWindow?: number }>;
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

export type OrchestrationConfigPayload = {
  sidecar_agent_types: string[];
};

export type PermissionsConfigPayload = {
  allow_read_only_tools_without_approval: boolean;
  auto_approve_all: boolean;
  remember_default_scope: PermissionRememberScope;
  remember_default_matcher: PermissionRememberMatcher;
};

export type ContextConfigPayload = {
  selector_profiles: Record<string, ContextSelectorProfileConfig>;
};

export type UiConfigPayload = ConfigUiDefinition;

export type MultimodalConfigPayload = {
  max_upload_size_bytes: number;
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
  operation_label?: string | null;
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
  tool_call_id?: string | null;
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
  brain_events?: Array<{
    id: string;
    phase?: string | null;
    category?: string | null;
    tone?: string | null;
    from_entity?: string | null;
    to_entity?: string | null;
    operation_label?: string | null;
    label?: string | null;
    detail?: string | null;
  }> | null;
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
  type?: string;
  title?: string | null;
  operation_label?: string | null;
  preview?: string | null;
  agent?: string | null;
  from_entity?: string | null;
  to_entity?: string | null;
  model?: string | null;
  tool_name?: string | null;
  tool_call_id?: string | null;
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
  brain_events?: Array<{
    id: string;
    phase: string;
    category: string;
    tone: string;
    from_entity?: string | null;
    to_entity?: string | null;
    operation_label?: string | null;
    label: string;
    detail?: string | null;
  }>;
  detail_sections?: Array<{
    id: string;
    phase: string;
    label: string;
    content: string;
    tone: "neutral" | "success" | "warning" | "error" | "accent";
    format: "text" | "json";
    variant: "meta" | "result" | "raw";
  }>;
  card: Record<string, unknown>;
};

export type MonitorFileEvent = {
  id: string;
  source: string;
  runtime_message_id?: number | null;
  created_at?: string | null;
  agent?: string | null;
  tool_name: string;
  action: "read" | "write" | "list" | "search" | "delete" | "access" | string;
  file_path: string;
  project_id?: number | null;
  project_name?: string | null;
  chatroom_id: number;
  chat_title: string;
  success?: boolean | null;
  status?: string | null;
  blocked?: boolean | null;
  duration_ms?: number | null;
  turn?: number | null;
  client_turn_id?: string | null;
  arguments?: Record<string, unknown>;
  arguments_preview?: string | null;
  result_preview?: string | null;
  result_size?: number | null;
};

export type MonitorFilesResponse = {
  captured_at: string;
  limit: number;
  tool: string;
  query: string;
  counts: {
    total: number;
    reads: number;
    writes: number;
    lists: number;
    searches: number;
    deletes: number;
    errors: number;
    unique_paths: number;
  };
  by_tool: { tool_name: string; count: number }[];
  by_agent: { agent: string; count: number }[];
  diagnostics?: {
    projection_health: {
      runtime_cards: number;
      projections: number;
      missing: number;
      status: string;
    };
    scope: {
      mode: string;
      tool_names: string[];
      shell_activity_included: boolean;
    };
  };
  entries: MonitorFileEvent[];
};

export type MonitorTaskRunSummary = TaskRunSummary & {
  chat_title: string;
  project_name?: string | null;
  latest_event_type?: string | null;
};

export type MonitorTaskRunsResponse = {
  captured_at: string;
  range: "1h" | "6h" | "24h" | "7d" | "30d";
  entries: MonitorTaskRunSummary[];
};

export type MonitorProcessEntry = {
  id: string;
  token: string;
  tool_name?: string | null;
  command: string;
  cwd?: string | null;
  timeout_seconds?: number | null;
  chatroom_id?: number | null;
  project_id?: number | null;
  task_run_id?: number | null;
  client_turn_id?: string | null;
  tool_call_id?: string | null;
  turn?: number | null;
  agent_name?: string | null;
  status: string;
  is_active: boolean;
  is_terminal: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  pid?: number | null;
  worker_pid?: number | null;
  pgid?: number | null;
  exit_code?: number | null;
  tail_output?: string | null;
  last_result_preview?: string | null;
  redirected_log_path?: string | null;
  state_path?: string | null;
  log_path?: string | null;
  exit_path?: string | null;
};

export type MonitorProcessesResponse = {
  captured_at: string;
  entries: MonitorProcessEntry[];
  counts: {
    total: number;
    running: number;
    finished: number;
    failed: number;
  };
};

export type MonitorTaskRunStep = {
  id: string;
  sequence: number;
  source: string;
  step_kind: "llm" | "tool" | "event";
  title: string;
  preview?: string | null;
  created_at?: string | null;
  agent_name?: string | null;
  turn?: number | null;
  model?: string | null;
  tool_name?: string | null;
  success?: boolean | null;
  status?: string | null;
  blocked?: boolean | null;
  blocked_kind?: string | null;
  duration_ms?: number | null;
  tokens_in?: number;
  tokens_out?: number;
  event_type?: string | null;
  message_id?: number | null;
  pipeline_run_id?: number | null;
  pipeline_stage_id?: number | null;
  arguments?: string | null;
  result?: string | null;
  prompt_preview?: string | null;
  response_preview?: string | null;
  planned_tools?: string[];
  payload?: Record<string, unknown> | null;
};

export type MonitorTaskRunStepsResponse = {
  task_run_id: number;
  chatroom_id: number;
  project_id?: number | null;
  client_turn_id?: string | null;
  captured_at: string;
  counts: {
    total: number;
    llm: number;
    tool: number;
    event: number;
    tool_errors: number;
    tool_blocked: number;
    tokens_in: number;
    tokens_out: number;
  };
  runtime_message_ids: number[];
  steps: MonitorTaskRunStep[];
};

export type MonitorUsageBucket = {
  label: string;
  start: string;
  end: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  llm_calls: number;
};

export type MonitorUsageTotals = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  llm_calls: number;
};

export type MonitorUsageResponse = {
  captured_at: string;
  range: string;
  pricing: {
    input_per_1k: number;
    output_per_1k: number;
  };
  totals: {
    day: MonitorUsageTotals;
    week: MonitorUsageTotals;
    month: MonitorUsageTotals;
  };
  buckets: MonitorUsageBucket[];
  scanned_runtime_cards: number;
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

export type MonitorNetworkEvent = {
  id: number;
  created_at: string;
  task_run_id?: number | null;
  chatroom_id?: number | null;
  category: "frontend_backend" | "backend_llm" | "backend_other" | "frontend_other" | string;
  source: string;
  protocol: string;
  from_entity: string;
  to_entity: string;
  request_direction?: string;
  response_direction?: string;
  flow_id?: string;
  flow_kind?: string;
  flow_seq?: number | null;
  aggregated?: boolean;
  method: string;
  url: string;
  host: string;
  path: string;
  status_code?: number | null;
  success?: boolean | null;
  request_bytes: number;
  response_bytes: number;
  total_bytes: number;
  duration_ms: number;
  content_type?: string;
  preview?: string;
  error?: string;
  client_source?: string;
  raw_request?: string;
  raw_response?: string;
  raw_request_blob_id?: number | null;
  raw_response_blob_id?: number | null;
  request_headers?: Record<string, string>;
  response_headers?: Record<string, string>;
  metadata?: Record<string, unknown>;
};

export type MonitorNetworkResponse = {
  captured_at: string;
  latest_id: number;
  entries: MonitorNetworkEvent[];
};

export type MonitorOverviewSummary = {
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
      runtime_card_projections?: number;
      runtime_card_projection_missing?: number;
      approval_queue_total?: number;
      approval_queue_pending?: number;
      context_compactions?: number;
    };
    features: Record<string, boolean>;
    collaboration: {
      active_collaborators: number;
      chatrooms: number;
      pending_tasks: number;
      status: string;
    };
    projections?: {
      runtime_cards: number;
      projections: number;
      missing: number;
      status: string;
    };
    last_message_at?: string | null;
  };
  usage_window: {
    range?: "1h" | "6h" | "24h" | "7d" | "30d";
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
    top_skills?: Array<{
      skill_name: string;
      inject_count: number;
    }>;
    files?: {
      reads: number;
      writes: number;
      lists: number;
      searches: number;
      deletes: number;
      errors: number;
      unique_paths: number;
      top_paths: Array<{
        path: string;
        count: number;
      }>;
    };
  };
  tasks: {
    range: "1h" | "6h" | "24h" | "7d" | "30d";
    counts: {
      total: number;
      running: number;
      completed: number;
      failed: number;
      awaiting_approval: number;
    };
    by_agent: Array<{
      agent_name: string;
      task_count: number;
    }>;
    tokens: {
      input: number;
      output: number;
      total: number;
      avg_per_task: number;
    };
    context: {
      configured_window?: number | null;
      avg_usage_ratio?: number | null;
      max_usage_ratio?: number | null;
      sampled_runs: number;
    };
    artifacts: {
      recorded: number;
      task_runs_with_artifacts: number;
      top_outputs: Array<{
        path: string;
        count: number;
      }>;
    };
  };
  llm: {
    range: "1h" | "6h" | "24h" | "7d" | "30d";
    status: string;
    requests: number;
    success: number;
    errors: number;
    success_rate?: number | null;
    avg_latency_ms?: number | null;
    tokens: {
      input: number;
      output: number;
      total: number;
    };
    tool_followups: {
      calls: number;
      errors: number;
    };
    top_models: Array<{
      name: string;
      calls: number;
      tokens: number;
    }>;
    last_request_at?: string | null;
  };
  approvals: {
    queue: {
      total: number;
      pending: number;
      approved: number;
      rejected: number;
    };
    audit: {
      approved: number;
      rejected: number;
      remembered: number;
      automatic: number;
    };
  };
  compactions: {
    total: number;
    task_runs: number;
    avg_interval_minutes?: number | null;
    avg_per_task_run?: number | null;
    avg_prompt_tokens?: number | null;
    avg_usage_ratio?: number | null;
    reasons: Array<{
      reason: string;
      count: number;
    }>;
    last_compaction_at?: string | null;
  };
};

export type MonitorOverviewActivity = {
  captured_at: string;
  recent_runtime: MonitorRuntimeItem[];
  recent_messages: MonitorMessageItem[];
  recent_compactions: MonitorCompactionItem[];
};

export type MonitorOverview = MonitorOverviewSummary & MonitorOverviewActivity;
