import type { Message } from "./types";

export interface User {
  id: string;
  username: string;
  role: "admin" | "user";
  disabled?: boolean;
}
export interface ConversationSummary {
  id: string;
  title: string;
  updatedAt: number;
  status: string;
}
export interface ServerConversation extends ConversationSummary {
  messages: Message[];
}
export interface ServerStatus {
  ready: boolean;
  model: string;
  execution: "headless";
  capabilities: string[];
}
export type ServerEvent =
  | { type: "run"; id: string; userMessage: Message; assistantMessage: Message }
  | { type: "delta" | "replace" | "status"; text: string }
  | { type: "done"; conversation: ServerConversation }
  | { type: "error"; message: string };
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "登录已过期，请重新登录。";
    if (error.status === 429) return "请求过于频繁，请稍后重试。";
    if (error.status === 503)
      return "模型服务尚未就绪，请联系管理员配置后重试。";
    if (error.status === 409 && /busy|running/i.test(error.message))
      return "已有任务正在运行，或服务繁忙，请稍后重试。";
  }
  return error instanceof Error
    ? error.message
    : "请求失败，请检查网络后重试。";
}
const segment = (id: string) => encodeURIComponent(id);
async function request(
  path: string,
  method = "GET",
  data?: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    signal,
    headers: {
      ...(method !== "GET"
        ? { "X-SGZ-Request": "1", "Content-Type": "application/json" }
        : {}),
      Accept: path.endsWith("/messages")
        ? "text/event-stream"
        : "application/json",
    },
    ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
  });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）。`;
    try {
      const value = await response.json();
      if (typeof value.message === "string") message = value.message;
    } catch {
      /* non-JSON gateway failure */
    }
    throw new ApiError(response.status, message);
  }
  return response;
}
async function json<T>(
  path: string,
  method = "GET",
  data?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return (await request(path, method, data, signal)).json();
}

/** Strict server-owned SSE contract; an early EOF is never a successful completion. */
export async function readEvents(
  body: ReadableStream<Uint8Array>,
  emit: (event: ServerEvent) => void,
  signal?: AbortSignal,
): Promise<ServerConversation> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lines: string[] = [];
  let result: ServerConversation | undefined;
  const abort = () => {
    void reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  function line(text: string) {
    if (text !== "") {
      if (text.startsWith("data:")) lines.push(text.slice(5).replace(/^ /, ""));
      return;
    }
    if (!lines.length) return;
    const raw = lines.join("\n");
    lines = [];
    let event: ServerEvent;
    try {
      event = JSON.parse(raw);
    } catch {
      throw new Error("服务返回了无效的事件数据。");
    }
    if (!event || typeof event !== "object")
      throw new Error("服务返回了无效的事件数据。");
    signal?.throwIfAborted();
    if (event.type === "error")
      throw new Error(event.message || "生成失败，请稍后重试。");
    if (event.type === "done") {
      if (!event.conversation || !Array.isArray(event.conversation.messages))
        throw new Error("服务未返回完整会话。");
      result = event.conversation;
    } else if (event.type === "run") {
      if (!event.userMessage?.id || !event.assistantMessage?.id)
        throw new Error("服务返回了无效的运行事件。");
    } else if (
      !["delta", "replace", "status"].includes(event.type) ||
      typeof event.text !== "string"
    )
      throw new Error("服务返回了未知事件。");
    emit(event);
  }
  try {
    signal?.throwIfAborted();
    while (!result) {
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      let match: RegExpExecArray | null;
      while ((match = /\r\n|\r|\n/.exec(buffer))) {
        if (match[0] === "\r" && match.index === buffer.length - 1 && !done)
          break;
        const text = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        line(text);
        if (result) break;
      }
      if (done) break;
    }
    if (!result)
      throw new Error("连接已中断，未收到完成标记。请刷新会话查看已保存内容。");
    return result;
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export const api = {
  me: (signal?: AbortSignal) =>
    json<{ user: User }>("/auth/me", "GET", undefined, signal),
  login: (username: string, password: string, signal?: AbortSignal) =>
    json<{ user: User }>("/auth/login", "POST", { username, password }, signal),
  logout: (signal?: AbortSignal) => request("/auth/logout", "POST", {}, signal),
  status: (signal?: AbortSignal) =>
    json<ServerStatus>("/status", "GET", undefined, signal),
  conversations: (signal?: AbortSignal) =>
    json<{ conversations: ConversationSummary[] }>(
      "/conversations",
      "GET",
      undefined,
      signal,
    ),
  conversation: (id: string, signal?: AbortSignal) =>
    json<{ conversation: ServerConversation }>(
      `/conversations/${segment(id)}`,
      "GET",
      undefined,
      signal,
    ),
  create: (signal?: AbortSignal) =>
    json<{ conversation: ServerConversation }>(
      "/conversations",
      "POST",
      {},
      signal,
    ),
  remove: (id: string, signal?: AbortSignal) =>
    request(`/conversations/${segment(id)}`, "DELETE", undefined, signal),
  cancel: (id: string, signal?: AbortSignal) =>
    json<{ cancelled: boolean }>(
      `/conversations/${segment(id)}/cancel`,
      "POST",
      {},
      signal,
    ),
  users: (signal?: AbortSignal) =>
    json<{ users: User[] }>("/admin/users", "GET", undefined, signal),
  createUser: (
    username: string,
    password: string,
    role: User["role"],
    signal?: AbortSignal,
  ) =>
    json<{ user: User }>(
      "/admin/users",
      "POST",
      { username, password, role },
      signal,
    ),
  updateUser: (
    id: string,
    patch: { disabled?: boolean; password?: string },
    signal?: AbortSignal,
  ) =>
    json<{ user: User }>(`/admin/users/${segment(id)}`, "PATCH", patch, signal),
  async send(
    id: string,
    text: string,
    requestId: string,
    signal: AbortSignal,
    emit: (event: ServerEvent) => void,
  ) {
    const response = await request(
      `/conversations/${segment(id)}/messages`,
      "POST",
      { text, requestId },
      signal,
    );
    if (
      !response.body ||
      !response.headers.get("content-type")?.includes("text/event-stream")
    )
      throw new Error("服务未返回流式响应。");
    return readEvents(response.body, emit, signal);
  },
};
