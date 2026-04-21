import type { ChatCardLlmTimings } from "../types";

const TIMING_ROWS: Array<[keyof ChatCardLlmTimings, string]> = [
  ["request_sent_ms", "Request sent"],
  ["first_chunk_ms", "First chunk"],
  ["first_content_ms", "First content"],
  ["first_tool_call_ms", "First tool delta"],
  ["tool_call_ready_ms", "Tool call ready"],
  ["completed_ms", "Completed"],
];

export function formatTimingDuration(elapsedMs?: number) {
  if (typeof elapsedMs !== "number" || !Number.isFinite(elapsedMs) || elapsedMs < 0) return "";
  if (elapsedMs < 1000) return `${elapsedMs}ms`;
  const seconds = elapsedMs / 1000;
  return `${seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)}s`;
}

export function buildLlmTimingsMarkdown(timings?: ChatCardLlmTimings) {
  if (!timings) return "";
  const lines = TIMING_ROWS.map(([key, label]) => {
    const formatted = formatTimingDuration(timings[key]);
    return formatted ? `- ${label}: ${formatted}` : "";
  }).filter(Boolean);

  if (lines.length === 0) return "";
  return `### LLM Timings\n\n${lines.join("\n")}`;
}
