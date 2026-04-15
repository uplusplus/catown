import { BookOpen, CheckCircle2, PlayCircle, Scale, X } from 'lucide-react';

type Props = {
  open: boolean;
  onClose: () => void;
};

export function HelpPanel({ open, onClose }: Props) {
  if (!open) return null;

  return (
    <div className="help-overlay" role="dialog" aria-modal="true" aria-label="Mission Board help">
      <section className="help-panel panel-shell">
        <div className="help-header">
          <div>
            <p className="eyebrow">Help</p>
            <h2>How to use Mission Board</h2>
          </div>
          <button className="help-close-button" type="button" onClick={onClose} aria-label="Close help">
            <X size={18} />
          </button>
        </div>

        <div className="help-section">
          <div className="help-section-title">
            <BookOpen size={16} />
            <strong>Core loop</strong>
          </div>
          <ol className="help-list ordered">
            <li>Create or select a project in the left rail.</li>
            <li>Read the Project Hero and Action Focus before acting.</li>
            <li>Resolve any pending decision first.</li>
            <li>Use Continue Project to push the mission forward.</li>
            <li>Inspect stage, assets, and activity after each change.</li>
          </ol>
        </div>

        <div className="help-grid">
          <div className="help-section">
            <div className="help-section-title">
              <Scale size={16} />
              <strong>When to resolve decisions</strong>
            </div>
            <ul className="help-list">
              <li>If Decision Panel shows pending items, open one and approve or reject it.</li>
              <li>Pending decisions can block Continue Project.</li>
              <li>Decision detail in the right rail gives the context you need.</li>
            </ul>
          </div>

          <div className="help-section">
            <div className="help-section-title">
              <PlayCircle size={16} />
              <strong>What Continue Project means</strong>
            </div>
            <ul className="help-list">
              <li>It tells the backend to advance the selected project once.</li>
              <li>It is not a page navigation button.</li>
              <li>After success, the board refreshes overview, stage runs, decisions, and assets.</li>
            </ul>
          </div>
        </div>

        <div className="help-section">
          <div className="help-section-title">
            <CheckCircle2 size={16} />
            <strong>Reading the board</strong>
          </div>
          <ul className="help-list">
            <li><strong>Project Hero</strong> gives project status, focus, stage, and block reason.</li>
            <li><strong>Action Focus</strong> tells you the most useful next move.</li>
            <li><strong>Stage Lane</strong> shows where execution currently is.</li>
            <li><strong>Detail Rail</strong> lets you inspect a selected stage, decision, asset, or event.</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
