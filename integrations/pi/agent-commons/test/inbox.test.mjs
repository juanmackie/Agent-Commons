import assert from "node:assert/strict";
import { test } from "node:test";
import { acknowledgeOutboundMessage, acknowledgeRelayMessage, classifyOutboundFailure, closeRelayThread, closedThreadsForOutbound, hasQueuedOutboundMessage, hasVisiblePeerNotice, nextOutbound, nextRelayMessage, queueOutboundMessage, queueRelayPage, rejectOutboundMessage, visiblePendingMessages } from "../src/inbox.mjs";

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
const initial = { cursor: 0, seen: [], peerContext: null, pendingInbox: [], outbox: [], closedThreads: [] };

test("pending relay data is durable and any live owner can deliver it", () => {
  const queued = queueRelayPage(initial, { messages: [message(1)], next_after: 1 }, "pi");
  assert.equal(queued.cursor, 1);
  assert.equal("target_session_id" in queued.pendingInbox[0], false);
  assert.equal(nextRelayMessage(queued).client_msg_id, message(1).client_msg_id);
  assert.equal(queueRelayPage(queued, { messages: [message(1)], next_after: 1 }, "pi").pendingInbox.length, 1);
});

test("an interrupt is delivered before older ordinary inbox messages", () => {
  const ordinary = { ...message(1), delivery: "follow_up" };
  const interrupt = { ...message(2), delivery: "interrupt" };
  const state = { ...initial, pendingInbox: [ordinary, interrupt] };
  assert.equal(nextRelayMessage(state), interrupt);
  assert.equal(nextRelayMessage(state, new Set([interrupt.client_msg_id])), ordinary);
});

test("visible prioritized messages are acknowledged even when older messages are not visible", () => {
  const ordinary = { ...message(1), delivery: "follow_up" };
  const interrupt = { ...message(2), delivery: "interrupt" };
  const state = { ...initial, pendingInbox: [ordinary, interrupt] };
  assert.deepEqual(visiblePendingMessages(state, (id) => id === interrupt.client_msg_id), [interrupt]);
});

test("old pending messages without a current session marker remain deliverable", () => {
  const old = { ...message(1), target_session_id: "closed-session" };
  const state = { ...initial, pendingInbox: [old] };
  assert.equal(nextRelayMessage(state).client_msg_id, old.client_msg_id);
});

test("full inbox does not advance past an unpersisted target message", () => {
  const existing = Array.from({ length: 100 }, (_, index) => message(index + 1));
  const state = { ...initial, cursor: 100, pendingInbox: existing };
  const result = queueRelayPage(state, { messages: [message(101), message(102)], next_after: 102 }, "pi");
  assert.equal(result.cursor, 100);
  assert.equal(result.pendingInbox.length, 100);
});

test("completion durably closes the thread and drops later messages from model delivery", () => {
  const final = message(1, { complete: true });
  const later = message(2, { client_msg_id: "550e8400-e29b-41d4-a716-446655440004", text: "late reply" });
  const queued = queueRelayPage(initial, { messages: [final, later], next_after: 2 }, "pi");
  assert.deepEqual(queued.closedThreads, [final.thread_id]);
  assert.equal(queued.pendingInbox.length, 1);
  assert.equal(queued.pendingInbox[0].complete, true);
  assert.deepEqual(queued.seen, [later.client_msg_id]);
  assert.equal(queued.cursor, 2);
  const outbound = closeRelayThread(queued, final.thread_id);
  assert.deepEqual(outbound.closedThreads, [final.thread_id]);
});

test("outbound failures distinguish retry, rejected, and expired-project hold", () => {
  assert.equal(classifyOutboundFailure({ status: 503 }), "retry");
  assert.equal(classifyOutboundFailure({ status: 422 }), "reject");
  assert.equal(classifyOutboundFailure({ status: 409 }), "reject");
  assert.equal(classifyOutboundFailure({ status: 413 }), "reject");
  assert.equal(classifyOutboundFailure({ status: 404 }), "hold");
});

test("rejecting an outbound completion releases the thread and allows later relay messages", () => {
  const outgoing = {
    ...message(1), client_msg_id: "id-1", complete: true,
    recipient: "hermes",
  };
  const queued = queueOutboundMessage({ ...initial, outbox: [], closedThreads: [] }, outgoing);
  assert.deepEqual(queued.closedThreads, []);
  assert.deepEqual(closedThreadsForOutbound(queued), [outgoing.thread_id]);
  const rejected = rejectOutboundMessage(queued, "id-1");
  assert.deepEqual(rejected.outbox, []);
  assert.deepEqual(closedThreadsForOutbound(rejected), []);
  const later = message(2, { thread_id: outgoing.thread_id, client_msg_id: "id-2" });
  const received = queueRelayPage(rejected, { messages: [later], next_after: 2 }, "pi");
  assert.equal(received.pendingInbox[0].client_msg_id, "id-2");
});

test("rejected outbound completion preserves an independently confirmed closure", () => {
  const incoming = message(3, { complete: true, client_msg_id: "incoming-complete" });
  const received = queueRelayPage(initial, { messages: [incoming], next_after: 3 }, "pi");
  const acknowledged = acknowledgeRelayMessage(received, incoming.client_msg_id);
  assert.deepEqual(acknowledged.pendingInbox, []);

  const outgoing = {
    ...message(4), client_msg_id: "outgoing-complete", complete: true,
  };
  const queued = queueOutboundMessage({ ...acknowledged, outbox: [] }, outgoing);
  const rejected = rejectOutboundMessage(queued, outgoing.client_msg_id);
  assert.deepEqual(rejected.closedThreads, [incoming.thread_id]);
  assert.deepEqual(closedThreadsForOutbound(rejected), [incoming.thread_id]);
});

test("queued-send detection requires the exact durable envelope", () => {
  const outgoing = {
    recipient: "hermes", thread_id: message(1).thread_id, reply_to: null, hop: 0,
    text: "approved", complete: false, client_msg_id: "stable-id",
  };
  const state = { ...initial, outbox: [outgoing] };
  assert.equal(hasQueuedOutboundMessage(state, outgoing), true);
  assert.equal(hasQueuedOutboundMessage(state, { ...outgoing, text: "changed" }), false);
  assert.equal(hasQueuedOutboundMessage(state, { ...outgoing, recipient: "other" }), false);
});

test("approved outbound payload persists with a stable retry ID until acknowledged", () => {
  const outgoing = {
    recipient: "hermes", thread_id: message(1).thread_id, reply_to: 8, hop: 1,
    text: "approved text", complete: true,
    client_msg_id: "550e8400-e29b-41d4-a716-446655440099",
  };
  const queued = queueOutboundMessage({ ...initial, outbox: [] }, outgoing);
  assert.deepEqual(queued.outbox, [outgoing]);
  assert.deepEqual(nextOutbound(queued), outgoing);
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
  const queued = queueRelayPage(initial, { messages: [message(1)], next_after: 1 }, "pi");
  const received = acknowledgeRelayMessage(queued, message(1).client_msg_id);
  assert.deepEqual(received.pendingInbox, []);
  assert.deepEqual(received.seen, [message(1).client_msg_id]);
  assert.equal(received.peerContext.client_msg_id, message(1).client_msg_id);
});
