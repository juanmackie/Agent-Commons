import json

import roomctl


def test_join_saves_project_address_and_registers_without_secret(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "agent-commons" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    monkeypatch.setenv("AGENT_ID", "pi")
    calls = []

    class Client:
        def __init__(self, api, project, agent):
            calls.append((api, project, agent))

        def join(self):
            return {
                "project": "demo",
                "agent": "pi",
                "created": True,
                "participants": [{"agent_id": "pi"}],
            }

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert (
        roomctl.main(["join", "--api", "https://relay.tailnet", "--project", "demo"])
        == 0
    )
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet",
        "project": "demo",
        "agent_id": "pi",
    }
    assert calls == [("https://relay.tailnet", "demo", "pi")]
    assert "room_id" not in capsys.readouterr().out


def test_setup_then_create_project_is_a_two_step_onboarding_flow(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "agent-commons" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    calls = []

    class Client:
        def __init__(self, api, project, agent):
            self.args = (api, project, agent)

        def join(self):
            calls.append(self.args)
            return {
                "project": "crew",
                "agent": "pi",
                "created": True,
                "participants": [{"agent_id": "pi"}],
            }

        def create(self, allow_existing=False):
            return self.join()

        def healthz(self):
            return {"status": "ok"}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["setup", "--api", "https://relay.tailnet", "--agent", "pi"]) == 0
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet",
        "agent_id": "pi",
    }
    assert calls == []

    assert roomctl.main(["create-project", "--project", "crew"]) == 0
    assert calls == [("https://relay.tailnet", "crew", "pi")]
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet",
        "project": "crew",
        "agent_id": "pi",
    }
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["created"] is True


def test_setup_refuses_an_unreachable_relay_and_writes_no_config(
    tmp_path, monkeypatch
):
    path = tmp_path / "agent-commons" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: path)

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            raise roomctl.RelayError("relay unavailable: URLError")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["setup", "--api", "https://relay.tailnet", "--agent", "pi"]) == 1
    assert not path.exists()


def test_join_replace_preserves_unresolved_outbound_message(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"api": "https://relay.tailnet", "project": "old", "agent_id": "pi"})
    )
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    db = roomctl.state_db()
    db.execute("INSERT INTO pending VALUES (?,?)", ("id", "{}"))
    db.commit()
    db.close()
    assert (
        roomctl.main(
            [
                "join",
                "--api",
                "https://relay.tailnet",
                "--project",
                "new",
                "--agent",
                "pi",
                "--replace",
            ]
        )
        == 1
    )
    assert json.loads(path.read_text())["project"] == "old"


def test_default_thread_send_reuses_persisted_payload_after_ambiguous_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"}
        )
    )
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    sent = []

    class Client:
        def __init__(self, *_args):
            self.agent_id = "pi"

        def post(self, payload):
            sent.append(payload.copy())
            if len(sent) == 1:
                raise roomctl.RelayError("relay unavailable")
            return {"seq": 1}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    args = ["send", "--to", "hermes", "--text", "check this"]
    assert roomctl.main(args) == 1
    assert roomctl.main(args) == 0
    assert sent[0] == sent[1]
    assert sent[0]["thread_id"] and sent[0]["client_msg_id"]


def test_join_refuses_to_replace_existing_local_config_without_flag(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "config.json"
    path.write_text('{"api":"https://relay.tailnet","project":"old","agent_id":"pi"}')
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    assert (
        roomctl.main(
            [
                "join",
                "--api",
                "https://relay.tailnet",
                "--project",
                "new",
                "--agent",
                "pi",
            ]
        )
        == 1
    )
    assert json.loads(path.read_text())["project"] == "old"
