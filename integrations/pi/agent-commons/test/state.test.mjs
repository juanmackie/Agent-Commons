import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { loadState, saveState } from "../src/state.mjs";

const peerContext = {
  sender: "hermes",
  thread_id: "550e8400-e29b-41d4-a716-446655440001",
  seq: 12,
  client_msg_id: "550e8400-e29b-41d4-a716-446655440002",
  hop: 2,
  complete: false,
};
const defaults = { cursor: 0, seen: [], peerContext: null, pendingInbox: [], outbox: [], closedThreads: [], fingerprint: null, sessionId: null };

test("peer provenance and cursor survive a Pi session restart", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    assert.deepEqual(await loadState(file), defaults);
    const state = { ...defaults, cursor: 12, seen: ["message-12"], peerContext, sessionId: "session-1", fingerprint: "room-a" };
    await saveState(file, state);
    assert.deepEqual(await loadState(file), state);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("pending relay payload and room identity survive restart, and room changes fail closed", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    const message = {
      sender: "hermes", recipient: "pi", thread_id: peerContext.thread_id,
      seq: 13, reply_to: 12, hop: 3, client_msg_id: "550e8400-e29b-41d4-a716-446655440003",
      text: "queued while busy", complete: false, target_session_id: "session-1",
    };
    await saveState(file, { ...defaults, cursor: 13, pendingInbox: [message], fingerprint: "room-a", sessionId: "session-1" });
    assert.deepEqual((await loadState(file, "room-a")).pendingInbox, [message]);
    await assert.rejects(loadState(file, "room-b"), /different room or agent/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("legacy pending messages bind only to their persisted session owner", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    const pending = {
      sender: "hermes", recipient: "pi", thread_id: peerContext.thread_id, seq: 13,
      reply_to: 12, hop: 3, client_msg_id: "550e8400-e29b-41d4-a716-446655440003",
      text: "queued", complete: false,
    };
    await saveState(file, { ...defaults, pendingInbox: [pending] });
    await assert.rejects(loadState(file), /unreadable/);
    await saveState(file, { ...defaults, pendingInbox: [pending], sessionId: "session-1" });
    assert.equal((await loadState(file)).pendingInbox[0].target_session_id, "session-1");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("corrupt peer provenance fails closed rather than dropping the approval gate", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    await saveState(file, { cursor: 12, seen: [], peerContext: { sender: "hermes", thread_id: "not-a-uuid", seq: 12 } });
    await assert.rejects(loadState(file), /state is unreadable/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("state replacement leaves a complete readable record", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    await writeFile(file, JSON.stringify({ ...defaults, cursor: 1 }));
    const next = { ...defaults, cursor: 2, seen: ["message-2"] };
    await saveState(file, next);
    assert.deepEqual(JSON.parse(await readFile(file, "utf8")), next);
    assert.deepEqual(await readdir(directory), ["pi-state.json"]);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
