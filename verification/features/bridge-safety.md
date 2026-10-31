# Bridge safety and recovery

## What a user does

A local Hermes or Pi bridge reads messages addressed to its agent, treats peer text as untrusted, and writes a bounded reply. Explicit completion closes the thread. Relay retries reuse a saved outbox payload.

## How to drive it

Run `bash verification/run.sh`. The bridge tests use real SQLite persistence and a fake relay/agent to exercise retry, restart, thread limits, completion, and the Hermes adapter's fake API protocol. The Pi RPC adapter tests start a fake child process and validate JSONL behavior.

## Observable proof

Pytest must pass. Tests verify no agent call after thread completion, no POST after the retry deadline, and identical outbox replay after a crash. These tests do not prove a live Hermes model call, Hermes provider session continuity, Pi provider readiness, or cross-device behavior.
