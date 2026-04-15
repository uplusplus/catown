export type Project = {
  id: number;
  slug?: string | null;
  name: string;
  description?: string | null;
  one_line_vision?: string | null;
  status: string;
  current_stage: string;
  execution_mode: string;
  health_status?: string | null;
  current_focus?: string | null;
  blocking_reason?: string | null;
  latest_summary?: string | null;
  target_platforms?: string[];
  target_users?: string[];
  references?: string[];
  last_decision_id?: number | null;
  legacy_mode?: string | null;
  last_activity_at?: string | null;
  created_at?: string | null;
};

export type StageLifecycle = {
  phase: string;
  is_active: boolean;
  is_terminal: boolean;
  requires_attention: boolean;
};

export type StageRun = {
  id: number;
  project_id: number;
  stage_type: string;
  run_index: number;
  status: string;
  lifecycle: StageLifecycle;
  triggered_by?: string | null;
  trigger_reason?: string | null;
  summary?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
};

export type AssetRelationship = {
  asset_id: number;
  asset_type: string;
  relation_type: string;
};

export type Asset = {
  id: number;
  project_id: number;
  asset_type: string;
  title?: string | null;
  summary?: string | null;
  content_json?: Record<string, unknown>;
  content_markdown?: string | null;
  version: number;
  status: string;
  is_current: boolean;
  approval_decision_id?: number | null;
  produced_by_stage_run_id?: number | null;
  relationships?: {
    upstream: AssetRelationship[];
    downstream: AssetRelationship[];
  };
  stage_links?: Array<{ stage_run_id: number; direction: string }>;
  decision_links?: Array<{ decision_id: number; relation_role: string }>;
  updated_at?: string | null;
  created_at?: string | null;
};

export type Decision = {
  id: number;
  project_id: number;
  stage_run_id?: number | null;
  decision_type: string;
  title: string;
  context_summary?: string | null;
  recommended_option?: string | null;
  alternative_options: string[];
  impact_summary?: string | null;
  requested_action?: string | null;
  status: string;
  resolved_option?: string | null;
  resolution_note?: string | null;
  created_at?: string | null;
  resolved_at?: string | null;
};

export type EventItem = {
  id: number;
  project_id?: number | null;
  stage_run_id?: number | null;
  asset_id?: number | null;
  event_type: string;
  agent_name?: string | null;
  stage_name?: string | null;
  summary?: string | null;
  payload: Record<string, unknown>;
  created_at?: string | null;
};

export type ReleaseReadiness = {
  has_prd: boolean;
  has_release_pack: boolean;
  pending_release_decision: boolean;
  status: string;
  next_gate?: string | null;
};

export type ProjectOverview = {
  project: Project;
  current_stage_run: StageRun | null;
  key_assets: Asset[];
  assets_by_type?: Record<string, Asset>;
  pending_decisions: Decision[];
  decision_history?: Decision[];
  stage_summary: {
    total: number;
    completed: number;
    active: number;
    latest_completed_stage?: string | null;
  };
  stage_asset_links?: Array<Record<string, unknown>>;
  decision_asset_links?: Array<Record<string, unknown>>;
  open_tasks_summary?: { count: number };
  recent_activity: StageRun[];
  release_readiness: ReleaseReadiness;
  recommended_next_action: string;
};

export type StageRunDetail = {
  stage_run: StageRun;
  project: Project;
  input_assets: Asset[];
  output_assets: Asset[];
  decisions: Decision[];
  events: EventItem[];
  summary: {
    input_count: number;
    output_count: number;
    decision_count: number;
    event_count: number;
  };
};

export type DecisionResolveResponse = {
  decision?: Decision;
  project?: Project;
  stage_run?: StageRun;
  stage_run_detail?: StageRunDetail;
  [key: string]: unknown;
};
