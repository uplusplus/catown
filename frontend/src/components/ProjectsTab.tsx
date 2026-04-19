import { FormEvent, useMemo, useState } from "react";

import type { AgentInfo, ProjectSummary } from "../types";

type ProjectsTabProps = {
  projects: ProjectSummary[];
  agents: AgentInfo[];
  selectedProjectId: number | null;
  creating: boolean;
  onCreateProject: (payload: { name: string; description: string; agent_names: string[] }) => Promise<void>;
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
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedAgents, setSelectedAgents] = useState<string[]>(["assistant"]);

  const activeAgents = useMemo(() => agents.filter((agent) => agent.is_active), [agents]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const projectName = name.trim();
    if (!projectName || selectedAgents.length === 0 || creating) return;

    await onCreateProject({
      name: projectName,
      description: description.trim(),
      agent_names: selectedAgents,
    });

    setName("");
    setDescription("");
  }

  function toggleAgent(agentName: string) {
    setSelectedAgents((current) =>
      current.includes(agentName)
        ? current.filter((value) => value !== agentName)
        : [...current, agentName],
    );
  }

  return (
    <section className="panel-grid">
      <div className="panel-card">
        <div className="panel-card-header">
          <div>
            <p className="eyebrow">Session Launcher</p>
            <h2>Create Project</h2>
          </div>
          <span className="soft-pill">{projects.length} total</span>
        </div>

        <form className="project-form" onSubmit={handleSubmit}>
          <label>
            <span>Name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="New mission name" />
          </label>
          <label>
            <span>Description</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Short project brief"
              rows={4}
            />
          </label>
          <div>
            <span className="field-label">Agents</span>
            <div className="agent-selector-grid">
              {activeAgents.map((agent) => (
                <button
                  key={agent.id}
                  type="button"
                  className={`agent-select-chip ${selectedAgents.includes(agent.name) ? "is-selected" : ""}`}
                  onClick={() => toggleAgent(agent.name)}
                >
                  <strong>{agent.name}</strong>
                  <span>{agent.role}</span>
                </button>
              ))}
            </div>
          </div>
          <button type="submit" className="primary-button" disabled={creating}>
            {creating ? "Creating..." : "Create Project"}
          </button>
        </form>
      </div>

      <div className="panel-card">
        <div className="panel-card-header">
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
