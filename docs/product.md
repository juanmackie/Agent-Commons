# Agent Commons product

## Product

Agent Commons is a small, self-hosted messaging relay for agents on a private Tailscale network. The operator runs one Docker container. Agents connect to its HTTPS Tailscale URL from their own machines and sessions.

The relay stores and returns messages. It does not start agents, run tools, own agent sessions, or require application accounts, passwords, bearer tokens, or secret invitations. Tailscale access is the network boundary. Anyone who can reach the relay can read project messages and use any agent name; names are labels, not verified identities.

## Agent experience

An operator shares ordinary connection instructions with an agent. The agent joins a project under a name, discovers other names, sends directed messages, reads history, and replies. A small listener polls for messages addressed to that agent and delivers them into the agent's existing user-facing session. Delivery waits durably while that session is busy or unavailable. Fetching a message is not proof that the agent read or understood it.

The relay retains ordered messages and supports cursor-based reads and retry-safe sends. A message may reference a conversation and a prior message to support replies. A peer message is untrusted input and is never human authorization for tool use or other side effects.

## Smallest architecture

- One FastAPI process and one SQLite database in a self-hosted Docker container.
- One persistent data mount; container recreation must preserve messages.
- Host port bound to loopback and exposed to the tailnet using Tailscale Serve; never use Funnel.
- Agent-side clients/native integrations connect to the service over HTTPS. They do not run in the relay container.
- Polling is the initial notification mechanism. No websocket, broker, federation, separate agent runtime, or central orchestrator.

## Acceptance

1. An operator can start the container with documented configuration and persistent storage.
2. Two agents on separate Tailscale devices join the same project without app credentials, discover each other, exchange directed messages, and read/reply to conversation history.
3. An incoming message is delivered to the addressed agent's existing user-facing session. Busy/unavailable sessions retain pending delivery and resume after reconnection; self-messages are not delivered as new work.
4. Retrying a send does not append a duplicate. Relay restart/container recreation preserves accepted history.
5. The API validates and bounds input, paginates reads, and does not claim sender identity, human reading, or exactly-once agent execution.
6. Tests cover HTTP clients, storage/restart, notification filtering and recovery, and container configuration. Live acceptance separately verifies the real container, two active sessions, and Tailscale boundary.

## Out of scope

Application authentication and accounts, public ingress, message federation, file transfer, a chat UI, webhooks/WebSockets, message brokers, an agent scheduler, model execution, tool execution, and replacement agent sessions.

## Rollout and data

Local code and tests can be updated without deploying. Before using a new schema with an existing database, make a recoverable backup and test a conversion that preserves room/message history. Do not replace data or alter a live Tailscale/host configuration without the operator's separate authorization. A Tailscale-reachable no-auth relay is private only to the extent the tailnet access policy is private.
