# Verification map

Run `bash verification/run.sh` from the repository root. The control runs the locked test suite, builds the source and wheel into a temporary directory, and checks deployment-template safety invariants. It writes a timestamped, local-only summary under `verification/evidence/` and removes only its own temporary build directory.

A green local run is not deployment approval or live-agent acceptance. Docker is unavailable in the current environment. The control does not call Hermes, Pi, TrueNAS, or Tailscale. Complete the external gates in `docs/operations.md` before deployment or a real two-agent claim.

| Feature | Proof | Current boundary |
| --- | --- | --- |
| Relay room create/read/write and capability denial | [`relay-http.md`](relay-http.md) | Real loopback HTTP through Uvicorn; isolated temporary SQLite database |
| Agent bridge retries and completion | [`bridge-safety.md`](bridge-safety.md) | Hermes API and Pi subprocess are fakes; no model call |
| TrueNAS deployment template | [`deployment-preflight.md`](deployment-preflight.md) | YAML and source structure only; no container or host acceptance |
