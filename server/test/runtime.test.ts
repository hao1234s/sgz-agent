import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, rmSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import { HeadlessRunner } from "../src/agent/runtime.js";

test(
  "real headless is isolated, persists native history and resumes after restart with lossless text",
  { timeout: 120000 },
  async () => {
    const work = mkdtempSync(join(tmpdir(), "sgz-headless-"));
    const db = new DatabaseSync(join(work, "db.sqlite"));
    db.exec(
      "PRAGMA foreign_keys=ON; CREATE TABLE conversations(id TEXT PRIMARY KEY); INSERT INTO conversations VALUES ('alice-chat'),('bob-chat');",
    );
    const calls: any[] = [];
    let slowStarted: () => void = () => {};
    const model = createServer(async (req, res) => {
      let body = "";
      for await (const chunk of req) body += chunk;
      const input = JSON.parse(body);
      calls.push(input);
      const last = JSON.stringify(
        input.messages
          .filter(
            (m: any) =>
              m.role === "user" &&
              !String(m.content).startsWith("Current runtime context."),
          )
          .at(-1)?.content,
      );
      if (last.includes("slow-test")) {
        slowStarted();
        return;
      }
      const calculation = last.includes("calculate-test");
      const skill = last.includes("skill-test");
      const toolResult = input.messages.findLast((m: any) => m.role === "tool");
      const text = last.includes("long")
        ? "中文".repeat(6000)
        : calculation && toolResult
          ? "calculation result: wait"
          : last.includes("remember")
            ? "The earlier secret was maple."
            : "maple";
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const toolCall = (calculation || skill) && !toolResult;
      const delta = toolCall
        ? {
            tool_calls: [
              {
                index: 0,
                id: "call-calc",
                type: "function",
                function: {
                  name: skill ? "sgz_skill_load" : "sgz_assess_combat",
                  arguments: skill
                    ? '{"name":"sgz-combat-assessment"}'
                    : '{"phase":"draw"}',
                },
              },
            ],
          }
        : { content: text };
      for (const part of [{ role: "assistant", content: "" }, delta])
        res.write(
          `data: ${JSON.stringify({ id: "reply", object: "chat.completion.chunk", created: 1, model: "test-model", choices: [{ index: 0, delta: part, finish_reason: null }] })}\n\n`,
        );
      res.end(
        `data: ${JSON.stringify({ id: "reply", object: "chat.completion.chunk", created: 1, model: "test-model", choices: [{ index: 0, delta: {}, finish_reason: toolCall ? "tool_calls" : "stop" }], usage: { prompt_tokens: 4, completion_tokens: 4, total_tokens: 8 } })}\n\ndata: [DONE]\n\n`,
      );
    });
    await new Promise<void>((r) => model.listen(0, "127.0.0.1", r));
    const baseUrl = `http://127.0.0.1:${(model.address() as any).port}/v1`;
    const options = {
      rootDir: process.env.SGZ_TEST_RELEASE_ROOT ?? resolve(import.meta.dirname, "../.."),
      dataDir: work,
      db,
      model: { baseUrl, apiKey: "local-protocol-test", name: "test-model" },
      timeoutMs: 30000,
    };
    let runner = new HeadlessRunner(options);
    const send = (conversationId: string, text: string) =>
      runner.run({
        conversationId,
        userId: conversationId,
        runId: randomUUID(),
        text,
        signal: new AbortController().signal,
        onEvent: () => {},
      });
    try {
      assert.equal(
        (await send("alice-chat", "My secret is maple.")).text,
        "maple",
      );
      await runner.close();
      runner = new HeadlessRunner(options);
      assert.match(
        (await send("alice-chat", "remember my secret")).text,
        /maple/,
      );
      assert.ok(
        JSON.stringify(calls[1].messages).includes("My secret is maple."),
      );
      const long = (await send("bob-chat", "long response")).text;
      assert.ok(
        long === "中文".repeat(6000),
        `Expected 12000 characters, got ${long.length}; messages=${JSON.stringify(calls.at(-1).messages).slice(-1500)}`,
      );
      assert.ok(
        !JSON.stringify(calls[2].messages).includes("My secret is maple."),
      );
      const tools = calls
        .flatMap((c) => c.tools ?? [])
        .map((t) => t.function.name);
      assert.ok(tools.includes("sgz_assess_combat"));
      assert.ok(
        tools.every((name) => name.startsWith("sgz_")),
        JSON.stringify(tools),
      );
      await send("bob-chat", "skill-test");
      const loadedSkill = JSON.stringify(
        calls.at(-1).messages.findLast((m: any) => m.role === "tool"),
      );
      assert.match(loadedSkill, /通用战斗评估/);
      assert.doesNotMatch(loadedSkill, /skill_not_available/);
      assert.match(loadedSkill, /通用评估模型与研究依据/);
      assert.equal(
        (await send("alice-chat", "calculate-test")).text,
        "calculation result: wait",
      );
      assert.match(
        JSON.stringify(
          calls.at(-1).messages.findLast((m: any) => m.role === "tool"),
        ),
        /wait/,
      );
      const abort = new AbortController();
      const started = new Promise<void>((r) => {
        slowStarted = r;
      });
      const cancelled = runner.run({
        conversationId: "alice-chat",
        userId: "alice-chat",
        runId: randomUUID(),
        text: "slow-test",
        signal: abort.signal,
        onEvent: () => {},
      });
      const rejection = assert.rejects(
        cancelled,
        (error: any) => error.name === "AbortError",
      );
      await started;
      abort.abort();
      await rejection;
      assert.equal(
        (await send("alice-chat", "continue after stop")).text,
        "maple",
      );
      assert.equal(
        (db.prepare("SELECT count(*) AS n FROM native_sessions").get() as any)
          .n,
        2,
      );
    } finally {
      await runner.close();
      model.closeAllConnections();
      await new Promise<void>((r) => model.close(() => r()));
      db.close();
      rmSync(work, { recursive: true, force: true });
    }
  },
);
