import assert from "node:assert/strict";
import { test } from "node:test";
import { gatePeerToolCall, resolveOutboundEnvelope, shouldGateToolCall } from "../src/permission.mjs";

const event = { toolName: "bash", input: { command: "rm -r build" } };

test("read-only tools do not ask for destructive-action approval", async () => {
  let asked = false;
  const result = await gatePeerToolCall(event, { annotations: { readOnlyHint: true } }, {
    hasUI: true,
    ui: { confirm: async () => { asked = true; return true; } },
  });
  assert.equal(result, undefined);
  assert.equal(asked, false);
});

test("built-in read-only tools run during a peer thread even without annotations", async () => {
  let prompted = false;
  const ctx = { hasUI: true, ui: { confirm: async () => { prompted = true; return true; } } };
  for (const toolName of ["read", "grep", "find", "ls"]) {
    assert.equal(await gatePeerToolCall(
      { toolName, input: {} }, { name: toolName }, ctx,
      { sender: "hermes", thread_id: "550e8400-e29b-41d4-a716-446655440001", seq: 1, hop: 0, complete: false },
    ), undefined);
  }
  assert.equal(prompted, false);
});

test("peer side effects and every outbound relay message require human review", () => {
  assert.equal(shouldGateToolCall("bash", true), true);
  assert.equal(shouldGateToolCall("agent_commons_send", false), true);
  assert.equal(shouldGateToolCall("read", false), false);
});

test("unknown or side-effecting tools require exact human approval", async () => {
  let title;
  let question;
  const result = await gatePeerToolCall(event, { annotations: { readOnlyHint: false } }, {
    hasUI: true,
    ui: { confirm: async (dialogTitle, prompt) => { title = dialogTitle; question = prompt; return true; } },
  });
  assert.equal(result, undefined);
  assert.equal(title, "Peer-requested action");
  assert.match(question, /bash/);
  assert.match(question, /rm -r build/);
  assert.match(question, /target/);
});

test("outbound envelope resolves recipient and preserves the peer thread before approval", () => {
  const envelope = resolveOutboundEnvelope(
    { text: "Check this", complete: false },
    {
      sender: "hermes",
      thread_id: "550e8400-e29b-41d4-a716-446655440001",
      seq: 12,
      hop: 3,
      complete: false,
    },
    () => "550e8400-e29b-41d4-a716-446655440003",
  );
  assert.deepEqual(envelope, {
    recipient: "hermes",
    thread_id: "550e8400-e29b-41d4-a716-446655440001",
    reply_to: 12,
    hop: 4,
    complete: false,
    client_msg_id: "550e8400-e29b-41d4-a716-446655440003",
    text: "Check this",
  });
});

test("closed or exhausted peer threads cannot accept another reply", () => {
  assert.throws(() => resolveOutboundEnvelope(
    { text: "continue" },
    { sender: "hermes", thread_id: "550e8400-e29b-41d4-a716-446655440001", seq: 1, hop: 8, complete: false },
    () => "550e8400-e29b-41d4-a716-446655440003",
  ), /hop limit/);
  assert.throws(() => resolveOutboundEnvelope(
    { text: "continue" },
    { sender: "hermes", thread_id: "550e8400-e29b-41d4-a716-446655440001", seq: 1, hop: 0, complete: true },
    () => "550e8400-e29b-41d4-a716-446655440003",
  ), /closed/);
});

test("closed thread IDs are rejected even after peer context has ended", () => {
  assert.throws(() => resolveOutboundEnvelope(
    { recipient: "hermes", thread_id: "550e8400-e29b-41d4-a716-446655440001", text: "late reply" },
    null,
    () => "550e8400-e29b-41d4-a716-446655440003",
    ["550e8400-e29b-41d4-a716-446655440001"],
  ), /thread is closed/);
});

test("legacy roomctl sends are gated as outbound messages", () => {
  assert.equal(shouldGateToolCall("bash", false, { command: "roomctl send --to hermes --text hi" }), true);
  assert.equal(shouldGateToolCall("bash", false, { command: "roomctl read --after 3" }), false);
});

test("relay message approval shows the exact recipient and message body", async () => {
  let question;
  const sendEvent = {
    toolName: "agent_commons_send",
    input: { text: "Check the project status" },
  };
  const result = await gatePeerToolCall(sendEvent, { name: "agent_commons_send" }, {
    hasUI: true,
    ui: { confirm: async (_title, prompt) => { question = prompt; return false; } },
  }, {
    sender: "hermes",
    thread_id: "550e8400-e29b-41d4-a716-446655440001",
    seq: 12,
    hop: 3,
    complete: false,
  });
  assert.equal(result.block, true);
  assert.equal(sendEvent.input.recipient, "hermes");
  assert.equal(sendEvent.input.thread_id, "550e8400-e29b-41d4-a716-446655440001");
  assert.equal(sendEvent.input.reply_to, 12);
  assert.equal(sendEvent.input.hop, 4);
  assert.match(question, /hermes/);
  assert.match(question, /Check the project status/);
  assert.match(question, /thread_id/);
  assert.match(question, /reply_to/);
});

test("denial and missing UI block peer-requested actions", async () => {
  const denied = await gatePeerToolCall(event, {}, {
    hasUI: true,
    ui: { confirm: async () => false },
  });
  assert.equal(denied.block, true);
  assert.match(denied.reason, /not approved/);

  const secret = await gatePeerToolCall({
    toolName: "bash", input: { command: "export API_KEY=sk-test-placeholder-1234567890" },
  }, {}, { hasUI: true, ui: { confirm: async () => true } });
  assert.equal(secret.block, true);
  assert.match(secret.reason, /contain a secret/);

  const headless = await gatePeerToolCall(event, {}, { hasUI: false, ui: {} });
  assert.equal(headless.block, true);
  assert.match(headless.reason, /no human approval interface/);
});

test("oversized action details are blocked instead of asking ambiguous approval", async () => {
  let asked = false;
  const result = await gatePeerToolCall({ toolName: "bash", input: { command: "x".repeat(9000) } }, {}, {
    hasUI: true,
    ui: { confirm: async () => { asked = true; return true; } },
  });
  assert.equal(result.block, true);
  assert.equal(asked, false);
});
