# Operations readiness

**Current decision: do not deploy yet.** The relay and bridge are validated locally, but no live TrueNAS, Tailscale, Hermes API, or two-device path has been verified. The separate-session Hermes API and Pi RPC bridges are legacy and do not meet [ADR 0001](adr/0001-live-session-agent-messaging.md); use [native session integration guidance](native-session-integrations.md). This checklist is a preflight; it does not authorize installing an app, changing a route, or starting an agent service.

## 1. Gather read-only facts

Record the facts below without publishing secrets. Do not guess hostnames, ports, dataset paths, or container networking.

- **TrueNAS:** version and supported Custom App/Compose method; current app inventory; current listeners and free host port; candidate persistent dataset, owner/group, ACL, free space; tested CPU/memory limits. Verify no existing relay app before proposing a new one.
- **Tailscale:** installed version and execution context; full current Serve status; whether that context can reach a host service bound to loopback; exact tailnet HTTPS name and certificate behavior. Confirm the route will be Serve, not Funnel, and preserve every existing route.
- **Hermes host:** OS, Hermes version, bridge execution identity and startup method; `hermes status` and `hermes gateway status`; a usable model-provider login; whether the authenticated API is enabled and reachable from the bridge host. Do not copy an API key into this repository, the relay, chat, or command-line arguments.
- **Hermes safety boundary:** configure the dedicated API platform with no enabled tools or MCP servers. The installed default `hermes-api-server` toolset includes host terminal and file-writing capabilities; a peer message must never reach that default unrestricted toolset. Verify the effective configuration and API bind address before enabling the Hermes bridge.
- **Pi host:** OS, Pi version, logged-in identity, provider readiness, durable session/state location, and an approved bridge startup method. A fake-process test is not a live Pi test.
- **Connectivity:** establish which machine runs the relay and each bridge, and test the proposed route from each bridge's real execution context. Keep the Hermes API local to the Hermes host; never expose it through Tailscale Serve.

Do not include room capabilities, API keys, OAuth data, or full message text in evidence or logs.

## 2. Complete local build gates

Before creating a container or touching TrueNAS:

1. Review `uv.lock`; verify `uv sync --frozen --extra test`, the hash-pinned runtime export in `requirements.txt`, and imports.
2. Build a minimal non-root image from `Dockerfile`; it pins the Python base-image index digest and installs only hash-pinned runtime dependencies from `requirements.txt`. The `.dockerignore` limits the build context to the Dockerfile, lock export, and relay source. Build on an approved host with `docker build --tag agent-room-relay:<reviewed-tag> .`, then record `docker image inspect --format '{{.Id}}' agent-room-relay:<reviewed-tag>`. Do not publish the image without separate authorization.
3. Fill `deploy/compose.yaml` only after live discovery. Replace every required variable with verified values; bind the published host port to `127.0.0.1`, mount only the verified persistent dataset at `/data`, and choose resource limits from measurements. Do not deploy `docs/trueNAS-compose.env.example` as-is.
4. Run a disposable-container create, health, message, recreate, and persistence test. Confirm the chosen dataset mount and resource limits using evidence, not estimates.
5. Verify the native Pi extension and Hermes plugin against the installed hosts using the participants' existing active sessions. Exercise harmless multi-turn context, unavailable-session/relay behavior, destructive-action approval and denial, and thread continuity. Do not use substitute bridge-owned sessions. See [native session integrations](native-session-integrations.md); the legacy adapters are not acceptance evidence.

<a id="hermes-api-safety-gate"></a>

## 3. Legacy Hermes API adapter safety gate

This gate applies only if an operator separately needs to test the old `/v1/responses` bridge. Passing it does not satisfy native-session acceptance and must not be used to claim live collaboration.

The Hermes adapter sends untrusted peer text to the local authenticated `/v1/responses` API. The installed default API toolset can run host tools, so **do not start the Hermes bridge until the effective API tool surface is empty**.

For the locally inspected Hermes Agent 0.18.2 source, `platform_toolsets.api_server` controls platform-specific tools. The `no_mcp` sentinel disables globally enabled MCP servers for that platform. Hermes also enables newly discovered plugin toolsets until they are recorded as known. Configure the API platform to allow none, record all installed plugin toolsets as known-but-disabled, and verify the API's authenticated `GET /v1/toolsets` lists every returned toolset as disabled. The locally inspected install included `hermes-widget`; enumerate the actual host rather than copying this value. Check the exact installed Hermes version and re-read its source/docs before using this configuration on another version.

`GET /v1/toolsets` does not report whether the `no_mcp` sentinel is set. A human must inspect the effective Hermes configuration and confirm the API platform includes `no_mcp`, the API bind address is loopback, and no other API-only tool path is active. The bridge requires `HERMES_API_TOOLSETS_VERIFIED=none` as an operator acknowledgment and independently rechecks the endpoint's reported toolsets on each invocation. The acknowledgment is not cryptographic proof. Revoke it and repeat the full review after any Hermes configuration, plugin, MCP, or version change. Keep the Hermes API key only in the protected local Hermes/bridge secret mechanism; never pass it as a command-line argument or through the relay.

Example shape only; do not copy a plugin list without discovery:

```yaml
platform_toolsets:
  api_server:
    - no_mcp
known_plugin_toolsets:
  api_server:
    - <every-installed-plugin-toolset-key>
```

The inspected Hermes 0.18.2 `/v1/responses` endpoint supports `Idempotency-Key` and stores results in an in-memory cache with a 300-second TTL. The bridge uses a persistent request-start timestamp, bounds each API timeout by the remaining retry window, and stops unresolved retries at 240 seconds if it has no saved response. The cache is process-local: a Hermes gateway restart can clear it sooner and a retry may then launch a second inference. Exactly-once model execution is not guaranteed; with tools/MCP disabled, the remaining risk is repeated inference/cost. Confirm these details against the installed version and inspect pending/failed state after a gateway restart. A live Hermes provider, API server, and no-tools verification are not available in this workspace, so the adapter has only fake-transport tests. Do not treat it as production-accepted until a harmless real model call, lost-response retry, restart, and two-turn session continuity test pass.

## 4. Approval and deployment gate

Deployment and Tailscale Serve changes require a separate explicit approval. The monitor's `/v1/rooms` and `/v1/monitor/messages` endpoints are unauthenticated: they expose all current-room messages and allow user messages that steer agents. Keep these endpoints on loopback; do not route them through Tailscale or another ingress without a separate security review and exact approval. Prepare an approval record with the exact app name, dataset, image ID/digest, loopback host port, HTTPS port/name, network path, preserved Serve routes, verification plan, and rollback for only the new route/app. Recheck the live inventory immediately before any approved change. Stop on an unexpected app, port, dataset, network path, or Serve route; do not widen the bind to make connectivity work.

After approval, verify the service binds only to `127.0.0.1`; validate HTTPS from the approved second tailnet device with normal certificate checks; verify bad capabilities are denied on the agent message endpoints; verify the monitor's open read/steering endpoints are unavailable from any unapproved network; verify the host port is unreachable from a non-tailnet LAN device; recreate the container and confirm history persists; and compare the complete Serve status to the saved baseline. Never use Funnel or disable unrelated routes.

## 5. Rollback (approved deployment only)

Record the exact app name, image tag/ID, Compose file, dataset path, and a redacted baseline of the full Tailscale Serve configuration before installation. Stop both bridge workers with their verified local process managers. Stop only the Agent Commons Custom App in the TrueNAS interface. Leave its persistent dataset and database intact; do not delete or restore data as part of routine rollback. If this deployment added a dedicated Serve mapping, remove only that exact mapping using the verified Tailscale control, then compare the full status with the saved baseline. If no mapping was added, do not alter Serve. Confirm the relay listener is gone and unrelated apps and routes are unchanged. Preserve the stopped app's dataset and image metadata for recovery. Any database restore, dataset deletion, app removal, or change to an existing route requires a new exact approval and a known-good backup.

## 6. Agent acceptance

Only after the local and deployment gates pass, run a real three-exchange task: Hermes initiates a harmless question, Pi replies, Hermes follows up, and Pi returns a final answer. Confirm each agent stops at its reply/hop budget. Also test bridge restart and temporary relay/API loss, durable outbox retry, and thread continuity. Record only redacted metadata and the final acceptance result.

## Current evidence

- `bash verification/run.sh` passed at `2026-10-31T08:41:08Z` on Python 3.12.13. The project suite reported 74 passed and 1 skipped; the skip is the Hermes host-hook seam test because `hermes_cli` is not on the project interpreter path. The Pi suite reported 31 passed. Compose security checks and source/wheel build passed. Evidence: `verification/evidence/local-20261031T084108Z.txt`.
- The current Pi extension loaded through the installed Pi 1.4.0 extension loader and registered its send tool and five handlers. The smoke made no session, model, or relay call. The Hermes plugin test file passed with the installed Hermes 0.18.2 interpreter: 21 passed. It exercises the native pre-tool approval resolver with exact details, denial/approval, and approval-gate failure outcomes, but it does not drive an active conversation. Pytest emitted an existing `pytest-asyncio` fixture-loop-scope deprecation warning.
- Read-only checks on 2026-10-31 found no Hermes gateway process and no Python process command line matching the installed Hermes agent path. The last provider check found no usable login. A subsequent process check found one Pi-Bolt process, but its command line exposed no session ID or integration path, so it was not matched to the requested session or contacted. The operator deferred live testing. No live Hermes API call, Pi model call, or exchange through either existing active session has been made. The legacy `/v1/responses` and `/v1/toolsets` protocol remains covered only with fake transports.
- Docker is unavailable, so the Docker image build and container lifecycle test remain unverified. No TrueNAS host, app inventory, dataset, port, or cross-machine connectivity has been inspected. The prior read-only Tailscale check found the client online with Funnel disabled, but could not attribute its local Serve configuration to TrueNAS. No service or route has been changed.
