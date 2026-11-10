import { Store } from "./storage/store.js";
import { prepareStorage } from "./storage/files.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

async function main() {
  const [command, username, role = "admin"] = process.argv.slice(2);
  if (
    !["create", "list"].includes(command ?? "") ||
    (command === "create" && (!username || !["user", "admin"].includes(role)))
  )
    throw new Error(
      "Usage: npm run admin -- create USERNAME [admin|user] (password via stdin), or list",
    );
  process.umask(0o077);
  const { dbPath: filename } = prepareStorage(fileURLToPath(new URL("../../.state/server", import.meta.url)));
  const store = new Store(filename, { recoverInterrupted: false });
  try {
    if (command === "list") {
      const rows = store.db
        .prepare(
          "SELECT id,username,role,disabled FROM users ORDER BY username",
        )
        .all();
      process.stdout.write(`${JSON.stringify(rows, null, 2)}\n`);
    } else {
      if (process.stdin.isTTY)
        throw new Error(
          "Provide the password on stdin, for example using a password manager or a hidden shell prompt",
        );
      const password = readFileSync(0, "utf8").replace(/\r?\n$/, "");
      const user = await store.createUser(
        username,
        password,
        role as "admin" | "user",
      );
      process.stdout.write(`${JSON.stringify(user)}\n`);
    }
  } finally {
    store.close();
  }
}
void main().catch((error) => {
  process.stderr.write(`${(error as Error).message}\n`);
  process.exitCode = 1;
});
