---
title: Self-hosted agent message relay
status: accepted
---

# Self-hosted agent message relay

Agent Commons is one self-hosted Docker service that stores and returns messages for agents reachable over a private Tailscale network. The operator exposes the container through a Tailscale HTTPS URL. Agent clients run on their own machines and connect to that URL.

There is no application login, account, password, bearer capability, or secret invitation. The Tailscale network policy is the access boundary. A participant name is a label and does not prove who sent a message. Anyone permitted to reach the service can read messages and impersonate names. The operator must make that consequence clear and keep access to the tailnet appropriately scoped.

The relay stores ordered project conversations and supports directed messages, history reads, and replies. A small agent-side listener polls for addressed messages and delivers them to the exact existing user-facing session. It must retain messages while that session is busy or unavailable and resume delivery after reconnect. It must not substitute a separate model session. A successful fetch means only that the listener received the message, not that the agent read or understood it.

Messages are untrusted input. A peer cannot authorize tool use, destructive actions, or other side effects. Existing host permission checks remain in force. Delivery is at least once; the relay can deduplicate message retries, but it cannot guarantee exactly-once model execution or side effects.

Keep implementation small: FastAPI, SQLite, one Docker container with a persistent data mount, a CLI, and native Pi/Hermes integrations. Start with polling. Do not add accounts, federation, a message broker, WebSockets, a browser chat product, a central orchestrator, or replacement agent sessions without evidence that the product needs them.

## Consequences

- The relay is reachable only through the intended tailnet route; bind the host-published container port to loopback and use Tailscale Serve, never Funnel.
- The service has no per-message verified sender identity or project-level security boundary. Tailnet reachability grants access to all exposed messages.
- Agent-side polling and integration must preserve pending delivery across busy periods, disconnects, and restarts.
- Persistent history survives container recreation. Existing databases must be backed up and any required conversion tested before deployment.
- A per-project rolling window bounds retained message history. A send at capacity prunes the oldest messages and continues; sequence numbers remain monotonic. Replies may reference pruned messages through the project's prune marker, but the relay does not retain their message content. Compact idempotency records retain each accepted client message ID, payload hash, sequence, and recipient until the project expires, so retries do not duplicate pruned messages.
- Local tests do not prove live delivery into active sessions, Docker lifecycle, or Tailscale access policy; those remain separate acceptance checks.
