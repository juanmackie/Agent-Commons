# Local discovery and go/no-go

**Checked:** 2026-10-31 (Australia/Brisbane)

## Scope and decision

Proceed with the smallest self-hosted SQLite mailbox implementation for local development. The user explicitly requested implementation, and this workspace contains only the revised plan (no existing service or repository). There is no evidence here that ntfy plus existing listeners meets durable replay, idempotency, and bridge acceptance criteria, so that alternative cannot be certified as a stop-build solution. This is a local build decision, not deployment approval.

## Observed environment

- At initial inspection, the workspace contained `agent-room-relay-revised-plan.md` only and Git did not recognize the directory as a worktree. On continuation, the current directory now reports `main` tracking `origin/main`; treat that checkout and its current uncommitted edits as authoritative.
- Local Python: 3.11.15; `uv` 0.11.28; pytest, FastAPI, Uvicorn, and httpx are importable.
- Docker CLI is not installed in this execution environment.
- Hermes Agent: 0.18.2, local Python 3.11.15. Read-only `hermes status` and `hermes gateway status` report no gateway process (stopped); the status output also reports the configured terminal backend as `local`. No `.env`, auth file, or configuration secrets were read. A local API endpoint is therefore not currently available to verify.
- Pi CLI: 1.4.0. Its help and installed `docs/rpc.md` / `docs/rpc-commands.md` confirm JSONL RPC, `agent_settled`, and `get_last_assistant_text`; the source package documents `--session-id` and `--session-dir`. The adapter uses these documented options with tools, MCP, extensions, and project context files disabled. Tests exercise the protocol with a fake child process. No Pi model invocation was made. The help command emitted a settings-lock EPERM warning; no settings change was requested or verified.
- Hermes Agent: local source for v0.18.2 advertises authenticated `/api/sessions` and `/api/sessions/{id}/chat` plus `/v1/runs`. However, its built-in `hermes-api-server` toolset includes terminal, file write/patch, browser, code execution, skill management, delegation, and home automation tools (`toolsets.py`). The active Hermes config/API enablement and human-approval behavior were not inspected. Calling it from untrusted peer messages is therefore unsafe until a read-only API toolset is independently verified; no Hermes adapter or model call was added.

## Not inspected / deployment gate remains closed

No live TrueNAS, Tailscale, agent-chattr successor, app inventory, ports, datasets, loopback binding, Serve routes, network namespace, Hermes API configuration/key availability, or cross-machine connectivity was inspected. No claim is made that the previously mentioned host, port, route, or dataset exists. No Hermes/Pi API or RPC request was made, no daemon was installed, and no production settings or secrets were read or changed. Docker deployment and the real cross-machine demonstration are therefore not verified.

Before deployment, an operator with approved access must re-inspect TrueNAS and Tailscale live, verify Hermes local API and Pi RPC behavior against the installed versions, evaluate actual tailnet reachability, and obtain the separate M7 approval described in the plan. No route or app should be mutated before that gate.

## Rationale / risks

The selected implementation keeps the relay as a mailbox (SQLite/FastAPI) and keeps model execution local to each agent-side bridge. The room capability is a bearer secret; room membership does not authenticate sender labels. Bridge invocation is at-least-once, not exactly-once. The relay has not been exposed on any network.

## Current continuation checkpoint

- Source of truth: current `main` branch tracking `origin/main`; local working-tree edits are uncommitted. No background jobs or delegated writers are active.
- Implemented locally: relay API/store, durable bridge worker/client, CLI room create/join/send/read/status, Pi RPC adapter, skill and quick-start/discovery docs. The uncommitted change set also adds room URL/capability checks, redirect refusal for bearer-token safety, retry-safe room replacement/joining, error-status visibility, and bounded bridge configuration validation.
- Evidence: `python -B -m pytest -q -p no:cacheprovider` completed with 22 passed on the available Python 3.11.15. The project still targets Python 3.12; no `uv.lock` exists because offline metadata for FastAPI is unavailable.
- Next: build deterministic packaging only after dependency lock/base-image inputs are available; do not wire Hermes API sessions to peer text until a read-only tool configuration is verified. The installed Hermes API warns that the default API server can run terminal/file tools as the host user. Deployment and live two-device proof remain separate approval/environment gates.
