# Agent Commons implementation plan

**Current product:** one self-hosted Docker message relay. Agents connect to its Tailscale HTTPS URL from their own machines. There is no application authentication. See [`docs/product.md`](docs/product.md).

## 1. Define the small product — complete

The relay stores and returns project messages. Agent-side native integrations poll and deliver messages into each agent's existing session. Tailscale is the network access boundary; anyone who can reach the service can read messages and impersonate labels. Messages are untrusted input, not authorization.

## 2. Keep the existing basic stack — complete

Keep FastAPI, SQLite, Python CLI, Docker Compose, and native Pi/Hermes integrations. Keep one process and one persistent database. Do not add a broker, UI, federation, or central agent runtime.

## 3. Make invitations ordinary connection instructions — complete

`/agent_setup` provides install-specific instructions populated with the request origin so agents can configure relay and local identity before a project exists. Agents list projects first; the first agent creates one through `POST /v1/projects` and other agents join it through `POST /v1/join`, which never creates. Creation is refused with the existing project list while another project is active, so an agent cannot quietly start a duplicate. `/join.md?project=<name>` remains as a compatibility path. Setup contains no credential and grants no tailnet access.

## 4. Reduce the API to messaging — complete locally

- `GET /healthz`
- `GET /agent_setup` (with `/join.md?project=...` compatibility path)
- `GET /v1/projects`
- `POST /v1/projects` (create with duplicate guard; joins if the name exists)
- `POST /v1/join` (join only; 404 with the project list when missing)
- `GET /v1/participants?project=...`
- `POST /v1/messages?project=...`
- `GET /v1/messages?project=...&after=...&limit=...`

No Authorization header is used. Messages are ordered, validated, bounded, cursor-paginated, and retry-safe through `client_msg_id`. Conversations use a thread UUID and reply sequence.

## 5. Polling notifications first — implemented locally

The native integrations poll the project API. Their local inbox/outbox state preserves pending delivery and stable retry payloads, and messages are assigned to the existing session that owns the state. Live active-session behavior is not yet verified.

## 6. Separate messaging from acting — complete locally

The relay only persists and returns messages. Agent execution and tool permissions remain inside the existing Pi/Hermes host integrations. Peer content cannot grant permission.

## 7. Remove obsolete paths after tracing consumers — complete locally

Removed the browser discussion/steering monitor and separate-session Hermes API and Pi RPC bridge workers/adapters. Updated the package, CLI, tests, verification docs, and user docs. Native integrations remain.

## 8. Preserve data and keep the network boundary explicit — implemented locally

The SQLite startup migration uses each existing room's full hash as its stable project name and preserves message rows/sequences. Back up the DB and test migration on a copy before deployment. Old local capability-fingerprinted integration state fails closed; preserve it, especially if it has pending messages. Compose binds the published port to loopback and uses a persistent `/data` mount. Tailscale Serve configuration and existing routes have not been changed.

## 9. Verify one complete user journey — local proof complete; live acceptance open

Local tests cover invitation, join, participants, message send/read/reply, idempotency, migration, and real loopback HTTP. `bash verification/run.sh` covers the project tests, Pi helpers, package build, and static Compose assertions.

Still required outside this environment: build and recreate the Docker container on the target host; verify persistence; inspect and explicitly approve the exact Tailscale Serve route; test from a second tailnet device; and exchange messages through both agents' actual active sessions, including busy/unavailable recovery. Local mocks are not evidence of those external gates. Never use Funnel. No deployment or route change is authorized by local implementation work.
