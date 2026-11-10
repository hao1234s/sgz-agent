import type { Request, Response } from "express";
import { randomUUID } from "node:crypto";
import type { Store, UserRow } from "../storage/store.js";
import type { RuntimeAdapter, RuntimeEvent } from "../agent/contracts.js";
import { body, fail } from "../http/request.js";

type ActiveRun = {
  userId: string;
  abort: AbortController;
  done: Promise<void>;
};

export function createConversationRoutes(options: {
  store: Store;
  runner: RuntimeAdapter;
  maxConcurrentRuns?: number;
}) {
  const { store, runner } = options;
  const db = store.db;
  const capacity = options.maxConcurrentRuns ?? 4;
  if (!Number.isSafeInteger(capacity) || capacity < 1 || capacity > 1024)
    throw new Error("Invalid maxConcurrentRuns");
  const active = new Map<string, ActiveRun>();
  let stopping = false;
  const stopUser = async (userId: string) => {
    const jobs = [...active.values()].filter((job) => job.userId === userId);
    jobs.forEach((job) => job.abort.abort());
    await Promise.allSettled(jobs.map((job) => job.done));
  };
  const lifecycle = {
    async onModuleDestroy() {
      stopping = true;
      const jobs = [...active.values()];
      jobs.forEach((job) => job.abort.abort());
      await Promise.allSettled(jobs.map((job) => job.done));
      await runner.close?.();
    },
  };

  async function handle(
    req: Request,
    res: Response,
    path: string,
    method: string,
    user: UserRow,
  ) {
    if (path === "/conversations" && method === "GET") {
      const rows = db
        .prepare(
          "SELECT id,title,status,updated_at FROM conversations WHERE user_id=? ORDER BY updated_at DESC,id",
        )
        .all(user.id);
      res.json({
        conversations: rows.map((row) => ({
          id: row.id,
          title: row.title,
          status: row.status,
          updatedAt: row.updated_at,
        })),
      });
      return;
    }
    if (path === "/conversations" && method === "POST") {
      body(req, []);
      const id = randomUUID();
      const now = Date.now();
      db.prepare(
        "INSERT INTO conversations(id,user_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
      ).run(id, user.id, "新会话", "idle", now, now);
      res.status(201).json({ conversation: store.conversation(id, user.id) });
      return;
    }
    const match = /^\/conversations\/([^/]+)(?:\/(messages|cancel))?$/.exec(
      path,
    );
    if (!match) return fail(404, "Not found");
    const id = match[1];
    const action = match[2];
    const conversation = store.conversation(id, user.id);
    if (!conversation) return fail(404, "Conversation not found");
    if (!action && method === "GET") {
      res.json({ conversation });
      return;
    }
    if (!action && method === "DELETE") {
      if (active.has(id)) return fail(409, "Conversation is running");
      db.prepare("DELETE FROM conversations WHERE id=? AND user_id=?").run(
        id,
        user.id,
      );
      res.status(204).end();
      return;
    }
    if (action === "cancel" && method === "POST") {
      body(req, []);
      const job = active.get(id);
      if (job) {
        job.abort.abort();
        await job.done;
      }
      res.json({ cancelled: Boolean(job) });
      return;
    }
    if (action !== "messages" || method !== "POST")
      return fail(404, "Not found");
    const input = body(req, ["text", "requestId"]);
    if (
      typeof input.text !== "string" ||
      !input.text.trim() ||
      input.text.length > 32_000 ||
      typeof input.requestId !== "string" ||
      !/^[A-Za-z0-9_.:-]{1,128}$/.test(input.requestId)
    )
      return fail(400, "Invalid message or requestId");
    if (
      db
        .prepare("SELECT id FROM runs WHERE conversation_id=? AND request_id=?")
        .get(id, input.requestId)
    )
      return fail(409, "Request already exists");
    if (
      active.has(id) ||
      [...active.values()].some((job) => job.userId === user.id) ||
      active.size >= capacity
    )
      return fail(409, "Run capacity is busy");
    if (!runner.ready) return fail(503, "Model runtime is not configured");
    const runId = randomUUID();
    const userMessageId = randomUUID();
    const assistantId = randomUUID();
    const now = Date.now();
    const text = input.text.trim();
    store.transaction(() => {
      db.prepare(
        "INSERT INTO runs(id,conversation_id,user_id,request_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
      ).run(runId, id, user.id, input.requestId as string, "running", now, now);
      const insert = db.prepare(
        "INSERT INTO messages(id,conversation_id,run_id,role,content,status,created_at) VALUES(?,?,?,?,?,?,?)",
      );
      insert.run(userMessageId, id, runId, "user", text, "complete", now);
      insert.run(assistantId, id, runId, "assistant", "", "streaming", now);
      db.prepare(
        "UPDATE conversations SET status=?,updated_at=?,title=? WHERE id=?",
      ).run(
        "running",
        now,
        conversation.messages.length ? conversation.title : text.slice(0, 60),
        id,
      );
    });
    const abort = new AbortController();
    const send = (event: unknown) => {
      if (!res.destroyed && !res.writableEnded)
        res.write(`data: ${JSON.stringify(event)}\n\n`);
    };
    res
      .status(200)
      .set({
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      });
    res.flushHeaders();
    res.on("close", () => {
      if (!res.writableEnded) abort.abort();
    });
    const messages = store.conversation(id, user.id)!.messages;
    send({
      type: "run",
      id: runId,
      userMessage: messages.at(-2),
      assistantMessage: messages.at(-1),
    });
    let content = "";
    let terminal = false;
    const onEvent = (event: RuntimeEvent) => {
      if (terminal) return;
      if (event.type === "delta") content += event.text;
      if (event.type === "replace") content = event.text;
      if (event.type !== "status")
        db.prepare("UPDATE messages SET content=? WHERE id=?").run(
          content,
          assistantId,
        );
      send(event);
    };
    const done = Promise.resolve().then(async () => {
      let status = "complete";
      let error: string | undefined;
      try {
        const result = await runner.run({
          conversationId: id,
          userId: user.id,
          runId,
          text,
          signal: abort.signal,
          onEvent,
        });
        content = result.text;
      } catch {
        status = abort.signal.aborted ? "stopped" : "error";
        if (status === "error") error = "Headless generation failed";
      } finally {
        terminal = true;
        if (abort.signal.aborted) status = "stopped";
        store.transaction(() => {
          db.prepare(
            "UPDATE messages SET content=?,status=?,error=? WHERE id=?",
          ).run(content, status, error ?? null, assistantId);
          db.prepare("UPDATE runs SET status=?,updated_at=? WHERE id=?").run(
            status,
            Date.now(),
            runId,
          );
          db.prepare(
            "UPDATE conversations SET status='idle',updated_at=? WHERE id=?",
          ).run(Date.now(), id);
        });
        if (error) send({ type: "error", message: error });
        send({ type: "done", conversation: store.conversation(id, user.id) });
        res.end();
        active.delete(id);
      }
    });
    active.set(id, { userId: user.id, abort, done });
    // The cancellation/shutdown owners also await this promise; avoid an unhandled rejection on a broken database.
    void done.catch(() => {
      abort.abort();
      res.end();
      active.delete(id);
    });
  }
  return {
    handle,
    stopUser,
    lifecycle,
    get stopping() {
      return stopping;
    },
  };
}
