import type { JSX } from 'react';

import { FileText, GitBranch, ListChecks, Loader, Workflow } from 'lucide-react';

import { prettyJson, titleize } from '../lib/format';
import type { Asset, Decision, EventItem, StageRunDetail } from '../types';

export type DetailFocus = 'stage' | 'decision' | 'asset' | 'event';

type Props = {
  focus: DetailFocus;
  stageDetail: StageRunDetail | null;
  decisionDetail: Decision | null;
  assetDetail: Asset | null;
  selectedEvent: EventItem | null;
  onSelectDecision: (decisionId: number) => void;
  onSelectAsset: (assetId: number) => void;
  onSelectEvent: (event: EventItem) => void;
  loading: 'decision' | 'asset' | null;
  error: string | null;
};

export function DetailRail({
  focus,
  stageDetail,
  decisionDetail,
  assetDetail,
  selectedEvent,
  onSelectDecision,
  onSelectAsset,
  onSelectEvent,
  loading,
  error,
}: Props) {
  let title = 'Detail Rail';
  let icon = <Workflow size={18} />;
  let body: JSX.Element = <div className="empty-card">Select a stage, decision, asset, or event.</div>;

  if (loading) {
    body = (
      <div className="detail-loading-state">
        <Loader className="spin" size={18} />
        <span>{loading === 'decision' ? 'Loading decision detail...' : 'Loading asset detail...'}</span>
      </div>
    );
  }

  if (!loading && error) {
    body = <div className="detail-error-state">{error}</div>;
  }

  if (!loading && !error && focus === 'stage' && stageDetail) {

    title = `${titleize(stageDetail.stage_run.stage_type)} Run`;
    icon = <Workflow size={18} />;
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
        <section className="detail-block">
          <h4>Decisions</h4>
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
          <h4>Events</h4>
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
      </div>
    );
  }

  if (!loading && !error && focus === 'decision' && decisionDetail) {
    title = 'Decision Detail';
    icon = <ListChecks size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{decisionDetail.title}</h4>
          <p>{decisionDetail.context_summary || decisionDetail.requested_action || 'No context summary yet.'}</p>
        </section>
        <section className="detail-block detail-grid-block">
          <div>
            <label>Status</label>
            <strong>{titleize(decisionDetail.status)}</strong>
          </div>
          <div>
            <label>Type</label>
            <strong>{titleize(decisionDetail.decision_type)}</strong>
          </div>
          <div>
            <label>Recommended</label>
            <strong>{decisionDetail.recommended_option || 'None'}</strong>
          </div>
          <div>
            <label>Resolved</label>
            <strong>{decisionDetail.resolved_option || 'Pending'}</strong>
          </div>
        </section>
        <section className="detail-block">
          <h4>Impact</h4>
          <p>{decisionDetail.impact_summary || 'No impact summary yet.'}</p>
        </section>
        <section className="detail-block">
          <h4>Alternative Options</h4>
          <ul className="detail-list">
            {decisionDetail.alternative_options.map((option) => (
              <li key={option}>{option}</li>
            ))}
            {decisionDetail.alternative_options.length === 0 ? <li>No alternatives recorded.</li> : null}
          </ul>
        </section>
      </div>
    );
  }

  if (!loading && !error && focus === 'asset' && assetDetail) {
    title = 'Asset Detail';
    icon = <FileText size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{assetDetail.title || titleize(assetDetail.asset_type)}</h4>
          <p>{assetDetail.summary || 'No asset summary yet.'}</p>
        </section>
        <section className="detail-block detail-grid-block">
          <div>
            <label>Type</label>
            <strong>{titleize(assetDetail.asset_type)}</strong>
          </div>
          <div>
            <label>Version</label>
            <strong>v{assetDetail.version}</strong>
          </div>
          <div>
            <label>Status</label>
            <strong>{titleize(assetDetail.status)}</strong>
          </div>
          <div>
            <label>Current</label>
            <strong>{assetDetail.is_current ? 'Yes' : 'No'}</strong>
          </div>
        </section>
        <section className="detail-block">
          <h4>Relationships</h4>
          <ul className="detail-list">
            {assetDetail.relationships?.upstream.map((link) => (
              <li key={`up-${link.asset_id}`}>Upstream #{link.asset_id} - {titleize(link.relation_type)}</li>
            ))}
            {assetDetail.relationships?.downstream.map((link) => (
              <li key={`down-${link.asset_id}`}>Downstream #{link.asset_id} - {titleize(link.relation_type)}</li>
            ))}
            {!assetDetail.relationships?.upstream.length && !assetDetail.relationships?.downstream.length ? (
              <li>No asset relationships yet.</li>
            ) : null}
          </ul>
        </section>
        <section className="detail-block">
          <h4>Content</h4>
          <pre>{assetDetail.content_markdown || prettyJson(assetDetail.content_json)}</pre>
        </section>
      </div>
    );
  }

  if (!loading && !error && focus === 'event' && selectedEvent) {
    title = 'Event Detail';
    icon = <GitBranch size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{titleize(selectedEvent.event_type)}</h4>
          <p>{selectedEvent.summary || 'No event summary yet.'}</p>
        </section>
        <section className="detail-block detail-grid-block">
          <div>
            <label>Stage</label>
            <strong>{selectedEvent.stage_name ? titleize(selectedEvent.stage_name) : 'None'}</strong>
          </div>
          <div>
            <label>Agent</label>
            <strong>{selectedEvent.agent_name || 'System'}</strong>
          </div>
          <div>
            <label>Stage Run</label>
            <strong>{selectedEvent.stage_run_id ?? 'None'}</strong>
          </div>
          <div>
            <label>Asset</label>
            <strong>{selectedEvent.asset_id ?? 'None'}</strong>
          </div>
        </section>
        <section className="detail-block">
          <h4>Payload</h4>
          <pre>{prettyJson(selectedEvent.payload)}</pre>
        </section>
      </div>
    );
  }

  return (
    <aside className="detail-rail panel-shell">
      <div className="section-header detail-header">
        <div>
          <p className="eyebrow">Focused Inspection</p>
          <h3>{title}</h3>
        </div>
        <span className="detail-icon">{icon}</span>
      </div>
      {body}
    </aside>
  );
}
