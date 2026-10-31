# Verification map

Run `bash verification/run.sh` from the repository root. The control runs the locked Python suite and native Pi tests, builds the source and wheel into a temporary directory, and checks deployment-template safety invariants. It writes a timestamped, local-only summary under `verification/evidence/` and removes only its own temporary build directory.

A green local run is not deployment approval or live-agent acceptance. Docker is unavailable in the current environment. The control does not call Hermes, Pi, TrueNAS, or Tailscale. Complete the external gates in `docs/operations.md` before deployment or a real two-agent claim.

| Feature | Proof | Current boundary |
| --- | --- | --- |
| Public-network project join, participant directory, messages, replies, retry, and migration | [`relay-http.md`](relay-http.md) | Real loopback HTTP and isolated SQLite; no external network |
| Native session collaboration | [`native-session-collaboration.md`](native-session-collaboration.md) | Helper and host-hook checks only; no active-session exchange |
| Docker deployment template | [`deployment-preflight.md`](deployment-preflight.md) | YAML and source structure only; no container or host acceptance |
