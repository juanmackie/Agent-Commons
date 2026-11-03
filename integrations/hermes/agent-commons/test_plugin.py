import importlib.util
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

PLUGIN = Path(__file__).parent / "__init__.py"
spec = importlib.util.spec_from_file_location("agent_commons_hermes", PLUGIN)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

THREAD = "550e8400-e29b-41d4-a716-446655440001"
MESSAGE_ID = "550e8400-e29b-41d4-a716-446655440002"


def peer_message(**overrides):
    return {
        "sender": "pi",
        "recipient": "hermes",
        "thread_id": THREAD,
        "seq": 8,
        "reply_to": None,
        "hop": 0,
        "client_msg_id": MESSAGE_ID,
        "text": "review this",
        "complete": False,
        **overrides,
    }


def test_listener_status_tool_stays_read_only_for_peer_sessions():
    assert plugin.guard_peer_tool_call(
        {"tool_name": "agent_commons_status", "args": {}},
        "session-1", {"session-1"},
    ) is None


def test_peer_tool_call_uses_hermes_native_exact_approval(monkeypatch):
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
    call = {"tool_name": "terminal", "args": {"command": "rm -r build"}}

    first = plugin.guard_peer_tool_call(call, "session-1", {"session-1"})
    second = plugin.guard_peer_tool_call(call, "session-1", {"session-1"})

    assert first["action"] == "approve"
    assert "terminal" in first["message"]
    assert "rm -r build" in first["message"]
    assert first["rule_key"].startswith("agent-commons-once:")
    assert first["rule_key"] != second["rule_key"]
    # Hermes' native gate owns YOLO behavior. The plugin must not add a
    # second bypass check that blocks the host's configured approval mode.
    assert plugin.guard_peer_tool_call(call, "session-1", {"session-1"})["action"] == "approve"


def test_installed_hermes_host_resolves_exact_approval_decisions(monkeypatch):
    host_plugins = pytest.importorskip("hermes_cli.plugins")
    approval = pytest.importorskip("tools.approval")
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
    directive = plugin.guard_peer_tool_call(
        {"tool_name": "terminal", "args": {"command": "rm -r build"}},
        "session-1", {"session-1"},
    )
    monkeypatch.setattr(host_plugins, "invoke_hook", lambda *_args, **_kwargs: [directive])
    requested = []

    def gate(tool_name, reason, *, rule_key=""):
        requested.append((tool_name, reason, rule_key))
        return {"approved": False, "message": "human denied the exact action"}

    monkeypatch.setattr(approval, "request_tool_approval", gate)
    denied = host_plugins.resolve_pre_tool_block(
        "terminal", {"command": "rm -r build"}, task_id="task-1", session_id="session-1",
    )
    assert denied == "human denied the exact action"
    assert requested == [("terminal", directive["message"], directive["rule_key"])]

    monkeypatch.setattr(approval, "request_tool_approval", lambda *_args, **_kwargs: {"approved": True})
    assert host_plugins.resolve_pre_tool_block(
        "terminal", {"command": "rm -r build"}, task_id="task-1", session_id="session-1",
    ) is None

    def broken_gate(*_args, **_kwargs):
        raise RuntimeError("approval UI unavailable")

    monkeypatch.setattr(approval, "request_tool_approval", broken_gate)
    blocked = host_plugins.resolve_pre_tool_block(
        "terminal", {"command": "rm -r build"}, task_id="task-1", session_id="session-1",
    )
    assert blocked == "BLOCKED: plugin approval gate failed for terminal"


def test_peer_tool_approval_fails_closed_when_details_are_redacted_or_unavailable(monkeypatch):
    call = {"tool_name": "terminal", "args": {"command": "echo sk-test-placeholder-1234567890"}}
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text.replace("sk-test-placeholder-1234567890", "[REDACTED]"))
    redacted = plugin.guard_peer_tool_call(call, "session-1", {"session-1"})
    assert redacted["action"] == "block"
    assert "sk-test-placeholder" not in redacted["message"]

    monkeypatch.setattr(plugin, "_approval_redactor", lambda _text: (_ for _ in ()).throw(RuntimeError("missing host API")))
    unavailable = plugin.guard_peer_tool_call(call, "session-1", {"session-1"})
    assert unavailable["action"] == "block"
    assert "approval details" in unavailable["message"].lower()


def test_missing_identity_and_rotated_session_cannot_drop_peer_provenance(monkeypatch):
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
    call = {"tool_name": "terminal", "args": {"command": "rm -r build"}}

    missing = plugin.guard_peer_tool_call(call, "", {"session-1"}, task_id="task-2")
    assert missing["action"] == "block"

    # Unknown session IDs are conservatively peer-originated if any peer
    # provenance exists; Hermes compaction can rotate the session ID mid-turn.
    rotated = plugin.guard_peer_tool_call(call, "rotated-session", {"session-1"}, task_id="task-2")
    assert rotated["action"] == "approve"


def test_outbound_room_messages_and_roomctl_commands_require_approval(monkeypatch):
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
    send = plugin.guard_peer_tool_call(
        {"tool_name": "agent_commons_send", "args": {"recipient": "pi", "text": "check status"}},
        "session-1",
        set(),
    )
    shell = plugin.guard_peer_tool_call(
        {"tool_name": "terminal", "args": {"command": "roomctl send --to pi --text 'check status'"}},
        "session-1",
        set(),
    )
    assert send["action"] == "approve"
    assert "check status" in send["message"]
    assert shell["action"] == "approve"
    assert "roomctl send" in shell["message"]


def test_compaction_task_identity_preserves_peer_provenance_and_native_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)

    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.commands = {}
            self.tools = {}

        def register_hook(self, name, handler):
            self.hooks[name] = handler

        def register_command(self, name, **kwargs):
            self.commands[name] = kwargs

        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {"state_path": str(tmp_path / "state.json")})
    context = FakeContext()
    plugin.register(context)
    marker = plugin.make_peer_text(peer_message())
    pre_llm = context.hooks["pre_llm_call"](
        session_id="session-1", task_id="task-1", user_message=marker,
        platform="cli", conversation_history=[],
    )
    assert pre_llm and "untrusted" in pre_llm["context"].lower()

    rotated = context.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "rm -r build"},
        task_id="task-1", session_id="rotated-session",
    )
    assert rotated["action"] == "approve"
    assert not context.commands  # Human approval uses Hermes' active gate, not global slash commands.

    final_marker = plugin.make_peer_text(peer_message(complete=True))
    context.hooks["pre_llm_call"](
        session_id="session-2", task_id="task-complete", user_message=final_marker,
        platform="cli", conversation_history=[],
    )
    blocked = context.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "rm -r build"},
        task_id="task-complete", session_id="session-2",
    )
    assert blocked["action"] == "block"
    assert "closed its thread" in blocked["message"]

    human_turn = context.hooks["pre_llm_call"](
        session_id="session-2", task_id="task-human", user_message="run the user-requested check",
        platform="cli", conversation_history=[final_marker],
    )
    assert human_turn is None
    human_action = context.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "echo check"},
        task_id="task-human", session_id="session-2",
    )
    assert human_action["action"] == "approve"


def test_gateway_message_is_clean_and_keeps_only_verified_route_metadata(monkeypatch):
    import sys
    import types

    @dataclass
    class Source:
        platform: str = "telegram"
        user_id: str = "42"
        chat_id: str = "42"
        thread_id: str = "topic-2"
        chat_type: str = "dm"

    @dataclass
    class Event:
        text: str
        source: Source
        metadata: dict
        internal: bool = False
        message_id: str = "old-message"
        message_type: object = None
        raw_message: object = None
        media_urls: list = field(default_factory=list)
        media_types: list = field(default_factory=list)
        reply_to_message_id: str | None = None
        reply_to_text: str | None = None
        reply_to_author_id: str | None = None
        reply_to_author_name: str | None = None
        channel_context: str | None = None
        channel_prompt: str | None = None

    class MessageType:
        TEXT = "text"

    gateway = types.ModuleType("gateway")
    platforms = types.ModuleType("gateway.platforms")
    base = types.ModuleType("gateway.platforms.base")
    base.MessageType = MessageType
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.platforms", platforms)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", base)

    source = Source()
    original = Event(
        "human text", source, {"existing": "must not be replayed"},
        media_urls=["/private/previous-image.png"], media_types=["image"],
        reply_to_text="previous private context", channel_context="old backfill",
        channel_prompt="old channel prompt",
    )
    injected = plugin.make_gateway_event(original, peer_message())

    assert injected.source is source
    assert injected.internal is True
    assert injected.message_id is None
    telegram_visible = plugin.make_gateway_event(original, peer_message(), 12345)
    assert telegram_visible.message_id == "12345"
    assert plugin.make_gateway_event(original, peer_message(), "agent-commons:synthetic").message_id is None
    assert injected.metadata["agent_commons_peer"]["thread_id"] == THREAD
    assert injected.media_urls == []
    assert injected.media_types == []
    assert injected.reply_to_text is None
    assert injected.channel_context is None
    assert injected.channel_prompt is None
    assert "review this" in injected.text


def test_peer_marker_carries_thread_limits_and_completion():
    text = plugin.make_peer_text(peer_message(hop=7, complete=True))
    parsed = plugin.parse_peer_marker(text)
    assert parsed == {
        "client_msg_id": MESSAGE_ID,
        "thread_id": THREAD,
        "seq": 8,
        "sender": "pi",
        "hop": 7,
        "complete": True,
    }
    older = plugin.make_peer_text(peer_message(seq=7, client_msg_id="550e8400-e29b-41d4-a716-446655440003"))
    latest = plugin.find_peer_marker([older, text])
    assert latest["client_msg_id"] == MESSAGE_ID
    assert latest["seq"] == 8


def test_state_owner_lock_is_exclusive_and_released_by_owner(tmp_path):
    lock_path = tmp_path / "state.owner.lock"
    release = plugin.acquire_owner_lock(lock_path)
    with pytest.raises(FileExistsError):
        plugin.acquire_owner_lock(lock_path)
    release()
    second_release = plugin.acquire_owner_lock(lock_path)
    second_release()


def test_concurrent_stale_lock_recovery_leaves_exactly_one_owner(tmp_path):
    lock_path = tmp_path / "state.owner.lock"
    lock_path.write_text(json.dumps({"pid": 2_000_000_000, "token": "stale"}), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(plugin.acquire_owner_lock, lock_path) for _ in range(2)]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except FileExistsError as error:
                errors.append(error)
    assert len(results) == 1
    assert len(errors) == 1
    results[0]()


def test_state_owner_lock_refuses_a_recovery_guard_left_by_another_starter(tmp_path):
    lock_path = tmp_path / "state.owner.lock"
    guard = Path(str(lock_path) + ".recovery.lock")
    lock_path.write_text(json.dumps({"pid": 2_000_000_000, "token": "stale"}), encoding="utf-8")
    guard.write_text(json.dumps({"pid": plugin.os.getpid(), "token": "recovering"}), encoding="utf-8")
    with pytest.raises(FileExistsError, match="recovery"):
        plugin.acquire_owner_lock(lock_path)
    assert json.loads(lock_path.read_text(encoding="utf-8"))["token"] == "stale"


def test_state_owner_lock_recovers_dead_pid_and_refuses_live_pid(tmp_path):
    lock_path = tmp_path / "state.owner.lock"
    stale_process = subprocess.Popen([sys.executable, "-c", "pass"])
    stale_pid = stale_process.pid
    stale_process.wait(timeout=5)
    lock_path.write_text(json.dumps({"pid": stale_pid, "token": "stale"}), encoding="utf-8")
    release = plugin.acquire_owner_lock(lock_path)
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == plugin.os.getpid()
    release()
    lock_path.write_text(json.dumps({"pid": plugin.os.getpid(), "token": "live"}), encoding="utf-8")
    with pytest.raises(FileExistsError):
        plugin.acquire_owner_lock(lock_path)


def test_state_retains_pending_inbox_and_connection_identity(tmp_path):
    state_file = tmp_path / "state.json"
    initial = {"cursor": 0, "seen": [], "pending": [], "outbox": [], "outbox_notices": [], "closed_threads": [], "fingerprint": "room-a", "session_id": None}
    plugin.save_state(state_file, initial)
    assert plugin.load_state(state_file) == initial


def test_state_identity_change_fails_closed_instead_of_skipping_room_messages(tmp_path):
    state_file = tmp_path / "state.json"
    plugin.save_state(state_file, {"cursor": 8, "seen": [], "pending": [], "fingerprint": "room-a"})
    with pytest.raises(plugin.RelayError, match="different room or agent"):
        plugin.load_state(state_file, expected_fingerprint="room-b")


def test_pending_message_is_persisted_before_cursor_advances_and_bound_to_one_session():
    first = peer_message(seq=8)
    page = {"messages": [first], "next_after": 8}
    queued = plugin.queue_relay_page(
        {"cursor": 0, "seen": [], "pending": [], "fingerprint": "room-a"},
        page, "hermes", "session-1",
    )
    assert queued["cursor"] == 8
    assert queued["pending"][0]["client_msg_id"] == MESSAGE_ID
    assert queued["pending"][0]["target_session_id"] == "session-1"
    assert plugin.next_pending_for_session(queued, "session-1")["client_msg_id"] == MESSAGE_ID
    assert plugin.next_pending_for_session(queued, "replacement-session") is None

    full = {"cursor": 8, "seen": [], "pending": [peer_message(seq=i, client_msg_id=f"550e8400-e29b-41d4-a716-{i:012d}") for i in range(1, 101)], "fingerprint": "room-a"}
    another = peer_message(seq=9, client_msg_id="550e8400-e29b-41d4-a716-446655440009")
    result = plugin.queue_relay_page(full, {"messages": [another], "next_after": 9}, "hermes", "session-1")
    assert result["cursor"] == 8
    assert len(result["pending"]) == 100


def test_pending_delivery_requires_a_verified_target_session():
    state = {"cursor": 0, "seen": [], "pending": [], "closed_threads": [], "fingerprint": "room-a"}
    with pytest.raises(plugin.RelayError, match="target session identity"):
        plugin.queue_relay_page(state, {"messages": [peer_message()], "next_after": 8}, "hermes", None)
    assert plugin.next_pending_for_session({**state, "pending": [peer_message()]}, "replacement-session") is None


def test_completion_durably_closes_thread_and_suppresses_late_messages():
    final = peer_message(complete=True)
    late = peer_message(seq=9, client_msg_id="550e8400-e29b-41d4-a716-446655440009", text="late")
    queued = plugin.queue_relay_page(
        {"cursor": 0, "seen": [], "pending": [], "closed_threads": [], "fingerprint": "room-a"},
        {"messages": [final, late], "next_after": 9}, "hermes", "session-1",
    )
    assert queued["cursor"] == 9
    assert [item["client_msg_id"] for item in queued["pending"]] == [MESSAGE_ID]
    assert queued["closed_threads"] == [THREAD]
    assert queued["seen"] == [late["client_msg_id"]]
    with pytest.raises(ValueError, match="closed"):
        plugin.validate_outbound_message({
            "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
            "text": "reply", "complete": False, "client_msg_id": MESSAGE_ID,
        }, closed_threads=queued["closed_threads"])


def test_approved_outbox_retries_same_payload_and_closes_only_after_receipt(tmp_path):
    message = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": 8, "hop": 1,
        "text": "approved", "complete": True, "client_msg_id": MESSAGE_ID,
        "target_session_id": "session-1",
    }
    state = {"cursor": 0, "seen": [], "pending": [], "outbox": [], "closed_threads": [], "fingerprint": "room-a", "session_id": "session-1"}
    queued = plugin.queue_outbound_message(state, message)
    assert queued["outbox"] == [message]
    assert plugin.queue_outbound_message(queued, message) == queued
    with pytest.raises(plugin.RelayError, match="different message"):
        plugin.queue_outbound_message(queued, {**message, "text": "changed"})
    file = tmp_path / "state.json"
    plugin.save_state(file, queued)
    restored = plugin.load_state(file, "room-a")
    assert plugin.next_outbound_for_session(restored, "session-1") == message
    sent = plugin.acknowledge_outbound_message(restored, MESSAGE_ID)
    assert sent["outbox"] == []
    assert sent["closed_threads"] == [THREAD]


def test_outbound_peer_reply_requires_exact_open_thread_and_sequence():
    params = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": 8, "hop": 1,
        "text": "ack", "complete": False, "client_msg_id": MESSAGE_ID,
    }
    assert plugin.validate_outbound_message(params, peer_message()) is params
    assert "closes the thread" in plugin.make_peer_text(peer_message(complete=True))
    with pytest.raises(ValueError, match="incomplete or invalid"):
        plugin.validate_outbound_message({**params, "reply_to": 9007199254740992}, peer_message())
    with pytest.raises(ValueError, match="exact sequence"):
        plugin.validate_outbound_message({**params, "reply_to": 7}, peer_message())
    with pytest.raises(ValueError, match="closed"):
        plugin.validate_outbound_message({**params, "reply_to": 8}, peer_message(complete=True))


def test_final_peer_message_blocks_tools_when_hermes_omits_session_and_task_ids(monkeypatch, tmp_path):
    class FakeContext:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, handler):
            self.hooks[name] = handler

        def register_tool(self, *args, **kwargs):
            pass

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {"state_path": str(tmp_path / "state.json")})
    context = FakeContext()
    plugin.register(context)
    context.hooks["pre_llm_call"](
        user_message=plugin.make_peer_text(peer_message(complete=True)),
        conversation_history=[],
    )
    blocked = context.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "rm -r build"},
    )
    assert blocked["action"] == "block"


def test_cli_injection_requires_the_exact_active_hermes_session():
    class Agent:
        session_id = "session-1"

    class Cli:
        agent = Agent()
        session_id = "session-1"

    class Manager:
        _cli_ref = Cli()

    assert plugin.current_cli_session_id(Manager()) == "session-1"
    Manager._cli_ref.session_id = "replacement-session"
    assert plugin.current_cli_session_id(Manager()) is None
    Manager._cli_ref = None
    assert plugin.current_cli_session_id(Manager()) is None


def test_relay_poll_failures_are_actionable_without_logging_relay_data(caplog):
    plugin.log_poll_failure(plugin.RelayError("https://relay.invalid secret message text", retryable=True), 6)
    assert "Agent Commons relay poll failed (RelayError); retrying in 6s" in caplog.text
    assert "relay.invalid" not in caplog.text
    assert "secret message text" not in caplog.text
    caplog.clear()
    plugin.log_poll_failure(plugin.RelayError("private detail", status=404), 60)
    assert "project may be expired or missing, rejoin the project" in caplog.text
    assert "private detail" not in caplog.text


def test_permanent_poll_failure_is_reported_without_exposing_error_text(caplog):
    plugin.log_poll_failure(plugin.RelayError("invalid config with secret value"))
    assert "Agent Commons relay poll stopped (RelayError)" in caplog.text
    assert "secret value" not in caplog.text


@pytest.mark.parametrize(("status", "retryable"), [(401, False), (429, True), (503, True)])
def test_http_status_classifies_configuration_and_transient_poll_failures(monkeypatch, status, retryable):
    import urllib.error

    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError("https://relay.invalid/private", status, "secret", {}, None)

    monkeypatch.setattr(plugin, "_opener", Opener())
    with pytest.raises(plugin.RelayError) as failure:
        plugin._request({"api": "https://relay.invalid"}, "GET", "/healthz")
    assert failure.value.retryable is retryable
    assert failure.value.status == status
    assert "secret" not in str(failure.value)


def test_poll_failure_action_retries_only_transient_or_missing_project_errors():
    assert plugin.poll_failure_action(plugin.RelayError("expired", status=404), 4) == 60
    assert plugin.poll_failure_action(plugin.RelayError("rejected", status=422), 1) is None
    assert plugin.poll_failure_action(plugin.RelayError("unauthorized", status=401), 1) is None
    assert plugin.poll_failure_action(plugin.RelayError("unavailable", status=503, retryable=True), 2) == 12
    assert plugin.poll_failure_action(OSError("state unavailable"), 2) == 12
    assert plugin.poll_failure_action(RuntimeError("state unreadable"), 1) is None


def test_unresolved_relay_hostname_fails_closed_with_network_hint(monkeypatch):
    import socket
    import urllib.error

    class UnresolvedRelayOpener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(plugin, "_opener", UnresolvedRelayOpener())
    config = {"api": "http://relay:8080", "project": "demo", "agent_id": "hermes"}
    with pytest.raises(plugin.RelayResolutionError) as failure:
        plugin.read_page(config, 0)
    assert failure.value.retryable is False
    assert plugin.poll_failure_action(failure.value, 1) is None
    assert "ix-agent-commons_default" in str(failure.value)
    assert "relay:8080" not in str(failure.value)


def test_unreachable_relay_port_stays_retryable(monkeypatch):
    import urllib.error

    class RefusingRelayOpener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(plugin, "_opener", RefusingRelayOpener())
    config = {"api": "http://relay:8080", "project": "demo", "agent_id": "hermes"}
    with pytest.raises(plugin.RelayError) as failure:
        plugin.read_page(config, 0)
    assert not isinstance(failure.value, plugin.RelayResolutionError)
    assert failure.value.retryable is True
    assert plugin.poll_failure_action(failure.value, 1) == 6


def test_rejected_outbound_completion_releases_thread_reservation():
    message = {
        "client_msg_id": MESSAGE_ID, "recipient": "pi", "thread_id": THREAD,
        "reply_to": 8, "hop": 1, "text": "finish", "complete": True,
        "target_session_id": "session-1",
    }
    state = {"outbox": [], "closed_threads": []}
    queued = plugin.queue_outbound_message(state, message)
    assert queued["closed_threads"] == []
    assert plugin.outbound_closed_threads(queued) == {THREAD}
    rejected = plugin.reject_outbound_message(queued, MESSAGE_ID, "rejected")
    assert plugin.outbound_closed_threads(rejected) == set()
    later = peer_message(seq=9, client_msg_id="550e8400-e29b-41d4-a716-446655440009")
    received = plugin.queue_relay_page(
        {"cursor": 8, "seen": [], "pending": [], "closed_threads": rejected["closed_threads"]},
        {"messages": [later], "next_after": 9}, "hermes", "session-1",
    )
    assert [item["client_msg_id"] for item in received["pending"]] == [later["client_msg_id"]]


def test_outbound_rejection_removes_message_and_stores_notice():
    message = {"client_msg_id": MESSAGE_ID, "recipient": "pi"}
    state = {"outbox": [message], "outbox_notices": []}
    notice = "Not sent to pi: HTTP 422"
    rejected = plugin.reject_outbound_message(state, MESSAGE_ID, notice)
    assert rejected["outbox"] == []
    assert rejected["outbox_notices"] == [notice]


def test_rejected_outbound_completion_preserves_confirmed_thread_closure():
    message = {"client_msg_id": MESSAGE_ID, "recipient": "pi", "thread_id": THREAD, "complete": True}
    state = {"outbox": [message], "outbox_notices": [], "closed_threads": [THREAD], "pending": []}
    rejected = plugin.reject_outbound_message(state, MESSAGE_ID, "rejected")
    assert rejected["closed_threads"] == [THREAD]
    assert plugin.outbound_closed_threads(rejected) == {THREAD}


def test_registered_send_rejects_to_durable_notice_and_holds_404(tmp_path, monkeypatch):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_command(self, *_args, **_kwargs):
            pass
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    state_file = tmp_path / "state.json"
    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "state_path": str(state_file),
    })
    context = FakeContext()
    plugin.register(context)
    params = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "hello", "complete": False, "client_msg_id": MESSAGE_ID,
    }
    monkeypatch.setattr(plugin, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        plugin.RelayError("relay returned HTTP 422", status=422)
    ))
    response = json.loads(context.tools["agent_commons_send"]["handler"](params, session_id="session-1"))
    assert response["sent"] is False and "HTTP 422" in response["error"]
    saved = plugin.load_state(state_file)
    assert saved["outbox"] == [] and len(saved["outbox_notices"]) == 1
    injected = context.hooks["pre_llm_call"](user_message="human turn")
    assert "Not sent to pi" in injected["context"]
    assert plugin.load_state(state_file)["outbox_notices"] == []

    held_id = "550e8400-e29b-41d4-a716-446655440003"
    held = {**params, "client_msg_id": held_id, "text": "hold me"}
    monkeypatch.setattr(plugin, "send_message", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        plugin.RelayError("relay returned HTTP 404", status=404)
    ))
    queued = json.loads(context.tools["agent_commons_send"]["handler"](held, session_id="session-1"))
    assert queued["queued"] is True
    assert [item["client_msg_id"] for item in plugin.load_state(state_file)["outbox"]] == [held_id]
    conflict = json.loads(context.tools["agent_commons_send"]["handler"](
        {**held, "text": "changed payload"}, session_id="session-1"
    ))
    assert "queued" not in conflict
    assert "different message" in conflict["error"]
    assert plugin.load_state(state_file)["outbox"][0]["text"] == "hold me"


def test_registered_send_serializes_queue_and_flush_against_background_poller(tmp_path, monkeypatch):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_command(self, *_args, **_kwargs):
            pass
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    fake_threads = []
    class FakeThread:
        def __init__(self, target, **_kwargs):
            self.target = target
            self.started = False
            fake_threads.append(self)
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started

    class StopPolling(Exception):
        pass

    real_thread = threading.Thread
    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "state_path": str(tmp_path / "state.json"),
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: lambda: None)
    monkeypatch.setattr(plugin.threading, "Thread", FakeThread)
    context = FakeContext()
    plugin.register(context)
    context.hooks["on_session_start"](session_id="session-1", platform="cli")
    assert len(fake_threads) == 1

    original_next = plugin.next_outbound_for_session
    poller_selected_message = threading.Event()
    def next_outbound(state, session_id):
        message = original_next(state, session_id)
        if threading.current_thread().name == "background-poller" and message is not None:
            poller_selected_message.set()
        return message
    monkeypatch.setattr(plugin, "next_outbound_for_session", next_outbound)

    poller_read = threading.Event()
    def read_page(_config, cursor):
        poller_read.set()
        return {"messages": [], "next_after": cursor}
    monkeypatch.setattr(plugin, "read_page", read_page)

    send_started = threading.Event()
    release_send = threading.Event()
    send_calls = []
    def send_message(*_args, **_kwargs):
        send_calls.append(1)
        send_started.set()
        assert release_send.wait(5)
        raise plugin.RelayError("relay returned HTTP 422", status=422)
    monkeypatch.setattr(plugin, "send_message", send_message)
    monkeypatch.setattr(plugin.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopPolling()))

    params = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "hello", "complete": False, "client_msg_id": MESSAGE_ID,
    }
    result = []
    handler = context.tools["agent_commons_send"]["handler"]
    handler_thread = real_thread(target=lambda: result.append(json.loads(handler(params, session_id="session-1"))))
    handler_thread.start()
    def run_poller():
        try:
            fake_threads[0].target()
        except StopPolling:
            pass
    poller_thread = real_thread(target=run_poller, name="background-poller")
    poller_started = False
    try:
        assert send_started.wait(5)
        poller_thread.start()
        poller_started = True
        assert poller_read.wait(5)
        selected_while_send_pending = poller_selected_message.wait(0.2)
    finally:
        release_send.set()
        handler_thread.join(5)
        if poller_started:
            poller_thread.join(5)
    assert not handler_thread.is_alive() and not poller_thread.is_alive()
    assert selected_while_send_pending is False
    assert len(send_calls) == 1
    assert result[0]["sent"] is False and "HTTP 422" in result[0]["error"]


def test_truncated_http_response_is_retryable_without_exposing_response_data(monkeypatch):

    import http.client

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def read(self, _limit):
            raise http.client.IncompleteRead(b"private partial body", 12)

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(plugin, "_opener", Opener())
    with pytest.raises(plugin.RelayError) as failure:
        plugin._request({"api": "https://relay.invalid"}, "GET", "/healthz")
    assert failure.value.retryable
    assert "private partial body" not in str(failure.value)


def test_poll_retry_delay_is_bounded_and_resets_after_success():
    assert [plugin.poll_retry_delay(failures) for failures in range(1, 6)] == [6, 12, 24, 48, 60]
    assert plugin.poll_retry_delay(0) == plugin._POLL_SECONDS


def test_registered_cli_poller_retries_missing_project_after_rejoin(tmp_path, monkeypatch):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_command(self, *_args, **_kwargs):
            pass
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    threads = []
    class FakeThread:
        def __init__(self, target, **_kwargs):
            self.target = target
            self.started = False
            threads.append(self)
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started

    class StopPolling(Exception):
        pass

    settings = {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "state_path": str(tmp_path / "state.json"),
    }
    monkeypatch.setattr(plugin, "plugin_settings", lambda: settings)
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: lambda: None)
    monkeypatch.setattr(plugin.threading, "Thread", FakeThread)
    context = FakeContext()
    plugin.register(context)

    read_calls = []
    def read_page(_config, cursor):
        read_calls.append(cursor)
        if len(read_calls) == 1:
            raise plugin.RelayError("expired", status=404)
        return {"messages": [], "next_after": cursor}
    monkeypatch.setattr(plugin, "read_page", read_page)
    delays = []
    def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise StopPolling
    monkeypatch.setattr(plugin.time, "sleep", sleep)

    context.hooks["on_session_start"](session_id="session-1", platform="cli")
    assert len(threads) == 1
    with pytest.raises(StopPolling):
        threads[0].target()
    assert len(read_calls) == 2
    assert delays == [60, plugin._POLL_SECONDS]


def test_dashboard_message_uses_exact_existing_session_and_queues_follow_up(monkeypatch):
    import sys
    import threading
    from types import ModuleType, SimpleNamespace

    class Agent:
        session_id = "active"
        def __init__(self):
            self.steered = []
            self.interrupts = 0
        def steer(self, text):
            self.steered.append(text)
            return True
        def interrupt(self):
            self.interrupts += 1

    agent = Agent()
    session_transport = object()
    session = {"agent": agent, "running": True, "history_lock": threading.RLock(), "queued_prompt": None, "transport": session_transport}
    dispatched = []
    server = SimpleNamespace(_sessions={"rpc-id": session}, _sessions_lock=threading.RLock(), _enqueue_prompt=lambda s, text, transport: s.update(queued_prompt={"text": text, "transport": transport}), dispatch=lambda request, transport: dispatched.append((request, transport)) or {"ok": True})
    package = ModuleType("tui_gateway")
    package.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", package)

    assert plugin.inject_dashboard_message("wrong-session", "wrong") is False
    assert plugin.inject_dashboard_message("active", "human steer", interrupt=True) is True
    assert agent.steered == []
    assert agent.interrupts == 1
    assert session["queued_prompt"]["text"] == "human steer"
    assert session["queued_prompt"]["transport"] is session_transport

    deferred = set()
    assert plugin.inject_dashboard_message("active", "do not merge", True, "message-2", deferred) == "deferred"
    assert session["queued_prompt"]["text"] == "human steer"
    assert agent.interrupts == 2
    assert plugin.inject_dashboard_message("active", "do not merge", True, "message-2", deferred) == "deferred"
    assert agent.interrupts == 2
    assert plugin.dashboard_has_queued_prompt("active")

    session["queued_prompt"] = None
    assert not plugin.dashboard_has_queued_prompt("active")
    assert plugin.inject_dashboard_message("active", "do not merge", True, "message-2", deferred) is True
    assert session["queued_prompt"]["text"] == "do not merge"
    assert not deferred
    assert agent.interrupts == 3

    session["queued_prompt"] = None
    assert plugin.inject_dashboard_message("active", "human follow-up", interrupt=False) is True
    assert session["queued_prompt"]["text"] == "human follow-up"
    assert session["queued_prompt"]["transport"] is session_transport
    assert agent.interrupts == 3

    session["queued_prompt"] = None
    session["running"] = False
    result = plugin.inject_dashboard_message("active", "idle follow-up")
    assert result is True
    assert dispatched[0][0]["params"]["session_id"] == "rpc-id"
    assert dispatched[0][1] is session_transport

    class TurnFinishesAtLockBoundary:
        def __enter__(self):
            session["running"] = False
            return self
        def __exit__(self, *_args):
            return False

    session["history_lock"] = TurnFinishesAtLockBoundary()
    session["running"] = True
    assert plugin.inject_dashboard_message("active", "no stranded prompt") is True
    assert len(dispatched) == 2


def test_telegram_busy_detection_uses_adapter_active_session_map():
    import asyncio
    from types import SimpleNamespace
    route = {
        "adapter": SimpleNamespace(_active_sessions={"telegram:42:42": asyncio.Event()}, _pending_messages={}),
        "session_key": "telegram:42:42",
        "gateway": SimpleNamespace(_queued_events={}),
    }
    assert plugin.gateway_session_is_active(route)
    route["adapter"]._active_sessions.clear()
    assert not plugin.gateway_session_is_active(route)
    route["adapter"]._pending_messages[route["session_key"]] = object()
    assert plugin.gateway_has_pending_event(route)
    route["adapter"]._pending_messages.clear()
    route["gateway"]._queued_events[route["session_key"]] = [object()]
    assert plugin.gateway_has_pending_event(route)
def test_gateway_route_requires_existing_session_and_stops_on_replacement(monkeypatch, tmp_path):
    class FakeSessionStore:
        def __init__(self, session_id):
            self.session_id = session_id
        def get_session_id(self, key):
            return self.session_id if key == "telegram:42:42:topic-2" else None

    class FakeGateway:
        adapters = {"telegram": object()}
        def _session_key_for_source(self, _source):
            return "telegram:42:42:topic-2"

    @dataclass
    class Source:
        platform: str = "telegram"
        user_id: str = "42"
        chat_id: str = "42"
        thread_id: str = "topic-2"
        chat_type: str = "dm"

    @dataclass
    class Event:
        source: Source
        text: str = "human input"
        metadata: dict = field(default_factory=dict)

    class FakeContext:
        def __init__(self):
            self.hooks = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_command(self, *_args, **_kwargs):
            raise AssertionError("native approvals must not register global commands")
        def register_tool(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "state_path": str(tmp_path / "state.json"), "mode": "telegram",
        "human_user_id": "42", "human_chat_id": "42",
    })
    monkeypatch.setenv("ROOM_API", "https://relay.example.test")
    monkeypatch.setenv("ROOM_PROJECT", "demo")
    monkeypatch.setenv("AGENT_ID", "hermes")
    context = FakeContext()
    plugin.register(context)
    gateway = FakeGateway()
    event = Event(Source())
    session_store = FakeSessionStore(None)
    adapter = gateway.adapters["telegram"]

    assert plugin.bind_existing_route(gateway, session_store, event, adapter) is None

    session_store.session_id = "active-session"
    snapshot = plugin.bind_existing_route(gateway, session_store, event, adapter)
    assert snapshot["session_id"] == "active-session"
    assert plugin.route_session_is_current(gateway, session_store, snapshot)
    session_store.session_id = "replacement-session"
    assert not plugin.route_session_is_current(gateway, session_store, snapshot)


def test_relay_client_uses_project_url_without_application_credentials(monkeypatch):
    requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_args): pass
        def read(self,_size=-1): return json.dumps({"messages":[],"next_after":0}).encode()
    class Opener:
        def open(self,request,timeout):
            requests.append(request)
            return Response()
    monkeypatch.setattr(plugin,"_opener",Opener())
    config=plugin.configured_client({"api":"https://relay.example.test","project":"demo","agent_id":"hermes"})
    assert config=={"api":"https://relay.example.test","project":"demo","agent_id":"hermes"}
    plugin.read_page(config,0)
    request=requests[0]
    assert request.full_url=="https://relay.example.test/v1/messages?project=demo&after=0&limit=100"
    assert request.get_header("Authorization") is None


def test_relay_page_rejects_malformed_envelopes():
    with pytest.raises(plugin.RelayError, match="invalid message"):
        plugin.validate_message(peer_message(hop=9))
    with pytest.raises(plugin.RelayError, match="invalid message"):
        plugin.validate_message(peer_message(complete="false"))
    with pytest.raises(plugin.RelayError, match="invalid message"):
        plugin.validate_message(peer_message(delivery=[]))


def test_expired_approval_state_is_not_reused():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
        directive = plugin.guard_peer_tool_call(
            {"tool_name": "terminal", "args": {"command": "rm -r build"}},
            "session-1", {"session-1"},
        )
        assert directive["action"] == "approve"
        assert directive["rule_key"]  # Each call gets a fresh, one-call-only grant key.
    finally:
        monkeypatch.undo()


def test_dashboard_listener_starts_at_plugin_registration_and_exposes_health(monkeypatch, tmp_path):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    threads = []
    class FakeThread:
        def __init__(self, target, **kwargs):
            self.target = target
            self.kwargs = kwargs
            self.started = False
            threads.append(self)
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": "dashboard", "state_path": str(tmp_path / "state.json"),
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: lambda: None)
    monkeypatch.setattr(plugin.threading, "Thread", FakeThread)
    context = FakeContext()

    plugin.register(context)

    assert len(threads) == 1 and threads[0].started
    status_handler = context.tools["agent_commons_status"]["handler"]
    status = json.loads(status_handler())
    assert status["mode"] == "dashboard"
    assert status["listener"] == "waiting_for_session"
    positional_status = json.loads(status_handler({}))
    assert positional_status["listener"] == "waiting_for_session"


def test_passive_dashboard_keeps_send_tool_and_sends_without_outbox(monkeypatch, tmp_path):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    threads = []
    class FakeThread:
        def __init__(self, target, **kwargs):
            self.target = target
            threads.append(self)
        def start(self):
            pass
        def is_alive(self):
            return False

    state_file = tmp_path / "state.json"
    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": "dashboard", "state_path": str(state_file),
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: (_ for _ in ()).throw(FileExistsError("owned")))
    monkeypatch.setattr(plugin.threading, "Thread", FakeThread)
    monkeypatch.setattr(plugin, "send_message", lambda _config, **_kwargs: {"seq": 12})
    monkeypatch.setattr(plugin, "queue_outbound_message", lambda *_args: pytest.fail("passive send must not use the outbox"))
    context = FakeContext()

    plugin.register(context)
    params = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "hello", "complete": False, "client_msg_id": MESSAGE_ID,
    }
    result = json.loads(context.tools["agent_commons_send"]["handler"](params, session_id="session-1"))

    assert result["sent"] is True and result["seq"] == 12
    assert "listening window" in result["message"].lower()
    assert not state_file.exists()
    assert threads  # A passive process keeps retrying ownership.


def test_listener_role_uses_the_supported_hermes_gateway_marker():
    assert plugin.listener_role_allowed("telegram", gateway_process=True)
    assert not plugin.listener_role_allowed("telegram", gateway_process=False)
    assert plugin.listener_role_allowed("dashboard", gateway_process=False)
    assert not plugin.listener_role_allowed("dashboard", gateway_process=True)
    assert plugin.listener_role_allowed("cli", gateway_process=False)
    assert not plugin.listener_role_allowed("cli", gateway_process=True)


def test_dashboard_mode_messaging_gateway_is_passive(monkeypatch, tmp_path):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": "dashboard", "state_path": str(tmp_path / "state.json"),
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: pytest.fail("messaging gateway must not own dashboard listener"))
    context = FakeContext()

    plugin.register(context)

    status = json.loads(context.tools["agent_commons_status"]["handler"]())
    assert status["listener"] == "passive"
    assert not status["owns_listener"]
    assert "agent_commons_send" in context.tools


def test_dashboard_listener_retries_lock_and_waits_for_a_session(monkeypatch, tmp_path):
    class FakeContext:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    threads = []
    class FakeThread:
        def __init__(self, target, **_kwargs):
            self.target = target
            threads.append(self)
        def start(self):
            pass
        def is_alive(self):
            return True

    attempts = []
    def acquire(_path):
        attempts.append(1)
        if len(attempts) < 3:
            raise FileExistsError("another process owns the listener")
        return lambda: None

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": "dashboard", "state_path": str(tmp_path / "state.json"),
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", acquire)
    monkeypatch.setattr(plugin.threading, "Thread", FakeThread)
    read_calls = []
    monkeypatch.setattr(plugin, "read_page", lambda *_args: read_calls.append(1))
    delays = []
    class StopPolling(Exception):
        pass
    def sleep(delay):
        delays.append(delay)
        if delay == plugin._POLL_SECONDS:
            raise StopPolling
    monkeypatch.setattr(plugin.time, "sleep", sleep)
    context = FakeContext()

    plugin.register(context)

    with pytest.raises(StopPolling):
        threads[0].target()
    assert attempts == [1, 1, 1]
    assert delays == [plugin._OWNER_RETRY_SECONDS, plugin._POLL_SECONDS]
    assert read_calls == []


class _HermesTestContext:
    def __init__(self, manager=None):
        self.hooks = {}
        self.tools = {}
        self._manager = manager

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_tool(self, name, **kwargs):
        self.tools[name] = kwargs


class _HermesCapturedThread:
    instances: ClassVar[list] = []

    def __init__(self, target, **_kwargs):
        self.target = target
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started


def _setup_hermes_test(monkeypatch, state_file, lock_acquire, *, mode="cli", context=None):
    settings = {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": mode, "state_path": str(state_file),
    }
    monkeypatch.setattr(plugin, "plugin_settings", lambda: settings)
    monkeypatch.setattr(plugin, "acquire_owner_lock", lock_acquire)
    monkeypatch.setattr(plugin.threading, "Thread", _HermesCapturedThread)
    context = context or _HermesTestContext()
    plugin.register(context)
    return context


def _write_hermes_state(path, *, session_id=None, cursor=0):
    config = {"api": "https://relay.example.test", "project": "demo", "agent_id": "hermes"}
    state = plugin.load_state(path, plugin.connection_fingerprint(config))
    plugin.save_state(path, {**state, "session_id": session_id, "cursor": cursor})


def test_passive_session_start_cannot_overwrite_owner_state(tmp_path, monkeypatch):
    _HermesCapturedThread.instances.clear()
    state_file = tmp_path / "state.json"
    owner_held = False

    def acquire(_path):
        nonlocal owner_held
        if owner_held:
            raise FileExistsError("listener is owned")
        owner_held = True
        def release():
            nonlocal owner_held
            owner_held = False
        return release

    owner_manager = type("Manager", (), {})()
    owner_manager._cli_ref = type("Cli", (), {
        "session_id": "session-owner",
        "agent": type("Agent", (), {"session_id": "session-owner"})(),
    })()
    owner = _setup_hermes_test(
        monkeypatch, state_file, acquire, context=_HermesTestContext(owner_manager)
    )
    passive = _setup_hermes_test(monkeypatch, state_file, acquire)
    monkeypatch.setattr(plugin, "send_message", lambda *_args, **_kwargs: {"seq": 1})
    owner.hooks["on_session_start"](session_id="session-owner", platform="cli")

    send = passive.tools["agent_commons_send"]["handler"]
    send({
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "passive cache warm-up", "complete": False,
        "client_msg_id": MESSAGE_ID,
    }, session_id="session-passive")
    monkeypatch.setattr(plugin, "read_page", lambda *_args: {
        "messages": [peer_message()], "next_after": 8,
    })

    class StopPolling(Exception):
        pass

    monkeypatch.setattr(plugin.time, "sleep", lambda _delay: (_ for _ in ()).throw(StopPolling()))
    with pytest.raises(StopPolling):
        _HermesCapturedThread.instances[0].target()
    owner.hooks["pre_llm_call"](
        session_id="session-owner",
        user_message=plugin.make_peer_text(peer_message(
            client_msg_id="550e8400-e29b-41d4-a716-446655440003"
        )),
    )
    peer_sessions_file = state_file.with_name(state_file.stem + "-peer-sessions.json")
    assert plugin.load_peer_sessions(peer_sessions_file) == {"session-owner"}
    before = plugin.load_state(state_file)
    assert before["cursor"] == 8 and before["pending"][0]["target_session_id"] == "session-owner"

    passive.hooks["pre_llm_call"](
        session_id="session-passive",
        user_message=plugin.make_peer_text(peer_message(
            client_msg_id="550e8400-e29b-41d4-a716-446655440004"
        )),
    )
    monkeypatch.setattr(plugin, "_approval_redactor", lambda text: text)
    passive_guard = passive.hooks["pre_tool_call"](
        tool_name="terminal", args={"command": "echo check"}, session_id="session-passive"
    )
    assert passive_guard["action"] == "approve"
    passive.hooks["on_session_start"](session_id="session-passive", platform="cli")

    assert plugin.load_state(state_file) == before
    assert plugin.load_peer_sessions(peer_sessions_file) == {"session-owner"}


def test_takeover_reloads_state_cached_while_passive(tmp_path, monkeypatch):
    _HermesCapturedThread.instances.clear()
    state_file = tmp_path / "state.json"
    owner_held = False
    releases = []

    def acquire(_path):
        nonlocal owner_held
        if owner_held:
            raise FileExistsError("listener is owned")
        owner_held = True
        def release():
            nonlocal owner_held
            owner_held = False
        releases.append(release)
        return release

    owner_manager = type("Manager", (), {})()
    owner_manager._cli_ref = type("Cli", (), {
        "session_id": "session-owner",
        "agent": type("Agent", (), {"session_id": "session-owner"})(),
    })()
    owner = _setup_hermes_test(
        monkeypatch, state_file, acquire, context=_HermesTestContext(owner_manager)
    )
    manager = type("Manager", (), {})()
    manager._cli_ref = type("Cli", (), {
        "session_id": "session-owner",
        "agent": type("Agent", (), {"session_id": "session-owner"})(),
    })()
    passive = _setup_hermes_test(
        monkeypatch, state_file, acquire, context=_HermesTestContext(manager)
    )
    monkeypatch.setattr(plugin, "send_message", lambda *_args, **_kwargs: {"seq": 1})
    passive.tools["agent_commons_send"]["handler"]({
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "passive cache warm-up", "complete": False,
        "client_msg_id": MESSAGE_ID,
    }, session_id="session-owner")
    owner.hooks["on_session_start"](session_id="session-owner", platform="cli")
    cursors = []

    def read_page(_config, cursor):
        cursors.append(cursor)
        messages = [peer_message(seq=7)] if cursor == 0 else []
        return {"messages": messages, "next_after": 7}

    monkeypatch.setattr(plugin, "read_page", read_page)

    class StopPolling(Exception):
        pass

    monkeypatch.setattr(plugin.time, "sleep", lambda _delay: (_ for _ in ()).throw(StopPolling()))
    with pytest.raises(StopPolling):
        _HermesCapturedThread.instances[0].target()
    owner.hooks["pre_llm_call"](
        session_id="session-owner",
        user_message=plugin.make_peer_text(peer_message(
            client_msg_id="550e8400-e29b-41d4-a716-446655440003"
        )),
    )
    releases[0]()

    with pytest.raises(StopPolling):
        _HermesCapturedThread.instances[1].target()
    passive.hooks["pre_llm_call"](
        session_id="session-passive",
        user_message=plugin.make_peer_text(peer_message(
            client_msg_id="550e8400-e29b-41d4-a716-446655440004"
        )),
    )

    saved = plugin.load_state(state_file)
    assert cursors == [0, 7]
    assert saved["cursor"] == 7
    assert [item["client_msg_id"] for item in saved["pending"]] == [MESSAGE_ID]
    peer_sessions_file = state_file.with_name(state_file.stem + "-peer-sessions.json")
    assert plugin.load_peer_sessions(peer_sessions_file) == {"session-owner", "session-passive"}


def test_dashboard_startup_uses_live_session_not_saved_id(tmp_path, monkeypatch):
    import sys
    import threading
    from types import ModuleType, SimpleNamespace

    _HermesCapturedThread.instances.clear()
    state_file = tmp_path / "state.json"
    _write_hermes_state(state_file, session_id="session-old", cursor=4)
    agent = SimpleNamespace(session_id="session-current")
    server = SimpleNamespace(
        _sessions={"current-rpc-id": {"agent": agent, "_finalized": False}},
        _sessions_lock=threading.RLock(),
    )
    package = ModuleType("tui_gateway")
    package.server = server
    monkeypatch.setitem(sys.modules, "tui_gateway", package)
    _setup_hermes_test(
        monkeypatch, state_file, lambda _path: lambda: None, mode="dashboard"
    )
    read_calls = []
    monkeypatch.setattr(plugin, "read_page", lambda _config, cursor: read_calls.append(cursor) or {
        "messages": [peer_message(seq=5)], "next_after": 5,
    })
    delivered = []
    monkeypatch.setattr(
        plugin, "inject_dashboard_message",
        lambda session_id, *_args: delivered.append(session_id) or True,
    )

    class StopPolling(Exception):
        pass

    monkeypatch.setattr(plugin.time, "sleep", lambda _delay: (_ for _ in ()).throw(StopPolling()))
    with pytest.raises(StopPolling):
        _HermesCapturedThread.instances[0].target()

    saved = plugin.load_state(state_file)
    assert read_calls == [4]
    assert saved["session_id"] == "session-current"
    assert saved["pending"][0]["target_session_id"] == "session-current"
    assert delivered == ["session-current"]


def test_cli_startup_uses_current_session_not_saved_id(tmp_path, monkeypatch):
    import queue

    _HermesCapturedThread.instances.clear()
    state_file = tmp_path / "state.json"
    _write_hermes_state(state_file, session_id="session-old", cursor=4)
    pending_input = queue.Queue()
    cli = type("Cli", (), {
        "session_id": "session-current",
        "agent": type("Agent", (), {"session_id": "session-current"})(),
        "_pending_input": pending_input,
        "_agent_running": False,
    })()
    manager = type("Manager", (), {"_cli_ref": cli})()
    _setup_hermes_test(
        monkeypatch, state_file, lambda _path: lambda: None,
        context=_HermesTestContext(manager),
    )
    monkeypatch.setattr(plugin, "read_page", lambda _config, cursor: {
        "messages": [peer_message(seq=5)], "next_after": 5,
    })

    class StopPolling(Exception):
        pass

    monkeypatch.setattr(plugin.time, "sleep", lambda _delay: (_ for _ in ()).throw(StopPolling()))
    with pytest.raises(StopPolling):
        _HermesCapturedThread.instances[0].target()

    saved = plugin.load_state(state_file)
    assert saved["session_id"] == "session-current"
    assert saved["pending"][0]["target_session_id"] == "session-current"
    assert pending_input.qsize() == 1


def test_passive_completion_closes_thread_for_later_local_sends(tmp_path, monkeypatch):
    _HermesCapturedThread.instances.clear()
    state_file = tmp_path / "state.json"
    context = _setup_hermes_test(
        monkeypatch, state_file,
        lambda _path: (_ for _ in ()).throw(FileExistsError("listener is owned")),
        mode="dashboard",
    )
    send_calls = []
    monkeypatch.setattr(plugin, "send_message", lambda *_args, **_kwargs: send_calls.append(1) or {"seq": 1})
    send = context.tools["agent_commons_send"]["handler"]
    params = {
        "recipient": "pi", "thread_id": THREAD, "reply_to": None, "hop": 0,
        "text": "done", "complete": True, "client_msg_id": MESSAGE_ID,
    }

    first = json.loads(send(params, session_id="session-human"))
    second = json.loads(send({**params, "client_msg_id": "550e8400-e29b-41d4-a716-446655440003"}, session_id="session-human"))

    assert first["sent"] is True
    assert "error" in second and "closed" in second["error"].lower()
    assert len(send_calls) == 1


def test_session_rebind_preserves_concurrent_outbox_enqueue():
    import threading

    class ObservableRLock:
        def __init__(self):
            self.lock = threading.RLock()
            self.condition = threading.Condition()
            self.sender_blocked = False
            self.sender_done = False

        def __enter__(self):
            if threading.current_thread().name == "outbox-sender":
                if not self.lock.acquire(blocking=False):
                    with self.condition:
                        self.sender_blocked = True
                        self.condition.notify_all()
                    self.lock.acquire()
            else:
                self.lock.acquire()
            return self

        def __exit__(self, *_args):
            self.lock.release()

        def mark_sender_done(self):
            with self.condition:
                self.sender_done = True
                self.condition.notify_all()

        def wait_for_sender_progress(self):
            with self.condition:
                assert self.condition.wait_for(
                    lambda: self.sender_blocked or self.sender_done, timeout=5
                )

    snapshot_read = threading.Event()
    allow_rebind = threading.Event()
    state_lock = ObservableRLock()

    class PausedState(dict):
        def get(self, key, *args):
            if key == "session_id" and threading.current_thread().name == "session-rebind":
                snapshot_read.set()
                allow_rebind.wait(timeout=5)
            return super().get(key, *args)

    state = PausedState(session_id="old-session", outbox=[])

    def persist(next_state):
        nonlocal state
        state = next_state

    rebind = threading.Thread(
        name="session-rebind",
        target=lambda: plugin._persist_session_id_if_changed(
            state_lock, lambda: state, persist, "new-session"
        ),
    )
    rebind.start()
    assert snapshot_read.wait(timeout=5)

    def enqueue():
        nonlocal state
        with state_lock:
            state = {**state, "outbox": [{"client_msg_id": MESSAGE_ID}]}
        state_lock.mark_sender_done()

    sender = threading.Thread(name="outbox-sender", target=enqueue)
    sender.start()
    state_lock.wait_for_sender_progress()
    allow_rebind.set()
    rebind.join(timeout=5)
    sender.join(timeout=5)

    assert not rebind.is_alive()
    assert not sender.is_alive()
    assert state["session_id"] == "new-session"
    assert [item["client_msg_id"] for item in state["outbox"]] == [MESSAGE_ID]


def _register_telegram_status_test(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    class Context:
        def __init__(self):
            self.hooks = {}
            self.tools = {}
        def register_hook(self, name, handler):
            self.hooks[name] = handler
        def register_tool(self, name, **kwargs):
            self.tools[name] = kwargs

    class Gateway:
        def __init__(self):
            self.adapters = {"telegram": object()}
        def _session_key_for_source(self, _source):
            return "telegram:42:42:topic-2"

    class SessionStore:
        session_id = "active-session"
        def get_session_id(self, key):
            return self.session_id if key == "telegram:42:42:topic-2" else None

    monkeypatch.setattr(plugin, "plugin_settings", lambda: {
        "api": "https://relay.example.test", "project": "demo", "agent_id": "hermes",
        "mode": "telegram", "state_path": str(tmp_path / "state.json"),
        "human_user_id": "42", "human_chat_id": "42",
    })
    monkeypatch.setattr(plugin, "acquire_owner_lock", lambda _path: lambda: None)
    monkeypatch.setattr(plugin, "read_page", lambda _config, cursor: {
        "messages": [], "next_after": cursor,
    })
    monkeypatch.setattr(plugin, "_POLL_SECONDS", 0.01)
    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    context = Context()
    plugin.register(context)
    source = SimpleNamespace(
        platform="telegram", user_id="42", chat_id="42", thread_id="topic-2", chat_type="dm"
    )
    event = SimpleNamespace(source=source, text="human input", metadata={})
    return context, Gateway(), event, SessionStore(), asyncio


def test_telegram_route_invalidation_resets_listener_health(monkeypatch, tmp_path):
    import json

    context, gateway, event, session_store, asyncio = _register_telegram_status_test(monkeypatch, tmp_path)
    status_tool = context.tools["agent_commons_status"]["handler"]

    async def exercise():
        def status():
            return json.loads(status_tool())

        async def wait_for_listener(expected):
            for _ in range(500):
                current = status()
                if current["listener"] == expected:
                    return current
                await asyncio.sleep(0.001)
            pytest.fail(f"listener did not enter {expected}: {status()}")

        context.hooks["pre_gateway_dispatch"](event, gateway, session_store=session_store)
        assert (await wait_for_listener("listening"))["session_target_bound"] is True
        session_store.session_id = "replacement-session"
        current = await wait_for_listener("waiting_for_route")
        assert current["session_target_bound"] is False

    asyncio.run(exercise())


def test_telegram_session_finalization_resets_listener_health(monkeypatch, tmp_path):
    import json

    context, gateway, event, session_store, asyncio = _register_telegram_status_test(monkeypatch, tmp_path)
    status_tool = context.tools["agent_commons_status"]["handler"]

    async def exercise():
        def status():
            return json.loads(status_tool())

        context.hooks["pre_gateway_dispatch"](event, gateway, session_store=session_store)
        for _ in range(500):
            if status()["listener"] == "listening":
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail(f"listener did not start: {status()}")

        context.hooks["on_session_finalize"](session_id="active-session", platform="telegram")
        current = status()
        assert current["listener"] == "waiting_for_route"
        assert current["session_target_bound"] is False

    asyncio.run(exercise())
