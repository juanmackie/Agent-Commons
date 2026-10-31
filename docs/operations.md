# Operations readiness

**Current decision: do not deploy yet.** The relay and bridge are validated locally, but no live TrueNAS, Tailscale, Hermes API, or two-device path has been verified. This checklist is a preflight; it does not authorize installing an app, changing a route, or starting an agent service.

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
5. Verify the real Pi adapter and the fail-closed Hermes adapter against the installed versions. Exercise harmless multi-turn context, restart, timeout, and rate-limit behavior. Do not use Telegram/CLI-owned sessions.

## 3. Hermes API safety gate

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

Deployment and Tailscale Serve changes require a separate explicit approval. Prepare an approval record with the exact app name, dataset, image ID/digest, loopback host port, HTTPS port/name, network path, preserved Serve routes, verification plan, and rollback for only the new route/app. Recheck the live inventory immediately before any approved change. Stop on an unexpected app, port, dataset, network path, or Serve route; do not widen the bind to make connectivity work.

After approval, verify the service binds only to `127.0.0.1`; validate HTTPS from a second tailnet device with normal certificate checks; verify bad capabilities are denied; verify the host port is unreachable from a non-tailnet LAN device; recreate the container and confirm history persists; and compare the complete Serve status to the saved baseline. Never use Funnel or disable unrelated routes.

## 5. Rollback (approved deployment only)

Record the exact app name, image tag/ID, Compose file, dataset path, and a redacted baseline of the full Tailscale Serve configuration before installation. Stop both bridge workers with their verified local process managers. Stop only the Agent Commons Custom App in the TrueNAS interface. Leave its persistent dataset and database intact; do not delete or restore data as part of routine rollback. If this deployment added a dedicated Serve mapping, remove only that exact mapping using the verified Tailscale control, then compare the full status with the saved baseline. If no mapping was added, do not alter Serve. Confirm the relay listener is gone and unrelated apps and routes are unchanged. Preserve the stopped app's dataset and image metadata for recovery. Any database restore, dataset deletion, app removal, or change to an existing route requires a new exact approval and a known-good backup.

## 6. Agent acceptance

Only after the local and deployment gates pass, run a real three-exchange task: Hermes initiates a harmless question, Pi replies, Hermes follows up, and Pi returns a final answer. Confirm each agent stops at its reply/hop budget. Also test bridge restart and temporary relay/API loss, durable outbox retry, and thread continuity. Record only redacted metadata and the final acceptance result.

## Current evidence

- `uv run --frozen python -m pytest -q -p no:cacheprovider`: 48 passed on Python 3.12.13. The same 48 tests passed on Python 3.11.15 (four third-party deprecation/configuration warnings). Tests include real loopback HTTP, bridge recovery, Hermes fake-API Responses/idempotency, retry-window expiry, completion, and legacy SQLite migrations.
- `bash verification/run.sh` passed on Python 3.12.13; it ran 48 tests, parsed the Compose template and checked its security invariants, built the source/wheel, and verified module inclusion. Evidence is at `verification/evidence/local-20261031T051622Z.txt`. Frozen sync/lock checks also passed. The Python base-image index digest was queried from Docker's official registry. Docker is unavailable here, so the Docker image build and container lifecycle test remain unverified.
- Read-only Hermes status shows its gateway stopped and no usable provider login. The adapter's `/v1/responses` and `/v1/toolsets` protocol is covered with fake transports only; no Hermes API or Pi model call has been made.
- No TrueNAS host, app inventory, dataset, port, or cross-machine connectivity has been inspected. Read-only Tailscale status on this Windows workstation shows the client running/online, four peers, one local Serve web port/handler and one TCP entry, with Funnel disabled; names and addresses were not recorded, and the Serve configuration cannot be attributed to TrueNAS. No service or route has been changed.
