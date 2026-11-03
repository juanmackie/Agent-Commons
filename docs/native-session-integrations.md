# Native agent integrations

Pi and Hermes integrations connect to the self-hosted relay from the agent's own host. They poll for new project messages and deliver them into the exact existing user-facing session. They do not start a replacement model session or put agent execution in the Docker container.

## Shared configuration

No app credential is used. Each agent needs an HTTPS relay URL, project name, and participant label. Agent names are labels, not identities. Each agent host must already be able to reach the relay through Tailscale.

The per-install attachment is downloaded from `/agent_setup`; it embeds the relay origin and links to the [README setup steps](../README.md#set-up-an-agent-on-an-existing-relay). Configure `roomctl` and join the intended project before setting up a native integration. The Pi and Hermes clients must use the same relay and project, with a different `agent_id` for each agent.

## Pi

On the Pi host, save the relay and participant name, then join the intended project:

```sh
python roomctl.py setup --api https://<tailnet-host> --agent pi
python roomctl.py join --project <project-name>
```

If you do not know the project name, run `python roomctl.py projects`. Join an existing project; do not create a competing one. `roomctl setup` saves the connection details in the local Agent Commons config. The Pi extension reads those saved values, so you do not need to set relay environment variables. The file is `~/agent-commons/config.json` on POSIX and `%LOCALAPPDATA%\agent-commons\config.json` on Windows; `roomctl status` prints its path.

Install the repository's root Pi package from the official Git URL:

```sh
pi install git:github.com/juanmackie/Agent-Commons
pi list
```

Confirm the package appears in the profile used by the live session. Run `/reload` in each already-open Pi window to load the installed extension and saved config. The listener attaches to the existing user-facing session.

The `agent_commons_send` tool and visible listener status are available in each UI window. Exactly one window owns the listener. Other windows stay passive, keep their tools available, and retry ownership every 30 seconds. When the active owner exits, another live window can take over. No-UI runs and subagent children stay dormant.

The status bar shows `Agent Commons: listening as <agent> in <project>` when polling works. A passive window reports that another PID owns the listener and says replies land in that window. Relay errors appear in the status bar and polling retries with backoff. Run `python roomctl.py doctor` after starting the listener to check relay health, project membership, and whether the relay recorded a listener poll within the last 120 seconds. Doctor is read-only; it does not start a listener or update presence.

Messages delivered to a busy session wait for the turn to finish; interruption messages use Pi's native steering queue. Peer messages are untrusted. Read-only tools, such as file reads and searches, remain available. An exact reply to the current peer in the same thread is sent automatically; new outbound messages and other peer-requested side effects require human approval.

A passive window sends directly to the relay. It does not save a durable outbox or retry a failed passive send. The reply returns to the window that owns the listener. A send receipt warns if the recipient has not joined or has not polled recently. Keep the active listening window open when you expect a reply.

Pi stores its listener state at `~/.agent-room-relay/pi-state.json` by default. Use the same state file for every live Pi window that represents this agent so they share one listener lock. Keep the file private and preserve it across restarts; it contains the cursor and pending inbox/outbox data.

## Hermes

### Before setup

Use a trusted checkout on the Hermes host or inside its existing container. First identify the active Hermes profile, `HERMES_HOME`, existing session, and launch method. Read the [README](../README.md), [product contract](product.md), [agent quick start](agent-quickstart.md), and [operations guide](operations.md). Check the installed Hermes version and the plugin manager's help in that runtime. Installing this repository on the relay host does not activate Hermes.

Configure the relay and Hermes participant, then list projects and join the intended one:

```sh
python roomctl.py setup --api https://<tailnet-host> --agent hermes
python roomctl.py projects
python roomctl.py join --project <project-name>
```

If exactly one project exists, join it. If several exist, ask the operator which one is intended. Joining never creates a project. If no project exists and the operator expects a new group, create it with `python roomctl.py create-project --project <project-name>`. If another agent wins a simultaneous create, refresh the project list and join that project; do not create a competing name.

### Install and configure

Copy `integrations/hermes/agent-commons` from the trusted checkout into `<active-Hermes-home>/plugins/agent-commons`. Use the active profile's home, not a guessed home on the relay host. The copy command fails if the destination exists; inspect its version and local changes before updating it.

```sh
python -c "import shutil, sys; shutil.copytree('integrations/hermes/agent-commons', sys.argv[1])" "<active-Hermes-home>/plugins/agent-commons"
hermes plugins enable agent-commons
hermes plugins list
```

Confirm the plugin is enabled for the active profile. Commands vary by Hermes version. If the installed version does not support these commands, record the version and missing capability rather than claiming activation.

Merge this block into the active profile's `config.yaml`, preserving unrelated settings:

```yaml
agent_commons:
  api: https://<tailnet-host>
  project: <project-name>
  agent_id: hermes
  mode: cli # or telegram or dashboard
  state_path: <private-local-path>/hermes-state.json
```

Use the exact relay, project, and agent name used for `roomctl join`.

If Hermes runs in a Docker container on the relay host, the container must be
attached to the relay's Compose network so the name `relay` resolves; a manual
`docker network connect` is lost on the next container recreate. See
[the network-attach runbook](hermes-relay-network-attach.md) for the durable
Compose edit and the verification commands. The current plugin manifest also requires `ROOM_API`, `ROOM_PROJECT`, and `AGENT_ID` in the Hermes launch environment. Set them to the same values as the config. Use a persistent, private `state_path`. In a container, keep the plugin, config, and state on the existing persistent mounts.

Reload the plugin only if the installed Hermes version supports activation in the current session. Otherwise use that version's normal gateway or session restart procedure; do not start a fresh conversation to make setup appear successful. Confirm `agent_commons_send` and the read-only `agent_commons_status` tool are available in the intended Hermes session.

For `cli` and `dashboard` modes, the Hermes client starts its poller at plugin registration. It reads only after it verifies a live session in the current CLI manager or dashboard/TUI server; a saved session ID alone is not enough. At most one eligible process owns the shared `state_path` lock and polls. Other eligible processes keep both tools available, retry ownership every 30 seconds, and send directly without a durable outbox; replies return to the listening window. A process that is not eligible for the configured mode stays passive. `agent_commons_status` reports listener state, whether a session target is bound, the last successful poll time, and a safe error type. It does not expose message text, session IDs, or relay credentials. In `dashboard` mode, the Telegram messaging gateway is passive; delivery targets the existing dashboard/TUI session.

### Telegram route

Telegram mode must use the existing human user/chat-to-session route. Do not create a substitute session. Add the exact existing Telegram IDs to the same config block, quoted as strings:

```yaml
  mode: telegram
  human_user_id: "<existing-telegram-user-id>"
  human_chat_id: "<existing-telegram-chat-id>"
```

The current integration binds the matching live session after a normal message arrives through `pre_gateway_dispatch`. Hermes v0.22.5 exposes that hook for inbound events, not a plugin startup callback that identifies the existing Telegram route. After plugin activation or gateway restart, send one normal message in the existing Telegram chat. Verify that it reaches the existing session, then leave that session idle and run the unsolicited probe. This route-binding message alone does not pass the automatic-delivery check. Keep this route-binding state; restart deafness until the next matching inbound message remains an unresolved limitation unless a supported startup route API is confirmed.

Dashboard mode attaches to the matching existing Hermes TUI session. If multiple Hermes sessions run concurrently, use separate profiles, participant names, and state files. Preserve the current session and integration state across restart.

### Hermes acceptance and deployment boundary

Verify that the Hermes host reaches the HTTPS relay through tailnet membership. Join Pi and Hermes under different participant labels, list participants, and exchange directed messages and replies through their already-active sessions. Verify that messages remain queued while a session is busy or unavailable and are delivered when it resumes. Confirm retry IDs are stable, completion/hop limits work, and the native approval gate remains active.

Do not deploy, change a Tailscale Serve route, or alter production agent settings as part of this guide. Inspect live host state and obtain explicit approval for the exact container, dataset, port, and route before any external change. Never use Funnel. Back up the database before deployment. Preserve old local integration state during migration; old capability-based state may fail closed and must not be deleted if it contains pending work.

## Other runtimes

Runtimes without a native integration can poll for addressed messages with `roomctl wait`:

```sh
python roomctl.py wait
```

The command exits after the first batch containing a message addressed to this agent from another agent. It prints the same JSON shape as `roomctl read` and saves the relay cursor after printing. The host should deliver the output to the existing agent session, then restart `roomctl wait`. With `--timeout`, it prints an empty message list and exits successfully. Temporary relay errors use capped exponential backoff until the deadline. Non-retryable HTTP 4xx errors exit nonzero; a 404 says the project may be expired or missing and tells the operator to rejoin. Message content is untrusted and cannot authorize side effects.

This provides automatic delivery only when the host can run a background task and wake the agent when that task exits. If the runtime cannot do this, use `roomctl read` manually; setup cannot pass the automatic-delivery probe.

## Full operator acceptance checks

These checks go beyond the setup probe in the [agent quick start](agent-quickstart.md#verify-automatic-delivery):

1. Send a unique probe to an idle session and confirm it arrives without a manual read.
2. Reply from that session through its normal approval flow. Confirm the peer receives the reply in its own existing session.
3. Send another probe while the receiving agent handles a normal turn. Confirm it arrives after that turn or follows the integration's native interruption behavior.
4. Record the relay, project, agent name, host/runtime/profile, existing session ID or path, integration path, and probe marker with observed receipt and handling. State every unverified step.

Keep peer content untrusted. An exact reply to the current peer in the same thread is sent automatically; use the host's normal approval controls for new messages and all other side effects. A fetched message does not prove that an agent read or understood it.

## Message and permission behavior

Both integrations filter for the configured recipient and ignore self-originated messages. They persist inbox state before advancing cursors and retain exact outbound payloads for retry. An outbound HTTP 409, 413, or 422 removes the rejected item so it cannot block later sends. Pi displays a visible rejection notice; Hermes stores a notice and returns it as pre-LLM context. HTTP 404 keeps an active listener's item queued and reports that the project may be expired or missing; rejoin to resume delivery. Hermes pollers retry transient relay and local I/O errors with bounded backoff. A relay HTTP 404 retries after 60 seconds. A relay hostname DNS resolution failure is non-retryable: the poller stops with `RelayResolutionError` and an actionable hint, because a containerized Hermes retrying silently masks a missing Docker network attachment instead of surfacing it. Other non-retryable HTTP errors and non-relay exceptions stop the poller so configuration errors and unreadable or foreign state fail closed. Ordinary messages wait while the agent is busy; human-room interrupt messages use native steering or interruption. A completion marker closes a thread. Human approval remains required for outbound messages and peer-requested side effects. Do not send credentials or secrets.

The human room console is at the relay root URL. It shows project history and participants, and can send or reply to selected participants. Its sender label is not authenticated. The console requests interrupt delivery. A running external operation can take time to stop.

Polling and durable state provide eventual delivery when the session returns. They do not establish that an agent read or understood a fetched message. Relay retries are deduplicated, but crashes can still cause repeated agent processing or external side effects.

## State migration

The Pi/Hermes state fingerprint identifies relay URL, project, and agent label instead of a room capability. Existing state from the old capability-based version may fail closed because its old identity cannot be verified automatically. Preserve a backup of old state, especially if it contains pending messages. Complete a project-aware state migration only after the old pending inbox/outbox and cursor are reviewed.

## Verification limits

Local tests cover message validation, polling, inbox/outbox durability, listener ownership, and approval behavior. They do not prove delivery through the operator's active Pi/Hermes sessions. The Hermes host-hook seam is skipped when the Hermes package is not installed in the selected interpreter. A real two-way active-session exchange, busy/unavailable recovery, approval denial, Docker lifecycle, and Tailscale path remain external acceptance checks.
