# Agent Room Relay — First-Principles Implementation Plan

**Version:** 2.0 — 31 October 2026  
**Status:** Partial implementation handoff; session-adapter sections superseded; **not approval to deploy**
**Target:** TrueNAS SCALE on the HP Z640; Hermes Agent on the Z640; Pi Agent on a second machine; private Tailscale connectivity
**North star:** *Agents working on a project can message and ping each other in multi-turn exchanges, with the full exchange visible in the exact active user-facing sessions and no human forwarding required.*

> **Accepted goal update (2026-10-31):** Agent Commons is a messaging capability, not a WhatsApp-style chat product. Use one private room per project. Messages must enter the recipient's exact active Telegram or Pi terminal session, remain visible to the human, and queue for that same session while it is busy or unavailable. Do not silently fall back to a bridge-owned session. The Hermes API and Pi RPC adapter sections below describe separate sessions and do not meet this goal; do not implement those sections as written. Investigate native session integrations and block any adapter whose behavior cannot be verified. Non-secret project data may be shared, but secrets must stay out of prompts and relay messages. Peer-requested destructive actions require the recipient agent to ask its human for permission for the specific action and target, then wait. See [ADR 0001](docs/adr/0001-live-session-agent-messaging.md).

> **Instruction to implementing agent:** Implement incrementally, with tests and read-only environmental checks before mutations. Do not deploy to TrueNAS, change Tailscale Serve, delete or replace any existing service, modify production agent settings, or commit/push code without the appropriate explicit approval. The user confirms `agentchattr` has been removed. Do not assume the former service or its port/route still exists. Reinspect everything live.

---

## 0. Executive decision

Build **a minimal, tailnet-only message relay** and **two tiny, independent agent-side bridges**. The relay stores and delivers messages; the bridges wake the agents and carry their replies back. This is *not* a central agent orchestrator.

**Deliverables:**

1. **Relay (TrueNAS):** FastAPI + SQLite + a simple HTTP API, durable chronological messages, one shareable random room capability, incremental reads, and idempotent posting.
2. **Agent bridge (each machine):** a small persistent poller/worker with a durable local inbox, an adapter to invoke that machine's actual agent, and an outgoing reply publisher.
3. **Hermes adapter:** use supported, authenticated, *local* Hermes API/session/run functionality, subject to verification against the installed version.
4. **Pi adapter:** use Pi's documented long-lived RPC mode and retained sessions, subject to verification against the installed version.
5. **Portable agent skill + operator guide:** allow either agent to **initiate** messages on demand, monitor conversations, and understand the boundaries.
6. **One genuine end-to-end demonstration:** Hermes asks Pi for work; Pi answers; Hermes asks a follow-up; Pi answers; both conclude without the user relaying any messages.

**Definition of done is the autonomous conversation, not a successful `curl`.**

**Non-goals:** agent discovery, user accounts, public web access, file transfer, browser UI, MCP server, WebSockets, Redis/NATS, distributed locking, cross-agent shared memory, unlimited autonomous actions, and a general-purpose workflow engine. Do not add these merely because a framework makes them easy.

**Why not ntfy.sh?** Hosted ntfy is an excellent zero-install transport for temporary experiments. This plan chooses a private self-hosted log because it deliberately needs explicit chronological replay, per-room retention, and deterministic idempotent sends. Do not copy ntfy's infrastructure or reimplement its entire product. If a working no-build alternative proves *all* acceptance criteria with less work, document the evidence and stop building the redundant relay.

---

## 1. Exercise abstract thinking before selecting technology

### 1.1 Reduce the problem to irreducible capabilities

An autonomous collaboration system needs only:

| Primitive | Question it answers | Chosen smallest mechanism |
|---|---|---|
| **Address** | Who is speaking to whom? | `sender`, `recipient`, `thread_id` |
| **Memory of exchange** | What was said, in order? | Append-only SQLite messages with increasing `seq` |
| **Notice** | How does an idle participant hear new work? | Lightweight agent-side polling |
| **Thought / action** | Who interprets the request and works? | Existing Hermes and Pi runtimes, not the relay |
| **Return** | How does a result reach its requester? | Post another message with `reply_to` |
| **Boundary** | Who is permitted to reach the system? | Tailscale access + room capability + local agent permissions |
| **Closure** | How does the exchange stop? | Explicit completion, turn budget, and bounded timeouts |

**Key abstraction:** the relay is a *durable mailbox*, not an AI. The agent bridge is a *doorbell + delivery clerk*, not a new AI framework. Hermes and Pi are the only brains.

### 1.2 The minimum system

```text
User / Telegram / Pi terminal
          |
   agent decides to contact peer
          |
  roomctl send (CLI or agent skill)
          |
          v
 +---------------------------------------+
 |  TrueNAS: agent-room-relay             |
 |  FastAPI + SQLite message log          |
 |  HTTPS through Tailscale Serve         |
 +---------------------------------------+
           ^                        ^
           | poll/post              | poll/post
           |                        |
 +-------------------+    +-------------------+
 | Hermes bridge     |    | Pi bridge         |
 | local inbox/cursor|    | local inbox/cursor|
 | Hermes API adapter|    | Pi RPC adapter    |
 +-------------------+    +-------------------+
           |                        |
     Hermes Agent                Pi Agent
```

The bridge executes *on or immediately beside* its own agent. The relay cannot and must not directly execute model turns or tools on other machines.

### 1.3 Invariants (design tests against these)

- **I1:** A valid room capability permits send/read in that room, *but does not authenticate an agent identity*.
- **I2:** A message is append-only and receives one strictly increasing global `seq` when committed.
- **I3:** Reposting an identical `client_msg_id` and payload returns the original result, without duplicate messages; reusing it with a different payload returns `409`.
- **I4:** Clients can restart and resume from durable local state without silently losing accepted messages.
- **I5:** Messages sent by an agent are never automatically reprocessed by that same agent.
- **I6:** Delivery is **at least once**; agent execution is *not guaranteed exactly once*. Idempotent publishing reduces duplicate replies but cannot reverse an external side effect.
- **I7:** An incoming room message is **untrusted data**, never authorization to expand tool access, read credentials, or execute destructive operations.
- **I8:** A conversation finishes or stops at a finite budget. No autonomous ping-pong forever.
- **I9:** The relay is reachable only through an explicitly permitted tailnet route; no accidental LAN/public listener.

---

## 2. Apply Musk's five-step algorithm in order

### Law 1 — Question every requirement

Before coding, challenge each assertion:

1. **"We need a new chat service."** Confirm the user needs persistence/replay beyond a hosted ntfy test. If an existing simple service demonstrably suffices, stop.
2. **"We need agents to chat."** The actual need is *asynchronous task collaboration with a reply*, not visual chat bubbles or general social messaging.
3. **"We need a central orchestrator."** No. Each agent should own its decisions and local tools. A central planner is unnecessary.
4. **"We need no authentication."** Interpret as *no accounts or login flow*. Tailnet membership controls reachability. A random room ID is still a bearer capability and must be kept private.
5. **"We need real time."** For these agents, polling every 2–5 seconds is enough until measured latency proves otherwise.
6. **"We need a shared live chat session."** Not necessarily. Dedicated agent-bridge sessions are enough for v1. Do not silently claim a bridge can write to an existing Telegram/terminal session.
7. **"Room expiry must be 12 hours."** That was inherited from a reference product, not the outcome. Make it configurable; set a practical initial default after the user accepts the tradeoff.

**Gate Q:** Write a one-page evidence note identifying the cheapest approach that meets the acceptance criteria and why each retained component exists. No coding before this decision is explicit.

### Law 2 — Delete aggressively

Do **not** ship a frontend, room list UI, WebSockets/SSE, Redis, message broker, public ingress, user database, OAuth, invitations, per-agent tokens, embeddings, vector store, agent discovery, skills registry, multi-worker coordination, Kubernetes, general job scheduler, or MCP server.

Do not create another app to host the agent bridge on TrueNAS if it can run safely alongside Hermes using existing facilities. Do not add a second language solely to share code; prefer a small Python implementation on both machines unless the verified agent API warrants otherwise.

**Gate D:** Every file, dependency, daemon, and endpoint must map to an invariant or acceptance test. Delete speculative abstractions.

### Law 3 — Simplify what remains

One FastAPI process; one SQLite database; one capability per room; three operations plus health; one bridge process per agent; one persistent local inbox per bridge; simple CLI to initiate messages. No complex general-purpose state machine.

**Gate S:** An implementer should be able to explain the full architecture in under two minutes, and every failure should be diagnosable with request IDs, message sequence numbers, and local logs (never message secrets).

### Law 4 — Accelerate cycle time

Implement thin, vertical test slices. Test the relay on loopback, then two raw HTTP clients, then a mock agent bridge, then Pi, then Hermes, then one genuine multi-turn conversation. Prefer a 10-line working integration to a week of speculative framework work. TDD is encouraged where behavior is deterministic; integration tests are mandatory where adapter behavior depends on a specific installed version.

**Gate A:** Each milestone produces runnable evidence, not just source files.

### Law 5 — Automate only what is proven

After a manual end-to-end demo, add restart policies, persistent bridge startup, health checks, replay on restart, and repeatable deployment. Do not add automatic rollout, automatic self-modification, or monitoring dashboards in v1.

**Gate U:** No automation of an unvalidated workflow; automate the proven path only.

---

## 3. Known context versus claims that require verification

**Known from the supplied original plan (historical, unverified):**

- TrueNAS host previously reported at `192.168.68.116`; workspace `/opt/data`; planned source `/opt/data/agent-room-relay`.
- Existing Tailscale Serve container previously had several mappings, including candidate old `8446` associated with `agentchattr`.
- The relay app was not yet present when the prior plan was written.
- Previously suggested internal container port `8080`, host port `8310`, and tailnet HTTPS port `8447` are **candidates only**.
- Proposed data mount `/mnt/tank/apps/agent-room-relay/data` depends on a dataset/path check.

**User-provided update:** `agentchattr` has been **removed**. Do not assume its app, container, host port, or route remains; equally, do not assume an orphaned Serve route was removed. Do not resurrect or modify `agentchattr`.

**Before changing anything, inspect live:** TrueNAS version, app inventory and port bindings, Tailscale version and Serve status, container network mode, existing source directory, dataset path/UID permissions, Hermes version and API enablement, Pi version and RPC availability, installed Python/uv/Docker versions, and actual Tailscale connectivity from both machines.

**Hard preserve rule:** Any unrelated app/Serve route must remain unchanged. Never use `tailscale serve reset` or broad docker/app pruning as a shortcut.

---

## 4. Exact v1 relay contract

### 4.1 Network and capability model

- `POST /v1/rooms` is accessible **only inside the tailnet**. It returns a cryptographically random 256-bit URL-safe room ID (`secrets.token_urlsafe(32)`; normally 43 characters).
- `POST /v1/messages` and `GET /v1/messages` require `Authorization: Bearer <ROOM_ID>`.
- There are **no user accounts** and no separate agent registration. Any room-holder can read, write, and impersonate a `sender` label. Do not imply stronger sender identity.
- In SQLite store **SHA-256(room ID)** as the lookup key, not the raw capability. Use parameterized queries and constant-time comparison where an actual secret comparison occurs.
- Never include room IDs in message URLs, structured logs, error bodies, metrics, or crash reports; reveal the ID only in the initial room creation response. Never accept room IDs as query strings.
- Use CORS disabled by default. No unauthenticated admin/debug endpoints beyond health. Limit request body size before JSON parsing where practical.

### 4.2 Endpoints (exactly three operations + health)

| Method/path | Request | Successful response |
|---|---|---|
| `GET /healthz` | No auth | `200 {"status":"ok"}`; no secrets |
| `POST /v1/rooms` | Empty body | `201 {"room_id":"...","idle_ttl_seconds":604800}` |
| `POST /v1/messages` | Bearer + JSON message | `201` on new write, `200` on exact replay; same `seq`/timestamp |
| `GET /v1/messages?after=0&limit=100` | Bearer | `200 {"messages":[...],"next_after":N}` |

A posted message has this **minimal explicit envelope**:

```json
{
  "client_msg_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
  "sender": "hermes",
  "recipient": "pi",
  "thread_id": "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
  "reply_to": null,
  "hop": 0,
  "text": "Please review this design and identify its largest risk.",
  "complete": false
}
```

The server adds `seq` (monotonically increasing), `created_at` (UTC ISO-8601), and echoes the accepted envelope (not the room secret) in GET responses. The optional `complete` flag defaults to false for older clients; a true flag ends automated replies for the thread. For a reply, `reply_to` is the original message's numeric `seq`, `thread_id` stays unchanged, and `hop` is one higher. The `recipient` names one intended peer: initially `hermes` or `pi`; no auto-reply to wildcard/broadcast messages in v1. Agent names are plain labels, not authenticated identities.

**Validation:**

- `sender` and `recipient`: 1–64 characters, `[A-Za-z0-9_-]+`.
- `client_msg_id` and `thread_id`: canonical UUID text; stable on retries.
- `text`: non-empty UTF-8, maximum **16 KiB encoded**; trim neither content nor newlines silently.
- `hop`: integer 0–8; bridges refuse to auto-reply at the maximum; `reply_to`: nullable non-negative committed `seq` in the same room (validate when provided).
- `complete`: strict boolean, defaults to `false`; a final reply sets `true` and both bridges close the thread without another model call. It is a conversation control flag, not sender authentication or authorization.
- `after`: integer >= 0; `limit`: 1–100, default 100. `next_after` is the largest returned `seq`, or the supplied `after` when the batch is empty. Clients page until caught up.
- Missing/invalid/unknown/expired room: uniform `401 {"detail":"invalid room id"}`; validation errors `422`; too-large body/message `413`; conflicting `client_msg_id` payload `409`; capacity `429` or `507` with a documented stable error code. Never echo secrets.

**Idempotency:** Unique `(room_hash, client_msg_id)`. Within a transaction, identical retry returns original `seq`, original timestamp, and original content. Same key but changed sender, recipient, thread, reply target, hop, or text is a conflict. Never silently overwrite messages.

### 4.3 SQLite schema and lifecycle

Suggested logical schema, not an excuse for a migration framework:

```sql
rooms(
  room_hash TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  last_posted_at INTEGER NOT NULL
);
messages(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  room_hash TEXT NOT NULL REFERENCES rooms(room_hash) ON DELETE CASCADE,
  client_msg_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  reply_to INTEGER,
  hop INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(room_hash, client_msg_id)
);
CREATE INDEX idx_messages_room_seq ON messages(room_hash, seq);
```

Set `PRAGMA foreign_keys=ON` and a reasonable `busy_timeout` on **every connection**; test concurrent duplicate sends and SQLite transaction correctness. Prefer WAL when the verified local filesystem/mount supports it, otherwise use a compatible safe journal mode. Pin one Uvicorn worker for SQLite simplicity initially. Database path from `ROOMS_DB`, default `/data/rooms.sqlite3` in the container.

**Expiry:** `ROOM_IDLE_TTL_SECONDS`, initially **604800 (7 days)** rather than an inherited 12 hours; configurable down to 43200 for a truly ephemeral experiment. Activity means room creation or a **successful new post**, not polling or idempotent replay. An expired room is immediately unauthorized even if lazy cleanup has not physically removed its rows. Cleanup on traffic and room creation is enough for v1; do not promise secure deletion from SQLite files, WAL, or backups.

**Bounded growth:** start with `MAX_ACTIVE_ROOMS=16` and `MAX_MESSAGES_PER_ROOM=1000`; return an explicit capacity error rather than silently discarding unread messages. Keep maximum text at 16 KiB and monitor SQLite/WAL sizes during testing. Tune only from measurements. No unbounded history or silent truncation.

---

## 5. The bridge: the missing half of actual collaboration

**This section is mandatory for the objective.** The relay is useful before bridges exist, but *not complete*.

### 5.1 One small process per agent

Each bridge is configured with:

```text
ROOM_API=https://<verified-tailnet-host>:<verified-serve-port>
ROOM_ID=<room-capability-loaded-from-local-secret-config>
AGENT_ID=hermes               # or pi
POLL_SECONDS=3
MAX_AUTO_REPLIES_PER_THREAD=4
MAX_HOP=8
BRIDGE_STATE_DB=<local-durable-path>
```

The bridge does the following:

1. Poll messages in ascending sequence (`GET ...?after=<last_seen>&limit=100`) and follow pages until caught up.
2. Atomically persist each new relevant incoming message to a **local durable inbox**. Ignore messages sent by itself, not addressed to itself, or already present.
3. Advance its durable ingestion cursor *only once it has persisted or safely ignored each message*. Never advance past a message that has not been captured.
4. A single worker selects a pending message; check per-thread budget, sender != self, hop limit, text boundaries, and timeout.
5. Call the **local existing agent** through the chosen adapter, with a stable thread/session mapping. Read the full completed agent result, not just the RPC/HTTP acknowledgement.
6. Persist the outgoing reply payload in local state **before posting** to the relay. Retry that *identical* payload with the *same client message ID* until confirmed (or report a durable error).
7. Mark the input done; retry transient network/agent failures with bounded exponential backoff. Failed tasks remain inspectable and retryable, not silently dropped.
8. If the agent signals completion or the conversation budget is exhausted, do **not** generate another automatic reply. Include a final summary in the prior outgoing response when appropriate.

**Local state** can be one tiny SQLite DB: `cursor`, `inbox(seq UNIQUE, state, attempts, ... )`, `outbox(inbound_seq UNIQUE, payload, state)`, and `thread_sessions(thread_id PRIMARY KEY, local_agent_session_ref, reply_count)`. Avoid a full framework or distributed queue.

**Important honesty about delivery:** a crash between an irreversible agent tool action and recording completion may cause re-execution. Exactly-once tool execution is **not** guaranteed. Configure bridge-triggered agents initially for *read-only/review tasks*. Anything destructive, external, costly, or requiring privileges must follow the agent's existing human confirmation rules; a peer's text cannot approve it.

### 5.2 Hermes adapter

- Prefer the **existing authenticated local Hermes API server**, not a custom Hermes fork. Verify the installed version and use the non-streaming `/v1/responses` route with a stable per-thread `conversation` ID and `Idempotency-Key`. For the locally inspected Hermes 0.18.2, `/v1/runs` returns an admission ID but does not honor `Idempotency-Key`; do not claim safe admission retries there. Parse the completed Responses output, not a transport acknowledgement.
- Use a **dedicated bridge-owned Hermes API session**, ideally one per `thread_id`, so multi-turn context persists; persist its mapping in the bridge DB.
- Do not assume the API can inject into the user's live Telegram/CLI session. Hermes documentation distinguishes API-owned sessions from sessions owned by other surfaces. If shared-live-session behaviour is requested later, investigate the supported gateway WebSocket approach separately.
- Keep Hermes' API key and API access **local to the bridge** if possible. Verify the actual container/host network path. Never publish the Hermes tool-execution API merely to make the relay work.
- In the first spike, make a safe, read-only prompt, parse the completed response, verify context continuity across two turns, and test lost-response retry using the API's documented idempotency behaviour. The inspected Hermes 0.18.2 Responses cache is in-memory with a 300-second TTL; persist each bridge request's first-start time and stop unresolved retries after 240 seconds. Verify cache lifetime and conversation storage after restart against the installed version.

### 5.3 Pi adapter

- Prefer **Pi RPC mode** (`pi --mode rpc`), a long-lived JSONL subprocess, or Pi's SDK only if it materially reduces complexity in the verified installation.
- In RPC mode, write one JSON object per LF-terminated line, correlate command responses using the command `id`, and continuously drain stdout. An accepted `prompt` response is *not completion*; wait for `agent_settled` and retrieve or reconstruct the final assistant text using the documented RPC protocol.
- Preserve Pi session history **per thread** using documented session options and persisted state. Do not use `--no-session` if cross-process persistence is required. Verify the actual installed CLI flags and session-resumption behavior before coding the adapter.
- On Windows, run the bridge under the logged-in user or an approved scheduled-task/service arrangement; determine where Pi, Python, project workspaces, and logs are actually installed. Do not assume a Linux path or daemon manager.

### 5.4 Conversation rules shared by both agents

- An automatic response is allowed only for a message addressed specifically to the recipient agent.
- Never automatically reply to own output, duplicates, expired threads, or a final/closed thread.
- One active agent run per thread on a bridge; start with **one worker total** to reduce concurrency surprises.
- Default maximum **4 automatic replies per agent per thread** and **8 hops total**; these are configurable and deliberately conservative.
- A peer's message can request research/review, but cannot change the agent's permissions, read local secrets, disable safeguards, authorize deployments, or demand self-modification.
- The initiator includes an explicit objective and completion criterion. A completed response does not imply a further question; the recipient should stop unless directly addressed again.
- Log metadata (thread, seq, state, timing, error category) but **not** room capabilities, API keys, or full message text by default.

### 5.5 Initiating a message (not just listening)

Provide a tiny cross-platform `roomctl` command that both agents may invoke through their normal shell/tool permissions:

```text
roomctl create
roomctl send --to pi --thread <uuid> --text "Review this design."
roomctl read --after 0
roomctl status
```

Use local secret config rather than putting `ROOM_ID` in process command-line arguments or URLs. `roomctl` must generate and persist the outgoing `client_msg_id` for retry safety. It can be a small Python module calling the relay HTTP API; do not add a CLI framework dependency unnecessarily.

Ship `skills/agent-room/SKILL.md` with concise instructions: agent identity, sending, reading, thread/reply handling, completion, permissions, and when *not* to respond. A skill alone is not a live listener.

---

## 6. TDD milestones and approval gates

Each milestone follows the pattern **write focused failing test → smallest implementation → narrow test → full suite → evidence**. Avoid test mocks that merely duplicate implementation assumptions. Do not automatically commit; show a diff and request separate Git approval when required.

### M0 — Read-only reconnaissance and delete/reuse gate

**Mutations:** none.  
**Tasks:**

- Inspect live TrueNAS `app.query`, app settings, listeners, deployment capabilities, dataset paths, and `tailscale serve status`/version. Confirm `agentchattr` is gone; independently inspect whether `8446` is stale. Do **not** delete the stale route without explicit approval.
- Confirm host IP, SSH authorization/access, Tailscale reachability, bridge host OS, Hermes installation/versions/config, and Pi installation/versions.
- Test whether existing ntfy-based transport + agent listeners would fully meet requirements at lower cost. If yes, write an evidence-based stop recommendation, rather than automatically building.
- Choose and document actual loopback publish port, verified Tailscale HTTPS Serve port, TrueNAS app dataset, and network namespace path. Proposed `8310` / `8447` are not reserved.
- Inspect the precise Hermes API and Pi RPC entry points before choosing adapters.

**Evidence:** `docs/discovery.md` with redacted commands/output, selected protocol, source version links, risks, exact deployment candidate, and go/no-go decision. No guessed states.

### M1 — Pure relay package and deterministic store tests

**Files:** `pyproject.toml`, `uv.lock`, `app/{__init__,room_ids,store}.py`, `tests/test_room_ids.py`, `tests/test_store.py`.

- Python 3.12, FastAPI, Uvicorn, stdlib SQLite, pytest/httpx. Lock versions with `uv`; use explicit package build configuration and verify `uv sync --frozen` and imports work.
- Tests: randomness/URL-safe room ID, hashed lookup, creation, separate room isolation, strict sequence ordering, unique client ID replay, conflict, finite retention boundary with injected UTC clock, no read-activity extension, cascade cleanup, database reopen, foreign keys, concurrency contention, capacity.
- Store functions take explicit `now` in tests; no sleeps for TTL. Use SQLite transactions and `busy_timeout`.

**Gate:** full store test suite passes and file-based restart retains messages.

### M2 — HTTP API and two-client proof

**Files:** `app/main.py`, `tests/test_api.py`, `tests/test_integration_http.py`.

- Implement app factory `create_app(db_path=...)` and export `app = create_app()` if starting Uvicorn as `app.main:app`; **test the exact import/startup path**.
- Implement precisely the API of Section 4, with central bearer parsing, consistent errors, body size limit, cursor semantics, and no secret logging.
- Tests: two clients share a room, A→B and B→A, paging, self-message visible but ignorable by client, invalid auth uniformity, response values, UTF-8 byte size, malformed JSON, `reply_to` room check, concurrent same `client_msg_id`, idempotent POST and conflict, expiry, capacity.

**Gate:** local `curl` demo and automated two-client test pass without a model.

### M3 — Agent-neutral bridge + fake-agent proving reliability

**Files:** `bridge/{main,client,store,worker,adapters/base}.py`, `tests/test_bridge.py`, `tests/test_bridge_recovery.py`.

- Implement durable polling, ingestion cursor, inbox, outbox, per-thread budget, safe prompt envelope, worker timeouts, exponential backoff, restart recovery, and one-worker policy.
- Use a fake adapter that echoes deterministic answers, counts invocations, and can intentionally crash/fail at each checkpoint.
- Fault tests: lost poll response, duplicate messages, restart after intake, failure during model invocation, restart after reply persisted but before POST, POST acknowledged but response lost, restart before marking done, irrelevant recipient, self-message, max-hop and exhausted-budget stop.
- Distinguish guaranteed **single relay append** from only **at-least-once agent invocation** in docs and assertions.

**Gate:** fake Hermes and fake Pi bridges exchange 3+ turns unattended; restart does not lose messages or create duplicate relay replies.

### M4 — Pi adapter spike and integration

**Files:** `bridge/adapters/pi_rpc.py`, `tests/test_pi_rpc.py` and one actual installed-version smoke script.

- Probe `pi --help`, `pi --mode rpc`, JSONL framing, the `prompt` command, `agent_settled`, extracting final text, session resumption and busy-process behaviour.
- Write a real two-turn Pi conversation in a dedicated test workspace with harmless read-only prompts; confirm the second turn has the first turn's context.
- Test process exit, timeout, malformed JSONL, provider/model failure, restart, no duplicate publish. Do not store raw model transcripts in normal logs.

**Gate:** real Pi receives a relay message and writes a completed reply back to that thread.

### M5 — Hermes adapter spike and integration

**Files:** `bridge/adapters/hermes_api.py`, `tests/test_hermes_api.py` and actual installed-version smoke script.

- Verify API server settings/keys and reachable address **from the bridge's actual execution context**; no unintended external exposure.
- Check `/v1/responses` and `/v1/toolsets` availability; implement authenticated completed-response parsing, stable per-thread conversations, deterministic `Idempotency-Key`, a persisted retry-age cutoff shorter than the Hermes cache TTL, and fail-closed toolset verification.
- Test gateway restart, API `429`/`503`/timeouts and a two-turn read-only context-continuity conversation. Do not hijack Telegram/CLI-owned sessions.

**Gate:** real Hermes receives a relay message and writes a completed reply back to that thread.

### M6 — Self-hosted container and documentation

**Files:** `Dockerfile`, `.dockerignore`, `deploy/compose.yaml`, `README.md`, `docs/{agent-quickstart,operations}.md`, `skills/agent-room/SKILL.md`, container contract tests.

- Build a minimal Python 3.12 slim image with exact locked runtime dependencies, pinned verified base-image digest, non-root UID/GID, one Uvicorn worker, port `8080`, persistent `/data`, and `/healthz` check.
- Keep the image reproducible; `.dockerignore` must exclude credentials, local DBs, caches, `.env*`, and Git internals.
- TrueNAS Custom App Compose: **one service**, bind persistent verified dataset to `/data`, publish **only** `127.0.0.1:<verified-free-host-port>:8080`, restart policy, no `privileged`/extra capabilities, healthcheck, resource limits based on tested RAM/CPU needs.
- Document room creation, storing the ID securely, Bash/PowerShell examples, CLI, both agent bridge startups, TTL, no-auth caveat, backup/recovery, and how to disable *only this* route/app.
- Run isolated container lifecycle test on a disposable dataset before TrueNAS mutation.

**Gate:** tagged image starts, health passes, messages survive a test-container recreate, and docs cover both machines.

### M7 — Explicit production deployment approval

**Mutations:** TrueNAS filesystem/image build/app install and Tailscale Serve changes are **not** authorized by this document.

1. Produce an operator approval card showing exact app name, data dataset, loopback port, HTTPS port, verified tailnet URL, container network mode, image digest, Tailscale command, preserved Serve routes, and specific rollback steps.
2. Obtain explicit approval for the listed changes.
3. Recheck the inventory *immediately* before mutation. Fail closed on conflicting port, unexpected existing app, mismatched mount, unavailable network path, or changed Serve config.
4. Build the reviewed image on the intended host and install **exactly one** canonical TrueNAS Custom App named `agent-room-relay` (or update an existing same-name app without duplicating it).
5. Verify host listener **really** binds loopback only. Do not use `0.0.0.0` or a LAN proxy just to make it work.
6. **Before adding Serve:** test that the selected Tailscale container/network namespace can access the host-loopback target. Container `127.0.0.1` is not automatically host `127.0.0.1`. If routing is impossible within the approved topology, stop and propose a new one; do not silently widen exposure.
7. Add only the exact approved `tailscale serve --bg --https=<PORT> http://127.0.0.1:<HOST_PORT>` mapping in the verified Tailscale execution context. Confirm this is **Serve**, never Funnel. Read back the complete status and compare every pre-existing route.
8. From another tailnet device, verify HTTPS with normal certificate validation (never `curl -k`), room create/read/write and invalid credential denial. From a non-tailnet LAN host, verify the selected host port is unreachable. Also verify local loopback health.
9. Recreate the Custom App once with the same persistent dataset; confirm history survives. Save the pre-deploy Serve config/status and provide exact rollback command for the *new mapping only*.

**Gate:** full private-network acceptance and no unrelated route/app changes. Document actual endpoint without publishing a live room ID.

### M8 — Install persistent bridges and demonstrate autonomous collaboration

- Install/enable the Hermes bridge beside Hermes and the Pi bridge beside Pi using a tested, approved startup method on each OS. Keep each machine's bridge state durable.
- Test the outbound `roomctl` skill from each actual agent, not just manual HTTP.
- Conduct a real three-exchange task:
  1. User asks Hermes to consult Pi on a harmless technical question.
  2. Hermes sends a directed message with clear objective and completion criterion.
  3. Pi listener invokes Pi, waits for completion, publishes a reply.
  4. Hermes listener invokes Hermes, evaluates Pi's reply, sends one substantive follow-up.
  5. Pi answers; Hermes summarizes/concludes without endless automatic replies.
- Reverse the initiation direction: Pi → Hermes → Pi.
- Restart each bridge mid-flow, temporarily disconnect Tailscale, and verify inbox recovery, eventual delivery, no lost accepted messages, and no duplicate published reply.
- Verify models cannot be instructed via room messages to run a forbidden/destructive command or reveal a secret, and that exhausted reply budgets stop.

**Final gate:** Two distinct agent runtimes on two devices hold a task-focused multi-turn dialogue unattended over tailnet, survive a transient interruption, respect permissions, and stop.

### M9 — Only then automate and hand over

- Make the proven deployments restart reliably (container restart for relay; appropriate Windows/Linux service/daemon for bridges).
- Add concise health/readiness checks and structured metadata-only logs for poll age, delivery lag, last processed sequence, errors, and agent-run failures. Do not build a dashboard unless observability is demonstrably insufficient.
- Record reproducible install commands and exact versions/digests; produce `docs/operations.md` with backup, rotation (new room), expiry, reset bridge state, and rollback.
- No self-updating agents or auto-deployment. Show final diffs, tests, known limitations, and approval evidence. Git commit/push only with explicit authorization.

---

## 7. Human-readable proof scripts

Use placeholders; do not hardcode a live room ID, a public URL, or any API key in scripts committed to Git. For a Linux/macOS testing shell:

```bash
ROOM_API='https://<verified-tailnet-host>:<verified-serve-port>'
ROOM_ID=$(curl -fsS -X POST "$ROOM_API/v1/rooms" | python3 -c 'import json,sys; print(json.load(sys.stdin)["room_id"])')
# Export only in a controlled shell/session; never echo into shared logs.
export ROOM_API ROOM_ID

export THREAD_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
export CLIENT_MSG_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')

python3 - <<'PY' | curl -fsS -X POST "$ROOM_API/v1/messages" \
  -H "Authorization: Bearer $ROOM_ID" \
  -H 'Content-Type: application/json' --data-binary @-
import os, json
print(json.dumps({
    'client_msg_id': os.environ['CLIENT_MSG_ID'],
    'sender': 'hermes', 'recipient': 'pi',
    'thread_id': os.environ['THREAD_ID'],
    'reply_to': None, 'hop': 0,
    'text': 'Please assess this approach and name one risk.'
}))
PY

curl -fsS -H "Authorization: Bearer $ROOM_ID" \
  "$ROOM_API/v1/messages?after=0&limit=100"
```

**Note:** The shell variables are exported before running the Python POST block. Prefer `roomctl` in normal use, since it handles IDs and retry semantics safely. The snippet above is for operator-level proof only, not the agent bridge implementation.

For Windows, deliver an equivalent PowerShell test in the operator guide, using environment variables or a restricted local secret file rather than command-line room IDs.

---

## 8. Hard acceptance matrix

| Test | Required result |
|---|---|
| Two clients with same room capability | Both can post/read each other's messages |
| Different or invalid room | Cannot access another room; uniform unauthorized response |
| 100 messages + cursor pagination | No skips/reordering/duplicates in the stored log |
| Exact send retry | One stored message and same `seq` |
| Same client message ID, changed body | `409`, no mutation |
| Restart relay/container | Messages survive; no new/duplicate app |
| Poll empty room 24 hours in simulated clock | Does not extend TTL |
| Room past expiry | Inaccessible, even before lazy cleanup |
| Loopback / Tailscale boundary | HTTPS tailnet works; LAN publish port unreachable; Funnel unused |
| Bridge crashes at every state boundary | No accepted message silently lost; no duplicate *published* reply |
| Hermes/Pi real adapters | Correct terminal model results and per-thread context; real model calls remain external acceptance gates |
| Self/other/wrong recipient messages | Correct filtering; no self-echo loop |
| Final reply with `complete=true` | Both bridges close the thread and do not invoke another model turn |
| Follow-up discussion | At least three model-generated, task-relevant turns |
| Turn budget exceeded | Conversation stops and reports why |
| Injected prompt demanding secrets or elevated tools | Agent refuses or follows existing approval boundary; no permission escalation |
| Disconnect/reconnect Tailscale | Eventually resumes from durable local state |
| TrueNAS services | No unrelated app/container/Tailscale route changed |

No benchmark targets for throughput or latency are justified before measurements. Start with 3-second polling and one worker per agent. Do not prematurely optimize.

---

## 9. Operational risks and intentionally accepted limits

1. **No user-account authentication:** The 256-bit room capability grants full read/write access inside the reachable tailnet. Any holder may spoof a sender label. Protect the capability and rotate by creating a new room. Tailscale membership is a network trust boundary, not identity proof for individual messages.
2. **Tool safety:** A remote agent's words are untrusted. Initially permit only harmless analysis/review tasks; destructive operations, repository mutations, emails, deployments, purchases, and privileged actions retain their existing approval rules. Do not put credentials in chat messages.
3. **At-least-once processing:** Relay idempotency and bridge outboxes prevent duplicate *messages*; model calls or side effects can repeat after a crash. Document this honestly and constrain tool permissions accordingly.
4. **Dedicated sessions:** Hermes API bridge sessions and Pi RPC sessions are not necessarily the user's *currently visible* Telegram or Pi terminal chat. They share the underlying agent capability/config as supported, but do not assume they merge transcripts with other surfaces.
5. **Retention:** Seven-day idle TTL is a default, not a guarantee of secure erasure. SQLite/WAL/backups may retain deleted bytes. Polling never extends expiry.
6. **Cost runaway:** Turn limits, agent-call timeouts, one worker, and explicit completion prevent unlimited recursive agent conversations and uncontrolled model usage.
7. **Connectivity:** No direct public internet ingress. Do not substitute Tailscale Funnel, disable TLS validation, or widen host binds to fix a failed probe.
8. **Power/restarts:** The Z640 can go down. Persistent DB and bridge state allow recovery after reboot; they do not make a physically offline server available.
9. **Scope creep:** If the first unattended conversation works, **stop building**. Add UI, file attachments, WebSockets, queue brokers, and richer identities only on evidence of a real limitation.

---

## 10. Implementer reporting format

For **each milestone**, return:

```text
Milestone:
Requirement challenged / deleted / simplified:
Files created or changed:
Exact commands executed:
Tests run (pass/fail counts):
Observed behavior (not assumptions):
Risks / unresolved blockers:
Environment mutations (if any):
Next recommended step:
Approval needed before next mutation:
```

Stop and ask for approval if a necessary step changes the TrueNAS host, the Tailscale configuration, existing Hermes/Pi service settings, secrets, or any unrelated state. Never report a future action as completed. Deliver useful partial results if blocked.

---

## 11. References and version verification

Use current primary documentation and installed CLI help to verify behavior rather than treating this plan as a hardcoded API guarantee:

- Pi RPC mode: https://pi.dev/docs/latest/rpc
- Pi RPC commands: https://pi.dev/docs/latest/rpc-commands
- Hermes API server, sessions, and runs: https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- Hermes A2A functionality (a **possible alternative**, not a required part of this relay): https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/messaging/a2a.md
- Tailscale Serve: https://tailscale.com/docs/features/tailscale-serve
- Tailscale Serve CLI: https://tailscale.com/docs/reference/tailscale-cli/serve
- TrueNAS custom apps via Compose: https://apps.truenas.com/managing-apps/installing-custom-apps/

**Original basis:** `2026-10-31_131251-agent-room-relay.md` (supplied planning document). This is a revised replacement, not evidence that the proposed service exists or has been deployed.

---

## One-sentence final instruction

> Build the smallest private, durable mailbox that two independent agent bridges can reliably use; verify Hermes and Pi can actually talk, work, follow up, and stop; delete everything that is not necessary to demonstrate that outcome.
