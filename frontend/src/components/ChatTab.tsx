import { FormEvent, KeyboardEvent, MouseEvent, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import type {
  AgentInfo,
  ChatCardItem,
  ChatEventItem,
  ChatSummary,
  MessageItem,
  MessageStreamStep,
  ProjectSummary,
} from "../types";

const LOCAL_OVERLAY_STORAGE_KEY = "catown:chat-local-overlay";
const THREAD_AUTO_SCROLL_THRESHOLD = 72;
const LARGE_MARKDOWN_HIGHLIGHT_LIMIT = 12000;

function overlayScopeKey(chatId: number | null) {
  return chatId === null ? "pending" : `chat:${chatId}`;
}

function readOverlayStore() {
  if (typeof window === "undefined") return {} as Record<string, MessageItem[]>;
  try {
    const raw = window.localStorage.getItem(LOCAL_OVERLAY_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, MessageItem[]>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeOverlayStore(store: Record<string, MessageItem[]>) {
  if (typeof window === "undefined") return;
  const nextEntries = Object.entries(store).filter(([, value]) => Array.isArray(value) && value.length > 0);
  if (nextEntries.length === 0) {
    window.localStorage.removeItem(LOCAL_OVERLAY_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(LOCAL_OVERLAY_STORAGE_KEY, JSON.stringify(Object.fromEntries(nextEntries)));
}

function readOverlayMessages(chatId: number | null) {
  const store = readOverlayStore();
  return store[overlayScopeKey(chatId)] ?? [];
}

function writeOverlayMessages(chatId: number | null, messages: MessageItem[]) {
  const store = readOverlayStore();
  const key = overlayScopeKey(chatId);
  if (messages.length === 0) {
    delete store[key];
  } else {
    store[key] = messages;
  }
  writeOverlayStore(store);
}

function migrateOverlayMessages(fromChatId: number | null, toChatId: number | null) {
  const store = readOverlayStore();
  const fromKey = overlayScopeKey(fromChatId);
  const toKey = overlayScopeKey(toChatId);
  if (fromKey === toKey) return store[toKey] ?? [];

  const fromMessages = store[fromKey] ?? [];
  if (fromMessages.length === 0) return store[toKey] ?? [];

  const nextMessages = [...(store[toKey] ?? []), ...fromMessages].sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
  store[toKey] = nextMessages;
  delete store[fromKey];
  writeOverlayStore(store);
  return nextMessages;
}

type ChatTabProps = {
  chat: ChatSummary | null;
  project: ProjectSummary | null;
  agents: AgentInfo[];
  messages: MessageItem[];
  optimisticMessages: MessageItem[];
  cards: ChatCardItem[];
  loading: boolean;
  sending: boolean;
  refreshing: boolean;
  creatingProjectFromChat: boolean;
  connectionState: "connected" | "connecting" | "disconnected";
  events: ChatEventItem[];
  onSend: (content: string) => Promise<void>;
  onOpenWorkspace: () => Promise<void>;
  onOpenSidebar: () => void;
  onOpenActivity: () => void;
  activityDrawerOpen: boolean;
  onCloseActivity: () => void;
  onOpenSettings: () => void;
  onRefresh: () => Promise<void>;
  onApproveGate: (pipelineId: number) => Promise<void>;
  onRejectGate: (pipelineId: number) => Promise<void>;
  onCreateProjectFromChat: (payload: {
    name: string;
    description: string;
    agent_names: string[];
  }) => Promise<void>;
};

type ToolMergeCard = {
  id: string;
  kind: "tool_merge";
  created_at: string;
  source?: string;
  agent?: string;
  tool?: string;
  count: number;
  items: ChatCardItem[];
};

type ThreadCard = ChatCardItem | ToolMergeCard;
type ParsedLlmConversation = {
  meta: string;
  outbound: string;
  inbound: string;
};

type ThreadItem =
  | {
      id: string;
      sortKey: string;
      kind: "message";
      message: MessageItem;
    }
  | {
      id: string;
      sortKey: string;
      kind: "card";
      card: ThreadCard;
    }
  | {
      id: string;
      sortKey: string;
      kind: "activity_batch";
      cards: ThreadCard[];
    };

const EMPTY_THREAD_CARDS: ThreadCard[] = [];
const llmConversationMarkdownCache = new Map<string, ParsedLlmConversation>();

function rememberLlmConversationMarkdown(content: string, parsed: ParsedLlmConversation) {
  if (content.length < 256) return;
  if (llmConversationMarkdownCache.size >= 120) {
    const oldestKey = llmConversationMarkdownCache.keys().next().value;
    if (typeof oldestKey === "string") {
      llmConversationMarkdownCache.delete(oldestKey);
    }
  }
  llmConversationMarkdownCache.set(content, parsed);
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function toneLabel(tone: ChatEventItem["tone"]) {
  switch (tone) {
    case "success":
      return "Success";
    case "warning":
      return "Warning";
    case "error":
      return "Error";
    case "info":
      return "Info";
    default:
      return "Event";
  }
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

function oneLinePreview(value: string | undefined, fallback: string, limit = 96) {
  const normalized = value?.replace(/\s+/g, " ").trim() ?? "";
  if (!normalized) return fallback;
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit)}...`;
}

function threadItemSortWeight(item: Extract<ThreadItem, { kind: "message" | "card" }>) {
  if (item.kind === "card") return 1;
  if (item.message.message_type === "user" || !item.message.agent_name) return 0;
  return 2;
}

function cardBadge(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      return "LLM";
    case "tool_call":
      return "TOOL";
    case "tool_merge":
      return "TOOLS";
    case "stage_start":
      return "STAGE";
    case "stage_end":
      return "DONE";
    case "gate_blocked":
      return "GATE";
    case "gate_approved":
      return "OK";
    case "gate_rejected":
      return "BACK";
    case "skill_inject":
      return "SKILL";
    case "agent_message":
      return "MSG";
    case "boss_instruction":
      return "BOSS";
    default:
      return "CARD";
  }
}

function cardTitle(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      return `${card.agent || "assistant"} contacting LLM`;
    case "tool_call":
      return card.tool || "Tool call";
    case "tool_merge":
      return `${card.tool || "tool"} x${card.count}`;
    case "stage_start":
      return card.display_name || card.stage || "Stage started";
    case "stage_end":
      return `${card.stage || "Stage"} completed`;
    case "gate_blocked":
      return `Manual gate · ${card.display_name || card.stage || "approval needed"}`;
    case "gate_approved":
      return `Gate approved · ${card.stage || "pipeline"}`;
    case "gate_rejected":
      return `Gate rejected · ${card.from_stage || "stage"} -> ${card.to_stage || "rollback"}`;
    case "skill_inject":
      return `${card.agent || "agent"} skill injection`;
    case "agent_message":
      return `${card.from_agent || "agent"} -> ${card.to_agent || "team"}`;
    case "boss_instruction":
      return `Instruction for ${card.agent || "agent"}`;
    default:
      return "Runtime event";
  }
}

function cardSummary(card: ThreadCard) {
  const rawSummary = (() => {
    switch (card.kind) {
      case "llm_call":
        return card.response || "Model response captured.";
      case "tool_call":
        return card.result || "Tool execution recorded.";
      case "tool_merge":
        return `${card.count} consecutive ${card.tool || "tool"} calls from ${card.agent || "agent"}.`;
      case "stage_start":
        return card.summary || "A pipeline stage has started.";
      case "stage_end":
        return card.summary || "A pipeline stage has completed.";
      case "gate_blocked":
        return "Waiting for manual approval before continuing.";
      case "gate_approved":
        return "Manual gate approved.";
      case "gate_rejected":
        return `Rollback to ${card.to_stage || "previous stage"} requested.`;
      case "skill_inject":
        return card.skills?.map((skill) => skill.name).filter(Boolean).join(", ") || "Skills injected.";
      case "agent_message":
        return card.content || "Agent handoff message.";
      case "boss_instruction":
        return card.content_preview || "Boss instruction recorded.";
      default:
        return "";
    }
  })();

  if (rawSummary.length <= 180) return rawSummary;
  return `${rawSummary.slice(0, 180)}...`;
}

function cardActorName(card: ThreadCard) {
  if ("agent" in card && typeof card.agent === "string" && card.agent) {
    return card.agent;
  }

  if ("from_agent" in card && typeof card.from_agent === "string" && card.from_agent) {
    return card.from_agent;
  }

  if ("to_agent" in card && typeof card.to_agent === "string" && card.to_agent) {
    return card.to_agent;
  }

  return "system";
}

function messageStepStateFromCard(card: ThreadCard): MessageStreamStep["state"] {
  if ("success" in card && card.success === false) return "error";
  if (card.kind === "gate_blocked" || card.kind === "gate_rejected") return "error";
  return "done";
}

function messageStepSummaryFromCard(card: ThreadCard) {
  return compactCardSummary(card);
}

function messageStepDetailFromCard(card: ThreadCard) {
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
    case "tool_merge":
      return card.items
        .map(
          (item, index) =>
            `### #${index + 1} ${item.tool || card.tool || "tool"}\n\n${item.arguments ? `${markdownSection("Arguments", prettyJson(item.arguments), { language: "json" })}\n\n` : ""}${item.result ? isJsonContent(item.result) ? markdownSection("Result", prettyJson(item.result), { language: "json" }) : markdownSection("Result", item.result, { asMarkdown: true }) : ""}`,
        )
        .join("\n\n");
    case "stage_start":
    case "stage_end":
      return card.summary || card.content || card.stage || "";
    case "skill_inject":
      return card.skills?.map((skill) => `${skill.name || "skill"}${skill.hint ? `\n${skill.hint}` : ""}`).join("\n\n") || "";
    case "agent_message":
      return card.content || "";
    case "boss_instruction":
      return card.content_preview || "";
    case "gate_blocked":
      return `Waiting for approval${card.display_name ? `: ${card.display_name}` : ""}`;
    case "gate_rejected":
      return `Rollback target: ${card.to_stage || "previous stage"}`;
    default:
      return cardSummary(card);
  }
}

function CopyTextButton({ content, title }: { content: string; title: string }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    },
    [],
  );

  const handleClick = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      className={`chat-copy-inline-btn ${copied ? "is-copied" : ""}`}
      onClick={(event) => void handleClick(event)}
      title={title}
      aria-label={title}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function renderJsonCollapse(
  badge: string,
  label: string,
  content: string | undefined,
  options?: { pretty?: boolean; open?: boolean; copyLabel?: string },
) {
  if (!content) return null;
  const prepared = options?.pretty === false ? content : prettyJson(content);
  return (
    <details className="chat-json-collapse" open={options?.open}>
      <summary className="chat-json-summary">
        <span className="chat-json-summary__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-json-label">{label}</span>
        </span>
        <CopyTextButton content={prepared} title={options?.copyLabel || `Copy ${label}`} />
      </summary>
      <pre className="chat-json-content">{prepared}</pre>
    </details>
  );
}

function renderProgressTextBlock(badge: string, label: string, content: string | undefined) {
  if (!content) return null;
  return (
    <section className="chat-progress-detail-block">
      <div className="chat-progress-detail-block__header">
        <span className="chat-progress-detail-block__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-progress-detail-block__label">{label}</span>
        </span>
        <CopyTextButton content={content} title={`Copy ${label}`} />
      </div>
      {renderMarkdownContent(content, "chat-progress-detail-block__content")}
    </section>
  );
}

function renderProgressJsonBlock(badge: string, label: string, content: string | undefined) {
  if (!content) return null;
  return (
    <section className="chat-progress-detail-block">
      <div className="chat-progress-detail-block__header">
        <span className="chat-progress-detail-block__main">
          <span className="chat-json-badge">{badge}</span>
          <span className="chat-progress-detail-block__label">{label}</span>
        </span>
        <CopyTextButton content={prettyJson(content)} title={`Copy ${label}`} />
      </div>
      {renderMarkdownContent(`\`\`\`json\n${prettyJson(content)}\n\`\`\``, "chat-progress-detail-block__content")}
    </section>
  );
}

function buildMarkdownHeadingSection(title: string | null, body: string) {
  const normalizedBody = body.trim();
  if (!normalizedBody) return "";
  if (!title) return normalizedBody;
  return `### ${title}\n\n${normalizedBody}`;
}

function splitMarkdownHeadingSections(content: string) {
  const normalized = content.trim();
  if (!normalized) return [] as Array<{ title: string | null; body: string }>;

  const headingMatches = [...normalized.matchAll(/^###\s+(.+?)\s*$/gm)];
  if (headingMatches.length === 0) {
    return [{ title: null, body: normalized }];
  }

  const sections: Array<{ title: string | null; body: string }> = [];
  let cursor = 0;
  let activeTitle: string | null = null;

  for (const match of headingMatches) {
    const matchIndex = match.index ?? 0;
    const body = normalized.slice(cursor, matchIndex).trim();
    if (activeTitle !== null && body) {
      sections.push({ title: activeTitle, body });
    }
    activeTitle = match[1]?.trim() ?? null;
    cursor = matchIndex + match[0].length;
  }

  const tail = normalized.slice(cursor).trim();
  if (activeTitle !== null && tail) {
    sections.push({ title: activeTitle, body: tail });
  }

  return sections;
}

function isLikelyLlmStep(label: string, detailContent: string | undefined) {
  const sample = `${label}\n${detailContent ?? ""}`.toLowerCase();
  return (
    sample.includes("contacting llm") ||
    sample.includes("full prompt payload") ||
    sample.includes("raw response") ||
    sample.includes("current response draft") ||
    sample.includes("prompt sent to llm")
  );
}

function inferAgentNameFromLlmStepLabel(label: string) {
  const matched = label.match(/^(.+?)\s+contacting\s+llm$/i);
  if (matched?.[1]) return matched[1].trim();
  const firstWord = label.trim().split(/\s+/)[0];
  return firstWord || "agent";
}

function buildPlannedToolsMarkdown(toolCalls: ChatCardItem["tool_calls"]) {
  if (!toolCalls || toolCalls.length === 0) return "";
  return `### Planned Tools\n\n${toolCalls
    .map(
      (tool) =>
        `- **${tool.name || "tool"}**${
          tool.args_preview ? `\n  - args preview: \`${tool.args_preview.replace(/`/g, "'").replace(/\n/g, " ")}\`` : ""
        }`,
    )
    .join("\n")}`;
}

function parseLlmConversationMarkdown(content: string) {
  const cached = llmConversationMarkdownCache.get(content);
  if (cached) return cached;

  const sections = splitMarkdownHeadingSections(content);
  const metaSections: string[] = [];
  const outboundSections: string[] = [];
  const inboundSections: string[] = [];
  const uncategorizedSections: string[] = [];

  for (const section of sections) {
    const title = section.title?.trim().toLowerCase() ?? "";
    const rendered = buildMarkdownHeadingSection(section.title, section.body);
    if (!rendered) continue;

    if (title === "meta") {
      metaSections.push(rendered);
      continue;
    }

    if (
      title === "user message" ||
      title === "system prompt" ||
      title === "full prompt payload" ||
      title === "prompt sent to llm" ||
      title === "planned tools"
    ) {
      outboundSections.push(rendered);
      continue;
    }

    if (
      title === "current response draft" ||
      title === "response" ||
      title === "llm response" ||
      title === "raw response" ||
      title === "status"
    ) {
      inboundSections.push(rendered);
      continue;
    }

    uncategorizedSections.push(rendered);
  }

  if (sections.length === 1 && sections[0]?.title === null) {
    inboundSections.push(sections[0].body.trim());
  } else if (uncategorizedSections.length > 0) {
    if (outboundSections.length === 0) {
      outboundSections.push(...uncategorizedSections);
    } else {
      inboundSections.push(...uncategorizedSections);
    }
  }

  const parsed = {
    meta: metaSections.join("\n\n"),
    outbound: outboundSections.join("\n\n"),
    inbound: inboundSections.join("\n\n"),
  };
  rememberLlmConversationMarkdown(content, parsed);
  return parsed;
}

function renderLlmExchangePanel(
  direction: "outbound" | "inbound",
  actorName: string,
  content: string,
  metaLabel?: string,
) {
  if (!content.trim()) return null;

  const isOutbound = direction === "outbound";
  const badge = isOutbound ? "Prompt" : "Response";
  const title = isOutbound ? `Prompt · ${actorName} -> LLM` : `Response · LLM -> ${actorName}`;
  const avatarLabel = initials(actorName) || "AG";

  return (
    <section className={`llm-exchange-card llm-exchange-card--${direction}`}>
      <div className="llm-exchange-card__header">
        <div className="llm-exchange-card__heading">
          <span className={`llm-exchange-card__avatar llm-exchange-card__avatar--${direction}`} aria-hidden="true">
            {isOutbound ? (
              <>
                <svg viewBox="0 0 24 24" fill="none">
                  <path
                    d="M12 12a3.25 3.25 0 1 0 0-6.5 3.25 3.25 0 0 0 0 6.5ZM6 18.25c0-2.68 2.69-4.75 6-4.75s6 2.07 6 4.75"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                <span className="llm-exchange-card__avatar-tag">{avatarLabel}</span>
              </>
            ) : (
              <svg viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 3.5 14.3 8l4.95.72-3.58 3.49.84 4.92L12 14.88 7.49 17.13l.86-4.92L4.76 8.72 9.7 8 12 3.5Z"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </span>
          <span className={`llm-exchange-card__badge llm-exchange-card__badge--${direction}`}>
            {isOutbound ? (
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="M3.25 8h8.5M8.75 3.5 12.5 8l-3.75 4.5"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : (
              <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path
                  d="m8 2 1.24 2.76L12 6l-2.76 1.24L8 10 6.76 7.24 4 6l2.76-1.24L8 2Z"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinejoin="round"
                />
              </svg>
            )}
            <span>{badge}</span>
          </span>
          <strong>{title}</strong>
        </div>
        {metaLabel ? <span className="llm-exchange-card__meta">{metaLabel}</span> : null}
      </div>
      <div className="llm-exchange-card__body">
        {renderMarkdownContent(content, "llm-exchange-card__markdown", { highlight: false })}
      </div>
    </section>
  );
}

function renderLlmDetailLayout(
  agentName: string,
  content: {
    meta?: string;
    outbound?: string;
    inbound?: string;
  },
  options?: {
    responseMetaLabel?: string;
    className?: string;
  },
) {
  if (!content.meta && !content.outbound && !content.inbound) return null;

  return (
    <div className={options?.className ?? "llm-exchange-stack"}>
      {content.meta ? (
        <section className="llm-exchange-meta">
          <div className="llm-exchange-meta__header">
            <div className="llm-exchange-meta__heading">
              <span className="chat-json-badge">META</span>
              <strong>Execution Meta</strong>
            </div>
          </div>
          {renderMarkdownContent(content.meta, "llm-exchange-meta__content")}
        </section>
      ) : null}
      {content.outbound ? renderLlmExchangePanel("outbound", agentName, content.outbound) : null}
      {content.inbound
        ? renderLlmExchangePanel("inbound", agentName, content.inbound, options?.responseMetaLabel)
        : null}
    </div>
  );
}

function renderMarkdownContent(content: string | undefined, className: string, options?: { highlight?: boolean }) {
  if (!content) return null;
  const enableHighlight = options?.highlight ?? content.length <= LARGE_MARKDOWN_HIGHLIGHT_LIMIT;
  return (
    <div className={className}>
      <ReactMarkdown
        className="message-markdown"
        remarkPlugins={[remarkGfm]}
        rehypePlugins={enableHighlight ? [rehypeHighlight] : []}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
          table: ({ node: _node, ...props }) => (
            <div className="message-markdown__table-wrap">
              <table {...props} />
            </div>
          ),
          input: ({ node: _node, ...props }) =>
            props.type === "checkbox" ? <input {...props} disabled readOnly className="message-markdown__checkbox" /> : <input {...props} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function renderStreamingTextContent(content: string | undefined, className: string) {
  if (!content) return null;
  return (
    <div className={`${className} message-streaming-plain`}>
      <pre>{content}</pre>
    </div>
  );
}

function renderCardBody(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call":
      return (
        <>
          {renderMarkdownContent(card.response, "message-body chat-card-preview")}
          {card.tool_calls && card.tool_calls.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.tool_calls.map((toolCall, index) => (
                <span key={`${toolCall.name || "tool"}-${index}`} className="chat-card-chip">
                  {toolCall.name || "tool"}
                  {toolCall.args_preview ? `(${toolCall.args_preview})` : ""}
                </span>
              ))}
            </div>
          ) : null}
          {renderJsonCollapse("PROMPT", "System prompt", card.system_prompt, {
            pretty: false,
            copyLabel: "Copy system prompt",
          })}
          {renderJsonCollapse("INPUT", "Full prompt payload", card.prompt_messages, {
            copyLabel: "Copy full prompt payload",
          })}
          {renderJsonCollapse("RAW", "Raw LLM response", card.raw_response, {
            copyLabel: "Copy raw LLM response",
          })}
        </>
      );
    case "tool_call":
      return (
        <>
          {renderJsonCollapse("ARGS", `${card.tool || "tool"} input`, card.arguments, {
            copyLabel: `Copy ${card.tool || "tool"} input`,
          })}
          {renderJsonCollapse(card.success === false ? "ERROR" : "RESULT", `${card.tool || "tool"} output`, card.result, {
            pretty: false,
            open: card.success === false,
            copyLabel: `Copy ${card.tool || "tool"} output`,
          })}
        </>
      );
    case "tool_merge":
      return (
        <details className="chat-json-collapse">
          <summary className="chat-json-summary">
            <span className="chat-json-summary__main">
              <span className="chat-json-badge">LIST</span>
              <span className="chat-json-label">Merged tool calls</span>
            </span>
          </summary>
          <div className="chat-tool-merge-list">
            {card.items.map((item, index) => (
              <div key={`${item.id}-${index}`} className="chat-tool-merge-item">
                <div className="chat-tool-merge-item__meta">
                  <span>#{index + 1}</span>
                  <span>{typeof item.duration_ms === "number" ? `${item.duration_ms}ms` : "n/a"}</span>
                  <span>{item.success === false ? "failed" : "ok"}</span>
                </div>
                {item.arguments ? (
                  <div className="chat-json-block">
                    <div className="chat-json-block__header">
                      <span>Arguments</span>
                      <CopyTextButton content={prettyJson(item.arguments)} title="Copy tool arguments" />
                    </div>
                    <pre className="chat-json-content">{prettyJson(item.arguments)}</pre>
                  </div>
                ) : null}
                {item.result ? (
                  <div className="chat-json-block">
                    <div className="chat-json-block__header">
                      <span>Result</span>
                      <CopyTextButton content={item.result} title="Copy tool result" />
                    </div>
                    <pre className="chat-json-content">{item.result}</pre>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </details>
      );
    case "stage_start":
      return (
        <>
          {card.active_skills && card.active_skills.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.active_skills.map((skill) => (
                <span key={skill} className="chat-card-chip chat-card-chip--accent">
                  {skill}
                </span>
              ))}
            </div>
          ) : null}
          {card.expected_artifacts && card.expected_artifacts.length > 0 ? (
            <div className="chat-card-chip-row">
              {card.expected_artifacts.map((artifact) => (
                <span key={artifact} className="chat-card-chip">
                  {artifact}
                </span>
              ))}
            </div>
          ) : null}
        </>
      );
    case "stage_end":
      return renderMarkdownContent(card.summary, "message-body chat-card-preview");
    case "gate_blocked":
      return renderMarkdownContent("This stage is blocked until someone approves or rejects it.", "message-body chat-card-preview");
    case "gate_approved":
      return renderMarkdownContent("Pipeline can continue to the next stage.", "message-body chat-card-preview");
    case "gate_rejected":
      return renderMarkdownContent(`Rollback target: ${card.to_stage || "previous stage"}`, "message-body chat-card-preview");
    case "skill_inject":
      return (
        <>
          {card.skills && card.skills.length > 0 ? (
            <div className="chat-skill-list">
              {card.skills.map((skill, index) => (
                <div key={`${skill.name || "skill"}-${index}`} className="chat-skill-card">
                  <strong>{skill.name || "Unnamed skill"}</strong>
                  {renderMarkdownContent(skill.hint, "message-body chat-card-preview")}
                  {skill.guide ? (
                    renderJsonCollapse(
                      "GUIDE",
                      typeof skill.guide_tokens === "number" ? `~${skill.guide_tokens} words` : "Skill guide",
                      skill.guide,
                      {
                        pretty: false,
                        copyLabel: "Copy skill guide",
                      },
                    )
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          {renderJsonCollapse("ALL", "Agent skill set", card.agent_all_skills?.join("\n"), {
            pretty: false,
            copyLabel: "Copy agent skill set",
          })}
        </>
      );
    case "agent_message":
      return renderMarkdownContent(card.content, "message-body chat-card-preview");
    case "boss_instruction":
      return renderMarkdownContent(card.content_preview, "message-body chat-card-preview");
    default:
      return null;
  }
}

function renderCompactCardBody(card: ThreadCard) {
  switch (card.kind) {
    case "llm_call": {
      const metaBits = [
        card.model,
        typeof card.turn === "number" ? `turn ${card.turn}` : "",
        typeof card.duration_ms === "number" ? `${card.duration_ms}ms` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      const outboundSections = [
        card.system_prompt ? markdownSection("System Prompt", card.system_prompt, { language: "text" }) : "",
        card.prompt_messages ? markdownSection("Full Prompt Payload", prettyJson(card.prompt_messages), { language: "json" }) : "",
        buildPlannedToolsMarkdown(card.tool_calls),
      ]
        .filter(Boolean)
        .join("\n\n");
      const inboundSections = [
        card.response ? markdownSection("Response", card.response, { asMarkdown: true }) : "",
        card.raw_response ? markdownSection("Raw Response", prettyJson(card.raw_response), { language: "json" }) : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      const llmLayout = renderLlmDetailLayout(card.agent || "agent", {
        meta: metaBits ? `### Meta\n\n- ${metaBits}` : "",
        outbound: outboundSections,
        inbound: inboundSections,
      });
      if (llmLayout) return llmLayout;
      return (
        <div className="chat-progress-detail-stack">
          {renderProgressTextBlock("PROMPT", "Prompt sent to LLM", card.system_prompt)}
          {renderProgressJsonBlock("INPUT", "Full prompt payload", card.prompt_messages)}
          {card.tool_calls && card.tool_calls.length > 0 ? (
            <section className="chat-progress-detail-block">
              <div className="chat-progress-detail-block__header">
                <span className="chat-json-badge">TOOLS</span>
                <span className="chat-progress-detail-block__label">Tool calls proposed by the model</span>
              </div>
              <div className="chat-progress-tool-list">
                {card.tool_calls.map((toolCall, index) => (
                  <div key={`${toolCall.name || "tool"}-${index}`} className="chat-progress-tool-list__item">
                    <div className="chat-progress-tool-list__title">{toolCall.name || "tool"}</div>
                    {toolCall.args_preview ? (
                      <pre className="chat-progress-detail-block__content chat-progress-detail-block__content--inline">
                        {toolCall.args_preview}
                      </pre>
                    ) : (
                      <div className="chat-progress-tool-list__empty">No arguments preview</div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {renderProgressTextBlock("OUTPUT", "LLM response", card.response)}
          {renderProgressJsonBlock("RAW", "Raw LLM response", card.raw_response)}
        </div>
      );
    }
    case "tool_call":
      return (
        <div className="chat-progress-detail-stack">
          {renderProgressJsonBlock("ARGS", `${card.tool || "tool"} input`, card.arguments)}
          {renderProgressTextBlock(
            card.success === false ? "ERROR" : "RESULT",
            `${card.tool || "tool"} output`,
            card.result,
          )}
        </div>
      );
    case "tool_merge":
      return (
        <div className="chat-progress-detail-stack">
          {card.items.map((item, index) => (
            <section key={`${item.id}-${index}`} className="chat-progress-detail-block">
              <div className="chat-progress-detail-block__header">
                <span className="chat-json-badge">CALL</span>
                <span className="chat-progress-detail-block__label">
                  #{index + 1} · {item.tool || card.tool || "tool"}
                  {typeof item.duration_ms === "number" ? ` · ${item.duration_ms}ms` : ""}
                  {item.success === false ? " · failed" : ""}
                </span>
              </div>
              <div className="chat-progress-detail-stack chat-progress-detail-stack--nested">
                {renderProgressJsonBlock("ARGS", "Tool input", item.arguments)}
                {renderProgressTextBlock(
                  item.success === false ? "ERROR" : "RESULT",
                  "Tool output",
                  item.result,
                )}
              </div>
            </section>
          ))}
        </div>
      );
    case "stage_start":
    case "stage_end":
    case "gate_blocked":
    case "gate_approved":
    case "gate_rejected":
    case "skill_inject":
    case "agent_message":
    case "boss_instruction":
      return renderCardBody(card);
    default:
      return renderCardBody(card);
  }
}

function renderCardSurface(
  card: ThreadCard,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const gatePipelineId =
    "pipeline_id" in card && typeof card.pipeline_id === "number" ? card.pipeline_id : null;
  const isBlockingGate = card.kind === "gate_blocked" && gatePipelineId !== null;
  const gateBusy = gatePipelineId !== null && gateActionPipelineId === gatePipelineId;

  return (
    <article className="chat-tool-card">
      <div className="chat-tool-card__header">
        <div>
          <div className="chat-tool-card__title">
            <span className="chat-json-badge">{cardBadge(card)}</span>
            <span>{cardTitle(card)}</span>
          </div>
          <div className="chat-tool-card__detail">
            {card.source || "chatroom"}
            {"model" in card && card.model ? ` · ${card.model}` : ""}
            {"turn" in card && card.turn ? ` · turn ${card.turn}` : ""}
            {"duration_ms" in card && typeof card.duration_ms === "number" ? ` · ${card.duration_ms}ms` : ""}
            {"success" in card && typeof card.success === "boolean" ? ` · ${card.success ? "success" : "failed"}` : ""}
          </div>
        </div>
        <div className="chat-tool-card__detail">{formatTime(card.created_at)}</div>
      </div>

      {cardSummary(card) ? <div className="chat-card-summary">{cardSummary(card)}</div> : null}
      {renderCardBody(card)}
      {isBlockingGate ? (
        <div className="chat-card-actions">
          <button
            type="button"
            className="chat-card-action-btn chat-card-action-btn--approve"
            disabled={gateBusy}
            onClick={() => void onApproveGate(gatePipelineId)}
          >
            {gateBusy ? "Working..." : "Approve"}
          </button>
          <button
            type="button"
            className="chat-card-action-btn chat-card-action-btn--reject"
            disabled={gateBusy}
            onClick={() => void onRejectGate(gatePipelineId)}
          >
            Reject
          </button>
        </div>
      ) : null}
    </article>
  );
}

function compactCardMeta(card: ThreadCard) {
  const bits: string[] = [];
  if ("model" in card && card.model) bits.push(card.model);
  if (card.kind !== "tool_merge" && "tool" in card && card.tool) bits.push(card.tool);
  if ("duration_ms" in card && typeof card.duration_ms === "number") bits.push(`${card.duration_ms}ms`);
  if ("turn" in card && card.turn) bits.push(`turn ${card.turn}`);
  if ("success" in card && typeof card.success === "boolean") {
    bits.push(card.success ? "ok" : "failed");
  }
  return bits.join(" · ");
}

function compactCardDefaultOpen(card: ThreadCard, isLive: boolean) {
  return false;
}

function compactCardState(card: ThreadCard, isLive: boolean) {
  if (isLive) return "live";
  if (card.kind === "gate_blocked") return "blocked";
  if ("success" in card && card.success === false) return "error";
  return "done";
}

function compactCardStateLabel(card: ThreadCard, isLive: boolean) {
  const state = compactCardState(card, isLive);
  switch (state) {
    case "live":
      return "running";
    case "blocked":
      return "blocked";
    case "error":
      return "failed";
    default:
      return "done";
  }
}

function compactCardSummary(card: ThreadCard) {
  switch (card.kind) {
    case "tool_call":
      return oneLinePreview(card.result, `${card.tool || "Tool"} finished.`);
    case "tool_merge":
      return `${card.count} calls · ${card.tool || "tool"} · latest ${oneLinePreview(card.items[card.items.length - 1]?.result, "completed")}`;
    case "llm_call":
      return oneLinePreview(card.response, "Model responded.");
    case "stage_start":
      return oneLinePreview(card.summary || card.content, card.display_name || card.stage || "Stage started");
    case "stage_end":
      return oneLinePreview(card.summary, card.stage || "Stage completed");
    case "skill_inject":
      return oneLinePreview(
        card.skills?.map((skill) => skill.name).filter(Boolean).join(", "),
        "Skills injected.",
      );
    case "agent_message":
      return oneLinePreview(card.content, "Agent handoff");
    case "boss_instruction":
      return oneLinePreview(card.content_preview, "Instruction recorded");
    default:
      return oneLinePreview(cardSummary(card), "Runtime event");
  }
}

function renderCompactCard(
  card: ThreadCard,
  groupKey: string,
  itemIndex: number,
  isLive: boolean,
  isCurrent: boolean,
  isExpanded: boolean,
  onToggle: (groupKey: string, cardId: string) => void,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const detail = compactCardSummary(card);
  const meta = compactCardMeta(card);
  const state = compactCardState(card, isLive);
  const gatePipelineId =
    "pipeline_id" in card && typeof card.pipeline_id === "number" ? card.pipeline_id : null;
  const isBlockingGate = card.kind === "gate_blocked" && gatePipelineId !== null;
  const gateBusy = gatePipelineId !== null && gateActionPipelineId === gatePipelineId;

  return (
    <details
      key={card.id}
      className={`chat-progress-item chat-progress-item--${state} ${isCurrent ? "is-current" : ""}`}
      open={isExpanded}
    >
      <summary
        className="chat-progress-item__summary"
        onClick={(event) => {
          event.preventDefault();
          onToggle(groupKey, card.id);
        }}
      >
        <span className={`chat-progress-item__state chat-progress-item__state--${state}`} aria-hidden="true">
          {state === "done" ? "✓" : state === "error" ? "!" : state === "blocked" ? "!" : ""}
        </span>
        <span className="chat-progress-item__index">{itemIndex + 1}</span>
        <span className="chat-progress-item__copy">
          <span className="chat-progress-item__heading">
            <span className="chat-json-badge">{cardBadge(card)}</span>
            <strong>{cardTitle(card)}</strong>
          </span>
          <small>{detail}</small>
        </span>
        <span className="chat-progress-item__meta">
          {meta ? <span>{meta}</span> : null}
          <span>{formatTime(card.created_at)}</span>
          <span className={`chat-progress-item__label chat-progress-item__label--${state}`}>
            {compactCardStateLabel(card, isLive)}
          </span>
        </span>
        <span className="chat-progress-item__toggle" aria-hidden="true">
          ▸
        </span>
      </summary>

      <div className="chat-progress-item__body">
        {renderCompactCardBody(card)}
        {isBlockingGate ? (
          <div className="chat-card-actions">
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--approve"
              disabled={gateBusy}
              onClick={() => void onApproveGate(gatePipelineId)}
            >
              {gateBusy ? "Working..." : "Approve"}
            </button>
            <button
              type="button"
              className="chat-card-action-btn chat-card-action-btn--reject"
              disabled={gateBusy}
              onClick={() => void onRejectGate(gatePipelineId)}
            >
              Reject
            </button>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function renderCard(
  card: ThreadCard,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const sender = cardActorName(card);

  return (
    <div key={card.id} className="chat-card-row">
      <div className="chat-avatar assistant">{initials(sender)}</div>
      <div className="chat-group-messages chat-card-stack">
        {renderCardSurface(card, gateActionPipelineId, onApproveGate, onRejectGate)}
      </div>
    </div>
  );
}

function renderActivityBatch(
  batchId: string,
  cards: ThreadCard[],
  isCurrentBatch: boolean,
  currentActivityAgentName: string | null,
  expandedProgressCards: Record<string, string | null>,
  onToggleProgressCard: (groupKey: string, cardId: string) => void,
  gateActionPipelineId: number | null,
  onApproveGate: (pipelineId: number) => Promise<void>,
  onRejectGate: (pipelineId: number) => Promise<void>,
) {
  const groups = new Map<
    string,
    {
      name: string;
      cards: ThreadCard[];
      latestAt: string;
    }
  >();

  for (const card of cards) {
    const actor = cardActorName(card);
    const existing = groups.get(actor);
    if (existing) {
      existing.cards.push(card);
      existing.latestAt = card.created_at;
      continue;
    }
    groups.set(actor, {
      name: actor,
      cards: [card],
      latestAt: card.created_at,
    });
  }

  const orderedGroups = Array.from(groups.values());
  const fallbackActiveName =
    currentActivityAgentName ||
    [...cards]
      .reverse()
      .map((card) => cardActorName(card))
      .find((name) => name !== "system") ||
    orderedGroups[orderedGroups.length - 1]?.name ||
    "system";

  return (
    <div key={batchId} className="chat-card-row">
      <div className="chat-avatar assistant">{initials(fallbackActiveName)}</div>
      <div className="chat-group-messages chat-card-stack">
        <section className="chat-activity-batch">
          <div className="chat-activity-batch__header">
            <div>
              <div className="chat-tool-card__title">
                <span className="chat-json-badge">FLOW</span>
                <span>Agent activity</span>
              </div>
              <div className="chat-tool-card__detail">
                {orderedGroups.length} agent{orderedGroups.length === 1 ? "" : "s"} · {cards.length} action
                {cards.length === 1 ? "" : "s"}
              </div>
            </div>
            <div className="chat-tool-card__detail">
              {isCurrentBatch ? "live" : formatTime(cards[cards.length - 1]?.created_at || new Date().toISOString())}
            </div>
          </div>

          <div className="chat-activity-batch__groups">
            {orderedGroups.map((group) => {
              const isActiveGroup = group.name === fallbackActiveName;
              const orderedCards = group.cards;
              const groupKey = `${batchId}:${group.name}`;
              const activeCardId =
                isActiveGroup && isCurrentBatch ? group.cards[group.cards.length - 1]?.id ?? null : null;
              const expandedCardId = expandedProgressCards[groupKey] ?? activeCardId;
              return (
                <section
                  key={`${batchId}-${group.name}`}
                  className={`chat-agent-activity ${isActiveGroup ? "is-active" : ""}`}
                >
                  <div className="chat-agent-activity__summary">
                    <span className={`chat-agent-activity__status ${isActiveGroup ? "is-live" : ""}`} />
                    <span className="chat-agent-activity__avatar">{initials(group.name)}</span>
                    <span className="chat-agent-activity__copy">
                      <strong>{group.name}</strong>
                      <small>
                        {group.cards.length} step{group.cards.length === 1 ? "" : "s"} · {formatTime(group.latestAt)}
                      </small>
                    </span>
                    <span className={`chat-agent-activity__pill ${isActiveGroup ? "is-live" : ""}`}>
                      {isActiveGroup ? "active" : "summary"}
                    </span>
                  </div>

                  <div className="chat-agent-activity__body">
                    {orderedCards.map((card, index) =>
                      renderCompactCard(
                        card,
                        groupKey,
                        index,
                        isActiveGroup && card.id === group.cards[group.cards.length - 1]?.id && isCurrentBatch,
                        isActiveGroup && card.id === group.cards[group.cards.length - 1]?.id && isCurrentBatch,
                        expandedCardId === card.id,
                        onToggleProgressCard,
                        gateActionPipelineId,
                        onApproveGate,
                        onRejectGate,
                      ),
                    )}
                  </div>
                </section>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

function renderMessage(
  message: MessageItem,
  copiedMessageId: number | null,
  onCopyMessage: (message: MessageItem) => Promise<void>,
  expandedStepId: string | null,
  onToggleStep: (messageId: number, stepId: string) => void,
  fallbackCards: ThreadCard[] = [],
) {
  const isAssistant = Boolean(message.agent_name);
  const sender = message.agent_name || "You";
  const streamSteps =
    message.streamSteps && message.streamSteps.length > 0
      ? message.streamSteps.map((step) => ({
          id: step.id,
          label: step.label,
          detail: step.detail,
          detailContent: step.detailContent,
          state: step.state,
        }))
      : fallbackCards.map((card, index) => ({
          id: `${message.id}-card-${card.id}-${index}`,
          label: cardTitle(card),
          detail: messageStepSummaryFromCard(card),
          detailContent: messageStepDetailFromCard(card),
          state: messageStepStateFromCard(card),
        }));
  const hasStreamSteps = streamSteps.length > 0;
  const showReplyAfterTrace = isAssistant && hasStreamSteps;
  const placeholderCopy =
    message.agent_name && message.agent_name !== "pipeline"
      ? `${message.agent_name} is working...`
      : "Agent is working...";
  const messageBodyContent = message.content || (message.isStreaming ? placeholderCopy : "");
  const messageBodyClassName = `message-body ${message.isStreaming ? "message-body--streaming" : ""} ${
    showReplyAfterTrace ? "message-body--after-trace" : ""
  }`;
  const messageBody = message.isStreaming
    ? renderStreamingTextContent(messageBodyContent, messageBodyClassName)
    : renderMarkdownContent(messageBodyContent, messageBodyClassName);
  const messageTrace = hasStreamSteps ? (
    <div className={`message-stream-trace ${showReplyAfterTrace ? "message-stream-trace--top" : ""}`}>
      {streamSteps.map((step: MessageStreamStep) => (
        <details
          key={step.id}
          className={`message-stream-step message-stream-step--${step.state}`}
          open={expandedStepId === step.id}
        >
          <summary
            className="message-stream-step__summary"
            onClick={(event) => {
              event.preventDefault();
              onToggleStep(message.id, step.id);
            }}
          >
            <span className="message-stream-step__state" aria-hidden="true">
              {step.state === "done" ? "✓" : step.state === "error" ? "!" : ""}
            </span>
            <span className="message-stream-step__copy">
              <strong>{step.label}</strong>
              {step.detail ? <small>{step.detail}</small> : null}
            </span>
            <span className="message-stream-step__toggle" aria-hidden="true">
              ▸
            </span>
          </summary>
          {expandedStepId === step.id && (step.detailContent || step.detail) ? (
            <div className="message-stream-step__detail">
              {(() => {
                const detailSource = step.detailContent || step.detail || "";
                const isLlmStep = isLikelyLlmStep(step.label, detailSource);
                if (isLlmStep) {
                  const llmDetail = parseLlmConversationMarkdown(detailSource);
                  const structuredDetail = renderLlmDetailLayout(inferAgentNameFromLlmStepLabel(step.label), llmDetail, {
                    className: "message-stream-step__detail-content llm-exchange-stack",
                  });
                  return structuredDetail || renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
                }

                if (step.state === "live") {
                  return renderStreamingTextContent(detailSource, "message-stream-step__detail-content");
                }

                return renderMarkdownContent(detailSource, "message-stream-step__detail-content", { highlight: false });
              })()}
            </div>
          ) : null}
        </details>
      ))}
    </div>
  ) : null;

  return (
    <div className={`chat-group ${isAssistant ? "" : "user"}`}>
      <div className={`chat-avatar ${isAssistant ? "assistant" : "user"}`}>{initials(sender)}</div>

      <div className="chat-group-messages">
        <div className={`chat-bubble ${message.isStreaming ? "chat-bubble--streaming" : ""}`}>
          {showReplyAfterTrace ? (
            <>
              {messageTrace}
              {messageBody}
            </>
          ) : (
            <>
              {messageBody}
              {messageTrace}
            </>
          )}
        </div>

        <div className="chat-group-footer">
          <span className="chat-sender-name">{sender}</span>
          <span className="chat-group-timestamp">{formatTime(message.created_at)}</span>
          {message.isStreaming ? <span className="soft-pill">streaming</span> : null}
          {!message.localOnly ? (
            <button type="button" className="chat-footer-btn" onClick={() => void onCopyMessage(message)}>
              {copiedMessageId === message.id ? "Copied" : "Copy"}
            </button>
          ) : (
            <span className="soft-pill">local</span>
          )}
        </div>
      </div>
    </div>
  );
}

type MessageRowProps = {
  message: MessageItem;
  copiedMessageId: number | null;
  onCopyMessage: (message: MessageItem) => void | Promise<void>;
  expandedStepId: string | null;
  onToggleStep: (messageId: number, stepId: string) => void;
  fallbackStepCards: ThreadCard[];
};

const MessageRow = memo(
  function MessageRow({
    message,
    copiedMessageId,
    onCopyMessage,
    expandedStepId,
    onToggleStep,
    fallbackStepCards,
  }: MessageRowProps) {
    return renderMessage(message, copiedMessageId, onCopyMessage, expandedStepId, onToggleStep, fallbackStepCards);
  },
  (prev, next) => {
    const prevIsCopied = prev.copiedMessageId === prev.message.id;
    const nextIsCopied = next.copiedMessageId === next.message.id;
    return (
      prev.message === next.message &&
      prev.expandedStepId === next.expandedStepId &&
      prev.fallbackStepCards === next.fallbackStepCards &&
      prevIsCopied === nextIsCopied
    );
  },
);

export function ChatTab({
  chat,
  project,
  agents,
  messages,
  optimisticMessages,
  cards,
  loading,
  sending,
  refreshing,
  creatingProjectFromChat,
  connectionState,
  events,
  onSend,
  onOpenWorkspace,
  onOpenSidebar,
  onOpenActivity,
  activityDrawerOpen,
  onCloseActivity,
  onOpenSettings,
  onRefresh,
  onApproveGate,
  onRejectGate,
  onCreateProjectFromChat,
}: ChatTabProps) {
  const [draft, setDraft] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<number | null>(null);
  const [showProjectCreateConfirm, setShowProjectCreateConfirm] = useState(false);
  const [gateActionPipelineId, setGateActionPipelineId] = useState<number | null>(null);
  const [localOverlayMessages, setLocalOverlayMessages] = useState<MessageItem[]>([]);
  const [expandedMessageSteps, setExpandedMessageSteps] = useState<Record<number, string | null>>({});
  const [expandedProgressCards, setExpandedProgressCards] = useState<Record<string, string | null>>({});
  const [showMentionPicker, setShowMentionPicker] = useState(false);
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const isComposingRef = useRef(false);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const currentScopeRef = useRef<string>(overlayScopeKey(chat?.id ?? null));
  const shouldStickThreadToBottomRef = useRef(true);
  const lastAutoScrolledChatIdRef = useRef<number | null>(chat?.id ?? null);
  const activeAgents = useMemo(() => agents.filter((agent) => agent.is_active), [agents]);
  const mentionableAgents = useMemo(
    () => [...agents].sort((left, right) => left.name.localeCompare(right.name)),
    [agents],
  );
  const defaultProjectName = chat?.title?.trim() || (chat ? `Project ${chat.id}` : "");
  const defaultAgentNames = useMemo(() => {
    if (activeAgents.some((agent) => agent.name === "assistant")) {
      return ["assistant"];
    }
    if (activeAgents.length > 0) {
      return [activeAgents[0].name];
    }
    return ["assistant"];
  }, [activeAgents]);
  const [projectNameDraft, setProjectNameDraft] = useState(defaultProjectName);
  const [projectDescriptionDraft, setProjectDescriptionDraft] = useState("");
  const [projectAgentNames, setProjectAgentNames] = useState<string[]>(defaultAgentNames);
  const fallbackStepCardsByMessageId = useMemo(() => {
    const pendingByActor = new Map<string, ThreadCard[]>();
    const mapped = new Map<number, ThreadCard[]>();

    const timeline = [
      ...cards.map((card) => ({
        sortKey: card.created_at,
        kind: "card" as const,
        card,
      })),
      ...messages.map((message) => ({
        sortKey: message.created_at,
        kind: "message" as const,
        message,
      })),
    ].sort((left, right) => new Date(left.sortKey).getTime() - new Date(right.sortKey).getTime());

    for (const item of timeline) {
      if (item.kind === "card") {
        const actor = cardActorName(item.card);
        pendingByActor.set(actor, [...(pendingByActor.get(actor) ?? []), item.card]);
        continue;
      }

      if (!item.message.agent_name || (item.message.streamSteps?.length ?? 0) > 0) {
        continue;
      }

      const actor = item.message.agent_name;
      const pending = pendingByActor.get(actor) ?? [];
      if (pending.length === 0) continue;
      mapped.set(item.message.id, pending);
      pendingByActor.delete(actor);
    }

    if (mapped.size === 0 && cards.length > 0) {
      const groupedCardsByActor = new Map<string, ThreadCard[][]>();
      let currentActor: string | null = null;
      let currentGroup: ThreadCard[] = [];

      const flushGroup = () => {
        if (!currentActor || currentGroup.length === 0) return;
        groupedCardsByActor.set(currentActor, [...(groupedCardsByActor.get(currentActor) ?? []), currentGroup]);
        currentGroup = [];
      };

      for (const card of cards) {
        const actor = cardActorName(card);
        if (actor !== currentActor) {
          flushGroup();
          currentActor = actor;
        }
        currentGroup = [...currentGroup, card];
      }
      flushGroup();

      for (const message of [...messages].sort(
        (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
      )) {
        if (!message.agent_name || (message.streamSteps?.length ?? 0) > 0) continue;
        const actorGroups = groupedCardsByActor.get(message.agent_name) ?? [];
        if (actorGroups.length === 0) continue;
        const [nextGroup, ...rest] = actorGroups;
        mapped.set(message.id, nextGroup);
        groupedCardsByActor.set(message.agent_name, rest);
      }
    }

    return mapped;
  }, [cards, messages]);
  const consumedFallbackCardIds = useMemo(
    () => new Set(Array.from(fallbackStepCardsByMessageId.values()).flatMap((group) => group.map((card) => card.id))),
    [fallbackStepCardsByMessageId],
  );

  const threadItems = useMemo<ThreadItem[]>(() => {
    const orderedItems: ThreadItem[] = [
      ...messages.map((message) => ({
        id: `message-${message.id}`,
        sortKey: message.created_at,
        kind: "message" as const,
        message,
      })),
      ...cards
        .filter((card) => !consumedFallbackCardIds.has(card.id))
        .map((card) => ({
        id: `card-${card.id}`,
        sortKey: card.created_at,
        kind: "card" as const,
        card,
      })),
    ];

    orderedItems.sort((left, right) => {
      const timeDiff = new Date(left.sortKey).getTime() - new Date(right.sortKey).getTime();
      if (timeDiff !== 0) return timeDiff;
      return threadItemSortWeight(left) - threadItemSortWeight(right);
    });

    const mergedItems: ThreadItem[] = [];
    let streak: ChatCardItem[] = [];

    const flushStreak = () => {
      if (streak.length === 0) return;
      if (streak.length === 1) {
        mergedItems.push(
          ...streak.map((card) => ({
            id: `card-${card.id}`,
            sortKey: card.created_at,
            kind: "card" as const,
            card,
          })),
        );
      } else {
        mergedItems.push({
          id: `card-merge-${streak[0].id}`,
          sortKey: streak[0].created_at,
          kind: "card",
          card: {
            id: `tool-merge-${streak[0].id}`,
            kind: "tool_merge",
            created_at: streak[0].created_at,
            source: streak[0].source,
            agent: streak[0].agent,
            tool: streak[0].tool,
            count: streak.length,
            items: streak,
          },
        });
      }
      streak = [];
    };

    for (const item of orderedItems) {
      if (
        item.kind === "card" &&
        item.card.kind === "tool_call" &&
        streak.length > 0 &&
        streak[streak.length - 1].kind === "tool_call" &&
        streak[streak.length - 1].tool === item.card.tool &&
        streak[streak.length - 1].agent === item.card.agent
      ) {
        streak.push(item.card);
        continue;
      }

      if (item.kind === "card" && item.card.kind === "tool_call") {
        flushStreak();
        streak.push(item.card);
        continue;
      }

      flushStreak();
      mergedItems.push(item);
    }

    flushStreak();
    const batchedItems: ThreadItem[] = [];
    let activityBatch: ThreadCard[] = [];

    const flushActivityBatch = () => {
      if (activityBatch.length === 0) return;
      batchedItems.push({
        id: `activity-${activityBatch[0].id}`,
        sortKey: activityBatch[0].created_at,
        kind: "activity_batch",
        cards: activityBatch,
      });
      activityBatch = [];
    };

    for (const item of mergedItems) {
      if (item.kind === "card") {
        activityBatch.push(item.card);
        continue;
      }

      flushActivityBatch();
      batchedItems.push(item);
    }

    flushActivityBatch();
    return batchedItems;
  }, [cards, consumedFallbackCardIds, messages]);

  const currentActivityAgentName = useMemo(() => {
    const streamingAgent =
      [...messages]
        .reverse()
        .find((message) => message.isStreaming && Boolean(message.agent_name))?.agent_name ?? null;
    if (streamingAgent) return streamingAgent;

    return (
      [...cards]
        .reverse()
        .map((card) => cardActorName(card))
        .find((name) => name !== "system") ?? null
    );
  }, [cards, messages]);

  const latestActivityBatchId = useMemo(
    () =>
      [...threadItems]
        .reverse()
        .find((item) => item.kind === "activity_batch")?.id ?? null,
    [threadItems],
  );

  const trailingMentionMatch = useMemo(
    () => draft.match(/(?:^|\s)@([a-zA-Z0-9_-]*)$/),
    [draft],
  );
  const mentionQuery = trailingMentionMatch?.[1]?.toLowerCase() ?? "";
  const mentionOptions = useMemo(() => {
    if (!showMentionPicker) return [];
    if (mentionQuery === "") return mentionableAgents;
    return mentionableAgents.filter((agent) => {
      const name = agent.name.toLowerCase();
      const role = agent.role.toLowerCase();
      return name.includes(mentionQuery) || role.includes(mentionQuery);
    });
  }, [mentionQuery, mentionableAgents, showMentionPicker]);

  useEffect(() => {
    setProjectNameDraft(defaultProjectName);
    setProjectDescriptionDraft("");
    setProjectAgentNames(defaultAgentNames);
    setShowProjectCreateConfirm(false);
  }, [chat?.id, defaultProjectName, defaultAgentNames]);

  useEffect(() => {
    setExpandedMessageSteps({});
    setExpandedProgressCards({});
  }, [chat?.id]);

  useEffect(() => {
    const chatId = chat?.id ?? null;
    currentScopeRef.current = overlayScopeKey(chatId);

    if (chatId !== null) {
      const migrated = migrateOverlayMessages(null, chatId);
      if (migrated.length > 0) {
        setLocalOverlayMessages(migrated);
        return;
      }
    }

    setLocalOverlayMessages(readOverlayMessages(chatId));
  }, [chat?.id]);

  useEffect(() => {
    setLocalOverlayMessages((current) => {
      let changed = false;
      const next = current.flatMap((item) => {
        if (item.optimisticKind === "user") {
          const hasServerCopy = messages.some(
            (message) =>
              !message.agent_name &&
              message.message_type === item.message_type &&
              message.content === item.content,
          );
          if (hasServerCopy) {
            changed = true;
            return [];
          }
          return [item];
        }

        if (item.optimisticKind === "assistant_placeholder") {
          const matchedServerReply =
            [...messages]
              .filter(
                (message) =>
                  Boolean(message.agent_name) &&
                  new Date(message.created_at).getTime() >= new Date(item.created_at).getTime() - 1000,
              )
              .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())[0] ??
            null;
          const hasServerReply = Boolean(matchedServerReply);
          if (!hasServerReply) {
            return [item];
          }

          const nextItem: MessageItem = {
            ...item,
            agent_name: matchedServerReply?.agent_name || item.agent_name,
            content: matchedServerReply?.content || item.content || "Execution trace",
            isStreaming: false,
            streamSteps: item.isStreaming
              ? [
                  ...(item.streamSteps ?? []),
                  {
                    id: `${Date.now()}-done`,
                    label: "Completed",
                    detail: "Server reply received.",
                    state: "done",
                  },
                ]
              : item.streamSteps,
          };

          if (
            nextItem.agent_name !== item.agent_name ||
            nextItem.content !== item.content ||
            nextItem.isStreaming !== item.isStreaming ||
            JSON.stringify(nextItem.streamSteps ?? []) !== JSON.stringify(item.streamSteps ?? [])
          ) {
            changed = true;
            return [nextItem];
          }

          return [item];
        }

        return [item];
      });

      if (!changed) return current;
      writeOverlayMessages(chat?.id ?? null, next);
      return next;
    });
  }, [chat?.id, messages]);

  useEffect(() => {
    if (optimisticMessages.length === 0) return;

    setLocalOverlayMessages((current) => {
      let changed = false;
      const usedMatches = new Set<number>();

      const next = current.map((item) => {
        if (item.optimisticKind === "user") {
          const matchedIndex = optimisticMessages.findIndex(
            (candidate, index) =>
              !usedMatches.has(index) &&
              !candidate.agent_name &&
              candidate.message_type === item.message_type &&
              candidate.content === item.content &&
              Math.abs(new Date(candidate.created_at).getTime() - new Date(item.created_at).getTime()) < 30_000,
          );
          if (matchedIndex === -1) {
            return item;
          }
          usedMatches.add(matchedIndex);
          return item;
        }

        if (item.optimisticKind === "assistant_placeholder") {
          const matchedIndex = optimisticMessages.findIndex(
            (candidate, index) =>
              !usedMatches.has(index) &&
              Boolean(candidate.agent_name) &&
              Math.abs(new Date(candidate.created_at).getTime() - new Date(item.created_at).getTime()) < 30_000,
          );
          if (matchedIndex === -1) {
            return item;
          }

          usedMatches.add(matchedIndex);
          const matched = optimisticMessages[matchedIndex];
          const nextItem: MessageItem = {
            ...item,
            agent_name: matched.agent_name || item.agent_name,
            content: matched.content || item.content,
            isStreaming: matched.isStreaming ?? item.isStreaming,
            streamSteps: matched.streamSteps ?? item.streamSteps,
          };

          if (
            nextItem.agent_name !== item.agent_name ||
            nextItem.content !== item.content ||
            nextItem.isStreaming !== item.isStreaming ||
            JSON.stringify(nextItem.streamSteps ?? []) !== JSON.stringify(item.streamSteps ?? [])
          ) {
            changed = true;
            return nextItem;
          }
        }

        return item;
      });

      if (!changed) return current;
      writeOverlayMessages(chat?.id ?? null, next);
      return next;
    });
  }, [chat?.id, optimisticMessages]);

  useEffect(() => {
    if (copiedMessageId === null) return;
    const timer = window.setTimeout(() => setCopiedMessageId(null), 1200);
    return () => window.clearTimeout(timer);
  }, [copiedMessageId]);

  useEffect(() => {
    if (trailingMentionMatch) {
      setShowMentionPicker(true);
      return;
    }
    setShowMentionPicker(false);
    setSelectedMentionIndex(0);
  }, [trailingMentionMatch]);

  useEffect(() => {
    if (!showMentionPicker) {
      setSelectedMentionIndex(0);
      return;
    }
    setSelectedMentionIndex((current) => Math.min(current, Math.max(mentionOptions.length - 1, 0)));
  }, [mentionOptions.length, showMentionPicker]);

  useEffect(() => {
    if (!showMentionPicker) return;

    function handlePointerDown(event: MouseEvent) {
      if (!composerRef.current?.contains(event.target as Node)) {
        setShowMentionPicker(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [showMentionPicker]);

  function updateThreadStickiness() {
    const thread = threadRef.current;
    if (!thread) return;
    const distanceToBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    shouldStickThreadToBottomRef.current = distanceToBottom <= THREAD_AUTO_SCROLL_THRESHOLD;
  }

  useLayoutEffect(() => {
    const activeChatId = chat?.id ?? null;
    const shouldForceScroll = lastAutoScrolledChatIdRef.current !== activeChatId;
    if (!shouldForceScroll && !shouldStickThreadToBottomRef.current) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      threadEndRef.current?.scrollIntoView({ block: "end" });
      shouldStickThreadToBottomRef.current = true;
      lastAutoScrolledChatIdRef.current = activeChatId;
    });

    return () => window.cancelAnimationFrame(frame);
  }, [chat?.id, loading, threadItems, localOverlayMessages]);

  function submitDraft() {
    const next = draft.trim();
    if (!next || sending) return;
    shouldStickThreadToBottomRef.current = true;
    setShowMentionPicker(false);
    setDraft("");
    const now = new Date();
    const baseId = -Math.floor(now.getTime());
    const userLocalMessage: MessageItem = {
      id: baseId,
      content: next,
      message_type: "user",
      created_at: now.toISOString(),
      agent_name: null,
      optimisticKind: "user",
      localOnly: true,
    };
    const assistantLocalMessage: MessageItem = {
      id: baseId - 1,
      content: "",
      message_type: "text",
      created_at: new Date(now.getTime() + 1).toISOString(),
      agent_name: "assistant",
      isStreaming: true,
      streamSteps: [
        {
          id: `${now.getTime()}-queued`,
          label: "Queued",
          detail: "Waiting for server response.",
          state: "live",
        },
      ],
      optimisticKind: "assistant_placeholder",
      localOnly: true,
    };
    flushSync(() => {
      setLocalOverlayMessages((current) => {
        const nextMessages = [...current, userLocalMessage, assistantLocalMessage].sort(
          (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
        );
        writeOverlayMessages(chat?.id ?? null, nextMessages);
        return nextMessages;
      });
    });

    window.requestAnimationFrame(() => {
      void onSend(next).catch((error) => {
        const message = error instanceof Error ? error.message : "Send failed";
        setLocalOverlayMessages((current) => {
          const nextMessages = current.map((item) =>
            item.id === assistantLocalMessage.id
              ? {
                  ...item,
                  isStreaming: false,
                  content: item.content || `Error: ${message}`,
                  streamSteps: [
                    {
                      id: `${Date.now()}-failed`,
                      label: "Failed",
                      detail: message,
                      state: "error",
                    },
                  ],
                }
              : item,
          );
          writeOverlayMessages(chat?.id ?? null, nextMessages);
          return nextMessages;
        });
      });
    });
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submitDraft();
  }

  const toggleProgressCard = useCallback((groupKey: string, cardId: string) => {
    setExpandedProgressCards((current) => ({
      ...current,
      [groupKey]: current[groupKey] === cardId ? null : cardId,
    }));
  }, []);

  const toggleMessageStep = useCallback((messageId: number, stepId: string) => {
    setExpandedMessageSteps((current) => ({
      ...current,
      [messageId]: current[messageId] === stepId ? null : stepId,
    }));
  }, []);

  function insertMention(agentName: string) {
    setDraft((current) => {
      if (/(?:^|\s)@([a-zA-Z0-9_-]*)$/.test(current)) {
        return current.replace(/(^|\s)@([a-zA-Z0-9_-]*)$/, `$1@${agentName} `);
      }

      const separator = current.length === 0 || /\s$/.test(current) ? "" : " ";
      return `${current}${separator}@${agentName} `;
    });
    setShowMentionPicker(false);
    setSelectedMentionIndex(0);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  }

  function handleMentionTriggerClick() {
    if (sending) return;
    setDraft((current) => {
      if (/(?:^|\s)@([a-zA-Z0-9_-]*)$/.test(current)) return current;
      const separator = current.length === 0 || /\s$/.test(current) ? "" : " ";
      return `${current}${separator}@`;
    });
    setShowMentionPicker(true);
    setSelectedMentionIndex(0);
    window.requestAnimationFrame(() => composerInputRef.current?.focus());
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || isComposingRef.current) return;

    const keyCode = "keyCode" in event ? event.keyCode : event.which;
    const isEnterKey = event.key === "Enter" || event.code === "Enter" || keyCode === 13 || event.which === 13;
    const isArrowDownKey = event.key === "ArrowDown" || event.code === "ArrowDown";
    const isArrowUpKey = event.key === "ArrowUp" || event.code === "ArrowUp";
    const isTabKey = event.key === "Tab" || event.code === "Tab";
    const isEscapeKey = event.key === "Escape" || event.code === "Escape";

    if (showMentionPicker && mentionOptions.length > 0) {
      if (isArrowDownKey) {
        event.preventDefault();
        setSelectedMentionIndex((current) => (current + 1) % mentionOptions.length);
        return;
      }

      if (isArrowUpKey) {
        event.preventDefault();
        setSelectedMentionIndex((current) => (current - 1 + mentionOptions.length) % mentionOptions.length);
        return;
      }

      if ((isEnterKey && !event.shiftKey) || isTabKey) {
        event.preventDefault();
        insertMention(mentionOptions[selectedMentionIndex]?.name ?? mentionOptions[0].name);
        return;
      }
    }

    if (isEscapeKey && showMentionPicker) {
      event.preventDefault();
      setShowMentionPicker(false);
      return;
    }

    if (!isEnterKey || event.shiftKey) return;
    event.preventDefault();
    void submitDraft();
  }

  const handleCopyMessage = useCallback(async (message: MessageItem) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedMessageId(message.id);
    } catch {
      setCopiedMessageId(null);
    }
  }, []);

  async function handleConfirmCreateProject() {
    const nextProjectName = projectNameDraft.trim() || defaultProjectName;
    if (!nextProjectName || projectAgentNames.length === 0) return;
    setShowProjectCreateConfirm(false);
    await onCreateProjectFromChat({
      name: nextProjectName,
      description: projectDescriptionDraft.trim(),
      agent_names: projectAgentNames,
    });
  }

  const handleApproveGate = useCallback(async (pipelineId: number) => {
    try {
      setGateActionPipelineId(pipelineId);
      await onApproveGate(pipelineId);
    } finally {
      setGateActionPipelineId(null);
    }
  }, [onApproveGate]);

  const handleRejectGate = useCallback(async (pipelineId: number) => {
    try {
      setGateActionPipelineId(pipelineId);
      await onRejectGate(pipelineId);
    } finally {
      setGateActionPipelineId(null);
    }
  }, [onRejectGate]);

  function toggleProjectAgent(agentName: string) {
    setProjectAgentNames((current) =>
      current.includes(agentName)
        ? current.filter((value) => value !== agentName)
        : [...current, agentName],
    );
  }

  const connectionCopy =
    connectionState === "connected"
      ? "Realtime connected"
      : connectionState === "connecting"
        ? "Connecting realtime"
        : "Realtime offline";
  const workspaceCopy = project?.workspace_path?.replace(/\\/g, "/") ?? "";
  const workspaceLabel =
    workspaceCopy.length > 54 ? `...${workspaceCopy.slice(-54)}` : workspaceCopy;
  const chatHeading =
    project && chat && chat.id !== project.default_chatroom_id
      ? chat.title
      : project?.name ?? chat?.title ?? "New conversation";
  const chatSubheading = project ? "" : chat ? "standalone chat" : "Your first message will create a chat";
  const threadContent = useMemo(() => {
    const resolveExpandedMessageStepId = (message: MessageItem) => {
      const explicit = expandedMessageSteps[message.id];
      if (explicit !== undefined) return explicit;
      return [...(message.streamSteps ?? [])].reverse().find((step) => step.state === "live")?.id ?? null;
    };

    if (!chat && threadItems.length === 0) {
      return (
        <div className="agent-chat__welcome">
          <div className="agent-chat__avatar--logo">CA</div>
          <h2>Start with a message</h2>
          <p className="agent-chat__hint">Say anything below. Catown will create a chat automatically and pin it under Chats.</p>
          <div className="agent-chat__badges">
            <span className="agent-chat__badge">chat first</span>
            <span className="agent-chat__badge">project later</span>
            <span className="agent-chat__badge">multi-agent ready</span>
          </div>
        </div>
      );
    }

    if (threadItems.length > 0 || localOverlayMessages.length > 0) {
      return (
        <>
          {threadItems.map((item) =>
            item.kind === "message"
              ? (
                  <MessageRow
                    key={`message-${item.message.id}`}
                    message={item.message}
                    copiedMessageId={copiedMessageId}
                    onCopyMessage={handleCopyMessage}
                    expandedStepId={resolveExpandedMessageStepId(item.message)}
                    onToggleStep={toggleMessageStep}
                    fallbackStepCards={fallbackStepCardsByMessageId.get(item.message.id) ?? EMPTY_THREAD_CARDS}
                  />
                )
              : item.kind === "activity_batch"
                ? renderActivityBatch(
                    item.id,
                    item.cards,
                    item.id === latestActivityBatchId,
                    item.id === latestActivityBatchId ? currentActivityAgentName : null,
                    expandedProgressCards,
                    toggleProgressCard,
                    gateActionPipelineId,
                    handleApproveGate,
                    handleRejectGate,
                  )
                : renderCard(item.card, gateActionPipelineId, handleApproveGate, handleRejectGate),
          )}
          {localOverlayMessages.map((message) => (
            <MessageRow
              key={`message-${message.id}`}
              message={message}
              copiedMessageId={copiedMessageId}
              onCopyMessage={handleCopyMessage}
              expandedStepId={resolveExpandedMessageStepId(message)}
              onToggleStep={toggleMessageStep}
              fallbackStepCards={EMPTY_THREAD_CARDS}
            />
          ))}
        </>
      );
    }

    if (loading) {
      return (
        <div className="agent-chat__welcome">
          <h2>Loading conversation</h2>
          <p className="agent-chat__hint">Fetching the latest thread.</p>
        </div>
      );
    }

    return (
      <div className="agent-chat__welcome">
        <div className="agent-chat__avatar--logo">CA</div>
        <h2>No messages yet</h2>
        <p className="agent-chat__hint">
          {project
            ? "Send the first instruction to start this project session."
            : "Use this standalone chat to explore before turning it into a project."}
        </p>
      </div>
    );
  }, [
    chat,
    copiedMessageId,
    currentActivityAgentName,
    expandedMessageSteps,
    expandedProgressCards,
    fallbackStepCardsByMessageId,
    gateActionPipelineId,
    handleApproveGate,
    handleCopyMessage,
    handleRejectGate,
    latestActivityBatchId,
    loading,
    localOverlayMessages,
    project,
    threadItems,
    toggleMessageStep,
    toggleProgressCard,
  ]);

  return (
    <section className="chat-shell chat">
      <header className="chat-header">
        <div className="chat-header__left">
          <div className="chat-session">
            <h2>{chatHeading}</h2>
            {project && workspaceLabel ? (
              <button
                type="button"
                className="chat-session__workspace"
                onClick={() => void onOpenWorkspace()}
                title={workspaceCopy}
              >
                <span className="chat-session__workspace-icon" aria-hidden="true">
                  <svg viewBox="0 0 16 16" fill="none">
                    <path
                      d="M1.75 4.25A1.5 1.5 0 0 1 3.25 2.75H6.1c.34 0 .66.14.9.38l.92.92c.23.24.56.37.9.37h3.93a1.5 1.5 0 0 1 1.5 1.5v5.88a1.5 1.5 0 0 1-1.5 1.5H3.25a1.5 1.5 0 0 1-1.5-1.5V4.25Z"
                      stroke="currentColor"
                      strokeWidth="1.2"
                      strokeLinejoin="round"
                    />
                    <path
                      d="M1.75 5.5h12.5"
                      stroke="currentColor"
                      strokeWidth="1.2"
                      strokeLinecap="round"
                    />
                  </svg>
                </span>
                <span>{workspaceLabel}</span>
              </button>
            ) : chatSubheading ? (
              <span>{chatSubheading}</span>
            ) : null}
          </div>
        </div>

        <div className="chat-header__right">
          <span className={`chat-live-pill is-${connectionState}`}>
            <span className="status-dot chat-live-pill__dot" />
            <span>{connectionCopy}</span>
          </span>
          <button
            type="button"
            className="btn btn--sm btn--icon mobile-sidebar-toggle"
            onClick={onOpenSidebar}
            aria-label="Open chats and projects"
            title="Open sidebar"
          >
            ☰
          </button>
          <button
            type="button"
            className="btn btn--sm btn--icon mobile-sidebar-toggle"
            onClick={onOpenActivity}
            aria-label="Open activity"
            title="Open activity"
          >
            ≣
          </button>
          <button
            type="button"
            className="btn btn--sm btn--icon settings-icon-btn"
            onClick={onOpenSettings}
            aria-label="Open settings"
            title="Settings"
          >
            <span className="settings-icon-glyph" aria-hidden="true">
              ⚙
            </span>
          </button>
        </div>
      </header>

      {activeAgents.length > 0 ? (
        <div className="agent-strip">
          {activeAgents.map((agent) => (
            <div key={agent.id} className="agent-strip__chip">
              <span className="agent-dot is-active" />
              <div>
                <strong>{agent.name}</strong>
                <small>{agent.role}</small>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="chat-split-container">
        <div className="chat-main">
          <div
            ref={threadRef}
            className="chat-thread"
            onScroll={() => {
              updateThreadStickiness();
            }}
          >
            <div className="chat-thread-inner">
              {!project && chat && showProjectCreateConfirm ? (
                <div className="chat-inline-decision">
                  <div className="chat-inline-decision__copy">
                    <strong>Create project from this chat?</strong>
                    <p>
                      This keeps the current standalone chat in the sidebar, creates a hidden main project chat, and copies
                      the recent conversation into it.
                    </p>
                  </div>
                  <div className="project-form chat-inline-decision__form">
                    <label>
                      <span>Name</span>
                      <input
                        value={projectNameDraft}
                        onChange={(event) => setProjectNameDraft(event.target.value)}
                        placeholder={defaultProjectName}
                      />
                    </label>
                    <label>
                      <span>Description</span>
                      <textarea
                        value={projectDescriptionDraft}
                        onChange={(event) => setProjectDescriptionDraft(event.target.value)}
                        placeholder="Optional project brief"
                        rows={3}
                      />
                    </label>
                    <div>
                      <span className="field-label">Agents</span>
                      {activeAgents.length === 0 ? (
                        <p className="chat-inline-decision__hint">No active agents are available yet.</p>
                      ) : (
                        <div className="agent-selector-grid">
                          {activeAgents.map((agent) => (
                            <button
                              key={agent.id}
                              type="button"
                              className={`agent-select-chip ${projectAgentNames.includes(agent.name) ? "is-selected" : ""}`}
                              onClick={() => toggleProjectAgent(agent.name)}
                            >
                              <strong>{agent.name}</strong>
                              <span>{agent.role}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="chat-inline-decision__actions">
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => void handleConfirmCreateProject()}
                      disabled={creatingProjectFromChat || projectAgentNames.length === 0 || projectNameDraft.trim() === ""}
                    >
                      {creatingProjectFromChat ? "Creating..." : "Confirm"}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => setShowProjectCreateConfirm(false)}
                      disabled={creatingProjectFromChat}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}

              {threadContent}
              <div ref={threadEndRef} />
            </div>
          </div>

          <form className="chat-compose" onSubmit={handleSubmit}>
            <div ref={composerRef} className="agent-chat__input">
              {showMentionPicker ? (
                <div className="agent-chat__mention-menu" role="listbox" aria-label="Select an agent to mention">
                  <div className="agent-chat__mention-header">
                    <div>
                      <div className="agent-chat__mention-kicker">Mention agent</div>
                      <div className="agent-chat__mention-title">
                        {mentionQuery ? `Results for @${mentionQuery}` : "Choose a teammate"}
                      </div>
                    </div>
                    <div className="agent-chat__mention-shortcut">↑↓ move · Enter insert</div>
                  </div>
                  {mentionOptions.length > 0 ? (
                    mentionOptions.map((agent, index) => (
                      <button
                        key={agent.id}
                        type="button"
                        className={`agent-chat__mention-item ${index === selectedMentionIndex ? "is-selected" : ""}`}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => insertMention(agent.name)}
                        role="option"
                        aria-selected={index === selectedMentionIndex}
                      >
                        <span className="agent-chat__mention-avatar">{initials(agent.name)}</span>
                        <span className="agent-chat__mention-copy">
                          <span className="agent-chat__mention-name">@{agent.name}</span>
                          <span className="agent-chat__mention-role">{agent.role}</span>
                        </span>
                        <span className={`agent-chat__mention-state ${agent.is_active ? "is-active" : ""}`}>
                          {agent.is_active ? "online" : "idle"}
                        </span>
                      </button>
                    ))
                  ) : (
                    <div className="agent-chat__mention-empty">No matching agents.</div>
                  )}
                </div>
              ) : null}
              <textarea
                ref={composerInputRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDownCapture={handleComposerKeyDown}
                onCompositionStart={() => {
                  isComposingRef.current = true;
                }}
                onCompositionEnd={() => {
                  isComposingRef.current = false;
                }}
                placeholder={chat ? "Send a message..." : "Start a conversation..."}
                rows={1}
                disabled={sending}
              />
              <div className="agent-chat__toolbar">
                <div className="agent-chat__toolbar-left">
                  <button
                    type="button"
                    className="agent-chat__input-btn"
                    disabled={sending}
                    onClick={handleMentionTriggerClick}
                    title="Mention an agent"
                  >
                    @
                  </button>
                  <button
                    type="button"
                    className="agent-chat__input-btn"
                    disabled={sending}
                    onClick={() => {
                      setDraft("");
                      setShowMentionPicker(false);
                    }}
                    title="Clear draft"
                  >
                    ×
                  </button>
                </div>

                <div className="agent-chat__toolbar-right">
                  <span className="compose-status">{sending ? "assistant thinking..." : connectionCopy}</span>
                  <button
                    type="submit"
                    className="chat-send-btn"
                    disabled={sending || draft.trim() === ""}
                    aria-label="Send message"
                  >
                    {sending ? "..." : "→"}
                  </button>
                </div>
              </div>
            </div>
          </form>
        </div>

        {activityDrawerOpen ? <button type="button" className="mobile-drawer-backdrop" onClick={onCloseActivity} aria-label="Close activity panel" /> : null}
        <aside className={`chat-sidebar ${activityDrawerOpen ? "is-mobile-open" : ""}`}>
          <div className="sidebar-panel">
            <div className="sidebar-header">
              <span className="sidebar-title">Activity</span>
              <div className="sidebar-header__actions">
                <span className="soft-pill">{events.length}</span>
                <button
                  type="button"
                  className="chat-sidebar__close"
                  onClick={onCloseActivity}
                  aria-label="Close activity"
                  title="Close activity"
                >
                  ×
                </button>
              </div>
            </div>
            <div className="sidebar-content sidebar-markdown">
              <div className="activity-section">
                <h3>Session</h3>
                <ul>
                  <li>{chat ? `Chat: ${chat.title}` : "No chat selected"}</li>
                  <li>{project ? `Project: ${project.name}` : "Standalone mode"}</li>
                  <li>{messages.length} messages loaded</li>
                  <li>{cards.length} runtime cards</li>
                  <li>{activeAgents.length} active agents</li>
                </ul>
              </div>

              <div className="activity-section">
                <h3>Recent activity</h3>
                {events.length === 0 ? (
                  <div className="empty-card">No activity yet.</div>
                ) : (
                  <div className="activity-list">
                    {[...events].reverse().map((event) => (
                      <div key={event.id} className={`activity-entry activity-entry--${event.tone}`}>
                        <div className="activity-entry__meta">
                          <span>{toneLabel(event.tone)}</span>
                          <span>{formatTime(event.created_at)}</span>
                        </div>
                        <div className="activity-entry__message">{event.message}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}
