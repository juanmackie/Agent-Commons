import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { configuredRelayClient, recipientPollWarning, RelayClient } from "../src/relay.mjs";

const THREAD = "b3d9dcc7-137c-481f-b268-2a40c7e20c88";
const CLIENT_ID = "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13";
const config = { api:"https://relay.example.test", project:"demo", agentId:"pi" };

test("Pi reads roomctl config when env variables are unset and lets env override it", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "agent-commons-config-"));
  try {
    const configDirectory = path.join(directory, "agent-commons");
    await mkdir(configDirectory);
    await writeFile(path.join(configDirectory, "config.json"), JSON.stringify({
      api: "https://relay.example.test", project: "demo", agent_id: "pi",
    }));
    const env = { LOCALAPPDATA: directory };
    const fromFile = await configuredRelayClient(env);
    assert.deepEqual(
      { api: fromFile.baseUrl, project: fromFile.project, agentId: fromFile.agentId },
      { api: "https://relay.example.test", project: "demo", agentId: "pi" },
    );
    const overridden = await configuredRelayClient({
      ...env, ROOM_PROJECT: "other", AGENT_ID: "pi-2",
    });
    assert.deepEqual(
      { project: overridden.project, agentId: overridden.agentId },
      { project: "other", agentId: "pi-2" },
    );
    await writeFile(path.join(configDirectory, "config.json"), "not-json");
    const explicit = await configuredRelayClient({
      ...env, ROOM_API: "https://explicit.example.test", ROOM_PROJECT: "explicit", AGENT_ID: "pi-3",
    });
    assert.deepEqual(
      { api: explicit.baseUrl, project: explicit.project, agentId: explicit.agentId },
      { api: "https://explicit.example.test", project: "explicit", agentId: "pi-3" },
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("send receipts warn only when recipient poll time is absent or at least two minutes old", () => {
  const now = Date.parse("2026-11-02T00:00:00Z");
  assert.equal(recipientPollWarning({}, "hermes", now), "");
  assert.match(recipientPollWarning({ recipient_last_seen: null }, "hermes", now), /never polled/);
  assert.equal(recipientPollWarning({ recipient_last_seen: "2026-11-01T23:59:00Z" }, "hermes", now), "");
  assert.match(recipientPollWarning({ recipient_last_seen: "2026-11-01T23:58:00Z" }, "hermes", now), /2 minutes/);
  assert.equal(recipientPollWarning({ recipient_last_seen: "invalid" }, "hermes", now), "");
});

test("relay poll uses project addressing and no application credential", async () => {
  let request;
  const client = new RelayClient({ ...config, fetchImpl: async (url,options) => {
    request={url,options}; return Response.json({messages:[],next_after:12});
  }});
  assert.deepEqual(await client.read(7),{messages:[],next_after:12});
  assert.equal(request.url,"https://relay.example.test/v1/messages?project=demo&after=7&limit=100");
  assert.equal(request.options.headers.Authorization,undefined);
  assert.equal(request.options.redirect,"error");
});

test("relay post validates and sends a bounded project message", async () => {
  let request;
  const client=new RelayClient({api:"http://127.0.0.1:8080",project:"demo",agentId:"pi",fetchImpl:async(url,options)=>{
    request={url,options};return Response.json({seq:13},{status:201});
  }});
  assert.deepEqual(await client.send({recipient:"hermes",threadId:THREAD,clientMessageId:CLIENT_ID,text:"review this",replyTo:12,hop:3,complete:false}),{seq:13});
  assert.equal(request.url,"http://127.0.0.1:8080/v1/messages?project=demo");
  assert.deepEqual(JSON.parse(request.options.body),{client_msg_id:CLIENT_ID,sender:"pi",recipient:"hermes",thread_id:THREAD,reply_to:12,hop:3,text:"review this",complete:false,delivery:"follow_up"});
});

test("relay client refuses unsafe endpoints and invalid message shapes",async()=>{
  assert.throws(()=>new RelayClient({api:"http://relay.example.test",project:"demo",agentId:"pi"}),/HTTPS/);
  assert.throws(()=>new RelayClient({api:"http://127.0.0.1",project:"Bad",agentId:"pi"}),/project/);
  const client=new RelayClient({...config});
  await assert.rejects(client.send({recipient:"hermes",threadId:THREAD,text:""}),/non-empty/);
  await assert.rejects(client.send({recipient:"hermes",threadId:THREAD,text:"x".repeat(17000)}),/16 KiB/);
});

test("malformed send receipts fail closed so the durable outbox can retry",async()=>{
  const client=new RelayClient({...config,fetchImpl:async()=>Response.json({ok:true})});
  await assert.rejects(client.send({recipient:"hermes",threadId:THREAD,text:"message"}),/invalid send receipt/);
});

test("malformed envelopes and out-of-order relay messages fail closed",async()=>{
  const base={sender:"hermes",recipient:"pi",thread_id:THREAD,seq:1,reply_to:null,hop:0,client_msg_id:CLIENT_ID,text:"message",complete:false};
  const malformed=new RelayClient({...config,fetchImpl:async()=>Response.json({messages:[{...base,hop:9}],next_after:1})});
  await assert.rejects(malformed.read(0),/invalid message/);
  const outOfOrder=new RelayClient({...config,fetchImpl:async()=>Response.json({messages:[{...base,seq:2},{...base,seq:1}],next_after:2})});
  await assert.rejects(outOfOrder.read(0),/invalid message/);
});

test("relay API errors retain HTTP status for outbound classification",async()=>{
  const client=new RelayClient({...config,fetchImpl:async()=>new Response("unavailable",{status:503})});
  await assert.rejects(client.read(0), error => error.status === 503 && error.retryable && !error.configuration);
  const rejected=new RelayClient({...config,fetchImpl:async()=>new Response("rejected",{status:401})});
  await assert.rejects(rejected.read(0), error => error.status === 401 && !error.retryable && error.configuration);
});

test("relay response interruptions are classified as retryable without exposing response data", async () => {
  const body = new ReadableStream({ start(controller) { controller.error(new TypeError("private partial response")); } });
  const client = new RelayClient({ ...config, fetchImpl: async () => new Response(body) });
  await assert.rejects(client.read(0), error => error.retryable && !error.message.includes("private partial response"));
});

test("oversized relay responses are rejected before JSON parsing",async()=>{
  const client=new RelayClient({...config,fetchImpl:async()=>new Response("x".repeat(2*1024*1024+1))});
  await assert.rejects(client.read(0),/exceeded 2 MiB/);
});
