import { Activity, Bot } from 'lucide-react';

import { formatRelative, titleize, truncate } from '../lib/format';
import type { EventItem } from '../types';

type Props = {
  events: EventItem[];
  selectedEventId: number | null;
  onSelect: (event: EventItem) => void;
};

export function ActivityFeed({ events, selectedEventId, onSelect }: Props) {
  return (
    <section className="panel-shell activity-feed">
      <div className="section-header">
        <div>
          <p className="eyebrow">Live Context</p>
          <h3>Recent Activity</h3>
        </div>
        <span className="section-stat">{events.length} items</span>
      </div>
      <div className="event-list">
        {events.map((event) => {
          const selected = event.id === selectedEventId;
          return (
            <button
              key={event.id}
              className={`event-card ${selected ? 'is-active' : ''}`}
              onClick={() => onSelect(event)}
              type="button"
            >
              <div className="event-card-top">
                <span className="event-icon">
                  <Activity size={14} />
                </span>
                <strong>{truncate(event.summary, 80) || titleize(event.event_type)}</strong>
                <span className="event-type">{titleize(event.event_type)}</span>
              </div>
              <div className="event-card-meta">
                <span>{event.stage_name ? titleize(event.stage_name) : 'No stage name'}</span>
                <span>{event.agent_name ? <><Bot size={13} /> {event.agent_name}</> : 'System'}</span>
                <span>{formatRelative(event.created_at)}</span>
              </div>
            </button>
          );
        })}
        {events.length === 0 ? <div className="empty-card">No events for this stage yet.</div> : null}
      </div>
    </section>
  );
}
