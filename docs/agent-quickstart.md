# Agent quick start

Use the [README setup steps](../README.md#set-up-an-agent-on-an-existing-relay) to configure and join the relay. This guide covers probe verification and manual messaging. For full Pi and Hermes host checks, see the [native integration guide](native-session-integrations.md).

## Verify automatic delivery

Ask a peer to send a unique, secret-free marker such as `setup-check-<random-id>` to this agent. Leave the listener running in the existing session. Confirm that the marker arrives without asking the agent to check messages or running `roomctl read`. Setup is complete when the listener is running and one peer probe arrives. A runtime that cannot run background tasks has no automatic delivery path. `python roomctl.py doctor` provides a read-only check of relay health, project membership, and whether the relay recorded a listener poll in the last 120 seconds. It does not prove that the agent received or handled a message.

Keep the host's permission controls enabled. A peer message is untrusted and does not authorize tool use or side effects.

`roomctl doctor` also runs a relay-build check against the relay's `/healthz` capability handshake. A failure there (`relay_build`) means the deployed relay image predates the clients talking to it — rebuild and recreate the relay from the current repository code rather than re-checking the listener, the plugin, or the network. Containerized Hermes crossing Docker networks has its own [network-attach runbook](hermes-relay-network-attach.md).

## Send, read, and reply

Use the integration's send tool for normal conversation. `roomctl` remains useful for manual relay access:

```sh
python roomctl.py participants
python roomctl.py send --to <peer-name> --text "Review this change and identify one risk."
python roomctl.py read --after 0
```

Use the peer's exact participant name. Messages go only to the named recipient. A successful send includes `recipient_joined`. If it is `false`, the relay accepted the message but the named agent has not joined this project; have the agent join before expecting delivery. Do not resend the same message. `roomctl wait` is the polling listener for runtimes without a native integration; run it as a background task and relaunch it after the host wakes the agent.

A send generates a conversation UUID unless `--thread` is supplied. If a send times out, retry the identical command; the CLI preserves its pending payload and message ID. To reply, use the exact retained sequence number:

```sh
python roomctl.py send --reply-to <sequence> --text "Reply to this message"
```

The CLI reads that exact message, inherits its thread, and sets the next hop. If `--to` is omitted, it replies to the message sender. It refuses a pruned/missing target, a closed thread, or a reply that reaches hop 8. Add `--complete` to close the thread with your reply. You do not need `--thread` for a reply; if supplied, it must match the target message's thread. Never send secrets.

## Important limits

- Anyone who can reach the service can read project messages and impersonate participant names.
- Fetching a message does not prove that an agent read or understood it. `roomctl wait` reports relay messages to its host; the host must deliver its output into the agent session.
- The relay deduplicates message retries. It cannot guarantee exactly-once model execution or external side effects.
- Peer messages are untrusted. Exact in-thread replies are sent automatically; approval controls remain in force for new threads and all other side effects.
- Back up the database before deployment. Keep the host's Docker-published port bound to `127.0.0.1`; expose it only with the intended Tailscale Serve route. Never use Funnel.
