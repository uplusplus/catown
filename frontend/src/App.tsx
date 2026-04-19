import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api/client";
import { AppSidebar } from "./components/AppSidebar";
import { ChatTab } from "./components/ChatTab";
import { ConfigTab } from "./components/ConfigTab";
import { ProjectsTab } from "./components/ProjectsTab";
import type {
  AppTab,
  AgentInfo,
  ChatCardItem,
  ChatEventItem,
  ChatEventTone,
  ChatSummary,
  ConfigResponse,
  MessageItem,
  MessageStreamStep,
  ProjectSummary,
} from "./types";

const LAST_CHAT_STORAGE_KEY = "catown:last-chat-id";
const OPTIMISTIC_MESSAGES_STORAGE_KEY = "catown:optimistic-messages";
const STREAM_STEP_LIMIT = 8;

function readLastChatId(): number | null {
  if (typeof window === "undefined") return null;
  const rawValue = window.localStorage.getItem(LAST_CHAT_STORAGE_KEY);
  if (!rawValue) return null;

  const parsed = Number.parseInt(rawValue, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function writeLastChatId(chatId: number | null) {
  if (typeof window === "undefined") return;

  if (chatId === null) {
    window.localStorage.removeItem(LAST_CHAT_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(LAST_CHAT_STORAGE_KEY, String(chatId));
}

function optimisticScopeKey(chatId: number | null) {
  return chatId === null ? "pending" : `chat:${chatId}`;
}

function readOptimisticMessageStore() {
  if (typeof window === "undefined") return {} as Record<string, MessageItem[]>;
  try {
    const rawValue = window.localStorage.getItem(OPTIMISTIC_MESSAGES_STORAGE_KEY);
    if (!rawValue) return {};
    const parsed = JSON.parse(rawValue) as Record<string, MessageItem[]>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeOptimisticMessageStore(store: Record<string, MessageItem[]>) {
  if (typeof window === "undefined") return;
  const nextEntries = Object.entries(store).filter(([, value]) => Array.isArray(value) && value.length > 0);
  if (nextEntries.length === 0) {
    window.localStorage.removeItem(OPTIMISTIC_MESSAGES_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(OPTIMISTIC_MESSAGES_STORAGE_KEY, JSON.stringify(Object.fromEntries(nextEntries)));
}

function readOptimisticMessages(chatId: number | null) {
  const store = readOptimisticMessageStore();
  return store[optimisticScopeKey(chatId)] ?? [];
}

function writeOptimisticMessages(chatId: number | null, messages: MessageItem[]) {
  const store = readOptimisticMessageStore();
  const key = optimisticScopeKey(chatId);
  if (messages.length === 0) {
    delete store[key];
  } else {
    store[key] = messages;
  }
  writeOptimisticMessageStore(store);
}

function migrateOptimisticMessages(fromChatId: number | null, toChatId: number | null) {
  const fromKey = optimisticScopeKey(fromChatId);
  const toKey = optimisticScopeKey(toChatId);
  if (fromKey === toKey) return;

  const store = readOptimisticMessageStore();
  const fromMessages = store[fromKey] ?? [];
  if (fromMessages.length === 0) return;

  const targetMessages = store[toKey] ?? [];
  store[toKey] = mergeMessages(targetMessages, fromMessages);
  delete store[fromKey];
  writeOptimisticMessageStore(store);
}

function parseOptimisticScopeChatId(scope: string) {
  if (scope === "pending") return null;
  const parsed = Number.parseInt(scope.replace("chat:", ""), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function buildInitialChatTitle(content: string) {
  const normalized = content.trim().replace(/\s+/g, " ");
  if (!normalized) return "New Chat";
  return normalized.length > 32 ? `${normalized.slice(0, 32)}...` : normalized;
}

function resolveRestoredSelection(
  projects: ProjectSummary[],
  chats: ChatSummary[],
  preferredChatId: number | null,
) {
  if (preferredChatId !== null) {
    const matchedProject = projects.find((project) => project.default_chatroom_id === preferredChatId) ?? null;
    if (matchedProject) {
      return {
        projectId: matchedProject.id,
        chatId: matchedProject.default_chatroom_id,
      };
    }

    const matchedChat = chats.find((chat) => chat.id === preferredChatId) ?? null;
    if (matchedChat) {
      return {
        projectId: matchedChat.project_id ?? null,
        chatId: matchedChat.id,
      };
    }
  }

  const fallbackProject = projects[0] ?? null;
  if (fallbackProject) {
    return {
      projectId: fallbackProject.id,
      chatId: fallbackProject.default_chatroom_id,
    };
  }

  const fallbackChat = chats[0] ?? null;
  return {
    projectId: fallbackChat?.project_id ?? null,
    chatId: fallbackChat?.id ?? null,
  };
}

function buildEvent(message: string, tone: ChatEventTone = "info"): ChatEventItem {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    message,
    tone,
    created_at: new Date().toISOString(),
  };
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function readSkillDetails(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const record = item as Record<string, unknown>;
      return {
        name: typeof record.name === "string" ? record.name : undefined,
        hint: typeof record.hint === "string" ? record.hint : undefined,
        guide: typeof record.guide === "string" ? record.guide : undefined,
        guide_tokens: typeof record.guide_tokens === "number" ? record.guide_tokens : undefined,
      };
    })
    .filter(
      (item): item is { name?: string; hint?: string; guide?: string; guide_tokens?: number } =>
        item !== null,
    );
}

function buildCard(payload: Record<string, unknown>): ChatCardItem | null {
  const type = typeof payload.type === "string" ? payload.type : "";
  if (!type) return null;

  const createdAt =
    typeof payload.created_at === "string" && payload.created_at
      ? payload.created_at
      : new Date().toISOString();

  const baseCard = {
    id: `${type}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    kind: type as ChatCardItem["kind"],
    created_at: createdAt,
    source: typeof payload.source === "string" ? payload.source : "chatroom",
    pipeline_id: typeof payload.pipeline_id === "number" ? payload.pipeline_id : undefined,
    run_id: typeof payload.run_id === "number" ? payload.run_id : undefined,
  };

  switch (type) {
    case "llm_call":
      return {
        ...baseCard,
        kind: "llm_call",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        model: typeof payload.model === "string" ? payload.model : undefined,
        turn: typeof payload.turn === "number" ? payload.turn : undefined,
        tokens_in: typeof payload.tokens_in === "number" ? payload.tokens_in : undefined,
        tokens_out: typeof payload.tokens_out === "number" ? payload.tokens_out : undefined,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
        system_prompt: typeof payload.system_prompt === "string" ? payload.system_prompt : undefined,
        prompt_messages: typeof payload.prompt_messages === "string" ? payload.prompt_messages : undefined,
        response: typeof payload.response === "string" ? payload.response : undefined,
        raw_response: typeof payload.raw_response === "string" ? payload.raw_response : undefined,
        tool_calls: Array.isArray(payload.tool_calls)
          ? payload.tool_calls
              .map((item) => {
                if (!item || typeof item !== "object") return null;
                const record = item as Record<string, unknown>;
                return {
                  name: typeof record.name === "string" ? record.name : undefined,
                  args_preview: typeof record.args_preview === "string" ? record.args_preview : undefined,
                };
              })
              .filter(
                (item): item is { name?: string; args_preview?: string } => item !== null,
              )
          : [],
      };
    case "tool_call":
      return {
        ...baseCard,
        kind: "tool_call",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        tool: typeof payload.tool === "string" ? payload.tool : undefined,
        arguments: typeof payload.arguments === "string" ? payload.arguments : undefined,
        success: typeof payload.success === "boolean" ? payload.success : undefined,
        result: typeof payload.result === "string" ? payload.result : undefined,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
      };
    case "stage_started":
      return {
        ...baseCard,
        kind: "stage_start",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        display_name: typeof payload.display_name === "string" ? payload.display_name : undefined,
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
        active_skills: readStringArray(payload.active_skills),
        expected_artifacts: readStringArray(payload.expected_artifacts),
        content: typeof payload.content === "string" ? payload.content : undefined,
      };
    case "stage_completed":
      return {
        ...baseCard,
        kind: "stage_end",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        summary: typeof payload.summary === "string" ? payload.summary : undefined,
      };
    case "gate_blocked":
      return {
        ...baseCard,
        kind: "gate_blocked",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        display_name: typeof payload.display_name === "string" ? payload.display_name : undefined,
      };
    case "gate_approved":
      return {
        ...baseCard,
        kind: "gate_approved",
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
      };
    case "gate_rejected":
      return {
        ...baseCard,
        kind: "gate_rejected",
        from_stage: typeof payload.from_stage === "string" ? payload.from_stage : undefined,
        to_stage: typeof payload.to_stage === "string" ? payload.to_stage : undefined,
      };
    case "skill_inject":
      return {
        ...baseCard,
        kind: "skill_inject",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        stage: typeof payload.stage === "string" ? payload.stage : undefined,
        skills: readSkillDetails(payload.skills),
        agent_all_skills: readStringArray(payload.agent_all_skills),
      };
    case "agent_message":
      return {
        ...baseCard,
        kind: "agent_message",
        from_agent: typeof payload.from_agent === "string" ? payload.from_agent : undefined,
        to_agent: typeof payload.to_agent === "string" ? payload.to_agent : undefined,
        content: typeof payload.content === "string" ? payload.content : undefined,
      };
    case "boss_instruction":
      return {
        ...baseCard,
        kind: "boss_instruction",
        agent: typeof payload.agent === "string" ? payload.agent : undefined,
        content_preview: typeof payload.content_preview === "string" ? payload.content_preview : undefined,
      };
    default:
      return null;
  }
}

function updateMessage(
  current: MessageItem[],
  messageId: number,
  updater: (message: MessageItem) => MessageItem,
) {
  let didUpdate = false;
  const next = current.map((message) => {
    if (message.id !== messageId) return message;
    didUpdate = true;
    return updater(message);
  });
  return didUpdate ? next : current;
}

function replaceMessageId(
  current: MessageItem[],
  previousId: number,
  nextId: number,
  patch: Partial<MessageItem> = {},
) {
  return updateMessage(current, previousId, (message) => ({
    ...message,
    ...patch,
    id: nextId,
  }));
}

function mergeMessages(current: MessageItem[], incoming: MessageItem[]) {
  const merged = new Map<number, MessageItem>();
  for (const item of current) {
    merged.set(item.id, item);
  }
  for (const item of incoming) {
    merged.set(item.id, { ...merged.get(item.id), ...item });
  }
  return Array.from(merged.values()).sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
}

function buildStreamStep(
  label: string,
  detail?: string,
  state: MessageStreamStep["state"] = "live",
  detailContent?: string,
): MessageStreamStep {
  return {
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    label,
    detail,
    detailContent,
    state,
  };
}

function trimStreamSteps(steps: MessageStreamStep[]) {
  return steps.slice(-STREAM_STEP_LIMIT);
}

function prettyJson(value: string | undefined) {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function isJsonContent(value: string | undefined) {
  if (!value) return false;
  try {
    JSON.parse(value);
    return true;
  } catch {
    return false;
  }
}

function markdownCodeFence(content: string, language = "") {
  const normalized = content.replace(/\n+$/g, "");
  return `\`\`\`${language}\n${normalized}\n\`\`\``;
}

function markdownSection(title: string, content: string, options?: { language?: string; asMarkdown?: boolean }) {
  const normalized = content.trim();
  if (!normalized) return "";
  if (options?.asMarkdown) {
    return `### ${title}\n\n${normalized}`;
  }
  return `### ${title}\n\n${markdownCodeFence(normalized, options?.language ?? "")}`;
}

function settleLiveStreamSteps(
  steps: MessageStreamStep[] | undefined,
  state: Extract<MessageStreamStep["state"], "done" | "error"> = "done",
) {
  return (steps ?? []).map((step) => (step.state === "live" ? { ...step, state } : step));
}

function pushStreamingStep(
  message: MessageItem,
  label: string,
  detail?: string,
  state: MessageStreamStep["state"] = "live",
  detailContent?: string,
) {
  const nextSteps = [...settleLiveStreamSteps(message.streamSteps), buildStreamStep(label, detail, state, detailContent)];
  return {
    ...message,
    streamSteps: trimStreamSteps(nextSteps),
  };
}

function patchLatestStreamingStep(
  message: MessageItem,
  patch: Partial<Omit<MessageStreamStep, "id">>,
  fallbackLabel?: string,
) {
  const nextSteps = [...(message.streamSteps ?? [])];
  for (let index = nextSteps.length - 1; index >= 0; index -= 1) {
    if (nextSteps[index].state === "live") {
      nextSteps[index] = { ...nextSteps[index], ...patch };
      return {
        ...message,
        streamSteps: trimStreamSteps(nextSteps),
      };
    }
  }

  if (!fallbackLabel) return message;
  return {
    ...message,
    streamSteps: trimStreamSteps([
      ...nextSteps,
      buildStreamStep(fallbackLabel, patch.detail, patch.state ?? "done", patch.detailContent),
    ]),
  };
}

function patchMatchingStreamingStep(
  message: MessageItem,
  matcher: (step: MessageStreamStep) => boolean,
  patch: Partial<Omit<MessageStreamStep, "id">>,
  fallbackLabel?: string,
) {
  const nextSteps = [...(message.streamSteps ?? [])];
  for (let index = nextSteps.length - 1; index >= 0; index -= 1) {
    if (!matcher(nextSteps[index])) continue;
    nextSteps[index] = { ...nextSteps[index], ...patch };
    return {
      ...message,
      streamSteps: trimStreamSteps(nextSteps),
    };
  }

  if (!fallbackLabel) return message;
  return {
    ...message,
    streamSteps: trimStreamSteps([
      ...nextSteps,
      buildStreamStep(fallbackLabel, patch.detail, patch.state ?? "done", patch.detailContent),
    ]),
  };
}

function finalizeStreamingTrace(
  message: MessageItem,
  state: Extract<MessageStreamStep["state"], "done" | "error"> = "done",
  finalLabel?: string,
  finalDetail?: string,
) {
  const settled = settleLiveStreamSteps(message.streamSteps, state);
  if (!finalLabel) {
    return {
      ...message,
      streamSteps: trimStreamSteps(settled),
    };
  }

  return {
    ...message,
    streamSteps: trimStreamSteps([...settled, buildStreamStep(finalLabel, finalDetail, state)]),
  };
}

function summarizeStepDetail(value: string | undefined, limit = 140) {
  const normalized = value?.replace(/\s+/g, " ").trim() ?? "";
  if (!normalized) return "";
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit)}...`;
}

function buildLiveLlmDetailContent(
  userContent: string,
  currentDraft: string,
  options?: {
    systemPrompt?: string;
    promptMessages?: string;
    model?: string;
    turn?: number;
  },
) {
  const sections: string[] = [];
  const normalizedUser = userContent.trim();
  const normalizedDraft = currentDraft.trim();
  const meta = [
    options?.model,
    typeof options?.turn === "number" ? `turn ${options.turn}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  if (meta) {
    sections.push(`### Meta\n\n- ${meta}`);
  }

  if (options?.systemPrompt?.trim()) {
    sections.push(markdownSection("System Prompt", options.systemPrompt, { language: "text" }));
  }

  if (options?.promptMessages?.trim()) {
    sections.push(markdownSection("Full Prompt Payload", prettyJson(options.promptMessages), { language: "json" }));
  }

  if (normalizedUser) {
    sections.push(markdownSection("User Message", normalizedUser, { language: "text" }));
  }

  if (normalizedDraft) {
    sections.push(markdownSection("Current Response Draft", normalizedDraft, { asMarkdown: true }));
  } else {
    sections.push("### Status\n\nWaiting for the model to return tokens.");
  }

  return sections.join("\n\n");
}

function buildLiveToolDetailContent(args?: string, result?: string, failed = false) {
  const sections: string[] = [];

  if (args && args.trim()) {
    sections.push(markdownSection("Arguments", prettyJson(args), { language: "json" }));
  }

  if (result && result.trim()) {
    sections.push(
      isJsonContent(result)
        ? markdownSection(failed ? "Error" : "Result", prettyJson(result), { language: "json" })
        : markdownSection(failed ? "Error" : "Result", result.trim(), { asMarkdown: true }),
    );
  }

  return sections.join("\n\n");
}

function buildCardStepDetail(card: ChatCardItem) {
  const bits: string[] = [];

  switch (card.kind) {
    case "llm_call":
      if (card.model) bits.push(card.model);
      if (typeof card.turn === "number") bits.push(`turn ${card.turn}`);
      if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
      if (card.tool_calls && card.tool_calls.length > 0) {
        bits.push(`tools: ${card.tool_calls.map((tool) => tool.name || "tool").join(", ")}`);
      }
      if (card.response) bits.push(summarizeStepDetail(card.response));
      break;
    case "tool_call":
      if (typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
      if (typeof card.success === "boolean") bits.push(card.success ? "ok" : "failed");
      if (card.arguments) bits.push(`args: ${summarizeStepDetail(card.arguments, 90)}`);
      if (card.result) bits.push(summarizeStepDetail(card.result));
      break;
    case "stage_start":
    case "stage_end":
      if (card.stage) bits.push(card.stage);
      if (card.summary) bits.push(summarizeStepDetail(card.summary));
      break;
    case "skill_inject":
      if (card.skills && card.skills.length > 0) {
        bits.push(card.skills.map((skill) => skill.name).filter(Boolean).join(", "));
      }
      break;
    case "agent_message":
      if (card.to_agent) bits.push(`to ${card.to_agent}`);
      if (card.content) bits.push(summarizeStepDetail(card.content));
      break;
    case "boss_instruction":
      if (card.agent) bits.push(`to ${card.agent}`);
      if (card.content_preview) bits.push(summarizeStepDetail(card.content_preview));
      break;
    case "gate_blocked":
      if (card.display_name || card.stage) bits.push(card.display_name || card.stage || "");
      bits.push("Waiting for approval");
      break;
    case "gate_approved":
      if (card.stage) bits.push(card.stage);
      break;
    case "gate_rejected":
      bits.push(`Rollback to ${card.to_stage || "previous stage"}`);
      break;
    default:
      break;
  }

  return bits.filter(Boolean).join(" · ");
}

function buildCardStepDetailContent(card: ChatCardItem) {
  switch (card.kind) {
    case "llm_call": {
      const sections: string[] = [];
      const meta = [
        card.model,
        typeof card.turn === "number" ? `turn ${card.turn}` : "",
        typeof card.duration_ms === "number" ? `${card.duration_ms}ms` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      if (meta) sections.push(`### Meta\n\n- ${meta}`);
      if (card.system_prompt) sections.push(markdownSection("System Prompt", card.system_prompt, { language: "text" }));
      if (card.prompt_messages) sections.push(markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }));
      if (card.tool_calls && card.tool_calls.length > 0) {
        sections.push(
          `### Planned Tools\n\n${card.tool_calls
            .map(
              (tool) =>
                `- **${tool.name || "tool"}**${
                  tool.args_preview ? `\n  - args preview: \`${tool.args_preview.replace(/`/g, "'").replace(/\n/g, " ")}\`` : ""
                }`,
            )
            .join("\n")}`,
        );
      }
      if (card.response) sections.push(markdownSection("Response", card.response, { asMarkdown: true }));
      if (card.raw_response) sections.push(markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }));
      return sections.join("\n\n");
    }
    case "tool_call": {
      const sections: string[] = [];
      if (card.arguments) sections.push(markdownSection("Arguments", prettyJson(card.arguments), { language: "json" }));
      if (card.result) {
        sections.push(
          isJsonContent(card.result)
            ? markdownSection(card.success === false ? "Error" : "Result", prettyJson(card.result), { language: "json" })
            : markdownSection(card.success === false ? "Error" : "Result", card.result, { asMarkdown: true }),
        );
      }
      return sections.join("\n\n");
    }
    case "stage_start":
    case "stage_end":
      return card.summary || card.content || card.stage || "";
    case "skill_inject":
      return card.skills
        ?.map((skill) => `${skill.name || "skill"}${skill.hint ? `\n${skill.hint}` : ""}${skill.guide ? `\n\n${skill.guide}` : ""}`)
        .join("\n\n") || "";
    case "agent_message":
      return card.content || "";
    case "boss_instruction":
      return card.content_preview || "";
    case "gate_blocked":
      return `Waiting for approval${card.display_name ? `: ${card.display_name}` : ""}`;
    case "gate_rejected":
      return `Rollback target: ${card.to_stage || "previous stage"}`;
    default:
      return "";
  }
}

function applyRuntimeCardStep(message: MessageItem, card: ChatCardItem) {
  const actor =
    card.agent ||
    card.from_agent ||
    card.to_agent ||
    message.agent_name ||
    "agent";

  switch (card.kind) {
    case "llm_call": {
      const label = `${actor} contacting LLM`;
      return patchMatchingStreamingStep(
        message,
        (step) => step.state === "live" || /llm|drafting|contacting/i.test(step.label),
        {
          label,
          detail: buildCardStepDetail(card),
          detailContent: buildCardStepDetailContent(card),
          state: "done",
        },
        label,
      );
    }
    case "tool_call": {
      const toolName = card.tool || "tool";
      const label = `${actor} ran ${toolName}`;
      return patchMatchingStreamingStep(
        message,
        (step) => step.label.toLowerCase().includes(toolName.toLowerCase()),
        {
          label,
          detail: buildCardStepDetail(card),
          detailContent: buildCardStepDetailContent(card),
          state: card.success === false ? "error" : "done",
        },
        label,
      );
    }
    case "stage_start": {
      const label = `${actor} started ${card.display_name || card.stage || "stage"}`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "stage_end": {
      const label = `${actor} completed ${card.stage || "stage"}`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "skill_inject": {
      return pushStreamingStep(
        message,
        `${actor} loaded skills`,
        buildCardStepDetail(card),
        "done",
        buildCardStepDetailContent(card),
      );
    }
    case "agent_message": {
      const label = `${card.from_agent || actor} sent a team message`;
      return pushStreamingStep(message, label, buildCardStepDetail(card), "done", buildCardStepDetailContent(card));
    }
    case "boss_instruction": {
      return pushStreamingStep(
        message,
        `Boss instruction for ${card.agent || actor}`,
        buildCardStepDetail(card),
        "done",
        buildCardStepDetailContent(card),
      );
    }
    case "gate_blocked": {
      return pushStreamingStep(
        message,
        `${actor} is waiting for approval`,
        buildCardStepDetail(card),
        "error",
        buildCardStepDetailContent(card),
      );
    }
    case "gate_approved": {
      return pushStreamingStep(message, `${actor} approved the gate`, buildCardStepDetail(card), "done");
    }
    case "gate_rejected": {
      return pushStreamingStep(
        message,
        `${actor} rejected the gate`,
        buildCardStepDetail(card),
        "error",
        buildCardStepDetailContent(card),
      );
    }
    default:
      return message;
  }
}

function upsertProject(current: ProjectSummary[], nextProject: ProjectSummary) {
  const existingIndex = current.findIndex((project) => project.id === nextProject.id);
  if (existingIndex === -1) {
    return [nextProject, ...current];
  }

  const next = [...current];
  next[existingIndex] = nextProject;
  return next;
}

function reorderProjects(current: ProjectSummary[], draggedProjectId: number, targetProjectId: number) {
  if (draggedProjectId === targetProjectId) return current;
  const next = [...current];
  const draggedIndex = next.findIndex((project) => project.id === draggedProjectId);
  const targetIndex = next.findIndex((project) => project.id === targetProjectId);
  if (draggedIndex === -1 || targetIndex === -1) return current;

  const [draggedProject] = next.splice(draggedIndex, 1);
  next.splice(targetIndex, 0, draggedProject);
  return next.map((project, index) => ({ ...project, display_order: index }));
}

function App() {
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const [sidebarDrawerOpen, setSidebarDrawerOpen] = useState(false);
  const [activityDrawerOpen, setActivityDrawerOpen] = useState(false);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [selectedChatId, setSelectedChatId] = useState<number | null>(() => readLastChatId());
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [optimisticMessages, setOptimisticMessages] = useState<MessageItem[]>([]);
  const [chatCards, setChatCards] = useState<ChatCardItem[]>([]);
  const [chatEvents, setChatEvents] = useState<ChatEventItem[]>([]);
  const [connectionState, setConnectionState] = useState<"connected" | "connecting" | "disconnected">("connecting");
  const [bootstrapped, setBootstrapped] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [refreshingMessages, setRefreshingMessages] = useState(false);
  const [sendingMessage, setSendingMessage] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [creatingProjectFromChat, setCreatingProjectFromChat] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);
  const [notice, setNotice] = useState<string>("");
  const [error, setError] = useState<string>("");

  const socketRef = useRef<WebSocket | null>(null);
  const selectedChatIdRef = useRef<number | null>(selectedChatId);
  const selectedProjectIdRef = useRef<number | null>(selectedProjectId);
  const optimisticScopeRef = useRef<string>(optimisticScopeKey(selectedChatId));
  const joinedRoomRef = useRef<number | null>(null);
  const tempMessageIdRef = useRef(-1);
  const streamingAssistantIdRef = useRef<number | null>(null);
  const sendAbortRef = useRef<AbortController | null>(null);

  function pushEvent(message: string, tone: ChatEventTone = "info") {
    setChatEvents((current) => [...current.slice(-79), buildEvent(message, tone)]);
  }

  function pushCard(card: ChatCardItem) {
    setChatCards((current) => [...current.slice(-39), card]);
  }

  function nextTempMessageId() {
    const nextId = tempMessageIdRef.current;
    tempMessageIdRef.current -= 1;
    return nextId;
  }

  function commitMessages(updater: MessageItem[] | ((current: MessageItem[]) => MessageItem[])) {
    setMessages(updater);
  }

  function commitOptimisticMessages(
    updater: MessageItem[] | ((current: MessageItem[]) => MessageItem[]),
  ) {
    setOptimisticMessages((current) => (typeof updater === "function" ? updater(current) : updater));
  }

  function toggleConfigTab() {
    setActiveTab((currentTab) => (currentTab === "config" ? "chat" : "config"));
  }

  async function ensureSelfBootstrapProject() {
    const project = await api.getOrCreateSelfBootstrapProject();
    setProjects((current) => upsertProject(current, project));
    migrateOptimisticMessages(selectedChatIdRef.current, project.default_chatroom_id);
    optimisticScopeRef.current = optimisticScopeKey(project.default_chatroom_id);
    setSelectedProjectId(project.id);
    setSelectedChatId(project.default_chatroom_id);
    setActiveTab("chat");
    return project;
  }

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId],
  );

  const selectedStandaloneChat = useMemo(
    () => chats.find((chat) => chat.id === selectedChatId) ?? null,
    [chats, selectedChatId],
  );

  const activeChat = useMemo<ChatSummary | null>(() => {
    if (selectedProject && selectedProject.default_chatroom_id === selectedChatId) {
      return {
        id: selectedProject.default_chatroom_id,
        title: selectedProject.name,
        session_type: "project-bound",
        is_visible_in_chat_list: false,
        project_id: selectedProject.id,
      };
    }
    return selectedStandaloneChat;
  }, [selectedProject, selectedStandaloneChat, selectedChatId]);

  useEffect(() => {
    selectedChatIdRef.current = selectedChatId;
  }, [selectedChatId]);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  useEffect(() => {
    if (!bootstrapped) return;
    optimisticScopeRef.current = optimisticScopeKey(selectedChatId);
    setOptimisticMessages(readOptimisticMessages(selectedChatId));
  }, [bootstrapped, selectedChatId]);

  useEffect(() => {
    if (!bootstrapped) return;
    const scopeChatId = parseOptimisticScopeChatId(optimisticScopeRef.current);
    const timer = window.setTimeout(() => {
      writeOptimisticMessages(scopeChatId, optimisticMessages);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [bootstrapped, optimisticMessages]);

  async function loadBootstrapData() {
    try {
      setError("");
      const preferredChatId = readLastChatId();
      const [agentRows, configData, chatRows] = await Promise.all([
        api.getAgents(),
        api.getConfig(),
        api.getChats(),
      ]);
      const selfProject = await api.getOrCreateSelfBootstrapProject();
      const projectRows = await api.getProjects();
      const restoredSelection = resolveRestoredSelection(projectRows, chatRows, preferredChatId);

      setChats(chatRows);
      setProjects(projectRows);
      setAgents(agentRows);
      setConfig(configData);
      setSelectedChatId(restoredSelection.chatId ?? selfProject.default_chatroom_id);
      setSelectedProjectId(restoredSelection.projectId ?? selfProject.id);
      setActiveTab("chat");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to load app data");
      pushEvent("Failed to load bootstrap data", "error");
    } finally {
      setBootstrapped(true);
    }
  }

  useEffect(() => {
    void loadBootstrapData();
  }, []);

  useEffect(() => {
    writeLastChatId(selectedChatId);
  }, [selectedChatId]);

  useEffect(() => {
    if (activeTab !== "chat") {
      setSidebarDrawerOpen(false);
      setActivityDrawerOpen(false);
    }
  }, [activeTab]);

  useEffect(() => {
    if (!bootstrapped) return;
    if (!selectedChatId) {
      setMessages([]);
      optimisticScopeRef.current = optimisticScopeKey(null);
      setOptimisticMessages(readOptimisticMessages(null));
      setChatCards([]);
      return;
    }
    const activeChatId = selectedChatId;

    let cancelled = false;

    async function loadMessages() {
      try {
        setLoadingMessages(true);
        setError("");
        setMessages([]);
        setChatCards([]);
        setChatEvents([]);
        const [rows, runtimeRows] = await Promise.all([
          api.getMessages(activeChatId),
          api.getRuntimeCards(activeChatId),
        ]);
        if (!cancelled) {
          setMessages((current) => mergeMessages(current, rows));
          setChatCards(
            runtimeRows
              .map((payload) => buildCard(payload))
              .filter((card): card is ChatCardItem => card !== null),
          );
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : "Failed to load messages");
        }
      } finally {
        if (!cancelled) {
          setLoadingMessages(false);
        }
      }
    }

    void loadMessages();

    return () => {
      cancelled = true;
    };
  }, [bootstrapped, selectedChatId]);

  useEffect(() => {
    if (!bootstrapped) return;
    if (!selectedChatId) return;

    const timer = window.setInterval(() => {
      void refreshMessages(false);
    }, 5000);

    return () => window.clearInterval(timer);
  }, [bootstrapped, selectedChatId]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let cancelled = false;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (cancelled) return;
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws`;
      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;
      setConnectionState("connecting");

      socket.onopen = () => {
        if (cancelled) return;
        setConnectionState("connected");
        pushEvent("Realtime connection established", "success");

        const roomId = selectedChatIdRef.current;
        if (roomId) {
          socket.send(JSON.stringify({ type: "join", chatroom_id: roomId }));
          joinedRoomRef.current = roomId;
        }
      };

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;

          if (data.type === "message" && typeof data.id === "number") {
            const incoming: MessageItem = {
              id: data.id,
              content: typeof data.content === "string" ? data.content : "",
              agent_name: typeof data.agent_name === "string" ? data.agent_name : null,
              message_type: typeof data.message_type === "string" ? data.message_type : "text",
              created_at: typeof data.created_at === "string" ? data.created_at : new Date().toISOString(),
            };
            setMessages((current) => {
              const streamingId = streamingAssistantIdRef.current;
              if (streamingId !== null) {
                const next = updateMessage(current, streamingId, (message) => ({
                  ...finalizeStreamingTrace(
                    {
                      ...message,
                      ...incoming,
                      isStreaming: false,
                    },
                    "done",
                  ),
                }));
                if (next !== current) {
                  streamingAssistantIdRef.current = incoming.id;
                  return next;
                }
              }
              return mergeMessages(current, [incoming]);
            });
            commitOptimisticMessages((current) =>
              current.filter(
                (item) =>
                  !(
                    item.isStreaming &&
                    (item.agent_name || null) === (incoming.agent_name || null)
                  ),
              ),
            );
            pushEvent(`Incoming reply from ${incoming.agent_name || "assistant"}`, "success");
            return;
          }

          if (typeof data.type === "string" && data.type.startsWith("pipeline_")) {
            const pipelineType = data.type.slice("pipeline_".length);
            const card = buildCard({
              ...data,
              type: pipelineType,
              source: "pipeline",
            });
            if (card && selectedProjectIdRef.current !== null) {
              pushCard(card);
              if (streamingAssistantIdRef.current !== null) {
                commitOptimisticMessages((current) =>
                  updateMessage(current, streamingAssistantIdRef.current ?? 0, (message) =>
                    applyRuntimeCardStep(
                      {
                        ...message,
                        agent_name:
                          card.agent ||
                          card.from_agent ||
                          card.to_agent ||
                          message.agent_name,
                      },
                      card,
                    ),
                  ),
                );
              }
            }

            switch (pipelineType) {
              case "agent_output":
                if (selectedProjectIdRef.current !== null && typeof data.content === "string") {
                  const tempId = nextTempMessageId();
                  const createdAt =
                    typeof data.created_at === "string" ? data.created_at : new Date().toISOString();
                  const agentName = typeof data.agent === "string" ? data.agent : "agent";
                  setMessages((current) =>
                    mergeMessages(current, [
                      {
                        id: tempId,
                        content: data.content,
                        agent_name: agentName,
                        message_type: "text",
                        created_at: createdAt,
                      },
                    ]),
                  );
                }
                break;
              case "pipeline_started":
                pushEvent("Pipeline started", "info");
                break;
              case "pipeline_paused":
                pushEvent("Pipeline paused", "warning");
                break;
              case "pipeline_failed":
                pushEvent(
                  `Pipeline failed${typeof data.failed_stage === "string" ? ` at ${data.failed_stage}` : ""}`,
                  "error",
                );
                break;
              case "pipeline_blocked":
                pushEvent(
                  `Pipeline waiting at ${typeof data.stage === "string" ? data.stage : "manual gate"}`,
                  "warning",
                );
                break;
              case "pipeline_completed":
                pushEvent("Pipeline completed", "success");
                break;
              case "stage_started":
                if (typeof data.display_name === "string") {
                  pushEvent(`Stage started: ${data.display_name}`, "info");
                }
                break;
              case "stage_completed":
                if (typeof data.stage === "string") {
                  pushEvent(`Stage completed: ${data.stage}`, "success");
                }
                break;
              case "gate_blocked":
                if (typeof data.display_name === "string") {
                  pushEvent(`Approval needed: ${data.display_name}`, "warning");
                }
                break;
              case "gate_approved":
                pushEvent("Manual gate approved", "success");
                break;
              case "gate_rejected":
                pushEvent("Manual gate rejected", "warning");
                break;
              case "boss_instruction":
                if (typeof data.agent === "string") {
                  pushEvent(`Boss instruction sent to ${data.agent}`, "info");
                }
                break;
              case "agent_message":
                if (typeof data.from_agent === "string" && typeof data.to_agent === "string") {
                  pushEvent(`${data.from_agent} -> ${data.to_agent}`, "info");
                }
                break;
              default:
                break;
            }
          }
        } catch {
          pushEvent("Realtime payload parse error", "warning");
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        setConnectionState("disconnected");
        joinedRoomRef.current = null;
        reconnectTimer = window.setTimeout(connect, 3000);
      };

      socket.onerror = () => {
        if (cancelled) return;
        setConnectionState("disconnected");
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      joinedRoomRef.current = null;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, []);

  useEffect(() => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;

    if (joinedRoomRef.current && joinedRoomRef.current !== selectedChatId) {
      socket.send(JSON.stringify({ type: "leave", chatroom_id: joinedRoomRef.current }));
    }

    if (selectedChatId) {
      socket.send(JSON.stringify({ type: "join", chatroom_id: selectedChatId }));
      joinedRoomRef.current = selectedChatId;
    } else {
      joinedRoomRef.current = null;
    }
  }, [selectedChatId]);

  async function refreshMessages(showSpinner = true, chatId = selectedChatId) {
    if (!chatId) return;
    try {
      if (showSpinner) {
        setRefreshingMessages(true);
      }
      setError("");
      const [rows, runtimeRows] = await Promise.all([
        api.getMessages(chatId),
        api.getRuntimeCards(chatId),
      ]);
      setMessages((current) => mergeMessages(current, rows));
      commitOptimisticMessages((current) =>
        current.filter(
          (item) => {
            const hasMatchingSavedRow = rows.some(
              (row) =>
                row.id === item.id ||
                (
                  row.content === item.content &&
                  (row.agent_name || null) === (item.agent_name || null) &&
                  row.message_type === item.message_type
                ),
            );
            if (hasMatchingSavedRow) {
              return false;
            }

            if (item.isStreaming && item.agent_name) {
              const hasNewerServerReply = rows.some(
                (row) =>
                  Boolean(row.agent_name) &&
                  new Date(row.created_at).getTime() >= new Date(item.created_at).getTime() - 1000,
              );
              if (hasNewerServerReply) {
                return false;
              }
            }

            return true;
          },
        ),
      );
      setChatCards(
        runtimeRows
          .map((payload) => buildCard(payload))
          .filter((card): card is ChatCardItem => card !== null),
      );
      if (showSpinner) {
        pushEvent("Conversation refreshed", "info");
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to refresh messages");
    } finally {
      if (showSpinner) {
        setRefreshingMessages(false);
      }
    }
  }

  async function handleSendMessage(content: string) {
    let chatId = selectedChatId;
    let streamReceivedEvent = false;
    const userTempId = nextTempMessageId();
    const assistantTempId = nextTempMessageId();
    const createdAt = new Date().toISOString();
    let activeAgentName = "assistant";
    let streamCompleted = false;
    let assistantDraftContent = "";
    let pendingContentDelta = "";
    let contentFlushTimer: number | null = null;
    let liveLlmSystemPrompt = "";
    let liveLlmPromptMessages = "";
    let liveLlmModel = "";
    let liveLlmTurn: number | undefined;

    try {
      setSendingMessage(true);
      setLoadingMessages(false);
      setError("");

      commitOptimisticMessages((current) =>
        mergeMessages(current, [
          {
            id: userTempId,
            content,
            message_type: "user",
            created_at: createdAt,
            agent_name: null,
          },
          {
            id: assistantTempId,
            content: "",
            message_type: "text",
            created_at: new Date(Date.now() + 1).toISOString(),
            agent_name: activeAgentName,
            isStreaming: true,
            streamSteps: [buildStreamStep("Queued", "Waiting to start agent response.")],
          },
        ]),
      );
      streamingAssistantIdRef.current = assistantTempId;
      pushEvent(`Sent: ${content.slice(0, 72)}`, "info");

      if (!chatId) {
        const selfProject = await ensureSelfBootstrapProject();
        chatId = selfProject.default_chatroom_id;
        pushEvent(`Opened self-bootstrap project "${selfProject.name}"`, "success");
      }

      const controller = new AbortController();
      sendAbortRef.current?.abort();
      sendAbortRef.current = controller;

      const response = await api.streamMessage(chatId, content, controller.signal);
      if (!response.ok || !response.body) {
        let detail = `Request failed: ${response.status}`;
        try {
          const data = (await response.json()) as { detail?: string; error?: string };
          detail = data.detail || data.error || detail;
        } catch {
          // Ignore JSON parse failures for SSE fallback errors.
        }
        throw new Error(detail);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const readAssistantMessageId = () => streamingAssistantIdRef.current ?? assistantTempId;

      const flushPendingContent = () => {
        if (!pendingContentDelta) return;
        const delta = pendingContentDelta;
        pendingContentDelta = "";
        assistantDraftContent = `${assistantDraftContent}${delta}`;
        commitOptimisticMessages((current) =>
          updateMessage(current, readAssistantMessageId(), (message) => {
            const nextMessage = patchMatchingStreamingStep(
              {
                ...message,
                agent_name: message.agent_name || activeAgentName,
              },
              (step) => step.state === "live" && /llm|drafting|contacting/i.test(step.label),
              {
                label: `${activeAgentName} contacting LLM`,
                detail: summarizeStepDetail(assistantDraftContent) || "Streaming tokens from the model.",
                detailContent: buildLiveLlmDetailContent(content, assistantDraftContent, {
                  systemPrompt: liveLlmSystemPrompt,
                  promptMessages: liveLlmPromptMessages,
                  model: liveLlmModel,
                  turn: liveLlmTurn,
                }),
                state: "live",
              },
              `${activeAgentName} contacting LLM`,
            );

            return {
              ...nextMessage,
              content: `${nextMessage.content}${delta}`,
            };
          }),
        );
      };

      const scheduleContentFlush = () => {
        if (contentFlushTimer !== null) return;
        contentFlushTimer = window.setTimeout(() => {
          contentFlushTimer = null;
          flushPendingContent();
        }, 48);
      };

      const handleStreamPayload = async (payload: string) => {
        if (!payload.startsWith("data:")) return;

        const raw = payload.slice(5).trim();
        if (!raw) return;

        const data = JSON.parse(raw) as Record<string, unknown>;
        streamReceivedEvent = true;

        if (data.type !== "content") {
          flushPendingContent();
        }

        if (typeof data.type === "string") {
          const card = buildCard(data);
          if (card) {
            pushCard(card);
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => {
                const nextAgentName =
                  card.agent ||
                  card.from_agent ||
                  card.to_agent ||
                  message.agent_name;
                return applyRuntimeCardStep(
                  {
                    ...message,
                    agent_name: nextAgentName || message.agent_name,
                  },
                  card,
                );
              }),
            );
          }
        }

        switch (data.type) {
          case "user_saved":
            if (typeof data.id === "number") {
              commitOptimisticMessages((current) => replaceMessageId(current, userTempId, data.id));
            }
            break;
          case "agent_start":
            if (typeof data.agent_name === "string" && data.agent_name) {
              activeAgentName = data.agent_name;
            }
            liveLlmSystemPrompt = typeof data.system_prompt === "string" ? data.system_prompt : "";
            liveLlmPromptMessages = typeof data.prompt_messages === "string" ? data.prompt_messages : "";
            liveLlmModel = typeof data.model === "string" ? data.model : "";
            liveLlmTurn = typeof data.turn === "number" ? data.turn : undefined;
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: activeAgentName,
                  },
                  `${activeAgentName} contacting LLM`,
                  "Preparing context and sending prompt.",
                  "live",
                  buildLiveLlmDetailContent(content, "", {
                    systemPrompt: liveLlmSystemPrompt,
                    promptMessages: liveLlmPromptMessages,
                    model: liveLlmModel,
                    turn: liveLlmTurn,
                  }),
                ),
              })),
            );
            pushEvent(`${activeAgentName} is responding`, "info");
            break;
          case "collab_start": {
            const agentNames = readStringArray(data.agents);
            activeAgentName = "pipeline";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: "pipeline",
                    content:
                      agentNames.length > 0
                        ? `Pipeline: ${agentNames.join(" -> ")}\n\n`
                        : message.content,
                  },
                  "Routing multi-agent flow",
                  agentNames.length > 0 ? agentNames.join(" -> ") : "Selecting agents.",
                ),
              })),
            );
            if (agentNames.length > 0) {
              pushEvent(`Multi-agent pipeline: ${agentNames.join(" -> ")}`, "info");
            }
            break;
          }
          case "collab_step": {
            const agentName = typeof data.agent === "string" && data.agent ? data.agent : activeAgentName;
            activeAgentName = agentName;
            const step = typeof data.step === "number" ? data.step : "?";
            const total = typeof data.total === "number" ? data.total : "?";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (message) => ({
                ...pushStreamingStep(
                  {
                    ...message,
                    agent_name: "pipeline",
                    content: `${message.content}\nStep ${step}/${total}: ${agentName}\n\n`,
                  },
                  `${agentName} is working`,
                  `Step ${step}/${total}`,
                ),
              })),
            );
            pushEvent(`Step ${step}/${total}: ${agentName}`, "info");
            break;
          }
          case "collab_step_done":
            if (typeof data.agent === "string") {
              pushEvent(`${data.agent} completed their step`, "success");
            }
            break;
          case "collab_skip":
            if (typeof data.agent === "string") {
              const reason = typeof data.reason === "string" ? data.reason : "skipped";
              pushEvent(`${data.agent} skipped: ${reason}`, "warning");
            }
            break;
          case "content": {
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            const delta = typeof data.delta === "string" ? data.delta : "";
            if (!delta) break;
            pendingContentDelta = `${pendingContentDelta}${delta}`;
            scheduleContentFlush();
            break;
          }
          case "tool_start":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const rawToolArgs = typeof data.args === "string" && data.args.trim() ? data.args.trim() : "";
              const toolArgs = rawToolArgs ? rawToolArgs.slice(0, 120) : "Calling tool.";
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) => ({
                  ...pushStreamingStep(
                    {
                      ...message,
                      agent_name: message.agent_name || activeAgentName,
                    },
                    `${activeAgentName} running ${data.tool}`,
                    toolArgs,
                    "live",
                    buildLiveToolDetailContent(rawToolArgs),
                  ),
                })),
              );
              pushEvent(`${activeAgentName} is running ${data.tool}`, "warning");
            }
            break;
          case "tool_result":
            if (typeof data.agent === "string" && data.agent) {
              activeAgentName = data.agent;
            }
            if (typeof data.tool === "string") {
              const resultPreview =
                typeof data.result === "string" && data.result.trim()
                  ? data.result.replace(/\s+/g, " ").trim().slice(0, 140)
                  : `${data.tool} completed.`;
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) =>
                  patchLatestStreamingStep(
                    message,
                    {
                      state: "done",
                      detail: resultPreview,
                      detailContent: buildLiveToolDetailContent(undefined, typeof data.result === "string" ? data.result : ""),
                    },
                    `${activeAgentName} finished ${data.tool}`,
                  ),
                ),
              );
              pushEvent(`${activeAgentName} finished ${data.tool}`, "success");
            }
            break;
          case "done": {
            streamCompleted = true;
            const savedMessageId = typeof data.message_id === "number" ? data.message_id : null;
            const finalAgentName =
              typeof data.agent_name === "string" && data.agent_name
                ? data.agent_name
                : activeAgentName;
            if (savedMessageId !== null) {
              commitMessages((current) =>
                mergeMessages(current, [
                  {
                    id: savedMessageId,
                    content: assistantDraftContent || "(Agent returned empty response)",
                    created_at: new Date().toISOString(),
                    message_type: "text",
                    agent_name: finalAgentName,
                    isStreaming: false,
                  },
                ]),
              );
              commitOptimisticMessages((current) =>
                replaceMessageId(
                  current,
                  readAssistantMessageId(),
                  savedMessageId,
                  finalizeStreamingTrace(
                    {
                      id: savedMessageId,
                      content: "",
                      created_at: new Date().toISOString(),
                      message_type: "text",
                      ...current.find((item) => item.id === readAssistantMessageId()),
                      agent_name: finalAgentName,
                      isStreaming: false,
                    },
                    "done",
                    "Completed",
                    "Agent reply saved.",
                  ),
                ),
              );
              streamingAssistantIdRef.current = savedMessageId;
            } else {
              commitOptimisticMessages((current) =>
                updateMessage(current, readAssistantMessageId(), (message) =>
                  finalizeStreamingTrace(
                    {
                      ...message,
                      agent_name: finalAgentName,
                      isStreaming: false,
                    },
                    "done",
                    "Completed",
                    "Agent flow finished.",
                  ),
                ),
              );
            }
            pushEvent(`${finalAgentName} replied`, "success");
            await refreshMessages(false, chatId);
            commitOptimisticMessages((current) =>
              current.filter((item) => item.id !== userTempId && item.id !== (savedMessageId ?? assistantTempId)),
            );
            break;
          }
          case "error": {
            const message =
              typeof data.error === "string" && data.error
                ? data.error
                : "Streaming failed";
            commitOptimisticMessages((current) =>
              updateMessage(current, readAssistantMessageId(), (item) =>
                finalizeStreamingTrace(
                  {
                    ...item,
                    content: item.content || `Error: ${message}`,
                    agent_name: activeAgentName,
                    isStreaming: false,
                  },
                  "error",
                  "Failed",
                  message,
                ),
              ),
            );
            pushEvent(`Message stream failed: ${message}`, "error");
            setError(message);
            break;
          }
          default:
            break;
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const chunk of events) {
          const lines = chunk
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean);
          for (const line of lines) {
            await handleStreamPayload(line);
          }
        }
      }

      const finalChunk = buffer.trim();
      if (finalChunk) {
        const lines = finalChunk
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
        for (const line of lines) {
          await handleStreamPayload(line);
        }
      }

      flushPendingContent();

      if (!streamCompleted) {
        commitOptimisticMessages((current) =>
          updateMessage(current, readAssistantMessageId(), (message) =>
            finalizeStreamingTrace(
              {
                ...message,
                isStreaming: false,
              },
              "done",
            ),
          ),
        );
        await refreshMessages(false, chatId);
        commitOptimisticMessages((current) =>
          current.filter((item) => item.id !== userTempId && item.id !== assistantTempId),
        );
      }
    } catch (nextError) {
      if (contentFlushTimer !== null) {
        window.clearTimeout(contentFlushTimer);
        contentFlushTimer = null;
      }
      const message = nextError instanceof Error ? nextError.message : "Failed to send message";
      setError(message);

      if (chatId && !streamReceivedEvent) {
        commitOptimisticMessages((current) => current.filter((item) => item.id >= 0));
        pushEvent("Streaming unavailable, falling back to sync send", "warning");
        try {
          const saved = await api.sendMessage(chatId, content);
          setMessages((current) => mergeMessages(current, [saved]));
          commitOptimisticMessages((current) =>
            current.filter((item) => item.id !== userTempId && item.id !== assistantTempId),
          );
          await refreshMessages(false, chatId);
          return;
        } catch (fallbackError) {
          const fallbackMessage =
            fallbackError instanceof Error ? fallbackError.message : "Failed to send message";
          setError(fallbackMessage);
        }
      } else if (streamingAssistantIdRef.current !== null) {
        commitOptimisticMessages((current) =>
          updateMessage(current, streamingAssistantIdRef.current ?? 0, (messageItem) =>
            finalizeStreamingTrace(
              {
                ...messageItem,
                content: messageItem.content || `Error: ${message}`,
                isStreaming: false,
              },
              "error",
              "Failed",
              message,
            ),
          ),
        );
      }

      pushEvent("Message send failed", "error");
    } finally {
      if (contentFlushTimer !== null) {
        window.clearTimeout(contentFlushTimer);
      }
      sendAbortRef.current = null;
      streamingAssistantIdRef.current = null;
      setSendingMessage(false);
    }
  }

  async function handleCreateProject(payload: { name: string; description: string; agent_names: string[] }) {
    try {
      setCreatingProject(true);
      setError("");
      const created = await api.createProject(payload);
      const nextProjects = await api.getProjects();
      setProjects(nextProjects);
      setSelectedProjectId(created.id);
      setSelectedChatId(created.default_chatroom_id);
      setActiveTab("chat");
      setNotice(`Created project "${created.name}" and opened its chat room.`);
      pushEvent(`Project "${created.name}" created`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create project");
    } finally {
      setCreatingProject(false);
    }
  }

  async function handleCreateChat() {
    try {
      setError("");
      const project = await ensureSelfBootstrapProject();
      setNotice(`Opened self-bootstrap project "${project.name}".`);
      pushEvent(`Opened self-bootstrap project "${project.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to open self-bootstrap project");
    }
  }

  async function handleCreateProjectChat(projectId: number) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;

    try {
      setError("");
      const created = await api.createProjectSubchat(projectId);
      setChats((current) => [created, ...current.filter((chat) => chat.id !== created.id)]);
      setSelectedProjectId(projectId);
      setSelectedChatId(created.id);
      setActiveTab("chat");
      setNotice(`Created sub chat "${created.title}" in "${targetProject.name}".`);
      pushEvent(`Created sub chat in "${targetProject.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create sub chat");
    }
  }

  async function handleOpenWorkspace() {
    if (!selectedProject) return;

    try {
      setError("");
      await api.openProjectWorkspace(selectedProject.id);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to open workspace");
    }
  }

  async function handleReorderProjects(draggedProjectId: number, targetProjectId: number) {
    if (draggedProjectId === targetProjectId) return;

    const previousProjects = projects;
    const nextProjects = reorderProjects(projects, draggedProjectId, targetProjectId);
    if (nextProjects === projects) return;

    try {
      setError("");
      setProjects(nextProjects);
      const persistedProjects = await api.reorderProjects(nextProjects.map((project) => project.id));
      setProjects(persistedProjects);
    } catch (nextError) {
      setProjects(previousProjects);
      setError(nextError instanceof Error ? nextError.message : "Failed to reorder projects");
    }
  }

  async function handleCreateProjectFromCurrentChat(payload: {
    name: string;
    description: string;
    agent_names: string[];
  }) {
    if (!activeChat || activeChat.session_type !== "standalone") return;

    try {
      setCreatingProjectFromChat(true);
      setError("");
      const created = await api.createProjectFromChat({
        source_chatroom_id: activeChat.id,
        name: payload.name,
        description: payload.description,
        agent_names: payload.agent_names,
      });
      const nextProjects = await api.getProjects();
      setProjects(nextProjects);
      setSelectedProjectId(created.id);
      setSelectedChatId(created.default_chatroom_id);
      setActiveTab("chat");
      setNotice(`Created project "${created.name}" from "${activeChat.title}".`);
      pushEvent(`Converted chat "${activeChat.title}" into project "${created.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to create project from chat");
    } finally {
      setCreatingProjectFromChat(false);
    }
  }

  async function handleRenameChat(chatId: number, nextTitle: string) {
    const targetChat = chats.find((chat) => chat.id === chatId) ?? null;
    if (!targetChat) return;
    if (!nextTitle || nextTitle === targetChat.title) return;

    try {
      setError("");
      const renamed = await api.renameChat(chatId, nextTitle);
      setChats((current) => current.map((chat) => (chat.id === chatId ? renamed : chat)));
      setNotice(`Renamed chat to "${renamed.title}".`);
      pushEvent(`Chat renamed to "${renamed.title}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to rename chat");
    }
  }

  async function handleRenameProject(projectId: number, nextName: string) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;
    if (!nextName || nextName === targetProject.name) return;

    try {
      setError("");
      const renamed = await api.renameProject(projectId, nextName);
      setProjects((current) => current.map((project) => (project.id === projectId ? renamed : project)));
      setNotice(`Renamed project to "${renamed.name}".`);
      pushEvent(`Project renamed to "${renamed.name}"`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to rename project");
    }
  }

  async function handleDeleteChat(chatId: number) {
    const targetChat = chats.find((chat) => chat.id === chatId) ?? null;
    if (!targetChat) return;

    try {
      setError("");
      await api.deleteChat(chatId);

      const remainingChats = chats.filter((chat) => chat.id !== chatId);
      setChats(remainingChats);

      if (selectedChatId === chatId) {
        const fallbackProject = targetChat.project_id
          ? projects.find((project) => project.id === targetChat.project_id) ?? null
          : null;
        setSelectedProjectId(fallbackProject?.id ?? null);
        setSelectedChatId(fallbackProject?.default_chatroom_id ?? remainingChats[0]?.id ?? null);
        if (!fallbackProject && remainingChats.length === 0) {
          setMessages([]);
          setChatCards([]);
        }
      }

      setNotice(`Deleted chat "${targetChat.title}".`);
      pushEvent(`Deleted chat "${targetChat.title}"`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to delete chat");
    }
  }

  async function handleDeleteProject(projectId: number) {
    const targetProject = projects.find((project) => project.id === projectId) ?? null;
    if (!targetProject) return;

    try {
      setError("");
      await api.deleteProject(projectId);

      const remainingProjects = projects.filter((project) => project.id !== projectId);
      setProjects(remainingProjects);

      if (selectedProjectId === projectId || selectedChatId === targetProject.default_chatroom_id) {
        setSelectedProjectId(null);
        setSelectedChatId(chats[0]?.id ?? null);
        if (chats.length === 0) {
          setMessages([]);
          setChatCards([]);
        }
      }

      setNotice(`Deleted project "${targetProject.name}".`);
      pushEvent(`Deleted project "${targetProject.name}"`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to delete project");
    }
  }

  async function handleQuickCreateProject() {
    const pickerWindow = window as Window & {
      showDirectoryPicker?: () => Promise<{ name: string }>;
    };

    if (!pickerWindow.showDirectoryPicker) {
      setError("Current browser does not support directory picking.");
      return;
    }

    try {
      setCreatingProject(true);
      setError("");
      const directoryHandle = await pickerWindow.showDirectoryPicker();
      const projectName = directoryHandle.name.trim();
      if (!projectName) return;

      const created = await api.createProject({
        name: projectName,
        description: "",
        agent_names: ["assistant"],
      });

      const nextProjects = await api.getProjects();
      setProjects(nextProjects);
      setSelectedProjectId(created.id);
      setSelectedChatId(created.default_chatroom_id);
      setActiveTab("chat");
      setNotice(`Created project "${created.name}" from selected directory.`);
      pushEvent(`Created project "${created.name}" from directory picker`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      const message =
        nextError instanceof Error && nextError.name === "AbortError"
          ? ""
          : nextError instanceof Error
            ? nextError.message
            : "Failed to create project from directory";
      setError(message);
    } finally {
      setCreatingProject(false);
    }
  }

  async function handleSaveGlobal(payload: {
    provider: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
    default_model: string;
  }) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveGlobalConfig(payload);
      const refreshed = await api.getConfig();
      setConfig(refreshed);
      setNotice("Global config saved.");
      pushEvent("Global config saved", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save global config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveAgent(
    agentName: string,
    payload: {
      provider?: { baseUrl: string; apiKey: string; models: Array<{ id: string; name: string }> };
      default_model?: string;
      role?: {
        title?: string;
        responsibilities?: string[];
        rules?: string[];
      };
      soul?: {
        identity?: string;
        values?: string[];
        style?: string;
        quirks?: string;
      };
      tools?: string[];
      skills?: string[];
    },
  ) {
    try {
      setSavingConfig(true);
      setError("");
      await api.saveAgentConfig(agentName, payload);
      const refreshed = await api.getConfig();
      setConfig(refreshed);
      setNotice(`${agentName} config saved.`);
      pushEvent(`${agentName} config saved`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to save agent config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleReloadConfig() {
    try {
      setSavingConfig(true);
      setError("");
      await api.reloadConfig();
      const refreshed = await api.getConfig();
      setConfig(refreshed);
      setNotice("Config reloaded.");
      pushEvent("Configuration reloaded", "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to reload config");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleTestAgentConfig(agentName: string) {
    try {
      setSavingConfig(true);
      setError("");
      const result = await api.testConfig(agentName);
      setNotice(`Connection successful for ${result.agent} (${result.model}).`);
      pushEvent(`Connection test succeeded for ${result.agent}`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Failed to test agent config");
      pushEvent(`Connection test failed for ${agentName}`, "error");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleApproveGate(pipelineId: number) {
    try {
      setError("");
      await api.approvePipeline(pipelineId);
      setNotice(`Approved pipeline gate for #${pipelineId}.`);
      pushEvent(`Approved pipeline gate #${pipelineId}`, "success");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to approve gate";
      setError(message);
      pushEvent(`Approve failed for pipeline #${pipelineId}`, "error");
      throw nextError;
    }
  }

  async function handleRejectGate(pipelineId: number) {
    try {
      setError("");
      await api.rejectPipeline(pipelineId);
      setNotice(`Rejected pipeline gate for #${pipelineId}.`);
      pushEvent(`Rejected pipeline gate #${pipelineId}`, "warning");
      window.setTimeout(() => setNotice(""), 3000);
    } catch (nextError) {
      const message = nextError instanceof Error ? nextError.message : "Failed to reject gate";
      setError(message);
      pushEvent(`Reject failed for pipeline #${pipelineId}`, "error");
      throw nextError;
    }
  }

  return (
    <div className={`app-shell ${activeTab === "chat" ? "app-shell--chat" : ""}`}>
      <AppSidebar
        chats={chats}
        selectedChatId={selectedChatId}
        onSelectChat={(chatId) => {
          const nextChat = chats.find((chat) => chat.id === chatId) ?? null;
          setSelectedChatId(chatId);
          setSelectedProjectId(nextChat?.project_id ?? null);
          setActiveTab("chat");
          setSidebarDrawerOpen(false);
          pushEvent(`Opened chat #${chatId}`, "info");
        }}
        onCreateChat={handleCreateChat}
        onRenameChat={handleRenameChat}
        onDeleteChat={handleDeleteChat}
        projects={projects}
        selectedProjectId={selectedProjectId}
        onSelectProject={(projectId) => {
          const nextProject = projects.find((project) => project.id === projectId) ?? null;
          setSelectedProjectId(projectId);
          setSelectedChatId(nextProject?.default_chatroom_id ?? null);
          setActiveTab("chat");
          setSidebarDrawerOpen(false);
          pushEvent(`Opened project "${nextProject?.name || projectId}"`, "info");
        }}
        onQuickCreateProject={handleQuickCreateProject}
        onCreateProjectChat={handleCreateProjectChat}
        onReorderProjects={handleReorderProjects}
        onRenameProject={handleRenameProject}
        onDeleteProject={handleDeleteProject}
        drawerOpen={sidebarDrawerOpen}
        onCloseDrawer={() => setSidebarDrawerOpen(false)}
      />

      {activeTab === "chat" && sidebarDrawerOpen ? (
        <button
          type="button"
          className="mobile-drawer-backdrop mobile-drawer-backdrop--left"
          onClick={() => setSidebarDrawerOpen(false)}
          aria-label="Close navigation"
        />
      ) : null}

      <main className={`main-surface ${activeTab === "chat" ? "main-surface--chat" : ""}`}>
        {activeTab !== "chat" ? (
          <>
            <header className="surface-header surface-header--console">
              <div className="topnav-shell">
                <div className="topnav-shell__content">
                  <div className="dashboard-header">
                    <div className="dashboard-header__breadcrumb">
                      <span className="dashboard-header__breadcrumb-link">Catown</span>
                      <span className="dashboard-header__breadcrumb-sep">/</span>
                      <span className="dashboard-header__breadcrumb-current">
                        {activeTab === "projects" && "Projects"}
                        {activeTab === "config" && "Global Settings"}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="topnav-shell__actions topbar-status">
                  <span className="soft-pill">
                    <span className="status-dot topbar-status-dot" />
                    <span>{agents.filter((agent) => agent.is_active).length} agents</span>
                  </span>
                  <span className="soft-pill mono-pill">{projects.length} rooms</span>
                  <button
                    type="button"
                    className="btn btn--sm btn--icon settings-icon-btn"
                    onClick={toggleConfigTab}
                    aria-label={activeTab === "config" ? "Back to chat" : "Open settings"}
                    title={activeTab === "config" ? "Back to chat" : "Settings"}
                  >
                    <span className="settings-icon-glyph" aria-hidden="true">⚙</span>
                  </button>
                </div>
              </div>
            </header>

            <section className="content-header">
              <div className="surface-header-copy">
                <h2 className="page-title">
                  {activeTab === "projects" && "Project center"}
                  {activeTab === "config" && "Global configuration"}
                </h2>
                <p className="page-sub">
                  {activeTab === "projects" && "Create rooms, assign agents, and switch sessions from one place."}
                  {activeTab === "config" &&
                    "Manage global defaults and edit every agent's model, role, soul, tools, and skills in one place."}
                </p>
              </div>
              <div className="page-meta">
                <span className="soft-pill">V2 preview</span>
                <span className="soft-pill">React shell</span>
              </div>
            </section>
          </>
        ) : null}

        {notice ? <div className="notice-banner">{notice}</div> : null}
        {error ? <div className="error-banner">{error}</div> : null}

        {activeTab === "chat" ? (
          <ChatTab
            chat={activeChat}
            project={selectedProject}
            agents={agents}
            messages={messages}
            optimisticMessages={optimisticMessages}
            loading={loadingMessages}
            sending={sendingMessage}
            refreshing={refreshingMessages}
            creatingProjectFromChat={creatingProjectFromChat}
            connectionState={connectionState}
            cards={chatCards}
            events={chatEvents}
            onSend={handleSendMessage}
            onOpenWorkspace={handleOpenWorkspace}
            onOpenSidebar={() => {
              setActivityDrawerOpen(false);
              setSidebarDrawerOpen(true);
            }}
            onOpenActivity={() => {
              setSidebarDrawerOpen(false);
              setActivityDrawerOpen(true);
            }}
            activityDrawerOpen={activityDrawerOpen}
            onCloseActivity={() => setActivityDrawerOpen(false)}
            onOpenSettings={toggleConfigTab}
            onRefresh={() => refreshMessages(true)}
            onApproveGate={handleApproveGate}
            onRejectGate={handleRejectGate}
            onCreateProjectFromChat={handleCreateProjectFromCurrentChat}
          />
        ) : null}

        {activeTab === "projects" ? (
          <ProjectsTab
            projects={projects}
            agents={agents}
            selectedProjectId={selectedProjectId}
            creating={creatingProject}
            onCreateProject={handleCreateProject}
            onSelectProject={(projectId) => {
              const nextProject = projects.find((project) => project.id === projectId) ?? null;
              setSelectedProjectId(projectId);
              setSelectedChatId(nextProject?.default_chatroom_id ?? null);
              setActiveTab("chat");
              pushEvent(`Opened project "${nextProject?.name || projectId}"`, "info");
            }}
          />
        ) : null}

        {activeTab === "config" ? (
          <ConfigTab
            config={config}
            saving={savingConfig}
            onBackToChat={() => setActiveTab("chat")}
            onSaveGlobal={handleSaveGlobal}
            onSaveAgent={handleSaveAgent}
            onReload={handleReloadConfig}
            onTestAgentConfig={handleTestAgentConfig}
          />
        ) : null}
      </main>
    </div>
  );
}

export default App;
