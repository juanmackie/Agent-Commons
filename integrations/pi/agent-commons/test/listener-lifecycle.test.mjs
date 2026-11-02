import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fsPromises from "node:fs/promises";
import { access, mkdtemp, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { registerHooks, syncBuiltinESMExports } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { acquireOwnerLock } from "../src/owner-lock.mjs";

const typeboxStub = "data:text/javascript," + encodeURIComponent(
  "export const Type = { String: x => x, Optional: x => x, Union: x => x, Integer: x => x, Null: () => ({}), Boolean: x => x, Object: x => x };",
);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@earendil-works/pi-ai") return { url: typeboxStub, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});
const { default: registerAgentCommons } = await import("../extensions/agent-commons.ts");

const THREAD = "550e8400-e29b-41d4-a716-446655440001";
const CLIENT_ID = "550e8400-e29b-41d4-a716-446655440002";
const ENV_KEYS = ["ROOM_API", "ROOM_PROJECT", "AGENT_ID", "AGENT_COMMONS_STATE", "PI_SUBAGENT_CHILD"];

function makeHarness(branch = []) {
  const tools = new Map();
  const events = new Map();
  const statuses = [];
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    on(name, handler) { events.set(name, handler); },
    getAllTools() { return [...tools.values()]; },
    sendMessage() {},
    sendUserMessage() {},
  };
  const ctx = {
    hasUI: true,
    sessionManager: {
      getSessionId: () => "session-1",
      getBranch: () => branch,
    },
    isIdle: () => true,
    ui: { setStatus: (_key, value) => statuses.push(value) },
  };
  return { ctx, events, pi, statuses, tools };
}

function saveEnv() {
  return Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]));
}

function restoreEnv(env) {
  for (const [key, value] of Object.entries(env)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

function configure(env, statePath) {
  Object.assign(process.env, {
    ROOM_API: "https://relay.example.test",
    ROOM_PROJECT: "demo",
    AGENT_ID: "pi",
    AGENT_COMMONS_STATE: statePath,
  });
  delete process.env.PI_SUBAGENT_CHILD;
}

test("no-UI and subagent processes stay dormant and never take the listener lock", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-dormant-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  configure(oldEnv, statePath);
  try {
    const noUi = makeHarness();
    noUi.ctx.hasUI = false;
    registerAgentCommons(noUi.pi);
    await noUi.events.get("session_start")({}, noUi.ctx);
    await assert.rejects(access(lockPath), (error) => error.code === "ENOENT");
    await noUi.events.get("session_shutdown")?.();

    process.env.PI_SUBAGENT_CHILD = "1";
    const child = makeHarness();
    registerAgentCommons(child.pi);
    await child.events.get("session_start")({}, child.ctx);
    await assert.rejects(access(lockPath), (error) => error.code === "ENOENT");
    await child.events.get("session_shutdown")?.();
  } finally {
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("passive send bypasses durable outbox and listener takes over after owner exits", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-takeover-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const requests = [];
  const timers = [];
  let releaseOwner;
  configure(oldEnv, statePath);
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: new URL(url), method: options.method ?? "GET" });
    if (options.method === "POST") {
      return Response.json({ seq: 20, recipient_joined: true, recipient_last_seen: null }, { status: 201 });
    }
    return Response.json({ messages: [], next_after: 0 });
  };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};

  const harness = makeHarness();
  try {
    releaseOwner = await acquireOwnerLock(lockPath);
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    assert.match(harness.statuses.at(-1), /PID .* owns the listener/);

    const sent = await harness.tools.get("agent_commons_send").execute("passive-send", {
      recipient: "hermes", thread_id: THREAD, reply_to: null, hop: 0,
      text: "message", complete: false, client_msg_id: CLIENT_ID,
    });
    assert.match(sent.content[0].text, /Replies land in the listening window/);
    assert.match(sent.content[0].text, /has never polled/);
    await assert.rejects(access(statePath), (error) => error.code === "ENOENT");

    await releaseOwner();
    releaseOwner = null;
    await assert.rejects(access(lockPath), (error) => error.code === "ENOENT");
    const retry = timers.find((timer) => timer.delay === 30_000);
    assert.ok(retry, "passive window should schedule a 30-second owner retry");
    retry.callback();
    await new Promise((resolve) => oldSetTimeout(resolve, 100));
    for (let index = 0; index < 30 && !requests.some((request) => request.method === "GET"); index++) {
      await new Promise((resolve) => setImmediate(resolve));
    }
    const poll = requests.find((request) => request.method === "GET");
    assert.ok(poll, `no poll request; statuses=${JSON.stringify(harness.statuses)}`);
    assert.equal(poll.url.searchParams.get("listener"), "pi", poll.url.href);
    assert.match(harness.statuses.at(-1), /listening as pi in demo/);
  } finally {
    await harness.events.get("session_shutdown")?.();
    await releaseOwner?.();
    globalThis.fetch = oldFetch;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("former listener cannot overwrite successor state after becoming passive", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-former-owner-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const branch = [];
  const fingerprint = createHash("sha256")
    .update("https://relay.example.test\0demo\0pi")
    .digest("hex");
  const pending = {
    sender: "hermes", recipient: "pi", thread_id: THREAD, seq: 1, reply_to: null,
    hop: 0, client_msg_id: CLIENT_ID, text: "peer message", complete: false,
  };
  let releaseSuccessor;
  configure(oldEnv, statePath);
  await writeFile(statePath, JSON.stringify({
    cursor: 1, seen: [], peerContext: null, pendingInbox: [pending], outbox: [],
    closedThreads: [], fingerprint,
  }));
  globalThis.fetch = async () => Response.json({ messages: [], next_after: 1 });
  const harness = makeHarness(branch);
  try {
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    await harness.events.get("session_shutdown")();

    releaseSuccessor = await acquireOwnerLock(lockPath);
    await writeFile(statePath, JSON.stringify({
      cursor: 10, seen: [], peerContext: null, pendingInbox: [], outbox: [],
      closedThreads: [THREAD], fingerprint,
    }));
    branch.push({
      type: "message",
      message: {
        role: "user",
        content: `[Agent Commons peer delivery client_msg_id=${CLIENT_ID} thread=${THREAD} seq=1 hop=0 complete=false]\nFrom: hermes\npeer message`,
      },
    });

    await harness.events.get("session_start")({}, harness.ctx);
    assert.match(harness.statuses.at(-1), /passive; PID .* owns the listener/);
    await harness.events.get("agent_start")({}, harness.ctx);
    const saved = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(saved.cursor, 10);
    assert.deepEqual(saved.closedThreads, [THREAD]);
    assert.deepEqual(saved.pendingInbox, []);
  } finally {
    await harness.events.get("session_shutdown")?.();
    await releaseSuccessor?.();
    globalThis.fetch = oldFetch;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("simultaneous windows retry while another process owns the recovery guard", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-start-race-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const guardPath = `${lockPath}.recovery.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const timers = [];
  configure(oldEnv, statePath);
  await writeFile(guardPath, JSON.stringify({ pid: process.pid, token: "active-recovery" }));
  globalThis.fetch = async () => Response.json({ messages: [], next_after: 0 });
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};
  const first = makeHarness();
  const second = makeHarness();
  try {
    registerAgentCommons(first.pi);
    registerAgentCommons(second.pi);
    await Promise.all([
      first.events.get("session_start")({}, first.ctx),
      second.events.get("session_start")({}, second.ctx),
    ]);
    assert.match(first.statuses.at(-1), /passive; PID .* checking listener ownership/);
    assert.match(second.statuses.at(-1), /passive; PID .* checking listener ownership/);
    await access(guardPath);

    await unlink(guardPath);
    const retries = timers.filter((timer) => timer.delay === 30_000);
    assert.equal(retries.length, 2);
    retries[0].callback();
    await new Promise((resolve) => oldSetTimeout(resolve, 100));
    retries[1].callback();
    await new Promise((resolve) => oldSetTimeout(resolve, 100));
    const statuses = [first.statuses.at(-1), second.statuses.at(-1)];
    assert.equal(statuses.filter((status) => /listening as pi in demo/.test(status)).length, 1);
    assert.equal(statuses.filter((status) => /passive; PID .* owns the listener/.test(status)).length, 1);
    assert.equal(statuses.some((status) => /listener disabled/.test(status)), false);
  } finally {
    await first.events.get("session_shutdown")?.();
    await second.events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("listener shutdown holds ownership until a delayed send settles", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-drain-send-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const timers = [];
  let resolvePost;
  let announcePost;
  const postStarted = new Promise((resolve) => { announcePost = resolve; });
  let releaseProbe;
  let shutdown;
  configure(oldEnv, statePath);
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === "POST") {
      announcePost();
      return new Promise((resolve) => { resolvePost = resolve; });
    }
    return Response.json({ messages: [], next_after: 0 });
  };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};
  const harness = makeHarness();
  try {
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    const sending = harness.tools.get("agent_commons_send").execute("delayed-send", {
      recipient: "hermes", thread_id: THREAD, reply_to: null, hop: 0,
      text: "handoff", complete: false, client_msg_id: CLIENT_ID,
    });
    await postStarted;
    shutdown = harness.events.get("session_shutdown")();
    await new Promise((resolve) => setImmediate(resolve));
    await assert.rejects(
      acquireOwnerLock(lockPath),
      (error) => error.name === "OwnerLockHeldError" && error.pid === process.pid,
    );

    resolvePost(Response.json({ seq: 4, recipient_joined: true, recipient_last_seen: null }, { status: 201 }));
    const sent = await sending;
    assert.match(sent.content[0].text, /Sent to hermes/);
    await shutdown;
    shutdown = null;

    releaseProbe = await acquireOwnerLock(lockPath);
    const saved = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(saved.cursor, 0);
    assert.equal(saved.outbox[0].client_msg_id, CLIENT_ID);
  } finally {
    resolvePost?.(Response.json({ seq: 4, recipient_joined: true, recipient_last_seen: null }, { status: 201 }));
    await shutdown;
    await releaseProbe?.();
    await harness.events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("a queue committed during shutdown is reported as queued", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-queue-shutdown-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const originalRename = fsPromises.rename;
  let announceRename;
  let releaseRename;
  let shutdown;
  let sending;
  const renameStarted = new Promise((resolve) => { announceRename = resolve; });
  const renameGate = new Promise((resolve) => { releaseRename = resolve; });
  configure(oldEnv, statePath);
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === "POST") throw new Error("send must not start during shutdown");
    return Response.json({ messages: [], next_after: 0 });
  };
  fsPromises.rename = async (source, destination) => {
    if (path.resolve(destination) === path.resolve(statePath)) {
      announceRename();
      await renameGate;
    }
    return originalRename(source, destination);
  };
  syncBuiltinESMExports();
  const harness = makeHarness();
  try {
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    sending = harness.tools.get("agent_commons_send").execute("queue-during-shutdown", {
      recipient: "hermes", thread_id: THREAD, reply_to: null, hop: 0,
      text: "persist before shutdown", complete: false, client_msg_id: CLIENT_ID,
    });
    await renameStarted;
    shutdown = harness.events.get("session_shutdown")();
    await new Promise((resolve) => setImmediate(resolve));
    await assert.rejects(
      acquireOwnerLock(lockPath),
      (error) => error.name === "OwnerLockHeldError" && error.pid === process.pid,
    );

    releaseRename();
    const result = await sending;
    assert.match(result.content[0].text, /Queued for hermes; the message remains in the durable outbox/);
    assert.deepEqual(result.details, { recipient: "hermes", queued: true });
    await shutdown;
    shutdown = null;
    const saved = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(saved.outbox.length, 1);
    assert.equal(saved.outbox[0].client_msg_id, CLIENT_ID);
  } finally {
    releaseRename();
    await sending?.catch(() => {});
    await shutdown;
    await harness.events.get("session_shutdown")?.();
    fsPromises.rename = originalRename;
    syncBuiltinESMExports();
    globalThis.fetch = oldFetch;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("shutdown during peer-context persistence prevents post-shutdown delivery", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-delivery-shutdown-"));
  const statePath = path.join(directory, "state.json");
  const lockPath = `${statePath}.owner.lock`;
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  const originalRename = fsPromises.rename;
  const message = {
    sender: "hermes", recipient: "pi", thread_id: THREAD, seq: 1, reply_to: null,
    hop: 0, client_msg_id: CLIENT_ID, text: "deliver after persistence", complete: false,
    delivery: "follow_up",
  };
  const deliveries = [];
  let renameCount = 0;
  let announceRename;
  let releaseRename;
  let shutdown;
  const renameStarted = new Promise((resolve) => { announceRename = resolve; });
  const renameGate = new Promise((resolve) => { releaseRename = resolve; });
  configure(oldEnv, statePath);
  globalThis.fetch = async () => Response.json({ messages: [message], next_after: 1 });
  fsPromises.rename = async (source, destination) => {
    if (path.resolve(destination) === path.resolve(statePath) && ++renameCount === 2) {
      announceRename();
      await renameGate;
    }
    return originalRename(source, destination);
  };
  syncBuiltinESMExports();
  const harness = makeHarness();
  harness.pi.sendUserMessage = (...args) => deliveries.push(args);
  try {
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    await renameStarted;
    assert.equal(renameCount, 2);
    shutdown = harness.events.get("session_shutdown")();
    await new Promise((resolve) => setImmediate(resolve));
    await assert.rejects(
      acquireOwnerLock(lockPath),
      (error) => error.name === "OwnerLockHeldError" && error.pid === process.pid,
    );

    releaseRename();
    await shutdown;
    shutdown = null;
    assert.deepEqual(deliveries, []);
    const saved = JSON.parse(await readFile(statePath, "utf8"));
    assert.equal(saved.cursor, 1);
    assert.deepEqual(saved.pendingInbox.map((item) => item.client_msg_id), [CLIENT_ID]);
  } finally {
    releaseRename();
    await shutdown;
    await harness.events.get("session_shutdown")?.();
    fsPromises.rename = originalRename;
    syncBuiltinESMExports();
    globalThis.fetch = oldFetch;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("unreadable transcript keeps peer actions behind the human gate without breaking turns", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-unreadable-transcript-"));
  const statePath = path.join(directory, "state.json");
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  configure(statePath);
  globalThis.fetch = async () => Response.json({ messages: [], next_after: 0 });
  let transcriptUnavailable = false;
  const tools = new Map();
  const events = new Map();
  const approvals = [];
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    on(name, handler) { events.set(name, handler); },
    getAllTools() { return [{ name: "bash", annotations: undefined }]; },
    sendMessage() {},
    sendUserMessage() {},
  };
  const ctx = {
    hasUI: true,
    sessionManager: {
      getSessionId: () => "session-1",
      getBranch() {
        if (transcriptUnavailable) throw new Error("transcript is unavailable");
        return [];
      },
    },
    isIdle: () => true,
    ui: { setStatus() {}, async confirm(_title, details) { approvals.push(details); return false; } },
  };
  try {
    registerAgentCommons(pi);
    await events.get("session_start")({}, ctx);
    transcriptUnavailable = true;
    await events.get("agent_start")({}, ctx);
    await events.get("before_agent_start")({ messageType: "user", prompt: "ordinary turn" }, ctx);
    const decision = await events.get("tool_call")(
      { toolName: "bash", input: { command: "rm -rf build" } }, ctx,
    );
    assert.equal(decision.block, true);
    assert.match(decision.reason, /not approved by the human/);
    assert.match(approvals[0], /rm -rf build/);
  } finally {
    await events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});

test("clean window ignores peerContext stored by an older session", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-clean-window-"));
  const statePath = path.join(directory, "state.json");
  const oldEnv = saveEnv();
  const oldFetch = globalThis.fetch;
  configure(oldEnv, statePath);
  const fingerprint = createHash("sha256")
    .update("https://relay.example.test\0demo\0pi").digest("hex");
  await writeFile(statePath, JSON.stringify({
    cursor: 5,
    seen: [],
    peerContext: {
      sender: "hermes", thread_id: THREAD, seq: 12,
      client_msg_id: CLIENT_ID, hop: 0, complete: false,
    },
    pendingInbox: [],
    outbox: [],
    closedThreads: [],
    fingerprint,
    sessionId: "old-session",
  }));
  globalThis.fetch = async () => Response.json({ messages: [], next_after: 5 });
  const harness = makeHarness();
  try {
    registerAgentCommons(harness.pi);
    await harness.events.get("session_start")({}, harness.ctx);
    const decision = await harness.events.get("tool_call")(
      { toolName: "bash", input: { command: "echo safe" } }, harness.ctx,
    );
    assert.equal(decision, undefined);
    assert.match(harness.statuses.at(-1), /listening as pi in demo/);
  } finally {
    await harness.events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    restoreEnv(oldEnv);
    await rm(directory, { recursive: true, force: true });
  }
});
