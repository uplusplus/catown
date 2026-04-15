import { useCallback, useState } from 'react';

import type { BoardTransitionState } from '../types/board';

const INITIAL_STATE: BoardTransitionState = {
  switchingProject: false,
  switchingStage: false,
};

export function useBoardTransitions() {
  const [state, setState] = useState<BoardTransitionState>(INITIAL_STATE);

  const beginProjectSwitch = useCallback(() => {
    setState((current) => ({ ...current, switchingProject: true }));
  }, []);

  const finishProjectSwitch = useCallback(() => {
    setState((current) => ({ ...current, switchingProject: false }));
  }, []);

  const beginStageSwitch = useCallback(() => {
    setState((current) => ({ ...current, switchingStage: true }));
  }, []);

  const finishStageSwitch = useCallback(() => {
    setState((current) => ({ ...current, switchingStage: false }));
  }, []);

  return {
    ...state,
    beginProjectSwitch,
    finishProjectSwitch,
    beginStageSwitch,
    finishStageSwitch,
  };
}
