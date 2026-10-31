#!/usr/bin/env bash
set -Eeuo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
mkdir -p verification/evidence
timestamp=$(date -u +'%Y%m%dT%H%M%SZ')
evidence="verification/evidence/local-${timestamp}.txt"
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/agent-commons-verify.XXXXXX")
trap 'rm -rf -- "$tmpdir"' EXIT

{
  echo "Agent Commons local verification"
  echo "UTC: $timestamp"
  echo "Scope: local tests, package build, and static Compose checks; no deployment or live model call"
  echo

  uv --version
  uv sync --frozen --extra test
  uv lock --check
  uv run --frozen python -c 'import sys; print("Python:", sys.version.split()[0]); assert sys.version_info >= (3, 12)'
  uv run --frozen python -m pytest -q -p no:cacheprovider
  uv run --frozen python - <<'PY'
from pathlib import Path
import re
import yaml

compose = yaml.safe_load(Path("deploy/compose.yaml").read_text(encoding="utf-8"))
assert set(compose["services"]) == {"relay"}
service = compose["services"]["relay"]
assert service["user"] == "10001:10001"
assert service["read_only"] is True
assert service["cap_drop"] == ["ALL"]
assert service["ports"][0]["host_ip"] == "127.0.0.1"
assert service["ports"][0]["target"] == 8080
assert service["volumes"][0]["target"] == "/data"
assert service["volumes"][0]["bind"]["create_host_path"] is False
assert service["environment"]["ROOMS_DB"] == "/data/rooms.sqlite3"
base = Path("Dockerfile").read_text(encoding="utf-8").splitlines()[0]
assert re.fullmatch(r"FROM python:3\.12-slim-bookworm@sha256:[0-9a-f]{64}", base)
print("Compose structure, loopback bind, non-root/read-only policy, persistent mount, and pinned base: PASS")
PY

  mkdir -p "$tmpdir/dist"
  if uv build --out-dir "$tmpdir/dist" >"$tmpdir/build.log" 2>&1; then
    echo "Source/wheel build: PASS"
  else
    echo "Source/wheel build: FAIL"
    tail -n 40 "$tmpdir/build.log"
    exit 1
  fi
  uv run --frozen python - "$tmpdir/dist" <<'PY'
from pathlib import Path
import sys
import zipfile

wheels = list(Path(sys.argv[1]).glob("*.whl"))
assert len(wheels) == 1, f"expected one wheel, got {len(wheels)}"
names = set(zipfile.ZipFile(wheels[0]).namelist())
assert {"roomctl.py", "bridge/adapters/hermes_api.py", "bridge/adapters/pi_rpc.py"} <= names
print("Source/wheel build and installed module inclusion: PASS")
PY

  if command -v docker >/dev/null 2>&1; then
    echo "Docker CLI available; image/container lifecycle is a separate deployment gate and was not run."
  else
    echo "Docker image/container lifecycle: NOT RUN (Docker CLI unavailable)."
  fi
  echo "Live Hermes/Pi calls and TrueNAS/Tailscale acceptance: NOT RUN (external gates; see docs/operations.md)."
  echo "Evidence saved: $evidence"
} 2>&1 | tee "$evidence"
