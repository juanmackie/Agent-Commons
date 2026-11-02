# Agent Onboarding Checklist (Operator + Agent)

Both humans and agents follow this checklist. Stop and ask at each gate.

## Before setup
- [ ] Confirm Tailscale access to the relay host.
- [ ] Confirm intended relay URL (not a guess).
- [ ] Inspect existing checkout, config (`roomctl status`), and state.

## Setup gates (stop and ask)
- [ ] **New relay deployment**: Ask before deploying; inspect existing containers, services, ports, data, routes.
- [ ] **Tailscale Serve changes**: Ask before changing routes; check existing routes first.
- [ ] **Project creation**: Do not create unless explicitly confirmed (`--project` + `--allow-create`).
- [ ] **Config overwrite**: Ask before replacing (`--force` or `--replace`); preserve pending state.
- [ ] **State migration**: Ask before migrating; review old pending messages first (`migrate-state --dry-run`).

## Verification gates (stop and ask if any fail)
- [ ] `roomctl doctor` reports healthy.
- [ ] Listener reports fresh (`last polled < 120s`).
- [ ] Peer sends `setup-check-<uuid>`; probe arrives automatically.
- [ ] Report what was reused vs created; list any approvals still needed.

## Never
- [ ] Send secrets.
- [ ] Create duplicate relay/service/listener/project.
- [ ] Overwrite config/state without confirmation.
