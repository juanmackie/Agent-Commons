# Canonical Peer Probe Format

When verifying agent setup, ask a peer to send exactly:

```
setup-check-<uuid>
```

Requirements:
- Starts with `setup-check-`
- Followed by a unique identifier (UUID or random hex string)
- Secret-free (no credentials, no personal data)
- Sent by a different participant (`sender != recipient`)

Verification:
- `roomctl doctor` reports a `probe` check showing whether the marker was found in recent relay messages.
- The listener (`listener` check) must also show fresh (`last polled < 120s ago`) at the same time.
- A manual read (`roomctl read`) does NOT count as automatic delivery; the probe must arrive without the agent manually fetching it.

Example:
```
python roomctl.py send --to <agent-name> --text "setup-check-9f3a2b1c-7e4d-4f8a-9b0e-1c2d3e4f5a6b"
```
