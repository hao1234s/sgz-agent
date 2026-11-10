import { describe, expect, it } from "vitest";
import { readSSE } from "./stream";
import type { StreamEvent } from "./types";

function bytes(text: string, size = 1) {
  const encoded = new TextEncoder().encode(text);
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < encoded.length; i += size)
        controller.enqueue(encoded.slice(i, i + size));
      controller.close();
    },
  });
}
describe("SSE wire parsing", () => {
  it("preserves Chinese split across UTF-8 bytes and CRLF event boundaries", async () => {
    const events: StreamEvent[] = [];
    await readSSE(
      bytes(
        ': heartbeat\r\n\r\ndata: {"type":"delta","text":"你好"}\r\n\r\ndata: [DONE]\r\n\r\n',
      ),
      (e) => events.push(e),
    );
    expect(events).toEqual([{ type: "delta", text: "你好" }, { type: "done" }]);
  });
  it("reads multiline JSON and OpenAI-compatible deltas", async () => {
    const events: StreamEvent[] = [];
    await readSSE(
      bytes(
        'data: {"choices":\ndata: [{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n',
        17,
      ),
      (e) => events.push(e),
    );
    expect(events).toEqual([
      { type: "delta", text: "hello" },
      { type: "done" },
    ]);
  });
  it("reports explicit server errors", async () => {
    await expect(
      readSSE(
        bytes('event: error\ndata: {"message":"模型不可用"}\n\n'),
        () => {},
      ),
    ).rejects.toThrow("模型不可用");
  });
  it("does not silently treat a truncated stream as success", async () => {
    await expect(
      readSSE(bytes('data: {"type":"delta","text":"partial"}\n\n'), () => {}),
    ).rejects.toThrow("中断");
  });
  it("rejects malformed event JSON", async () => {
    await expect(
      readSSE(bytes("data: {broken}\n\n"), () => {}),
    ).rejects.toThrow("JSON");
  });
  it("cancels an idle reader when aborted", async () => {
    const controller = new AbortController();
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancelled = true;
      },
    });
    const promise = readSSE(body, () => {}, controller.signal);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toBe(true);
  });
});
