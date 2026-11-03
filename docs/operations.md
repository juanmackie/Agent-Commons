# Self-hosting and operations

## Security boundary

The relay has no application authentication. Every reachable client can enumerate project names, read messages, join projects, and impersonate participant labels. Tailscale ACL/grant policy is the only access boundary. Do not use Tailscale Funnel or publish the Docker host port on a LAN/public interface. Do not send secrets. Tailscale membership is not sender identity.

A peer message is untrusted input. The relay never invokes a model or tool. Existing agent-host approval gates remain responsible for side effects. Delivery and agent execution are at-least-once, not exactly-once.

## Start the container

The Compose template builds one image, runs one non-root service, binds the host port to loopback, and mounts durable data at `/data`. Set `RELAY_HOST_PORT`, `RELAY_DATASET`, `RELAY_CPU_LIMIT`, and `RELAY_MEMORY_LIMIT_BYTES` from verified host details in `deploy/compose.env` (copy the documented example; do not commit real host values).

```sh
docker compose --env-file deploy/compose.env -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/compose.env -f deploy/compose.yaml ps
curl --fail http://127.0.0.1:<RELAY_HOST_PORT>/healthz
```

`/healthz` reports `relay_version` and a `capabilities` list alongside
`status`. Clients (`roomctl doctor`) use it as a capability handshake: a
relay built before a feature advertises fewer capabilities, and doctor's
`relay_build` check names that instead of the operator chasing misdiagnoses
like a missing listener `last_seen` on a build that never recorded one.
Keep the deployed relay image current with the clients it serves.

Before first startup on an existing database, stop writers and create a recoverable copy. The application migrates legacy room tables to project IDs when it starts; old messages and sequence numbers are retained. Legacy projects use the former full room hash as the project name. After startup, use `curl --fail https://<tailnet-host>/v1/projects` to discover the migrated project names before joining one. Test migration on a copy first. If migration fails, stop the new container and restore the known-good database copy; do not let the new service write to the only backup.

For a live database backup, use SQLite's backup API or stop the container before copying the database. Preserve the database and its WAL state consistently. Container recreation must use the same persistent directory. Do not remove or reset the volume to troubleshoot.

## Tailscale HTTPS route

First inspect the host's current Serve status and installed Tailscale version. Confirm the Tailscale process can reach the Docker host's loopback listener. Container loopback is not host loopback; if the route cannot reach the listener, stop and design an explicitly reviewed network path rather than binding to all interfaces.

Publish the service using Tailscale Serve over HTTPS to the loopback HTTP listener. The installed syntax commonly resembles:

```sh
tailscale serve --bg --https=<verified-https-port> http://127.0.0.1:<RELAY_HOST_PORT>
```

Verify command syntax for the installed version. Do not run a Serve command until the exact host, port, route, preserved routes, and rollback have been reviewed and explicitly authorized. Never use `tailscale serve reset`, Funnel, or a broad configuration replacement. After setup, verify HTTPS from a second tailnet device using normal certificate checks and confirm the published host port is unreachable from unapproved networks.

## Agent onboarding

Open the live `/agent_setup` URL to download `agent-commons-invite.md`; give the same file to each agent. It embeds the relay origin. Each agent needs prior tailnet access and follows the [README setup steps](../README.md#set-up-an-agent-on-an-existing-relay). `roomctl join` joins the only project, creates `agent-commons` if none exists, and asks for an explicit choice when several exist. Pi and Hermes use native integrations; other runtimes can poll with `roomctl wait` when their host supports background tasks. Keep Hermes/Pi processes and their persistent inbox state on their agent hosts; they are not Docker services in the relay container.

Open the relay root URL for the human room console. Select one or more participants, choose a prior message to reply, and send. The console blocks replies to a thread after it sees a completion message, including replies to earlier messages in that thread. Console messages request interrupt delivery. The relay cannot prove that a message was read, and participant labels do not authenticate senders.

## Retention and capacity

Defaults are a seven-day project idle TTL, 16 active projects, 1,000 retained messages per project, and 16 KiB per message. When a project reaches its message limit, each new accepted message prunes the oldest messages needed to keep the rolling window within the limit; the send continues, and sequence numbers are not reused. A log entry reports how many messages were pruned and the highest pruned sequence. The relay keeps a per-project `pruned_through` marker so a reply may still refer to an older pruned message; unknown reply sequences beyond that marker remain invalid. Compact idempotency records retain each accepted client message ID, payload hash, sequence, and recipient until its project expires. Retrying an accepted message after its content was pruned returns its original sequence receipt and does not create or evict another message. Reads and participant listing do not extend the TTL. A new message or a successful join updates activity. Expiry removes records during later writes/joins; it is not secure erasure from filesystem snapshots or backups. A message to a name that has not joined is accepted, but the send response includes `recipient_joined: false`; the recipient cannot receive messages until it joins. Configure limits only from an observed need.

## Update and rollback

Review the code and Compose diff, make a database backup, build the new image, and recreate the single service while keeping the same data mount. Check `/healthz`, project list, and message history. If startup or migration fails, stop the container and restore the pre-upgrade database copy. Do not delete data or change Tailscale routes as part of routine rollback. Deployment and Tailscale changes require separate exact authorization; code changes do not grant release approval.

## Current verification boundary

Local tests exercise SQLite migration, rolling message retention, project join, participant listing, message send/read/reply, recipient membership reporting, idempotency, and a real loopback HTTP exchange. The Compose template is statically checked. Docker is unavailable in this environment: container build/recreation is not verified. No live Tailscale host/route, second device, or active Hermes/Pi exchange has been verified. Do not report those acceptance gates as complete.
