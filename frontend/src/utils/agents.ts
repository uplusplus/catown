import type { AgentInfo } from "../types";

export const DEFAULT_AGENT_TYPE = "valet";
const LEGACY_AGENT_TYPE_ALIASES: Record<string, string> = {
  assistant: DEFAULT_AGENT_TYPE,
  bot: DEFAULT_AGENT_TYPE,
  arch: "architect",
  dev: "developer",
  qa: "tester",
  rel: "release",
};

export function normalizeAgentType(value?: string | null) {
  const raw = (value ?? "").trim();
  if (!raw) return DEFAULT_AGENT_TYPE;
  const normalized = raw.toLowerCase();
  return LEGACY_AGENT_TYPE_ALIASES[normalized] ?? normalized;
}

export function defaultAgentName(agentType = DEFAULT_AGENT_TYPE) {
  const normalized = normalizeAgentType(agentType);
  return normalized ? `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}` : "Agent";
}

export function getAgentType(agent?: Pick<AgentInfo, "type" | "name"> | null) {
  return normalizeAgentType(agent?.type ?? agent?.name);
}

export function getAgentDisplayName(agent?: Pick<AgentInfo, "type" | "name"> | null) {
  const name = agent?.name?.trim();
  return name || defaultAgentName(getAgentType(agent));
}

export function isAgentType(agent: Pick<AgentInfo, "type" | "name"> | null | undefined, type: string) {
  return getAgentType(agent) === normalizeAgentType(type);
}

export function findAgentByType<T extends Pick<AgentInfo, "type" | "name">>(
  agents: readonly T[],
  type: string,
) {
  const normalized = normalizeAgentType(type);
  return agents.find((agent) => getAgentType(agent) === normalized) ?? null;
}
