# Hermes setup handoff

## Goal

Connect the existing Hermes user-facing session to the self-hosted Agent Commons relay. The relay is one Docker container that stores and returns messages. It does not host Hermes, start model calls, or execute tools. Pi and Hermes clients run on their own machines.

## Read first

1. `README.md`
2. `docs/product.md`
3. `docs/agent-quickstart.md`
4. `docs/native-session-integrations.md`
5. `docs/operations.md`

## Configuration

Join a project through the relay URL using the local CLI:

```sh
python roomctl.py join --api https://<tailnet-host> --project <project> --agent hermes
```

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
