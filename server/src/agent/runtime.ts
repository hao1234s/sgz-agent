import { createServer } from "node:http";
import { spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";
import type { DatabaseSync } from "node:sqlite";
import type { RunInput, RuntimeAdapter } from "./contracts.js";
import { NativeStore } from "./persistence/native-store.js";

export interface RunnerOptions {
  rootDir: string;
  dataDir: string;
  db: DatabaseSync;
  model: { baseUrl: string; apiKey: string; name: string };
  pythonPath?: string;
  timeoutMs?: number;
}
const disabled = [
  "tool-plugin-manager",
  "plugin-manager",
  "config-editor",
  "settings",
  "agent-instructions",
  "session-title-llm",
  "session-persistence-jsonl",
  "session-projection-cache",
  "tool-bash",
  "tool-pwsh",
  "tool-fs",
  "tool-fs-search",
  "tool-web",
  "tool-subagent",
  "tool-subagent-fork",
  "tool-subagent-control",
  "tool-subagent-list-agents",
  "subagent",
  "subagent-spawn-in-process",
  "subagent-fork-in-process",
  "tool-jobs",
  "tool-workflow",
  "workflow-ptc",
  "ptc-runtime",
  "tool-todo",
  "tool-goal",
  "tool-working-directory",
  "tool-skill",
  "commands",
  "command-feedback",
  "command-compact",
  "goal",
  "goal-round-driver",
  "command-goal",
  "plan-mode",
  "llm-deepseek",
  "llm-deepseek-account",
];
export class HeadlessRunner implements RuntimeAdapter {
  readonly ready: boolean;
  readonly model: string;
  private native: NativeStore;
  private active = new Map<ChildProcess, Promise<unknown>>();
  private closing = false;
  constructor(private options: RunnerOptions) {
    this.model = options.model.name;
    this.ready = !!(
      options.model.apiKey &&
      options.model.baseUrl &&
      options.model.name
    );
    this.native = new NativeStore(options.db);
    mkdirSync(join(options.dataDir, "runs"), { recursive: true, mode: 0o700 });
  }
  async run(input: RunInput): Promise<{ text: string }> {
    if (!this.ready) throw Error("管理员尚未配置模型服务");
    if (this.closing) throw Error("服务正在关闭");
    input.signal.throwIfAborted();
    const { rootDir, dataDir } = this.options;
    const workspace = join(
      dataDir,
      "workspaces",
      createHash("sha256").update(input.conversationId).digest("hex"),
    );
    mkdirSync(workspace, { recursive: true, mode: 0o700 });
    const directory = mkdtempSync(join(dataDir, "runs", "run-"));
    const secret = randomBytes(32).toString("hex");
    let committed = "",
      terminal: string | undefined;
    const operation = this.native.scope(
      input.conversationId,
      input.runId,
      (events) => {
        for (const event of events) {
          if (event.type === "assistant/message") {
            const text = (event.data?.message?.content ?? [])
              .filter((p: any) => p.type === "text")
              .map((p: any) => p.text)
              .join("");
            if (text) {
              committed = [committed, text].filter(Boolean).join("\n\n");
              input.onEvent({ type: "replace", text: committed });
            }
          }
          if (event.type === "tool/call")
            input.onEvent({
              type: "status",
              text: `正在调用 ${event.data.name}`,
            });
          if (event.type === "turn/end") terminal = event.data?.reason?.kind;
        }
      },
    );
    const bridge = createServer(async (req, res) => {
      if (
        req.method !== "POST" ||
        req.url !== "/persist" ||
        req.headers.authorization !== `Bearer ${secret}`
      ) {
        res.writeHead(403).end();
        return;
      }
      try {
        const chunks: Buffer[] = [];
        let size = 0;
        for await (const chunk of req) {
          size += chunk.length;
          if (size > 16 * 1024 * 1024) {
            res.writeHead(413).end();
            req.destroy();
            return;
          }
          chunks.push(chunk);
        }
        const { operation: method, ...data } = JSON.parse(
          Buffer.concat(chunks).toString(),
        );
        const result = operation(method, data);
        res
          .writeHead(200, { "content-type": "application/json" })
          .end(JSON.stringify(result));
      } catch (error) {
        res.writeHead(400, { "content-type": "application/json" }).end(
          JSON.stringify({
            error: error instanceof Error ? error.message : "Persistence error",
          }),
        );
      }
    });
    bridge.requestTimeout = 35000;
    await new Promise<void>((resolve, reject) => {
      bridge.once("error", reject);
      bridge.listen(0, "127.0.0.1", resolve);
    });
    const url = `http://127.0.0.1:${(bridge.address() as any).port}/persist`;
    let child: ChildProcess | undefined,
      timeout: ReturnType<typeof setTimeout> | undefined,
      killTimer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false,
      stderr = "";
    const terminate = () => {
      if (child && child.exitCode === null && child.signalCode === null) {
        this.kill(child, "SIGTERM");
        killTimer ??= setTimeout(
          () => child && this.kill(child, "SIGKILL"),
          2500,
        );
      }
    };
    try {
      const patch = [
        ...disabled.map((id) => ({ id, disabled: true })),
        {
          id: "system-prompt",
          config: {
            personaPrefix:
              "你是三国志战略版助手。根据证据回答，按需使用三国志技能和计算工具。你无法访问用户游戏客户端或其他用户数据。",
          },
        },
        {
          id: "agent-default-model",
          config: { provider: "sgz-server", model: this.model },
        },
        {
          id: "llm-pi-ai",
          config: {
            providers: {
              "sgz-server": {
                displayName: "SGZ 服务端模型",
                api: "openai-completions",
                baseURL: this.options.model.baseUrl,
                apiKeyEnv: "SGZ_MODEL_KEY",
                models: [
                  {
                    id: this.model,
                    name: this.model,
                    input: ["text"],
                    contextWindow: 128000,
                    maxTokens: 8192,
                    reasoningEfforts: false,
                  },
                ],
              },
            },
          },
        },
        {
          id: "skill-filesystem",
          config: {
            providerName: "sgz-filesystem",
            includeDefaultRoots: false,
            customSkillDirs: [join(rootDir, "server/skills/conversation")],
            watch: false,
            bundledSkillDir: join(directory, "empty"),
            agentsHome: join(directory, "empty"),
            dshHome: join(directory, "empty"),
          },
        },
        {
          insert: [
            {
              id: "sgz-native-persistence",
              name: pathToFileURL(
                join(rootDir, "server/dist/agent/persistence/provider.mjs"),
              ).href,
              config: { url, token: secret },
            },
            {
              id: "sgz-agent",
              name: pathToFileURL(join(rootDir, "server/dist/agent/plugin/index.js"))
                .href,
              config: {
                rootDir,
                capabilities: {
                  analysis: true,
                  knowledge: false,
                  client: false,
                },
                analysis: {
                  pythonPath:
                    this.options.pythonPath ??
                    join(rootDir, ".venv/bin/python"),
                },
              },
            },
          ],
        },
      ];
      mkdirSync(join(directory, "empty"));
      const patchPath = join(directory, "profile.json");
      writeFileSync(patchPath, JSON.stringify(patch), { mode: 0o600 });
      const nativeId = this.native.nativeId(input.conversationId);
      const args = [
        join(rootDir, "server/node_modules/@deepseek-ai/dsh/lib/bin.js"),
        "--profile",
        "headless",
        "--patch",
        patchPath,
        "--json",
        ...(nativeId ? ["--session-id", nativeId] : []),
        "-",
      ];
      child = spawn(process.execPath, args, {
        cwd: workspace,
        detached: process.platform !== "win32",
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          PATH: process.env.PATH ?? "/usr/bin:/bin",
          HOME: directory,
          DSH_HOME: join(directory, "dsh"),
          SGZ_MODEL_KEY: this.options.model.apiKey,
          ...(process.env.NODE_EXTRA_CA_CERTS
            ? { NODE_EXTRA_CA_CERTS: process.env.NODE_EXTRA_CA_CERTS }
            : {}),
        },
      });
      const exited = new Promise<{
        code: number | null;
        signal: NodeJS.Signals | null;
      }>((resolve, reject) => {
        child!.once("error", reject);
        child!.once("close", (code, signal) => resolve({ code, signal }));
      });
      this.active.set(child, exited);
      input.signal.addEventListener("abort", terminate, { once: true });
      timeout = setTimeout(() => {
        timedOut = true;
        terminate();
      }, this.options.timeoutMs ?? 180000);
      if (input.signal.aborted) terminate();
      child.stderr!.on("data", (chunk) => {
        stderr = (stderr + String(chunk)).slice(-10000);
      });
      let finalText: string | undefined;
      const lines = createInterface({ input: child.stdout! });
      lines.on("line", (line) => {
        try {
          const event = JSON.parse(line);
          if (event.type === "final" && typeof event.text === "string")
            finalText = event.text;
          if (event.type === "status" && event.phase === "step_start")
            input.onEvent({ type: "status", text: "正在思考" });
        } catch {
          /* diagnostic output is not a model answer */
        }
      });
      child.stdin!.on("error", () => {});
      child.stdin!.end(input.text);
      const exit = await exited;
      if (input.signal.aborted)
        throw new DOMException("已停止生成", "AbortError");
      if (timedOut) throw Error("模型执行超时，请稍后重试");
      if (
        exit.code !== 0 ||
        terminal !== "completed" ||
        finalText === undefined
      ) {
        mkdirSync(join(dataDir, "logs"), { recursive: true, mode: 0o700 });
        writeFileSync(
          join(
            dataDir,
            "logs",
            `${createHash("sha256").update(input.runId).digest("hex")}.log`,
          ),
          stderr.split(this.options.model.apiKey).join("[REDACTED]"),
          { mode: 0o600 },
        );
        throw Error(
          `DSH 执行未完成（${terminal ?? "startup"} / ${exit.code ?? exit.signal}），请检查服务日志`,
        );
      }
      // The official terminal answer is lossless; incremental JSON projections can be truncated.
      input.onEvent({ type: "replace", text: finalText });
      return { text: finalText };
    } finally {
      clearTimeout(timeout);
      clearTimeout(killTimer);
      input.signal.removeEventListener("abort", terminate);
      if (child) this.active.delete(child);
      this.native.releaseRun(input.runId);
      await new Promise<void>((resolve) => {
        bridge.close(() => resolve());
        bridge.closeIdleConnections();
      });
      rmSync(directory, { recursive: true, force: true });
    }
  }
  private kill(child: ChildProcess, signal: NodeJS.Signals) {
    try {
      if (process.platform !== "win32" && child.pid)
        process.kill(-child.pid, signal);
      else child.kill(signal);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
  async close() {
    this.closing = true;
    for (const child of this.active.keys()) this.kill(child, "SIGTERM");
    const timer = setTimeout(() => {
      for (const child of this.active.keys()) this.kill(child, "SIGKILL");
    }, 2500);
    try {
      await Promise.allSettled([...this.active.values()]);
    } finally {
      clearTimeout(timer);
    }
  }
}
