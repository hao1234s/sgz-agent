import { createHash } from "node:crypto";
import type { DatabaseSync } from "node:sqlite";
import {
  assertContiguous,
  materializeCreateHeader,
  materializeAppendBatch,
  validateStoredEvents,
} from "@deepseek-ai/dsh-session-persistence";

type Row = Record<string, any>;
/** The bridge capability selects the conversation; child-supplied IDs cannot change it. */
export class NativeStore {
  private readonly claims = new Map<
    string,
    { runId: string; handle: string; conversationId: string }
  >();
  constructor(private readonly db: DatabaseSync) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS native_sessions (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE REFERENCES conversations(id) ON DELETE CASCADE,
        header TEXT NOT NULL, event_count INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS native_events (
        session_id TEXT NOT NULL REFERENCES native_sessions(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(session_id,seq));
      CREATE TABLE IF NOT EXISTS native_batches (
        session_id TEXT NOT NULL REFERENCES native_sessions(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(session_id,request_id));
    `);
  }
  nativeId(conversationId: string): string | undefined {
    return (
      this.db
        .prepare("SELECT id FROM native_sessions WHERE conversation_id=?")
        .get(conversationId) as Row | undefined
    )?.id;
  }
  releaseRun(runId: string): void {
    for (const [id, claim] of this.claims)
      if (claim.runId === runId) this.claims.delete(id);
  }
  scope(
    conversationId: string,
    runId: string,
    onEvents?: (events: Row[]) => void,
  ) {
    const row = (id: string): Row | undefined =>
      this.db
        .prepare(
          "SELECT * FROM native_sessions WHERE id=? AND conversation_id=?",
        )
        .get(id, conversationId) as Row | undefined;
    const owned = (id: string, handle: string) => {
      const claim = this.claims.get(id);
      if (
        !claim ||
        claim.runId !== runId ||
        claim.handle !== handle ||
        claim.conversationId !== conversationId
      )
        throw Error("SessionOwnershipLostError");
    };
    const snapshot = (r: Row) => ({
      header: JSON.parse(r.header),
      revision: String(r.event_count),
      eventCount: r.event_count,
      inheritedEventCount: 0,
    });
    return (operation: string, data: Row = {}): any => {
      const id = data.id ?? data.header?.id;
      if (operation === "list") {
        return (
          this.db
            .prepare("SELECT * FROM native_sessions WHERE conversation_id=?")
            .all(conversationId) as Row[]
        ).map(snapshot);
      }
      if (
        typeof id !== "string" ||
        id.length > 160 ||
        !/^[a-zA-Z0-9_-]+$/.test(id)
      )
        throw Error("Invalid native session id");
      if (operation === "stat") {
        const r = row(id);
        return r ? snapshot(r) : null;
      }
      if (operation === "claim") {
        if (
          typeof data.handle !== "string" ||
          !data.handle ||
          data.handle.length > 160
        )
          throw Error("Invalid handle");
        const existing = this.db
          .prepare("SELECT conversation_id FROM native_sessions WHERE id=?")
          .get(id) as Row | undefined;
        if (existing && existing.conversation_id !== conversationId)
          throw Error("SessionPersistenceNotFoundError");
        const nativeId = this.nativeId(conversationId);
        if (nativeId && nativeId !== id)
          throw Error("SessionPersistenceNotFoundError");
        const claim = this.claims.get(id);
        if (claim && (claim.runId !== runId || claim.handle !== data.handle))
          throw Error("SessionAlreadyOwnedError");
        this.claims.set(id, { runId, handle: data.handle, conversationId });
        return {};
      }
      if (operation === "release") {
        owned(id, data.handle);
        this.claims.delete(id);
        return {};
      }
      if (operation === "create") {
        owned(id, data.handle);
        const header = materializeCreateHeader(data.header);
        if (
          header.isSeeded ||
          header.parentSession ||
          (data.inherited ?? 0) !== 0
        )
          throw Error("Child sessions are disabled");
        if (
          this.nativeId(conversationId) ||
          this.db.prepare("SELECT id FROM native_sessions WHERE id=?").get(id)
        )
          throw Error("SessionAlreadyExistsError");
        this.db
          .prepare(
            "INSERT INTO native_sessions(id,conversation_id,header) VALUES (?,?,?)",
          )
          .run(id, conversationId, JSON.stringify(header));
        return {};
      }
      const current = row(id);
      if (!current) throw Error("SessionPersistenceNotFoundError");
      if (operation === "read") {
        const offset = data.offset ?? 0,
          length = data.length ?? Number.MAX_SAFE_INTEGER;
        if (![offset, length].every((n) => Number.isSafeInteger(n) && n >= 0))
          throw Error("Invalid event range");
        return (
          this.db
            .prepare(
              "SELECT event FROM native_events WHERE session_id=? AND seq>=? ORDER BY seq LIMIT ?",
            )
            .all(id, offset, length) as Row[]
        ).map((r) => JSON.parse(r.event));
      }
      if (operation === "flush") return {};
      if (operation !== "append") throw Error("Unknown persistence operation");
      owned(id, data.handle);
      if (
        typeof data.requestId !== "string" ||
        !data.requestId ||
        data.requestId.length > 160
      )
        throw Error("Invalid append request");
      const events = materializeAppendBatch(data.events);
      validateStoredEvents(JSON.parse(current.header), [...events]);
      const digest = createHash("sha256")
        .update(JSON.stringify(events))
        .digest("hex");
      this.db.exec("BEGIN IMMEDIATE");
      try {
        const prior = this.db
          .prepare(
            "SELECT digest FROM native_batches WHERE session_id=? AND request_id=?",
          )
          .get(id, data.requestId) as Row | undefined;
        if (prior) {
          if (prior.digest !== digest) throw Error("Append request conflict");
          this.db.exec("COMMIT");
          return {};
        }
        assertContiguous(id as any, events, Number(current.event_count));
        const insert = this.db.prepare(
          "INSERT INTO native_events(session_id,seq,event) VALUES (?,?,?)",
        );
        for (const event of events)
          insert.run(id, event.seq, JSON.stringify(event));
        this.db
          .prepare(
            "UPDATE native_sessions SET event_count=event_count+? WHERE id=?",
          )
          .run(events.length, id);
        this.db
          .prepare("INSERT INTO native_batches VALUES (?,?,?)")
          .run(id, data.requestId, digest);
        this.db.exec("COMMIT");
      } catch (error) {
        this.db.exec("ROLLBACK");
        throw error;
      }
      onEvents?.(events as unknown as Row[]);
      return {};
    };
  }
}
