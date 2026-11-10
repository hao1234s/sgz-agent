import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Store } from "../src/storage/store.js";
import { createApplication } from "../src/app.js";

test("production app serves login SPA but never exposes a DSH endpoint", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sgz-static-"));
  writeFileSync(join(dir, "index.html"), "<main>SGZ login</main>");
  const store = new Store(":memory:");
  const app = await createApplication({
    store,
    runner: {
      ready: false,
      model: "test",
      run: async () => {
        throw Error("unused");
      },
    },
    frontendDir: dir,
  });
  try {
    await app.listen(0, "127.0.0.1");
    const base = await app.getUrl();
    assert.equal(
      await (await fetch(base + "/")).text(),
      "<main>SGZ login</main>",
    );
    assert.equal((await fetch(base + "/dsh/")).status, 404);
    assert.equal((await fetch(base + "/api/conversations")).status, 401);
  } finally {
    await app.close();
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
