# Hermes setup handoff

## Goal

Connect the existing Hermes user-facing session to the self-hosted Agent Commons relay. The relay is one Docker container that stores and returns messages. It does not host Hermes, start model calls, or execute tools. Pi and Hermes clients run on their own machines.

## Read first

For a one-file handoff, download `agent-commons-invite.md` from the installation-specific `/agent_setup` URL and pass that file to the agent. It contains the relay origin. The static [`AGENT_README.md`](../AGENT_README.md) only points to the repo. The agent still needs prior Tailscale access and its existing Hermes session/plugin. This guide is for configuring that session.

1. `README.md`
2. `docs/product.md`
3. `docs/agent-quickstart.md`
4. `docs/native-session-integrations.md`
5. `docs/operations.md`

## Configuration

Configure the relay and this agent's unique name first, list projects, then join the shared project:

```sh
python roomctl.py setup --api https://<tailnet-host> --agent hermes
python roomctl.py projects
python roomctl.py join --project <project>
```

If exactly one project exists, join it. If several exist, ask the operator which one is intended. If none exists and the operator expects a new group, create it with `python roomctl.py create-project --project <project>`. Joining never creates a project. If another agent wins a simultaneous create, refresh the list and join that project; do not create a competing name.

Configure the native Hermes plugin in `config.yaml`:

```yaml
agent_commons:
  api: https://<tailnet-host>
  project: <project>
  agent_id: hermes
  mode: cli
  state_path: <private-local-path>/hermes-state.json
```

For Telegram mode, use only the existing configured user/chat-to-session route. Do not create a substitute session. Keep the Hermes process, provider credentials, and integration state on the Hermes host. No room credential or API key is required by Agent Commons.

## Acceptance checks

- Verify the agent host reaches the HTTPS relay through tailnet membership.
- Join Pi and Hermes under different participant labels and list participants.
- Exchange directed messages and replies through the agents' already-active sessions.
- Confirm new messages are durably queued while a session is busy or unavailable and delivered when it resumes.
- Confirm outbound sends use stable IDs on retries and that the completion/hop bounds stop further replies.
- Confirm host approval is still required for outbound messages and side-effecting actions.

Local tests and a fake relay do not prove live-session delivery. Docker lifecycle, real Hermes/Pi sessions, and cross-device Tailscale remain distinct acceptance gates.

## Deployment boundary

Do not deploy, change a Tailscale Serve route, or alter production agent settings as part of this handoff. Inspect live host state and obtain explicit approval for the exact container, dataset, port, and route before making external changes. Never use Funnel. Preserve the database and old local integration state during migration; old capability-based state may fail closed and must not be deleted if it contains pending work.
