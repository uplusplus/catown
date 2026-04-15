import axios from 'axios';
import type {
  Asset,
  Decision,
  DecisionResolveResponse,
  EventItem,
  Project,
  ProjectOverview,
  StageRun,
  StageRunDetail,
} from '../types';

const api = axios.create({
  baseURL: '/api/v2',
  timeout: 15000,
});

export async function createProject(payload: {
  name: string;
  one_line_vision?: string;
  description?: string;
}): Promise<{ project: Project; next_action?: string | null }> {
  const { data } = await api.post<{ project: Project; next_action?: string | null }>('/projects', payload);
  return data;
}

export async function listProjects(): Promise<Project[]> {
  const { data } = await api.get<Project[]>('/projects');
  return data;
}

export async function getProjectOverview(projectId: number): Promise<ProjectOverview> {
  const { data } = await api.get<ProjectOverview>(`/projects/${projectId}/overview`);
  return data;
}

export async function listStageRuns(projectId: number): Promise<StageRun[]> {
  const { data } = await api.get<StageRun[]>(`/projects/${projectId}/stage-runs`);
  return data;
}

export async function getStageRun(stageRunId: number): Promise<StageRunDetail> {
  const { data } = await api.get<StageRunDetail>(`/stage-runs/${stageRunId}`);
  return data;
}

export async function listStageEvents(stageRunId: number): Promise<EventItem[]> {
  const { data } = await api.get<EventItem[]>(`/stage-runs/${stageRunId}/events`);
  return data;
}

export async function listProjectDecisions(projectId: number): Promise<Decision[]> {
  const { data } = await api.get<Decision[]>(`/projects/${projectId}/decisions`);
  return data;
}

export async function getDecision(decisionId: number): Promise<Decision> {
  const { data } = await api.get<Decision>(`/decisions/${decisionId}`);
  return data;
}

export async function resolveDecision(
  decisionId: number,
  payload: { resolution: string; selected_option?: string; note?: string },
): Promise<DecisionResolveResponse> {
  const { data } = await api.post<DecisionResolveResponse>(`/decisions/${decisionId}/resolve`, payload);
  return data;
}

export async function listProjectAssets(projectId: number): Promise<Asset[]> {
  const { data } = await api.get<Asset[]>(`/projects/${projectId}/assets`);
  return data;
}

export async function getAsset(assetId: number): Promise<Asset> {
  const { data } = await api.get<Asset>(`/assets/${assetId}`);
  return data;
}

export async function continueProject(projectId: number): Promise<Record<string, unknown>> {
  const { data } = await api.post<Record<string, unknown>>(`/projects/${projectId}/continue`);
  return data;
}
