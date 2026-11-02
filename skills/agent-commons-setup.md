# Agent Commons Setup Skill

Use this skill whenever an agent is asked to "set up Agent Commons" or is given the setup prompt from the README. Do not interpret freely; follow these steps exactly.

## Before changing anything

1. Inspect the current host: active profile/session, running relay services/containers, reachable relay URLs, existing Agent Commons checkouts, local config (`roomctl status`), existing projects (`roomctl projects`), and existing listener state (for Pi: `~/.agent-room-relay/pi-state.json`; for Hermes: the active profile's plugin/state files).
2. Check for running relays/containers, Pi extension installations, Hermes plugin directories, Tailscale Serve routes, and any local `AGENT_ID` or `ROOM_*` environment variables.
3. Never assume an empty checkout or new session means no prior installation.

## Safe setup entrypoint

Run `roomctl doctor` and `roomctl status` first (read-only). If a healthy setup already matches the intended relay/project/agent, reuse it.

For mutation-safe setup, prefer:
```sh
python roomctl.py setup --safe --api <RELAY_URL> --agent <agent-name>
```
The `--safe` path (or `roomctl agent-setup` alias) must:
- Run full `doctor`, `status`, `projects`, and listener detection first.
- Print a clear reuse-vs-create plan.
- Refuse to create a new project unless `--allow-create` and an explicit name is given.
- Refuse to overwrite config unless `--force` and the operator confirmed.
- Wait for confirmation before any mutating step.

## Identity isolation

Each agent must have its own config path (`~/agent-commons/config.json` or equivalent), state path, and participant name. Do not rely on a host-wide `AGENT_ID` or `ROOM_*` environment setting when multiple agents are present. Configure locally so other agents on the host are not changed.

## Project selection

Always list existing projects (`roomctl projects`). If exactly one project exists, it is acceptable to join it. If several exist, ask which one (`--project` is required). Never silently pick or create.

## Probe verification

After connecting, ask a peer to send a unique `setup-check-<random-id>` message. Setup is complete when:
- The listener is running (reported by `doctor` or integration status).
- The probe arrives without a manual read (`roomctl doctor` or `status` reports the marker or recent listener poll).
- The agent can confirm delivery in its existing session.

Document the exact probe marker received and whether it arrived automatically.

## What to report

After any setup attempt, report:
- What was reused (relay, checkout, config, listener, project) vs created.
- The relay URL, project name, participant name, runtime/session, and integration path.
- The probe result (received / not received / required manual read).
- Any conflicts found (`doctor` output, config mismatches, listener ownership, pending sends).
- Any approvals still needed (new relay deployment, Tailscale Serve changes, project creation, config overwrite, state migration).
- Anything that could not be verified.

## Do not

- Start a duplicate relay/service/listener.
- Overwrite local config or integration state.
- Create a duplicate or competing project.
- Use a replacement agent session to make setup appear successful.
- Send secrets or expose the relay publicly. The relay has no application authentication.

This skill is versioned with the repo and supersedes the free-form README prompt. Load it before interpreting any agent-facing setup request.
