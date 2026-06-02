# Graph Report - catown  (2026-06-02)

## Corpus Check
- 326 files · ~542,930 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 9571 nodes · 37938 edges · 82 communities detected
- Extraction: 44% EXTRACTED · 56% INFERRED · 0% AMBIGUOUS · INFERRED: 21214 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 97|Community 97]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 100|Community 100]]
- [[_COMMUNITY_Community 101|Community 101]]
- [[_COMMUNITY_Community 102|Community 102]]
- [[_COMMUNITY_Community 103|Community 103]]
- [[_COMMUNITY_Community 104|Community 104]]

## God Nodes (most connected - your core abstractions)
1. `i()` - 481 edges
2. `TaskRun` - 437 edges
3. `Chatroom` - 380 edges
4. `set()` - 338 edges
5. `Project` - 333 edges
6. `Message` - 304 edges
7. `a()` - 278 edges
8. `Agent` - 264 edges
9. `createElement()` - 255 edges
10. `PipelineRun` - 253 edges

## Surprising Connections (you probably didn't know these)
- `Settings` --uses--> `Add a memory to the vector store.      Returns True if successful, False if Chro`  [INFERRED]
  backend\config.py → backend\services\vector_memory.py
- `_default_catown_home()` --calls--> `resolve()`  [INFERRED]
  backend\main.py → backend\static\docs\swagger-ui-bundle.js
- `RateLimiter` --uses--> `Frontend E2E Tests — Catown Pipeline Dashboard ================================`  [INFERRED]
  backend\main.py → tests\test_frontend.py
- `RateLimiter` --uses--> `Create a throwaway project for tests that need one.`  [INFERRED]
  backend\main.py → tests\test_frontend.py
- `RateLimiter` --uses--> `Create a pipeline linked to the sample project.`  [INFERRED]
  backend\main.py → tests\test_frontend.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.0
Nodes (1210): build_consult_agent_prompt_state(), run_consult_agent_response_action(), cancel_runtime_task_run(), cancel_runtime_task_run_subagent(), close_runtime_task_run_subagent(), list_runtime_task_run_subagents(), observe_runtime_task_run_subagent(), require_task_run() (+1202 more)

### Community 1 - "Community 1"
Cohesion: 0.0
Nodes (352): preciseSystemTime(), 14744(), 17285(), 19123(), 26571(), 31499(), 45981(), 50828() (+344 more)

### Community 2 - "Community 2"
Cohesion: 0.01
Nodes (458): AnalyzeDocumentTool, _coerce_int(), _extract_pdf_text(), Extract and optionally analyze PDF document content., _resolve_document_path(), _workspace_root(), AnalyzeImageTool, _image_to_data_uri() (+450 more)

### Community 3 - "Community 3"
Cohesion: 0.01
Nodes (497): ABC, build_consult_agent_messages(), build_consult_agent_prompt_profile(), _context_window_from_provider(), execute_chat_command(), get_config(), list_commands(), _resolve_llm_context_window() (+489 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (506): Shared execution helpers for high-level agent collaboration actions., Run one broadcast action and return the tool-facing status text., Return the tool-facing status text for one delegated task., Return a collaborator listing plus selection guidance for one chatroom., Run one direct-message action and return the tool-facing status text., Return agents that can be invited into one chatroom., Invite one agent into a chatroom and return the tool-facing status text., Resolve one synchronous consultation target and return either an error or the re (+498 more)

### Community 5 - "Community 5"
Cohesion: 0.01
Nodes (407): $(), a(), c(), ch(), fh(), i(), J(), l() (+399 more)

### Community 6 - "Community 6"
Cohesion: 0.02
Nodes (331): aa(), ac(), ad(), addChild(), addEntity(), Ae(), ao(), ar() (+323 more)

### Community 7 - "Community 7"
Cohesion: 0.01
Nodes (321): ActionRequestBase, ActionRequestSource, AskAgentActionRequest, AskAgentActionRequestPayload, dump_action_request(), parse_action_request(), PublishArtifactActionRequest, PublishArtifactActionRequestPayload (+313 more)

### Community 8 - "Community 8"
Cohesion: 0.01
Nodes (84): 21986(), 25911(), 31380(), 50689(), 63605(), 84977(), Annotation, api_optionAPI() (+76 more)

### Community 9 - "Community 9"
Cohesion: 0.02
Nodes (156): isEditableTarget(), matchCommands(), matchHistory(), FileWatcher, 启动文件监听（在 FastAPI 启动时调用）, actionStateLabel(), actorLabel(), buildRuntimeMapViewModel() (+148 more)

### Community 10 - "Community 10"
Cohesion: 0.02
Nodes (188): buildCard(), buildCardLlmPromptDetailContent(), buildCardLlmResponseDetailContent(), buildCardStepDetail(), buildCardStepDetailContent(), buildCardToolCallDetailContent(), buildCardToolResultDetailContent(), buildCardToolStepDetailContent() (+180 more)

### Community 11 - "Community 11"
Cohesion: 0.02
Nodes (138): Ag, bm(), Df(), Dx(), Ev(), Gp(), jv, Lb (+130 more)

### Community 12 - "Community 12"
Cohesion: 0.01
Nodes (93): Cp(), Dp(), on(), Op(), tw, xu, 16426(), 20317() (+85 more)

### Community 13 - "Community 13"
Cohesion: 0.04
Nodes (177): get_active_llm_runtime_context(), llm_runtime_context(), LlmRuntimeContext, reset_active_llm_runtime_context(), set_active_llm_runtime_context(), build_single_agent_stream_chat_profile(), build_single_agent_sync_chat_profile(), Build a managed sync runtime profile for one single-agent chat turn. (+169 more)

### Community 14 - "Community 14"
Cohesion: 0.02
Nodes (162): isAbortError(), approveApprovalQueueItem(), approvePipeline(), cancelTaskRun(), cancelTaskRunSubagent(), closeTaskRunSubagent(), compactTaskRun(), createChat() (+154 more)

### Community 15 - "Community 15"
Cohesion: 0.03
Nodes (110): OrchestrationAgentTurnDeps, Route-owned dependencies required to run an orchestrated agent turn., Route-owned dependencies required to stream an orchestrated agent turn., StreamOrchestrationAgentTurnDeps, acknowledge_orchestration_step_handoffs(), claim_orchestration_step_handoffs(), fail_orchestration_step_handoffs(), OrchestrationStepHandoffState (+102 more)

### Community 16 - "Community 16"
Cohesion: 0.03
Nodes (82): AgentConfigManager, load_agent_configs(), Agent 配置管理器 - 支持从文件加载配置, 从 JSON 文件加载配置                  文件格式示例：         {             "agents": {, AgentConfigV2, AgentProviderConfig, Config, create_agent_config_from_provider() (+74 more)

### Community 17 - "Community 17"
Cohesion: 0.02
Nodes (127): _available_collaborator_names(), _delegate_target_not_found(), _direct_target_not_found(), _open_db_session(), _query_target_not_found(), _query_target_not_in_room(), run_broadcast_agent_action(), run_check_task_status_action() (+119 more)

### Community 18 - "Community 18"
Cohesion: 0.02
Nodes (89): _broadcast_approval_queue_item_notification(), BaseHTTPMiddleware, run_migrations_online(), _cached_frontend_meta(), _default_catown_home(), _file_mtime_ns(), _forward_pipeline_events_to_ws(), _frontend_meta() (+81 more)

### Community 19 - "Community 19"
Cohesion: 0.03
Nodes (92): _format_json_block(), _snapshot_llm_messages(), Event, LLMCall, MonitorNetworkBlob, MonitorNetworkRecord, Persisted monitor network events for crash-safe troubleshooting., Captured metadata for one LLM turn. (+84 more)

### Community 20 - "Community 20"
Cohesion: 0.02
Nodes (87): inferAgentNameFromLlmStepLabel(), logClientSource(), Es(), Ls(), Ns(), Pf, rs(), ts() (+79 more)

### Community 21 - "Community 21"
Cohesion: 0.03
Nodes (97): get_chat_input_history(), save_chat_input_history(), update_multimodal_config(), update_orchestration_config(), update_permissions_config(), update_ui_config(), _copy_file_if_missing(), _default_catown_home() (+89 more)

### Community 22 - "Community 22"
Cohesion: 0.03
Nodes (23): oneLinePreview(), 通过 WebSocket 广播 reload 事件, al(), bl(), bw(), cl(), el(), hl() (+15 more)

### Community 23 - "Community 23"
Cohesion: 0.04
Nodes (90): App(), buildEvent(), buildInitialChatTitle(), buildProjectBrowserOrderMap(), buildStreamStep(), buildTaskRunStatusDetail(), buildToolCallStepDetail(), buildToolResultStepDetail() (+82 more)

### Community 24 - "Community 24"
Cohesion: 0.03
Nodes (67): _build_decoder(), _compact_text(), _compaction_item_from_output(), _connect_responses_websocket(), _content_as_text(), _error_fragments(), _estimate_bytes(), _estimate_prompt_tokens() (+59 more)

### Community 25 - "Community 25"
Cohesion: 0.02
Nodes (26): html(), Frontend Visual Rendering Tests — Catown ======================================, side-panel 是 flex-col 父容器，必须有 min-h-0。, chat-container 是 flex-col 父容器，必须有 min-h-0。, messages-area 必须有 overflow-y-auto。, logs-content 必须有 overflow-y-auto。, logs-content 必须有 flex-1 以占满父容器剩余空间。, side-panel 的父级（chat-area wrapper）必须有 overflow-hidden。 (+18 more)

### Community 26 - "Community 26"
Cohesion: 0.03
Nodes (51): loadAndStreamNetwork(), cm(), dm(), fm(), leave(), lm(), pm(), s() (+43 more)

### Community 27 - "Community 27"
Cohesion: 0.03
Nodes (41): o, 14248(), 16708(), 2523(), 30655(), 34932(), 40882(), 4640() (+33 more)

### Community 28 - "Community 28"
Cohesion: 0.04
Nodes (33): attachCardToTaskRun(), canInspect(), clampNumber(), compactDraftHistoryStore(), compactOverlayStore(), componentDidCatch(), createClientTurnId(), flushActivityBatch() (+25 more)

### Community 29 - "Community 29"
Cohesion: 0.05
Nodes (9): ai(), jf, Of, pi(), rb, Ti(), uf, ww() (+1 more)

### Community 30 - "Community 30"
Cohesion: 0.06
Nodes (51): _read_project_workspace_file(), _resolve_project_workspace_file(), _write_project_workspace_file(), archive_workspace_artifact_snapshot(), classify_workspace_artifact_path(), normalize_workspace_artifact_path(), _resolve_workspace_target(), ArtifactOutputPathPolicyDecision (+43 more)

### Community 31 - "Community 31"
Cohesion: 0.05
Nodes (35): for(), Eg, fn(), Xm(), 11842(), 13930(), 15287(), 1907() (+27 more)

### Community 32 - "Community 32"
Cohesion: 0.09
Nodes (42): Register a new choice box., 25264(), 2955(), 56698(), 80345(), 81214(), 85587(), 92861() (+34 more)

### Community 33 - "Community 33"
Cohesion: 0.08
Nodes (34): assemble_runtime_chat_messages(), build_runtime_environment_context(), build_tool_prompt(), canonical_tool_name(), canonical_tool_names(), _describe_run_shell_invocation(), Describe the local execution environment agents should rely on for shell work., Return the current public name for a tool, including legacy aliases. (+26 more)

### Community 34 - "Community 34"
Cohesion: 0.16
Nodes (33): _append_provider_compaction_event(), _blocked_tool_line(), build_compaction_context_messages(), build_local_compaction_sections(), _can_use_provider_native_compaction(), _checkpoint_path(), _checkpoint_summary(), _clean_checkpoint_id() (+25 more)

### Community 35 - "Community 35"
Cohesion: 0.11
Nodes (32): _available_actions_for_control_state(), build_subagent_lifecycle_from_events(), build_subagent_runtime_handles(), build_subagent_wait_result(), cancellable_subagent_handles(), cancellable_subagents_from_lifecycle(), _coerce_nonnegative_int(), _control_state_for_subagent() (+24 more)

### Community 36 - "Community 36"
Cohesion: 0.1
Nodes (20): buildAdaptiveLaneGroups(), buildEdgeCurve(), buildZoneLayoutPlan(), clamp(), clampPlacementToViewport(), clampWithinNode(), cubicBezierPoint(), cubicBezierTangent() (+12 more)

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (22): _average(), build_context_optimization_metrics(), build_observation_from_context_budget_events(), evaluate_context_budget_event_observations(), evaluate_context_optimization_metrics(), _evaluate_metric(), _event_payload(), _event_selector_diagnostics() (+14 more)

### Community 38 - "Community 38"
Cohesion: 0.13
Nodes (21): Protocol, derive_step_status(), derive_task_run_status(), _event_step_id(), _EventLike, _has_pending_approval(), _has_post_completion_activity(), is_terminal_status() (+13 more)

### Community 39 - "Community 39"
Cohesion: 0.13
Nodes (17): finalize_single_agent_session_failure(), finalize_single_agent_session_success(), Persist a completed non-stream single-agent turn., Terminalize a failed non-stream single-agent session., _maybe_await(), persist_single_agent_session_success(), Terminalize a failed single-agent session and return a unified result., Persist a completed single-agent session and return a unified terminal result. (+9 more)

### Community 40 - "Community 40"
Cohesion: 0.17
Nodes (3): objMap(), objReduce(), Store

### Community 41 - "Community 41"
Cohesion: 0.15
Nodes (1): SpecMap

### Community 42 - "Community 42"
Cohesion: 0.22
Nodes (15): build_handoff_metadata(), build_handoff_trigger_content(), handoff_targets(), _last_nonempty_paragraph_outside_fences(), maybe_schedule_assistant_handoff(), _normalize_newlines(), _tail_handoff_paragraph(), test_build_handoff_metadata_increments_depth_for_two_hop_roundtrip() (+7 more)

### Community 43 - "Community 43"
Cohesion: 0.18
Nodes (12): dump_evaluation_rubric(), EvaluationCriterion, EvaluationRubric, EvaluationRubricAppliesTo, parse_evaluation_rubric(), Validate and parse one schema-v1 evaluation rubric payload., Return the canonical JSON-compatible payload for one evaluation rubric., validate_criteria() (+4 more)

### Community 44 - "Community 44"
Cohesion: 0.32
Nodes (12): buildAgentThemeStyle(), fallbackAccentForAgent(), getAgentTheme(), hashString(), resolveAgentKey(), resolveAgentLabel(), defaultAgentName(), findAgentByType() (+4 more)

### Community 45 - "Community 45"
Cohesion: 0.17
Nodes (4): should_checkpoint() logic., Snapshot JSON round-trip., TestCheckpointSnapshotJSON, TestShouldCheckpoint

### Community 46 - "Community 46"
Cohesion: 0.42
Nodes (10): create_demo_project(), demo_workflow(), get_chatroom_messages(), get_system_status(), interactive_demo(), list_agents(), print_section(), Demo script for creating a project and sending chatroom messages. (+2 more)

### Community 47 - "Community 47"
Cohesion: 0.25
Nodes (5): _AttrItem, expire_stale_approvals transitions stale items to 'expired'., Verify the exact field values set by expiry., Mimics an ApprovalQueueItem with just the fields expiry logic touches., TestExpireStaleApprovals

### Community 48 - "Community 48"
Cohesion: 0.31
Nodes (2): om, Select

### Community 49 - "Community 49"
Cohesion: 0.36
Nodes (8): build_timestamped_artifact_path(), _normalize_directory(), _normalize_extension(), _prefixed_slug(), _slug(), slug_artifact_subject(), test_build_timestamped_artifact_path_uses_directory_timestamp_refs_and_slug(), test_slug_artifact_subject_normalizes_free_form_text()

### Community 50 - "Community 50"
Cohesion: 0.2
Nodes (6): GET /api/agents/{id} → 返回 Agent 详情, GET /api/agents/99999 → 404, 验证 Agent 角色与 PRD 定义一致, 5 个 Pipeline Agent + 1 个助理 Agent 注册, GET /api/agents → 包含所有 6 个 Pipeline 角色, TestAgentRegistrationE2E

### Community 51 - "Community 51"
Cohesion: 0.43
Nodes (3): context_tree_createNode(), context_tree_updateNode(), ContextTree

### Community 52 - "Community 52"
Cohesion: 0.33
Nodes (2): create_approval_queue_item sets expires_at correctly., TestApprovalTTLField

### Community 53 - "Community 53"
Cohesion: 0.5
Nodes (1): My

### Community 54 - "Community 54"
Cohesion: 0.4
Nodes (1): ValidatorImage

### Community 55 - "Community 55"
Cohesion: 0.67
Nodes (2): clamp(), pickAdaptiveColumns()

### Community 56 - "Community 56"
Cohesion: 0.67
Nodes (1): PredicateVisitor

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (1): Build OpenAI-compatible multimodal content.          Args:             text: The

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (1): Normalize message content for the OpenAI API.          Ensures each message's co

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (1): Assemble a system prompt from the stored SOUL data.

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (1): Scan a workspace and build the index.

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (1): Interpret common filter patterns used by expire_stale_approvals.

### Community 76 - "Community 76"
Cohesion: 1.0
Nodes (1): Filter the raw command output and return compressed version.

### Community 86 - "Community 86"
Cohesion: 1.0
Nodes (1): Convert bundled legacy JSON definitions into canonical skill packages.

### Community 87 - "Community 87"
Cohesion: 1.0
Nodes (1): Global infrastructure config plus managed runtime paths.

### Community 88 - "Community 88"
Cohesion: 1.0
Nodes (1): Reload settings lazily when relevant environment variables change.

### Community 89 - "Community 89"
Cohesion: 1.0
Nodes (1): LLM 客户端（OpenAI 兼容接口）      每个 Agent 可以有独立的 provider 配置（baseUrl, apiKey, model）。

### Community 90 - "Community 90"
Cohesion: 1.0
Nodes (1): Check if the current model supports multimodal (vision) inputs.

### Community 91 - "Community 91"
Cohesion: 1.0
Nodes (1): Build OpenAI-compatible multimodal content.          Args:             text: The

### Community 92 - "Community 92"
Cohesion: 1.0
Nodes (1): Normalize message content for the OpenAI API.          Ensures each message's co

### Community 93 - "Community 93"
Cohesion: 1.0
Nodes (1): 发送聊天消息（支持 multimodal content）

### Community 94 - "Community 94"
Cohesion: 1.0
Nodes (1): 支持工具调用的聊天（支持 multimodal content）

### Community 95 - "Community 95"
Cohesion: 1.0
Nodes (1): Call the provider-native Responses compact endpoint for a full context window.

### Community 96 - "Community 96"
Cohesion: 1.0
Nodes (1): 流式聊天（SSE generator，支持 multimodal content）          Yields:             dict: {"t

### Community 97 - "Community 97"
Cohesion: 1.0
Nodes (1): 解析字符串中的 ${ENV_VAR} 占位符

### Community 98 - "Community 98"
Cohesion: 1.0
Nodes (1): 从 agents.json 加载指定 Agent 的 provider 配置      优先级：Agent 自身 provider > global_llm p

### Community 99 - "Community 99"
Cohesion: 1.0
Nodes (1): 从 agents.json 的 global_llm 段加载全局 provider 配置

### Community 100 - "Community 100"
Cohesion: 1.0
Nodes (1): 获取指定 Agent 的 LLM 客户端（带缓存）      配置来源：agents.json → 该 Agent 的 provider 配置

### Community 101 - "Community 101"
Cohesion: 1.0
Nodes (1): 获取默认 LLM 客户端（用于无 Agent 上下文的场景，如记忆提取）      配置来源：agents.json 中第一个有 provider 的 Agen

### Community 102 - "Community 102"
Cohesion: 1.0
Nodes (1): 设置全局默认 LLM 客户端（测试用）      Args:         client: 新的客户端实例，None 表示重置

### Community 103 - "Community 103"
Cohesion: 1.0
Nodes (1): 获取默认 LLM 客户端（向后兼容入口）      支持 set_llm_client 设置的全局客户端。     优先返回已缓存的客户端，否则从 agents

### Community 104 - "Community 104"
Cohesion: 1.0
Nodes (1): 从 agents.json 获取第一个有 provider 配置的 Agent，兜底用 global_llm

## Knowledge Gaps
- **372 isolated node(s):** `Seed missing entries from a bundled JSON config into a runtime config.`, `Add a newly introduced tool to an existing runtime agent config.`, `Add newly introduced tools to an existing runtime agent config.`, `Add a newly introduced role rule to an existing runtime agent config.`, `Add a newly introduced role rule to an existing runtime agent config.` (+367 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 41`** (21 nodes): `SpecMap`, `._get()`, `._getContext()`, `.getCurrentMutations()`, `.getCurrentPlugin()`, `.getLib()`, `.getMutations()`, `.getMutationsForPlugin()`, `.getPatchesOfType()`, `.getPluginHistory()`, `.getPluginHistoryTip()`, `.getPluginMutationIndex()`, `.getPluginName()`, `.getPluginRunCount()`, `._hasRun()`, `.nextPlugin()`, `.promisedPatchThen()`, `.removePromisedPatch()`, `.setContext()`, `.updatePluginHistory()`, `.verbose()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (10 nodes): `om`, `.copyCustom()`, `.copyElement()`, `.copySelected()`, `.deselect()`, `.isSupported()`, `.selectElement()`, `Select`, `.constructor()`, `.UNSAFE_componentWillReceiveProps()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (6 nodes): `create_approval_queue_item sets expires_at correctly.`, `TestApprovalTTLField`, `.test_custom_ttl_one_hour()`, `.test_default_ttl_sets_expires_around_24h()`, `.test_none_ttl_means_no_expiry()`, `.test_zero_ttl_means_no_expiry()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (5 nodes): `My`, `.constructor()`, `.getMediaType()`, `.mediaModel()`, `.render()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (5 nodes): `ValidatorImage`, `.componentDidMount()`, `.constructor()`, `.render()`, `.UNSAFE_componentWillReceiveProps()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (4 nodes): `AdaptiveCardDeck()`, `clamp()`, `pickAdaptiveColumns()`, `AdaptiveCardDeck.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (3 nodes): `PredicateVisitor`, `.constructor()`, `.enter()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `Build OpenAI-compatible multimodal content.          Args:             text: The`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `Normalize message content for the OpenAI API.          Ensures each message's co`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `Assemble a system prompt from the stored SOUL data.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `Scan a workspace and build the index.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `Interpret common filter patterns used by expire_stale_approvals.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 76`** (1 nodes): `Filter the raw command output and return compressed version.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 86`** (1 nodes): `Convert bundled legacy JSON definitions into canonical skill packages.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 87`** (1 nodes): `Global infrastructure config plus managed runtime paths.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 88`** (1 nodes): `Reload settings lazily when relevant environment variables change.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 89`** (1 nodes): `LLM 客户端（OpenAI 兼容接口）      每个 Agent 可以有独立的 provider 配置（baseUrl, apiKey, model）。`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 90`** (1 nodes): `Check if the current model supports multimodal (vision) inputs.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 91`** (1 nodes): `Build OpenAI-compatible multimodal content.          Args:             text: The`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 92`** (1 nodes): `Normalize message content for the OpenAI API.          Ensures each message's co`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 93`** (1 nodes): `发送聊天消息（支持 multimodal content）`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 94`** (1 nodes): `支持工具调用的聊天（支持 multimodal content）`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 95`** (1 nodes): `Call the provider-native Responses compact endpoint for a full context window.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 96`** (1 nodes): `流式聊天（SSE generator，支持 multimodal content）          Yields:             dict: {"t`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 97`** (1 nodes): `解析字符串中的 ${ENV_VAR} 占位符`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 98`** (1 nodes): `从 agents.json 加载指定 Agent 的 provider 配置      优先级：Agent 自身 provider > global_llm p`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 99`** (1 nodes): `从 agents.json 的 global_llm 段加载全局 provider 配置`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 100`** (1 nodes): `获取指定 Agent 的 LLM 客户端（带缓存）      配置来源：agents.json → 该 Agent 的 provider 配置`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 101`** (1 nodes): `获取默认 LLM 客户端（用于无 Agent 上下文的场景，如记忆提取）      配置来源：agents.json 中第一个有 provider 的 Agen`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 102`** (1 nodes): `设置全局默认 LLM 客户端（测试用）      Args:         client: 新的客户端实例，None 表示重置`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 103`** (1 nodes): `获取默认 LLM 客户端（向后兼容入口）      支持 set_llm_client 设置的全局客户端。     优先返回已缓存的客户端，否则从 agents`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 104`** (1 nodes): `从 agents.json 获取第一个有 provider 配置的 Agent，兜底用 global_llm`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `set()` connect `Community 8` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 7`, `Community 9`, `Community 10`, `Community 11`, `Community 12`, `Community 13`, `Community 14`, `Community 15`, `Community 17`, `Community 18`, `Community 19`, `Community 20`, `Community 23`, `Community 26`, `Community 28`, `Community 29`, `Community 33`, `Community 41`, `Community 42`, `Community 43`?**
  _High betweenness centrality (0.070) - this node is a cross-community bridge._
- **Why does `list()` connect `Community 0` to `Community 1`, `Community 2`, `Community 3`, `Community 4`, `Community 33`, `Community 7`, `Community 9`, `Community 12`, `Community 13`, `Community 15`, `Community 16`, `Community 17`, `Community 18`, `Community 20`, `Community 21`, `Community 24`, `Community 30`?**
  _High betweenness centrality (0.056) - this node is a cross-community bridge._
- **Why does `i()` connect `Community 5` to `Community 0`, `Community 32`, `Community 1`, `Community 2`, `Community 6`, `Community 8`, `Community 9`, `Community 10`, `Community 11`, `Community 12`, `Community 20`, `Community 22`, `Community 26`, `Community 27`, `Community 31`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 772 inferred relationships involving `str` (e.g. with `_ensure_agent_tools()` and `_ensure_agent_skills()`) actually correct?**
  _`str` has 772 INFERRED edges - model-reasoned connections that need verification._
- **Are the 457 inferred relationships involving `i()` (e.g. with `isArray()` and `.apply()`) actually correct?**
  _`i()` has 457 INFERRED edges - model-reasoned connections that need verification._
- **Are the 434 inferred relationships involving `TaskRun` (e.g. with `CollaborationMessageType` and `TaskStatus`) actually correct?**
  _`TaskRun` has 434 INFERRED edges - model-reasoned connections that need verification._
- **Are the 378 inferred relationships involving `Chatroom` (e.g. with `ChatroomMessage` and `ChatroomManager`) actually correct?**
  _`Chatroom` has 378 INFERRED edges - model-reasoned connections that need verification._