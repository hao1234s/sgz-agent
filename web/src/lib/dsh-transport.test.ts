import { afterEach, describe, expect, it, vi } from "vitest";
import { streamDsh } from "./dsh";
import type { StreamEvent } from "./types";

// The fixture models the installed HTTP envelope + mux wire boundary; no DSH process is started.
class SocketFixture {
  static OPEN = 1;
  static instance: SocketFixture;
  readyState = 1;
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: () => void;
  onerror?: () => void;
  streamId = "";
  static snapshot: object = {
    type: "snapshot",
    records: [],
    projections: { values: { turnBoundary: { openTurnStartSeq: null } } },
  };
  constructor() {
    SocketFixture.instance = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(raw: string) {
    const message = JSON.parse(raw);
    if (message.type === "open") {
      this.streamId = message.streamId;
      queueMicrotask(() => this.deliver(SocketFixture.snapshot));
    }
  }
  deliver(value: object) {
    if (this.readyState === 1)
      this.onmessage?.({
        data: JSON.stringify({ type: "item", streamId: this.streamId, value }),
      });
  }
  close() {
    this.readyState = 3;
  }
}
function install(
  onRequest: (method: string, request: any) => Promise<object> | object,
) {
  SocketFixture.snapshot = {
    type: "snapshot",
    records: [],
    projections: { values: { turnBoundary: { openTurnStartSeq: null } } },
  };
  vi.stubGlobal("window", {
    location: { href: "http://127.0.0.1:5173/" },
    setTimeout,
    clearTimeout,
  });
  vi.stubGlobal("WebSocket", SocketFixture);
  vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    const value = await onRequest(
      body.method,
      body.payload.args.request ?? body.payload.args._request,
    );
    return new Response(
      JSON.stringify({ rpcId: body.rpcId, result: { ok: true, value } }),
    );
  });
}
afterEach(() => vi.unstubAllGlobals());
describe("DSH request lifetime", () => {
  it("surfaces preparation failures after its queue item is claimed but before user/message", async () => {
    install((method, request) => {
      if (method === "session/prompt")
        queueMicrotask(() => {
          const ws = SocketFixture.instance;
          ws.deliver({
            type: "event",
            event: {
              type: "agent/inbox/spliced",
              data: {
                target: "next-turn",
                start: 0,
                inserted: [
                  {
                    id: "queued-preflight",
                    source: { kind: "user", rpcId: request.requestId },
                  },
                ],
              },
            },
          });
          ws.deliver({
            type: "event",
            event: { type: "turn/start", data: { turn: 3 } },
          });
          ws.deliver({
            type: "event",
            event: {
              type: "agent/inbox/spliced",
              data: {
                target: "next-turn",
                start: 0,
                removedCount: 1,
                inserted: [],
              },
            },
          });
          ws.deliver({
            type: "event",
            event: {
              type: "turn/end",
              data: {
                turn: 3,
                reason: {
                  kind: "error",
                  error: { message: "Preparation failed" },
                },
              },
            },
          });
        });
      return { accepted: true };
    });
    await expect(
      streamDsh(
        "known",
        "D:\\agent",
        "hello",
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("Preparation failed");
  }, 500);
  it("does not cancel the active turn before prompt admission finishes", async () => {
    let acknowledge: (() => void) | undefined;
    let cancelCalls = 0;
    install(async (method, request) => {
      if (method === "session/prompt") {
        await new Promise<void>((resolve) => {
          acknowledge = () => {
            SocketFixture.instance.deliver({
              type: "event",
              event: { type: "turn/start", data: { turn: 1 } },
            });
            SocketFixture.instance.deliver({
              type: "event",
              event: {
                type: "user/message",
                data: { source: { kind: "user", rpcId: request.requestId } },
              },
            });
            resolve();
          };
        });
      }
      if (method === "session/cancel") cancelCalls++;
      return { accepted: true };
    });
    const abort = new AbortController();
    const pending = streamDsh(
      "known",
      "D:\\agent",
      "hello",
      abort.signal,
      () => {},
    ).catch((error) => error);
    await vi.waitFor(() => expect(acknowledge).toBeTypeOf("function"));
    abort.abort();
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(cancelCalls).toBe(0);
    acknowledge!();
    expect(await pending).toMatchObject({ name: "AbortError" });
    expect(cancelCalls).toBe(1);
  });
  it("removes its pending queue item instead of cancelling an unrelated running turn", async () => {
    let accepted = false;
    const operations: string[] = [];
    install((method, request) => {
      operations.push(method);
      if (method === "session/prompt") {
        SocketFixture.instance.deliver({
          type: "event",
          event: {
            type: "agent/inbox/spliced",
            data: {
              inserted: [
                {
                  id: "queued-1",
                  source: { kind: "user", rpcId: request.requestId },
                },
              ],
            },
          },
        });
        accepted = true;
      }
      if (method === "session/updateQueue")
        expect(request).toEqual({
          sessionId: "known",
          itemId: "queued-1",
          action: { kind: "remove" },
        });
      return { accepted: true };
    });
    const abort = new AbortController();
    const pending = streamDsh(
      "known",
      "D:\\agent",
      "hello",
      abort.signal,
      () => {},
    ).catch((error) => error);
    await vi.waitFor(() => expect(accepted).toBe(true));
    abort.abort();
    expect(await pending).toMatchObject({ name: "AbortError" });
    expect(operations).toContain("session/updateQueue");
    expect(operations).not.toContain("session/cancel");
  });
  it("ignores unrelated turn events before its own accepted user message", async () => {
    install((method, request) => {
      if (method === "session/prompt")
        queueMicrotask(() => {
          const ws = SocketFixture.instance;
          ws.deliver({
            type: "assistant-stream",
            frame: {
              type: "chunk",
              turn: 1,
              chunk: { type: "text-delta", text: "old" },
            },
          });
          ws.deliver({
            type: "event",
            event: { type: "turn/end", data: { turn: 1 } },
          });
          ws.deliver({
            type: "event",
            event: { type: "turn/start", data: { turn: 2 } },
          });
          ws.deliver({
            type: "event",
            event: {
              type: "user/message",
              data: { source: { kind: "user", rpcId: request.requestId } },
            },
          });
          ws.deliver({
            type: "assistant-stream",
            frame: {
              type: "chunk",
              turn: 2,
              chunk: { type: "text-delta", text: "new" },
            },
          });
          ws.deliver({
            type: "event",
            event: { type: "turn/end", data: { turn: 2 } },
          });
        });
      return { accepted: true };
    });
    const events: StreamEvent[] = [];
    await streamDsh(
      "known",
      "D:\\agent",
      "hello",
      new AbortController().signal,
      (event) => events.push(event),
    );
    expect(events.filter((e) => e.type === "replace").at(-1)).toEqual({
      type: "replace",
      text: "new",
    });
  });
  it("refuses an active snapshot without submitting another prompt", async () => {
    let prompts = 0;
    install((method) => {
      if (method === "session/prompt") {
        prompts++;
        SocketFixture.instance.deliver({
          type: "event",
          event: { type: "turn/end", data: { turn: 1 } },
        });
      }
      return { accepted: true };
    });
    SocketFixture.snapshot = {
      type: "snapshot",
      assistantStream: { activeAttempt: { turn: 1 } },
    };
    await expect(
      streamDsh(
        "known",
        "D:\\agent",
        "hello",
        new AbortController().signal,
        () => {},
      ),
    ).rejects.toThrow("正在运行");
    expect(prompts).toBe(0);
  });
});
