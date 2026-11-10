import type { Request, Response } from "express";
import { randomUUID } from "node:crypto";
import {
  publicUser,
  validateCredentials,
  validatePassword,
  type Store,
  type UserRow,
} from "../storage/store.js";
import {
  cookieToken,
  hashPassword,
  newSessionToken,
  sessionCookie,
  SESSION_AGE_MS,
  tokenHash,
  verifyPassword,
} from "./credentials.js";
import { body, fail, validate } from "../http/request.js";

type AuthenticatedSession = { user: UserRow; token: string };

export async function createAuthRoutes(options: {
  store: Store;
  secureCookies: boolean;
  stopUser: (userId: string) => Promise<void>;
}) {
  const { store, stopUser } = options;
  const db = store.db;
  const secure = options.secureCookies;
  const attempts = new Map<string, { count: number; until: number }>();
  const dummyHash = await hashPassword(randomUUID());

  async function login(req: Request, res: Response) {
    const input = body(req, ["username", "password"]);
    if (
      typeof input.username !== "string" ||
      input.username.length > 64 ||
      typeof input.password !== "string" ||
      !input.password ||
      input.password.length > 1024
    )
      return fail(400, "Username and password required");
    const now = Date.now();
    const key = req.ip ?? req.socket.remoteAddress ?? "unknown";
    let attempt = attempts.get(key);
    if (!attempt || attempt.until <= now) {
      if (attempts.size >= 10_000) {
        for (const [k, value] of attempts)
          if (value.until <= now) attempts.delete(k);
        if (!attempts.has(key) && attempts.size >= 10_000)
          return fail(429, "Too many login attempts");
      }
      attempt = { count: 0, until: now + 15 * 60_000 };
      attempts.set(key, attempt);
    }
    if (++attempt.count > 20) {
      res.setHeader("Retry-After", Math.ceil((attempt.until - now) / 1000));
      return fail(429, "Too many login attempts");
    }
    const user = db
      .prepare("SELECT * FROM users WHERE username=?")
      .get(input.username) as UserRow | undefined;
    const valid = await verifyPassword(
      input.password,
      user?.password_hash ?? dummyHash,
    );
    // Re-read after scrypt so a concurrent disable/reset cannot authenticate stale credentials.
    const current = user ? store.user(user.id) : undefined;
    if (
      !valid ||
      !current ||
      current.disabled ||
      current.password_hash !== user?.password_hash
    )
      return fail(401, "Invalid username or password");
    const token = newSessionToken();
    db.prepare("DELETE FROM sessions WHERE expires_at<=?").run(now);
    db.prepare(
      "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
    ).run(tokenHash(token), current.id, now + SESSION_AGE_MS);
    attempt.count--;
    res.setHeader("Set-Cookie", sessionCookie(token, secure));
    res.json({ user: publicUser(current) });
    return;
  }

  function authenticate(req: Request): AuthenticatedSession {
    const token = cookieToken(req.headers.cookie);
    const user = token
      ? (db
          .prepare(
            "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.disabled=0",
          )
          .get(tokenHash(token), Date.now()) as UserRow | undefined)
      : undefined;
    if (!user) return fail(401, "Authentication required");
    return { user, token: token! };
  }

  async function handle(
    req: Request,
    res: Response,
    path: string,
    method: string,
    { user, token }: AuthenticatedSession,
  ) {
    if (path === "/auth/me" && method === "GET") {
      res.json({ user: publicUser(user) });
      return;
    }
    if (path === "/auth/logout" && method === "POST") {
      body(req, []);
      db.prepare("DELETE FROM sessions WHERE token_hash=?").run(
        tokenHash(token!),
      );
      await stopUser(user.id);
      res.setHeader("Set-Cookie", sessionCookie("", secure, true));
      res.status(204).end();
      return;
    }
    if (path === "/admin/users" || path.startsWith("/admin/users/")) {
      if (user.role !== "admin") return fail(403, "Administrator required");
      const authorizeCommit = () => {
        const current = db
          .prepare(
            "SELECT users.role,users.disabled FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?",
          )
          .get(tokenHash(token!), Date.now());
        if (!current || current.disabled || current.role !== "admin")
          return fail(401, "Administrator session has expired");
      };
      if (path === "/admin/users" && method === "GET") {
        res.json({
          users: (
            db
              .prepare("SELECT * FROM users ORDER BY created_at,id")
              .all() as UserRow[]
          ).map(publicUser),
        });
        return;
      }
      if (path === "/admin/users" && method === "POST") {
        const input = body(req, ["username", "password", "role"]);
        validate(() => validateCredentials(input.username, input.password));
        if (
          input.role !== undefined &&
          input.role !== "admin" &&
          input.role !== "user"
        )
          return fail(400, "Invalid role");
        try {
          const created = await store.createUser(
            input.username as string,
            input.password as string,
            input.role as "admin" | "user" | undefined,
            authorizeCommit,
          );
          res.status(201).json({ user: created });
          return;
        } catch (error) {
          if (String(error).includes("UNIQUE"))
            return fail(409, "Username already exists");
          throw error;
        }
      }
      const match = /^\/admin\/users\/([^/]+)$/.exec(path);
      if (match && method === "PATCH") {
        const input = body(req, ["disabled", "password"]);
        if (
          !Object.keys(input).length ||
          (input.disabled !== undefined && typeof input.disabled !== "boolean")
        )
          return fail(400, "Invalid account update");
        if (input.password !== undefined)
          validate(() => validatePassword(input.password));
        const target = store.user(match[1]);
        if (!target) return fail(404, "User not found");
        const hash =
          input.password === undefined
            ? undefined
            : await hashPassword(input.password as string);
        store.transaction(() => {
          authorizeCommit();
          const current = store.user(target.id)!;
          if (
            input.disabled === true &&
            current.role === "admin" &&
            (current.id === user.id ||
              Number(
                db
                  .prepare(
                    "SELECT count(*) AS n FROM users WHERE role='admin' AND disabled=0",
                  )
                  .get()!.n,
              ) <= 1)
          )
            return fail(409, "Cannot disable the active or last administrator");
          if (input.disabled !== undefined)
            db.prepare("UPDATE users SET disabled=? WHERE id=?").run(
              input.disabled ? 1 : 0,
              target.id,
            );
          if (hash)
            db.prepare("UPDATE users SET password_hash=? WHERE id=?").run(
              hash,
              target.id,
            );
          if (input.disabled === true || hash)
            db.prepare("DELETE FROM sessions WHERE user_id=?").run(target.id);
        });
        if (input.disabled === true || hash) await stopUser(target.id);
        res.json({ user: publicUser(store.user(target.id)!) });
        return;
      }
      return fail(404, "Not found");
    }
    return fail(404, "Not found");
  }

  return { login, authenticate, handle };
}
