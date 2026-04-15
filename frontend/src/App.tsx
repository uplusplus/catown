import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Compass, Loader } from 'lucide-react';

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
} from './api/client';
import { ActivityFeed } from './components/ActivityFeed';
import { AssetPanel } from './components/AssetPanel';
import { DecisionPanel } from './components/DecisionPanel';
import { DetailRail } from './components/DetailRail';
import { NextActionStrip } from './components/NextActionStrip';
import { ProjectHero } from './components/ProjectHero';
import { ProjectRail } from './components/ProjectRail';
import { StageLane } from './components/StageLane';
import type { Asset, Decision, EventItem, ProjectOverview, StageRun, StageRunDetail } from './types';

function App() {
  const [projects, setProjects] = useState<ProjectOverview['project'][]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [overview, setOverview] = useState<ProjectOverview | null>(null);
  const [stageRuns, setStageRuns] = useState<StageRun[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [stageDetail, setStageDetail] = useState<StageRunDetail | null>(null);
  const [decisionDetail, setDecisionDetail] = useState<Decision | null>(null);
  const [assetDetail, setAssetDetail] = useState<Asset | null>(null);
  const [selectedStageRunId, setSelectedStageRunId] = useState<number | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<EventItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [continuing, setContinuing] = useState(false);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  async function loadProjectsAndSelect() {
    setLoading(true);
    setError(null);
    try {
      const projectList = await listProjects();
      setProjects(projectList);
      const nextProjectId = selectedProjectId ?? projectList[0]?.id ?? null;
      setSelectedProjectId(nextProjectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects');
    } finally {
      setLoading(false);
    }
  }

  async function hydrateProject(projectId: number) {
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

      const preferredStageId = overviewData.current_stage_run?.id ?? stageRunData[0]?.id ?? null;
      setSelectedStageRunId(preferredStageId);
      setDecisionDetail(null);
      setAssetDetail(null);
      setSelectedEvent(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load project board');
    }
  }

  useEffect(() => {
    void loadProjectsAndSelect();
  }, []);

  useEffect(() => {
    if (selectedProjectId == null) return;
    void hydrateProject(selectedProjectId);
  }, [selectedProjectId]);

  useEffect(() => {
    if (selectedStageRunId == null) {
      setStageDetail(null);
      setEvents([]);
      return;
    }

    let cancelled = false;
    async function hydrateStage(stageRunId: number) {
      try {
        const [detailData, eventData] = await Promise.all([getStageRun(stageRunId), listStageEvents(stageRunId)]);
        if (cancelled) return;
        setStageDetail(detailData);
        setEvents(eventData);
        setSelectedEvent(eventData[0] ?? null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Failed to load stage detail');
      }
    }

    void hydrateStage(selectedStageRunId);
    return () => {
      cancelled = true;
    };
  }, [selectedStageRunId]);

  const boardReady = overview && selectedProjectId != null;

  const detailProps = useMemo(
    () => ({
      stageDetail,
      decisionDetail,
      assetDetail,
      selectedEvent,
    }),
    [assetDetail, decisionDetail, selectedEvent, stageDetail],
  );

  async function handleContinue() {
    if (selectedProjectId == null) return;
    setContinuing(true);
    try {
      await continueProject(selectedProjectId);
      await hydrateProject(selectedProjectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to continue project');
    } finally {
      setContinuing(false);
    }
  }

  async function handleResolve(decisionId: number, resolution: 'approved' | 'rejected') {
    setResolvingId(decisionId);
    try {
      await resolveDecision(decisionId, {
        resolution,
        selected_option: resolution === 'approved' ? 'approve' : 'reject',
      });
      if (selectedProjectId != null) {
        await hydrateProject(selectedProjectId);
      }
      setDecisionDetail(await getDecision(decisionId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resolve decision');
    } finally {
      setResolvingId(null);
    }
  }

  async function handleSelectDecision(decisionId: number) {
    try {
      setDecisionDetail(await getDecision(decisionId));
      setAssetDetail(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load decision detail');
    }
  }

  async function handleSelectAsset(assetId: number) {
    try {
      setAssetDetail(await getAsset(assetId));
      setDecisionDetail(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load asset detail');
    }
  }

  return (
    <div className="app-shell">
      <div className="app-topbar">
        <div>
          <p className="eyebrow">Catown Command Surface</p>
          <h1>Project-first Mission Board</h1>
        </div>
        <div className="topbar-badge">
          <Compass size={16} />
          <span>React/Vite frontend reset</span>
        </div>
      </div>

      {loading ? (
        <div className="loading-state panel-shell">
          <Loader className="spin" size={22} />
          <span>Loading projects...</span>
        </div>
      ) : null}

      {error ? (
        <div className="error-banner">
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="board-layout">
        <ProjectRail projects={projects} selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} />

        <main className="main-board">
          {boardReady && overview ? (
            <>
              <ProjectHero overview={overview} onContinue={handleContinue} continuing={continuing} />
              <NextActionStrip action={overview.recommended_next_action} />
              <StageLane stageRuns={stageRuns} selectedStageRunId={selectedStageRunId} onSelect={setSelectedStageRunId} />
              <section className="board-row two-up">
                <DecisionPanel
                  decisions={decisions}
                  onSelect={handleSelectDecision}
                  onResolve={handleResolve}
                  resolvingId={resolvingId}
                />
                <AssetPanel assets={assets} onSelect={handleSelectAsset} />
              </section>
              <ActivityFeed events={events} />
            </>
          ) : (
            <section className="panel-shell empty-board">
              <h2>No mission selected</h2>
              <p>Create or select a project to populate the board.</p>
            </section>
          )}
        </main>

        <DetailRail {...detailProps} />
      </div>
    </div>
  );
}

export default App;
