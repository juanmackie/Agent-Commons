import { mkdir, open, readFile, unlink } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";

export async function acquireOwnerLock(file, sessionId) {
  const token = randomUUID();
  let handle;
  try {
    await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
    handle = await open(file, "wx", 0o600);
    await handle.writeFile(JSON.stringify({ pid: process.pid, session_id: sessionId, token }), "utf8");
    await handle.sync();
  } catch (error) {
    await handle?.close().catch(() => {});
    if (handle) await unlink(file).catch(() => {});
    if (error?.code === "EEXIST") {
      throw new Error("another Pi process owns the Agent Commons inbox; stop it or verify and remove a stale owner lock");
    }
    throw new Error("could not claim the Agent Commons session owner lock");
  }

  let released = false;
  return async function releaseOwnerLock() {
    if (released) return;
    released = true;
    await handle.close();
    try {
      const current = JSON.parse(await readFile(file, "utf8"));
      if (current?.token === token && current?.pid === process.pid) await unlink(file);
    } catch (error) {
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    }
  };
}
