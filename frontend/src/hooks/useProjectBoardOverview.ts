import { useCallback, useState } from 'react';

import {
  continueProject,
  getProjectOverview,
  listProjectAssets,
  listProjectDecisions,
  listProjects,
  listStageRuns,
  resolveDecision,
} from '../api/client';
import type { Asset, Decision, ProjectOverview, StageRun } from '../types';

export function useProjectBoardOverview() {
  const [projects, setProjects] = useState<ProjectOverview['project'][]>([]);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [stageRuns, setStageRuns] = useState<StageRun[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  const resetBoard = useCallback(() => {
    setOverview(null);
    setStageRuns([]);
    setDecisions([]);
    setAssets([]);
  }, []);

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
      return overviewData.current_stage_run?.id ?? stageRunData[0]?.id ?? null;
    } catch (err) {
      resetBoard();
      setError(err instanceof Error ? err.message : 'Failed to load project board');
      return null;
    }
  }, [resetBoard]);

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
    loading,
    error,
    continuing,
    resolvingId,
    loadProjects,
    hydrateProject,
    runContinue,
    runResolve,
    clearBoardError: () => setError(null),
  };
}
