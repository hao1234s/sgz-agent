import type { Emit, Message, Settings } from "./types";

export async function readSSE(
  body: ReadableStream<Uint8Array>,
  emit: Emit,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "",
    data: string[] = [],
    eventName = "",
    completed = false;
  const abort = () => {
    void reader.cancel().catch(() => {});
  };
  signal?.addEventListener("abort", abort, { once: true });
  function dispatch() {
    if (!data.length) {
      eventName = "";
      return;
    }
    const raw = data.join("\n");
    data = [];
    const name = eventName;
    eventName = "";
    if (raw === "[DONE]") {
      completed = true;
      emit({ type: "done" });
      return;
    }
    let value;
    try {
      value = JSON.parse(raw);
    } catch {
      throw new Error("后端返回的 SSE 数据不是有效 JSON。");
    }
    if (!value || typeof value !== "object")
      throw new Error("后端返回了无效的 SSE 事件。");
    if (name === "error" || value.type === "error" || value.error) {
      throw new Error(
        value.error?.message ||
          value.message ||
          (typeof value.error === "string" ? value.error : "后端生成失败。"),
      );
    }
    const text =
      value.choices?.[0]?.delta?.content ??
      (value.type === "delta" || name === "delta"
        ? (value.text ?? value.content)
        : undefined);
    if (typeof text === "string") emit({ type: "delta", text });
    if (value.type === "status" && typeof value.text === "string")
      emit({ type: "status", text: value.text });
    if (
      value.type === "done" ||
      name === "done" ||
      value.choices?.[0]?.finish_reason
    ) {
      completed = true;
      emit({ type: "done" });
    }
  }
  function line(value: string) {
    if (value === "") {
      dispatch();
      return;
    }
    if (value.startsWith(":")) return;
    const colon = value.indexOf(":");
    const field = colon < 0 ? value : value.slice(0, colon);
    const content = colon < 0 ? "" : value.slice(colon + 1).replace(/^ /, "");
    if (field === "data") data.push(content);
    if (field === "event") eventName = content;
  }
  try {
    signal?.throwIfAborted();
    while (!completed) {
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      let match: RegExpExecArray | null;
      while ((match = /\r\n|\r|\n/.exec(buffer))) {
        if (match[0] === "\r" && match.index === buffer.length - 1 && !done)
          break;
        const next = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        line(next);
        if (completed) break;
      }
      if (done) break;
    }
    if (!completed)
      throw new Error("连接已中断，未收到完成标记。已保留收到的内容。");
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function streamSSE(
  settings: Settings,
  messages: Message[],
  sessionId: string,
  token: string,
  signal: AbortSignal,
  emit: Emit,
) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(settings.endpoint, {
    method: "POST",
    headers,
    signal,
    body: JSON.stringify({
      messages: messages
        .filter(
          (m) => m.role === "user" || (m.content && m.status === "complete"),
        )
        .map(({ role, content }) => ({ role, content })),
      stream: true,
      session_id: sessionId,
      ...(settings.model ? { model: settings.model } : {}),
    }),
  });
  if (!response.ok)
    throw new Error(
      `后端请求失败（HTTP ${response.status}）。${response.status === 401 ? "请检查连接凭据。" : "请检查接口地址和服务状态。"}`,
    );
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  )
    throw new Error("接口没有返回 text/event-stream，请检查流式接口地址。");
  await readSSE(response.body, emit, signal);
}
