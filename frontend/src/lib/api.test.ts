import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, readEvents } from "./api";

const conversation = {
  id: "c1",
  title: "策略",
  updatedAt: 1,
  status: "idle",
  messages: [],
};
const encode = (events: unknown[]) =>
  events.map((event) => `data: ${JSON.stringify(event)}\r\n\r\n`).join("");
afterEach(() => vi.unstubAllGlobals());
describe("authenticated API", () => {
  it("uses same-origin cookies and the application header for login and mutations", async () => {
    const requests: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      requests.push([url, init]);
      return Response.json({
        user: { id: "u", username: "alice", role: "user" },
      });
    });
    expect((await api.login("alice", "long-password")).user.username).toBe(
      "alice",
    );
    await api.cancel("c1");
    expect(requests[0][0]).toBe("/api/auth/login");
    expect(requests[0][1]?.credentials).toBe("same-origin");
    expect(new Headers(requests[0][1]?.headers).get("X-SGZ-Request")).toBe("1");
    expect(requests[1][0]).toBe("/api/conversations/c1/cancel");
    expect(JSON.parse(String(requests[1][1]?.body))).toEqual({});
  });
  it("preserves auth expiry and busy failures without retrying a mutation", async () => {
    let count = 0;
    vi.stubGlobal("fetch", async () => {
      count++;
      return Response.json(
        { message: "Authentication required" },
        { status: 401 },
      );
    });
    await expect(api.conversations()).rejects.toMatchObject({ status: 401 });
    vi.stubGlobal("fetch", async () => {
      count++;
      return Response.json(
        { message: "Run capacity is busy" },
        { status: 409 },
      );
    });
    await expect(
      api.send("c1", "hello", "r1", new AbortController().signal, () => {}),
    ).rejects.toBeInstanceOf(ApiError);
    expect(count).toBe(2);
  });
  it("sends only server-owned text and request id, then returns the committed conversation", async () => {
    let request: RequestInit | undefined;
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      request = init;
      return new Response(
        encode([
          {
            type: "run",
            id: "r",
            userMessage: { id: "m1" },
            assistantMessage: { id: "m2" },
          },
          { type: "delta", text: "你" },
          { type: "replace", text: "你好" },
          { type: "status", text: "计算中" },
          { type: "done", conversation },
        ]),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    });
    const events: unknown[] = [];
    expect(
      await api.send(
        "c1",
        "hello",
        "r1",
        new AbortController().signal,
        (event) => events.push(event),
      ),
    ).toEqual(conversation);
    expect(JSON.parse(String(request?.body))).toEqual({
      text: "hello",
      requestId: "r1",
    });
    expect(events.map((event) => (event as { type: string }).type)).toEqual([
      "run",
      "delta",
      "replace",
      "status",
      "done",
    ]);
  });
});
describe("server SSE", () => {
  it("decodes split UTF-8 and CRLF frames without losing the final authoritative text", async () => {
    const data = new TextEncoder().encode(
      encode([
        { type: "delta", text: "三国" },
        { type: "done", conversation },
      ]),
    );
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of data) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    });
    const events: unknown[] = [];
    expect(await readEvents(body, (event) => events.push(event))).toEqual(
      conversation,
    );
    expect(events[0]).toEqual({ type: "delta", text: "三国" });
  });
  it("reports server error events and interrupted streams explicitly", async () => {
    await expect(
      readEvents(
        new Response(encode([{ type: "error", message: "generation failed" }]))
          .body!,
        () => {},
      ),
    ).rejects.toThrow("generation failed");
    await expect(
      readEvents(
        new Response(encode([{ type: "delta", text: "partial" }])).body!,
        () => {},
      ),
    ).rejects.toThrow("中断");
  });
  it("aborts an idle stream and suppresses events buffered after cancellation", async () => {
    const controller = new AbortController();
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancelled = true;
      },
    });
    const pending = readEvents(
      body,
      () => {
        throw new Error("stale event");
      },
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toBe(true);
  });
});
