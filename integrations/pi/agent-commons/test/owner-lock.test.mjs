import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { acquireOwnerLock } from "../src/owner-lock.mjs";

test("only one Pi process can own an inbox until its owner releases the lock", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    const release = await acquireOwnerLock(lockFile, "session-1");
    await assert.rejects(acquireOwnerLock(lockFile, "session-2"), /another Pi process owns/);
    await release();
    const releaseSecond = await acquireOwnerLock(lockFile, "session-2");
    await releaseSecond();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
