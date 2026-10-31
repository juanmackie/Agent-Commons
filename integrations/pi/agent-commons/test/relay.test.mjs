import assert from "node:assert/strict";
import { test } from "node:test";
import { RelayClient } from "../src/relay.mjs";

const THREAD = "b3d9dcc7-137c-481f-b268-2a40c7e20c88";
const CLIENT_ID = "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13";

test("relay poll authenticates and rejects redirects", async () => {
  let request;
  const client = new RelayClient({
    api: "https://relay.example.test",
    capability: "a".repeat(43),
    agentId: "pi",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return Response.json({ messages: [], next_after: 12 });
    },
  });

  assert.deepEqual(await client.read(7), { messages: [], next_after: 12 });
  assert.equal(request.url, "https://relay.example.test/v1/messages?after=7&limit=100");
  assert.equal(request.options.headers.Authorization, `Bearer ${"a".repeat(43)}`);
  assert.equal(request.options.redirect, "error");
});

test("relay post validates and sends a bounded message to the authenticated API", async () => {
  let request;
  const client = new RelayClient({
    api: "http://127.0.0.1:8080",
    capability: "a".repeat(43),
    agentId: "pi",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return Response.json({ seq: 13 }, { status: 201 });
    },
  });

  assert.deepEqual(await client.send({
    recipient: "hermes",
    threadId: THREAD,
    clientMessageId: CLIENT_ID,
    text: "review this",
    replyTo: 12,
    hop: 3,
    complete: false,
  }), { seq: 13 });
  assert.equal(request.url, "http://127.0.0.1:8080/v1/messages");
  assert.deepEqual(JSON.parse(request.options.body), {
    client_msg_id: CLIENT_ID,
    sender: "pi",
    recipient: "hermes",
    thread_id: THREAD,
    reply_to: 12,
    hop: 3,
    text: "review this",
    complete: false,
  });
});

test("relay client refuses unsafe endpoints and invalid message shapes", async () => {
  assert.throws(() => new RelayClient({ api: "http://relay.example.test", capability: "a".repeat(43), agentId: "pi" }), /HTTPS/);
  assert.throws(() => new RelayClient({ api: "http://127.0.0.1", capability: "short", agentId: "pi" }), /capability/);
  const client = new RelayClient({ api: "https://relay.example.test", capability: "a".repeat(43), agentId: "pi" });
  await assert.rejects(client.send({ recipient: "hermes", threadId: THREAD, text: "" }), /non-empty/);
  await assert.rejects(client.send({ recipient: "hermes", threadId: THREAD, text: "x".repeat(17000) }), /16 KiB/);
});

test("malformed send receipts fail closed so the durable outbox can retry", async () => {
  const client = new RelayClient({
    api: "https://relay.example.test", capability: "a".repeat(43), agentId: "pi",
    fetchImpl: async () => Response.json({ ok: true }),
  });
  await assert.rejects(client.send({ recipient: "hermes", threadId: THREAD, text: "message" }), /invalid send receipt/);
});

test("malformed envelopes and out-of-order relay messages fail closed", async () => {
  const base = {
    sender: "hermes", recipient: "pi", thread_id: THREAD, seq: 1, reply_to: null,
    hop: 0, client_msg_id: CLIENT_ID, text: "message", complete: false,
  };
  const malformed = new RelayClient({
    api: "https://relay.example.test", capability: "a".repeat(43), agentId: "pi",
    fetchImpl: async () => Response.json({ messages: [{ ...base, hop: 9 }], next_after: 1 }),
  });
  await assert.rejects(malformed.read(0), /invalid message/);
  const outOfOrder = new RelayClient({
    api: "https://relay.example.test", capability: "a".repeat(43), agentId: "pi",
    fetchImpl: async () => Response.json({ messages: [{ ...base, seq: 2 }, { ...base, seq: 1 }], next_after: 2 }),
  });
  await assert.rejects(outOfOrder.read(0), /invalid message/);
});

test("relay API failures fail closed", async () => {
  const client = new RelayClient({
    api: "https://relay.example.test",
    capability: "a".repeat(43),
    agentId: "pi",
    fetchImpl: async () => new Response("unavailable", { status: 503 }),
  });
  await assert.rejects(client.read(0), /HTTP 503/);
});

test("oversized relay responses are rejected before JSON parsing", async () => {
  const client = new RelayClient({
    api: "https://relay.example.test",
    capability: "a".repeat(43),
    agentId: "pi",
    fetchImpl: async () => new Response("x".repeat(2 * 1024 * 1024 + 1)),
  });
  await assert.rejects(client.read(0), /exceeded 2 MiB/);
});
