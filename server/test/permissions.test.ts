import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, existsSync, mkdtempSync, mkdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync, spawn } from "node:child_process";
import { DatabaseSync } from "node:sqlite";
import { Store } from "../src/storage/store.js";

const mode = (p: string) => statSync(p).mode & 0o777;
test("Store creates private DB/sidecars and tightens existing owned files under umask 0022", () => {
  const dir = mkdtempSync(join(tmpdir(), "sgz-modes-"));
  const file = join(dir, "shared", "db.sqlite");
  const old = process.umask(0o022);
  mkdirSync(join(dir, "shared"), { mode: 0o755 });
  try {
    const store = new Store(file);
    try {
      for (const path of [file, `${file}-wal`, `${file}-shm`]) {
        assert.equal(mode(path), 0o600, path);
        chmodSync(path, 0o644);
      }
      // Keep the original SQLite handle open so existing sidecars remain present.
      const reopened = new Store(file, { recoverInterrupted: false });
      try { for (const path of [file, `${file}-wal`, `${file}-shm`]) assert.equal(mode(path), 0o600, path); }
      finally { reopened.close(); }
      assert.equal(mode(join(dir, "shared")), 0o755);
    } finally { store.close(); }
  } finally { process.umask(old); rmSync(dir, { recursive: true, force: true }); }
});

for (const entry of ["admin", "main"]) {
  for (const customDb of [false, true]) {
    test(`${entry} bootstrap hardens its data directory and preserves a custom database parent (${customDb})`, async () => {
      const dir = mkdtempSync(join(tmpdir(), "sgz-bootstrap-"));
      const dataDir = join(dir, "data"), sharedDir = join(dir, "shared");
      mkdirSync(dataDir, { mode: 0o755 }); mkdirSync(sharedDir, { mode: 0o755 });
      const file = join(customDb ? sharedDir : dataDir, "sgz.sqlite");
      const previousUmask = process.umask(0o022);
      // Keep permissive existing sidecars alive across the CLI/server process.
      const db = new DatabaseSync(file); db.exec("PRAGMA journal_mode=WAL; CREATE TABLE fixture (id INTEGER);");
      for (const path of [file, `${file}-wal`, `${file}-shm`]) chmodSync(path, 0o644);
      const env = { PATH: process.env.PATH, SGZ_DATA_DIR: dataDir, ...(customDb ? { SGZ_DB_PATH: file } : {}), PORT: "0", HOST: "127.0.0.1" };
      try {
        if (entry === "admin") execFileSync(process.execPath, ["--import", "tsx", "src/admin.ts", "list"], { env, stdio: "pipe" });
        else {
          const child = spawn(process.execPath, ["--import", "tsx", "src/main.ts"], { env, stdio: ["ignore", "pipe", "pipe"] });
          let errors = ""; child.stderr.on("data", b => { errors += b; });
          try {
            await new Promise<void>((resolve, reject) => {
              const timer = setTimeout(() => reject(new Error("bootstrap timeout")), 10000);
              child.stdout.on("data", () => { clearTimeout(timer); resolve(); });
              child.once("exit", code => { clearTimeout(timer); reject(new Error(`exit ${code}: ${errors}`)); });
            });
            for (const sidecar of [`${file}-wal`, `${file}-shm`]) if (existsSync(sidecar)) assert.equal(mode(sidecar), 0o600);
          } finally {
            if (child.exitCode === null && child.signalCode === null) {
              const done = new Promise(resolve => child.once("exit", resolve)); child.kill("SIGTERM"); await done;
            }
          }
        }
        assert.equal(mode(dataDir), 0o700);
        for (const path of [file, `${file}-wal`, `${file}-shm`]) assert.equal(mode(path), 0o600, path);
        assert.equal(mode(sharedDir), 0o755);
      } finally { db.close(); process.umask(previousUmask); rmSync(dir, { recursive: true, force: true }); }
    });
  }
}
