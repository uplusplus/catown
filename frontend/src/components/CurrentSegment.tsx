import { ChevronRight, Loader, Workflow } from 'lucide-react';

import { titleize } from '../lib/format';
import type { EventItem, StageRunDetail } from '../types';

type Props = {
  stageDetail: StageRunDetail | null;
  projectName?: string | null;
  loading: boolean;
  error: string | null;
  onSelectDecision: (decisionId: number) => void;
  onSelectAsset: (assetId: number) => void;
  onSelectEvent: (event: EventItem) => void;
};

export function CurrentSegment({
  stageDetail,
  projectName,
  loading,
  error,
  onSelectDecision,
  onSelectAsset,
  onSelectEvent,
}: Props) {
  const trail = [
    projectName || 'Cockpit homepage',
    stageDetail ? titleize(stageDetail.stage_run.stage_type) : 'Current segment',
  ];

  let body = <div className="empty-card">Select a route point to inspect the current segment.</div>;

  if (loading) {
    body = (
      <div className="detail-loading-state">
        <Loader className="spin" size={18} />
        <span>Loading current segment...</span>
      </div>
    );
  } else if (error) {
    body = <div className="detail-error-state">{error}</div>;
  } else if (stageDetail) {
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>Status</h4>
          <p>{titleize(stageDetail.stage_run.status)}</p>
          <small>{stageDetail.stage_run.summary || 'No stage summary yet.'}</small>
        </section>
        <section className="detail-block detail-grid-block">
          <div>
            <label>Phase</label>
            <strong>{titleize(stageDetail.stage_run.lifecycle.phase)}</strong>
          </div>
          <div>
            <label>Inputs</label>
            <strong>{stageDetail.summary.input_count}</strong>
          </div>
          <div>
            <label>Outputs</label>
            <strong>{stageDetail.summary.output_count}</strong>
          </div>
          <div>
            <label>Events</label>
            <strong>{stageDetail.summary.event_count}</strong>
          </div>
        </section>
        <section className="current-segment-grid">
          <section className="detail-block">
            <h4>Inputs</h4>
            <div className="detail-linked-list">
              {stageDetail.input_assets.map((asset) => (
                <button key={asset.id} className="detail-link-card" onClick={() => onSelectAsset(asset.id)} type="button">
                  <strong>{asset.title || titleize(asset.asset_type)}</strong>
                  <span>Input - {titleize(asset.asset_type)}</span>
                </button>
              ))}
              {stageDetail.input_assets.length === 0 ? <div className="empty-card">No inputs linked yet.</div> : null}
            </div>
          </section>
          <section className="detail-block">
            <h4>Outputs</h4>
            <div className="detail-linked-list">
              {stageDetail.output_assets.map((asset) => (
                <button key={asset.id} className="detail-link-card" onClick={() => onSelectAsset(asset.id)} type="button">
                  <strong>{asset.title || titleize(asset.asset_type)}</strong>
                  <span>Output - {titleize(asset.asset_type)}</span>
                </button>
              ))}
              {stageDetail.output_assets.length === 0 ? <div className="empty-card">No outputs yet.</div> : null}
            </div>
          </section>
        </section>
        <section className="current-segment-grid">
          <section className="detail-block">
            <h4>Linked decisions</h4>
            <div className="detail-linked-list">
              {stageDetail.decisions.map((decision) => (
                <button key={decision.id} className="detail-link-card" onClick={() => onSelectDecision(decision.id)} type="button">
                  <strong>{decision.title}</strong>
                  <span>{titleize(decision.status)}</span>
                </button>
              ))}
              {stageDetail.decisions.length === 0 ? <div className="empty-card">No linked decisions.</div> : null}
            </div>
          </section>
          <section className="detail-block">
            <h4>Recent events</h4>
            <div className="detail-linked-list">
              {stageDetail.events.slice(0, 5).map((event) => (
                <button key={event.id} className="detail-link-card" onClick={() => onSelectEvent(event)} type="button">
                  <strong>{event.summary || titleize(event.event_type)}</strong>
                  <span>{titleize(event.event_type)}</span>
                </button>
              ))}
              {stageDetail.events.length === 0 ? <div className="empty-card">No events captured yet.</div> : null}
            </div>
          </section>
        </section>
      </div>
    );
  }

  return (
    <section className="panel-shell current-segment-shell">
      <div className="section-header detail-header">
        <div>
          <p className="eyebrow">Current Segment</p>
          <div className="detail-context-trail" aria-label="Current segment context trail">
            {trail.map((segment, index) => (
              <span key={`${segment}-${index}`} className="detail-context-segment">
                {index > 0 ? <ChevronRight size={14} /> : null}
                <span>{segment}</span>
              </span>
            ))}
          </div>
          <h3>{stageDetail ? `${titleize(stageDetail.stage_run.stage_type)} Segment` : 'Segment Overview'}</h3>
        </div>
        <span className="detail-icon">
          <Workflow size={18} />
        </span>
      </div>
      {body}
    </section>
  );
}
