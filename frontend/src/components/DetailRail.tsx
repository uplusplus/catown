import type { JSX } from 'react';

import { FileText, GitBranch, ListChecks, Workflow } from 'lucide-react';

import { prettyJson, titleize } from '../lib/format';
import type { Asset, Decision, EventItem, StageRunDetail } from '../types';

type Props = {
  stageDetail: StageRunDetail | null;
  decisionDetail: Decision | null;
  assetDetail: Asset | null;
  selectedEvent: EventItem | null;
};

export function DetailRail({ stageDetail, decisionDetail, assetDetail, selectedEvent }: Props) {
  let title = 'Detail Rail';
  let icon = <Workflow size={18} />;
  let body: JSX.Element = <div className="empty-card">Select a stage, decision, asset, or event.</div>;

  if (stageDetail) {
    title = `${titleize(stageDetail.stage_run.stage_type)} Run`;
    icon = <Workflow size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>Status</h4>
          <p>{titleize(stageDetail.stage_run.status)}</p>
          <small>{stageDetail.stage_run.summary || 'No stage summary yet.'}</small>
        </section>
        <section className="detail-block">
          <h4>Outputs</h4>
          <ul>
            {stageDetail.output_assets.map((asset) => (
              <li key={asset.id}>{asset.title || titleize(asset.asset_type)}</li>
            ))}
            {stageDetail.output_assets.length === 0 ? <li>No outputs yet.</li> : null}
          </ul>
        </section>
        <section className="detail-block">
          <h4>Decisions</h4>
          <ul>
            {stageDetail.decisions.map((decision) => (
              <li key={decision.id}>{decision.title}</li>
            ))}
            {stageDetail.decisions.length === 0 ? <li>No linked decisions.</li> : null}
          </ul>
        </section>
      </div>
    );
  }

  if (decisionDetail) {
    title = 'Decision Detail';
    icon = <ListChecks size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{decisionDetail.title}</h4>
          <p>{decisionDetail.context_summary || decisionDetail.requested_action || 'No context summary yet.'}</p>
        </section>
        <section className="detail-block">
          <h4>Recommendation</h4>
          <p>{decisionDetail.recommended_option || 'No recommended option.'}</p>
          <small>{decisionDetail.impact_summary || 'No impact summary yet.'}</small>
        </section>
      </div>
    );
  }

  if (assetDetail) {
    title = 'Asset Detail';
    icon = <FileText size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{assetDetail.title || titleize(assetDetail.asset_type)}</h4>
          <p>{assetDetail.summary || 'No asset summary yet.'}</p>
        </section>
        <section className="detail-block">
          <h4>Markdown</h4>
          <pre>{assetDetail.content_markdown || prettyJson(assetDetail.content_json)}</pre>
        </section>
      </div>
    );
  }

  if (selectedEvent) {
    title = 'Event Detail';
    icon = <GitBranch size={18} />;
    body = (
      <div className="detail-stack">
        <section className="detail-block">
          <h4>{titleize(selectedEvent.event_type)}</h4>
          <p>{selectedEvent.summary || 'No event summary yet.'}</p>
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
