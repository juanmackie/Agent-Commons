"""Environment-configured single-worker bridge runner."""

import os

from bridge.client import RelayClient
from bridge.store import BridgeStore
from bridge.worker import BridgeWorker


def run(adapter):
    from roomctl import config_path, read_config
    config = read_config() if config_path().exists() else {}
    agent_id = os.getenv("AGENT_ID") or config.get("agent_id")
    api = os.getenv("ROOM_API") or config.get("api")
    room_id = os.getenv("ROOM_ID") or config.get("room_id")
    state_path = os.getenv("BRIDGE_STATE_DB")
    missing = [name for name, value in (("AGENT_ID", agent_id), ("ROOM_API", api), ("room capability config", room_id), ("BRIDGE_STATE_DB", state_path)) if not value]
    if missing:
        raise RuntimeError("missing bridge configuration: " + ", ".join(missing))
    relay = RelayClient(api, room_id)
    state = BridgeStore(state_path)
    worker = BridgeWorker(relay, state, adapter, agent_id=agent_id,
                          poll_seconds=float(os.getenv("POLL_SECONDS", "3")),
                          max_auto_replies_per_thread=int(os.getenv("MAX_AUTO_REPLIES_PER_THREAD", "4")),
                          max_hop=int(os.getenv("MAX_HOP", "8")))
    worker.run_forever()


def main():
    from roomctl import config_path, read_config
    config = read_config() if config_path().exists() else {}
    agent_id = os.getenv("AGENT_ID") or config.get("agent_id")
    if agent_id not in {"pi", "hermes"}:
        raise SystemExit("AGENT_ID must be pi or hermes")
    state_path = os.getenv("BRIDGE_STATE_DB")
    if not state_path:
        raise SystemExit("BRIDGE_STATE_DB is required")
    if agent_id == "pi":
        session_dir = os.getenv("PI_SESSION_DIR", state_path + ".pi-sessions")
        from bridge.adapters.pi_rpc import PiRpcAdapter
        run(PiRpcAdapter(session_dir))
        return

    api_key = os.getenv("HERMES_API_KEY")
    toolsets_verified = os.getenv("HERMES_API_TOOLSETS_VERIFIED") == "none"
    from bridge.adapters.hermes_api import HermesApiAdapter
    try:
        adapter = HermesApiAdapter(
            os.getenv("HERMES_API_URL", "http://127.0.0.1:8642"),
            api_key or "",
            toolsets_verified=toolsets_verified,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    run(adapter)


if __name__ == "__main__":
    main()
