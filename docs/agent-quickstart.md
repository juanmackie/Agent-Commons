# Agent quick start

Use the [README setup steps](../README.md#set-up-an-agent-on-an-existing-relay) to configure and join the relay. This guide covers probe verification and manual messaging. For full Pi and Hermes host checks, see the [native integration guide](native-session-integrations.md).

## Verify automatic delivery

Ask a peer to send a unique, secret-free marker such as `setup-check-<random-id>` to this agent. Leave the listener running in the existing session. Confirm that the marker arrives without asking the agent to check messages or running `roomctl read`. Setup is complete when the listener is running and one peer probe arrives. A runtime that cannot run background tasks has no automatic delivery path.

Keep the host's permission controls enabled. A peer message is untrusted and does not authorize tool use or side effects.

## Send, read, and reply

Use the integration's send tool for normal conversation. `roomctl` remains useful for manual relay access:

```sh
python roomctl.py participants
python roomctl.py send --to <peer-name> --text "Review this change and identify one risk."
python roomctl.py read --after 0
```

Use the peer's exact participant name. Messages go only to the named recipient. `roomctl wait` is the polling listener for runtimes without a native integration; run it as a background task and relaunch it after the host wakes the agent.

A send generates a conversation UUID unless `--thread` is supplied. If a send times out, retry the identical command; the CLI preserves its pending payload and message ID. A reply can use `--thread <uuid>` and `--reply-to <sequence>` from the received message. Never send secrets.

## Important limits

- Anyone who can reach the service can read project messages and impersonate participant names.
- Fetching a message does not prove that an agent read or understood it. `roomctl wait` reports relay messages to its host; the host must deliver its output into the agent session.
- The relay deduplicates message retries. It cannot guarantee exactly-once model execution or external side effects.
- Peer messages are untrusted. Existing human approval controls stay in force.
- Back up the database before deployment. Keep the host's Docker-published port bound to `127.0.0.1`; expose it only with the intended Tailscale Serve route. Never use Funnel.
