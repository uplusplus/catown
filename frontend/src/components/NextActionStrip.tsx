import { ArrowRight, Sparkles } from 'lucide-react';

import { titleize } from '../lib/format';

type Props = {
  action: string;
};

const LABELS: Record<string, string> = {
  continue_project: 'Push the project into its next stage.',
  review_current_stage: 'Inspect the current stage before acting.',
  review_prd: 'Review the latest PRD output.',
  review_definition_bundle: 'Inspect the full definition bundle.',
  review_task_plan: 'Check the generated execution plan.',
  review_test_report: 'Inspect the test report before release.',
  review_release_pack: 'Open the release pack for final checks.',
  resolve_scope_confirmation: 'A scope decision needs your approval.',
  resolve_direction_confirmation: 'A direction choice is waiting on you.',
  resolve_release_approval: 'The release gate is waiting on you.',
  resolve_decision: 'A decision is waiting on you.',
  review_project: 'Review the current project state.',
};

export function NextActionStrip({ action }: Props) {
  return (
    <section className="next-action panel-shell">
      <div className="next-action-copy">
        <p className="eyebrow">Recommended Next Move</p>
        <h3>{titleize(action)}</h3>
        <p>{LABELS[action] || 'Inspect the board and decide the next move.'}</p>
      </div>
      <div className="next-action-icon">
        <Sparkles size={20} />
        <ArrowRight size={20} />
      </div>
    </section>
  );
}
