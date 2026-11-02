import json
import subprocess
from datetime import UTC, datetime

import roomctl


def test_configure_utf8_output_reconfigures_standard_streams(monkeypatch):
    class Stream:
        def __init__(self):
            self.options = None

        def reconfigure(self, **options):
            self.options = options

    stdout, stderr = Stream(), Stream()
    monkeypatch.setattr(roomctl.sys, "stdout", stdout)
    monkeypatch.setattr(roomctl.sys, "stderr", stderr)
    roomctl.configure_utf8_output()
    assert stdout.options == {"encoding": "utf-8", "errors": "strict"}
    assert stderr.options == {"encoding": "utf-8", "errors": "strict"}


def _config(tmp_path, monkeypatch, value=None):
    path = tmp_path / "agent-commons" / "config.json"
    monkeypatch.setattr(roomctl, "config_path", lambda: path)
    if value is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    return path


def test_doctor_reports_fresh_listener_and_does_not_change_config(tmp_path, monkeypatch, capsys):
    config = {
        "api": "https://relay.tailnet", "project": "crew", "agent_id": "pi", "cursor": 27,
    }
    path = _config(tmp_path, monkeypatch, config)
    before = path.read_bytes()
    monkeypatch.setattr(roomctl.time, "time", lambda: 1000)
    monkeypatch.setattr(roomctl.os, "environ", {})
    monkeypatch.setattr(roomctl.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: type("Result", (), {"stdout": ""})()
    )
    last_seen = datetime.fromtimestamp(880.001, UTC).isoformat().replace("+00:00", "Z")

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

        def participants(self):
            return {"participants": [{"agent_id": "pi", "last_seen": last_seen}]}

        def read(self, _after):
            return {"messages": [{"text": "setup-check-test", "recipient": "pi", "sender": "peer"}]}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert [check["name"] for check in report["checks"]] == [
        "relay", "participant", "listener", "conflicts", "probe",
    ]
    assert report["checks"][2] == {"name": "listener", "ok": True, "detail": "last polled 120s ago"}
    assert path.read_bytes() == before


def test_doctor_fails_for_stale_listener_or_missing_project_member(tmp_path, monkeypatch, capsys):
    path = _config(
        tmp_path, monkeypatch,
        {"api": "https://relay.tailnet", "project": "crew", "agent_id": "pi"},
    )
    monkeypatch.setattr(roomctl.time, "time", lambda: 1000)
    monkeypatch.setattr(roomctl.os, "environ", {})
    monkeypatch.setattr(roomctl.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_args, **_kwargs: type("Result", (), {"stdout": ""})()
    )
    last_seen = datetime.fromtimestamp(880, UTC).isoformat().replace("+00:00", "Z")
    members = [{"agent_id": "pi", "last_seen": last_seen}]

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

        def participants(self):
            return {"participants": members}

        def read(self, _after):
            return {"messages": []}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["doctor"]) == 1
    stale = json.loads(capsys.readouterr().out)
    assert stale["ok"] is False
    assert stale["checks"][2]["detail"] == "stale; last polled 120s ago"

    members.clear()
    assert roomctl.main(["doctor"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["checks"][-1] == {
        "name": "participant", "ok": False, "detail": "agent has not joined this project",
    }
    assert path.exists()


def test_doctor_reports_relay_failure_without_exposing_error_details(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path, monkeypatch,
        {"api": "https://relay.tailnet", "project": "crew", "agent_id": "pi"},
    )

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            raise roomctl.RelayError("private response body", status=502)

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["doctor"]) == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["checks"] == [{
        "name": "relay", "ok": False, "detail": "health check failed (HTTP 502)",
    }]
    assert "private response body" not in output


def test_setup_generates_name_and_rerun_reuses_saved_name(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_ID", raising=False)
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


def test_agent_setup_joins_only_project_and_saves_config(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)
    calls = []

    class Client:
        def __init__(self, api, project, agent):
            calls.append(("init", api, project, agent))

        def healthz(self):
            return {"status": "ok"}

        def projects(self):
            return {"projects": [{"project": "crew"}]}

        def join(self, project):
            calls.append(("join", project))
            return {"project": project, "agent": "pi", "created": False}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main([
        "agent-setup", "--api", "https://relay.tailnet", "--agent", "pi"
    ]) == 0
    assert ("join", "crew") in calls
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet", "project": "crew", "agent_id": "pi",
    }
    assert json.loads(capsys.readouterr().out)["setup"] == "joined project"


def test_agent_setup_does_not_use_host_agent_id_as_participant_name(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_ID", "shared-host-name")

    class Client:
        def __init__(self, _api, _project, agent):
            self.agent_id = agent

        def healthz(self):
            return {"status": "ok"}

        def projects(self):
            return {"projects": [{"project": "crew"}]}

        def join(self, project):
            return {"project": project, "agent": self.agent_id, "created": False}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["agent-setup", "--api", "https://relay.tailnet"]) == 0
    saved = json.loads(path.read_text())
    assert saved["agent_id"] != "shared-host-name"
    assert saved["agent_id"].startswith("agent-")
    assert json.loads(capsys.readouterr().out)["agent"] == saved["agent_id"]


def test_agent_setup_requires_choice_for_multiple_projects_without_mutating(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

        def projects(self):
            return {"projects": [{"project": "alpha"}, {"project": "beta"}]}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main([
        "agent-setup", "--api", "https://relay.tailnet", "--agent", "pi"
    ]) == 1
    assert not path.exists()
    assert "alpha, beta" in capsys.readouterr().err


def test_agent_setup_creates_named_project_only_after_explicit_opt_in(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)
    calls = []

    class Client:
        def __init__(self, api, project, agent):
            self.agent_id = agent
            calls.append(("init", api, project, agent))

        def healthz(self):
            return {"status": "ok"}

        def projects(self):
            return {"projects": []}

        def create(self):
            calls.append(("create",))
            return {"project": "crew", "agent": "pi", "created": True}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main([
        "agent-setup", "--api", "https://relay.tailnet", "--agent", "pi",
        "--project", "crew", "--allow-create",
    ]) == 0
    assert ("create",) in calls
    assert json.loads(path.read_text()) == {
        "api": "https://relay.tailnet", "project": "crew", "agent_id": "pi",
    }
    assert json.loads(capsys.readouterr().out)["setup"] == "created project"


def test_agent_setup_refuses_project_creation_without_explicit_opt_in(tmp_path, monkeypatch, capsys):
    path = _config(tmp_path, monkeypatch)

    class Client:
        def __init__(self, *_args):
            pass

        def healthz(self):
            return {"status": "ok"}

        def projects(self):
            return {"projects": []}

        def create(self, *_args, **_kwargs):
            raise AssertionError("must not create without explicit opt-in")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main([
        "agent-setup", "--api", "https://relay.tailnet", "--agent", "pi",
        "--project", "crew",
    ]) == 1
    assert not path.exists()
    assert "--allow-create" in capsys.readouterr().err


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


def test_join_refuses_to_create_a_project_without_explicit_approval(
    tmp_path, monkeypatch, capsys
):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": []}

        def create(self, *_args):
            raise AssertionError("join must not create a project")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 1
    assert "project" not in json.loads(path.read_text())
    assert "--allow-create" in capsys.readouterr().err


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


def test_join_does_not_create_project_when_project_list_is_empty(tmp_path, monkeypatch):
    path = _config(
        tmp_path, monkeypatch, {"api": "https://relay.tailnet", "agent_id": "pi"}
    )

    class Client:
        def __init__(self, *_args):
            pass

        def projects(self):
            return {"projects": []}

        def create(self, *_args):
            raise AssertionError("join must not create a project")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["join"]) == 1
    assert "project" not in json.loads(path.read_text())


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

        def read(self, after, *, listener=None):
            assert after == 0 and listener == "pi"
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

        def read(self, _after, *, listener=None):
            assert listener == "pi"
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

        def read(self, _after, *, listener=None):
            assert listener == "pi"
            return {"messages": [], "next_after": 0}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["wait", "--timeout", "0"]) == 0
    assert json.loads(capsys.readouterr().out) == {"messages": [], "next_after": 0}


def test_wait_without_project_fails_before_polling(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "agent_id": "pi"},
    )
    def unexpected_sleep(_seconds):
        raise AssertionError("wait must not retry without a project")
    monkeypatch.setattr(roomctl.time, "sleep", unexpected_sleep)
    assert roomctl.main(["wait"]) == 1
    assert "create or join a project first" in capsys.readouterr().err


def test_wait_retries_twelve_relay_errors_then_receives_message(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )
    calls = []
    delays = []

    class Client:
        agent_id = "pi"
        def __init__(self, *_args):
            pass
        def read(self, _after, *, listener=None):
            assert listener == "pi"
            calls.append(1)
            if len(calls) <= 12:
                raise roomctl.RelayError("relay unavailable")
            return {"messages": [{"sender": "hermes", "recipient": "pi", "seq": 1}], "next_after": 1}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    monkeypatch.setattr(roomctl.time, "sleep", delays.append)
    monkeypatch.setattr(roomctl.time, "monotonic", lambda: 100.0)
    assert roomctl.main(["wait", "--timeout", "600"]) == 0
    assert len(calls) == 13
    assert delays[:6] == [3, 6, 12, 24, 48, 60]
    assert json.loads(capsys.readouterr().out)["messages"][0]["seq"] == 1


def test_wait_stops_on_non_retryable_4xx_with_rejoin_hint(tmp_path, monkeypatch, capsys):
    _config(
        tmp_path,
        monkeypatch,
        {"api": "https://relay.tailnet", "project": "demo", "agent_id": "pi"},
    )

    class Client:
        agent_id = "pi"
        def __init__(self, *_args):
            pass
        def read(self, _after, *, listener=None):
            assert listener == "pi"
            raise roomctl.RelayError("relay returned HTTP 404", status=404)

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    monkeypatch.setattr(roomctl.time, "sleep", lambda _seconds: None)
    assert roomctl.main(["wait"]) == 1
    assert "project may be expired or missing" in capsys.readouterr().err


def test_reply_to_inherits_target_thread_hop_and_recipient_and_can_complete(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch, {"api": "https://relay.tailnet", "project": "demo", "agent_id": "hermes"})
    target = {"seq": 12, "sender": "pi", "recipient": "hermes", "thread_id": "550e8400-e29b-41d4-a716-446655440001", "hop": 3, "complete": False}
    sent = []

    class Client:
        agent_id = "hermes"
        def __init__(self, *_args): pass
        def read_before(self, before, limit=1):
            assert (before, limit) == (13, 1)
            return {"messages": [target]}
        def read(self, after):
            return {"messages": [], "next_after": after}
        def post(self, payload):
            sent.append(payload.copy())
            return {"seq": 13}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "done", "--complete"]) == 0
    assert sent[0]["recipient"] == "pi"
    assert sent[0]["thread_id"] == target["thread_id"]
    assert sent[0]["reply_to"] == 12 and sent[0]["hop"] == 4
    assert sent[0]["complete"] is True


def test_reply_to_refuses_thread_closed_before_reply_target(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch, {"api": "https://relay.tailnet", "project": "demo", "agent_id": "hermes"})
    thread_id = "550e8400-e29b-41d4-a716-446655440001"
    closure = {"seq": 11, "sender": "pi", "thread_id": thread_id, "hop": 2, "complete": True}
    target = {"seq": 12, "sender": "pi", "thread_id": thread_id, "hop": 3, "complete": False}

    class Client:
        def __init__(self, *_args): pass
        def read_before(self, _before, limit=1): return {"messages": [target]}
        def read(self, after):
            if after == 0:
                return {"messages": [closure, target], "next_after": 12}
            return {"messages": [], "next_after": after}
        def post(self, _payload): raise AssertionError("reply to closed thread must not send")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "reply"]) == 1
    assert "thread is closed" in capsys.readouterr().err


def test_reply_to_refuses_hop_eight(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch, {"api": "https://relay.tailnet", "project": "demo", "agent_id": "hermes"})
    target = {"seq": 12, "sender": "pi", "thread_id": "550e8400-e29b-41d4-a716-446655440001", "hop": 7, "complete": False}

    class Client:
        def __init__(self, *_args): pass
        def read_before(self, _before, limit=1): return {"messages": [target]}
        def read(self, _after): return {"messages": [], "next_after": _after}
        def post(self, _payload): raise AssertionError("hop-eight reply must not send")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "reply"]) == 1
    assert "hop 8" in capsys.readouterr().err


def test_pending_completion_retry_replays_without_reading_mutable_reply_history(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch, {"api": "https://relay.tailnet", "project": "demo", "agent_id": "hermes"})
    payload = {
        "client_msg_id": "550e8400-e29b-41d4-a716-446655440099",
        "sender": "hermes", "recipient": "pi",
        "thread_id": "550e8400-e29b-41d4-a716-446655440001",
        "reply_to": 12, "hop": 1, "text": "final answer", "complete": True,
    }
    db = roomctl.state_db()
    db.execute("INSERT INTO pending VALUES (?,?)", (payload["client_msg_id"], json.dumps(payload, sort_keys=True)))
    db.commit()
    db.close()
    sent = []

    class Client:
        agent_id = "hermes"
        def __init__(self, *_args): pass
        def read_before(self, *_args, **_kwargs):
            raise AssertionError("retry must not revalidate a pruned/closed reply target")
        def read(self, *_args):
            raise AssertionError("retry must not scan mutable thread history")
        def post(self, value):
            sent.append(value)
            return {"seq": 13}

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "final answer", "--complete"]) == 0
    assert sent == [payload]
    assert json.loads(capsys.readouterr().out)["seq"] == 13


def test_reply_to_refuses_closed_threads_and_pruned_targets(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch, {"api": "https://relay.tailnet", "project": "demo", "agent_id": "hermes"})
    target = {"seq": 12, "sender": "pi", "recipient": "hermes", "thread_id": "550e8400-e29b-41d4-a716-446655440001", "hop": 0, "complete": False}

    class Client:
        def __init__(self, *_args): pass
        def read_before(self, _before, limit=1): return {"messages": [target]}
        def read(self, after):
            closure = target | {"seq": 14, "complete": True}
            return {"messages": [closure], "next_after": 14} if after < 14 else {"messages": [], "next_after": after}
        def post(self, _payload): raise AssertionError("closed reply must not send")

    monkeypatch.setattr(roomctl, "RelayClient", Client)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "reply"]) == 1
    assert "thread is closed" in capsys.readouterr().err

    class PrunedClient(Client):
        def read_before(self, _before, limit=1): return {"messages": [target | {"seq": 11}]}

    monkeypatch.setattr(roomctl, "RelayClient", PrunedClient)
    assert roomctl.main(["send", "--reply-to", "12", "--text", "reply"]) == 1
    assert "pruned or missing" in capsys.readouterr().err


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
