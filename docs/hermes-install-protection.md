# Hermes Plugin Install Protection

The plugin install must refuse to overwrite an existing directory without `--force`.

Before copying `integrations/hermes/agent-commons` into `<active-Hermes-home>/plugins/agent-commons`, inspect the existing directory:
- If it exists and differs from the intended version, preserve it or ask before overwriting.
- Use `cp -r` or `shutil.copytree` with checks, or run `python -c "import shutil, sys; shutil.copytree(...)"` only after confirming.
- The plugin requires explicit mode (`cli`, `dashboard`, or `telegram`) in `config.yaml`.
- Verify the live session target before claiming delivery works.

Telegram binding:
- After plugin activation, send one normal message in the existing Telegram chat.
- Verify it reaches the existing session before running the unsolicited probe.
- The route-binding message alone does not pass the automatic-delivery check.
