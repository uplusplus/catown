import { Clock3, Loader } from 'lucide-react';

import { formatRelative, titleize, truncate } from '../lib/format';
import type { StageRun } from '../types';

type Props = {
  stageRuns: StageRun[];
  selectedStageRunId: number | null;
  onSelect: (stageRunId: number) => void;
  switchingStage?: boolean;
};

export function StageLane({ stageRuns, selectedStageRunId, onSelect, switchingStage = false }: Props) {
  return (
    <section className={`panel-shell stage-lane ${switchingStage ? 'is-switching-stage' : ''}`}>
      <div className="section-header">
        <div>
          <p className="eyebrow">Project Progression</p>
          <h3>Stage Lane</h3>
        </div>
        <span className="section-stat">
          {switchingStage ? <Loader className="spin" size={14} /> : null}
          {stageRuns.length} runs
        </span>
      </div>
      <div className={`stage-list ${switchingStage ? 'is-switching-stage' : ''}`}>
        {stageRuns.map((stageRun) => {
          const active = stageRun.id === selectedStageRunId;
          const stateClass = `stage-card-${stageRun.lifecycle.requires_attention ? 'attention' : stageRun.lifecycle.is_terminal ? 'terminal' : stageRun.lifecycle.is_active ? 'active' : 'idle'}`;
          return (
            <button
              key={stageRun.id}
              className={`stage-card ${stateClass} ${active ? 'is-active' : ''}`}
              onClick={() => onSelect(stageRun.id)}
              type="button"
            >
              <div className="stage-card-top">
                <div>
                  <strong>{titleize(stageRun.stage_type)}</strong>
                  <span>Run #{stageRun.run_index}</span>
                </div>
                <span className={`stage-badge stage-${stageRun.status}`}>{titleize(stageRun.status)}</span>
              </div>
              <p>{truncate(stageRun.summary, 110) || 'No stage summary yet.'}</p>
              <div className="stage-card-meta">
                <span>
                  {stageRun.lifecycle.is_active ? <Loader size={14} /> : <Clock3 size={14} />}
                  {titleize(stageRun.lifecycle.phase)}
                </span>
                <span>{formatRelative(stageRun.created_at)}</span>
              </div>
            </button>
          );
        })}
        {stageRuns.length === 0 ? <div className="empty-card">No stage runs yet.</div> : null}
      </div>
    </section>
  );
}
