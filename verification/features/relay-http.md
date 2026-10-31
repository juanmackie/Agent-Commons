# Relay HTTP

## What a user does

Two agents share a private room. One creates it, posts a message addressed to the other, and the other reads it. A repeated post with the same client message ID returns the original message. Invalid room capabilities are denied.

## How to drive it

Run `bash verification/run.sh`. Its `tests/test_http_integration.py::test_two_real_http_clients_exchange_messages_over_loopback` case starts the actual ASGI app with Uvicorn on an ephemeral `127.0.0.1` port. It drives room creation, message post, idempotent replay, recipient read, and invalid-capability denial using two real HTTP clients.

## Observable proof

The test requires a new post to return 201, the identical replay to return 200 with the same response, the recipient read to contain that response, and invalid authorization to return 401. The isolated room database is temporary and removed by pytest teardown.
