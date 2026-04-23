import { FormEvent, useMemo, useState } from "react";

import { FormSuggestionStrip } from "./FormSuggestionStrip";
import type { AgentInfo, GitHubProjectImportPayload, ProjectSummary } from "../types";
import { DEFAULT_AGENT_TYPE, getAgentDisplayName, getAgentType } from "../utils/agents";
import {
  filterAgentSetSuggestions,
  filterTextSuggestions,
  readProjectFormSuggestionStore,
  rememberGitHubProjectSuggestion,
} from "../utils/projectFormSuggestions";

type ProjectsTabProps = {
  projects: ProjectSummary[];
  agents: AgentInfo[];
  selectedProjectId: number | null;
  creating: boolean;
  onCreateProject: (payload: GitHubProjectImportPayload) => Promise<void>;
  onSelectProject: (projectId: number) => void;
};

export function ProjectsTab({
  projects,
  agents,
  selectedProjectId,
  creating,
  onCreateProject,
  onSelectProject,
}: ProjectsTabProps) {
  const [repoUrl, setRepoUrl] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [ref, setRef] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>([DEFAULT_AGENT_TYPE]);
  const [suggestionStore, setSuggestionStore] = useState(() => readProjectFormSuggestionStore());

  const activeAgents = useMemo(() => agents.filter((agent) => agent.is_active), [agents]);
  const repoSuggestions = useMemo(
    () =>
      filterTextSuggestions(suggestionStore.github.repoUrls, repoUrl).map((value) => ({
        key: `repo-${value}`,
        label: value,
        value,
        title: value,
        monospace: true,
      })),
    [repoUrl, suggestionStore.github.repoUrls],
  );
  const nameSuggestions = useMemo(
    () =>
      filterTextSuggestions(suggestionStore.github.names, name).map((value) => ({
        key: `name-${value}`,
        label: value,
        value,
        title: value,
      })),
    [name, suggestionStore.github.names],
  );
  const refSuggestions = useMemo(
    () =>
      filterTextSuggestions(suggestionStore.github.refs, ref).map((value) => ({
        key: `ref-${value}`,
        label: value,
        value,
        title: value,
        monospace: true,
      })),
    [ref, suggestionStore.github.refs],
  );
  const descriptionSuggestions = useMemo(
    () =>
      filterTextSuggestions(suggestionStore.github.descriptions, description, 3).map((value) => ({
        key: `description-${value}`,
        label: value.length > 48 ? `${value.slice(0, 48)}...` : value,
        value,
        title: value,
      })),
    [description, suggestionStore.github.descriptions],
  );
  const agentPresetSuggestions = useMemo(
    () =>
      filterAgentSetSuggestions(suggestionStore.github.agentSets, selectedAgents).map((agentTypes) => ({
        key: `agents-${agentTypes.join("-")}`,
        label: agentTypes
          .map((agentType) => getAgentDisplayName(activeAgents.find((agent) => getAgentType(agent) === agentType) ?? null))
          .join(" + "),
        value: JSON.stringify(agentTypes),
        title: agentTypes.map((agentType) => `@${agentType}`).join(", "),
      })),
    [activeAgents, selectedAgents, suggestionStore.github.agentSets],
  );

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const repo = repoUrl.trim();
    if (!repo || selectedAgents.length === 0 || creating) return;

    const payload: GitHubProjectImportPayload = {
      repo_url: repo,
      name: name.trim() || undefined,
      description: description.trim(),
      ref: ref.trim() || undefined,
      agent_names: selectedAgents,
    };

    await onCreateProject(payload);
    setSuggestionStore((current) => rememberGitHubProjectSuggestion(current, payload));

    setRepoUrl("");
    setName("");
    setDescription("");
    setRef("");
  }

  function toggleAgent(agentName: string) {
    setSelectedAgents((current) =>
      current.includes(agentName)
        ? current.filter((value) => value !== agentName)
        : [...current, agentName],
    );
  }

  return (
    <section className="panel-grid panel-grid--projects">
      <div className="panel-card panel-card--compact">
        <div className="panel-card-header panel-card-header--compact">
          <div>
            <p className="eyebrow">Repository Import</p>
            <h2>Import from GitHub</h2>
          </div>
          <span className="soft-pill">{projects.length} total</span>
        </div>

        <form className="project-form project-form--compact settings-form" onSubmit={handleSubmit}>
          <label className="settings-form__field settings-form__field--full">
            <span>GitHub Repo</span>
            <input
              value={repoUrl}
              onChange={(event) => setRepoUrl(event.target.value)}
              placeholder="owner/repo or https://github.com/owner/repo"
            />
            <FormSuggestionStrip label="Recent" items={repoSuggestions} onSelect={setRepoUrl} />
          </label>
          <div className="project-form__row settings-form__row">
            <label className="settings-form__field">
              <span>Project Name</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Optional override"
              />
              <FormSuggestionStrip label="Recent" items={nameSuggestions} onSelect={setName} />
            </label>
            <label className="settings-form__field">
              <span>Git Ref</span>
              <input
                value={ref}
                onChange={(event) => setRef(event.target.value)}
                placeholder="Branch / tag / commit"
              />
              <FormSuggestionStrip label="Recent" items={refSuggestions} onSelect={setRef} />
            </label>
          </div>
          <label className="settings-form__field settings-form__field--full">
            <span>Description</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Short project brief"
              rows={3}
            />
            <FormSuggestionStrip label="Recent" items={descriptionSuggestions} onSelect={setDescription} />
          </label>
          <div className="project-form__section settings-form__field settings-form__field--full">
            <span className="field-label">Agents</span>
            <div className="agent-selector-grid settings-chip-row">
              {activeAgents.map((agent) => (
                <button
                  key={agent.id}
                  type="button"
                  className={`agent-select-chip ${selectedAgents.includes(getAgentType(agent)) ? "is-selected" : ""}`}
                  onClick={() => toggleAgent(getAgentType(agent))}
                >
                  <strong>{getAgentDisplayName(agent)}</strong>
                  <small>@{getAgentType(agent)}</small>
                  <span>{agent.role}</span>
                </button>
              ))}
            </div>
            <FormSuggestionStrip
              label="Preset"
              items={agentPresetSuggestions}
              onSelect={(value) => {
                try {
                  const parsed = JSON.parse(value) as string[];
                  setSelectedAgents(parsed);
                } catch {
                  // Ignore malformed local suggestion payloads.
                }
              }}
            />
          </div>
          <div className="project-form__footer settings-form__footer">
            <p className="project-form__hint">GitHub-first workflow. Catown clones the repo into a managed workspace.</p>
            <button type="submit" className="primary-button" disabled={creating}>
              {creating ? "Importing..." : "Import from GitHub"}
            </button>
          </div>
        </form>
      </div>

      <div className="panel-card panel-card--compact">
        <div className="panel-card-header panel-card-header--compact">
          <div>
            <p className="eyebrow">Room Switcher</p>
            <h2>Existing Sessions</h2>
          </div>
        </div>
        <div className="project-list">
          {projects.length === 0 ? (
            <div className="empty-card">No projects yet.</div>
          ) : (
            projects.map((project) => (
              <button
                key={project.id}
                type="button"
                className={`project-card ${selectedProjectId === project.id ? "is-active" : ""}`}
                onClick={() => onSelectProject(project.id)}
              >
                <strong>{project.name}</strong>
                <p>{project.description || "No description yet."}</p>
                <div className="project-card-footer">
                  <span>{project.status}</span>
                  <span>{project.agents.length} agents</span>
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
