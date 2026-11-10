import "reflect-metadata";
import { existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { Store } from "./storage/store.js";
import { prepareStorage } from "./storage/files.js";
import { HeadlessRunner } from "./agent/runtime.js";
import { createApplication } from "./app.js";

const rootDir = resolve(import.meta.dirname, "../..");
process.umask(0o077);
const { dataDir, dbPath } = prepareStorage(join(rootDir, ".state/server"));
const store = new Store(dbPath);
const runner = new HeadlessRunner({
  rootDir,
  dataDir,
  db: store.db,
  model: {
    baseUrl: process.env.SGZ_MODEL_BASE_URL ?? "https://api.deepseek.com/v1",
    apiKey: process.env.SGZ_MODEL_API_KEY ?? process.env.DEEPSEEK_API_KEY ?? "",
    name: process.env.SGZ_MODEL_NAME ?? "deepseek-flash",
  },
  pythonPath: process.env.SGZ_PYTHON_PATH,
});
const app = await createApplication({
  store,
  runner,
  publicOrigin: process.env.SGZ_PUBLIC_ORIGIN,
  secureCookies:
    process.env.SGZ_COOKIE_SECURE === undefined
      ? process.env.NODE_ENV === "production"
      : process.env.SGZ_COOKIE_SECURE === "true",
  trustedProxyCidrs: process.env.SGZ_TRUSTED_PROXY_CIDRS?.split(",").map(value => value.trim()).filter(Boolean),
  maxConcurrentRuns: Number(process.env.SGZ_MAX_RUNS ?? 4),
  frontendDir: existsSync(join(rootDir, "web/dist/index.html"))
    ? join(rootDir, "web/dist")
    : undefined,
});
const port = Number(process.env.PORT ?? 3080),
  host = process.env.HOST ?? "127.0.0.1";
try {
  await app.listen(port, host);
} catch (error) {
  await app.close();
  store.close();
  throw error;
}
console.log(
  `SGZ multi-user API: http://${host}:${port} (headless; model ${runner.ready ? "configured" : "not configured"})`,
);
let shuttingDown = false;
async function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    await app.close();
  } finally {
    store.close();
  }
}
process.on("SIGTERM", () => void shutdown());
process.on("SIGINT", () => void shutdown());
