# Plan: Handoffs + read-only status dashboard

Borrowed from Okto Nexus (local-first MCP coordination hub). YAGNI-scoped: only the two features that close a real gap in Agent Commons today — explicit handoffs between agents and a read-only view of project state. Everything else (artifacts store, governance, embeddings, wake-on-message) is deferred until there's concrete pain.

## Why

Agent Commons already has: projects, participants, messages, history, replies, Pi/Hermes listeners, `roomctl` CLI. What's missing:
- No first-class way to say "I'm handing this task to you, here's the context" — today it's just a message with no structure or ack.
- No single view of who's online, what's pending, recent activity. Operators must poll `roomctl status` / `roomctl projects` per host.

## Scope (v1)

### Handoffs
- New message type `handoff` with fields: `from`, `to`, `summary`, `context_id` (links to thread), `status` (open/accepted/done), `accepted_by`, `accepted_at`.
- MCP/CLI: `roomctl handoff create`, `roomctl handoff list`, `roomctl handoff accept <id>`, `roomctl handoff done <id>`.
- Listener: when a handoff targets an agent, deliver it like a message; agent can accept/done via the same tools.
- Storage: new `handoffs` table in SQLite; no separate service.

### Dashboard (read-only)
- Single HTML page served by the relay at `/dashboard` (no auth beyond existing Tailscale boundary — same trust model as the relay).
- Shows: projects, participants + last-seen, open handoffs, recent messages (last N per project), relay health.
- No writes from the dashboard. No JS build step — server-rendered or a single static file.

## Out of scope (deferred)

- Artifacts store (file attachments beyond what messages already carry).
- Governance/permissions beyond Tailscale network boundary.
- Wake-on-message / process supervisor (agentmbx) — needs a host agent runner we don't own.
- Embeddings/semantic search (guild) — FTS on messages is enough until proven otherwise.
- Multi-relay federation.

## Acceptance criteria

1. An agent can create a handoff to a peer; peer sees it; peer can accept then mark done; state persists across relay restart.
2. `/dashboard` renders project/participant/handoff/recent-message state from live DB; read-only (no POST handlers).
3. Existing tests still pass; add tests for handoff lifecycle + dashboard render.
4. No new runtime dependency beyond what's already in requirements.

## Notes

- Trust model unchanged: relay stays on loopback + Tailscale; no public exposure, no Tailscale Funnel.
- DB migration: add `handoffs` table on startup if missing (idempotent), consistent with existing schema bootstrap.
- Keep `roomctl` as the single CLI entry; no second binary.
