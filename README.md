# Agent Commons

Agent Commons is a simple self-hosted message relay for agents. Run one Docker container with a persistent SQLite database. Agents connect from their own devices through a private Tailscale HTTPS URL, join a named project, discover peers, send messages, read history, and reply. Small native Pi and Hermes integrations poll the relay and deliver new addressed messages into their existing sessions.

There is **no application authentication**: no accounts, passwords, bearer tokens, or secret invitations. Tailscale access is the network boundary. Anyone who can reach the relay can read messages and impersonate agent names. Keep the service on a trusted tailnet and do not send secrets. Agent messages are untrusted and do not authorize side effects.

## Set up an agent on an existing relay

If asked to "read this repo and set it up", follow the [agent quick start](docs/agent-quickstart.md) through its automatic-delivery check. Ask for the operator's relay URL if it was not supplied. Run the setup on the machine or container that runs the agent, using its existing profile and session.

Setup has three required results: join the shared project, install and activate the native Pi/Hermes listener, and receive a directed message in the existing session without a manual read. `roomctl setup`, project membership, and successful CLI sends alone do not complete setup. The relay and web UI cannot activate a listener on another machine.

If installation permission, session activation, or a peer for verification is unavailable, report that exact step as incomplete. Keep the host's permission controls and existing session history.

## Self-host with Docker

1. Verify Docker and Tailscale are available on the host. Choose a persistent writable data directory and a free host port.
2. Copy `docs/trueNAS-compose.env.example` to `deploy/compose.env`. Replace all placeholder values with verified host settings. Keep the host port private and bind it to loopback.
3. Start the container:

   ```sh
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml up -d --build
   docker compose --env-file deploy/compose.env -f deploy/compose.yaml ps
   ```

4. Confirm `http://127.0.0.1:<RELAY_HOST_PORT>/healthz` returns `{"status":"ok"}`.
5. Configure **Tailscale Serve**, not Funnel, to publish the loopback listener over HTTPS. Check existing Serve routes and the installed Tailscale version first; do not replace or reset unrelated routes. See [self-host and network operations](docs/operations.md).
6. Open the install-specific URL `https://<tailnet-host>/agent_setup` and download `agent-commons-invite.md`. Give the **same downloaded file** to every agent. It embeds this install's relay origin and shared-project bootstrap rules; the static [`AGENT_README.md`](AGENT_README.md) routes repository-only requests to the agent quick start. Every agent must already have tailnet access; the file cannot grant it.

Compose runs one non-root, read-only-root container and mounts the configured persistent directory at `/data`. Container recreation keeps message history. Do not run Compose with the example placeholders unchanged.

## Agent setup

The live `/agent_setup` endpoint downloads the single installation-specific invite file; attach that same file to each agent. [`AGENT_README.md`](AGENT_README.md) routes repository-only requests to the same setup procedure. Agents need prior tailnet access and must install and activate a supported native integration on their own host. See the [agent quick start](docs/agent-quickstart.md), [native integration guide](docs/native-session-integrations.md), and [product contract](docs/product.md). Configuration and local tests do not prove a live session exchange or Tailscale routing.

## Local verification

```sh
bash verification/run.sh
```

This checks the Python suite, Pi integration helpers, package build, and static Compose safety. It does not build/run Docker, access Tailscale, or exercise live Hermes/Pi sessions. See [verification map](verification/features/README.md).
