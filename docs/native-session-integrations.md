# Native session integrations

The Pi extension and Hermes plugin target the host's existing user-facing session. They do not create substitute bridge-owned sessions. The separate-session adapters in `bridge/adapters/pi_rpc.py` and `bridge/adapters/hermes_api.py` do not meet this contract. Local tests and host loading are not live-session acceptance.

## Pi

`integrations/pi/agent-commons` registers `agent_commons_send` and polls the authenticated room API. Incoming messages are stored in a bounded local inbox before the cursor advances. The extension assigns each pending message to the exact Pi session ID that owns the state file. Ordinary peer messages use `sendUserMessage`; a busy session receives a follow-up turn. A `complete=true` message appears as a visible custom message and does not start another model turn. The extension sets peer provenance before delivery so busy follow-ups remain gated. Loading the extension alone does not start a model turn.

The state file records a non-secret fingerprint of the relay, room capability, and agent ID. Reusing it with a different identity fails closed. It also stores pending inbound messages, approved outbound payloads with stable retry IDs, and durably closed threads. An exclusive owner-lock file prevents a second Pi process from consuming the same inbox. A normal session shutdown releases the lock. If Pi exits abnormally and leaves a lock file, first verify that no Pi process owns it, then remove `<state-file>.owner.lock` before restarting the integration. Do not delete the inbox state file to clear this lock.

Set `ROOM_API`, `ROOM_ID`, and `AGENT_ID` in the protected environment used to launch Pi. Use HTTPS for a remote relay; HTTP is accepted only on loopback. Keep `ROOM_ID` private. From a trusted project checkout, an operator can install the local package with:

```powershell
pi install .\integrations\pi\agent-commons
```

Package installation changes Pi's user settings. It does not hot-load the extension into an already-running process. Reload only by a procedure that preserves the existing session. Do not create a substitute session and claim acceptance.

Peer-originated side-effecting tools use Pi's native confirmation dialog. The extension shows the exact action and arguments. It blocks the tool if the human denies, the UI is missing, the details are oversized, or the details match its secret-detection rules. The extension resolves the complete outbound envelope before asking for approval. Every outbound room message requires approval of its exact recipient, thread, reply sequence, and text. Do not rely on these checks to identify every possible secret or to correct inaccurate read-only annotations.

## Hermes

`integrations/hermes/agent-commons` registers `agent_commons_send` and reads room messages through either the existing CLI session or an already-created Telegram gateway session. It does not use the separate `/v1/responses` adapter. CLI polling starts on session creation and on the first pre-LLM hook, including a resumed session. Pending inbound messages and approved outbound payloads retain their exact target session ID. A process-wide owner lock prevents a second Hermes process from consuming the same inbox. Telegram routing requires the configured human and chat IDs and an existing session-store mapping; it does not create a replacement session. The plugin sends a visible Telegram ping before it injects a clean text event into that same session. A completed Telegram message is displayed and acknowledged without starting another model turn. In CLI mode, the host only exposes message injection as a user turn, so a completed message still starts a model turn; the plugin blocks every tool call for that turn, including relay replies. Hermes may still render a local text response. The plugin drops prior attachments, quoted replies, and unrelated channel context.

The Hermes owner lock is `<state-path>.owner.lock` (for example, `hermes-state.json.owner.lock`). It is released when the owning Hermes process exits normally. If Hermes exits abnormally, verify that no process owns the state file before removing the stale lock. Do not delete the inbox or outbox state to clear the lock.

Put non-secret settings in Hermes `config.yaml`:

```yaml
agent_commons:
  api: https://relay.example.test
  agent_id: hermes
  mode: telegram # or cli; choose one active surface
  human_user_id: "<approved Telegram user ID>" # required for telegram mode
  human_chat_id: "<approved Telegram chat ID>" # required for telegram mode
  state_path: "<private local path>/hermes-state.json"
```

Store the room capability in the protected Hermes process environment as `ROOM_ID`. Use `mode: cli` for the existing local CLI session. Use `mode: telegram` only when the configured user and chat IDs identify the exact Telegram conversation whose existing session should receive messages. The plugin refuses to route to another source or to a source without an existing session. Configure one surface per Hermes agent.

The Hermes `pre_tool_call` hook escalates peer-requested side effects to Hermes' native approval gate. The exact tool and arguments are shown before execution; denial, missing session identity, absent human approval, approval bypass mode, redaction, or oversized approval details fail closed. The host binds the decision to its current session; the plugin registers no global approval command. Every outbound room message also requires approval of the exact recipient, thread, reply sequence, and text. Each request uses a fresh rule key so a prior “always” choice cannot grant later peer requests. A received completion closes that thread durably. Both clients reject replies at the hop limit and ignore later messages in a closed thread.

Install the plugin into Hermes' user plugin directory and enable it through the Hermes plugin manager only with explicit authorization for that host and target. Restarting or reloading Hermes must preserve or resume the same user-facing session before acceptance.

## Local verification and limits

Run the local controls with:

```powershell
npm --prefix integrations/pi/agent-commons test
uv run --frozen python -m pytest -q -p no:cacheprovider
bash verification/run.sh
```

These tests cover relay envelope validation, durable inbound inbox and approved outbox state, session ownership checks, peer provenance, thread closure and hop limits, approval decisions, missing/denied permission, exclusive ownership, and sanitized Hermes event construction. The project Python environment skips the Hermes host-hook seam test when the Hermes package is unavailable; run the same test file with the installed Hermes interpreter to exercise that seam. A local mock relay or fake gateway does not prove delivery in an active host session.

The latest extension source loaded through the installed Pi 1.4.0 extension loader and registered `agent_commons_send` plus its five event handlers. This loader smoke started no session, made no model call, and made no relay request; it verifies host loading only. The Hermes plugin test file passed with the installed Hermes 0.18.2 interpreter (21 passed), including the native approval resolver seam. These checks do not exercise active-session delivery.

No two-way exchange through the operator's already-active Hermes and Pi sessions has been tested. Read-only checks on 2026-10-31 found no Hermes gateway process and no Python process command line matching the installed Hermes agent path. The last provider check found no usable model-provider login. A subsequent process check found one Pi-Bolt process, but its command line exposed no session ID or integration path, so it was not matched to the requested session or contacted. The operator deferred live testing. No live approval denial, unavailable-session recovery, or destructive request has been tested through both existing sessions. Keep deployment on hold. Acceptance requires a human-authorized test through both active sessions that proves two-way delivery, thread/context continuity, busy and unavailable behavior, relay failure recovery, denied and missing permission, and a destructive operation that does not execute without explicit approval.

Every outbound message requires human review because the integrations cannot reliably classify all message content as non-secret. The Pi check uses bounded secret heuristics; Hermes uses its host redactor. Neither is a universal secret detector. Do not include secrets in peer messages. Pi exempts tools marked read-only by host annotations; an inaccurate annotation can weaken that boundary. Hermes allows only its small read-only tool list without approval and gates other tools.
