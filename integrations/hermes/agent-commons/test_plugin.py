import importlib.util
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

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
    bypassed = plugin.guard_peer_tool_call(call, "session-1", {"session-1"}, approval_bypass=True)
    assert bypassed["action"] == "block"
    assert "approval bypass" in bypassed["message"]


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
    monkeypatch.setattr(plugin, "_approval_bypass_active", lambda: False)

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


def test_state_retains_pending_inbox_and_connection_identity(tmp_path):
    state_file = tmp_path / "state.json"
    initial = {"cursor": 0, "seen": [], "pending": [], "outbox": [], "closed_threads": [], "fingerprint": "room-a", "session_id": None}
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
    assert "secret" not in str(failure.value)


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
