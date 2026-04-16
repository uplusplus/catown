import { AlertTriangle, ArrowRight, Compass, Loader, Play, Scale, Sparkles, Workflow } from 'lucide-react';

import { formatRelative, titleize } from '../lib/format';
import type { EventItem, ProjectOverview, StageRunDetail } from '../types';
import { CurrentSegment } from './CurrentSegment';
import { StageLane } from './StageLane';

type Props = {
  overview: ProjectOverview;
  stageRuns: ProjectOverview['recent_activity'];
  stageDetail: StageRunDetail | null;
  selectedStageRunId: number | null;
  switchingProject: boolean;
  switchingStage: boolean;
  continuing: boolean;
  detailError: string | null;
  onContinue: () => void;
  onReviewDecision: () => void;
  onSelectStage: (stageRunId: number) => void;
  onSelectDecision: (decisionId: number) => void;
  onSelectAsset: (assetId: number) => void;
  onSelectEvent: (event: EventItem) => void;
};

type ActionCopy = {
  kicker: string;
  description: string;
};

const LABELS: Record<string, ActionCopy> = {
  continue_project: {
    kicker: 'Execution can keep moving.',
    description: 'Push the project through the next route point when the system looks aligned.',
  },
  review_current_stage: {
    kicker: 'Current route point needs operator eyes.',
    description: 'Check the current segment, outputs, and event trail before taking action.',
  },
  review_prd: {
    kicker: 'PRD output is ready for review.',
    description: 'Open the latest PRD materials and decide whether they are strong enough to keep momentum.',
  },
  review_definition_bundle: {
    kicker: 'Definition artifacts are ready.',
    description: 'Review the scope bundle for clarity, risk, and missing constraints.',
  },
  review_task_plan: {
    kicker: 'Execution planning is on deck.',
    description: 'Validate the generated task plan before the team commits to the wrong build path.',
  },
  review_test_report: {
    kicker: 'Quality signals need review.',
    description: 'Read the latest test signals and decide whether the project is safe to advance.',
  },
  review_release_pack: {
    kicker: 'Release material is assembled.',
    description: 'Review the pack and confirm that the project is ready for shipment.',
  },
  resolve_scope_confirmation: {
    kicker: 'A scope call is blocking progress.',
    description: 'Resolve the scope decision so the project can leave bootstrap cleanly.',
  },
  resolve_direction_confirmation: {
    kicker: 'A direction choice needs your call.',
    description: 'Choose the direction before the system spends more effort on the wrong branch.',
  },
  resolve_release_approval: {
    kicker: 'Release approval is the last hard gate.',
    description: 'Approve or reject the release after checking the pack and risk signals.',
  },
  resolve_decision: {
    kicker: 'A decision is waiting on you.',
    description: 'Open the linked decision and resolve it clearly so the project can move again.',
  },
  review_project: {
    kicker: 'Take a cockpit-wide pass.',
    description: 'Scan route, status, and artifacts before deciding the next move.',
  },
};

export function NavigationCore({
  overview,
  stageRuns,
  stageDetail,
  selectedStageRunId,
  switchingProject,
  switchingStage,
  continuing,
  detailError,
  onContinue,
  onReviewDecision,
  onSelectStage,
  onSelectDecision,
  onSelectAsset,
  onSelectEvent,
}: Props) {
  const { project, pending_decisions: pendingDecisions, release_readiness: readiness, recommended_next_action: action } = overview;
  const hasPendingDecision = pendingDecisions.length > 0;
  const copy = LABELS[action] || {
    kicker: 'Choose the next move.',
    description: 'Review the route and operator context, then make the next useful intervention.',
  };

  return (
    <section className={`panel-shell navigation-core ${switchingProject ? 'is-switching-project' : ''}`}>
      <div className="navigation-core-header section-header">
        <div>
          <p className="eyebrow">Navigation Core</p>
          <h2>{project.name}</h2>
          <p className="section-copy">Route first, operator actions second. This is the homepage control surface for the selected project.</p>
        </div>
        <div className="navigation-core-badges">
          <span className="pill">{titleize(project.status)}</span>
          <span className="pill muted">Stage: {titleize(project.current_stage)}</span>
          <span className="pill muted">Mode: {titleize(project.execution_mode)}</span>
          {switchingProject ? (
            <span className="pill muted">
              <Loader className="spin" size={14} />
              Syncing cockpit
            </span>
          ) : null}
        </div>
      </div>

      <div className="navigation-core-route">
        <StageLane
          stageRuns={stageRuns}
          selectedStageRunId={selectedStageRunId}
          onSelect={onSelectStage}
          switchingStage={switchingStage}
        />
      </div>

      <section className="navigation-action-area">
        <div className="navigation-action-header section-header">
          <div>
            <p className="eyebrow">User Action Area</p>
            <h3>{copy.kicker}</h3>
            <p className="section-copy">{copy.description}</p>
          </div>
          <span className="section-stat">
            <Sparkles size={14} />
            {titleize(action)}
          </span>
        </div>

        <div className="navigation-action-grid">
          <section className="detail-block action-cta-block">
            <div className="action-cta-copy">
              <div>
                <label>Current focus</label>
                <strong>{project.current_focus || 'No focus summary yet.'}</strong>
              </div>
              <p>{project.one_line_vision || project.description || 'No project vision yet.'}</p>
              <div className="action-cta-meta">
                <span className="pill muted">Last movement: {formatRelative(project.last_activity_at)}</span>
                {project.blocking_reason ? <span className="pill warning-pill">Blocked: {project.blocking_reason}</span> : null}
              </div>
            </div>
            <button
              className={`primary-cta ${hasPendingDecision ? 'is-secondary' : ''}`}
              onClick={hasPendingDecision ? onReviewDecision : onContinue}
              type="button"
              disabled={continuing}
            >
              {hasPendingDecision ? <Scale size={18} /> : <Play size={18} />}
              <span>{continuing ? 'Continuing...' : hasPendingDecision ? 'Review Pending Decision' : 'Continue Project'}</span>
            </button>
          </section>

          <section className="detail-block operator-signal-block">
            <div className="operator-signal-head">
              <Compass size={18} />
              <strong>Operator signals</strong>
            </div>
            <div className="operator-signal-grid">
              <div>
                <label>Pending decisions</label>
                <strong>{pendingDecisions.length}</strong>
              </div>
              <div>
                <label>Release gate</label>
                <strong>{readiness.pending_release_decision ? 'Pending' : 'Clear'}</strong>
              </div>
              <div>
                <label>PRD ready</label>
                <strong>{readiness.has_prd ? 'Yes' : 'No'}</strong>
              </div>
              <div>
                <label>Release pack</label>
                <strong>{readiness.has_release_pack ? 'Ready' : 'Missing'}</strong>
              </div>
            </div>
            {hasPendingDecision ? (
              <button className="inline-link-button" onClick={onReviewDecision} type="button">
                <AlertTriangle size={16} />
                <span>{pendingDecisions[0]?.title || 'Review blocking decision'}</span>
                <ArrowRight size={16} />
              </button>
            ) : (
              <div className="inline-status-line">
                <Workflow size={16} />
                <span>No blocking human decisions at the moment.</span>
              </div>
            )}
          </section>
        </div>

        <CurrentSegment
          stageDetail={stageDetail}
          projectName={project.name}
          loading={switchingStage}
          error={detailError}
          onSelectDecision={onSelectDecision}
          onSelectAsset={onSelectAsset}
          onSelectEvent={onSelectEvent}
        />
      </section>
    </section>
  );
}
