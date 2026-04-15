import { AlertTriangle, Loader, Play, Scale, ShieldCheck } from 'lucide-react';

import { formatRelative, titleize } from '../lib/format';
import type { ProjectOverview } from '../types';

type Props = {
  overview: ProjectOverview;
  onContinue: () => void;
  onReviewDecision: () => void;
  continuing: boolean;
  switchingProject?: boolean;
};

export function ProjectHero({ overview, onContinue, onReviewDecision, continuing, switchingProject = false }: Props) {
  const { project, pending_decisions: pendingDecisions, release_readiness: readiness } = overview;
  const hasPendingDecision = pendingDecisions.length > 0;

  return (
    <section className={`hero-shell panel-shell ${switchingProject ? 'is-switching-project' : ''}`}>
      <div className="hero-copy">
        <p className="eyebrow">Selected Mission</p>
        <h1>{project.name}</h1>
        <p className="hero-vision">{project.one_line_vision || project.description || 'No project vision yet.'}</p>
        <div className="hero-meta-row">
          <span className="pill">{titleize(project.status)}</span>
          {switchingProject ? (
            <span className="pill muted">
              <Loader className="spin" size={14} />
              Syncing mission board
            </span>
          ) : null}
          <span className="pill muted">Stage: {titleize(project.current_stage)}</span>
          <span className="pill muted">Mode: {titleize(project.execution_mode)}</span>
          {project.health_status ? <span className="pill muted">Health: {titleize(project.health_status)}</span> : null}
        </div>
        <div className="hero-summary-grid">
          <div>
            <label>Current focus</label>
            <p>{project.current_focus || 'No focus summary yet.'}</p>
          </div>
          <div>
            <label>Current stage</label>
            <p>{titleize(project.current_stage)}</p>
          </div>
          <div>
            <label>Last movement</label>
            <p>{formatRelative(project.last_activity_at)}</p>
          </div>
          <div>
            <label>Latest summary</label>
            <p>{project.latest_summary || 'No latest summary yet.'}</p>
          </div>
        </div>
      </div>

      <div className="hero-side">
        <div className="readiness-card">
          <div className="readiness-header">
            <ShieldCheck size={18} />
            <span>Release Readiness</span>
          </div>
          <div className="readiness-items">
            <div>
              <strong>{readiness.has_prd ? 'Yes' : 'No'}</strong>
              <span>PRD ready</span>
            </div>
            <div>
              <strong>{readiness.has_release_pack ? 'Yes' : 'No'}</strong>
              <span>Release pack</span>
            </div>
            <div>
              <strong>{readiness.pending_release_decision ? 'Pending' : 'Clear'}</strong>
              <span>Release gate</span>
            </div>
          </div>
        </div>

        {project.blocking_reason ? (
          <div className="warning-card">
            <AlertTriangle size={18} />
            <div>
              <strong>Blocked</strong>
              <p>{project.blocking_reason}</p>
            </div>
          </div>
        ) : null}

        {hasPendingDecision ? (
          <div className="warning-card hero-guidance-card">
            <Scale size={18} />
            <div>
              <strong>Decision required before continue</strong>
              <p>{pendingDecisions[0]?.title || 'A pending decision is blocking project progress.'}</p>
            </div>
          </div>
        ) : null}

        <button
          className={`primary-cta ${hasPendingDecision ? 'is-secondary' : ''}`}
          onClick={hasPendingDecision ? onReviewDecision : onContinue}
          type="button"
          disabled={continuing}
        >
          {hasPendingDecision ? <Scale size={18} /> : <Play size={18} />}
          <span>{continuing ? 'Continuing...' : hasPendingDecision ? 'Review Pending Decision' : 'Continue Project'}</span>
        </button>
      </div>
    </section>
  );
}
