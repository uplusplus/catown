import type { CSSProperties } from "react";
import type { AgentInfo } from "../types";
import { DEFAULT_AGENT_TYPE, defaultAgentName, findAgentByType, getAgentDisplayName, normalizeAgentType } from "./agents";

const FIXED_AGENT_COLORS: Record<string, string> = {
  analyst: "#4fd1c5",
  architect: "#60a5fa",
  developer: "#f97316",
  tester: "#a3e635",
  release: "#f472b6",
  valet: "#facc15",
};

const HASH_FALLBACK_COLORS = [
  "#4fd1c5",
  "#60a5fa",
  "#818cf8",
  "#c084fc",
  "#f472b6",
  "#fb7185",
  "#f97316",
  "#f59e0b",
  "#facc15",
  "#84cc16",
  "#22c55e",
  "#2dd4bf",
];

export type AgentTheme = {
  agentKey: string;
  agentLabel: string;
  accent: string;
  accentSoft: string;
  accentMuted: string;
  text: string;
};

function hashString(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash) + value.charCodeAt(index);
    hash |= 0;
  }
  return Math.abs(hash);
}

function fallbackAccentForAgent(agentKey: string) {
  return HASH_FALLBACK_COLORS[hashString(agentKey) % HASH_FALLBACK_COLORS.length];
}

export function resolveAgentKey(agentName?: string | null, agents: AgentInfo[] = []) {
  const raw = (agentName || "").trim();
  if (!raw) return "";

  const normalized = normalizeAgentType(raw);
  const matched = findAgentByType(agents, raw) ?? agents.find((agent) => {
    const display = getAgentDisplayName(agent).trim().toLowerCase();
    return display === raw.toLowerCase();
  }) ?? null;

  return matched ? normalizeAgentType(matched.type || matched.name) : normalized;
}

export function resolveAgentLabel(agentName?: string | null, agents: AgentInfo[] = []) {
  const raw = (agentName || "").trim();
  if (!raw) return defaultAgentName(DEFAULT_AGENT_TYPE);
  const matched = findAgentByType(agents, raw) ?? agents.find((agent) => {
    const display = getAgentDisplayName(agent).trim().toLowerCase();
    return display === raw.toLowerCase();
  }) ?? null;
  return matched ? getAgentDisplayName(matched) : raw;
}

export function getAgentTheme(agentName?: string | null, agents: AgentInfo[] = []): AgentTheme | null {
  const agentKey = resolveAgentKey(agentName, agents);
  if (!agentKey) return null;

  const accent = FIXED_AGENT_COLORS[agentKey] ?? fallbackAccentForAgent(agentKey);
  return {
    agentKey,
    agentLabel: resolveAgentLabel(agentName, agents),
    accent,
    accentSoft: `color-mix(in srgb, ${accent} 12%, transparent)`,
    accentMuted: `color-mix(in srgb, ${accent} 32%, var(--border) 68%)`,
    text: `color-mix(in srgb, ${accent} 76%, white 24%)`,
  };
}

export function buildAgentThemeStyle(agentName?: string | null, agents: AgentInfo[] = []) {
  const theme = getAgentTheme(agentName, agents);
  if (!theme) return undefined;
  return {
    "--agent-accent": theme.accent,
    "--agent-accent-soft": theme.accentSoft,
    "--agent-accent-muted": theme.accentMuted,
    "--agent-text": theme.text,
  } as CSSProperties;
}
