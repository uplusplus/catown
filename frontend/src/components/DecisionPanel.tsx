import { Check, X } from 'lucide-react';

import { formatRelative, titleize, truncate } from '../lib/format';
import type { Decision } from '../types';

type Props = {
  decisions: Decision[];
  selectedDecisionId: number | null;
  onSelect: (decisionId: number) => void;
  onResolve: (decisionId: number, resolution: 'approved' | 'rejected') => void;
  resolvingId: number | null;
};

export function DecisionPanel({ decisions, selectedDecisionId, onSelect, onResolve, resolvingId }: Props) {
  return (
    <section className="panel-shell decision-panel">
      <div className="section-header">
        <div>
          <p className="eyebrow">Human Checkpoints</p>
          <h3>Decisions</h3>
        </div>
        <span className="section-stat">{decisions.filter((item) => item.status === 'pending').length} pending</span>
      </div>
      <div className="decision-list">
        {decisions.map((decision) => {
          const busy = resolvingId === decision.id;
          return (
            <article
              key={decision.id}
              className={`decision-card ${decision.status === 'pending' ? 'is-pending' : ''} ${decision.id === selectedDecisionId ? 'is-active' : ''}`}
            >
              <button className="decision-open" onClick={() => onSelect(decision.id)} type="button">
                <div className="decision-card-top">
                  <strong>{decision.title}</strong>
                  <span className={`stage-badge stage-${decision.status}`}>{titleize(decision.status)}</span>
                </div>
                <p>{truncate(decision.context_summary || decision.requested_action, 120) || 'No decision context yet.'}</p>
                <div className="decision-card-meta">
                  <span>{titleize(decision.decision_type)}</span>
                  <span>{formatRelative(decision.created_at)}</span>
                </div>
              </button>
              {decision.status === 'pending' ? (
                <div className="decision-actions">
                  <button disabled={busy} onClick={() => onResolve(decision.id, 'approved')} type="button">
                    <Check size={14} />
                    <span>{busy ? 'Working...' : 'Approve'}</span>
                  </button>
                  <button className="ghost-danger" disabled={busy} onClick={() => onResolve(decision.id, 'rejected')} type="button">
                    <X size={14} />
                    <span>{busy ? 'Working...' : 'Reject'}</span>
                  </button>
                </div>
              ) : null}
            </article>
          );
        })}
        {decisions.length === 0 ? <div className="empty-card">No decisions right now.</div> : null}
      </div>
    </section>
  );
}
