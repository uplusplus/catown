import { useCallback, useState } from 'react';

import {
  continueProject,
  getAsset,
  getDecision,
  getProjectOverview,
  getStageRun,
  listProjectAssets,
  listProjectDecisions,
  listProjects,
  listStageEvents,
  listStageRuns,
  resolveDecision,
} from '../api/client';
import type { Asset, Decision, EventItem, ProjectOverview, StageRun, StageRunDetail } from '../types';

export function useProjectBoardData() {
  const [projects, setProjects] = useState<ProjectOverview['project'][]>([]);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [stageRuns, setStageRuns] = useState<StageRun[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [stageDetail, setStageDetail] = useState<StageRunDetail | null>(null);
  const [decisionDetail, setDecisionDetail] = useState<Decision | null>(null);
  const [assetDetail, setAssetDetail] = useState<Asset | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const projectList = await listProjects();
      setProjects(projectList);
      return projectList;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects');
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const hydrateProject = useCallback(async (projectId: number) => {
    setError(null);
    try {
      const [overviewData, stageRunData, decisionData, assetData] = await Promise.all([
        getProjectOverview(projectId),
        listStageRuns(projectId),
        listProjectDecisions(projectId),
        listProjectAssets(projectId),
      ]);
      setOverview(overviewData);
      setStageRuns(stageRunData);
      setDecisions(decisionData);
      setAssets(assetData);
      setDecisionDetail(null);
      setAssetDetail(null);

      return overviewData.current_stage_run?.id ?? stageRunData[0]?.id ?? null;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load project board');
      return null;
    }
  }, []);

  const hydrateStage = useCallback(async (stageRunId: number) => {
    try {
      const [detailData, eventData] = await Promise.all([getStageRun(stageRunId), listStageEvents(stageRunId)]);
      setStageDetail(detailData);
      setEvents(eventData);
      return eventData;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load stage detail');
      return [];
    }
  }, []);

  const clearStageDetail = useCallback(() => {
    setStageDetail(null);
    setEvents([]);
  }, []);

  const loadDecision = useCallback(async (decisionId: number) => {
    try {
      const detail = await getDecision(decisionId);
      setDecisionDetail(detail);
      setAssetDetail(null);
      return detail;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load decision detail');
      return null;
    }
  }, []);

  const loadAsset = useCallback(async (assetId: number) => {
    try {
      const detail = await getAsset(assetId);
      setAssetDetail(detail);
      setDecisionDetail(null);
      return detail;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load asset detail');
      return null;
    }
  }, []);

  const runContinue = useCallback(async (projectId: number) => {
    setContinuing(true);
    try {
      await continueProject(projectId);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to continue project');
      return false;
    } finally {
      setContinuing(false);
    }
  }, []);

  const runResolve = useCallback(async (decisionId: number, resolution: 'approved' | 'rejected') => {
    setResolvingId(decisionId);
    try {
      await resolveDecision(decisionId, {
        resolution,
        selected_option: resolution === 'approved' ? 'approve' : 'reject',
      });
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resolve decision');
      return false;
    } finally {
      setResolvingId(null);
    }
  }, []);

  return {
    projects,
    overview,
    stageRuns,
    decisions,
    assets,
    events,
    stageDetail,
    decisionDetail,
    assetDetail,
    loading,
    error,
    continuing,
    resolvingId,
    loadProjects,
    hydrateProject,
    hydrateStage,
    clearStageDetail,
    loadDecision,
    loadAsset,
    runContinue,
    runResolve,
  };
}
