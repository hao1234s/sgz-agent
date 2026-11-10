import type { Emit } from "./types";
export interface DshText {
  committed: string;
  draft: string;
  activity: string;
  done: boolean;
}
export const emptyDshText = (): DshText => ({
  committed: "",
  draft: "",
  activity: "",
  done: false,
});

// Boundary data comes from the installed DSH protocol, not application state.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function reduceDshEvent(state: DshText, input: unknown): DshText {
  const value = input as Record<string, any>;
  const next = { ...state };
  if (value.type === "assistant-stream") {
    const frame = value.frame;
    if (frame.type === "start") {
      next.draft = "";
      next.activity = "正在思考";
    }
    if (frame.type === "chunk" && frame.chunk?.type === "text-delta") {
      next.draft += frame.chunk.text;
      next.activity = "正在回复";
    }
    if (frame.type === "chunk" && frame.chunk?.type === "tool-call-delta")
      next.activity = frame.chunk.name
        ? `正在调用 ${frame.chunk.name}`
        : "正在准备工具";
  }
  if (value.type === "event") {
    const event = value.event;
    if (event.type === "assistant/message") {
      const text = (event.data?.message?.content ?? [])
        .filter((part: { type: string }) => part.type === "text")
        .map((part: { text: string }) => part.text)
        .join("");
      next.committed = [next.committed, text].filter(Boolean).join("\n\n");
      next.draft = "";
    }
    if (event.type === "approval/asked")
      next.activity = "等待批准，请在 DSH 中处理";
    if (event.type === "tool/call") next.activity = "正在执行工具";
    if (event.type === "tool/result") next.activity = "正在整理结果";
    if (event.type === "turn/end") {
      if (event.data?.reason?.kind === "error")
        throw new Error(
          event.data.reason.error?.message || "DSH 本轮执行失败。",
        );
      if (event.data?.reason?.kind === "aborted")
        throw new DOMException("DSH 已停止本轮生成。", "AbortError");
      if (event.data?.error)
        throw new Error(event.data.error.message || String(event.data.error));
      next.done = true;
    }
  }
  return next;
}

export class DshRemoteError extends Error {
  constructor(
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}

export async function dshRpc<T>(
  method: string,
  request: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  const rpcId = crypto.randomUUID();
  const response = await fetch(`/dsh/api/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({
      type: "client-request",
      rpcId,
      method,
      payload: {
        args: { [method === "session/list" ? "_request" : "request"]: request },
      },
    }),
  });
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? "DSH 尚未认证。请在连接设置中输入 DSH 启动链接里的 token。"
        : `DSH 连接失败（HTTP ${response.status}），请检查 .env.local 的服务地址。`,
    );
  const envelope = await response.json();
  if (envelope.rpcId !== rpcId) throw new Error("DSH 返回了不匹配的请求标识。");
  if (envelope.result?.ok !== true)
    throw new DshRemoteError(
      envelope.result?.error?.message || "DSH 请求未成功。",
      envelope.result?.error?.code,
    );
  return envelope.result.value as T;
}

export async function authenticateDsh(token: string) {
  let raw = token.trim();
  if (/^https?:\/\//.test(raw))
    raw = new URL(raw).searchParams.get("token") || "";
  if (!raw) throw new Error("请输入 DSH 启动链接中的 token。");
  const response = await fetch(`/dsh/?token=${encodeURIComponent(raw)}`, {
    cache: "no-store",
  });
  if (!response.ok)
    throw new Error(
      "DSH 认证未通过，请检查 token 和代理地址是否属于同一服务。",
    );
}

export async function streamDsh(
  remoteId: string | undefined,
  cwd: string,
  prompt: string,
  signal: AbortSignal,
  emit: Emit,
) {
  const sessionId =
    remoteId ??
    (await dshRpc<{ sessionId: string }>("session/create", { cwd }, signal))
      .sessionId;
  emit({ type: "session", id: sessionId });
  signal.throwIfAborted();
  const url = new URL("/dsh/api/remote.mux", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url);
  const streamId = crypto.randomUUID();
  const requestId = crypto.randomUUID();
  let state = emptyDshText(),
    prompted = false,
    settled = false,
    ownMessageSeen = false;
  let currentTurn: number | undefined,
    ownTurn: number | undefined,
    queuedId: string | undefined;
  let admission: Promise<unknown> | undefined;
  type QueuedMessage = { id: string; source?: { rpcId?: string } };
  const queues: Record<string, QueuedMessage[]> = {};
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(
      () => finish(new Error("DSH 订阅超时，请检查连接设置。")),
      15_000,
    );
    function finish(error?: unknown) {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      if (socket.readyState === WebSocket.OPEN)
        socket.send(JSON.stringify({ type: "cancel", streamId }));
      socket.close();
      error ? reject(error) : resolve();
    }
    function onAbort() {
      if (!prompted) {
        finish(signal.reason);
        return;
      }
      void cancelOwnedPrompt().catch((error) =>
        finish(
          new Error(
            `停止请求未确认：${error.message}。请在 DSH 中检查该会话，不要重复发送任务。`,
          ),
        ),
      );
    }
    async function cancelOwnedPrompt() {
      // Admission has no user-abort signal: cancelling its HTTP request cannot undo admission.
      await admission;
      if (settled) return;
      if (!ownMessageSeen && !queuedId) {
        const projection = await dshRpc<{
          values?: {
            inbox?: Record<
              string,
              Array<{ id: string; source?: { rpcId?: string } }>
            >;
          };
        }>("session/projections", { sessionId }, AbortSignal.timeout(8_000));
        queuedId = Object.values(projection.values?.inbox ?? {})
          .flat()
          .find((message) => message.source?.rpcId === requestId)?.id;
      }
      if (queuedId && !ownMessageSeen) {
        try {
          await dshRpc(
            "session/updateQueue",
            { sessionId, itemId: queuedId, action: { kind: "remove" } },
            AbortSignal.timeout(8_000),
          );
          finish(signal.reason);
          return;
        } catch (error) {
          if (
            !(error instanceof DshRemoteError) ||
            error.code !== "session/queue-item-not-found"
          )
            throw error;
        }
      }
      if (!ownMessageSeen)
        await new Promise((resolve) => window.setTimeout(resolve, 200));
      if (settled) return;
      if (!ownMessageSeen) throw new Error("尚未确认此消息的执行轮次");
      await dshRpc("session/cancel", { sessionId }, AbortSignal.timeout(8_000));
      finish(signal.reason);
    }
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) {
      onAbort();
      return;
    }
    socket.onopen = () => {
      if (settled) return;
      socket.send(
        JSON.stringify({
          type: "open",
          streamId,
          endpoint: "session/follow",
          payload: {
            args: {
              request: {
                address: { kind: "session", sessionId },
                assistantStream: true,
              },
            },
          },
        }),
      );
    };
    socket.onerror = () =>
      finish(new Error("DSH 流式连接失败，请检查认证和服务地址。"));
    socket.onclose = () => {
      if (!settled)
        finish(
          new Error(
            "DSH 流式连接中断，请在 DSH 检查该会话；不会自动重发消息。",
          ),
        );
    };
    socket.onmessage = async (event) => {
      if (settled) return;
      try {
        const message = JSON.parse(event.data);
        if (message.streamId !== streamId) return;
        if (message.type === "error")
          throw new Error(message.error?.message || "DSH 订阅失败。");
        if (message.type === "end")
          throw new Error("DSH 订阅提前结束，请检查会话状态。");
        if (message.type !== "item") return;
        if (message.value.type === "snapshot") {
          if (prompted || signal.aborted) return;
          const values = message.value.projections?.values;
          const inbox = values?.inbox ?? {};
          if (
            message.value.assistantStream?.activeAttempt ||
            values?.turnBoundary?.openTurnStartSeq != null ||
            Object.values(inbox).some(
              (items) => Array.isArray(items) && items.length,
            )
          )
            throw new Error(
              "此会话仍有任务正在运行或等待执行，请先在 DSH 中处理，或新建会话。",
            );
          for (const [target, items] of Object.entries(inbox))
            if (Array.isArray(items)) queues[target] = [...items];
          window.clearTimeout(timeout);
          prompted = true;
          admission = dshRpc(
            "session/prompt",
            {
              sessionId,
              requestId,
              mode: "queue",
              content: [{ type: "text", text: prompt }],
              clientTimeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            },
            AbortSignal.timeout(30_000),
          );
          await admission;
          return;
        }
        const value = message.value;
        if (value.type === "event") {
          if (value.event.type === "turn/start")
            currentTurn = value.event.data?.turn;
          if (value.event.type === "agent/inbox/spliced") {
            const splice = value.event.data;
            const queue = (queues[splice.target ?? "next-turn"] ??= []);
            const removed = queue.splice(
              splice.start ?? queue.length,
              splice.removedCount ?? 0,
              ...(splice.inserted ?? []),
            );
            const queued = splice.inserted?.find(
              (item: QueuedMessage) => item.source?.rpcId === requestId,
            );
            if (queued) queuedId = queued.id;
            const ownRemoved = removed.some(
              (item) => item.source?.rpcId === requestId,
            );
            if (ownRemoved && !queued) {
              if (splice.outcome === "canceled") {
                finish(
                  new DOMException("DSH 已移除待执行消息。", "AbortError"),
                );
                return;
              }
              // DSH can fail during preparation before appending user/message.
              // A claimed queue item is already owned by this turn.
              ownMessageSeen = true;
              ownTurn = currentTurn;
            }
          }
          if (
            value.event.type === "user/message" &&
            value.event.data?.source?.rpcId === requestId
          ) {
            ownMessageSeen = true;
            ownTurn = currentTurn;
          }
        }
        if (!ownMessageSeen) return;
        const eventTurn =
          value.type === "assistant-stream"
            ? value.frame?.turn
            : value.event?.data?.turn;
        if (
          ownTurn !== undefined &&
          eventTurn !== undefined &&
          eventTurn !== ownTurn
        )
          return;
        if (signal.aborted) {
          if (value.type === "event" && value.event.type === "turn/end")
            finish(signal.reason);
          return;
        }
        state = reduceDshEvent(state, message.value);
        emit({
          type: "replace",
          text: [state.committed, state.draft].filter(Boolean).join("\n\n"),
        });
        emit({ type: "status", text: state.activity });
        if (state.done) {
          emit({ type: "done" });
          finish();
        }
      } catch (error) {
        if (!signal.aborted) finish(error);
      }
    };
  });
}
