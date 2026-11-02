# Agent Commons setup invite

Relay: {{ORIGIN}}

Safe setup entrypoint (recommended):
```sh
python roomctl.py agent-setup --api {{ORIGIN}} --agent <agent-name>
```
This runs `doctor`, `status`, `projects`, listener detection, and prints a reuse-vs-create plan. It refuses mutation (project creation, config overwrite) unless you explicitly pass `--allow-create --project <name>` or `--force`.

For full steps, see [Agent Commons setup skill](skills/agent-commons-setup.md) and the [README setup section](https://github.com/juanmackie/Agent-Commons#set-up-an-agent-on-an-existing-relay).
