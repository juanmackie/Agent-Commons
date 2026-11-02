import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { acquireOwnerLock, OwnerLockHeldError, OwnerLockRecoveryInProgressError } from "../src/owner-lock.mjs";

test("a stale owner PID is recovered and a live owner remains protected", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000 }));
    const release = await acquireOwnerLock(lockFile);
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).pid, process.pid);
    await release();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("live recovery contention is retryable and stale guards fail closed", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-guard-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000 }));
    const guardFile = `${lockFile}.recovery.lock`;
    await writeFile(guardFile, JSON.stringify({ pid: process.pid, token: "recovering" }));
    await assert.rejects(acquireOwnerLock(lockFile), (error) =>
      error instanceof OwnerLockRecoveryInProgressError && error.pid === process.pid);
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).pid, 2_000_000_000);
    assert.equal(JSON.parse(await readFile(guardFile, "utf8")).pid, process.pid);

    await writeFile(guardFile, JSON.stringify({ pid: 2_000_000_000, token: "stale" }));
    await assert.rejects(acquireOwnerLock(lockFile), /recovery guard.*dead PID/);
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).pid, 2_000_000_000);

    await writeFile(guardFile, "{");
    await assert.rejects(acquireOwnerLock(lockFile), /recovery guard.*unreadable/);
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).pid, 2_000_000_000);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("concurrent stale-lock recovery leaves exactly one process as owner", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-race-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000 }));
    const results = await Promise.allSettled([
      acquireOwnerLock(lockFile),
      acquireOwnerLock(lockFile),
    ]);
    const winners = results.filter((result) => result.status === "fulfilled");
    assert.equal(winners.length, 1);
    const owner = JSON.parse(await readFile(lockFile, "utf8"));
    assert.equal(owner.pid, process.pid);
    await winners[0].value();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("a lock held by a different live process remains protected", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  try {
    await new Promise((resolve, reject) => {
      child.once("spawn", resolve);
      child.once("error", reject);
    });
    await writeFile(lockFile, JSON.stringify({ pid: child.pid }));
    await assert.rejects(acquireOwnerLock(lockFile), (error) =>
      error instanceof OwnerLockHeldError && error.pid === child.pid
      && error.message.includes(lockFile));
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).pid, child.pid);
  } finally {
    child.kill();
    await rm(directory, { recursive: true, force: true });
  }
});
