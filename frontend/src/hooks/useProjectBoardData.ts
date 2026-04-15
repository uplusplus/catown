import { useCallback } from 'react';

import { useProjectBoardDetail } from './useProjectBoardDetail';
import { useProjectBoardOverview } from './useProjectBoardOverview';

export function useProjectBoardData() {
  const overview = useProjectBoardOverview();
  const detail = useProjectBoardDetail();
  const { hydrateProject: hydrateOverviewProject } = overview;
  const { resetForProject } = detail;

  const hydrateProject = useCallback(async (projectId: number) => {
    const preferredStageId = await hydrateOverviewProject(projectId);
    resetForProject();
    return preferredStageId;
  }, [hydrateOverviewProject, resetForProject]);

  return {
    ...overview,
    ...detail,
    error: overview.error,
    detailError: detail.detailError,
    hydrateProject,
  };
}
