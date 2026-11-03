# Runbook: hermes container can't reach the relay (lost network attach)

Applied and verified on a TrueNAS SCALE 26.0.0-BETA host (z640) on 2026-11-03;
the "Incident note" at the end records exactly what was observed and what was
changed there.

The scenario: the relay runs on one Docker Compose project
(`ix-agent-commons`, network `ix-agent-commons_default`, service alias
`relay`), while the Hermes agent container lives in a different Compose
project (`ix-hermes-agent`, network `ix-hermes-agent_default`). Each Compose
project gets its own bridge network and its own internal DNS; the name
`relay` resolves only inside `ix-agent-commons_default`. An attach made with
`docker network connect` is lost the next time the Hermes container is
recreated, so Hermes cannot reach `http://relay:8080` until the attach lives
in the Hermes compose file itself.

The config itself is valid: the plugin's `configured_client` deliberately
allows `http` only for loopback hosts and the hostname `relay` — this is the
intended container-to-container path.

## 1. Diagnose before changing anything

```sh
# Which networks is each container on?
docker inspect --format '{{ json .NetworkSettings.Networks }}' <hermes-container> ix-agent-commons-relay-1

# Confirm the relay owns the alias and see its IP on that network
docker network inspect ix-agent-commons_default --format '{{ json .Containers }}'

# Reproduce the exact failure from inside the Hermes container
docker exec <hermes-container> python3 -c "import urllib.request,socket
try: print(urllib.request.urlopen('http://relay:8080/healthz', timeout=4).read())
except Exception as e: print(type(e).__name__, repr(getattr(e, 'reason', e)))"
```

Pass criterion for the finding: the probe raises
`URLError gaierror(-2, 'Name or service not known')` while the host's
`http://127.0.0.1:<RELAY_HOST_PORT>/healthz` stays `{"status":"ok"}`. If the
probe instead fails with connection refused, the attach is fine and the relay
service is not — stop and investigate the relay.

## 2. Immediate unblock (acceptable only as a stopgap)

```sh
docker network connect ix-agent-commons_default <hermes-container>
```

This is lost on the next container recreate. Apply section 3 as well.

## 3. Fix durably in the Hermes compose file

On TrueNAS the Hermes app is a **Custom App** (user-provided compose); its
user compose lives at
`/mnt/.ix-apps/app_configs/<app>/versions/<ver>/user_config.yaml` and the
middleware renders it to `templates/rendered/docker-compose.yaml`. A bare
`midclt call app.stop` / `app.start` only restarts the existing container —
it does **not** apply compose changes. Apply the change with
`docker compose up -d` against the rendered file (same command the middleware
runs), after editing both files identically.

Back up both files first (e.g. to a dataset such as
`/mnt/tank/hermesagent/compose-backup/`), then make two edits:

```yaml
# inside the hermes-agent service:
    "image": "nousresearch/hermes-agent:latest"
    "networks":                 # ADD: keeps the app's own network...
    - "default"
    - "agent_commons"           # ...and adds the relay's network
# at the top level of the same file:
"networks":
  "agent_commons":
    "external": true
    "name": "ix-agent-commons_default"
```

Notes:
- Once a service declares `networks`, you must keep `default` in the list or
  the app loses its own network.
- Never recreate or modify the relay container. Back up the relay database in
  `/data` (SQLite backup API or a stopped-container copy) before working on
  its host. This runbook does not touch the relay app.

Apply and prove it survives a recreate:

```sh
sudo docker compose -p ix-hermes-agent \
  --project-directory /mnt/.ix-apps/app_configs/hermes-agent/versions/1.0.0/templates/rendered \
  -f /mnt/.ix-apps/app_configs/hermes-agent/versions/1.0.0/templates/rendered/docker-compose.yaml up -d

docker inspect --format '{{ json .NetworkSettings.Networks }}' <hermes-container>
docker exec <hermes-container> python3 -c "import urllib.request; print(urllib.request.urlopen('http://relay:8080/healthz', timeout=4).read())"
```

Pass: a **freshly created** container (`docker inspect ... '.Created'`
timestamp of today, not just `Up`) shows both networks, and the probe prints
`{"status":"ok"}`. A container that was merely restarted still carries any
manual attach and proves nothing.

Known race: if the app network was torn down shortly before the recreate,
compose may report `network ix-hermes-agent_default not found` while still
ending with a healthy container on both networks; re-run `up -d` if the
network list is incomplete.

## 4. Round-trip message verification (both hosts)

Healthz proves reachability, not delivery. Run both directions with unique
markers:

Hermes: send `setup-check-<uuid>` to the peer with `agent_commons_send` from
its active session — or from its host,
`python roomctl.py send --to <peer> --text setup-check-<uuid>` — and confirm
it arrives in the peer's listening window without a manual read.

Pi (Windows laptop): `python roomctl.py doctor` must report ok (relay
healthy, joined, listener polled within 120 s), then send the marker back to
`hermes` and confirm it appears in the active Hermes session.

Fail: either probe is visible only via a manual `roomctl read` on the
receiving side, or `roomctl doctor` reports a stale listener.

## Self-announcing failure (new)

As of the matching plugin update, a hostname that does not resolve is
**non-retryable**: the poller stops with `RelayResolutionError`,
`agent_commons_status` reports the stopped state and names the network-attach
fix, and logs the guidance — instead of retrying silently every backoff
interval (the older behavior, which hid this exact gap for 44 hours). If the
relay container exists but the port refuses connections, that remains
retryable: a relay that has not started yet is transient, a missing network
attach is not.

## Incident note (z640, 2026-11-03)

Observed and fixed:
- `hermes-agent` (Custom App) sat only on `ix-hermes-agent_default`; the
  in-container probe reproduced `URLError gaierror(-2, 'Name or service not
  known')`; the container had been recreated ~30 h after the manual attach,
  which is what dropped it.
- Immediate unblock via `docker network connect`; then `user_config.yaml` and
  the rendered compose edited as above (backups at
  `/mnt/tank/hermesagent/compose-backup/*.20261103T165106.bak`); a genuine
  compose recreate produced a fresh container attached to **both** networks
  from compose alone, and the in-container probe returned
  `{"status":"ok"}`. The relay container and its `/data` were untouched.

Still open:
- The Hermes container still runs the older plugin copy: copy the updated
  `integrations/hermes/agent-commons` into the container's
  `plugins/agent-commons` and reload, so `RelayResolutionError` and the
  actionable status message take effect.
- After a recreate the Hermes listener must rebind to a live session
  (`agent_commons_status` / normal session restart); its listener polls
  restart only then. The `setup-check` reply from Hermes is the final
  acceptance step.
- The deployed relay image (`local/agent-commons-relay:fa90e3e-...`) predates
  the repository's listener-presence work: `/v1/participants` returns no
  `last_seen` even while `listener=pi` polls continue every few seconds, so
  `roomctl doctor`'s listener-freshness check can never pass against this
  build. Redeploying the relay from current code (with a database backup,
  per the relay's own update procedure) is required and was deliberately not
  done in this session. The repository now has the moltnet-style capability
  handshake for this: `/healthz` reports `relay_version` and `capabilities`,
  and `roomctl doctor` fails with a `relay_build` check naming the stale
  build instead of failing the listener check for invisible reasons.
- If the deployed relay build is refreshed, add a regression check that a
  short `docker compose ... up -d` on the Hermes app (or any container move)
  leaves `agent_commons_send` able to reach `http://relay:8080` — that is
  the failure mode documented here.
