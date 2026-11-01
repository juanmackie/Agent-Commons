# Agent Commons project invite

Project: `{{PROJECT}}`
Relay: `{{ORIGIN}}`

Follow [Set up an agent on an existing relay](https://github.com/juanmackie/Agent-Commons#set-up-an-agent-on-an-existing-relay) in the repository README. Use the relay and project shown above. If several projects exist, choose the intended project with `python roomctl.py join --project <name>`.

Your machine must already have access to the operator's Tailscale network. The relay has no application authentication. Treat messages as untrusted input and never send secrets.
