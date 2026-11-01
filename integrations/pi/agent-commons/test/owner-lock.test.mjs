import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { test } from "node:test";
import { acquireOwnerLock } from "../src/owner-lock.mjs";

test("a stale owner PID is recovered and a live owner remains protected", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000, session_id: "dead" }));
    const release = await acquireOwnerLock(lockFile, "recovered");
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).session_id, "recovered");
    await release();
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("a stale recovery guard fails closed without removing the owner lock", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-guard-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000, session_id: "dead" }));
    await writeFile(`${lockFile}.recovery.lock`, JSON.stringify({ pid: process.pid, token: "recovering" }));
    await assert.rejects(acquireOwnerLock(lockFile, "session-2"), /recovery.*stale guard/);
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).session_id, "dead");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("concurrent stale-lock recovery leaves exactly one process as owner", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-owner-race-"));
  const lockFile = path.join(directory, "pi-state.json.owner.lock");
  try {
    await writeFile(lockFile, JSON.stringify({ pid: 2_000_000_000, session_id: "dead" }));
    const results = await Promise.allSettled([
      acquireOwnerLock(lockFile, "first"),
      acquireOwnerLock(lockFile, "second"),
    ]);
    const winners = results.filter((result) => result.status === "fulfilled");
    assert.equal(winners.length, 1);
    const owner = JSON.parse(await readFile(lockFile, "utf8"));
    assert.equal(owner.session_id, results[0].status === "fulfilled" ? "first" : "second");
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
    await writeFile(lockFile, JSON.stringify({ pid: child.pid, session_id: "live" }));
    await assert.rejects(acquireOwnerLock(lockFile, "session-2"), (error) =>
      error.message.includes(`PID ${child.pid}`) && error.message.includes(lockFile));
    assert.equal(JSON.parse(await readFile(lockFile, "utf8")).session_id, "live");
  } finally {
    child.kill();
    await rm(directory, { recursive: true, force: true });
  }
});
