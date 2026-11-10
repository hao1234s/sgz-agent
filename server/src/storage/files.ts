import {
  constants,
  closeSync,
  fchmodSync,
  fstatSync,
  mkdirSync,
  openSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";

// Work through a descriptor and refuse symlinks: never chmod an unrelated target.
function privatePath(path: string, directory: boolean, create = false) {
  let fd: number;
  try {
    fd = openSync(
      path,
      constants.O_NOFOLLOW |
        (directory ? constants.O_RDONLY | constants.O_DIRECTORY : constants.O_RDWR) |
        (create ? constants.O_CREAT : 0),
      0o600,
    );
  } catch (error) {
    if (!create && !directory && (error as NodeJS.ErrnoException).code === "ENOENT")
      return;
    throw error;
  }
  try {
    const stat = fstatSync(fd);
    if (directory ? !stat.isDirectory() : !stat.isFile())
      throw new Error("Invalid storage file type");
    if (process.getuid && stat.uid !== process.getuid())
      throw new Error("Storage must be owned by the service account");
    fchmodSync(fd, directory ? 0o700 : 0o600);
  } finally {
    closeSync(fd);
  }
}

export function prepareStorage(defaultDataDir: string) {
  // SGZ_DATA_DIR designates an application-owned directory, even with an external DB.
  const dataDir = resolve(process.env.SGZ_DATA_DIR ?? defaultDataDir);
  mkdirSync(dataDir, { recursive: true, mode: 0o700 });
  privatePath(dataDir, true);
  const dbPath = resolve(process.env.SGZ_DB_PATH ?? join(dataDir, "sgz.sqlite"));
  // A custom DB parent may be shared: create missing parents, never chmod existing ones.
  mkdirSync(dirname(dbPath), { recursive: true, mode: 0o700 });
  return { dataDir, dbPath };
}

export function privateDatabaseFiles(filename: string, create = false) {
  if (filename === ":memory:") return;
  privatePath(filename, false, create);
  privatePath(`${filename}-wal`, false);
  privatePath(`${filename}-shm`, false);
}
