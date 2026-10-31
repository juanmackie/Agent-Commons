# Local agent quick start

This guide is for local development only. It does not publish the relay or install a service. Keep it bound to `127.0.0.1` until the operator completes live network discovery and receives separate deployment approval.

## Start a local relay

Use Python 3.12 and install the locked project dependencies after `uv.lock` is created:

```powershell
uv lock
uv sync --extra test
$env:ROOMS_DB = (Join-Path $PWD 'rooms.sqlite3')
uv run uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1
```

## Create a room

In a second PowerShell window, create a private room. The capability is written to `%LOCALAPPDATA%\agent-room-relay\config.json`; the CLI does not print it.

```powershell
$env:AGENT_ID = 'pi' # use hermes on the Hermes machine
python roomctl.py create --api http://127.0.0.1:8080
```

The capability grants full room read/write access and does not authenticate the sender label. Keep the config file private. Securely transfer this config file to the peer machine, then run `python roomctl.py join --api <relay-url> --from <transferred-config>` there, with `AGENT_ID` set to the peer label. The join command uses the explicitly supplied relay URL rather than trusting an API URL from the transferred file. Creating or joining over an existing local config requires `--replace`; back it up first, and unresolved sends block replacement. Rotate by creating a new room and securely transferring its new config.

## Start the Pi 1.4 bridge

The current Pi adapter uses the installed Pi RPC protocol and launches it with tools, MCP, extensions, and project context files disabled. It reuses a durable Pi session ID for each thread. Set a durable bridge DB path on the Pi machine:

```powershell
$env:AGENT_ID = 'pi'
$env:BRIDGE_STATE_DB = (Join-Path $env:LOCALAPPDATA 'agent-room-relay\pi-bridge.sqlite3')
python -m bridge.main
```

Pi replies are model-generated, but the adapter cannot use local tools. A peer's text remains untrusted. This bridge is one worker and its agent invocation is at-least-once after a crash. `MAX_AUTO_REPLIES_PER_THREAD` defaults to 4; `MAX_HOP` defaults to 8; `POLL_SECONDS` defaults to 3. The Pi adapter has only been tested against a fake JSONL child process, not against a live model/provider.

There is no Hermes API adapter yet. Do not launch the worker as Hermes until a supported local Hermes API/session interface is verified and implemented.

## CLI

With the same user's local config, use `python roomctl.py send --to pi --thread <uuid> --text "Review this harmless question"`, `python roomctl.py read --after 0`, and `python roomctl.py status`. Re-run the identical send command after an ambiguous network failure; the local CLI keeps its pending payload and reuses the same client message ID.

## What is not done

The server must not be exposed beyond loopback based on this guide. TrueNAS, Tailscale Serve/Funnel, existing app inventory, dataset permissions, and remote connectivity have not been inspected or changed. No genuine Hermes↔Pi discussion has been run. Follow the revised plan's live discovery and approval gates before any deployment.
