import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Compass, Loader } from 'lucide-react';

import { ActivityFeed } from './components/ActivityFeed';
import { AssetPanel } from './components/AssetPanel';
import { DecisionPanel } from './components/DecisionPanel';
import { DetailRail } from './components/DetailRail';
import { NextActionStrip } from './components/NextActionStrip';
import { ProjectHero } from './components/ProjectHero';
import { ProjectRail } from './components/ProjectRail';
import { StageLane } from './components/StageLane';
import { useBoardSelection } from './hooks/useBoardSelection';
import { useProjectBoardData } from './hooks/useProjectBoardData';
import type { EventItem } from './types';

function App() {
  const board = useProjectBoardData();
  const selection = useBoardSelection();
  const [detailLoading, setDetailLoading] = useState<'decision' | 'asset' | null>(null);
  const [notice, setNotice] = useState<{ tone: 'success' | 'info'; message: string } | null>(null);

  const {
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
  } = board;

  const {
    selectedProjectId,
    selectedStageRunId,
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
  } = selection;

  async function loadProjectsAndSelect() {
    const projectList = await loadProjects();
    const nextProjectId = selectedProjectId ?? projectList[0]?.id ?? null;
    setProject(nextProjectId);
  }

  useEffect(() => {
    void loadProjectsAndSelect();
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2600);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (selectedProjectId == null) return;

    let cancelled = false;
    async function loadProjectBoard(projectId: number) {
      const preferredStageId = await hydrateProject(projectId);
      if (cancelled) return;
      resetForProject(preferredStageId);
    }

    void loadProjectBoard(selectedProjectId);
    return () => {
      cancelled = true;
    };
  }, [hydrateProject, resetForProject, selectedProjectId]);

  useEffect(() => {
    if (selectedStageRunId == null) {
      clearStageDetail();
      return;
    }

    let cancelled = false;
    async function loadStageBoard(stageRunId: number) {
      const eventData = await hydrateStage(stageRunId);
      if (cancelled) return;
      syncSelectedEvent(eventData);
    }

    void loadStageBoard(selectedStageRunId);
    return () => {
      cancelled = true;
    };
  }, [clearStageDetail, hydrateStage, selectedStageRunId, syncSelectedEvent]);

  const boardReady = overview && selectedProjectId != null;

  const detailProps = useMemo(
    () => ({
      focus: detailFocus,
      stageDetail,
      decisionDetail,
      assetDetail,
      selectedEvent,
      onSelectDecision: handleSelectDecision,
      onSelectAsset: handleSelectAsset,
    }),
    [assetDetail, decisionDetail, detailFocus, selectedEvent, stageDetail],
  );

  async function handleContinue() {
    if (selectedProjectId == null) return;
    const ok = await runContinue(selectedProjectId);
    if (!ok) return;

    const preferredStageId = await hydrateProject(selectedProjectId);
    resetForProject(preferredStageId);
    setDetailFocus('stage');
    setNotice({ tone: 'success', message: 'Project advanced. Mission board refreshed.' });
  }

  async function handleResolve(decisionId: number, resolution: 'approved' | 'rejected') {
    const ok = await runResolve(decisionId, resolution);
    if (!ok) return;

    if (selectedProjectId != null) {
      const preferredStageId = await hydrateProject(selectedProjectId);
      resetForProject(preferredStageId);
    }
    await loadDecision(decisionId);
    setDecision(decisionId);
    setNotice({
      tone: 'success',
      message: resolution === 'approved' ? 'Decision approved and board refreshed.' : 'Decision rejected and board refreshed.',
    });
  }

  async function handleSelectDecision(decisionId: number) {
    setDetailLoading('decision');
    try {
      const detail = await loadDecision(decisionId);
      if (!detail) return;
      setDecision(decisionId);
    } finally {
      setDetailLoading(null);
    }
  }

  async function handleSelectAsset(assetId: number) {
    setDetailLoading('asset');
    try {
      const detail = await loadAsset(assetId);
      if (!detail) return;
      setAsset(assetId);
    } finally {
      setDetailLoading(null);
    }
  }

  function handleSelectStage(stageRunId: number) {
    setStage(stageRunId);
  }

  function handleSelectEvent(event: EventItem) {
    setEvent(event);
  }

  const boardBusy = continuing || resolvingId != null;

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

      {notice ? (
        <div className={`notice-banner is-${notice.tone}`}>
          <CheckCircle2 size={18} />
          <span>{notice.message}</span>
        </div>
      ) : null}

      <div className="board-layout">
        <ProjectRail projects={projects} selectedProjectId={selectedProjectId} onSelect={setProject} />

        <main className={`main-board ${boardBusy ? 'is-busy' : ''}`}>
          {boardReady && overview ? (
            <>
              <ProjectHero overview={overview} onContinue={handleContinue} continuing={continuing} />
              <NextActionStrip action={overview.recommended_next_action} />
              <StageLane stageRuns={stageRuns} selectedStageRunId={selectedStageRunId} onSelect={handleSelectStage} />
              <section className="board-row two-up">
                <DecisionPanel
                  decisions={decisions}
                  selectedDecisionId={selectedIds.decisionId}
                  onSelect={handleSelectDecision}
                  onResolve={handleResolve}
                  resolvingId={resolvingId}
                />
                <AssetPanel
                  assets={assets}
                  selectedAssetId={selectedIds.assetId}
                  onSelect={handleSelectAsset}
                />
              </section>
              <ActivityFeed events={events} selectedEventId={selectedIds.eventId} onSelect={handleSelectEvent} />
            </>
          ) : (
            <section className="panel-shell empty-board">
              <h2>No mission selected</h2>
              <p>Create or select a project to populate the board.</p>
            </section>
          )}
        </main>

        <DetailRail {...detailProps} loading={detailLoading} />
      </div>
    </div>
  );
}

export default App;
