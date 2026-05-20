import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { AdaptiveCardDeck } from "./AdaptiveCardDeck";
import type {
  AgentInfo,
  AgentMemoryItem,
  ConfigAgentDefinition,
  ContextConfigPayload,
  ConfigResponse,
  ConfigSection,
  PermissionsConfigPayload,
  SkillMarketplace,
  ToolAuthorizationRule,
} from "../types";
import { DEFAULT_AGENT_TYPE, defaultAgentName } from "../utils/agents";

type ConfigTabProps = {
  config: ConfigResponse | null;
  activeSection: ConfigSection;
  saving: boolean;
  onBackToChat: () => void;
  onSaveGlobal: (payload: {
    provider: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string; contextWindow?: number }> };
    default_model: string;
  }) => Promise<void>;
  onSaveAgent: (
    agentName: string,
    payload: {
      provider?: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string; contextWindow?: number }> };
      default_model?: string;
      role?: {
        title?: string;
        responsibilities?: string[];
        rules?: string[];
      };
      soul?: {
        identity?: string;
        values?: string[];
        style?: string;
        quirks?: string;
      };
      tools?: string[];
      skills?: string[];
    },
  ) => Promise<void>;
  onSaveOrchestration: (payload: { sidecar_agent_types: string[] }) => Promise<void>;
  onSavePermissions: (payload: PermissionsConfigPayload) => Promise<void>;
  onSaveContext: (payload: ContextConfigPayload) => Promise<void>;
  authorizationRules: ToolAuthorizationRule[];
  onRevokeAuthorizationRule: (ruleId: number) => Promise<void>;
  onReload: () => Promise<void>;
  onTestAgentConfig: (agentName: string) => Promise<void>;
};

type GlobalDraft = {
  baseUrl: string;
  apiKey: string;
  model: string;
  contextWindow: string;
};

type OrchestrationDraft = {
  sidecarAgentTypes: string;
};

type PermissionsDraft = {
  allowReadOnlyToolsWithoutApproval: boolean;
  autoApproveAll: boolean;
};

type ContextDraft = {
  selectorProfilesJson: string;
};

type AgentDraft = {
  baseUrl: string;
  apiKey: string;
  model: string;
  contextWindow: string;
  roleTitle: string;
  responsibilities: string;
  rules: string;
  soulIdentity: string;
  soulStyle: string;
  soulValues: string;
  soulQuirks: string;
  tools: string;
  skills: string;
};

const COLLABORATION_TOOL_NAMES = [
  "delegate_task",
  "broadcast_message",
  "check_task_status",
  "list_collaborators",
  "send_direct_message",
  "consult_agent",
  "list_agents",
  "invite_agent",
] as const;

const TOOL_NAME_ALIASES: Record<string, string> = {
  query_agent: "consult_agent",
};

function canonicalToolName(toolName: string): string {
  const normalized = toolName.trim();
  return TOOL_NAME_ALIASES[normalized] ?? normalized;
}

function canonicalToolNames(toolNames: string[]): string[] {
  return Array.from(new Set(toolNames.map(canonicalToolName).filter(Boolean)));
}

function buildGlobalDraft(config: ConfigResponse | null): GlobalDraft {
  const provider = config?.global_llm?.provider;
  const defaultModel = config?.global_llm?.default_model ?? provider?.models?.[0]?.id ?? "";
  const modelConfig = provider?.models?.find((model) => model.id === defaultModel) ?? provider?.models?.[0];
  return {
    baseUrl: provider?.baseUrl ?? "",
    apiKey: provider?.apiKey ?? "",
    model: defaultModel,
    contextWindow:
      typeof modelConfig?.contextWindow === "number" && Number.isFinite(modelConfig.contextWindow) && modelConfig.contextWindow > 0
        ? String(Math.trunc(modelConfig.contextWindow))
        : "",
  };
}

function buildOrchestrationDraft(config: ConfigResponse | null): OrchestrationDraft {
  return {
    sidecarAgentTypes: (config?.orchestration?.sidecar_agent_types ?? []).join("\n"),
  };
}

function buildPermissionsDraft(config: ConfigResponse | null): PermissionsDraft {
  return {
    allowReadOnlyToolsWithoutApproval: config?.permissions?.allow_read_only_tools_without_approval ?? true,
    autoApproveAll: config?.permissions?.auto_approve_all ?? false,
  };
}

function buildContextDraft(config: ConfigResponse | null): ContextDraft {
  return {
    selectorProfilesJson: JSON.stringify(config?.context?.selector_profiles ?? {}, null, 2),
  };
}

function buildAgentDraft(
  agentConfig: ConfigAgentDefinition | undefined,
  effective: ConfigResponse["agent_llm_configs"][string] | undefined,
): AgentDraft {
  const providerModel =
    agentConfig?.provider?.models?.find((model) => model.id === agentConfig?.default_model)
      ?? agentConfig?.provider?.models?.[0];
  return {
    baseUrl: agentConfig?.provider?.baseUrl ?? "",
    apiKey: agentConfig?.provider?.apiKey ?? "",
    model: agentConfig?.default_model ?? effective?.model ?? "",
    contextWindow:
      typeof providerModel?.contextWindow === "number" && Number.isFinite(providerModel.contextWindow) && providerModel.contextWindow > 0
        ? String(Math.trunc(providerModel.contextWindow))
        : "",
    roleTitle: agentConfig?.role?.title ?? "",
    responsibilities: (agentConfig?.role?.responsibilities ?? []).join("\n"),
    rules: (agentConfig?.role?.rules ?? []).join("\n"),
    soulIdentity: agentConfig?.soul?.identity ?? "",
    soulStyle: agentConfig?.soul?.style ?? "",
    soulValues: (agentConfig?.soul?.values ?? []).join("\n"),
    soulQuirks: agentConfig?.soul?.quirks ?? "",
    tools: canonicalToolNames(agentConfig?.tools ?? []).join("\n"),
    skills: (agentConfig?.skills ?? []).join("\n"),
  };
}

function readMultilineList(value: string) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function readPositiveInteger(value: string) {
  const normalized = value.trim();
  if (!normalized) return undefined;
  const parsed = Number.parseInt(normalized, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function previewText(value: string | undefined | null, fallback = "Not configured") {
  const normalized = (value ?? "").trim();
  return normalized || fallback;
}

function previewList(values: string[] | undefined, fallback = "Not configured", limit = 4) {
  const normalized = (values ?? []).map((item) => item.trim()).filter(Boolean);
  if (normalized.length === 0) return fallback;
  const visible = normalized.slice(0, limit).join(" · ");
  return normalized.length > limit ? `${visible} +${normalized.length - limit}` : visible;
}

function previewSecret(value: string | undefined | null, fallback = "Not configured") {
  const normalized = (value ?? "").trim();
  if (!normalized) return fallback;
  if (normalized.length <= 8) return "Configured";
  return `Configured · ${normalized.slice(0, 3)}...${normalized.slice(-4)}`;
}

function previewContextWindow(value: number | undefined | null, fallback = "Not configured") {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return fallback;
  return value.toLocaleString("en-US");
}

function formatMemoryDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function PreviewCard({
  title,
  subtitle,
  items,
  onActivate,
}: {
  title: string;
  subtitle: string;
  items: Array<{ label: string; value: string }>;
  onActivate: () => void;
}) {
  return (
    <button type="button" className="config-card-preview" onClick={onActivate}>
      <div className="config-card-preview__header">
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </div>
      <div className="config-card-preview__grid">
        {items.map((item) => (
          <div key={`${title}-${item.label}`} className="config-card-preview__item">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      <div className="config-card-preview__footer">Click to edit</div>
    </button>
  );
}

function AgentMemoryCard({
  displayName,
  agentType,
  identity,
  style,
  values,
  memories,
  memoryCount,
  runtimeMissing,
}: {
  displayName: string;
  agentType: string;
  identity: string;
  style: string;
  values: string[];
  memories: AgentMemoryItem[];
  memoryCount: number;
  runtimeMissing: boolean;
}) {
  const visibleMemories = memories.slice(0, 8);
  const hiddenMemories = Math.max(memoryCount - visibleMemories.length, 0);
  return (
    <div className="config-agent-card config-memory-card">
      <div className="config-agent-card__header">
        <div>
          <h3>{displayName}</h3>
          <p className="config-agent-card__eyebrow">@{agentType}</p>
        </div>
        <span className={`soft-pill ${memoryCount > 0 ? "soft-pill--success" : ""}`}>
          {runtimeMissing ? "No runtime agent" : `${memoryCount} memories`}
        </span>
      </div>

      <PreviewCard
        title={`${displayName} Persona`}
        subtitle="Configured identity context"
        items={[
          { label: "Identity", value: identity },
          { label: "Style", value: style },
          { label: "Values", value: previewList(values, "Not configured") },
        ]}
        onActivate={() => undefined}
      />

      <div className="config-memory-list">
        <div className="config-memory-list__header">
          <strong>Long-term memories</strong>
          <span>{runtimeMissing ? "Unavailable" : `${memoryCount} saved`}</span>
        </div>
        {runtimeMissing ? (
          <div className="config-memory-empty">This configured agent is not present in the runtime agent list.</div>
        ) : visibleMemories.length === 0 ? (
          <div className="config-memory-empty">No saved long-term memories yet.</div>
        ) : (
          visibleMemories.map((memory) => (
            <article key={memory.id} className="config-memory-item">
              <div className="config-memory-item__meta">
                <span className="soft-pill">{memory.type}</span>
                <span className="soft-pill">importance {memory.importance}</span>
                <time>{formatMemoryDate(memory.created_at)}</time>
              </div>
              <p>{memory.content}</p>
            </article>
          ))
        )}
        {hiddenMemories > 0 ? <div className="config-memory-more">+{hiddenMemories} more memories</div> : null}
      </div>
    </div>
  );
}

function SkillGalleryCard({ name, agents, prompt }: { name: string; agents: string[]; prompt?: string }) {
  return (
    <div className="config-skill-card">
      <div className="config-skill-card__header">
        <div>
          <h3>{name}</h3>
          <p>{agents.length} agent bindings</p>
        </div>
        <span className="soft-pill">{agents.length}</span>
      </div>
      <div className="config-skill-card__agents">
        {agents.map((agent) => (
          <span key={`${name}-${agent}`} className="soft-pill">
            {agent}
          </span>
        ))}
      </div>
      <div className="config-skill-card__footer">{prompt?.trim() || "Edit an agent card in Settings to change this skill binding."}</div>
    </div>
  );
}

function ToolGalleryCard({
  name,
  agents,
  approvals,
  description,
  riskLevel,
  approvalKind,
}: {
  name: string;
  agents: string[];
  approvals: number;
  description?: string;
  riskLevel?: string;
  approvalKind?: string;
}) {
  const isBound = agents.length > 0;
  return (
    <div className="config-skill-card">
      <div className="config-skill-card__header">
        <div>
          <h3>{name}</h3>
          <p>{isBound ? `${agents.length} agent bindings` : "Registered, not assigned"}</p>
        </div>
        <span className={`soft-pill ${isBound ? "" : "soft-pill--accent"}`}>{isBound ? agents.length : "0"}</span>
      </div>
      <div className="config-skill-card__agents">
        {agents.length > 0 ? (
          agents.map((agent) => (
            <span key={`${name}-${agent}`} className="soft-pill">
              {agent}
            </span>
          ))
        ) : (
          <span className="soft-pill soft-pill--accent">System tool only</span>
        )}
        {approvals > 0 ? <span className="soft-pill soft-pill--accent">{approvals} saved rules</span> : null}
        {riskLevel ? <span className="soft-pill">{riskLevel}</span> : null}
        {approvalKind ? <span className="soft-pill">{approvalKind}</span> : null}
      </div>
      <div className="config-skill-card__footer">
        {description?.trim()
          ? description
          : isBound
            ? "Use Agent Settings to edit bindings. Use Permissions to change approval defaults and revoke saved rules."
            : "This tool is registered in the system but is not currently assigned to any agent."}
      </div>
    </div>
  );
}

function AgentGalleryCard({
  displayName,
  agentType,
  sourceLabel,
  modelLabel,
  contextWindowLabel,
  roleTitle,
  identity,
  style,
  responsibilities,
  rules,
  tools,
  skills,
  onEdit,
  onTest,
  disabled,
}: {
  displayName: string;
  agentType: string;
  sourceLabel: string;
  modelLabel: string;
  contextWindowLabel: string;
  roleTitle: string;
  identity: string;
  style: string;
  responsibilities: string[];
  rules: string[];
  tools: string[];
  skills: string[];
  onEdit: () => void;
  onTest: () => void;
  disabled: boolean;
}) {
  const visibleTools = tools.slice(0, 4);
  const visibleSkills = skills.slice(0, 4);
  const hiddenTools = Math.max(tools.length - visibleTools.length, 0);
  const hiddenSkills = Math.max(skills.length - visibleSkills.length, 0);

  return (
    <article className="config-agent-overview">
      <div className="config-agent-overview__hero">
        <div>
          <div className="config-agent-overview__eyebrow">@{agentType}</div>
          <strong>{displayName}</strong>
        </div>
        <span className="soft-pill">{modelLabel}</span>
      </div>

      <div className="config-agent-overview__meta">
        <div className="config-agent-overview__meta-item">
          <span>Source</span>
          <strong>{sourceLabel}</strong>
        </div>
        <div className="config-agent-overview__meta-item">
          <span>Role</span>
          <strong>{roleTitle}</strong>
        </div>
        <div className="config-agent-overview__meta-item">
          <span>Style</span>
          <strong>{style}</strong>
        </div>
        <div className="config-agent-overview__meta-item">
          <span>Ctx Window</span>
          <strong>{contextWindowLabel}</strong>
        </div>
        <div className="config-agent-overview__meta-item">
          <span>Rules</span>
          <strong>{rules.length}</strong>
        </div>
      </div>

      <div className="config-agent-overview__story">
        <div className="config-agent-overview__section">
          <span>Identity</span>
          <p>{identity}</p>
        </div>
        <div className="config-agent-overview__section">
          <span>Responsibilities</span>
          <p>{responsibilities.slice(0, 2).join(" · ") || "Not configured"}</p>
        </div>
      </div>

      <div className="config-agent-overview__tags">
        {visibleSkills.map((skill) => (
          <span key={`${agentType}-skill-${skill}`} className="soft-pill soft-pill--accent">
            {skill}
          </span>
        ))}
        {hiddenSkills > 0 ? <span className="soft-pill soft-pill--accent">+{hiddenSkills} skills</span> : null}
        {visibleTools.map((tool) => (
          <span key={`${agentType}-tool-${tool}`} className="soft-pill">
            {tool}
          </span>
        ))}
        {hiddenTools > 0 ? <span className="soft-pill">+{hiddenTools} tools</span> : null}
      </div>

      <div className="config-agent-overview__footer">
        <span>Monitor-style overview. Open edit mode when you need to change provider, role, tools, or skills.</span>
        <div className="config-agent-overview__actions">
          <button type="button" className="primary-button compact-button" onClick={onEdit} disabled={disabled}>
            Edit
          </button>
          <button type="button" className="secondary-button compact-button" onClick={onTest} disabled={disabled}>
            Test
          </button>
        </div>
      </div>
    </article>
  );
}

export function ConfigTab({
  config,
  activeSection,
  saving,
  onBackToChat,
  onSaveGlobal,
  onSaveOrchestration,
  onSavePermissions,
  onSaveContext,
  authorizationRules,
  onRevokeAuthorizationRule,
  onSaveAgent,
  onReload,
  onTestAgentConfig,
}: ConfigTabProps) {
  const [globalBaseUrl, setGlobalBaseUrl] = useState("");
  const [globalApiKey, setGlobalApiKey] = useState("");
  const [globalModel, setGlobalModel] = useState("");
  const [globalContextWindow, setGlobalContextWindow] = useState("");
  const [orchestrationDraft, setOrchestrationDraft] = useState<OrchestrationDraft>(() => buildOrchestrationDraft(config));
  const [permissionsDraft, setPermissionsDraft] = useState<PermissionsDraft>(() => buildPermissionsDraft(config));
  const [contextDraft, setContextDraft] = useState<ContextDraft>(() => buildContextDraft(config));
  const [contextDraftError, setContextDraftError] = useState("");
  const [syncToAllAgents, setSyncToAllAgents] = useState(true);
  const [agentDrafts, setAgentDrafts] = useState<Record<string, AgentDraft>>({});
  const [editingGlobal, setEditingGlobal] = useState(false);
  const [editingOrchestration, setEditingOrchestration] = useState(false);
  const [editingContext, setEditingContext] = useState(false);
  const [editingAgents, setEditingAgents] = useState<Record<string, boolean>>({});
  const [marketplaces, setMarketplaces] = useState<SkillMarketplace[]>([]);
  const [marketplaceError, setMarketplaceError] = useState("");
  const [updatingMarketplace, setUpdatingMarketplace] = useState<string | null>(null);
  const [runtimeAgents, setRuntimeAgents] = useState<AgentInfo[]>([]);
  const [agentMemories, setAgentMemories] = useState<Record<number, AgentMemoryItem[]>>({});
  const [memoryCounts, setMemoryCounts] = useState<Record<number, number>>({});
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState("");

  const agentEntries = useMemo(() => Object.entries(config?.agents ?? {}), [config]);
  const agentConfigs = config?.agent_llm_configs ?? {};
  const skillRows = useMemo(() => {
    const catalog = config?.skills_catalog ?? {};
    const rows = new Map<string, string[]>();
    for (const [agentName, agentConfig] of agentEntries) {
      for (const skill of agentConfig.skills ?? []) {
        const normalized = skill.trim();
        if (!normalized) continue;
        rows.set(normalized, [...(rows.get(normalized) ?? []), agentConfig.name?.trim() || defaultAgentName(agentName)]);
      }
    }
    return Array.from(rows.entries())
      .map(([name, agents]) => {
        const entry = catalog[name] ?? {};
        return {
          name,
          agents,
          prompt: entry.levels?.full || entry.levels?.guide || entry.levels?.hint || entry.description || "",
        };
      })
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [agentEntries, config?.skills_catalog]);
  const toolRows = useMemo(() => {
    const policyByName = new Map((config?.tools?.tool_policies ?? []).map((policy) => [policy.name, policy]));
    const rows = new Map<string, string[]>();
    for (const [agentName, agentConfig] of agentEntries) {
      for (const tool of agentConfig.tools ?? []) {
        const normalized = canonicalToolName(tool);
        if (!normalized) continue;
        rows.set(normalized, [...(rows.get(normalized) ?? []), agentConfig.name?.trim() || defaultAgentName(agentName)]);
      }
    }
    const namedRows = Array.from(rows.entries())
      .map(([name, agents]) => {
        const policy = policyByName.get(name);
        return {
          name,
          agents,
          approvals: authorizationRules.filter((rule) => rule.tool_name === name).length,
          description: policy?.description,
          riskLevel: policy?.risk_level,
          approvalKind: policy?.approval?.kind,
        };
      });
    for (const policy of config?.tools?.tool_policies ?? []) {
      if (rows.has(policy.name)) continue;
      namedRows.push({
        name: policy.name,
        agents: [],
        approvals: authorizationRules.filter((rule) => rule.tool_name === policy.name).length,
        description: policy.description,
        riskLevel: policy.risk_level,
        approvalKind: policy.approval?.kind,
      });
    }
    return namedRows
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [agentEntries, authorizationRules, config?.tools?.tool_policies]);
  const memoryRows = useMemo(() => {
    const runtimeByType = new Map(runtimeAgents.map((agent) => [agent.type, agent]));
    return agentEntries.map(([agentName, agentConfig]) => {
      const runtimeAgent = runtimeByType.get(agentName);
      const memoryRowsForAgent = runtimeAgent ? (agentMemories[runtimeAgent.id] ?? []) : [];
      return {
        name: agentConfig.name?.trim() || defaultAgentName(agentName),
        type: agentName,
        runtimeAgent,
        identity: previewText(agentConfig.soul?.identity, "Not configured"),
        values: agentConfig.soul?.values ?? [],
        style: previewText(agentConfig.soul?.style, "Not configured"),
        memories: memoryRowsForAgent,
        memoryCount: runtimeAgent ? (memoryCounts[runtimeAgent.id] ?? memoryRowsForAgent.length) : 0,
      };
    });
  }, [agentEntries, agentMemories, memoryCounts, runtimeAgents]);
  const totalMemoryCount = useMemo(
    () => memoryRows.reduce((total, row) => total + row.memoryCount, 0),
    [memoryRows],
  );
  const hasEditingAgent = useMemo(() => Object.values(editingAgents).some(Boolean), [editingAgents]);

  useEffect(() => {
    const draft = buildGlobalDraft(config);
    setGlobalBaseUrl(draft.baseUrl);
    setGlobalApiKey(draft.apiKey);
    setGlobalModel(draft.model);
    setGlobalContextWindow(draft.contextWindow);
  }, [config]);

  useEffect(() => {
    setOrchestrationDraft(buildOrchestrationDraft(config));
  }, [config]);

  useEffect(() => {
    setPermissionsDraft(buildPermissionsDraft(config));
  }, [config]);

  useEffect(() => {
    setContextDraft(buildContextDraft(config));
    setContextDraftError("");
  }, [config]);

  useEffect(() => {
    const nextDrafts: Record<string, AgentDraft> = {};
    for (const [agentName, agentConfig] of agentEntries) {
      nextDrafts[agentName] = buildAgentDraft(agentConfig, agentConfigs[agentName]);
    }
    setAgentDrafts(nextDrafts);
  }, [agentEntries, agentConfigs]);

  useEffect(() => {
    if (activeSection !== "skills") return;
    let cancelled = false;
    async function loadMarketplaces() {
      try {
        setMarketplaceError("");
        const result = await api.getSkillMarketplaces();
        if (!cancelled) setMarketplaces(result.marketplaces);
      } catch (nextError) {
        if (!cancelled) {
          setMarketplaceError(nextError instanceof Error ? nextError.message : "Failed to load skill marketplaces");
        }
      }
    }
    void loadMarketplaces();
    return () => {
      cancelled = true;
    };
  }, [activeSection]);

  useEffect(() => {
    if (activeSection !== "memory") return;
    let cancelled = false;

    async function loadAgentMemories() {
      try {
        setMemoryLoading(true);
        setMemoryError("");
        const agents = await api.getAgents();
        if (cancelled) return;
        setRuntimeAgents(agents);

        const memoryResults = await Promise.all(
          agents.map(async (agent) => {
            const response = await api.getAgentMemory(agent.id);
            return [agent.id, response] as const;
          }),
        );
        if (cancelled) return;

        const nextMemories: Record<number, AgentMemoryItem[]> = {};
        const nextCounts: Record<number, number> = {};
        for (const [agentId, response] of memoryResults) {
          nextMemories[agentId] = response.memories;
          nextCounts[agentId] = response.memory_count;
        }
        setAgentMemories(nextMemories);
        setMemoryCounts(nextCounts);
      } catch (nextError) {
        if (!cancelled) {
          setMemoryError(nextError instanceof Error ? nextError.message : "Failed to load agent memories");
        }
      } finally {
        if (!cancelled) setMemoryLoading(false);
      }
    }

    void loadAgentMemories();
    return () => {
      cancelled = true;
    };
  }, [activeSection]);

  function resetGlobalDraft() {
    const draft = buildGlobalDraft(config);
    setGlobalBaseUrl(draft.baseUrl);
    setGlobalApiKey(draft.apiKey);
    setGlobalModel(draft.model);
    setGlobalContextWindow(draft.contextWindow);
  }

  function startEditingGlobal() {
    resetGlobalDraft();
    setEditingGlobal(true);
  }

  function cancelEditingGlobal() {
    resetGlobalDraft();
    setEditingGlobal(false);
  }

  async function handleGlobalSubmit(event: FormEvent) {
    event.preventDefault();
    const payload = {
      provider: {
        baseUrl: globalBaseUrl.trim(),
        apiKey: globalApiKey.trim(),
        models: [{
          id: globalModel.trim(),
          name: globalModel.trim(),
          contextWindow: readPositiveInteger(globalContextWindow),
        }],
      },
      default_model: globalModel.trim(),
    };

    await onSaveGlobal(payload);

    if (syncToAllAgents) {
      for (const [agentName] of agentEntries) {
        await onSaveAgent(agentName, payload);
      }
    }

    setEditingGlobal(false);
  }

  function resetOrchestrationDraft() {
    setOrchestrationDraft(buildOrchestrationDraft(config));
  }

  function startEditingOrchestration() {
    resetOrchestrationDraft();
    setEditingOrchestration(true);
  }

  function cancelEditingOrchestration() {
    resetOrchestrationDraft();
    setEditingOrchestration(false);
  }

  async function handleOrchestrationSubmit(event: FormEvent) {
    event.preventDefault();
    await onSaveOrchestration({
      sidecar_agent_types: readMultilineList(orchestrationDraft.sidecarAgentTypes),
    });
    setEditingOrchestration(false);
  }

  function startEditingContext() {
    setContextDraft(buildContextDraft(config));
    setContextDraftError("");
    setEditingContext(true);
  }

  function cancelEditingContext() {
    setContextDraft(buildContextDraft(config));
    setContextDraftError("");
    setEditingContext(false);
  }

  async function handleContextSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      const parsed = JSON.parse(contextDraft.selectorProfilesJson || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Selector profiles must be a JSON object.");
      }
      await onSaveContext({ selector_profiles: parsed });
      setContextDraftError("");
      setEditingContext(false);
    } catch (nextError) {
      setContextDraftError(nextError instanceof Error ? nextError.message : "Invalid selector profile JSON.");
    }
  }

  function updateAgentDraft(agentName: string, patch: Partial<AgentDraft>) {
    setAgentDrafts((current) => ({
      ...current,
      [agentName]: {
        ...current[agentName],
        ...patch,
      },
    }));
  }

  function resetAgentDraft(agentName: string) {
    setAgentDrafts((current) => ({
      ...current,
      [agentName]: buildAgentDraft(config?.agents?.[agentName], agentConfigs[agentName]),
    }));
  }

  function startEditingAgent(agentName: string) {
    resetAgentDraft(agentName);
    setEditingAgents((current) => ({ ...current, [agentName]: true }));
  }

  function cancelEditingAgent(agentName: string) {
    resetAgentDraft(agentName);
    setEditingAgents((current) => ({ ...current, [agentName]: false }));
  }

  async function handleSaveAgentDraft(agentName: string) {
    const draft = agentDrafts[agentName];
    if (!draft) return;
    await onSaveAgent(agentName, {
      provider: {
        baseUrl: draft.baseUrl.trim(),
        apiKey: draft.apiKey.trim(),
        models: [{
          id: draft.model.trim(),
          name: draft.model.trim(),
          contextWindow: readPositiveInteger(draft.contextWindow),
        }],
      },
      default_model: draft.model.trim(),
      role: {
        title: draft.roleTitle.trim(),
        responsibilities: readMultilineList(draft.responsibilities),
        rules: readMultilineList(draft.rules),
      },
      soul: {
        identity: draft.soulIdentity.trim(),
        style: draft.soulStyle.trim(),
        values: readMultilineList(draft.soulValues),
        quirks: draft.soulQuirks.trim(),
      },
      tools: canonicalToolNames(readMultilineList(draft.tools)),
      skills: readMultilineList(draft.skills),
    });
    setEditingAgents((current) => ({ ...current, [agentName]: false }));
  }

  async function handleClearAgent(agentName: string) {
    await onSaveAgent(agentName, {
      provider: {
        baseUrl: "",
        apiKey: "",
        models: [],
      },
      default_model: "",
    });
    setEditingAgents((current) => ({ ...current, [agentName]: false }));
  }

  async function handleMarketplaceToggle(marketplace: SkillMarketplace) {
    try {
      setUpdatingMarketplace(marketplace.id);
      setMarketplaceError("");
      const shouldBootstrap = marketplace.enabled && marketplace.command_available === false && Boolean(marketplace.bootstrap_available);
      await api.updateSkillMarketplace(marketplace.id, shouldBootstrap ? true : !marketplace.enabled, true);
      const result = await api.getSkillMarketplaces();
      setMarketplaces(result.marketplaces);
    } catch (nextError) {
      setMarketplaceError(nextError instanceof Error ? nextError.message : "Failed to update marketplace");
    } finally {
      setUpdatingMarketplace(null);
    }
  }

  async function handleRefreshMemories() {
    try {
      setMemoryLoading(true);
      setMemoryError("");
      const agents = await api.getAgents();
      setRuntimeAgents(agents);
      const memoryResults = await Promise.all(
        agents.map(async (agent) => {
          const response = await api.getAgentMemory(agent.id);
          return [agent.id, response] as const;
        }),
      );
      const nextMemories: Record<number, AgentMemoryItem[]> = {};
      const nextCounts: Record<number, number> = {};
      for (const [agentId, response] of memoryResults) {
        nextMemories[agentId] = response.memories;
        nextCounts[agentId] = response.memory_count;
      }
      setAgentMemories(nextMemories);
      setMemoryCounts(nextCounts);
    } catch (nextError) {
      setMemoryError(nextError instanceof Error ? nextError.message : "Failed to load agent memories");
    } finally {
      setMemoryLoading(false);
    }
  }

  const globalPreviewItems = useMemo(
    () => [
      { label: "Base URL", value: previewText(config?.global_llm?.provider?.baseUrl, "Not set") },
      { label: "Model", value: previewText(config?.global_llm?.default_model, "Not set") },
      {
        label: "Context Window",
        value: previewContextWindow(
          config?.global_llm?.provider?.models?.find((model) => model.id === config?.global_llm?.default_model)?.contextWindow
            ?? config?.global_llm?.provider?.models?.[0]?.contextWindow,
          "Not set",
        ),
      },
      { label: "API Key", value: previewSecret(config?.global_llm?.provider?.apiKey, "Not set") },
      { label: "Sync Policy", value: syncToAllAgents ? "Save global + fan out to agents" : "Save global only" },
    ],
    [config, syncToAllAgents],
  );
  const orchestrationPreviewItems = useMemo(
    () => [
      {
        label: "Sidecar agents",
        value: previewList(config?.orchestration?.sidecar_agent_types, "Disabled", 6),
      },
      {
        label: "Planner mode",
        value:
          (config?.orchestration?.sidecar_agent_types ?? []).length > 0
            ? "Blocking chain + sidecars"
            : "Linear blocking chain",
      },
      {
        label: "Dispatch policy",
        value: "Blocking steps run before sidecars on each release point",
      },
    ],
    [config],
  );
  const permissionsPreviewItems = useMemo(
    () => [
      {
        label: "Read-only defaults",
        value: permissionsDraft.allowReadOnlyToolsWithoutApproval ? "Auto allow" : "Approval required",
      },
      {
        label: "All approvals",
        value: permissionsDraft.autoApproveAll ? "Auto approve" : "Manual when required",
      },
      {
        label: "Covered tools",
        value: "read_file · list_files · search_files · list_agents · retrieve_memory",
      },
      {
        label: "Scope",
        value: "Global runtime policy",
      },
    ],
    [permissionsDraft.allowReadOnlyToolsWithoutApproval, permissionsDraft.autoApproveAll],
  );
  const contextProfileRows = useMemo(
    () => Object.entries(config?.context?.selector_profiles ?? {}).sort(([left], [right]) => left.localeCompare(right)),
    [config?.context?.selector_profiles],
  );
  const contextPreviewItems = useMemo(
    () => {
      const chatProfile = config?.context?.selector_profiles?.chat_interactive;
      return [
        { label: "Profiles", value: String(contextProfileRows.length) },
        { label: "Interactive cap", value: chatProfile?.max_tokens_cap ? previewContextWindow(chatProfile.max_tokens_cap) : "Not set" },
        { label: "Fragments", value: chatProfile?.max_fragments ? String(chatProfile.max_fragments) : "Not set" },
        { label: "Truncate", value: chatProfile?.truncate_to_budget === false ? "Disabled" : "Enabled" },
      ];
    },
    [config?.context?.selector_profiles, contextProfileRows.length],
  );

  if (activeSection === "skills") {
    return (
      <section className="panel-grid panel-grid--config panel-grid--config-fluid">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Skill Registry</p>
              <h2>Skill Management</h2>
            </div>
            <span className="soft-pill">{skillRows.length} skills</span>
          </div>

          <div className="skill-marketplace-panel">
            <div className="skill-marketplace-panel__header">
              <div>
                <p className="eyebrow">Marketplaces</p>
                <h3>Skill Sources</h3>
              </div>
              <span className="soft-pill">{marketplaces.length} sources</span>
            </div>
            {marketplaceError ? <div className="config-inline-error">{marketplaceError}</div> : null}
            <AdaptiveCardDeck className="skill-marketplace-grid" itemCount={marketplaces.length} minCardWidth={260} idealCardWidth={320} maxCardWidth={380} maxColumns={4}>
              {marketplaces.map((marketplace) => {
                const isBusy = updatingMarketplace === marketplace.id;
                const commandState =
                  marketplace.command_available === null || marketplace.command_available === undefined
                    ? "native"
                    : marketplace.command_available
                      ? "cli ready"
                      : "cli missing";
                return (
                  <div key={marketplace.id} className="skill-marketplace-card">
                    <div className="skill-marketplace-card__top">
                      <div>
                        <h4>{marketplace.name}</h4>
                        <p>{marketplace.id}</p>
                      </div>
                      <span className={`soft-pill ${marketplace.enabled ? "soft-pill--success" : ""}`}>
                        {marketplace.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <div className="skill-marketplace-card__meta">
                      <span>{marketplace.adapter}</span>
                      <span>{commandState}</span>
                      {marketplace.bootstrap_available ? <span>bootstrap</span> : null}
                    </div>
                    <p className="skill-marketplace-card__description">{marketplace.description || "No description."}</p>
                    {(() => {
                      const needsBootstrap = marketplace.enabled && marketplace.command_available === false && Boolean(marketplace.bootstrap_available);
                      const actionLabel = isBusy
                        ? "Working..."
                        : needsBootstrap
                          ? "Install CLI"
                          : marketplace.enabled
                            ? "Disable"
                            : "Enable";
                      const actionClass = marketplace.enabled && !needsBootstrap ? "secondary-button compact-button" : "primary-button compact-button";
                      return (
                        <div className="config-actions-row">
                          <button
                            type="button"
                            className={actionClass}
                            onClick={() => void handleMarketplaceToggle(marketplace)}
                            disabled={saving || isBusy}
                          >
                            {actionLabel}
                          </button>
                          {marketplace.install_url ? (
                            <a className="secondary-button compact-button" href={marketplace.install_url} target="_blank" rel="noreferrer">
                              Install Doc
                            </a>
                          ) : null}
                        </div>
                      );
                    })()}
                  </div>
                  );
                })}
            </AdaptiveCardDeck>
          </div>

          <AdaptiveCardDeck className="config-skill-deck" itemCount={skillRows.length} minCardWidth={280} idealCardWidth={340} maxCardWidth={420} maxColumns={6}>
            {skillRows.length === 0 ? (
              <div className="empty-card">No skills configured yet.</div>
            ) : (
              skillRows.map((skill) => (
                <SkillGalleryCard key={skill.name} name={skill.name} agents={skill.agents} prompt={skill.prompt} />
              ))
            )}
          </AdaptiveCardDeck>
        </div>
      </section>
    );
  }

  if (activeSection === "tools") {
    const totalBindings = toolRows.reduce((total, tool) => total + tool.agents.length, 0);
    const collaborationToolRows = COLLABORATION_TOOL_NAMES.map((name) => (
      toolRows.find((tool) => tool.name === name) ?? { name, agents: [], approvals: 0 }
    ));
    const otherToolRows = toolRows.filter((tool) => !COLLABORATION_TOOL_NAMES.includes(tool.name as (typeof COLLABORATION_TOOL_NAMES)[number]));
    return (
      <section className="panel-grid panel-grid--config panel-grid--config-fluid">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Tool Registry</p>
              <h2>Tool Management</h2>
            </div>
            <span className="soft-pill">{toolRows.length} tools</span>
          </div>

          <div className="config-system-grid" style={{ marginBottom: 20 }}>
            <div className="config-system-row">
              <span>Unique tools</span>
              <strong>{toolRows.length}</strong>
            </div>
            <div className="config-system-row">
              <span>Total bindings</span>
              <strong>{totalBindings}</strong>
            </div>
            <div className="config-system-row">
              <span>Saved approval rules</span>
              <strong>{authorizationRules.length}</strong>
            </div>
            <div className="config-system-row">
              <span>Collaboration tools</span>
              <strong>{COLLABORATION_TOOL_NAMES.length}</strong>
            </div>
            <div className="config-system-row">
              <span>Read-only default</span>
              <strong>{permissionsDraft.allowReadOnlyToolsWithoutApproval ? "Auto allow" : "Approval required"}</strong>
            </div>
          </div>

          <div style={{ marginBottom: 20 }}>
            <div className="panel-card-header" style={{ marginBottom: 12 }}>
              <div>
                <p className="eyebrow">Internal Collaboration</p>
                <h3>Agent-to-Agent Tools</h3>
              </div>
              <span className="soft-pill">{collaborationToolRows.length}</span>
            </div>
            <div className="sidebar-note" style={{ marginBottom: 12 }}>
              Internal collaboration tools can appear here even when no agent currently binds them. That means the tool is registered in the system but not assigned in agent settings.
            </div>
            <AdaptiveCardDeck className="config-skill-deck" itemCount={collaborationToolRows.length} minCardWidth={280} idealCardWidth={340} maxCardWidth={420} maxColumns={6}>
              {collaborationToolRows.map((tool) => (
                <ToolGalleryCard
                  key={tool.name}
                  name={tool.name}
                  agents={tool.agents}
                  approvals={tool.approvals}
                  description={tool.description}
                  riskLevel={tool.riskLevel}
                  approvalKind={tool.approvalKind}
                />
              ))}
            </AdaptiveCardDeck>
          </div>

          <div>
            <div className="panel-card-header" style={{ marginBottom: 12 }}>
              <div>
                <p className="eyebrow">Execution & Data</p>
                <h3>All Other Tools</h3>
              </div>
              <span className="soft-pill">{otherToolRows.length}</span>
            </div>
            <AdaptiveCardDeck className="config-skill-deck" itemCount={otherToolRows.length} minCardWidth={280} idealCardWidth={340} maxCardWidth={420} maxColumns={6}>
              {otherToolRows.length === 0 ? (
                <div className="empty-card">No other tools configured yet.</div>
              ) : (
                otherToolRows.map((tool) => (
                  <ToolGalleryCard
                    key={tool.name}
                    name={tool.name}
                    agents={tool.agents}
                    approvals={tool.approvals}
                    description={tool.description}
                    riskLevel={tool.riskLevel}
                    approvalKind={tool.approvalKind}
                  />
                ))
              )}
            </AdaptiveCardDeck>
          </div>
        </div>
      </section>
    );
  }

  if (activeSection === "memory") {
    return (
      <section className="panel-grid panel-grid--config panel-grid--config-fluid">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Memory Surface</p>
              <h2>Memory Management</h2>
            </div>
            <div className="config-actions-row config-actions-row--header">
              <span className="soft-pill">{memoryRows.length} agents</span>
              <span className="soft-pill">{totalMemoryCount} memories</span>
              <button
                type="button"
                className="secondary-button compact-button"
                onClick={() => void handleRefreshMemories()}
                disabled={memoryLoading}
              >
                {memoryLoading ? "Loading..." : "Refresh"}
              </button>
            </div>
          </div>

          {memoryError ? <div className="config-inline-error">{memoryError}</div> : null}

          <AdaptiveCardDeck className="config-agent-stack" itemCount={memoryRows.length} minCardWidth={280} idealCardWidth={320} maxCardWidth={380} maxColumns={5}>
            {memoryRows.map((agent) => (
              <AgentMemoryCard
                key={agent.type}
                displayName={agent.name}
                agentType={agent.type}
                identity={agent.identity}
                style={agent.style}
                values={agent.values}
                memories={agent.memories}
                memoryCount={agent.memoryCount}
                runtimeMissing={!agent.runtimeAgent}
              />
            ))}
          </AdaptiveCardDeck>
        </div>
      </section>
    );
  }

  if (activeSection === "context") {
    return (
      <section className="panel-grid panel-grid--config panel-grid--config-fluid">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Prompt Budgets</p>
              <h2>Context Selector Profiles</h2>
            </div>
            <div className="config-actions-row config-actions-row--header">
              {editingContext ? (
                <>
                  <button type="submit" form="context-config-form" className="primary-button compact-button" disabled={saving}>
                    {saving ? "Saving..." : "Save"}
                  </button>
                  <button type="button" className="secondary-button compact-button" onClick={cancelEditingContext} disabled={saving}>
                    Cancel
                  </button>
                </>
              ) : (
                <button type="button" className="primary-button compact-button" onClick={startEditingContext} disabled={saving}>
                  Edit
                </button>
              )}
            </div>
          </div>

          {editingContext ? (
            <form id="context-config-form" className="project-form project-form--compact config-form settings-form" onSubmit={handleContextSubmit}>
              <label className="settings-form__field">
                <span>Selector profiles JSON</span>
                <textarea
                  rows={18}
                  value={contextDraft.selectorProfilesJson}
                  onChange={(event) => setContextDraft({ selectorProfilesJson: event.target.value })}
                  spellCheck={false}
                />
              </label>
              {contextDraftError ? <p className="small-note" style={{ color: "var(--danger, #ef4444)" }}>{contextDraftError}</p> : null}
              <p className="small-note">
                Budgets are estimated tokens. `max_tokens_cap` caps runtime context fragments after fixed system/history/current input costs are considered.
              </p>
            </form>
          ) : (
            <>
              <PreviewCard
                title="Context selector"
                subtitle="Runtime fragment budgets used before compaction events are emitted"
                items={contextPreviewItems}
                onActivate={startEditingContext}
              />
              <div className="usage-table" style={{ marginTop: 18 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Profile</th>
                      <th>Max Tokens</th>
                      <th>Fragments</th>
                      <th>Role Budgets</th>
                      <th>Scope Budgets</th>
                    </tr>
                  </thead>
                  <tbody>
                    {contextProfileRows.map(([profileName, profile]) => (
                      <tr key={profileName}>
                        <td>{profileName}</td>
                        <td>{profile.max_tokens_cap ? previewContextWindow(profile.max_tokens_cap) : "--"}</td>
                        <td>{profile.max_fragments ?? "--"}</td>
                        <td>{Object.entries(profile.max_tokens_by_role ?? {}).map(([key, value]) => `${key} ${value}`).join(" / ") || "--"}</td>
                        <td>{Object.entries(profile.max_tokens_by_scope ?? {}).map(([key, value]) => `${key} ${value}`).join(" / ") || "--"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </section>
    );
  }

  if (activeSection === "permissions") {
    return (
      <section className="panel-grid panel-grid--config panel-grid--config-fluid">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Permission Defaults</p>
              <h2>Permissions</h2>
            </div>
            <div className="config-actions-row config-actions-row--header">
              <button
                type="submit"
                form="permissions-config-form"
                className="primary-button compact-button"
                disabled={saving}
              >
                {saving ? "Saving..." : "Save"}
              </button>
              <button
                type="button"
                className="secondary-button compact-button"
                disabled={saving}
                onClick={() => setPermissionsDraft(buildPermissionsDraft(config))}
              >
                Reset
              </button>
            </div>
          </div>

          <form
            id="permissions-config-form"
            className="project-form project-form--compact config-form settings-form"
            onSubmit={(event) => {
              event.preventDefault();
              void onSavePermissions({
                allow_read_only_tools_without_approval: permissionsDraft.allowReadOnlyToolsWithoutApproval,
                auto_approve_all: permissionsDraft.autoApproveAll,
              });
            }}
          >
            <label className="config-toggle-row">
              <input
                type="checkbox"
                checked={permissionsDraft.allowReadOnlyToolsWithoutApproval}
                onChange={(event) =>
                  setPermissionsDraft((current) => ({
                    ...current,
                    allowReadOnlyToolsWithoutApproval: event.target.checked,
                  }))
                }
              />
              <span>Allow low-risk read-only tools without approval</span>
            </label>
            <p className="small-note">
              Applies globally to: <code>read_file</code>, <code>list_files</code>, <code>search_files</code>, <code>list_agents</code>, <code>retrieve_memory</code>.
            </p>
            <label className="config-toggle-row">
              <input
                type="checkbox"
                checked={permissionsDraft.autoApproveAll}
                onChange={(event) =>
                  setPermissionsDraft((current) => ({
                    ...current,
                    autoApproveAll: event.target.checked,
                  }))
                }
              />
              <span>Auto-approve all approval requests</span>
            </label>
            <p className="small-note">
              Applies to tool approval blocks and pipeline manual gates. Saved deny rules still block matching tool calls.
            </p>
          </form>

          <div style={{ marginTop: 16 }}>
            <PreviewCard
              title="Permission policy"
              subtitle="Global default approval rule for low-risk read-only tool calls"
              items={permissionsPreviewItems}
              onActivate={() => undefined}
            />
          </div>

          <div style={{ marginTop: 20 }}>
            <div className="panel-card-header">
              <div>
                <p className="eyebrow">Saved Rules</p>
                <h3>Long-Term Authorization Rules</h3>
              </div>
              <span className="soft-pill">{authorizationRules.length}</span>
            </div>
            {authorizationRules.length === 0 ? (
              <div className="empty-card">No saved authorization rules.</div>
            ) : (
              <div className="task-run-approval-list">
                {authorizationRules.map((rule) => (
                  <div key={rule.id} className="task-run-approval-card task-run-approval-card--neutral">
                    <div className="task-run-approval-card__header">
                      <div>
                        <strong>{rule.tool_name}</strong>
                        <div className="task-run-card__subtitle">
                          {rule.scope} · {rule.decision_kind} · {rule.matcher_type}
                        </div>
                      </div>
                    </div>
                    <div className="task-run-detail__summary">
                      {rule.command_preview || rule.matcher_value || "No matcher preview"}
                    </div>
                    <div className="task-run-card__footer">
                      {rule.project_id ? <span>project #{rule.project_id}</span> : null}
                      {!rule.project_id && rule.chatroom_id ? <span>chat #{rule.chatroom_id}</span> : null}
                      {rule.agent_name ? <span>{rule.agent_name}</span> : null}
                      {rule.updated_at ? <span>{new Date(rule.updated_at).toLocaleString()}</span> : null}
                    </div>
                    <div className="task-run-approval-card__actions">
                      <button
                        type="button"
                        className="secondary-button compact-button"
                        disabled={saving}
                        onClick={() => {
                          void onRevokeAuthorizationRule(rule.id);
                        }}
                      >
                        Revoke
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel-grid panel-grid--config panel-grid--config-fluid">
      <div className="config-top-row">
        <div className={`panel-card panel-card--config-main ${editingGlobal ? "is-editing" : ""}`}>
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Core Uplink</p>
              <h2>Global LLM Config</h2>
            </div>
            <div className="config-header-actions">
              <span className="soft-pill">Default fallback</span>
              <div className="config-actions-row config-actions-row--header">
                {editingGlobal ? (
                  <>
                    <button
                      type="submit"
                      form="global-llm-config-form"
                      className="primary-button compact-button"
                      disabled={saving || !globalBaseUrl.trim() || !globalModel.trim()}
                    >
                      {saving ? "Saving..." : "Save"}
                    </button>
                    <button type="button" className="secondary-button compact-button" onClick={cancelEditingGlobal} disabled={saving}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button type="button" className="primary-button compact-button" onClick={startEditingGlobal} disabled={saving}>
                    Edit
                  </button>
                )}
                <button type="button" className="secondary-button compact-button" onClick={() => void onTestAgentConfig(DEFAULT_AGENT_TYPE)} disabled={saving}>
                  Test
                </button>
                <button type="button" className="secondary-button compact-button" onClick={() => void onReload()} disabled={saving}>
                  Reload
                </button>
                <button type="button" className="secondary-button compact-button" onClick={onBackToChat}>
                  Back
                </button>
              </div>
            </div>
          </div>

          {editingGlobal ? (
            <form id="global-llm-config-form" className="project-form project-form--compact config-form config-form--toolbar settings-form" onSubmit={handleGlobalSubmit}>
              <div className="config-toolbar-row settings-form__row">
                <label className="config-inline-field config-inline-field--wide settings-form__field">
                  <span>Base URL</span>
                  <input value={globalBaseUrl} onChange={(event) => setGlobalBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" />
                </label>
                <label className="config-inline-field settings-form__field">
                  <span>Model</span>
                  <input value={globalModel} onChange={(event) => setGlobalModel(event.target.value)} placeholder="gpt-4.1" />
                </label>
                <label className="config-inline-field settings-form__field">
                  <span>Context Window</span>
                  <input value={globalContextWindow} onChange={(event) => setGlobalContextWindow(event.target.value)} inputMode="numeric" placeholder="400000" />
                </label>
                <label className="config-inline-field settings-form__field">
                  <span>API Key</span>
                  <input value={globalApiKey} onChange={(event) => setGlobalApiKey(event.target.value)} type="password" placeholder="sk-..." />
                </label>
                <label className="config-toggle-row config-toggle-row--toolbar">
                  <input type="checkbox" checked={syncToAllAgents} onChange={(event) => setSyncToAllAgents(event.target.checked)} />
                  <span>Sync to all agents</span>
                </label>
              </div>
            </form>
          ) : (
            <PreviewCard
              title="Global LLM"
              subtitle="Default provider + model fallback for every agent"
              items={globalPreviewItems}
              onActivate={startEditingGlobal}
            />
          )}
        </div>

        <div className="panel-card panel-card--config-side">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Runtime Controls</p>
              <h2>Backend & Scheduler</h2>
            </div>
          </div>
          <div className="config-side-stack">
            <div className="config-side-section">
              <div className="config-side-section__head">
                <div>
                  <p className="eyebrow">System Info</p>
                  <h3>Backend & Feature Flags</h3>
                </div>
              </div>
              <div className="config-system-grid">
                <div className="config-system-row">
                  <span>Backend host</span>
                  <strong>{config?.server?.host || "0.0.0.0"}</strong>
                </div>
                <div className="config-system-row">
                  <span>Backend port</span>
                  <strong>{config?.server?.port || 8000}</strong>
                </div>
                {Object.entries(config?.features ?? {}).map(([featureName, enabled]) => (
                  <div key={featureName} className="config-system-row">
                    <span>{featureName}</span>
                    <strong>{String(enabled)}</strong>
                  </div>
                ))}
              </div>
            </div>

            <div className="config-side-section">
              <div className="config-side-section__head">
                <div>
                  <p className="eyebrow">Runtime Policy</p>
                  <h3>Orchestration Scheduler</h3>
                </div>
                <div className="config-actions-row">
                  {editingOrchestration ? (
                    <>
                      <button
                        type="submit"
                        form="orchestration-config-form"
                        className="primary-button compact-button"
                        disabled={saving}
                      >
                        {saving ? "Saving..." : "Save"}
                      </button>
                      <button
                        type="button"
                        className="secondary-button compact-button"
                        onClick={cancelEditingOrchestration}
                        disabled={saving}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="primary-button compact-button"
                      onClick={startEditingOrchestration}
                      disabled={saving}
                    >
                      Edit
                    </button>
                  )}
                </div>
              </div>

              {editingOrchestration ? (
                <form id="orchestration-config-form" className="project-form project-form--compact config-form" onSubmit={handleOrchestrationSubmit}>
                  <label className="settings-form__field">
                    <span>Sidecar agent types</span>
                    <textarea
                      rows={4}
                      value={orchestrationDraft.sidecarAgentTypes}
                      onChange={(event) =>
                        setOrchestrationDraft((current) => ({
                          ...current,
                          sidecarAgentTypes: event.target.value,
                        }))
                      }
                      placeholder={"tester\nreviewer"}
                    />
                  </label>
                  <p className="small-note">
                    One agent type per line. Leave empty to disable sidecars and keep a fully blocking chain.
                  </p>
                </form>
              ) : (
                <PreviewCard
                  title="Scheduler policy"
                  subtitle="Controls which agent types attach as sidecars instead of blocking the main chain"
                  items={orchestrationPreviewItems}
                  onActivate={startEditingOrchestration}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="panel-card panel-card--full panel-card--config-agents">
        <div className="panel-card-header">
          <div>
            <p className="eyebrow">Global Agent Control</p>
            <h2>All Agent Settings</h2>
          </div>
          <span className="soft-pill">LLM + role + soul + tools + skills</span>
        </div>

        <AdaptiveCardDeck
          className="config-agent-stack"
          itemCount={agentEntries.length}
          minCardWidth={hasEditingAgent ? 760 : 260}
          idealCardWidth={hasEditingAgent ? 920 : 300}
          maxCardWidth={hasEditingAgent ? 1280 : 360}
          maxColumns={hasEditingAgent ? 1 : 6}
        >
          {agentEntries.map(([agentType, agentConfig]) => {
            const effective = agentConfigs[agentType];
            const draft = agentDrafts[agentType] ?? buildAgentDraft(agentConfig, effective);
            const displayName = agentConfig.name?.trim() || defaultAgentName(agentType);
            const isEditing = Boolean(editingAgents[agentType]);
            const configuredContextWindow =
              agentConfig.provider?.models?.find((model) => model.id === agentConfig.default_model)?.contextWindow
              ?? agentConfig.provider?.models?.[0]?.contextWindow;

            return (
              isEditing ? (
                <div key={agentType} className="config-agent-card is-editing adaptive-card-deck__item--editing">
                  <div className="config-agent-card__header">
                    <div>
                      <h3>{displayName}</h3>
                      <p className="config-agent-card__eyebrow">@{agentType}</p>
                      <p>
                        Source: <strong>{effective?.source || "global"}</strong>
                        {effective?.model ? ` · ${effective.model}` : ""}
                      </p>
                    </div>
                    <div className="config-agent-card__actions config-actions-row">
                      <button type="button" className="primary-button compact-button" onClick={() => void handleSaveAgentDraft(agentType)} disabled={saving}>
                        Save
                      </button>
                      <button type="button" className="secondary-button compact-button" onClick={() => cancelEditingAgent(agentType)} disabled={saving}>
                        Cancel
                      </button>
                      <button type="button" className="secondary-button compact-button" onClick={() => void handleClearAgent(agentType)} disabled={saving}>
                        Use Global
                      </button>
                      <button type="button" className="secondary-button compact-button" onClick={() => void onTestAgentConfig(agentType)} disabled={saving}>
                        Test
                      </button>
                    </div>
                  </div>
                  <div className="config-agent-card__grid">
                    <label>
                      <span>Base URL</span>
                      <input value={draft.baseUrl} onChange={(event) => updateAgentDraft(agentType, { baseUrl: event.target.value })} placeholder="(inherits global)" />
                    </label>
                    <label>
                      <span>Model</span>
                      <input value={draft.model} onChange={(event) => updateAgentDraft(agentType, { model: event.target.value })} placeholder="(inherits global)" />
                    </label>
                    <label>
                      <span>Context Window</span>
                      <input value={draft.contextWindow} onChange={(event) => updateAgentDraft(agentType, { contextWindow: event.target.value })} inputMode="numeric" placeholder="400000" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>API Key</span>
                      <input value={draft.apiKey} onChange={(event) => updateAgentDraft(agentType, { apiKey: event.target.value })} type="password" placeholder="(inherits global)" />
                    </label>
                    <label>
                      <span>Role Title</span>
                      <input value={draft.roleTitle} onChange={(event) => updateAgentDraft(agentType, { roleTitle: event.target.value })} placeholder="e.g. Architect" />
                    </label>
                    <label>
                      <span>SOUL Identity</span>
                      <input value={draft.soulIdentity} onChange={(event) => updateAgentDraft(agentType, { soulIdentity: event.target.value })} placeholder="Who this agent is" />
                    </label>
                    <label>
                      <span>SOUL Style</span>
                      <input value={draft.soulStyle} onChange={(event) => updateAgentDraft(agentType, { soulStyle: event.target.value })} placeholder="Communication style" />
                    </label>
                    <label>
                      <span>SOUL Quirks</span>
                      <input value={draft.soulQuirks} onChange={(event) => updateAgentDraft(agentType, { soulQuirks: event.target.value })} placeholder="Optional quirks" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>Responsibilities</span>
                      <textarea value={draft.responsibilities} onChange={(event) => updateAgentDraft(agentType, { responsibilities: event.target.value })} rows={4} placeholder="One responsibility per line" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>Rules</span>
                      <textarea value={draft.rules} onChange={(event) => updateAgentDraft(agentType, { rules: event.target.value })} rows={4} placeholder="One rule per line" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>SOUL Values</span>
                      <textarea value={draft.soulValues} onChange={(event) => updateAgentDraft(agentType, { soulValues: event.target.value })} rows={3} placeholder="One value per line" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>Tools</span>
                      <textarea value={draft.tools} onChange={(event) => updateAgentDraft(agentType, { tools: event.target.value })} rows={3} placeholder="One tool per line" />
                    </label>
                    <label className="config-agent-card__key">
                      <span>Skills</span>
                      <textarea value={draft.skills} onChange={(event) => updateAgentDraft(agentType, { skills: event.target.value })} rows={3} placeholder="One skill per line" />
                    </label>
                  </div>
                </div>
              ) : (
                <AgentGalleryCard
                  key={agentType}
                  displayName={displayName}
                  agentType={agentType}
                  sourceLabel={effective?.source || "global"}
                  modelLabel={previewText(agentConfig.default_model || effective?.model, "Inherit global")}
                  contextWindowLabel={previewContextWindow(configuredContextWindow, effective?.source === "global" ? "Inherit global" : "Not set")}
                  roleTitle={previewText(agentConfig.role?.title, "Not configured")}
                  identity={previewText(agentConfig.soul?.identity, "Not configured")}
                  style={previewText(agentConfig.soul?.style, "Not configured")}
                  responsibilities={agentConfig.role?.responsibilities ?? []}
                  rules={agentConfig.role?.rules ?? []}
                  tools={agentConfig.tools ?? []}
                  skills={agentConfig.skills ?? []}
                  onEdit={() => startEditingAgent(agentType)}
                  onTest={() => void onTestAgentConfig(agentType)}
                  disabled={saving}
                />
              )
            );
          })}
        </AdaptiveCardDeck>
      </div>
    </section>
  );
}
