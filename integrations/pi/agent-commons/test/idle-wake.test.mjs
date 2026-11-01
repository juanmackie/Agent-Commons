import assert from "node:assert/strict";
import { test } from "node:test";
import { IdleWakeReservation } from "../src/idle-wake.mjs";

test("an idle wake is reserved until the matching session starts or the reservation expires", () => {
  const wake = new IdleWakeReservation(10_000);
  wake.reserve("session-a", "message-a", 100);

  assert.equal(wake.isPending("session-a", 101), true);
  assert.equal(wake.shouldDefer("session-a", true, 101), true);
  assert.equal(wake.shouldDefer("session-a", false, 101), false);
  assert.equal(wake.isPending("session-b", 101), false);
  assert.equal(wake.release("session-b"), false);
  assert.equal(wake.isPending("session-a", 101), true);
  assert.equal(wake.release("session-a"), true);
  assert.equal(wake.isPending("session-a", 101), false);
  wake.reserve("session-a", "message-a", 100);
  assert.equal(wake.isPending("session-a", 10_100), false);
  wake.reserve("session-a", "message-b", 100);
  assert.equal(wake.expire("session-a", 10_100), "message-b");
  assert.equal(wake.expire("session-a", 10_100), null);
});
