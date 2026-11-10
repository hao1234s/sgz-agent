import { afterEach, describe, expect, it, vi } from "vitest";
import { dshRpc, emptyDshText, reduceDshEvent } from "./dsh";
afterEach(() => vi.unstubAllGlobals());
describe("DSH request envelope", () => {
  it("uses the installed session/list parameter name and verifies correlation", async () => {
    vi.stubGlobal("fetch", async (_url: string, options: RequestInit) => {
      const body = JSON.parse(String(options.body));
      if (!body.payload.args._request || body.payload.args.request)
        return new Response(
          JSON.stringify({
            rpcId: body.rpcId,
            result: { ok: false, error: { message: "Invalid argument name" } },
          }),
        );
      return new Response(
        JSON.stringify({
          rpcId: body.rpcId,
          result: { ok: true, value: { items: [] } },
        }),
      );
    });
    await expect(dshRpc("session/list", {})).resolves.toEqual({ items: [] });
  });
  it("shows an actionable authentication error instead of a successful empty reply", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 401 }));
    await expect(dshRpc("session/create", {})).rejects.toThrow("尚未认证");
  });
});
describe("DSH presentation frames", () => {
  it("reports an error terminal even after receiving partial output", () => {
    expect(() =>
      reduceDshEvent(
        { ...emptyDshText(), draft: "partial" },
        {
          type: "event",
          event: {
            type: "turn/end",
            data: {
              turn: 1,
              reason: {
                kind: "error",
                error: { message: "Provider unavailable" },
              },
            },
          },
        },
      ),
    ).toThrow("Provider unavailable");
  });
  it("recognizes cancellation performed in the DSH application", () => {
    expect(() =>
      reduceDshEvent(emptyDshText(), {
        type: "event",
        event: {
          type: "turn/end",
          data: { turn: 1, reason: { kind: "aborted" } },
        },
      }),
    ).toThrow(expect.objectContaining({ name: "AbortError" }));
  });
  it("replaces provisional text with the durable message without duplication", () => {
    let state = emptyDshText();
    state = reduceDshEvent(state, {
      type: "assistant-stream",
      frame: { type: "chunk", chunk: { type: "text-delta", text: "你好" } },
    });
    expect(state.draft).toBe("你好");
    state = reduceDshEvent(state, {
      type: "event",
      event: {
        type: "assistant/message",
        data: { message: { content: [{ type: "text", text: "你好！" }] } },
      },
    });
    expect(state.committed + state.draft).toBe("你好！");
  });
  it("keeps completed steps when the next assistant attempt starts", () => {
    let state = reduceDshEvent(emptyDshText(), {
      type: "event",
      event: {
        type: "assistant/message",
        data: { message: { content: [{ type: "text", text: "先查询。" }] } },
      },
    });
    state = reduceDshEvent(state, {
      type: "assistant-stream",
      frame: { type: "start" },
    });
    state = reduceDshEvent(state, {
      type: "assistant-stream",
      frame: { type: "chunk", chunk: { type: "text-delta", text: "结果" } },
    });
    expect(state.committed + "\n\n" + state.draft).toBe("先查询。\n\n结果");
  });
  it("does not display reasoning as answer text or auto-approve a tool", () => {
    let state = reduceDshEvent(emptyDshText(), {
      type: "assistant-stream",
      frame: {
        type: "chunk",
        chunk: { type: "reasoning-delta", text: "private" },
      },
    });
    expect(state.committed + state.draft).toBe("");
    state = reduceDshEvent(state, {
      type: "event",
      event: { type: "approval/asked", data: {} },
    });
    expect(state.activity).toContain("DSH");
    expect(state.done).toBe(false);
  });
});
