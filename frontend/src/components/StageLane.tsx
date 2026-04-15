import { Clock3, Loader } from 'lucide-react';

import { formatRelative, titleize, truncate } from '../lib/format';
import type { StageRun } from '../types';

type Props = {
  stageRuns: StageRun[];
  selectedStageRunId: number | null;
  onSelect: (stageRunId: number) => void;
};

export function StageLane({ stageRuns, selectedStageRunId, onSelect }: Props) {
  return (
    <section className="panel-shell stage-lane">
      <div className="section-header">
        <div>
          <p className="eyebrow">Project Progression</p>
          <h3>Stage Lane</h3>
        </div>
        <span className="section-stat">{stageRuns.length} runs</span>
      </div>
      <div className="stage-list">
        {stageRuns.map((stageRun) => {
          const active = stageRun.id === selectedStageRunId;
          return (
            <button
              key={stageRun.id}
              className={`stage-card ${active ? 'is-active' : ''}`}
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
