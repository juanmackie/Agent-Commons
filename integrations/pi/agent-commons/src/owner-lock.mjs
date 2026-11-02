import { mkdir, open, readFile, unlink } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const RECOVERY_GUARD_MISSING = Symbol("recovery guard missing");

export class OwnerLockHeldError extends Error {
  constructor(pid, file) {
    super(`another Pi process (PID ${pid}) owns the Agent Commons inbox; lock: ${file}`);
    this.name = "OwnerLockHeldError";
    this.pid = pid;
    this.file = file;
  }
}

export class OwnerLockRecoveryInProgressError extends Error {
  constructor(pid, file) {
    super(`another Pi process (PID ${pid}) is checking the Agent Commons owner lock; guard: ${file}`);
    this.name = "OwnerLockRecoveryInProgressError";
    this.pid = pid;
    this.file = file;
  }
}

async function createLock(file, token) {
  const handle = await open(file, "wx", 0o600);
  try {
    await handle.writeFile(JSON.stringify({ pid: process.pid, token }), "utf8");
    await handle.sync();
    return handle;
  } catch (error) {
    await handle.close().catch(() => {});
    await unlink(file).catch(() => {});
    throw error;
  }
}

async function readRecoveryOwner(guardFile) {
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      return JSON.parse(await readFile(guardFile, "utf8"));
    } catch (error) {
      if (error?.code === "ENOENT") return RECOVERY_GUARD_MISSING;
      if (!(error instanceof SyntaxError) || attempt === 3) {
        throw new Error(`Agent Commons recovery guard ${guardFile} is unreadable; verify it before removal`);
      }
      await delay(5);
    }
  }
  throw new Error(`Agent Commons recovery guard ${guardFile} is unreadable; verify it before removal`);
}

async function acquireRecoveryGuard(file) {
  const guardFile = `${file}.recovery.lock`;
  const token = randomUUID();
  let handle;
  for (;;) {
    try {
      handle = await open(guardFile, "wx", 0o600);
      break;
    } catch (error) {
      if (error?.code !== "EEXIST") {
        throw new Error(`could not claim Agent Commons lock recovery guard ${guardFile}`);
      }
      const owner = await readRecoveryOwner(guardFile);
      if (owner === RECOVERY_GUARD_MISSING) continue;
      if (!owner || typeof owner !== "object" || Array.isArray(owner)) {
        throw new Error(`Agent Commons recovery guard ${guardFile} has invalid contents; verify it before removal`);
      }
      const pid = owner.pid;
      if (!Number.isSafeInteger(pid) || pid <= 0) {
        throw new Error(`Agent Commons recovery guard ${guardFile} has no valid PID; verify it before removal`);
      }
      if (pid === process.pid) throw new OwnerLockRecoveryInProgressError(pid, guardFile);
      try {
        process.kill(pid, 0);
        throw new OwnerLockRecoveryInProgressError(pid, guardFile);
      } catch (probeError) {
        if (probeError instanceof OwnerLockRecoveryInProgressError) throw probeError;
        if (probeError?.code === "ESRCH") {
          throw new Error(`Agent Commons recovery guard ${guardFile} belongs to dead PID ${pid}; verify and remove it`);
        }
        if (probeError?.code === "EPERM") throw new OwnerLockRecoveryInProgressError(pid, guardFile);
        throw new Error(`cannot verify Agent Commons recovery guard PID ${pid} at ${guardFile}`);
      }
    }
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

export async function acquireOwnerLock(file) {
  await mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const releaseGuard = await acquireRecoveryGuard(file);
  const token = randomUUID();
  let handle;
  try {
    try {
      handle = await createLock(file, token);
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let owner;
      try {
        owner = JSON.parse(await readFile(file, "utf8"));
      } catch (readError) {
        if (readError?.code === "ENOENT") {
          handle = await createLock(file, token);
        } else {
          throw new Error(`another Pi process owns the Agent Commons inbox; lock ${file} is unreadable`);
        }
      }
      if (!handle) {
        const pid = owner?.pid;
        if (!Number.isSafeInteger(pid) || pid <= 0) {
          throw new Error(`Agent Commons owner lock ${file} has no valid PID`);
        }
        let stale = false;
        if (pid !== process.pid) {
          try {
            process.kill(pid, 0);
          } catch (probeError) {
            stale = probeError?.code === "ESRCH";
            if (!stale && probeError?.code !== "EPERM") {
              throw new Error(`cannot verify Agent Commons lock owner PID ${pid} at ${file}`);
            }
          }
        }
        if (!stale) throw new OwnerLockHeldError(pid, file);
        await unlink(file);
        console.warn(`Recovered stale Agent Commons owner lock for PID ${String(pid)} at ${file}`);
        handle = await createLock(file, token);
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
