import { useState, type FormEvent } from 'react';

import type { Project } from '../types';
import { formatRelative, titleize } from '../lib/format';

type Props = {
  projects: Project[];
  selectedProjectId: number | null;
  onSelect: (projectId: number) => void;
  onCreate: (payload: { name: string; one_line_vision?: string }) => Promise<void>;
  creating?: boolean;
};

export function ProjectRail({ projects, selectedProjectId, onSelect, onCreate, creating = false }: Props) {
  const [name, setName] = useState('');
  const [vision, setVision] = useState('');

  async function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedVision = vision.trim();
    if (!trimmedName) return;
    await onCreate({
      name: trimmedName,
      one_line_vision: trimmedVision || undefined,
    });
    setName('');
    setVision('');
  }

  return (
    <aside className="project-rail panel-shell">
      <div className="rail-header">
        <div>
          <p className="eyebrow">Project Roster</p>
          <h2>Navigation Core</h2>
        </div>
        <span className="rail-count">{projects.length}</span>
      </div>
      <form className="project-create-form" onSubmit={handleCreateSubmit}>
        <input
          className="project-create-input"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="New project name"
          disabled={creating}
        />
        <textarea
          className="project-create-textarea"
          value={vision}
          onChange={(event) => setVision(event.target.value)}
          placeholder="One-line vision (optional)"
          rows={3}
          disabled={creating}
        />
        <button className="project-create-button" type="submit" disabled={creating || !name.trim()}>
          {creating ? 'Creating...' : 'Create Project'}
        </button>
      </form>
      <div className="rail-list">
        {projects.map((project) => {
          const active = project.id === selectedProjectId;
          return (
            <button
              key={project.id}
              className={`project-card ${active ? 'is-active' : ''}`}
              onClick={() => onSelect(project.id)}
              type="button"
            >
              <div className="project-card-top">
                <div>
                  <strong>{project.name}</strong>
                  <span>{titleize(project.current_stage)}</span>
                </div>
                <span className={`status-dot status-${project.status}`}>{titleize(project.status)}</span>
              </div>
              <p>{project.current_focus || project.one_line_vision || project.description || 'No summary yet'}</p>
              <div className="project-card-meta">
                <span>{project.health_status ? titleize(project.health_status) : 'Unknown health'}</span>
                <span>{formatRelative(project.last_activity_at)}</span>
              </div>
              {project.blocking_reason ? <div className="project-card-blocked">Blocked: {project.blocking_reason}</div> : null}
            </button>
          );
        })}
        {projects.length === 0 ? <div className="empty-card">No projects yet. Create one to start the cockpit.</div> : null}
      </div>
    </aside>
  );
}
