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
const defaults = { cursor: 0, seen: [], peerContext: null, pendingInbox: [], outbox: [], closedThreads: [], fingerprint: null };

test("cursor survives a Pi restart but peer context is not restored from state", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    assert.deepEqual(await loadState(file), defaults);
    const state = { ...defaults, cursor: 12, seen: ["message-12"], peerContext, sessionId: "session-1", fingerprint: "room-a" };
    await saveState(file, state);
    const loaded = await loadState(file);
    assert.equal(loaded.cursor, 12);
    assert.deepEqual(loaded.seen, ["message-12"]);
    assert.equal(loaded.peerContext, null);
    assert.equal("sessionId" in loaded, false);
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
      text: "queued while busy", complete: false,
    };
    await saveState(file, { ...defaults, cursor: 13, pendingInbox: [message], fingerprint: "room-a" });
    assert.deepEqual((await loadState(file, "room-a")).pendingInbox, [message]);
    await assert.rejects(loadState(file, "room-b"), /different room or agent/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("legacy state keeps queues and cursor while ignoring session-pinned fields", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    const pending = {
      sender: "hermes", recipient: "pi", thread_id: peerContext.thread_id, seq: 13,
      reply_to: 12, hop: 3, client_msg_id: "550e8400-e29b-41d4-a716-446655440003",
      text: "queued", complete: false, target_session_id: "old-session",
    };
    const outgoing = {
      recipient: "hermes", thread_id: peerContext.thread_id, reply_to: 13, hop: 4,
      client_msg_id: "550e8400-e29b-41d4-a716-446655440004", text: "reply",
      complete: false, target_session_id: "old-session",
    };
    await saveState(file, {
      ...defaults, cursor: 5, seen: [pending.client_msg_id], sessionId: "old-session",
      pendingInbox: [pending], outbox: [outgoing], closedThreads: [peerContext.thread_id],
    });
    const state = await loadState(file);
    assert.equal(state.cursor, 5);
    assert.deepEqual(state.seen, [pending.client_msg_id]);
    assert.deepEqual(state.closedThreads, [peerContext.thread_id]);
    assert.deepEqual(state.pendingInbox, [{ ...pending, target_session_id: undefined }].map(({ target_session_id, ...rest }) => rest));
    assert.deepEqual(state.outbox, [{ ...outgoing, target_session_id: undefined }].map(({ target_session_id, ...rest }) => rest));
    assert.equal("sessionId" in state, false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("legacy state without inbox or outbox fields retains empty queues", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    await writeFile(file, JSON.stringify({ cursor: 7, seen: [], fingerprint: "room-a" }));
    const omitted = await loadState(file, "room-a");
    assert.equal(omitted.cursor, 7);
    assert.deepEqual(omitted.pendingInbox, []);
    assert.deepEqual(omitted.outbox, []);

    await writeFile(file, JSON.stringify({ cursor: 8, seen: [], pendingInbox: null, outbox: null }));
    const empty = await loadState(file);
    assert.equal(empty.cursor, 8);
    assert.deepEqual(empty.pendingInbox, []);
    assert.deepEqual(empty.outbox, []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("legacy peer provenance is ignored instead of gating a clean session", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-state-"));
  const file = path.join(directory, "pi-state.json");
  try {
    await saveState(file, { ...defaults, cursor: 12, peerContext: { sender: "hermes", thread_id: "not-a-uuid", seq: 12 } });
    const state = await loadState(file);
    assert.equal(state.cursor, 12);
    assert.equal(state.peerContext, null);
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
