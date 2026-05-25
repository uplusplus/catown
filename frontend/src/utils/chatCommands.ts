/**
 * Chat command definitions and utilities for ADR-005 command system.
 * Commands are defined here for instant autocomplete (no API round-trip).
 * Execution goes through the /api/commands/execute endpoint.
 */

export type ChatCommandDef = {
  command: string;
  aliases: string[];
  category: string;
  description: string;
  args?: string;
};

export const CHAT_COMMANDS: ChatCommandDef[] = [
  { command: "/help", aliases: ["/h"], category: "基础", description: "显示所有可用指令及说明" },
  { command: "/skills list", aliases: ["/sl"], category: "Skills", description: "列出已安装 Skills" },
  { command: "/skills info", aliases: ["/si"], category: "Skills", description: "查看 Skill 详情", args: "<name>" },
  { command: "/tools list", aliases: ["/tl"], category: "Tools", description: "列出所有可用工具" },
  { command: "/tools info", aliases: ["/ti"], category: "Tools", description: "查看工具详情", args: "<name>" },
  { command: "/agents list", aliases: ["/al"], category: "Agent", description: "列出所有 Agent 角色及状态" },
  { command: "/agents info", aliases: ["/ai"], category: "Agent", description: "查看 Agent 详情", args: "<name>" },
  { command: "/config get", aliases: ["/cg"], category: "配置", description: "查看当前全局配置" },
  { command: "/pipeline status", aliases: ["/ps"], category: "Pipeline", description: "查看当前 Pipeline 状态" },
];

/** Build a flat lookup: alias -> canonical command name */
const ALIAS_MAP: Record<string, string> = {};
for (const cmd of CHAT_COMMANDS) {
  ALIAS_MAP[cmd.command] = cmd.command;
  for (const alias of cmd.aliases) {
    ALIAS_MAP[alias] = cmd.command;
  }
}

/** Check if input looks like a command (starts with /) */
export function isCommandInput(input: string): boolean {
  return input.trimStart().startsWith("/");
}

/** Resolve a command alias to canonical name */
export function resolveCommandAlias(input: string): string | null {
  const text = input.trim();
  const parts = text.split(/\s+/);
  if (!parts[0]) return null;
  return ALIAS_MAP[parts[0]] ?? null;
}

/** Match commands for autocomplete based on current input */
export function matchCommands(input: string): ChatCommandDef[] {
  const text = input.trim().toLowerCase();
  if (!text.startsWith("/")) return [];

  return CHAT_COMMANDS.filter((cmd) => {
    if (cmd.command.toLowerCase().startsWith(text)) return true;
    return cmd.aliases.some((alias) => alias.toLowerCase().startsWith(text));
  });
}

/** Match history entries for autocomplete (prefix match) */
export function matchHistory(input: string, history: string[]): string[] {
  const text = input.trim().toLowerCase();
  if (!text || text.startsWith("/")) return [];

  const seen = new Set<string>();
  const results: string[] = [];
  // Search from newest to oldest
  for (let i = history.length - 1; i >= 0 && results.length < 5; i--) {
    const entry = history[i];
    if (entry.toLowerCase().startsWith(text) && !seen.has(entry)) {
      seen.add(entry);
      results.unshift(entry);
    }
  }
  return results;
}
