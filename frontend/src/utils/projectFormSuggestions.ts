import type { GitHubProjectImportPayload } from "../types";
import { normalizeAgentType } from "./agents";

const PROJECT_FORM_SUGGESTIONS_STORAGE_KEY = "catown:project-form-suggestions";
const MAX_VALUE_HISTORY = 6;
const MAX_AGENT_PRESETS = 4;

export type ProjectFormSuggestionStore = {
  github: {
    repoUrls: string[];
    names: string[];
    descriptions: string[];
    refs: string[];
    agentSets: string[][];
  };
  chat: {
    names: string[];
    descriptions: string[];
    agentSets: string[][];
  };
};

const EMPTY_SUGGESTION_STORE: ProjectFormSuggestionStore = {
  github: {
    repoUrls: [],
    names: [],
    descriptions: [],
    refs: [],
    agentSets: [],
  },
  chat: {
    names: [],
    descriptions: [],
    agentSets: [],
  },
};

function cloneStore(store: ProjectFormSuggestionStore): ProjectFormSuggestionStore {
  return {
    github: {
      repoUrls: [...store.github.repoUrls],
      names: [...store.github.names],
      descriptions: [...store.github.descriptions],
      refs: [...store.github.refs],
      agentSets: store.github.agentSets.map((value) => [...value]),
    },
    chat: {
      names: [...store.chat.names],
      descriptions: [...store.chat.descriptions],
      agentSets: store.chat.agentSets.map((value) => [...value]),
    },
  };
}

function normalizeTextValue(value: string | undefined | null) {
  return (value || "").trim();
}

function pushUniqueValue(values: string[], value: string, limit = MAX_VALUE_HISTORY) {
  const normalized = normalizeTextValue(value);
  if (!normalized) return values;
  return [normalized, ...values.filter((item) => item !== normalized)].slice(0, limit);
}

function normalizeAgentSet(agentNames: string[]) {
  return Array.from(
    new Set(
      agentNames
        .map((name) => normalizeAgentType(name))
        .filter(Boolean),
    ),
  );
}

function pushAgentSet(agentSets: string[][], agentNames: string[], limit = MAX_AGENT_PRESETS) {
  const normalized = normalizeAgentSet(agentNames);
  if (normalized.length === 0) return agentSets;
  const key = normalized.join("\u0001");
  return [normalized, ...agentSets.filter((candidate) => candidate.join("\u0001") !== key)].slice(0, limit);
}

function writeProjectFormSuggestionStore(store: ProjectFormSuggestionStore) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PROJECT_FORM_SUGGESTIONS_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Keep the UI usable even if the browser storage quota is already exhausted.
  }
}

export function readProjectFormSuggestionStore(): ProjectFormSuggestionStore {
  if (typeof window === "undefined") {
    return cloneStore(EMPTY_SUGGESTION_STORE);
  }

  try {
    const raw = window.localStorage.getItem(PROJECT_FORM_SUGGESTIONS_STORAGE_KEY);
    if (!raw) return cloneStore(EMPTY_SUGGESTION_STORE);

    const parsed = JSON.parse(raw) as Partial<ProjectFormSuggestionStore>;
    const nextStore = {
      github: {
        repoUrls: Array.isArray(parsed?.github?.repoUrls) ? parsed.github.repoUrls.filter((value): value is string => typeof value === "string") : [],
        names: Array.isArray(parsed?.github?.names) ? parsed.github.names.filter((value): value is string => typeof value === "string") : [],
        descriptions: Array.isArray(parsed?.github?.descriptions)
          ? parsed.github.descriptions.filter((value): value is string => typeof value === "string")
          : [],
        refs: Array.isArray(parsed?.github?.refs) ? parsed.github.refs.filter((value): value is string => typeof value === "string") : [],
        agentSets: Array.isArray(parsed?.github?.agentSets)
          ? parsed.github.agentSets
              .map((value) => normalizeAgentSet(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []))
              .filter((value) => value.length > 0)
          : [],
      },
      chat: {
        names: Array.isArray(parsed?.chat?.names) ? parsed.chat.names.filter((value): value is string => typeof value === "string") : [],
        descriptions: Array.isArray(parsed?.chat?.descriptions)
          ? parsed.chat.descriptions.filter((value): value is string => typeof value === "string")
          : [],
        agentSets: Array.isArray(parsed?.chat?.agentSets)
          ? parsed.chat.agentSets
              .map((value) => normalizeAgentSet(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []))
              .filter((value) => value.length > 0)
          : [],
      },
    };
    writeProjectFormSuggestionStore(nextStore);
    return nextStore;
  } catch {
    return cloneStore(EMPTY_SUGGESTION_STORE);
  }
}

export function rememberGitHubProjectSuggestion(
  store: ProjectFormSuggestionStore,
  payload: GitHubProjectImportPayload,
) {
  const next = cloneStore(store);
  next.github.repoUrls = pushUniqueValue(next.github.repoUrls, payload.repo_url);
  next.github.names = pushUniqueValue(next.github.names, payload.name);
  next.github.descriptions = pushUniqueValue(next.github.descriptions, payload.description);
  next.github.refs = pushUniqueValue(next.github.refs, payload.ref);
  next.github.agentSets = pushAgentSet(next.github.agentSets, payload.agent_names);
  writeProjectFormSuggestionStore(next);
  return next;
}

export function rememberChatProjectSuggestion(
  store: ProjectFormSuggestionStore,
  payload: {
    name: string;
    description: string;
    agent_names: string[];
  },
) {
  const next = cloneStore(store);
  next.chat.names = pushUniqueValue(next.chat.names, payload.name);
  next.chat.descriptions = pushUniqueValue(next.chat.descriptions, payload.description);
  next.chat.agentSets = pushAgentSet(next.chat.agentSets, payload.agent_names);
  writeProjectFormSuggestionStore(next);
  return next;
}

export function filterTextSuggestions(values: string[], input: string, limit = 4) {
  const normalizedInput = normalizeTextValue(input).toLowerCase();
  const uniqueValues = values
    .map((value) => normalizeTextValue(value))
    .filter(Boolean)
    .filter((value, index, all) => all.indexOf(value) === index);

  if (!normalizedInput) return uniqueValues.slice(0, limit);

  const prefixMatches = uniqueValues.filter(
    (value) => value.toLowerCase().startsWith(normalizedInput) && value.toLowerCase() !== normalizedInput,
  );
  const fuzzyMatches = uniqueValues.filter(
    (value) =>
      !value.toLowerCase().startsWith(normalizedInput) &&
      value.toLowerCase().includes(normalizedInput) &&
      value.toLowerCase() !== normalizedInput,
  );
  return [...prefixMatches, ...fuzzyMatches].slice(0, limit);
}

export function filterAgentSetSuggestions(agentSets: string[][], currentAgentNames: string[], limit = 3) {
  const currentKey = normalizeAgentSet(currentAgentNames).join("\u0001");
  return agentSets
    .filter((agentSet) => agentSet.length > 0)
    .filter((agentSet, index, all) => all.findIndex((candidate) => candidate.join("\u0001") === agentSet.join("\u0001")) === index)
    .filter((agentSet) => agentSet.join("\u0001") !== currentKey)
    .slice(0, limit);
}
