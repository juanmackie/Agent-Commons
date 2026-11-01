import { mkdir, open, readFile, unlink } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";

async function createLock(file, sessionId, token) {
  const handle = await open(file, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify({ pid: process.pid, session_id: sessionId, token }), "utf8");
    await handle.sync();
    return handle;
  } catch (error) {
    await handle.close().catch(() => {});
    await unlink(file).catch(() => {});
    throw error;
  }
}

async function acquireRecoveryGuard(file) {
  const guardFile = `${file}.recovery.lock`;
  const token = randomUUID();
  let handle;
  try {
    handle = await open(guardFile, "wx", 0o600);
  } catch (error) {
    if (error?.code === "EEXIST") {
      throw new Error(`Agent Commons lock recovery is already active or left a stale guard; verify and remove ${guardFile}`);
    }
    throw new Error(`could not claim Agent Commons lock recovery guard ${guardFile}`);
  }
  try {
    await handle.writeFile(JSON.stringify({ pid: process.pid, token }), "utf8");
    await handle.sync();
  } catch (error) {
    await handle.close().catch(() => {});
    await unlink(guardFile).catch(() => {});
    throw error;
  }
  return async function releaseRecoveryGuard() {
    await handle.close();
    try {
      const current = JSON.parse(await readFile(guardFile, "utf8"));
      if (current?.token === token && current?.pid === process.pid) await unlink(guardFile);
    } catch (error) {
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    }
  };
}

export async function acquireOwnerLock(file, sessionId) {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const releaseGuard = await acquireRecoveryGuard(file);
  const token = randomUUID();
  let handle;
  try {
    try {
      handle = await createLock(file, sessionId, token);
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let owner;
      try {
        owner = JSON.parse(await readFile(file, "utf8"));
      } catch (readError) {
        if (readError?.code === "ENOENT") {
          handle = await createLock(file, sessionId, token);
        } else {
          throw new Error(`another Pi process owns the Agent Commons inbox; lock ${file} is unreadable`);
        }
      }
      if (!handle) {
        const pid = owner?.pid;
        let stale = false;
        if (Number.isSafeInteger(pid) && pid > 0 && pid !== process.pid) {
          try {
            process.kill(pid, 0);
          } catch (probeError) {
            stale = probeError?.code === "ESRCH";
            if (!stale && probeError?.code !== "EPERM") {
              throw new Error(`cannot verify Agent Commons lock owner PID ${pid} at ${file}`);
            }
          }
        }
        if (!stale) {
          throw new Error(`another Pi process (PID ${String(pid)}) owns the Agent Commons inbox; lock: ${file}`);
        }
        await unlink(file);
        console.warn(`Recovered stale Agent Commons owner lock for PID ${String(pid)} at ${file}`);
        handle = await createLock(file, sessionId, token);
      }
    }
  } finally {
    await releaseGuard();
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
