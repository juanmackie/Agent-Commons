import assert from "node:assert/strict";
import { test } from "node:test";
import { isNearBottom, scrollTopAfterPrepend } from "../../../../app/static/room-scroll.mjs";

test("new messages follow the feed only when the reader is near the bottom", () => {
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 940, clientHeight: 400 }), true);
  assert.equal(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 400 }), false);
});

test("loading earlier messages keeps the visible content at the same viewport position", () => {
  assert.equal(scrollTopAfterPrepend(350, 1000, 1600), 950);
});
