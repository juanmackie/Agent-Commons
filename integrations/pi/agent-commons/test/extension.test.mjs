import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { registerHooks } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

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

test("registered send handler shares its in-flight result with the background poller", async () => {
  const stateDirectory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-poller-race-"));
  const statePath = path.join(stateDirectory, "state.json");
  const oldEnv = Object.fromEntries(["ROOM_API", "ROOM_PROJECT", "AGENT_ID", "AGENT_COMMONS_STATE"].map((key) => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const timers = [];
  const pendingPosts = [];
  let firstPostStarted;
  const postStarted = new Promise((resolve) => { firstPostStarted = resolve; });
  process.env.ROOM_API = "https://relay.example.test";
  process.env.ROOM_PROJECT = "demo";
  process.env.AGENT_ID = "pi";
  process.env.AGENT_COMMONS_STATE = statePath;
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === "POST") {
      return new Promise((resolve) => {
        pendingPosts.push(resolve);
        firstPostStarted();
      });
    }
    return Response.json({ messages: [], next_after: 0 });
  };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};

  const tools = new Map();
  const events = new Map();
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    on(name, handler) { events.set(name, handler); },
    sendMessage() {},
    sendUserMessage() {},
  };
  const ctx = {
    hasUI: true,
    sessionManager: {
      getSessionId: () => "session-1",
      getBranch: () => [],
    },
    isIdle: () => true,
    ui: { setStatus() {} },
  };
  let sendPromise;
  try {
    registerAgentCommons(pi);
    await events.get("session_start")({}, ctx);
    await new Promise((resolve) => setImmediate(resolve));
    sendPromise = tools.get("agent_commons_send").execute("call-1", {
      recipient: "hermes", thread_id: THREAD, reply_to: null, hop: 0,
      text: "message", complete: false, client_msg_id: CLIENT_ID,
    });
    await postStarted;
    const pollTimer = timers.at(-1);
    assert.ok(pollTimer, "initial poll should schedule a background retry");
    pollTimer.callback();
    await new Promise((resolve) => setImmediate(resolve));

    const postCount = pendingPosts.length;
    for (let index = 0; index < postCount; index++) {
      if (postCount > 1 && index === 0) {
        pendingPosts[index](new Response("temporary relay failure", { status: 503 }));
      } else {
        pendingPosts[index](Response.json({ seq: 10, recipient_joined: true }, { status: 201 }));
      }
    }
    const result = await sendPromise;
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(postCount, 1);
    assert.match(result.content[0].text, /Sent to hermes/);
    assert.doesNotMatch(result.content[0].text, /Queued for hermes/);
  } finally {
    for (const resolve of pendingPosts) {
      resolve(Response.json({ seq: 10, recipient_joined: true }, { status: 201 }));
    }
    await events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(stateDirectory, { recursive: true, force: true });
  }
});

test("registered send handler does not report a conflicting queued retry as successful", async () => {
  const stateDirectory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-extension-test-"));
  const statePath = path.join(stateDirectory, "state.json");
  const oldEnv = Object.fromEntries(["ROOM_API", "ROOM_PROJECT", "AGENT_ID", "AGENT_COMMONS_STATE"].map((key) => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const timers = [];
  process.env.ROOM_API = "https://relay.example.test";
  process.env.ROOM_PROJECT = "demo";
  process.env.AGENT_ID = "pi";
  process.env.AGENT_COMMONS_STATE = statePath;
  globalThis.fetch = async () => { throw new TypeError("network unavailable"); };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};

  const tools = new Map();
  const events = new Map();
  const pi = {
    registerTool(tool) { tools.set(tool.name, tool); },
    on(name, handler) { events.set(name, handler); },
    sendMessage() {},
    sendUserMessage() {},
  };
  const ctx = {
    hasUI: true,
    sessionManager: {
      getSessionId: () => "session-1",
      getBranch: () => [],
    },
    isIdle: () => true,
    ui: { setStatus() {} },
  };
  try {
    registerAgentCommons(pi);
    await events.get("session_start")({}, ctx);
    await new Promise((resolve) => setImmediate(resolve));
    const execute = tools.get("agent_commons_send").execute;
    const original = {
      recipient: "hermes", thread_id: THREAD, reply_to: null, hop: 0,
      text: "original", complete: false, client_msg_id: CLIENT_ID,
    };
    const queued = await execute("call-1", original);
    assert.match(queued.content[0].text, /Queued for hermes/);

    const conflict = await execute("call-2", { ...original, text: "changed payload" });
    assert.equal(conflict.isError, true);
    assert.match(conflict.content[0].text, /outbox ID already belongs to a different message/);
    assert.doesNotMatch(conflict.content[0].text, /Queued for hermes/);
  } finally {
    await events.get("session_shutdown")?.();
    globalThis.fetch = oldFetch;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(stateDirectory, { recursive: true, force: true });
  }
});
