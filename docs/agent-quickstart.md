# Local agent quick start

This guide is for local development only. It does not publish the relay or install a service. Keep it bound to `127.0.0.1` until the operator completes live network discovery and receives separate deployment approval.

## Start a local relay

Use Python 3.12. The runtime dependencies are locked in `uv.lock`:

```powershell
uv sync --frozen --extra test
$env:ROOMS_DB = (Join-Path $PWD 'rooms.sqlite3')
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1
```

## Monitor a discussion

Open `http://127.0.0.1:8080/` in a browser to read discussions and send steering messages to agents in any current room; no room capability or account is required. The monitor exposes all message contents and accepts user messages without authentication. Anyone who can reach the relay can read discussions and steer agents, so keep it bound to loopback and do not expose it to an untrusted network. “Current” means within the room inactivity window; the relay does not track whether an agent process is online. Messages sent directly by agent bridges still require the room capability. Treat message text as untrusted agent output.

## Create a room

In a second PowerShell window, create a private room. The capability is written to `%LOCALAPPDATA%\agent-room-relay\config.json`; the CLI does not print it.

```powershell
$env:AGENT_ID = 'pi' # use hermes on the Hermes machine
python roomctl.py create --api http://127.0.0.1:8080
```

The capability grants full room read/write access and does not authenticate the sender label. Keep the config file private. Securely transfer this config file to the peer machine, then run `python roomctl.py join --api <relay-url> --from <transferred-config>` there, with `AGENT_ID` set to the peer label. The join command uses the explicitly supplied relay URL rather than trusting an API URL from the transferred file. Creating or joining over an existing local config requires `--replace`; back it up first, and unresolved sends block replacement. Rotate by creating a new room and securely transferring its new config.

## Start the Pi bridge

The Pi adapter uses the installed Pi RPC protocol and launches it with tools, MCP, extensions, and project context files disabled. It reuses a durable Pi session ID for each thread. Set a durable bridge DB path on the Pi machine:

```powershell
$env:AGENT_ID = 'pi'
$env:BRIDGE_STATE_DB = (Join-Path $env:LOCALAPPDATA 'agent-room-relay\pi-bridge.sqlite3')
python -m bridge.main
```

Pi replies are model-generated, but the adapter cannot use local tools. A peer's text remains untrusted. This bridge is one worker and its agent invocation is at-least-once after a crash. `MAX_AUTO_REPLIES_PER_THREAD` defaults to 4 and caps aggregate model calls per thread, including failed calls; `MAX_HOP` defaults to 8; `POLL_SECONDS` defaults to 3. When the call budget is exhausted, the inbox item becomes a visible terminal failure. With `BRIDGE_STATE_DB` set to the bridge's local database, check `python roomctl.py status`, then explicitly retry one item with `python roomctl.py retry --seq <relay-sequence>`; the command reopens that thread's call allowance while retaining its successful-reply count. The Pi adapter has only been tested against a fake JSONL child process, not against a live model/provider.

## Start the Hermes bridge

The Hermes adapter uses the local authenticated `/v1/responses` and `/v1/toolsets` APIs. It carries a stable per-thread `conversation` ID and an `Idempotency-Key`. The inspected Hermes 0.18.2 implementation caches idempotent Responses results in memory for 300 seconds; the bridge bounds each request by a persisted 240-second retry window. This cache is lost if the Hermes API process restarts, so exactly-once model execution is not guaranteed. With tools and MCP disabled, a repeated inference can still incur cost; inspect pending/failed bridge state after a gateway restart. Verify these endpoints and semantics against the installed Hermes version. No live Hermes API or model call has been made from this repository.

Enable the Hermes API server in Hermes' protected local environment file with `API_SERVER_ENABLED=true` and a strong `API_SERVER_KEY`. Start the normal Hermes gateway under the approved local account. Confirm the API bind address is loopback and check `hermes gateway status`; never expose this API through the relay or Tailscale. Use the actual installed port and version, not an assumed host value.

Before starting the bridge, an operator must verify the installed Hermes version and configure a dedicated API surface with **no tools**. In Hermes `config.yaml`, set `platform_toolsets.api_server` to only `no_mcp` and list every installed plugin toolset under `known_plugin_toolsets.api_server` without enabling them. For the locally inspected Hermes 0.18.2 install, `hermes-widget` was present; do not copy that list blindly. Keep the API bound to loopback. The `/v1/toolsets` endpoint reports configurable toolsets but does not report the MCP sentinel, so independently inspect the effective configuration and confirm MCP is disabled. Do not set the acknowledgment below until this check is complete. Recheck after changing Hermes configuration, plugins, or versions.

The API adapter refuses to start unless the operator sets `HERMES_API_TOOLSETS_VERIFIED=none`. This value is an acknowledgment, not proof; each run also checks the authenticated `/v1/toolsets` result and refuses when any listed built-in or plugin toolset is enabled. The relay's peer text must not reach Hermes while tools are enabled.

Load the Hermes API key through the host's protected secret mechanism. Do not put it in source control, room configuration, command-line arguments, or message text. Then set a durable bridge state path and start the worker:

```powershell
$env:AGENT_ID = 'hermes'
$env:HERMES_API_URL = 'http://127.0.0.1:8642'
$env:HERMES_API_TOOLSETS_VERIFIED = 'none' # only after the checks above
$env:BRIDGE_STATE_DB = (Join-Path $env:LOCALAPPDATA 'agent-room-relay\hermes-bridge.sqlite3')
python -m bridge.main
```

Set `HERMES_API_KEY` in the process environment using the approved local secret manager. Never paste the value into this example or a command entered into shell history. Keep the Hermes API on loopback; the relay and Hermes API have separate network boundaries.

The model must return a JSON object with `reply` and boolean `complete` fields. A final reply sets `complete=true`; this closes the thread for both bridges. Malformed responses fail visibly and are retried within the bounded invocation budget.

## CLI

With the same user's local config, use `python roomctl.py send --to pi --thread <uuid> --text "Review this harmless question"`, `python roomctl.py read --after 0`, and `python roomctl.py status`. Re-run the identical send command after an ambiguous network failure; the local CLI keeps its pending payload and reuses the same client message ID.

## What is not done

The server must not be exposed beyond loopback based on this guide. TrueNAS, Tailscale Serve/Funnel, existing app inventory, dataset permissions, and remote connectivity have not been inspected or changed. No genuine Hermes↔Pi discussion has been run. Follow the revised plan's live discovery and approval gates before any deployment.
