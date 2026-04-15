import { ArrowRight, Sparkles } from 'lucide-react';

import { titleize } from '../lib/format';

type Props = {
  action: string;
  blockingReason?: string | null;
};

type ActionCopy = {
  kicker: string;
  description: string;
};

const LABELS: Record<string, ActionCopy> = {
  continue_project: {
    kicker: 'Execution can keep moving.',
    description: 'Push the mission into its next stage when the board looks aligned.',
  },
  review_current_stage: {
    kicker: 'Current stage needs operator eyes.',
    description: 'Inspect the active stage, its outputs, and the event trail before taking action.',
  },
  review_prd: {
    kicker: 'PRD output is ready for review.',
    description: 'Check the latest PRD draft and decide whether it is strong enough to keep momentum.',
  },
  review_definition_bundle: {
    kicker: 'Definition artifacts are ready.',
    description: 'Inspect the bundle for scope clarity, missing constraints, and follow-up decisions.',
  },
  review_task_plan: {
    kicker: 'Execution planning is on deck.',
    description: 'Validate the generated task plan before the team burns time building the wrong thing.',
  },
  review_test_report: {
    kicker: 'Quality signals need review.',
    description: 'Read the test report and decide whether the mission is safe to advance.',
  },
  review_release_pack: {
    kicker: 'Release material is assembled.',
    description: 'Open the release pack and confirm everything needed for shipment is present.',
  },
  resolve_scope_confirmation: {
    kicker: 'A scope call is blocking progress.',
    description: 'Resolve the pending scope decision so the mission can move out of bootstrap.',
  },
  resolve_direction_confirmation: {
    kicker: 'A direction choice needs your call.',
    description: 'Pick the recommended direction or reject it before the board commits more work.',
  },
  resolve_release_approval: {
    kicker: 'Release approval is the last hard gate.',
    description: 'Approve or reject the release decision after checking the pack and risk signals.',
  },
  resolve_decision: {
    kicker: 'A decision is waiting on you.',
    description: 'Open the decision card, inspect context and impact, then resolve it clearly.',
  },
  review_project: {
    kicker: 'Take a board-wide pass.',
    description: 'Scan the mission state, activity, and artifacts before deciding the next move.',
  },
};

export function NextActionStrip({ action, blockingReason }: Props) {
  const copy = LABELS[action] || {
    kicker: 'Decide the next move.',
    description: 'Inspect the board state and choose the most useful intervention.',
  };

  return (
    <section className="next-action panel-shell">
      <div className="next-action-copy">
        <p className="eyebrow">Action Focus</p>
        <h3>{copy.kicker}</h3>
        <p>{copy.description}</p>
        <div className="next-action-meta">
          <span className="pill muted">
            <Sparkles size={14} />
            {titleize(action)}
          </span>
          {blockingReason ? <span className="pill warning-pill">Blocked: {blockingReason}</span> : null}
        </div>
      </div>
      <div className="next-action-icon">
        <Sparkles size={20} />
        <ArrowRight size={20} />
      </div>
    </section>
  );
}
