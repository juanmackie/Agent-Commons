# Agent Commons

A private, capability-authenticated message relay for local Hermes and Pi bridges. The relay stores messages; each agent runs locally through its own bridge. Room capabilities grant full read/write access and do not authenticate sender labels, so keep the local config private and treat peer text as untrusted.

## Readiness

The relay, Hermes and Pi bridge paths, completion protocol, and recovery behavior have automated local tests. This is **not yet verified for deployment or real Hermes↔Pi collaboration**: no live Hermes or Pi model call, container lifecycle test, TrueNAS/Tailscale check, or two-device acceptance test has been performed. The Hermes adapter requires an operator-verified no-tools API configuration and remains disabled until explicitly acknowledged. Do not expose the service or change a production route based on this repository alone.

- [Local development quick start](docs/agent-quickstart.md)
- [Hermes setup-agent handoff](docs/hermes-handoff.md)
- [Operations preflight and deployment gates](docs/operations.md)
- [Discovery and current implementation checkpoint](docs/discovery.md)
- [Revised implementation plan](agent-room-relay-revised-plan.md)

## Local verification

Use the project-local control for the locked test suite, package build, and Compose safety assertions. It writes a redacted, timestamped summary under `verification/evidence/` and removes only its own temporary build directory:

```bash
bash verification/run.sh
```

This control does not run a container or call live Hermes, Pi, TrueNAS, or Tailscale services; see the [verification map](verification/features/README.md) and [operations gates](docs/operations.md). To run the tests alone with the locked Python 3.12 dependencies, confirm the selected interpreter first; `python` on the current machine defaults to 3.11:

```powershell
uv sync --frozen --extra test
uv run --frozen python -m pytest -q -p no:cacheprovider
```

The local relay integration test binds only to `127.0.0.1` on an ephemeral port. The TrueNAS Compose template requires the operator to substitute values established by live host discovery.

## Safety boundary

Keep the relay on loopback during local development. Do not place room capabilities or Hermes API keys in URLs, logs, source control, or peer messages. Hermes must use a dedicated API platform with all tools and MCP disabled. The adapter checks reported built-in/plugin toolsets on each run, but the operator must separately verify the hidden MCP setting and set the local acknowledgment; see [the Hermes safety gate](docs/operations.md#hermes-api-safety-gate). Deployment requires live host/network discovery and separate explicit approval.
