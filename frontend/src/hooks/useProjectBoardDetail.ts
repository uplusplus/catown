import { useCallback, useState } from 'react';

import { getAsset, getDecision, getStageRun, listStageEvents } from '../api/client';
import type { Asset, Decision, EventItem, StageRunDetail } from '../types';

export function useProjectBoardDetail() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [stageDetail, setStageDetail] = useState<StageRunDetail | null>(null);
  const [decisionDetail, setDecisionDetail] = useState<Decision | null>(null);
  const [assetDetail, setAssetDetail] = useState<Asset | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const clearDetailError = useCallback(() => {
    setDetailError(null);
  }, []);

  const clearAllDetail = useCallback(() => {
    setEvents([]);
    setStageDetail(null);
    setDecisionDetail(null);
    setAssetDetail(null);
  }, []);

  const clearStageDetail = useCallback(() => {
    setStageDetail(null);
    setEvents([]);
  }, []);

  const resetForProject = useCallback(() => {
    clearAllDetail();
    clearDetailError();
  }, [clearAllDetail, clearDetailError]);

  const hydrateStage = useCallback(async (stageRunId: number) => {
    clearDetailError();
    try {
      const [detailData, eventData] = await Promise.all([getStageRun(stageRunId), listStageEvents(stageRunId)]);
      setStageDetail(detailData);
      setEvents(eventData);
      return eventData;
    } catch (err) {
      setStageDetail(null);
      setEvents([]);
      setDetailError(err instanceof Error ? err.message : 'Failed to load stage detail');
      return [];
    }
  }, [clearDetailError]);

  const loadDecision = useCallback(async (decisionId: number) => {
    clearDetailError();
    try {
      const detail = await getDecision(decisionId);
      setDecisionDetail(detail);
      setAssetDetail(null);
      return detail;
    } catch (err) {
      setDecisionDetail(null);
      setDetailError(err instanceof Error ? err.message : 'Failed to load decision detail');
      return null;
    }
  }, [clearDetailError]);

  const loadAsset = useCallback(async (assetId: number) => {
    clearDetailError();
    try {
      const detail = await getAsset(assetId);
      setAssetDetail(detail);
      setDecisionDetail(null);
      return detail;
    } catch (err) {
      setAssetDetail(null);
      setDetailError(err instanceof Error ? err.message : 'Failed to load asset detail');
      return null;
    }
  }, [clearDetailError]);

  return {
    events,
    stageDetail,
    decisionDetail,
    assetDetail,
    detailError,
    hydrateStage,
    clearStageDetail,
    resetForProject,
    loadDecision,
    loadAsset,
    clearDetailError,
  };
}
