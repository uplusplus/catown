import type { Project } from '../types';
import { formatRelative, titleize } from '../lib/format';

type Props = {
  projects: Project[];
  selectedProjectId: number | null;
  onSelect: (projectId: number) => void;
};

export function ProjectRail({ projects, selectedProjectId, onSelect }: Props) {
  return (
    <aside className="project-rail panel-shell">
      <div className="rail-header">
        <div>
          <p className="eyebrow">Project Roster</p>
          <h2>Mission Board</h2>
        </div>
        <span className="rail-count">{projects.length}</span>
      </div>
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
        {projects.length === 0 ? <div className="empty-card">No projects yet.</div> : null}
      </div>
    </aside>
  );
}
