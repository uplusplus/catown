# ADR-024: Configurable Tool Authorization Policy

**Status**: Proposed  
**Date**: 2026-05-23  
**Decision makers**: BOSS + Catown Runtime

**Related**:
- [ADR-020: Prompt Guidance vs Runtime Contracts](./ADR-020-prompt-vs-runtime-contracts.md)
- [ADR-023: Execution and Authorization Timing](./ADR-023-execution-authorization-timing.md)

## Context

Catown already has several authorization pieces:

- tool default policy, such as manual or conditional approval;
- `run_shell` read-only classification and auto allowlist;
- remembered authorization rules in `tool_execution_preferences`;
- approval queue items for concrete blocked runtime actions;
- project/chat scoped persisted rules.

The behavior is usable but not yet expressed as one configurable policy model. This
makes it hard to reason about questions such as:

- should an approval apply to the exact command, the executable name, or the whole tool?
- should it apply only to this chat, the project, or globally?
- should read-only shell commands continue to bypass approval?
- what does "full permission" actually bypass?

## Decision

Catown authorization will be modeled as four independent axes:

1. **Decision**: what to do when the rule matches.
2. **Matcher**: what invocation shape the rule matches.
3. **Scope**: where the rule applies.
4. **Baseline classifier**: built-in risk classification when no explicit rule matches.

This avoids treating "bin name", "command hash", "project", and "chat" as competing
levels. They are composable:

- "allow exact command hash in this chat"
- "ask for npm in this project"
- "allow read-only shell commands everywhere"
- "full access for Developer in this project"

## Hard Safety Floor

Authorization rules grant permission only inside the tool boundary already exposed to an
agent. They must not bypass:

- `system_only` tools;
- workspace/path sandbox checks;
- missing tool capability assignment;
- malformed request payload validation;
- cancellation and task lifecycle guards;
- explicit deny rules.

"Full access" means "do not create manual approval queue items for matching invocations",
not "ignore runtime safety invariants."

## Decisions

| Decision | Meaning | Queue behavior |
| --- | --- | --- |
| `allow` | Execute without asking. | No approval item. |
| `require_approval` | Pause and ask for this invocation. | Create/keep approval item. |
| `deny` | Block without continuation. | No approval continuation; may create audit/block event. |
| `allow_no_timeout` | Execute and allow long wait behavior for matching run_shell command. | No timeout approval item. |

`deny` wins over all allows. `require_approval` is useful for policy presets that force
approval even if a broader allow exists.

## Matchers

| Matcher | Example | Applies to | Notes |
| --- | --- | --- | --- |
| `all_tools` | full access for a scope | Any tool invocation | Still respects hard safety floor. |
| `tool_target` | `run_shell`, `write_file` | Tool name | Current remembered "tool" approval maps here. |
| `shell_bin` | `python3`, `npm`, `git` | `run_shell` executable segment | For chained commands, every executable segment must be covered or the command falls through to stricter policy. |
| `command_fingerprint` | sha256/sha1 of normalized command + cwd | Exact `run_shell` command | Current command remembered approval maps here. |
| `read_only_class` | built-in safe shell/read tool class | Classifier output | Runtime-managed baseline, not user-authored free text. |

`shell_bin` is distinct from `tool_target`: approving `python3` means the `run_shell`
command may execute a Python binary; approving `run_shell` means the whole shell tool is
allowed.

For `run_shell`, command normalization must include:

- normalized command line;
- normalized cwd, with project workspace aliases resolved;
- shell platform when relevant;
- optional constraints such as "no redirection", "no background job", "no network".

## Scopes

| Scope | Meaning |
| --- | --- |
| `chatroom` | Applies only inside one chat. |
| `project` | Applies to all chats for one project. |
| `global` | Applies across Catown. Should be reserved for trusted local/operator policy. |

Rules may also be agent-specific through `agent_name`. An agent-specific rule is more
specific than an all-agent rule at the same scope/matcher.

Target precedence:

1. hard safety floor;
2. explicit `deny`;
3. explicit `require_approval`;
4. explicit `allow` / `allow_no_timeout`;
5. built-in read-only auto allow;
6. tool default policy.

Within the same decision class, specificity sorts by:

1. scope: `chatroom` > `project` > `global`;
2. agent: specific agent > all agents;
3. matcher: `command_fingerprint` > `shell_bin` > `tool_target` > `all_tools` > `read_only_class`;
4. newest active rule.

If implementation keeps the current database helper ordering during migration, that
must be treated as a compatibility gap, not the target policy.

## Permission Presets

### 1. Full Permission, No Approval

Use `decision=allow`, `matcher_type=all_tools`.

Recommended scopes:

- project-level for trusted local development;
- chat-level for temporary experimental sessions;
- agent-specific when only one agent should bypass approval.

This still does not bypass the hard safety floor.

### 2. Apply by Bin/Tool Name

Use:

- `matcher_type=tool_target` for non-shell tools or the entire `run_shell` tool;
- `matcher_type=shell_bin` for executable names inside `run_shell`.

Examples:

- allow `git` read/write commands in project;
- require approval for `npm` in chat;
- deny `curl` globally.

For shell chains, approving one binary is not enough if the command also invokes another
unapproved binary or uses side-effect shell operators.

### 3. Apply by Full Command-Line Hash

Use `matcher_type=command_fingerprint`.

This is the safest remembered approval for risky shell commands. It should bind to the
normalized command and cwd. UI should show the command preview and the fingerprint, but
users approve the human-readable command preview.

### 4. Project Dimension

Use `scope=project`.

This is the default remembered approval for project work because many approvals are tied
to a repository/workspace. It should be used for commands such as:

- project test runner;
- project build command;
- project formatter;
- project-local script.

### 5. Chat Dimension

Use `scope=chatroom`.

This is the safest temporary permission. It is appropriate for one-off diagnostics,
experimental commands, or user-specific conversation context that should not leak into
other project chats.

## Built-in Read-Only Auto Allow

Read-only auto allow remains a baseline classifier, not a remembered rule created by the
user.

For `run_shell`, the classifier may auto-allow commands such as:

- safe filesystem inspection: `ls`, `cat`, `head`, `tail`, `wc`, `rg`, `grep`;
- safe path inspection: `pwd`, `realpath`, `readlink`, `stat`;
- safe git inspection: `git status`, `git log`, `git diff`, `git show`, `git rev-parse`.

It must require approval for:

- redirection or heredoc;
- background jobs;
- shell command substitution;
- environment assignments;
- mutating commands such as `rm`, `mv`, `cp`, `chmod`, `npm`, `python`, `pip`, `make`;
- unknown binaries.

Explicit rules can override this baseline:

- `deny` may block a normally read-only command;
- `require_approval` may force approval for a normally read-only class;
- `allow` may permit a risky command at a specific matcher/scope.

## Data Model Mapping

Current `tool_execution_preferences` can support the policy with small extensions.

Existing fields:

- `tool_name`
- `scope`
- `project_id`
- `chatroom_id`
- `agent_name`
- `matcher_type`
- `matcher_value`
- `decision_kind`
- `preference_kind`
- `constraints_json`
- `expires_at`
- `revoked_at`
- `command_preview`

Required matcher constants:

- existing: `command_fingerprint`, `tool_target`
- add: `all_tools`, `shell_bin`, `read_only_class`

Required decision constants:

- existing: `allow`, `deny`, `allow_no_timeout`
- add or formalize: `require_approval`

Recommended `constraints_json` fields:

```json
{
  "no_redirection": true,
  "no_background_jobs": true,
  "no_command_substitution": true,
  "allow_network": false,
  "allow_workspace_mutation": false,
  "expires_after_uses": null,
  "created_from_queue_item_id": 123
}
```

## Evaluation Algorithm

```text
input: tool_name, arguments, project_id, chatroom_id, agent_name

1. Reject if hard safety floor fails.
2. Build matcher candidates:
   - all_tools
   - tool_target
   - shell_bin values for run_shell
   - command_fingerprint for run_shell
   - read_only_class when classifier says read-only
3. Load active rules for matching tool/scope/agent/matcher.
4. Sort by decision priority and specificity.
5. If best rule is deny: block.
6. If best rule is require_approval: create approval queue item.
7. If best rule is allow/allow_no_timeout: execute.
8. If no rule matched and classifier says read-only: execute.
9. Otherwise use tool default policy; for risky run_shell, request approval.
```

## Approval UI Requirements

Every approval request must show the approval id (`Approval #<queue_item_id>`)
and the command/tool being blocked. The runtime approval card deliberately stays
small: it offers approve once and remember, plus the existing reject actions.

The matcher/scope policy for remember is configured in Permissions settings, not
selected per approval card. This keeps the chat approval path focused on
continuation semantics while still allowing administrators to choose project,
chat, global, exact-command, bin/tool, tool-target, or full-access defaults.

Default configuration should remain conservative:

- `remember_default_scope=project`
- `remember_default_matcher=command_fingerprint`

## Non-goals

- This ADR does not implement the policy UI.
- This ADR does not remove the approval queue.
- This ADR does not let remembered approvals bypass workspace or system-only guards.
- This ADR does not silently merge duplicate approval queue items; duplicate creation must
  still be fixed at runtime ownership/idempotency boundaries.

## Migration Notes

Existing remembered command approvals map to:

- `decision_kind=allow`
- `matcher_type=command_fingerprint`
- existing `scope`

Existing remembered tool approvals map to:

- `decision_kind=allow`
- `matcher_type=tool_target`
- existing `scope`

Existing read-only shell auto allow remains code-owned classifier behavior. It should not
be backfilled as user-authored rules.
