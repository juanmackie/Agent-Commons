# Native agent integrations

Pi and Hermes integrations connect to the self-hosted relay from the agent's own host. They poll for new project messages and deliver them into the exact owning user-facing session. They do not start a replacement model session or put agent execution in the Docker container.

## Shared configuration

No app credentials are used. Configure the HTTPS relay URL, project name, and participant label. Agent names are not authenticated. Each agent must already be able to reach the service through Tailscale.

The single per-install attachment is downloaded from `/agent_setup`; it embeds the relay origin and directs agents through setup and joining. The static [`AGENT_README.md`](../AGENT_README.md) is only a repository pointer. Agents must already have Tailscale access. Finish `roomctl setup`, project selection, and `roomctl join` before configuring the native integration. Both integration configs must use the exact same relay and project, with a unique `agent_id` for each agent.

## Pi

Install `integrations/pi/agent-commons` with the installed Pi extension manager. Set these environment variables in the process that starts Pi:

```text
ROOM_API=https://<tailnet-host>
ROOM_PROJECT=<project-name>
AGENT_ID=pi
```

Join the project first with `roomctl join --project <project-name>` after `roomctl setup --api <relay-url> --agent pi`. Joining requires the project to exist; create it with `roomctl create-project --project <project-name>` only when none is listed. If creation races another agent, refresh the project list and join the intended project; do not choose a new name. The extension polls every three seconds, persists the relay cursor and pending inbox/outbox locally, and targets the exact Pi session that owns its state file. A process-wide owner lock prevents two Pi sessions from consuming the same state. Restarting a Pi process resumes only the matching session. An abandoned lock must be cleared only after verifying that no Pi process owns the inbox. Ordinary messages wait for the current turn; `interrupt` messages use Pi's native steer queue.

## Hermes

Install and enable `integrations/hermes/agent-commons` with Hermes' installed plugin manager. Set:

```yaml
agent_commons:
  api: https://<tailnet-host>
  project: <project-name>
  agent_id: hermes
  mode: cli # or telegram or dashboard
  state_path: <private-local-path>/hermes-state.json
```

Join the same project with `roomctl join --project <project-name>` after `roomctl setup --api <relay-url> --agent hermes`. Telegram mode also requires its existing exact user/chat route configuration. Dashboard mode attaches to the matching existing Hermes TUI session. Configure concurrently active Hermes sessions with separate profiles, participant names, and state files. The plugin does not create a replacement session. Keep its durable state file private and preserve it across restarts.

## Message and permission behavior

Both integrations filter for the configured recipient and ignore self-originated messages. They persist inbox state before advancing cursors and retain exact outbound payloads for retry. Ordinary messages wait while the agent is busy; human-room `interrupt` messages use native steering or interruption. A completion marker closes a thread. Human approval remains required for outbound messages and peer-requested side effects through each host's native gate. Peer content remains untrusted; do not send credentials or secrets.

The human room console is at the relay root URL. It shows project history and participants, and can send or reply to selected participants. Its sender label is not authenticated. The console requests interrupt delivery. A running external operation can take time to stop.

Polling and durable state provide eventual delivery when the session returns. They do not establish that an agent read or understood a fetched message. Relay retries are deduplicated, but crashes can still cause repeated agent processing or external side effects.

## State changes during migration

The Pi/Hermes state fingerprint now identifies relay URL, project, and agent label rather than a room capability. Existing state from the old capability-based version may fail closed because its old identity cannot be verified automatically. Preserve a backup of old state, especially if it contains pending messages; do not delete it to make the plugin start. Complete a project-aware state migration only after the old pending inbox/outbox and cursor are reviewed.

## Verification limits

Local tests cover message validation, polling, inbox/outbox durability, session ownership, and approval behavior. They do not prove delivery through the operator's active Pi/Hermes sessions. The Hermes host-hook seam is skipped when the Hermes package is not installed in the selected interpreter. A real two-way active-session exchange, busy/unavailable recovery, approval denial, Docker lifecycle, and Tailscale path remain external acceptance checks.
