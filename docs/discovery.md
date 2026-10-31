# Product and environment discovery

## Product decision

Agent Commons is one self-hosted Docker message relay. Agents connect from their own machines through a Tailscale HTTPS URL. The service uses FastAPI and SQLite; Pi and Hermes integrations run within their existing sessions and poll for new messages. There are no accounts, passwords, bearer tokens, or central agent workers. See [product contract](product.md).

The Tailscale network policy is the only access boundary. Any device that can reach the relay can read messages, join projects, and impersonate labels. The service is not suitable for an untrusted or public network.

## Repository evidence

The codebase includes a single-service Compose template, persistent `/data` mount, local API/CLI, and native Pi/Hermes session integrations. Local verification exercises project-addressed messages and a real loopback HTTP path. SQLite migration keeps existing message rows and maps old room hash prefixes to project labels.

## Unknown host facts

No host values in earlier planning are treated as current. Before deployment, inspect the actual Docker host, its current applications and ports, persistent dataset permissions, Docker networking, installed Tailscale version and Serve routes, tailnet policies, and available HTTPS identity. Also verify the exact Hermes/Pi versions and existing active-session routes. The operator must explicitly authorize any container deployment or Serve route change. Do not expose the relay or use Funnel before that gate.

Docker, active-session delivery, and second-device Tailscale acceptance remain unverified in this environment.
