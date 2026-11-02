# Native session collaboration

## User-visible behavior

The Pi extension and Hermes plugin are intended to deliver relay messages to the configured, existing user-facing session. They do not create bridge-owned sessions. A received peer message keeps its sender, thread, sequence, hop count, and completion marker. Peer-requested side effects and every outbound room message require the host's human-approval flow. This describes the implementation contract; it is not evidence that a live session received a message.

## Local proof

Run `bash verification/run.sh` from the repository root. It runs Pi helper tests, Hermes plugin tests, the project suite, and package/deployment-template checks. The Hermes approval-seam test runs when the installed Hermes package is available; otherwise pytest skips it. For the installed Hermes host, run the plugin tests with that host's Python interpreter.

Historical local checks on 2026-10-31 used the installed Pi 1.4.0 loader to load the extension and register its send tool and event handlers, and the installed Hermes 0.18.2 interpreter to exercise the host approval resolver. These checks used test doubles for relay/gateway boundaries. They proved host loading and local approval logic only, not message delivery through a live session; those versions are not asserted to be current.

## Live acceptance procedure

Only perform this after the operator authorizes the exact test messages and target sessions. Keep messages harmless and secret-free. Do not start a substitute session or change deployment/network configuration.

1. Record the existing Pi and Hermes session identifiers without recording message text or credentials.
2. Send a harmless message from Hermes to Pi. Confirm it appears in that existing Pi transcript and retains its relay thread metadata.
3. Reply from Pi and confirm the reply appears in the same Hermes session. Continue enough exchanges to prove thread and hop continuity.
4. Repeat while the recipient is busy, then with the recipient unavailable. Confirm queued messages remain bound to the original session and arrive only when it resumes.
5. Simulate relay loss and recovery. Confirm pending inbox/outbox entries remain durable and outbound retries reuse the same client message ID.
6. Send a harmless request for a destructive operation against a disposable target. Confirm the receiving human sees the exact action and target, denies it, and that no tool executes. Repeat with approval UI unavailable and confirm the action remains blocked.
7. Record only redacted session identifiers, message sequence metadata, approval outcome, and observed recovery. Do not record secrets or message bodies.

## Current status

Live acceptance is not complete. Read-only checks on 2026-10-31 found no Hermes gateway process and no Python process command line matching the installed Hermes agent path. The last provider check found no usable Hermes model-provider login. A subsequent process check found one Pi-Bolt process, but its command line exposed no session ID or integration path, so it was not matched to the required session or contacted. The operator chose to defer live testing. No unsolicited message was observed arriving in a live Pi or Hermes session, and no active-session exchange, destructive-request denial, or unavailable-session recovery was verified. These are historical checks, not current host facts. Keep deployment and live network changes gated. Follow [native integration limits](../../docs/native-session-integrations.md) and [operations gates](../../docs/operations.md).
