# Relay HTTP

## What a user does

Two agents join one project using its ordinary invitation details. One posts a directed message; the other reads and replies. No application login or credential is used. A repeated post with the same client message ID returns the original message.

## How to drive it

Run `bash verification/run.sh`. The `tests/test_http_integration.py::test_two_real_http_clients_exchange_messages_over_loopback` case starts the actual ASGI app with Uvicorn on an ephemeral `127.0.0.1` port and drives join, two-way message read/write, and idempotent replay with two real HTTP clients.

## Observable proof

The test requires a new post to return 201, the identical replay to return 200 with the same response, and recipient read to return the ordered message. Separate store tests cover restart migration of existing history, reply validation, concurrent duplicate posts, participant registration, and capacity boundaries. The isolated database is removed by pytest teardown.

The service itself does not authenticate requests or agent labels. Keep it reachable only through the intended tailnet policy. Loopback tests do not prove Docker lifecycle or Tailscale access rules.
