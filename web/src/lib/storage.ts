import type { Conversation, Settings } from "./types";
export const defaultSettings: Settings = {
  mode: "dsh",
  endpoint: "/agent-api/chat/stream",
  model: "",
  cwd: "D:\\agent",
};
export function loadSettings(): Settings {
  try {
    const value = JSON.parse(
      localStorage.getItem("agent.settings.v1") || "null",
    );
    if (
      value &&
      ["dsh", "sse", "demo"].includes(value.mode) &&
      ["endpoint", "model", "cwd"].every(
        (key) => typeof value[key] === "string",
      )
    )
      return {
        mode: value.mode,
        endpoint: value.endpoint,
        model: value.model,
        cwd: value.cwd,
      };
  } catch {
    /* A fresh workspace is usable with blocked or corrupt storage. */
  }
  return defaultSettings;
}
export function loadConversations(): Conversation[] {
  try {
    const data = JSON.parse(
      localStorage.getItem("agent.conversations.v1") || "[]",
    );
    if (!Array.isArray(data)) return [];
    return data
      .filter(
        (c) =>
          c &&
          typeof c.id === "string" &&
          typeof c.title === "string" &&
          Array.isArray(c.messages) &&
          c.messages.every(
            (m: Record<string, unknown>) =>
              m &&
              typeof m.id === "string" &&
              ["user", "assistant"].includes(String(m.role)) &&
              typeof m.content === "string",
          ),
      )
      .map((c) => ({
        ...c,
        messages: c.messages.map((m: Record<string, unknown>) =>
          m.status === "streaming"
            ? {
                ...m,
                status: "error",
                error: "页面刷新中断了连接，后台任务状态尚未确认。",
              }
            : m,
        ),
      }));
  } catch {
    return [];
  }
}
export function saveLocal(key: string, value: unknown): boolean {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
