import { Activity, AlertTriangle, Compass, Gauge, ShieldCheck } from 'lucide-react';

import { titleize } from '../lib/format';
import type { ProjectOverview, StageRunDetail } from '../types';

type Props = {
  overview: ProjectOverview | null;
  stageDetail: StageRunDetail | null;
  selectedStageName?: string | null;
};

export function StatusRail({ overview, stageDetail, selectedStageName }: Props) {
  const project = overview?.project ?? null;
  const readiness = overview?.release_readiness ?? null;
  const pendingDecisions = overview?.pending_decisions.length ?? 0;

  return (
    <aside className="detail-rail status-rail panel-shell">
      <div className="section-header detail-header">
        <div>
          <p className="eyebrow">Status Display</p>
          <h3>System State</h3>
          <p className="section-copy">Watch system health, autonomy signals, and route status without turning the right rail into a deep detail pane.</p>
        </div>
        <span className="detail-icon">
          <Gauge size={18} />
        </span>
      </div>

      <div className="detail-stack">
        <section className="detail-block">
          <div className="status-row-head">
            <Compass size={18} />
            <strong>Project posture</strong>
          </div>
          <div className="detail-grid-block">
            <div>
              <label>Status</label>
              <strong>{project ? titleize(project.status) : 'No project'}</strong>
            </div>
            <div>
              <label>Health</label>
              <strong>{project?.health_status ? titleize(project.health_status) : 'Unknown'}</strong>
            </div>
            <div>
              <label>Current stage</label>
              <strong>{project ? titleize(project.current_stage) : 'None'}</strong>
            </div>
            <div>
              <label>Selected route point</label>
              <strong>{selectedStageName ? titleize(selectedStageName) : 'None'}</strong>
            </div>
          </div>
        </section>

        <section className="detail-block">
          <div className="status-row-head">
            <ShieldCheck size={18} />
            <strong>Autonomy status</strong>
          </div>
          <div className="detail-grid-block">
            <div>
              <label>Mode</label>
              <strong>{project ? titleize(project.execution_mode) : 'Unknown'}</strong>
            </div>
            <div>
              <label>Pending decisions</label>
              <strong>{pendingDecisions}</strong>
            </div>
            <div>
              <label>Release gate</label>
              <strong>{readiness?.pending_release_decision ? 'Pending' : 'Clear'}</strong>
            </div>
            <div>
              <label>Stage events</label>
              <strong>{stageDetail?.summary.event_count ?? 0}</strong>
            </div>
          </div>
        </section>

        <section className="detail-block">
          <div className="status-row-head">
            <Activity size={18} />
            <strong>Signal summary</strong>
          </div>
          <div className="detail-grid-block">
            <div>
              <label>PRD</label>
              <strong>{readiness?.has_prd ? 'Ready' : 'Missing'}</strong>
            </div>
            <div>
              <label>Release pack</label>
              <strong>{readiness?.has_release_pack ? 'Ready' : 'Missing'}</strong>
            </div>
            <div>
              <label>Inputs</label>
              <strong>{stageDetail?.summary.input_count ?? 0}</strong>
            </div>
            <div>
              <label>Outputs</label>
              <strong>{stageDetail?.summary.output_count ?? 0}</strong>
            </div>
          </div>
        </section>

        {project?.blocking_reason ? (
          <section className="detail-block warning-card status-warning-card">
            <AlertTriangle size={18} />
            <div>
              <strong>Blocking signal</strong>
              <p>{project.blocking_reason}</p>
            </div>
          </section>
        ) : null}
      </div>
    </aside>
  );
}
