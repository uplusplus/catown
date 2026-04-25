import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { ConfigAgentDefinition, ConfigResponse, ConfigSection, SkillMarketplace } from "../types";
import { DEFAULT_AGENT_TYPE, defaultAgentName } from "../utils/agents";

type ConfigTabProps = {
  config: ConfigResponse | null;
  activeSection: ConfigSection;
  saving: boolean;
  onBackToChat: () => void;
  onSaveGlobal: (payload: {
    provider: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
    default_model: string;
  }) => Promise<void>;
  onSaveAgent: (
    agentName: string,
    payload: {
      provider?: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
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
  onReload: () => Promise<void>;
  onTestAgentConfig: (agentName: string) => Promise<void>;
};

type GlobalDraft = {
  baseUrl: string;
  apiKey: string;
  model: string;
};

type OrchestrationDraft = {
  sidecarAgentTypes: string;
};

type AgentDraft = {
  baseUrl: string;
  apiKey: string;
  model: string;
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

function buildGlobalDraft(config: ConfigResponse | null): GlobalDraft {
  const provider = config?.global_llm?.provider;
  return {
    baseUrl: provider?.baseUrl ?? "",
    apiKey: provider?.apiKey ?? "",
    model: config?.global_llm?.default_model ?? provider?.models?.[0]?.id ?? "",
  };
}

function buildOrchestrationDraft(config: ConfigResponse | null): OrchestrationDraft {
  return {
    sidecarAgentTypes: (config?.orchestration?.sidecar_agent_types ?? []).join("\n"),
  };
}

function buildAgentDraft(
  agentConfig: ConfigAgentDefinition | undefined,
  effective: ConfigResponse["agent_llm_configs"][string] | undefined,
): AgentDraft {
  return {
    baseUrl: agentConfig?.provider?.baseUrl ?? "",
    apiKey: agentConfig?.provider?.apiKey ?? "",
    model: agentConfig?.default_model ?? effective?.model ?? "",
    roleTitle: agentConfig?.role?.title ?? "",
    responsibilities: (agentConfig?.role?.responsibilities ?? []).join("\n"),
    rules: (agentConfig?.role?.rules ?? []).join("\n"),
    soulIdentity: agentConfig?.soul?.identity ?? "",
    soulStyle: agentConfig?.soul?.style ?? "",
    soulValues: (agentConfig?.soul?.values ?? []).join("\n"),
    soulQuirks: agentConfig?.soul?.quirks ?? "",
    tools: (agentConfig?.tools ?? []).join("\n"),
    skills: (agentConfig?.skills ?? []).join("\n"),
  };
}

function readMultilineList(value: string) {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
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

export function ConfigTab({
  config,
  activeSection,
  saving,
  onBackToChat,
  onSaveGlobal,
  onSaveOrchestration,
  onSaveAgent,
  onReload,
  onTestAgentConfig,
}: ConfigTabProps) {
  const [globalBaseUrl, setGlobalBaseUrl] = useState("");
  const [globalApiKey, setGlobalApiKey] = useState("");
  const [globalModel, setGlobalModel] = useState("");
  const [orchestrationDraft, setOrchestrationDraft] = useState<OrchestrationDraft>(() => buildOrchestrationDraft(config));
  const [syncToAllAgents, setSyncToAllAgents] = useState(true);
  const [agentDrafts, setAgentDrafts] = useState<Record<string, AgentDraft>>({});
  const [editingGlobal, setEditingGlobal] = useState(false);
  const [editingOrchestration, setEditingOrchestration] = useState(false);
  const [editingAgents, setEditingAgents] = useState<Record<string, boolean>>({});
  const [marketplaces, setMarketplaces] = useState<SkillMarketplace[]>([]);
  const [marketplaceError, setMarketplaceError] = useState("");
  const [updatingMarketplace, setUpdatingMarketplace] = useState<string | null>(null);

  const agentEntries = useMemo(() => Object.entries(config?.agents ?? {}), [config]);
  const agentConfigs = config?.agent_llm_configs ?? {};
  const skillRows = useMemo(() => {
    const rows = new Map<string, string[]>();
    for (const [agentName, agentConfig] of agentEntries) {
      for (const skill of agentConfig.skills ?? []) {
        const normalized = skill.trim();
        if (!normalized) continue;
        rows.set(normalized, [...(rows.get(normalized) ?? []), agentConfig.name?.trim() || defaultAgentName(agentName)]);
      }
    }
    return Array.from(rows.entries())
      .map(([name, agents]) => ({ name, agents }))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [agentEntries]);
  const memoryRows = useMemo(
    () =>
      agentEntries.map(([agentName, agentConfig]) => ({
        name: agentConfig.name?.trim() || defaultAgentName(agentName),
        type: agentName,
        identity: previewText(agentConfig.soul?.identity, "Not configured"),
        values: agentConfig.soul?.values ?? [],
        style: previewText(agentConfig.soul?.style, "Not configured"),
      })),
    [agentEntries],
  );

  useEffect(() => {
    const draft = buildGlobalDraft(config);
    setGlobalBaseUrl(draft.baseUrl);
    setGlobalApiKey(draft.apiKey);
    setGlobalModel(draft.model);
  }, [config]);

  useEffect(() => {
    setOrchestrationDraft(buildOrchestrationDraft(config));
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

  function resetGlobalDraft() {
    const draft = buildGlobalDraft(config);
    setGlobalBaseUrl(draft.baseUrl);
    setGlobalApiKey(draft.apiKey);
    setGlobalModel(draft.model);
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
        models: [{ id: globalModel.trim(), name: globalModel.trim() }],
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
        models: [{ id: draft.model.trim(), name: draft.model.trim() }],
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
      tools: readMultilineList(draft.tools),
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

  const globalPreviewItems = useMemo(
    () => [
      { label: "Base URL", value: previewText(config?.global_llm?.provider?.baseUrl, "Not set") },
      { label: "Model", value: previewText(config?.global_llm?.default_model, "Not set") },
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

  if (activeSection === "skills") {
    return (
      <section className="panel-grid panel-grid--config">
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
            <div className="skill-marketplace-grid">
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
            </div>
          </div>

          <div className="config-agent-stack">
            {skillRows.length === 0 ? (
              <div className="empty-card">No skills configured yet.</div>
            ) : (
              skillRows.map((skill) => (
                <div key={skill.name} className="config-agent-card">
                  <div className="config-agent-card__header">
                    <div>
                      <h3>{skill.name}</h3>
                      <p className="config-agent-card__eyebrow">{skill.agents.length} agent bindings</p>
                    </div>
                    <span className="soft-pill">{skill.agents.length}</span>
                  </div>
                  <PreviewCard
                    title={skill.name}
                    subtitle="Agents currently configured to use this skill"
                    items={skill.agents.map((agent) => ({ label: "Agent", value: agent }))}
                    onActivate={() => undefined}
                  />
                </div>
              ))
            )}
          </div>
        </div>
      </section>
    );
  }

  if (activeSection === "memory") {
    return (
      <section className="panel-grid panel-grid--config">
        <div className="panel-card panel-card--full">
          <div className="panel-card-header">
            <div>
              <p className="eyebrow">Memory Surface</p>
              <h2>Memory Management</h2>
            </div>
            <span className="soft-pill">{memoryRows.length} agents</span>
          </div>

          <div className="config-agent-stack">
            {memoryRows.map((agent) => (
              <div key={agent.type} className="config-agent-card">
                <div className="config-agent-card__header">
                  <div>
                    <h3>{agent.name}</h3>
                    <p className="config-agent-card__eyebrow">@{agent.type}</p>
                  </div>
                </div>
                <PreviewCard
                  title={`${agent.name} Memory`}
                  subtitle="Configured identity context used as retained agent memory"
                  items={[
                    { label: "Identity", value: agent.identity },
                    { label: "Style", value: agent.style },
                    { label: "Values", value: previewList(agent.values, "Not configured") },
                  ]}
                  onActivate={() => undefined}
                />
              </div>
            ))}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="panel-grid panel-grid--config">
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

        <div className="config-agent-stack">
          {agentEntries.map(([agentType, agentConfig]) => {
            const effective = agentConfigs[agentType];
            const draft = agentDrafts[agentType] ?? buildAgentDraft(agentConfig, effective);
            const displayName = agentConfig.name?.trim() || defaultAgentName(agentType);
            const isEditing = Boolean(editingAgents[agentType]);
            const previewItems = [
              { label: "Base URL", value: previewText(agentConfig.provider?.baseUrl || effective?.baseUrl, "Inherit global") },
              { label: "Model", value: previewText(agentConfig.default_model || effective?.model, "Inherit global") },
              {
                label: "API Key",
                value: agentConfig.provider?.apiKey?.trim()
                  ? previewSecret(agentConfig.provider.apiKey, "Inherit global")
                  : effective?.hasApiKey
                    ? "Inherit global"
                    : "Not set",
              },
              { label: "Role", value: previewText(agentConfig.role?.title, "Not configured") },
              { label: "Identity", value: previewText(agentConfig.soul?.identity, "Not configured") },
              { label: "Style", value: previewText(agentConfig.soul?.style, "Not configured") },
              { label: "Responsibilities", value: previewList(agentConfig.role?.responsibilities, "Not configured") },
              { label: "Rules", value: previewList(agentConfig.role?.rules, "Not configured") },
              { label: "Values", value: previewList(agentConfig.soul?.values, "Not configured") },
              { label: "Tools", value: previewList(agentConfig.tools, "None") },
              { label: "Skills", value: previewList(agentConfig.skills, "None") },
            ];

            return (
              <div key={agentType} className={`config-agent-card ${isEditing ? "is-editing" : ""}`}>
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
                    {isEditing ? (
                      <>
                        <button type="button" className="primary-button compact-button" onClick={() => void handleSaveAgentDraft(agentType)} disabled={saving}>
                          Save
                        </button>
                        <button type="button" className="secondary-button compact-button" onClick={() => cancelEditingAgent(agentType)} disabled={saving}>
                          Cancel
                        </button>
                        <button type="button" className="secondary-button compact-button" onClick={() => void handleClearAgent(agentType)} disabled={saving}>
                          Use Global
                        </button>
                      </>
                    ) : (
                      <button type="button" className="primary-button compact-button" onClick={() => startEditingAgent(agentType)} disabled={saving}>
                        Edit
                      </button>
                    )}
                    <button type="button" className="secondary-button compact-button" onClick={() => void onTestAgentConfig(agentType)} disabled={saving}>
                      Test
                    </button>
                  </div>
                </div>

                {isEditing ? (
                  <div className="config-agent-card__grid">
                    <label>
                      <span>Base URL</span>
                      <input value={draft.baseUrl} onChange={(event) => updateAgentDraft(agentType, { baseUrl: event.target.value })} placeholder="(inherits global)" />
                    </label>
                    <label>
                      <span>Model</span>
                      <input value={draft.model} onChange={(event) => updateAgentDraft(agentType, { model: event.target.value })} placeholder="(inherits global)" />
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
                ) : (
                  <PreviewCard
                    title={`${displayName} Settings`}
                    subtitle="Click anywhere on this card body to start editing"
                    items={previewItems}
                    onActivate={() => startEditingAgent(agentType)}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
