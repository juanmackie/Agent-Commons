import assert from "node:assert/strict";
import { test } from "node:test";
import { acknowledgeOutboundMessage, acknowledgeRelayMessage, closeRelayThread, hasVisiblePeerNotice, nextOutboundForSession, nextRelayMessage, queueOutboundMessage, queueRelayPage } from "../src/inbox.mjs";

const message = (seq, overrides = {}) => ({
  sender: "hermes",
  recipient: "pi",
  thread_id: "550e8400-e29b-41d4-a716-446655440001",
  seq,
  reply_to: null,
  hop: 0,
  client_msg_id: `550e8400-e29b-41d4-a716-${String(seq).padStart(12, "0")}`,
  text: `message ${seq}`,
  complete: false,
  ...overrides,
});
const initial = { cursor: 0, seen: [], peerContext: null, pendingInbox: [], sessionId: "session-1" };

test("incoming relay data is durable before delivery and remains assigned to its exact session", () => {
  const queued = queueRelayPage(initial, { messages: [message(1)], next_after: 1 }, "pi", "session-1");
  assert.equal(queued.cursor, 1);
  assert.equal(queued.pendingInbox[0].target_session_id, "session-1");
  assert.equal(nextRelayMessage(queued, "session-2"), null);
  assert.equal(nextRelayMessage(queued, "session-1").client_msg_id, message(1).client_msg_id);
  assert.equal(queueRelayPage(queued, { messages: [message(1)], next_after: 1 }, "pi", "session-1").pendingInbox.length, 1);
});

test("messages cannot be queued without a verified target session", () => {
  assert.throws(() => queueRelayPage(initial, { messages: [message(1)], next_after: 1 }, "pi", null), /target session identity/);
});

test("full inbox does not advance past an unpersisted target message", () => {
  const existing = Array.from({ length: 100 }, (_, index) => ({ ...message(index + 1), target_session_id: "session-1" }));
  const state = { ...initial, cursor: 100, pendingInbox: existing };
  const result = queueRelayPage(state, { messages: [message(101), message(102)], next_after: 102 }, "pi", "session-1");
  assert.equal(result.cursor, 100);
  assert.equal(result.pendingInbox.length, 100);
});

test("completion durably closes the thread and drops later messages from model delivery", () => {
  const final = message(1, { complete: true });
  const later = message(2, { client_msg_id: "550e8400-e29b-41d4-a716-446655440004", text: "late reply" });
  const queued = queueRelayPage(initial, { messages: [final, later], next_after: 2 }, "pi", "session-1");
  assert.deepEqual(queued.closedThreads, [final.thread_id]);
  assert.equal(queued.pendingInbox.length, 1);
  assert.equal(queued.pendingInbox[0].complete, true);
  assert.deepEqual(queued.seen, [later.client_msg_id]);
  assert.equal(queued.cursor, 2);
  const outbound = closeRelayThread(queued, final.thread_id);
  assert.deepEqual(outbound.closedThreads, [final.thread_id]);
});

test("approved outbound payload persists with a stable retry ID until acknowledged", () => {
  const outgoing = {
    recipient: "hermes", thread_id: message(1).thread_id, reply_to: 8, hop: 1,
    text: "approved text", complete: true,
    client_msg_id: "550e8400-e29b-41d4-a716-446655440099",
    target_session_id: "session-1",
  };
  const queued = queueOutboundMessage({ ...initial, outbox: [] }, outgoing);
  assert.deepEqual(queued.outbox, [outgoing]);
  assert.equal(nextOutboundForSession(queued, "session-2"), null);
  assert.deepEqual(nextOutboundForSession(queued, "session-1"), outgoing);
  assert.deepEqual(queueOutboundMessage(queued, outgoing), queued);
  assert.throws(() => queueOutboundMessage(queued, { ...outgoing, text: "changed after approval" }), /different message/);
  const sent = acknowledgeOutboundMessage(queued, outgoing.client_msg_id);
  assert.deepEqual(sent.outbox, []);
  assert.deepEqual(sent.closedThreads, [outgoing.thread_id]);
});

test("completion notices are visible in session history without a user-model turn", () => {
  const entries = [{
    type: "custom_message", customType: "agent-commons-peer", content: "finished",
    display: true, details: { client_msg_id: message(1).client_msg_id },
  }];
  assert.equal(hasVisiblePeerNotice(entries, message(1).client_msg_id), true);
  assert.equal(hasVisiblePeerNotice(entries, message(2).client_msg_id), false);
});

test("acknowledgment is one-session-only and marks the relay message seen", () => {
  const queued = queueRelayPage(initial, { messages: [message(1)], next_after: 1 }, "pi", "session-1");
  const wrongSession = acknowledgeRelayMessage(queued, message(1).client_msg_id, "session-2");
  assert.equal(wrongSession, queued);
  const received = acknowledgeRelayMessage(queued, message(1).client_msg_id, "session-1");
  assert.deepEqual(received.pendingInbox, []);
  assert.deepEqual(received.seen, [message(1).client_msg_id]);
  assert.equal(received.peerContext.client_msg_id, message(1).client_msg_id);
});
