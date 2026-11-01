import json

import roomctl


def _config(tmp_path, monkeypatch, value=None):
    path = tmp_path / "agent-commons" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    if value is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    return path


def test_setup_generates_name_and_rerun_reuses_saved_name(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["setup", "--api", "https://relay.tailnet"]) == 0
    first = json.loads(path.read_text())
    assert first["agent_id"].startswith("agent-") and len(first["agent_id"]) == 14
    assert roomctl.main(["setup", "--api", "https://relay.tailnet"]) == 0
    assert json.loads(path.read_text()) == first
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["agent_id"] == first[
        "agent_id"
    ]


def test_setup_replace_preserves_cursor_for_same_agent(tmp_path, monkeypatch):
    path = _config(
        tmp_path,
        monkeypatch,
        {
            "api": "https://relay.tailnet",
            "project": "crew",
            "agent_id": "pi",
            "cursor": 27,
        },
    )

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["setup", "--api", "https://relay.tailnet", "--replace"]) == 0
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet",
        "project": "crew",
        "agent_id": "pi",
        "cursor": 27,
    }


def test_join_automatically_joins_the_only_project(tmp_path, monkeypatch):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )
    calls = []

    class Client:
        def __init__(self, api, project, agent):
            calls.append(("init", api, project, agent))

        def projects(self):
            return {"projects": [{"project": "crew"}]}

        def join(self, project):
            calls.append(("join", project))
            return {"project": project, "agent": "pi", "created": False}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 0
    assert json.loads(path.read_text())["project"] == "crew"
    assert ("join", "crew") in calls


def test_join_creates_default_project_when_none_exist(tmp_path, monkeypatch):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )
    calls = []

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": []}

        def create(self, project):
            calls.append(project)
            return {"project": project, "agent": "pi", "created": True}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 0
    assert calls == ["agent-commons"]
    assert json.loads(path.read_text())["project"] == "agent-commons"


def test_join_requires_choice_when_several_projects_exist(tmp_path, monkeypatch, capsys):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": [{"project": "alpha"}, {"project": "beta"}]}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 1
    assert "alpha, beta" in capsys.readouterr().err
    assert "project" not in json.loads(path.read_text())


def test_default_join_cannot_switch_saved_project_without_replace(
    tmp_path, monkeypatch, capsys
):
    path = _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "old", "agent_id": "pi"},
    )

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": [{"project": "new"}]}

        def join(self, _project):
            raise AssertionError("must not switch projects")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 1
    assert json.loads(path.read_text())["project"] == "old"
    assert "use --replace" in capsys.readouterr().err


def test_replacing_project_resets_old_cursor(tmp_path, monkeypatch):
    path = _config(
        tmp_path,
        monkeypatch,
        {
            "api": "https://relay.tailnet",
            "project": "old",
            "agent_id": "pi",
            "cursor": 100,
        },
    )

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": [{"project": "new"}]}

        def join(self, project):
            return {"project": project, "agent": "pi", "created": False}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join", "--project", "new", "--replace"]) == 0
    saved = json.loads(path.read_text())
    assert saved["project"] == "new"
    assert "cursor" not in saved


def test_join_recovers_creation_race_by_joining_sole_project(tmp_path, monkeypatch):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )
    project_lists = iter([[], [{"project": "raced"}]])
    calls = []

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": next(project_lists)}

        def create(self, project):
            raise roomctl.RelayError(
                "project exists", status=409, payload={"code": "projects_exist"}
            )

        def join(self, project):
            calls.append(project)
            return {"project": project, "agent": "pi", "created": False}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 0
    assert calls == ["raced"]
    assert json.loads(path.read_text())["project"] == "raced"


def test_wait_filters_messages_and_saves_cursor_after_output(tmp_path, monkeypatch, capsys):
    path = _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )

    def message(sender, recipient, seq):
        return {"sender": sender, "recipient": recipient, "seq": seq, "text": str(seq)}

    class Client:
        agent_id = "pi"

        def __init__(self, *_args):
            pass

        def read(self, after):
            assert after == 0
            return {
                "messages": [
                    message("pi", "pi", 1),
                    message("hermes", "other", 2),
                    message("hermes", "pi", 3),
                ],
                "next_after": 3,
            }

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    original_write = roomctl.save_wait_cursor
    writes = []

    def write_after_output(config, cursor):
        writes.append((capsys.readouterr().out, config.copy(), cursor))
        original_write(config, cursor)

    monkeypatch.setattr(roomctl, "save_wait_cursor", write_after_output)
    assert roomctl.main(["wait", "--timeout", "1"]) == 0
    output, saved, cursor = writes[0]
    assert json.loads(output) == {
        "messages": [message("hermes", "pi", 3)],
        "next_after": 3,
    }
    assert "cursor" not in saved
    assert cursor == 3
    assert json.loads(path.read_text())["cursor"] == 3


def test_wait_does_not_restore_stale_config_after_reconfiguration(
    tmp_path, monkeypatch, capsys
):
    path = _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "old", "agent_id": "pi"},
    )

    class Client:
        agent_id = "pi"

        def __init__(self, *_args):
            pass

        def read(self, _after):
            roomctl.write_config(
                {"api": "https://relay.tailnet", "project": "new", "agent_id": "pi"}
            )
            return {
                "messages": [
                    {"sender": "hermes", "recipient": "pi", "seq": 1, "text": "hi"}
                ],
                "next_after": 1,
            }

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["wait", "--timeout", "1"]) == 1
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet",
        "project": "new",
        "agent_id": "pi",
    }
    assert "relay setup changed while waiting" in capsys.readouterr().err


def test_wait_timeout_emits_empty_batch(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )

    class Client:
        def __init__(self, *_args):
            pass

        def read(self, _after):
            return {"messages": [], "next_after": 0}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["wait", "--timeout", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"messages": [], "next_after": 0}


def test_wait_stops_after_ten_consecutive_relay_errors(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )
    calls = []

    class Client:
        def __init__(self, *_args):
            pass

        def read(self, _after):
            calls.append(1)
            raise roomctl.RelayError("relay unavailable")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    monkeypatch.setattr(roomctl.time, "sleep", lambda _seconds: None)
    assert roomctl.main(["wait"]) == 1
    assert len(calls) == 10
    assert "relay unavailable" in capsys.readouterr().err


def test_setup_refuses_an_unreachable_relay_and_writes_no_config(
    tmp_path, monkeypatch
):
    path = _config(tmp_path, monkeypatch)

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            raise roomctl.RelayError("relay unavailable: URLError")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["setup", "--api", "https://relay.tailnet", "--agent", "pi"]) == 1
    assert not path.exists()


def test_join_replace_preserves_unresolved_outbound_message(tmp_path, monkeypatch):
    path = _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "old", "agent_id": "pi"},
    )
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


def test_join_refuses_to_replace_existing_local_config_without_flag(
    tmp_path, monkeypatch, capsys
):
    path = _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "old", "agent_id": "pi"},
    )
    assert roomctl.main(["join", "--project", "new"]) == 1
    assert json.loads(path.read_text())["project"] == "old"
    assert "local config already exists" in capsys.readouterr().err


def test_default_thread_send_reuses_persisted_payload_after_ambiguous_failure(
    tmp_path, monkeypatch
):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )
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
