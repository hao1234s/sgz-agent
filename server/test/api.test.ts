import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { Store } from "../src/storage/store.js";
import { createApplication } from "../src/app.js";
import type { RuntimeAdapter, RunInput } from "../src/agent/contracts.js";

// This deterministic adapter exercises the HTTP lifecycle, not a real model.
class TestRunner implements RuntimeAdapter {
  ready = true;
  model = "test-only";
  calls = 0;
  hold = false;
  exited = 0;
  async run(input: RunInput) {
    this.calls++;
    input.onEvent({ type: "delta", text: "answer" });
    if (this.hold)
      await new Promise<void>((resolve) => {
        if (input.signal.aborted) return resolve();
        input.signal.addEventListener("abort", () => setTimeout(resolve, 30), {
          once: true,
        });
      });
    this.exited++;
    return { text: "answer" };
  }
}

async function fixture(
  options: { maxConcurrentRuns?: number; file?: string; trustedProxyCidrs?: string[] } = {},
) {
  const store = new Store(options.file ?? ":memory:");
  await store.createUser("admin", "admin-password", "admin");
  await store.createUser("alice", "alice-password", "user");
  await store.createUser("bob", "bob-password", "user");
  const runner = new TestRunner();
  const app = await createApplication({
    store,
    runner,
    maxConcurrentRuns: options.maxConcurrentRuns,
    trustedProxyCidrs: options.trustedProxyCidrs,
  });
  await app.listen(0, "127.0.0.1");
  const base = await app.getUrl();
  const request = async (
    path: string,
    cookie = "",
    method = "GET",
    body?: unknown,
    extra: Record<string, string> = {},
  ) =>
    fetch(`${base}/api${path}`, {
      method,
      headers: {
        cookie,
        "X-SGZ-Request": "1",
        "Content-Type": "application/json",
        ...extra,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  const login = async (username: string, password = `${username}-password`) => {
    const response = await request("/auth/login", "", "POST", {
      username,
      password,
    });
    assert.equal(response.status, 200);
    return response.headers.get("set-cookie")!.split(";")[0];
  };
  const create = async (cookie: string) => {
    const response = await request("/conversations", cookie, "POST", {});
    assert.equal(response.status, 201);
    return (await response.json()).conversation.id as string;
  };
  return {
    app,
    store,
    runner,
    request,
    login,
    create,
    close: async () => {
      await app.close();
      store.close();
    },
  };
}

test("real HTTP authentication, CSRF, ownership and admin account controls", async () => {
  const f = await fixture();
  try {
    assert.equal((await f.request("/auth/me")).status, 401);
    const alice = await f.login("alice");
    const bob = await f.login("bob");
    const admin = await f.login("admin");
    const id = await f.create(alice);
    assert.equal((await f.request(`/conversations/${id}`, bob)).status, 404);
    assert.equal((await f.request(`/conversations/${id}`, admin)).status, 404);
    for (const [suffix, method, body] of [
      ["", "DELETE", undefined],
      ["/cancel", "POST", {}],
      ["/messages", "POST", { text: "x", requestId: "r1" }],
    ] as const)
      assert.equal(
        (await f.request(`/conversations/${id}${suffix}`, bob, method, body))
          .status,
        404,
      );
    assert.equal((await f.request("/admin/users", alice)).status, 403);
    assert.equal(
      (
        await f.request(
          "/conversations",
          alice,
          "POST",
          {},
          { "X-SGZ-Request": "" },
        )
      ).status,
      403,
    );
    assert.equal(
      (
        await f.request(
          "/conversations",
          alice,
          "POST",
          {},
          {
            Origin: "https://evil.invalid",
            "X-Forwarded-Host": "evil.invalid",
          },
        )
      ).status,
      403,
    );
    assert.equal(
      (await f.request("/conversations", alice, "POST", { userId: "other" }))
        .status,
      400,
    );
    const users = (await (await f.request("/admin/users", admin)).json()).users;
    assert.equal(users.length, 3);
    assert.equal("password_hash" in users[0], false);
    const aliceId = users.find(
      (u: { username: string }) => u.username === "alice",
    ).id;
    assert.equal(
      (
        await f.request(`/admin/users/${aliceId}`, admin, "PATCH", {
          password: "new-password",
        })
      ).status,
      200,
    );
    assert.equal((await f.request("/auth/me", alice)).status, 401);
    const renewed = await f.login("alice", "new-password");
    assert.equal(
      (
        await f.request(`/admin/users/${aliceId}`, admin, "PATCH", {
          disabled: true,
        })
      ).status,
      200,
    );
    assert.equal((await f.request("/auth/me", renewed)).status, 401);
    const adminId = users.find(
      (u: { username: string }) => u.username === "admin",
    ).id;
    assert.equal(
      (
        await f.request(`/admin/users/${adminId}`, admin, "PATCH", {
          disabled: true,
        })
      ).status,
      409,
    );
    assert.equal(
      (await f.request("/auth/logout", bob, "POST", {})).status,
      204,
    );
    assert.equal((await f.request("/auth/me", bob)).status, 401);
  } finally {
    await f.close();
  }
});

test("SSE final conversation, durable request idempotency and unconfigured runtime", async () => {
  const f = await fixture();
  try {
    const alice = await f.login("alice");
    const id = await f.create(alice);
    const response = await f.request(
      `/conversations/${id}/messages`,
      alice,
      "POST",
      { text: "hi", requestId: "request-1" },
    );
    assert.equal(response.status, 200);
    const events = (await response.text())
      .split("\n\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line.slice(6)));
    assert.equal(events[0].type, "run");
    assert.equal(events.at(-1).type, "done");
    assert.equal(events.at(-1).conversation.messages[1].content, "answer");
    assert.equal(
      (
        await f.request(`/conversations/${id}/messages`, alice, "POST", {
          text: "hi",
          requestId: "request-1",
        })
      ).status,
      409,
    );
    assert.equal(f.runner.calls, 1);
    f.runner.ready = false;
    assert.equal(
      (
        await f.request(`/conversations/${id}/messages`, alice, "POST", {
          text: "hi",
          requestId: "request-2",
        })
      ).status,
      503,
    );
    assert.equal(
      (await f.request(`/conversations/${id}`, alice, "DELETE")).status,
      204,
    );
  } finally {
    await f.close();
  }
});

test("per-user/global capacity, cancellation and disconnect await runner termination", async () => {
  const f = await fixture({ maxConcurrentRuns: 1 });
  f.runner.hold = true;
  try {
    const alice = await f.login("alice");
    const bob = await f.login("bob");
    const id = await f.create(alice);
    const second = await f.create(alice);
    const other = await f.create(bob);
    const stream = await f.request(
      `/conversations/${id}/messages`,
      alice,
      "POST",
      { text: "hi", requestId: "run-1" },
    );
    assert.equal(
      (
        await f.request(`/conversations/${second}/messages`, alice, "POST", {
          text: "hi",
          requestId: "run-2",
        })
      ).status,
      409,
    );
    assert.equal(
      (
        await f.request(`/conversations/${other}/messages`, bob, "POST", {
          text: "hi",
          requestId: "run-3",
        })
      ).status,
      409,
    );
    assert.equal(
      (await f.request(`/conversations/${id}`, alice, "DELETE")).status,
      409,
    );
    const cancel = await f.request(
      `/conversations/${id}/cancel`,
      alice,
      "POST",
      {},
    );
    assert.deepEqual(await cancel.json(), { cancelled: true });
    assert.equal(f.runner.exited, 1);
    await stream.text();
    const next = await f.request(
      `/conversations/${id}/messages`,
      alice,
      "POST",
      { text: "again", requestId: "run-4" },
    );
    await next.body!.cancel();
    for (let i = 0; i < 100 && f.runner.exited < 2; i++)
      await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(f.runner.exited, 2);
    const conversation = (
      await (await f.request(`/conversations/${id}`, alice)).json()
    ).conversation;
    assert.equal(conversation.messages.at(-1).status, "stopped");
  } finally {
    await f.close();
  }
});

test("storage restart preserves owned history and marks unfinished runs interrupted", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sgz-api-"));
  const file = join(dir, "db.sqlite");
  try {
    const f = await fixture({ file });
    const cookie = await f.login("alice");
    const id = await f.create(cookie);
    await (
      await f.request(`/conversations/${id}/messages`, cookie, "POST", {
        text: "stored",
        requestId: "durable",
      })
    ).text();
    f.store.db
      .prepare("UPDATE runs SET status='running' WHERE conversation_id=?")
      .run(id);
    f.store.db
      .prepare(
        "UPDATE messages SET status='streaming' WHERE conversation_id=? AND role='assistant'",
      )
      .run(id);
    f.store.db
      .prepare("UPDATE conversations SET status='running' WHERE id=?")
      .run(id);
    await f.close();
    const store = new Store(file);
    const app = await createApplication({ store, runner: new TestRunner() });
    await app.listen(0, "127.0.0.1");
    try {
      const response = await fetch(
        `${await app.getUrl()}/api/conversations/${id}`,
        { headers: { cookie } },
      );
      assert.equal(response.status, 200);
      const restored = (await response.json()).conversation;
      assert.equal(restored.messages[1].content, "answer");
      assert.equal(restored.messages[1].status, "stopped");
      assert.equal(restored.status, "interrupted");
      assert.equal(
        store.db
          .prepare("SELECT status FROM runs WHERE conversation_id=?")
          .get(id)!.status,
        "interrupted",
      );
      const duplicate = await fetch(
        `${await app.getUrl()}/api/conversations/${id}/messages`,
        {
          method: "POST",
          headers: {
            cookie,
            "Content-Type": "application/json",
            "X-SGZ-Request": "1",
          },
          body: JSON.stringify({ text: "x", requestId: "durable" }),
        },
      );
      assert.equal(duplicate.status, 409);
    } finally {
      await app.close();
      store.close();
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("session and password secrets are hashed, expiration enforced and failed login throttled", async () => {
  const f = await fixture();
  try {
    const alice = await f.login("alice");
    const record = f.store.db
      .prepare("SELECT password_hash FROM users WHERE username='alice'")
      .get()!;
    assert.match(record.password_hash as string, /^scrypt:/);
    assert.equal(
      (record.password_hash as string).includes("alice-password"),
      false,
    );
    const session = f.store.db
      .prepare("SELECT token_hash FROM sessions")
      .get()!;
    assert.equal(alice.includes(session.token_hash as string), false);
    f.store.db.prepare("UPDATE sessions SET expires_at=0").run();
    assert.equal((await f.request("/auth/me", alice)).status, 401);
    for (let i = 0; i < 20; i++)
      assert.equal(
        (
          await f.request("/auth/login", "", "POST", {
            username: "alice",
            password: "wrong-password",
          })
        ).status,
        401,
      );
    assert.equal(
      (
        await f.request("/auth/login", "", "POST", {
          username: "alice",
          password: "wrong-password",
        })
      ).status,
      429,
    );
    assert.equal(
      (
        await f.request("/auth/login", "", "POST", {
          username: "missing",
          password: "wrong-password",
        })
      ).status,
      429,
    );
  } finally {
    await f.close();
  }
});

test("shutdown aborts active runner and waits for its exit before returning", async () => {
  const f = await fixture();
  f.runner.hold = true;
  try {
    const cookie = await f.login("alice");
    const id = await f.create(cookie);
    const stream = await f.request(
      `/conversations/${id}/messages`,
      cookie,
      "POST",
      { text: "running", requestId: "shutdown" },
    );
    await f.app.close();
    assert.equal(f.runner.exited, 1);
    assert.equal(
      f.store.db
        .prepare("SELECT status FROM runs WHERE conversation_id=?")
        .get(id)!.status,
      "stopped",
    );
    await stream.text();
  } finally {
    f.store.close();
  }
});

test("disabling an account aborts and awaits its active run", async () => {
  const f = await fixture();
  f.runner.hold = true;
  try {
    const alice = await f.login("alice");
    const admin = await f.login("admin");
    const id = await f.create(alice);
    const stream = await f.request(
      `/conversations/${id}/messages`,
      alice,
      "POST",
      { text: "running", requestId: "disable" },
    );
    const target = f.store.db
      .prepare("SELECT id FROM users WHERE username='alice'")
      .get()!.id;
    assert.equal(
      (
        await f.request(`/admin/users/${target}`, admin, "PATCH", {
          disabled: true,
        })
      ).status,
      200,
    );
    assert.equal(f.runner.exited, 1);
    await stream.text();
    assert.equal((await f.request("/auth/me", alice)).status, 401);
  } finally {
    await f.close();
  }
});

test("successful login to a known account cannot reset failed login throttling", async () => {
  const f = await fixture();
  try {
    for (let i = 0; i < 19; i++)
      assert.equal(
        (
          await f.request("/auth/login", "", "POST", {
            username: "alice",
            password: "wrong-password",
          })
        ).status,
        401,
      );
    await f.login("bob");
    assert.equal(
      (
        await f.request("/auth/login", "", "POST", {
          username: "alice",
          password: "wrong-password",
        })
      ).status,
      401,
    );
    assert.equal(
      (
        await f.request("/auth/login", "", "POST", {
          username: "alice",
          password: "wrong-password",
        })
      ).status,
      429,
    );
  } finally {
    await f.close();
  }
});

test("admin CLI listing does not interrupt live service runs", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sgz-cli-"));
  const filename = join(dir, "db.sqlite");
  const store = new Store(filename);
  try {
    const user = await store.createUser("admin", "admin-password", "admin");
    store.db
      .prepare("INSERT INTO conversations VALUES(?,?,?,?,?,?)")
      .run("conversation", user.id, "live", "running", 1, 1);
    store.db
      .prepare("INSERT INTO runs VALUES(?,?,?,?,?,?,?)")
      .run("run", "conversation", user.id, "request", "running", 1, 1);
    const output = execFileSync(
      process.execPath,
      [
        "--import",
        "tsx",
        fileURLToPath(new URL("../src/admin.ts", import.meta.url)),
        "list",
      ],
      { env: { ...process.env, SGZ_DB_PATH: filename, SGZ_DATA_DIR: join(dir, "data") }, encoding: "utf8" },
    );
    assert.equal(JSON.parse(output)[0].username, "admin");
    assert.equal(
      store.db.prepare("SELECT status FROM runs").get()!.status,
      "running",
    );
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test("revoked administrator cannot finish a password update or account creation already in flight", async () => {
  const f = await fixture();
  try {
    const cookie = await f.login("admin");
    const admin = (
      f.store.db
        .prepare("SELECT id FROM users WHERE username='admin'")
        .get() as any
    ).id;
    const original = f.store.user.bind(f.store);
    let revoke = true;
    f.store.user = (id: string) => {
      const current = original(id);
      if (revoke && id === admin) {
        revoke = false;
        queueMicrotask(() => {
          f.store.db
            .prepare("UPDATE users SET disabled=1 WHERE id=?")
            .run(admin);
          f.store.db.prepare("DELETE FROM sessions WHERE user_id=?").run(admin);
        });
      }
      return current;
    };
    const result = await f.request(`/admin/users/${admin}`, cookie, "PATCH", {
      disabled: false,
      password: "replacement-password",
    });
    assert.equal(result.status, 401);
    assert.equal(original(admin)!.disabled, 1);
    f.store.user = original;
    f.store.db.prepare("UPDATE users SET disabled=0 WHERE id=?").run(admin);
    const again = await f.login("admin");
    const create = f.store.createUser.bind(f.store);
    f.store.createUser = async (...args: Parameters<typeof create>) => {
      f.store.db.prepare("DELETE FROM sessions WHERE user_id=?").run(admin);
      return create(...args);
    };
    assert.equal(
      (
        await f.request("/admin/users", again, "POST", {
          username: "new-user",
          password: "new-user-password",
        })
      ).status,
      401,
    );
    assert.equal(
      f.store.db
        .prepare("SELECT id FROM users WHERE username='new-user'")
        .get(),
      undefined,
    );
  } finally {
    await f.close();
  }
});

for (const [label, trustedProxyCidrs, distinct] of [
  ["trusted loopback proxy", ["127.0.0.1/32"], true],
  ["default untrusted direct peer", undefined, false],
  ["peer outside configured boundary", ["192.0.2.0/24"], false],
] as const) {
  test(`login throttle uses validated client identity: ${label}`, async () => {
    const f = await fixture({ trustedProxyCidrs: trustedProxyCidrs ? [...trustedProxyCidrs] : undefined });
    const attempt = (ip: string) => f.request("/auth/login", "", "POST", {
      username: "alice", password: "wrong-password",
    }, { "X-Forwarded-For": `198.51.100.200, ${ip}`, "X-Real-IP": ip });
    try {
      for (let i = 0; i < 20; i++) assert.equal((await attempt("203.0.113.1")).status, 401);
      assert.equal((await attempt("203.0.113.1")).status, 429);
      // Only the nearest untrusted hop counts; arbitrary leftmost values cannot select a bucket.
      assert.equal((await attempt("203.0.113.2")).status, distinct ? 401 : 429);
      assert.equal((await f.request("/auth/login", "", "POST", {
        username: "alice", password: "wrong-password",
      }, { "X-Forwarded-For": "192.0.2.99, 203.0.113.1" })).status, 429);
    } finally { await f.close(); }
  });
}
