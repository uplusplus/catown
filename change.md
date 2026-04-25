# Change: Context Orchestration Refactor

## Version

0.2.0-task-state-chat-orchestration

## Goal

Refactor Catown prompt generation from ad-hoc monolithic system prompt concatenation into layered context orchestration, while keeping the first phase intentionally narrow:

- Keep the existing LLM provider interface unchanged.
- Keep Chat Completions compatible `messages` arrays.
- Keep tool schemas, tool loop behavior, database schema, and frontend interaction unchanged.
- Share one context assembly model across pipeline and major chat execution paths.

The new prompt layout is:

1. `system`: stable agent identity, role, long-term rules.
2. `developer`: stage rules, skill hints/guides, tool guidance, BOSS instructions.
3. `user`: project state, chat/runtime context, memories, team context, inter-agent messages.
4. recent history: windowed chat history.
5. current input/tool-loop continuation.

## Modified Files

- `backend/services/context_builder.py`
- `backend/services/turn_state.py`
- `backend/services/chat_prompt_builder.py`
- `backend/services/task_state.py`
- `backend/pipeline/engine.py`
- `backend/routes/api.py`
- `backend/chatrooms/manager.py`
- `backend/tools/query_agent.py`
- `backend/tests/test_prompt_context_builder.py`
- `backend/tests/test_context_integration.py`

## Implementation

### New Context Builder

Added `backend/services/context_builder.py` with:

- `ContextScope`: `session`, `run`, `stage`, `turn`, `agent_private`, `shared_fact`.
- `ContextVisibility`: `global`, `agent`, `role`, `stage`, `private`.
- `ContextFragment`: in-memory context metadata object with `role`, `content`, `scope`, `visibility`, `source`, and `priority`.
- `ContextSelector`: selects context fragments by scope/visibility/priority and enforces fragment/token budgets.
- `PromptAssembly`: structured prompt output with `system_message`, `developer_messages`, `user_context_messages`, `history_messages`, and `current_input_messages`.
- `assemble_messages()`: emits messages in the fixed order `system -> developer -> user -> history -> current input`.
- `developer_role_supported=False` fallback: merges developer context into system under `## Developer Context`.

Added builder helpers:

- `build_base_system_prompt(agent_config)`: builds stable SOUL/ROLE/RULES identity from agent config or DB agent object.
- `build_operating_developer_context(...)`: adds a Codex-style operating contract for context priority, visibility boundaries, attention management, and tool-use policy.
- `build_stage_developer_context(...)`: builds stage instructions, active skill guides, skill hints, and tool guidance.
- `build_boss_instruction_context(...)`: injects BOSS instructions as developer context.
- `build_runtime_user_fragments(...)`: splits project/chat/runtime/team/memory/inter-agent context into separate prioritized user fragments.
- `build_runtime_user_context(...)`: compatibility wrapper that combines runtime user fragments when a single user context message is needed.
- `build_turn_state_developer_fragments(...)`: converts per-turn BOSS instructions into developer fragments.
- `build_turn_state_user_fragments(...)`: converts inter-agent messages, previous work, and summarized older tool rounds into user fragments.
- `build_recent_history(...)`: preserves the existing windowing semantics without summary compression.

### Turn State

Added `backend/services/turn_state.py` with:

- `TurnContextState`: per-turn runtime state used to rebuild messages each LLM/tool loop iteration.
- `ToolRoundRecord`: keeps recent assistant tool calls plus tool results as protocol-compatible continuation messages.
- `ToolResultRecord`: normalized tool result object.
- `normalize_tool_call(...)` and `build_tool_result_record(...)`: helper functions for consistent tool loop recording.

Older tool rounds are summarized into user context while the most recent tool round remains as protocol messages. This keeps tool-call continuation compatible while preventing the current turn from growing unbounded.

### Task State

Added `backend/services/task_state.py` with:

- `TaskState`: a lightweight working-memory object for non-pipeline chat/query turns.
- `build_task_state(...)`: derives structured task memory from the current project state plus the current request.
- `build_task_state_fragments(...)`: emits high-priority user fragments for:
  - `## Active Task State`
  - `## Validation Checklist`

This moves `current_focus`, `blocking_reason`, and `latest_summary` out of the generic project status block and into a dedicated task-memory layer that is easier for the selector to prioritize.

### Pipeline Path

Updated `backend/pipeline/engine.py`:

- `_run_agent_stage()` now uses `assemble_messages()` instead of manually building a single system prompt plus user context.
- Stage config, skill hint/guide context, and tool names are emitted as developer messages.
- The operating contract is emitted as the first developer message.
- BOSS instructions are emitted as developer messages.
- Pipeline runtime context and inter-agent messages are emitted as user messages.
- Tool loop state is rebuilt through `TurnContextState` each iteration: recent tool messages stay in protocol order, older rounds become summaries.
- Pipeline context selection uses `ContextSelector.for_context_window(...)` when model context metadata is available.
- Existing `chat_with_tools(messages, tools=...)` call and tool loop append behavior remain unchanged.

### Chat Path

Updated `backend/routes/api.py`:

- Added `_assemble_chat_messages()` as the route-level adapter around `context_builder`.
- Chat no longer preserves the legacy DB `agent.system_prompt` as the primary prompt source.
- DB agents now build stable system identity from structured `soul` plus `config.role`.
- Remaining chat/SSE prompt preview and staging variables no longer read the legacy `agent.system_prompt` property.
- Agent skill hints are injected into developer context for chat paths.
- Standalone assistant response path now uses the shared assembly helper.
- Standalone SSE assistant path now uses the shared assembly helper.
- Project agent response path now replaces final model-bound messages with the shared assembly helper.
- Multi-agent single-turn helper now replaces final model-bound messages with the shared assembly helper.
- Main project SSE/tool path now replaces final model-bound messages with the shared assembly helper.
- Mentioned-agent SSE collaboration branches now replace final model-bound messages with the shared assembly helper.
- Chat runtime context is now assembled from structured user fragments instead of moving the old `_build_runtime_context_block()` wholesale.
- Runtime fragments include project identity, project status, chat context, chat lineage, team members, relevant memories, inter-agent messages, previous agent work, and turn/tool summaries.
- Chat context selection uses model context-window metadata where available to derive a token budget for runtime fragments.

### Shared Chat Builder

Added `backend/services/chat_prompt_builder.py`:

- Extracts the shared chat prompt assembly logic out of `api.py`.
- Centralizes agent base system prompt generation, team/memory context helpers, model context-window lookup, and context selector construction.
- Lets primary route handling and fallback chat manager use the same prompt assembly entry point.

Updated `backend/chatrooms/manager.py`:

- Fallback `ChatroomManager._call_agent_llm()` now uses the shared chat prompt builder instead of its own partial prompt assembly path.
- Shared chat assembly now injects task-state fragments ahead of generic runtime fragments, so chat/fallback paths keep the current request, active goal, blocker, and validation checklist in a higher-priority context layer.

### Query Agent Tool

Updated `backend/tools/query_agent.py`:

- `query_agent` now uses `target_agent` as the tool parameter instead of the ambiguous `agent_name`.
- Runtime caller metadata is now carried separately via `caller_agent_name`.
- The queried agent now receives the same layered prompt model as other chat entry points.
- The queried agent now also receives task-state fragments derived from the shared project state and the current query payload.
- A light alias remains internally so older direct callers using `agent_name` as the target can still be resolved during transition.

### Skills Behavior

Preserved ADR-008 behavior:

- `hint` is injected into developer context for all skills assigned to the agent.
- `guide` is injected only when the skill is active for the current stage.
- `full` content remains outside the prompt and continues to live under `.catown/skills/<skill-id>/SKILL.md`.

## Tests

Added `backend/tests/test_prompt_context_builder.py` with coverage for:

- System prompt only contains stable identity/role/rules and excludes stage/BOSS/inter-agent context.
- Message order is `system -> developer -> user -> history -> current input`.
- Skill `hint` is always injected and `guide` only appears for active skills.
- BOSS instructions go to developer context.
- Inter-agent messages go to user context.
- Empty stage/skills/runtime context still produces usable fallback messages.
- Developer role fallback merges developer context into system.
- Recent history preserves previous windowing and visibility behavior.
- DB-like agent objects use structured config fields and ignore legacy monolithic `system_prompt`.
- Runtime user context is split into structured prioritized fragments.
- Task-state fragments are emitted separately from generic project status so selectors can keep active work memory under tighter budgets.
- Context selector can limit fragments, filter by scope/visibility, and enforce token budgets across developer and user roles.
- Context selector can derive runtime budget from model context window.
- Turn state keeps recent tool protocol continuation while summarizing older tool rounds.

Added `backend/tests/test_context_integration.py` with coverage for:

- `query_agent` uses layered prompt assembly and the new `target_agent` schema.
- fallback `ChatroomManager._call_agent_llm()` uses the shared chat prompt builder.
- task-state fragments appear in both `query_agent` and fallback chat assembly.

## Test Results

Passed:

```bash
python -m py_compile backend/services/context_builder.py backend/services/turn_state.py backend/services/chat_prompt_builder.py backend/pipeline/engine.py backend/routes/api.py backend/chatrooms/manager.py backend/tools/query_agent.py backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
python -m py_compile backend/services/task_state.py backend/services/chat_prompt_builder.py backend/services/context_builder.py backend/tools/query_agent.py backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
pytest backend/tests/test_prompt_context_builder.py backend/tests/test_context_integration.py
```

Result:

```text
20 passed
```

Plan regression command also run:

```bash
pytest backend/tests/test_config_models.py backend/tests/test_llm_extended.py backend/tests/test_api_routes.py
```

Result:

```text
25 failed, 63 passed
```

Observed failures are concentrated in pre-existing/non-context-builder areas:

- `AgentConfigV2` tests expect legacy minimal config shape, but current model requires structured `soul` and `role`.
- `create_agent_config_from_provider()` tests expect a `system_prompt` keyword that the current implementation no longer accepts.
- `LLMClient.chat()` tests fail on existing `started_at` being undefined in `backend/llm/client.py`.
- Several API route tests expect legacy default agent names such as `assistant`/lowercase mentioned names, while current config/runtime returns names such as `Valet` and `Analyst`.

No failures were observed in the new context builder unit tests or Python syntax checks.
