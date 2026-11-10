import { randomUUID } from "node:crypto";
import {
  SessionPersistence,
  SessionPersistenceRevision,
  SessionAlreadyExistsError,
  SessionAlreadyOwnedError,
  SessionPersistenceNotFoundError,
  SessionOwnershipLostError,
  SessionReadOnlyError,
  SessionHandleClosedError,
  materializeCreateHeader,
  materializeAppendBatch,
  validateStoredEvents,
  assertStoredId,
  assertVersion,
} from "@deepseek-ai/dsh-session-persistence";

/** Official persistence seam backed by a conversation-scoped application capability. */
export default class ApplicationPersistence extends SessionPersistence {
  constructor(ctx, config) {
    super(ctx);
    this.config = config;
    this.writers = new Map();
    this.handles = new Set();
    this.pending = new Map();
    ctx.on("session/event", (session, event) =>
      this.writers.get(session.id)?.enqueue(event),
    );
    ctx.on("session/flush", (session) => this.writers.get(session.id)?.flush());
    ctx.on("session/disposed", (session) =>
      this.writers.get(session.id)?.close(),
    );
    ctx.effect(() => () => this.flushAndClose());
  }
  async rpc(operation, data = {}) {
    const response = await fetch(this.config.url, {
      method: "POST",
      headers: {
        authorization: `Bearer ${this.config.token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ operation, ...data }),
      signal: AbortSignal.timeout(30000),
    });
    const value = await response.json();
    if (!response.ok) {
      const errors = {
        SessionAlreadyExistsError,
        SessionAlreadyOwnedError,
        SessionPersistenceNotFoundError,
        SessionOwnershipLostError,
      };
      if (errors[value.error])
        throw new errors[value.error](data.id ?? data.header?.id);
      throw Error(value.error || "Native persistence failed");
    }
    return value;
  }
  async create(header, options) {
    options?.signal?.throwIfAborted();
    const meta = materializeCreateHeader(header);
    if (meta.isSeeded || (options?.inheritedEventCount ?? 0) !== 0)
      throw Error("Forks are disabled");
    if (await this.stat(meta.id)) throw new SessionAlreadyExistsError(meta.id);
    this.pending.set(meta.id, {
      header: meta,
      eventCount: 0,
      revision: "0",
      inheritedEventCount: 0,
    });
    try {
      return await this.handle(meta, "write", false);
    } catch (error) {
      this.pending.delete(meta.id);
      throw error;
    }
  }
  async handle(header, access, materialized = true) {
    if (access === "write" && this.writers.has(header.id))
      throw new SessionAlreadyOwnedError(header.id);
    const handle = new ApplicationHandle(this, header, access, materialized);
    if (access === "write") {
      await this.rpc("claim", { id: header.id, handle: handle.token });
      this.writers.set(header.id, handle);
    }
    this.handles.add(handle);
    return handle;
  }
  async open(id, access, options) {
    options?.signal?.throwIfAborted();
    if (!["read", "write"].includes(access)) throw TypeError("Invalid access");
    const snapshot = await this.stat(id);
    if (!snapshot) throw new SessionPersistenceNotFoundError(id);
    assertStoredId(id, snapshot.header);
    assertVersion(snapshot.header);
    if (!this.pending.has(id))
      validateStoredEvents(snapshot.header, await this.rpc("read", { id }));
    return this.handle(snapshot.header, access, !this.pending.has(id));
  }
  async stat(id, options) {
    options?.signal?.throwIfAborted();
    const value = this.pending.get(id) ?? (await this.rpc("stat", { id }));
    return value
      ? { ...value, revision: SessionPersistenceRevision(value.revision) }
      : undefined;
  }
  async list(options) {
    options?.signal?.throwIfAborted();
    return [...(await this.rpc("list")), ...this.pending.values()];
  }
  async flush() {
    await Promise.all(
      [...this.writers.values()].map((handle) => handle.flush()),
    );
  }
  async flushAndClose() {
    const results = await Promise.allSettled(
      [...this.handles].map((handle) => handle.close()),
    );
    const failures = results.filter((r) => r.status === "rejected");
    if (failures.length)
      throw new AggregateError(
        failures.map((r) => r.reason),
        "Persistence close failed",
      );
  }
}
class ApplicationHandle {
  constructor(store, header, access, materialized) {
    Object.assign(this, {
      store,
      header: Object.freeze(header),
      id: header.id,
      access,
      materialized,
      inheritedEventCount: 0,
      token: randomUUID(),
      closed: false,
      chain: Promise.resolve(),
      buffer: [],
    });
  }
  check(operation, write = false) {
    if (this.closed || this.closing)
      throw new SessionHandleClosedError(this.id, operation);
    if (write && this.access !== "write")
      throw new SessionReadOnlyError(this.id, operation);
  }
  async materialize() {
    if (this.materialized) return;
    await this.store.rpc("create", {
      header: this.header,
      handle: this.token,
      inherited: 0,
    });
    this.materialized = true;
    this.store.pending.delete(this.id);
  }
  serialize(fn) {
    const task = this.chain.then(fn);
    this.chain = task.catch(() => {});
    return task;
  }
  async read(offset = 0, length = Number.MAX_SAFE_INTEGER, options) {
    this.check("read");
    options?.signal?.throwIfAborted();
    if (![offset, length].every((n) => Number.isSafeInteger(n) && n >= 0))
      throw TypeError("Invalid range");
    await this.chain;
    const events = this.materialized
      ? await this.store.rpc("read", { id: this.id, offset, length })
      : [];
    validateStoredEvents(this.header, events);
    return { eventState: "detached", events };
  }
  async append(events, options) {
    this.check("append", true);
    options?.signal?.throwIfAborted();
    const batch = materializeAppendBatch(events);
    return this.serialize(() => this.persist(batch, randomUUID()));
  }
  async persist(events, requestId) {
    if (!events.length) return;
    await this.materialize();
    await this.store.rpc("append", {
      id: this.id,
      handle: this.token,
      requestId,
      events,
    });
  }
  enqueue(event) {
    this.check("append", true);
    this.buffer.push(...materializeAppendBatch([event]));
    if (!this.timer)
      this.timer = setTimeout(() => {
        this.timer = undefined;
        void this.drain().catch((error) => {
          this.failure = error;
        });
      }, 15);
  }
  drain() {
    clearTimeout(this.timer);
    this.timer = undefined;
    return this.serialize(async () => {
      while (this.retry || this.buffer.length) {
        const item = this.retry ?? {
          events: this.buffer.splice(0),
          id: randomUUID(),
        };
        this.retry = item;
        await this.persist(item.events, item.id);
        this.retry = undefined;
      }
      this.failure = undefined;
    });
  }
  async flush(options) {
    this.check("flush", true);
    options?.signal?.throwIfAborted();
    await this.drain();
    await this.materialize();
    await this.store.rpc("flush", { id: this.id });
  }
  close() {
    if (this.closing) return this.closing;
    this.closing = (async () => {
      try {
        if (this.access === "write") {
          await this.drain();
          if (this.materialized) await this.store.rpc("flush", { id: this.id });
        }
      } finally {
        if (this.access === "write")
          await this.store.rpc("release", { id: this.id, handle: this.token });
        this.closed = true;
        this.store.handles.delete(this);
        this.store.writers.delete(this.id);
        this.store.pending.delete(this.id);
      }
    })();
    return this.closing;
  }
  [Symbol.asyncDispose]() {
    return this.close();
  }
}
