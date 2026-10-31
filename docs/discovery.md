# Local discovery and go/no-go

**Checked:** 2026-10-31 (Australia/Brisbane)

## Scope and decision

Proceed with the smallest self-hosted SQLite mailbox implementation for local development. The user explicitly requested implementation, and this workspace contains only the revised plan (no existing service or repository). There is no evidence here that ntfy plus existing listeners meets durable replay, idempotency, and bridge acceptance criteria, so that alternative cannot be certified as a stop-build solution. This is a local build decision, not deployment approval.

## Observed environment

- Workspace contains `agent-room-relay-revised-plan.md`; it is not currently a Git worktree.
- Local Python: 3.11.15; `uv` 0.11.28; pytest, FastAPI, Uvicorn, and httpx are importable.
- Docker CLI is not installed in this execution environment.
- Hermes Agent: 0.18.2, local Python 3.11.15.
- Pi CLI: 1.4.0. Its help advertises `--mode rpc`, `--session`, and session resumption options. No Pi agent invocation was made. The help command emitted a settings-lock EPERM warning; no settings change was requested or verified.

## Not inspected / deployment gate remains closed

No live TrueNAS, Tailscale, agent-chattr successor, app inventory, ports, datasets, loopback binding, Serve routes, network namespace, Hermes API configuration/key availability, or cross-machine connectivity was inspected. No claim is made that the previously mentioned host, port, route, or dataset exists. No Hermes/Pi API or RPC request was made, no daemon was installed, and no production settings or secrets were read or changed. Docker deployment and the real cross-machine demonstration are therefore not verified.

Before deployment, an operator with approved access must re-inspect TrueNAS and Tailscale live, verify Hermes local API and Pi RPC behavior against the installed versions, evaluate actual tailnet reachability, and obtain the separate M7 approval described in the plan. No route or app should be mutated before that gate.

## Rationale / risks

The selected implementation keeps the relay as a mailbox (SQLite/FastAPI) and keeps model execution local to each agent-side bridge. The room capability is a bearer secret; room membership does not authenticate sender labels. Bridge invocation is at-least-once, not exactly-once. The relay has not been exposed on any network.
