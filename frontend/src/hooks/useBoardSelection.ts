import { useCallback, useMemo, useState } from 'react';

import type { EventItem } from '../types';
import type { DetailFocus } from '../components/DetailRail';

type Params = {
  decisionId: number | null;
  assetId: number | null;
  eventId: number | null;
};

export function useBoardSelection() {
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [selectedStageRunId, setSelectedStageRunId] = useState<number | null>(null);
  const [selectedDecisionId, setSelectedDecisionId] = useState<number | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState<number | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventItem | null>(null);
  const [detailFocus, setDetailFocus] = useState<DetailFocus>('stage');

  const resetDetailSelection = useCallback(() => {
    setSelectedDecisionId(null);
    setSelectedAssetId(null);
    setSelectedEvent(null);
  }, []);

  const setProject = useCallback((projectId: number | null) => {
    setSelectedProjectId(projectId);
  }, []);

  const setStage = useCallback(
    (stageRunId: number | null) => {
      setSelectedStageRunId(stageRunId);
      resetDetailSelection();
      setDetailFocus('stage');
    },
    [resetDetailSelection],
  );

  const setDecision = useCallback((decisionId: number) => {
    setSelectedDecisionId(decisionId);
    setSelectedAssetId(null);
    setSelectedEvent(null);
    setDetailFocus('decision');
  }, []);

  const setAsset = useCallback((assetId: number) => {
    setSelectedAssetId(assetId);
    setSelectedDecisionId(null);
    setSelectedEvent(null);
    setDetailFocus('asset');
  }, []);

  const setEvent = useCallback((event: EventItem) => {
    setSelectedEvent(event);
    setSelectedDecisionId(null);
    setSelectedAssetId(null);
    setDetailFocus('event');
  }, []);

  const resetForProject = useCallback(
    (preferredStageId: number | null) => {
      setSelectedStageRunId(preferredStageId);
      resetDetailSelection();
      setDetailFocus('stage');
    },
    [resetDetailSelection],
  );

  const syncSelectedEvent = useCallback((events: EventItem[]) => {
    setSelectedEvent((current) => events.find((item) => item.id === current?.id) ?? eventIdFallback(events));
  }, []);

  const selectedIds = useMemo<Params>(
    () => ({
      decisionId: detailFocus === 'decision' ? selectedDecisionId : null,
      assetId: detailFocus === 'asset' ? selectedAssetId : null,
      eventId: detailFocus === 'event' ? selectedEvent?.id ?? null : null,
    }),
    [detailFocus, selectedAssetId, selectedDecisionId, selectedEvent],
  );

  return {
    selectedProjectId,
    selectedStageRunId,
    selectedDecisionId,
    selectedAssetId,
    selectedEvent,
    detailFocus,
    selectedIds,
    setProject,
    setStage,
    setDecision,
    setAsset,
    setEvent,
    resetForProject,
    syncSelectedEvent,
    setDetailFocus,
  };
}

function eventIdFallback(events: EventItem[]) {
  return events[0] ?? null;
}
