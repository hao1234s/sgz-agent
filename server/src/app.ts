import "reflect-metadata";
import { Module, type INestApplication } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import express, {
  type Request,
  type Response,
  type NextFunction,
} from "express";
import { join } from "node:path";
import { isIP } from "node:net";
import type { Store } from "./storage/store.js";
import type { RuntimeAdapter } from "./agent/contracts.js";
import { createAuthRoutes } from "./auth/routes.js";
import { createConversationRoutes } from "./conversations/routes.js";
import { ApiError, fail } from "./http/request.js";

export async function createApplication(options: {
  store: Store;
  runner: RuntimeAdapter;
  publicOrigin?: string;
  trustedProxyCidrs?: string[];
  secureCookies?: boolean;
  maxConcurrentRuns?: number;
  frontendDir?: string;
}): Promise<INestApplication> {
  const { store, runner } = options;
  const secure = options.secureCookies ?? process.env.NODE_ENV === "production";
  const conversations = createConversationRoutes(options);
  const publicOrigin = options.publicOrigin
    ? new URL(options.publicOrigin).origin
    : undefined;
  if (publicOrigin && !/^https?:\/\//.test(publicOrigin))
    throw new Error("publicOrigin must use HTTP or HTTPS");
  const auth = await createAuthRoutes({
    store,
    secureCookies: secure,
    stopUser: conversations.stopUser,
  });
  class ApplicationModule {}
  Module({
    providers: [{ provide: "run-lifecycle", useValue: conversations.lifecycle }],
  })(ApplicationModule);
  const app = await NestFactory.create(ApplicationModule, {
    logger: false,
    bodyParser: false,
  });
  const trustedProxies = options.trustedProxyCidrs ?? [];
  for (const entry of trustedProxies) {
    const [address, prefix, extra] = entry.split("/");
    const version = isIP(address);
    if (!version || extra !== undefined || (prefix !== undefined &&
      (!/^\d+$/.test(prefix) || Number(prefix) < 1 || Number(prefix) > (version === 4 ? 32 : 128))))
      throw new Error("trustedProxyCidrs must contain explicit IP addresses or non-global CIDRs");
  }
  // Express walks forwarded hops only while their immediate peer is trusted.
  app.getHttpAdapter().getInstance().set("trust proxy", trustedProxies.length ? trustedProxies : false);
  const json = express.json({ limit: "128kb", strict: true });
  app.use((req: Request, res: Response, next: NextFunction) => {
    if (req.path === "/health" && req.method === "GET") {
      res.json({ ok: true });
      return;
    }
    next();
  });
  app.use("/api", json, (req: Request, res: Response) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("X-Content-Type-Options", "nosniff");
    void handle(req, res).catch((error) => {
      if (res.headersSent) {
        if (!res.writableEnded) res.end();
        return;
      }
      const status = error instanceof ApiError ? error.status : 500;
      res
        .status(status)
        .json({
          message: status === 500 ? "Internal server error" : error.message,
        });
    });
  });
  app.use(
    "/api",
    (error: unknown, _req: Request, res: Response, _next: NextFunction) => {
      const status =
        (error as { type?: string }).type === "entity.too.large" ? 413 : 400;
      res.status(status).json({ message: "Invalid JSON request body" });
    },
  );

  async function handle(req: Request, res: Response) {
    const path = req.path.replace(/\/$/, "") || "/";
    const method = req.method;
    if (!["GET", "HEAD"].includes(method)) {
      if (req.get("X-SGZ-Request") !== "1")
        return fail(403, "Application request header required");
      const origin = req.get("Origin");
      if (origin) {
        const expected = publicOrigin ?? `${req.protocol}://${req.get("host")}`;
        if (origin !== expected) return fail(403, "Origin is not allowed");
      }
      if (conversations.stopping) return fail(503, "Server is shutting down");
    }
    if (path === "/auth/login" && method === "POST")
      return auth.login(req, res);
    const session = auth.authenticate(req);
    if (path === "/status" && method === "GET") {
      res.json({
        ready: runner.ready && !conversations.stopping,
        model: runner.model,
        execution: "headless",
        capabilities: ["sgz-skills", "combat-calculation"],
      });
      return;
    }
    if (
      (path === "/auth/me" && method === "GET") ||
      (path === "/auth/logout" && method === "POST") ||
      path === "/admin/users" || path.startsWith("/admin/users/")
    ) return auth.handle(req, res, path, method, session);
    return conversations.handle(req, res, path, method, session.user);
  }
  if (options.frontendDir) {
    app.use(
      express.static(options.frontendDir, { index: false, dotfiles: "deny" }),
    );
    app
      .getHttpAdapter()
      .getInstance()
      .get(/^\/(?!api(?:\/|$)|dsh(?:\/|$)).*/, (_req: Request, res: Response) =>
        res.sendFile(join(options.frontendDir!, "index.html")),
      );
  }
  await app.init();
  return app;
}
