# Handoff for the Hermes setup agent

## Outcome

Deploy the Agent Commons relay as one private TrueNAS Custom App. Then connect the Hermes and Pi bridges so they can exchange a bounded, harmless task without a human forwarding messages. The relay is a mailbox. Each agent runs on its own host.

This document is a procedure, not proof that the app, host, or route exists. Do not assume that an old app, port, dataset, IP address, or Tailscale route still exists.

## Read first

Read these files in order:

1. `README.md`
2. `agent-room-relay-revised-plan.md`
3. `docs/operations.md`
4. `deploy/compose.yaml`
5. `docs/agent-quickstart.md`
6. `docs/discovery.md`

Use the installed Hermes and Pi versions as the source of truth for their runtime interfaces. The local development tests use a fake Hermes API and a fake Pi process. They do not prove that a live model call works.

## Apply the five-step method

Apply the five laws attributed to Elon Musk in this order, without weakening safety or removing requested behavior:

1. **Question requirements.** Verify the smallest architecture and actual TrueNAS, Tailscale, Hermes, and Pi requirements. Do not treat plan examples as host facts.
2. **Delete unnecessary parts.** Keep one relay and two small local bridges. Do not add a UI, public ingress, broker, or orchestrator without evidence that it is required.
3. **Simplify.** Keep the SQLite mailbox, one relay process, one worker per bridge, and explicit per-thread completion and reply limits.
4. **Accelerate feedback.** Verify the lock, run tests, and test each local boundary before any host change.
5. **Automate only proven steps.** Add no service startup or route automation until the manual flow has passed acceptance.

## Work sequence

1. **Inspect without changes.** Record the live TrueNAS version, app inventory, ports, datasets, ownership/ACLs, Tailscale Serve state, network path, Hermes version/API status, Pi version, and each bridge's execution host. Keep output redacted. Stop if an unexpected app, route, port, dataset, or permission appears.
2. **Verify the source.** Use Python 3.12 and `uv sync --frozen --extra test`, then run `uv run --frozen python -m pytest -q -p no:cacheprovider`. Build the image from the pinned `Dockerfile`; record the source revision and image ID. The current workspace has no Docker CLI, so the image and container lifecycle have not been tested here.
3. **Prepare the exact change card.** Select a verified dataset and a free host port. Set CPU and memory limits from measurements. Confirm that the chosen Tailscale execution context can reach the host's loopback listener. Fill the required Compose values only after those checks. Preserve all existing apps and Serve routes. Do not use Funnel or bind to a LAN address.
4. **Wait at the deployment gate.** Present the exact app, dataset, image ID, loopback port, resource limits, network path, any proposed Tailscale route, validation, and rollback. Do not install or alter a route until the owner approves the exact changes. If any live fact differs from the change card, stop and prepare a new one.
5. **Install the relay only after approval.** Use the TrueNAS Custom App Compose interface. Run as UID/GID 10001; grant only the verified dataset path at `/data`. Confirm loopback binding, health, room create/read/write, invalid-capability denial, LAN denial, and message persistence after a disposable recreation. Never modify or reset an unrelated Tailscale route.
6. **Configure Hermes safely.** Enable the authenticated Hermes API in its protected local environment with `API_SERVER_ENABLED=true` and a strong `API_SERVER_KEY`; start the normal Hermes gateway and verify its version, readiness, and loopback bind address. Keep the key in the host's protected secret mechanism. The relay bridge processes peer text as untrusted input. Before starting it, configure the API platform with no tools and no MCP servers. In Hermes `config.yaml`, the locally inspected version uses `platform_toolsets.api_server: [no_mcp]`; enumerate all installed plugin toolsets and record them as known-but-disabled. Verify every row from authenticated `GET /v1/toolsets` is disabled, and independently inspect the config to confirm `no_mcp` because that endpoint does not report the sentinel. Set `HERMES_API_TOOLSETS_VERIFIED=none` only after these checks. The bridge checks the reported toolsets on every run, but this acknowledgment is a human assertion. Recheck it after every Hermes configuration, plugin, MCP, or version change. The bridge sends non-streaming requests to `/v1/responses` with a stable per-thread `conversation` ID and `Idempotency-Key`; it does not use `/v1/runs`, which does not honor that key in the inspected 0.18.2 source. That Responses idempotency cache is in memory with a 300-second TTL. The bridge persists the first request time, bounds the API timeout by the remaining 240-second window, and stops an unresolved retry at that limit. The cache is process-local: if the Hermes gateway restarts, it may forget a request key before the 240-second cutoff and a retry may launch a second inference. Exactly-once model execution is not guaranteed; keep tools/MCP disabled and inspect pending/failed bridge state after a gateway restart. An item failed with `idempotency_window_expired` requires operator review before retry, because the API may already have completed it. Verify these endpoint and TTL details against the installed Hermes version. Do not expose the Hermes API through Tailscale.
7. **Configure Pi safely.** Run the Pi bridge under its approved local user. It starts RPC with tools, MCP, extensions, and project context disabled. Use a durable bridge DB and session directory. Do not put the room capability or provider key in process arguments or messages.
8. **Prove collaboration.** Run a harmless, clearly scoped task in each direction. Confirm the initiating agent receives a reply, can ask one follow-up, and receives a final answer with `complete=true`. Confirm both bridges then stop responding in that thread. Test relay interruption, bridge restart, durable outbox retry, and turn/hop limits. Use real installed models for this final gate; fake-adapter tests do not count.
9. **Make only proven operations persistent.** After manual acceptance, configure approved restart methods. Record backup, room rotation, dataset recovery, and rollback for only this app and any newly approved route.

## Rollback (only after an approved deployment)

1. Save the exact app name, image tag/ID, Compose input, dataset path, and redacted baseline of all Tailscale Serve routes.
2. Stop both bridge workers with their verified local process managers.
3. Stop only the Agent Commons Custom App. Keep its persistent dataset and database; routine rollback does not delete or restore data.
4. If this deployment added a dedicated Serve route, remove only that exact route and compare full Serve status with the saved baseline. If no route was added, make no Tailscale change.
5. Confirm the relay listener is gone and all unrelated apps/routes are unchanged. Preserve the dataset and image metadata for recovery.

App removal, dataset deletion, database restore, or changing an existing route requires separate exact approval and a verified backup. Do not run these steps on an unapproved deployment.

## Stop conditions

Stop and ask the owner before any destructive or externally visible change, any secret/configuration change, any route change, any port or dataset conflict, any broad permission change, or any state that differs from the approved change card. Do not guess missing values or widen network exposure to make a test pass. Never report a skipped live test as passed.

## Final report

Return the exact source revision and image ID; app/dataset/port/network facts; redacted command results; test and container results; both real conversation outcomes; persistence/restart evidence; changed or preserved routes; unresolved risks; and the exact rollback procedure. Do not include room capabilities, API keys, OAuth data, or full conversation text.
