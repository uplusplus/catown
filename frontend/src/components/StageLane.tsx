import { AlertTriangle, CheckCircle2, Clock3, Loader, Radar } from 'lucide-react';

import { formatRelative, titleize, truncate } from '../lib/format';
import type { StageRun } from '../types';

type Props = {
  stageRuns: StageRun[];
  selectedStageRunId: number | null;
  onSelect: (stageRunId: number) => void;
  switchingStage?: boolean;
};

function getStageTone(stageRun: StageRun) {
  if (stageRun.lifecycle.requires_attention) return 'attention';
  if (stageRun.lifecycle.is_terminal) return 'terminal';
  if (stageRun.lifecycle.is_active) return 'active';
  return 'idle';
}

export function StageLane({ stageRuns, selectedStageRunId, onSelect, switchingStage = false }: Props) {
  const selectedStage = stageRuns.find((stageRun) => stageRun.id === selectedStageRunId) ?? stageRuns[0] ?? null;
  const selectedIndex = selectedStage ? stageRuns.findIndex((stageRun) => stageRun.id === selectedStage.id) : -1;
  const nextStage = selectedIndex >= 0 ? stageRuns[selectedIndex + 1] ?? null : null;

  return (
    <section className={`panel-shell stage-lane stage-route-shell ${switchingStage ? 'is-switching-stage' : ''}`}>
      <div className="section-header">
        <div>
          <p className="eyebrow">Navigation Core</p>
          <h3>Route Visualization</h3>
          <p className="section-copy">
            Track the mission route, anchor on the current point, and open the local segment beneath it.
          </p>
        </div>
        <span className="section-stat">
          {switchingStage ? <Loader className="spin" size={14} /> : <Radar size={14} />}
          {stageRuns.length} route points
        </span>
      </div>
      <div className={`stage-route ${switchingStage ? 'is-switching-stage' : ''}`}>
        {stageRuns.map((stageRun, index) => {
          const active = stageRun.id === selectedStageRunId;
          const tone = getStageTone(stageRun);
          const isPast = selectedIndex >= 0 && index < selectedIndex;
          const isFuture = selectedIndex >= 0 && index > selectedIndex;
          return (
            <div key={stageRun.id} className={`route-stop route-stop-${tone} ${active ? 'is-active' : ''} ${isPast ? 'is-past' : ''} ${isFuture ? 'is-future' : ''}`}>
              <button className="route-stop-button" onClick={() => onSelect(stageRun.id)} type="button">
                <span className={`route-stop-node route-stop-node-${tone}`}>
                  {tone === 'attention' ? <AlertTriangle size={14} /> : tone === 'terminal' ? <CheckCircle2 size={14} /> : stageRun.lifecycle.is_active ? <Loader size={14} /> : <Clock3 size={14} />}
                </span>
                <div className="route-stop-copy">
                  <strong>{titleize(stageRun.stage_type)}</strong>
                  <span>Run #{stageRun.run_index}</span>
                  <small>{titleize(stageRun.lifecycle.phase)}</small>
                </div>
              </button>
              {index < stageRuns.length - 1 ? <div className={`route-connector ${selectedIndex >= 0 && index < selectedIndex ? 'is-travelled' : ''}`} /> : null}
            </div>
          );
        })}
        {stageRuns.length === 0 ? <div className="empty-card">No route points yet.</div> : null}
      </div>
      {selectedStage ? (
        <div className="route-focus-card">
          <div className="route-focus-copy">
            <p className="eyebrow">Selected Route Point</p>
            <h4>{titleize(selectedStage.stage_type)}</h4>
            <p>{truncate(selectedStage.summary, 160) || 'No stage summary yet.'}</p>
          </div>
          <div className="route-focus-meta">
            <div>
              <label>Current phase</label>
              <strong>{titleize(selectedStage.lifecycle.phase)}</strong>
            </div>
            <div>
              <label>Status</label>
              <strong>{titleize(selectedStage.status)}</strong>
            </div>
            <div>
              <label>Opened</label>
              <strong>{formatRelative(selectedStage.created_at)}</strong>
            </div>
            <div>
              <label>Next gate</label>
              <strong>{nextStage ? titleize(nextStage.stage_type) : 'Final route point'}</strong>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
