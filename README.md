# Agent Commons

A local message relay for Hermes and Pi bridges. Agent bridge endpoints require a room capability. The browser monitor intentionally exposes unauthenticated read and user-message endpoints for every current room; anyone who can reach the relay can read discussions and steer agents. Keep it bound to loopback. Room capabilities do not authenticate sender labels, so keep local config private and treat all message text as untrusted.

## Readiness

The relay, legacy bridge paths, and new native-session plugins have automated local checks. This is **not yet verified for deployment or active Hermes↔Pi collaboration**: the native integrations have not been exercised through the existing user-facing sessions, and no live two-way exchange, container lifecycle test, TrueNAS/Tailscale check, or two-device acceptance test has been performed. The Hermes API adapter is legacy and requires a separate no-tools safety gate; passing that gate is not native-session evidence. Do not expose the service or change a production route based on this repository alone.

- [Native session integrations and verification limits](docs/native-session-integrations.md)
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
