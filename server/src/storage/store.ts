import { DatabaseSync } from "node:sqlite";
import { randomUUID } from "node:crypto";
import { hashPassword } from "../auth/credentials.js";
import { privateDatabaseFiles } from "./files.js";

export type Role = "admin" | "user";
export type UserRow = {
  id: string;
  username: string;
  role: Role;
  disabled: number;
  password_hash: string;
  created_at: number;
};
export const publicUser = (user: UserRow) => ({
  id: user.id,
  username: user.username,
  role: user.role,
  disabled: Boolean(user.disabled),
  createdAt: user.created_at,
});

export function validateCredentials(
  username: unknown,
  password: unknown,
): asserts username is string {
  if (typeof username !== "string" || !/^[A-Za-z0-9_.-]{3,64}$/.test(username))
    throw new Error(
      "Username must be 3–64 letters, numbers, dots, hyphens or underscores",
    );
  validatePassword(password);
}
export function validatePassword(
  password: unknown,
): asserts password is string {
  if (
    typeof password !== "string" ||
    password.length < 10 ||
    password.length > 1024
  )
    throw new Error("Password must contain 10–1024 characters");
}

export class Store {
  readonly db: DatabaseSync;
  constructor(
    filename: string,
    options: { recoverInterrupted?: boolean } = {},
  ) {
    privateDatabaseFiles(filename, true);
    this.db = new DatabaseSync(filename);
    this.db
      .exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','user')), disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)), created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
      CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(user_id,updated_at);
      CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, request_id TEXT NOT NULL, status TEXT NOT NULL,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, UNIQUE(conversation_id,request_id)
      );
      CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        content TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,created_at);
    `);
    privateDatabaseFiles(filename);
    if (options.recoverInterrupted !== false)
      this.transaction(() => {
        this.db
          .prepare(
            "UPDATE runs SET status='interrupted',updated_at=? WHERE status='running'",
          )
          .run(Date.now());
        this.db.exec(
          "UPDATE messages SET status='stopped' WHERE status='streaming'; UPDATE conversations SET status='interrupted' WHERE status='running';",
        );
      });
  }
  async createUser(
    username: string,
    password: string,
    role: Role = "user",
    authorizeCommit?: () => void,
  ) {
    validateCredentials(username, password);
    if (role !== "user" && role !== "admin") throw new Error("Invalid role");
    const id = randomUUID();
    const hash = await hashPassword(password);
    // Password hashing yields; an administrator may have been revoked meanwhile.
    authorizeCommit?.();
    this.db
      .prepare(
        "INSERT INTO users(id,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
      )
      .run(id, username, hash, role, Date.now());
    return publicUser(
      this.db.prepare("SELECT * FROM users WHERE id=?").get(id) as UserRow,
    );
  }
  user(id: string) {
    return this.db.prepare("SELECT * FROM users WHERE id=?").get(id) as
      | UserRow
      | undefined;
  }
  conversation(id: string, userId: string) {
    const row = this.db
      .prepare(
        "SELECT id,title,updated_at,status FROM conversations WHERE id=? AND user_id=?",
      )
      .get(id, userId);
    if (!row) return undefined;
    const messages = this.db
      .prepare(
        "SELECT id,role,content,status,error FROM messages WHERE conversation_id=? ORDER BY created_at,rowid",
      )
      .all(id)
      .map((message) => {
        if (message.error === null) delete message.error;
        return message;
      });
    return {
      id: row.id,
      title: row.title,
      updatedAt: row.updated_at,
      status: row.status,
      messages,
    };
  }
  transaction<T>(operation: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const value = operation();
      this.db.exec("COMMIT");
      return value;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
  close() {
    this.db.close();
  }
}
