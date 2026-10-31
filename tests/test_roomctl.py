import json

import roomctl
from bridge.store import BridgeStore


CAPABILITY = "x" * 43


def test_create_persists_capability_without_printing_it(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "agent-room-relay" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: config_path)
    monkeypatch.setenv("AGENT_ID", "pi")
    monkeypatch.setattr(roomctl.RelayClient, "create_room", lambda api: {"room_id": CAPABILITY, "idle_ttl_seconds": 604800})

    assert roomctl.main(["create", "--api", "http://127.0.0.1:8080"]) == 0
    assert json.loads(config_path.read_text())["room_id"] == CAPABILITY
    assert CAPABILITY not in capsys.readouterr().out


def test_retry_command_requeues_only_an_explicitly_failed_local_message(tmp_path, monkeypatch, capsys):
    state_path = tmp_path / "bridge.sqlite3"
    store = BridgeStore(state_path)
    store.ingest([{"seq": 1, "thread_id": "thread-1", "sender": "hermes", "recipient": "pi", "hop": 0, "text": "task"}], 1)
    store.mark_failed(1, "thread_budget_exhausted")
    store.db.close()
    monkeypatch.setenv("BRIDGE_STATE_DB", str(state_path))

    assert roomctl.main(["retry", "--seq", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == {"seq": 1, "status": "pending"}
    reopened = BridgeStore(state_path)
    assert reopened.status()["failed_count"] == 0
    assert reopened.status()["pending_count"] == 1
    reopened.db.close()


def test_join_uses_explicit_url_and_validates_capability_before_saving(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "agent-room-relay" / "config.json"
    source = tmp_path / "shared-config.json"
    source.write_text(json.dumps({"api": "https://attacker.example", "room_id": CAPABILITY}))
    monkeypatch.setattr(roomctl, "config_path", lambda: config_path)
    monkeypatch.setenv("AGENT_ID", "pi")
    calls = []

    class Client:
        def __init__(self, api, room_id):
            calls.append((api, room_id))
        def read(self, after):
            return {"messages": [], "next_after": after}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join", "--api", "https://relay.tailnet", "--from", str(source)]) == 0
    saved = json.loads(config_path.read_text())
    assert calls == [("https://relay.tailnet", CAPABILITY)]
    assert saved == {"api": "https://relay.tailnet", "room_id": CAPABILITY, "agent_id": "pi"}
    output = capsys.readouterr().out
    assert CAPABILITY not in output and "attacker.example" not in output
