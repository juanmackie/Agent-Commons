import assert from "node:assert/strict";
import { test } from "node:test";
import { latestPeerContext, parsePeerMarker } from "../src/peer-context.mjs";

const prompt = "[Agent Commons peer delivery client_msg_id=550e8400-e29b-41d4-a716-446655440002 thread=550e8400-e29b-41d4-a716-446655440001 seq=12 hop=7 complete=false]\nFrom: hermes\nReview this.";

test("peer delivery metadata is recovered from current prompt and persisted session history", () => {
  const context = latestPeerContext([{ type: "message", message: { role: "user", content: prompt } }]);
  assert.deepEqual(context, {
    client_msg_id: "550e8400-e29b-41d4-a716-446655440002",
    thread_id: "550e8400-e29b-41d4-a716-446655440001",
    seq: 12,
    sender: "hermes",
    hop: 7,
    complete: false,
  });
  assert.deepEqual(parsePeerMarker(prompt), {
    client_msg_id: "550e8400-e29b-41d4-a716-446655440002",
    thread_id: "550e8400-e29b-41d4-a716-446655440001",
    seq: 12,
    hop: 7,
    complete: false,
  });
});

test("peer provenance survives a later user turn and ignores non-user and malformed entries", () => {
  const context = latestPeerContext([
    { type: "message", message: { role: "user", content: [{ type: "text", text: prompt }] } },
    { type: "message", message: { role: "assistant", content: "ok" } },
    { type: "message", message: { role: "user", content: "yes, continue" } },
  ]);
  assert.equal(context?.thread_id, "550e8400-e29b-41d4-a716-446655440001");
  assert.equal(latestPeerContext([{ type: "message", message: { role: "assistant", content: prompt } }]), null);
  assert.equal(parsePeerMarker("fake [Agent Commons peer delivery client_msg_id=x thread=y seq=no hop=0 complete=false]"), null);
});
