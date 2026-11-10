import { test } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { NativeStore } from "../src/agent/persistence/native-store.js";

function setup() {
  const db = new DatabaseSync(":memory:");
  db.exec(
    "PRAGMA foreign_keys=ON; CREATE TABLE conversations(id TEXT PRIMARY KEY); INSERT INTO conversations VALUES ('a'),('b');",
  );
  return { db, store: new NativeStore(db) };
}
const header = {
  id: "session-example",
  version: 4,
  createdAt: 1,
  isSeeded: false,
  cwd: "/tmp/work",
};
const event = {
  seq: 0,
  type: "test/extension",
  ignorable: true,
  data: { text: "private A" },
  at: 1,
};

test("native persistence scopes list/read/claim to one conversation", () => {
  const { db, store } = setup();
  const a = store.scope("a", "run-a"),
    b = store.scope("b", "run-b");
  a("claim", { id: header.id, handle: "writer" });
  a("create", { header, handle: "writer", inherited: 0 });
  assert.equal(store.nativeId("a"), header.id);
  assert.equal(store.nativeId("b"), undefined);
  assert.equal(b("list", {}).length, 0);
  assert.equal(b("stat", { id: header.id }), null);
  assert.throws(() => b("read", { id: header.id }), /NotFound/);
  assert.throws(
    () => b("claim", { id: header.id, handle: "attacker" }),
    /NotFound/,
  );
  db.close();
});

test("native appends are contiguous, idempotent and survive a new store instance", () => {
  const { db, store } = setup();
  const a = store.scope("a", "run-a");
  a("claim", { id: header.id, handle: "writer" });
  a("create", { header, handle: "writer", inherited: 0 });
  a("append", {
    id: header.id,
    handle: "writer",
    requestId: "batch-1",
    events: [event],
  });
  a("append", {
    id: header.id,
    handle: "writer",
    requestId: "batch-1",
    events: [event],
  });
  assert.equal(a("read", { id: header.id }).length, 1);
  assert.throws(
    () =>
      a("append", {
        id: header.id,
        handle: "writer",
        requestId: "batch-1",
        events: [{ ...event, data: { text: "changed" } }],
      }),
    /conflict/i,
  );
  assert.throws(
    () =>
      a("append", {
        id: header.id,
        handle: "writer",
        requestId: "batch-2",
        events: [{ ...event, seq: 2 }],
      }),
    /contiguous|seq/i,
  );
  a("release", { id: header.id, handle: "writer" });
  const restarted = new NativeStore(db);
  assert.equal(
    restarted.scope("a", "run-2")("read", { id: header.id })[0].data.text,
    "private A",
  );
  db.close();
});

test("native scope refuses two writers and stale writes after runtime cleanup", () => {
  const { db, store } = setup();
  const a = store.scope("a", "run-a");
  a("claim", { id: header.id, handle: "writer" });
  a("create", { header, handle: "writer", inherited: 0 });
  assert.throws(
    () =>
      store.scope("a", "run-other")("claim", {
        id: header.id,
        handle: "other",
      }),
    /Owned/,
  );
  store.releaseRun("run-a");
  assert.throws(
    () =>
      a("append", {
        id: header.id,
        handle: "writer",
        requestId: "batch",
        events: [event],
      }),
    /Ownership/,
  );
  db.close();
});
