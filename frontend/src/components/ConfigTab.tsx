import { FormEvent, useEffect, useMemo, useState } from "react";

import type { ConfigAgentDefinition, ConfigResponse } from "../types";

type ConfigTabProps = {
  config: ConfigResponse | null;
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
  onReload: () => Promise<void>;
  onTestAgentConfig: (agentName: string) => Promise<void>;
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

function buildAgentDraft(agentConfig: ConfigAgentDefinition | undefined, effective: ConfigResponse["agent_llm_configs"][string] | undefined): AgentDraft {
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

export function ConfigTab({
  config,
  saving,
  onBackToChat,
  onSaveGlobal,
  onSaveAgent,
  onReload,
  onTestAgentConfig,
}: ConfigTabProps) {
  const [globalBaseUrl, setGlobalBaseUrl] = useState("");
  const [globalApiKey, setGlobalApiKey] = useState("");
  const [globalModel, setGlobalModel] = useState("");
  const [syncToAllAgents, setSyncToAllAgents] = useState(true);
  const [agentDrafts, setAgentDrafts] = useState<Record<string, AgentDraft>>({});

  const agentEntries = useMemo(() => Object.entries(config?.agents ?? {}), [config]);
  const agentConfigs = config?.agent_llm_configs ?? {};

  useEffect(() => {
    const provider = config?.global_llm?.provider;
    setGlobalBaseUrl(provider?.baseUrl ?? "");
    setGlobalApiKey(provider?.apiKey ?? "");
    setGlobalModel(config?.global_llm?.default_model ?? provider?.models?.[0]?.id ?? "");
  }, [config]);

  useEffect(() => {
    const nextDrafts: Record<string, AgentDraft> = {};
    for (const [agentName, agentConfig] of agentEntries) {
      nextDrafts[agentName] = buildAgentDraft(agentConfig, agentConfigs[agentName]);
    }
    setAgentDrafts(nextDrafts);
  }, [agentEntries, agentConfigs]);

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
  }

  return (
    <section className="panel-grid panel-grid--config">
      <div className="panel-card panel-card--full">
        <div className="panel-card-header">
          <div>
            <p className="eyebrow">Core Uplink</p>
            <h2>Global LLM Config</h2>
          </div>
          <div className="config-header-actions">
            <span className="soft-pill">Default fallback</span>
            <div className="config-actions-row config-actions-row--header">
              <button type="submit" form="global-llm-config-form" className="primary-button compact-button" disabled={saving || !globalBaseUrl.trim() || !globalModel.trim()}>
                {saving ? "Saving..." : "Save"}
              </button>
              <button type="button" className="secondary-button compact-button" onClick={() => void onTestAgentConfig("assistant")} disabled={saving}>
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

        <form id="global-llm-config-form" className="project-form config-form config-form--toolbar" onSubmit={handleGlobalSubmit}>
          <div className="config-toolbar-row">
            <label className="config-inline-field config-inline-field--wide">
              <span>Base URL</span>
              <input value={globalBaseUrl} onChange={(event) => setGlobalBaseUrl(event.target.value)} placeholder="https://api.openai.com/v1" />
            </label>
            <label className="config-inline-field">
              <span>Model</span>
              <input value={globalModel} onChange={(event) => setGlobalModel(event.target.value)} placeholder="gpt-4.1" />
            </label>
            <label className="config-inline-field">
              <span>API Key</span>
              <input value={globalApiKey} onChange={(event) => setGlobalApiKey(event.target.value)} type="password" placeholder="sk-..." />
            </label>
            <label className="config-toggle-row config-toggle-row--toolbar">
              <input type="checkbox" checked={syncToAllAgents} onChange={(event) => setSyncToAllAgents(event.target.checked)} />
              <span>Sync to all agents</span>
            </label>
          </div>
        </form>
      </div>

      <div className="panel-card panel-card--full">
        <div className="panel-card-header">
          <div>
            <p className="eyebrow">Global Agent Control</p>
            <h2>All Agent Settings</h2>
          </div>
          <span className="soft-pill">LLM + role + soul + tools + skills</span>
        </div>

        <div className="config-agent-stack">
          {agentEntries.map(([agentName, agentConfig]) => {
            const effective = agentConfigs[agentName];
            const draft = agentDrafts[agentName] ?? buildAgentDraft(agentConfig, effective);

            return (
              <div key={agentName} className="config-agent-card">
                <div className="config-agent-card__header">
                  <div>
                    <h3>{agentName}</h3>
                    <p>
                      Source: <strong>{effective?.source || "global"}</strong>
                      {effective?.model ? ` · ${effective.model}` : ""}
                    </p>
                  </div>
                  <div className="config-agent-card__actions">
                    <button type="button" className="primary-button compact-button" onClick={() => void handleSaveAgentDraft(agentName)} disabled={saving}>
                      Save
                    </button>
                    <button type="button" className="secondary-button compact-button" onClick={() => void handleClearAgent(agentName)} disabled={saving}>
                      Use Global
                    </button>
                    <button type="button" className="secondary-button compact-button" onClick={() => void onTestAgentConfig(agentName)} disabled={saving}>
                      Test
                    </button>
                  </div>
                </div>

                <div className="config-agent-card__grid">
                  <label>
                    <span>Base URL</span>
                    <input value={draft.baseUrl} onChange={(event) => updateAgentDraft(agentName, { baseUrl: event.target.value })} placeholder="(inherits global)" />
                  </label>
                  <label>
                    <span>Model</span>
                    <input value={draft.model} onChange={(event) => updateAgentDraft(agentName, { model: event.target.value })} placeholder="(inherits global)" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>API Key</span>
                    <input value={draft.apiKey} onChange={(event) => updateAgentDraft(agentName, { apiKey: event.target.value })} type="password" placeholder="(inherits global)" />
                  </label>
                  <label>
                    <span>Role Title</span>
                    <input value={draft.roleTitle} onChange={(event) => updateAgentDraft(agentName, { roleTitle: event.target.value })} placeholder="e.g. Architect" />
                  </label>
                  <label>
                    <span>SOUL Identity</span>
                    <input value={draft.soulIdentity} onChange={(event) => updateAgentDraft(agentName, { soulIdentity: event.target.value })} placeholder="Who this agent is" />
                  </label>
                  <label>
                    <span>SOUL Style</span>
                    <input value={draft.soulStyle} onChange={(event) => updateAgentDraft(agentName, { soulStyle: event.target.value })} placeholder="Communication style" />
                  </label>
                  <label>
                    <span>SOUL Quirks</span>
                    <input value={draft.soulQuirks} onChange={(event) => updateAgentDraft(agentName, { soulQuirks: event.target.value })} placeholder="Optional quirks" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>Responsibilities</span>
                    <textarea value={draft.responsibilities} onChange={(event) => updateAgentDraft(agentName, { responsibilities: event.target.value })} rows={4} placeholder="One responsibility per line" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>Rules</span>
                    <textarea value={draft.rules} onChange={(event) => updateAgentDraft(agentName, { rules: event.target.value })} rows={4} placeholder="One rule per line" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>SOUL Values</span>
                    <textarea value={draft.soulValues} onChange={(event) => updateAgentDraft(agentName, { soulValues: event.target.value })} rows={3} placeholder="One value per line" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>Tools</span>
                    <textarea value={draft.tools} onChange={(event) => updateAgentDraft(agentName, { tools: event.target.value })} rows={3} placeholder="One tool per line" />
                  </label>
                  <label className="config-agent-card__key">
                    <span>Skills</span>
                    <textarea value={draft.skills} onChange={(event) => updateAgentDraft(agentName, { skills: event.target.value })} rows={3} placeholder="One skill per line" />
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="panel-card panel-card--full">
        <div className="panel-card-header">
          <div>
            <p className="eyebrow">System Info</p>
            <h2>Backend & Feature Flags</h2>
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
    </section>
  );
}
