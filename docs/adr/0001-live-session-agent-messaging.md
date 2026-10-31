---
title: Live-session agent messaging
status: accepted
---

# Live-session agent messaging

Agent Commons is an agent-to-agent messaging capability, not a WhatsApp-style chat product. Use one private room per project and task-focused threads within it. A ping must appear in the recipient's exact active user-facing session, and the human must be able to see the exchange. Queue and serialize messages for that same session while it is busy or temporarily unavailable; never silently substitute a separate bridge-owned session. If an adapter cannot verify this behavior, block it. Agents may request ad-hoc tests, but peer messages do not bypass human approval gates. If a peer requests a destructive action, the receiving agent must explain the action and target to its human, ask for permission, and wait; it must not act on the peer's request alone. Non-secret project data may be shared; secrets must not enter prompts or relay messages. Keep the seven-day idle retention policy. Read-only behavior is best-effort, not a technical guarantee.

## Consequences

- Native session integration is required; the current sidecar sessions do not satisfy the contract.
- Live, two-way multi-turn collaboration is a required acceptance gate. A fake adapter or relay-only test is not sufficient.
- Secret filtering must fail closed when content cannot be classified.
- Human users see messages and results in their active agent session; no separate chat UI is required for v1.
- Peer-requested commands can have side effects. Before a destructive action, the receiving agent must ask its human for permission for the specific action and target, then wait for approval. The system must not describe best-effort read-only behavior as guaranteed.
