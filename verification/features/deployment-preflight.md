# TrueNAS deployment preflight

## What a user does

An operator builds the pinned relay image, fills the TrueNAS Compose template with verified host values, and proposes only loopback publication plus one persistent dataset mount.

## How to drive it

Run `bash verification/run.sh`. The control parses `deploy/compose.yaml` and checks its service count, non-root identity, read-only root, dropped capabilities, loopback host bind, `/data` mount, database path, and pinned Python base-image digest. It also builds the Python source and wheel and verifies that the wheel contains the project API client, invitation instructions, and `roomctl.py`.

## Observable proof

All static assertions and the source/wheel build pass. This is not a Docker image build. It does not prove that TrueNAS accepts the Compose file, that the target dataset is writable by UID/GID 10001, that the host port is free, or that a Tailscale path can reach the local listener. Those checks require live discovery and a separately approved deployment; follow `docs/operations.md`.
