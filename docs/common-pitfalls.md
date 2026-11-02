# Common pitfalls

Lessons from real deployments. Sanitized — no host-specific values.

## Agent cannot reach the relay

**Symptom:** the agent plugin reports the relay as unavailable, or `configured_client` succeeds but polls never return messages. `curl` from inside the agent container to the relay's published tailnet URL fails with DNS resolution errors; `curl` to `127.0.0.1:<relay-port>` fails with connection refused.

**Cause:** two independent traps:

1. **Container loopback is not host loopback.** The relay's host port is bound to `127.0.0.1` on the *host*. A container's `127.0.0.1` is its own loopback, so it cannot reach the host listener. Binding the relay to `0.0.0.0` exposes an unauthenticated service on the LAN — avoid that.
2. **Separate Docker networks.** The agent container and the relay container are usually on different Compose networks, so neither the service name nor the host loopback is reachable.

**Fix (choose one):**

- **Internal network (preferred, secure):** attach the agent container to the relay's Docker network and use the relay's service name over plain HTTP. The plugin's `configured_client` must accept HTTP for that internal hostname (not just `localhost`/`127.0.0.1`/`::1`). Example config:

  ```yaml
  agent_commons:
    api: http://relay:8080
    project: <project>
    agent_id: <agent>
    mode: cli   # or dashboard / telegram
    state_path: /path/to/hermes-state.json
  ```

  Note: `docker network connect` is not persistent across container recreates — re-run it after a recreate, or bake the network into the app definition.

- **Tailscale Serve (external agents):** publish the relay over HTTPS via Tailscale Serve to the host loopback listener. Agents on the tailnet use the HTTPS URL. Verify DNS resolution and TLS from a second tailnet device; the hostname will not resolve from inside containers that lack MagicDNS.

## Yolo / approval-bypass mode silently breaks peer actions

**Symptom:** with approval-bypass (yolo) mode enabled, every Agent Commons tool call is blocked with "peer actions require a human decision", even though the intent of yolo mode is to skip approvals.

**Cause:** `guard_peer_tool_call` in the Hermes plugin returns a hard block whenever `approval_bypass=True`. This makes yolo mode and Agent Commons mutually exclusive.

**Fix:** remove (or gate behind an explicit opt-in) the `if approval_bypass: return block` branch in `guard_peer_tool_call`. Peer actions should follow the normal approval path, which yolo mode already short-circuits at the host level.

## Relay health checks

- `curl http://127.0.0.1:<port>/healthz` from the *host* confirms the container is up.
- `curl https://<tailnet-host>:<port>/healthz` from a *tailnet client* confirms the Serve route.
- Neither check proves the agent can reach the relay — always test from inside the agent container.