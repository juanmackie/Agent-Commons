import json
import sqlite3
import time

import pytest

from bridge.adapters.base import AgentResult
from bridge.store import BridgeStore
from bridge.worker import BridgeWorker


class FakeRelay:
    def __init__(self, messages):
        self.messages = messages
        self.posts = []
        self.accepted = {}
        self.fail_once = False

    def read(self, after, limit):
        messages = [m for m in self.messages if m["seq"] > after][:limit]
        return {"messages": messages, "next_after": messages[-1]["seq"] if messages else after}

    def post(self, message):
        prior = self.accepted.get(message["client_msg_id"])
        if prior is not None:
            return prior
        accepted = dict(message)
        accepted["seq"] = max((item["seq"] for item in self.messages), default=0) + 1
        accepted["created_at"] = "2026-10-31T00:00:00Z"
        self.messages.append(accepted)
        self.posts.append(message)
        self.accepted[message["client_msg_id"]] = accepted
        if self.fail_once:
            self.fail_once = False
            raise OSError("response lost after acceptance")
        return accepted


class FakeAgent:
    def __init__(self):
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        return AgentResult('{"reply":"answer","complete":false}')


class FailingAgent:
    def __init__(self):
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        raise TimeoutError("simulated agent timeout")


class CompletingAgent:
    def __init__(self):
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1
        return AgentResult('{"reply":"final answer","complete":true}')


def message(seq=1, sender="hermes", recipient="pi", hop=0):
    return {"seq": seq, "client_msg_id": str(seq), "sender": sender, "recipient": recipient,
            "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None,
            "hop":hop, "text":"review this", "complete":False, "created_at":"2026-10-31T00:00:00Z"}


@pytest.mark.parametrize(("agent_id", "options"), [
    ("not valid!", {}),
    ("pi", {"poll_seconds": float("nan")}),
    ("pi", {"max_auto_replies_per_thread": 5}),
    ("pi", {"max_hop": 9}),
    ("pi", {"agent_timeout": float("inf")}),
])
def test_bridge_rejects_unsafe_or_unbounded_configuration(agent_id, options):
    with pytest.raises(ValueError):
        BridgeWorker(None, None, None, agent_id=agent_id, **options)


def test_bridge_filters_self_and_other_recipient_and_publishes_once():
    relay = FakeRelay([message(), message(2, sender="pi", recipient="pi"), message(3, recipient="hermes")])
    state = BridgeStore(":memory:")
    agent = FakeAgent()
    worker = BridgeWorker(relay, state, agent, agent_id="pi")
    worker.poll_once()
    assert worker.work_once()
    assert len(relay.posts) == 1 and relay.posts[0]["reply_to"] == 1
    assert worker.work_once() and worker.work_once()
    assert agent.calls == 1
    assert state.cursor == 3


def test_outbox_retry_reuses_identical_payload_after_post_failure():
    relay = FakeRelay([message()])
    relay.fail_once = True
    state = BridgeStore(":memory:")
    agent = FakeAgent()
    worker = BridgeWorker(relay, state, agent, agent_id="pi")
    worker.poll_once()
    with pytest.raises(OSError):
        worker.work_once()
    saved = state.pending()[2]
    worker.work_once()
    assert relay.posts == [saved]
    assert agent.calls == 1


def test_restart_retries_persisted_outbox_without_duplicate_post(tmp_path):
    relay = FakeRelay([message()])
    relay.fail_once = True
    path = tmp_path / "bridge.sqlite3"
    store = BridgeStore(path)
    agent = FakeAgent()
    worker = BridgeWorker(relay, store, agent, agent_id="pi")
    worker.poll_once()
    with pytest.raises(OSError):
        worker.work_once()
    saved = store.pending()[2]
    store.db.close()

    restarted_store = BridgeStore(path)
    restarted = BridgeWorker(relay, restarted_store, FakeAgent(), agent_id="pi")
    assert restarted.work_once()
    assert relay.posts == [saved]
    assert restarted_store.pending() is None


def test_poll_response_loss_does_not_advance_cursor_or_drop_message():
    relay = FakeRelay([message()])
    state = BridgeStore(":memory:")
    worker = BridgeWorker(relay, state, FakeAgent(), agent_id="pi")
    original_read = relay.read
    attempts = 0

    def lose_first_response(after, limit):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("simulated lost poll response")
        return original_read(after, limit)

    relay.read = lose_first_response
    with pytest.raises(TimeoutError):
        worker.poll_once()
    assert state.cursor == 0
    assert state.pending() is None

    assert worker.poll_once() == 1
    assert state.cursor == 1
    assert state.pending()[0] == 1


def test_restart_posts_saved_reply_after_crash_before_first_post(tmp_path):
    relay = FakeRelay([message()])
    path = tmp_path / "bridge.sqlite3"
    first_store = BridgeStore(path)
    first_agent = FakeAgent()
    first_worker = BridgeWorker(relay, first_store, first_agent, agent_id="pi")
    first_worker.poll_once()
    original_post = relay.post
    relay.post = lambda payload: (_ for _ in ()).throw(SystemExit("simulated process crash before POST"))

    with pytest.raises(SystemExit):
        first_worker.work_once()
    saved = first_store.pending()[2]
    assert saved is not None
    assert relay.posts == []
    assert first_agent.calls == 1
    first_store.db.close()

    relay.post = original_post
    restarted_store = BridgeStore(path)
    restarted_agent = FakeAgent()
    restarted = BridgeWorker(relay, restarted_store, restarted_agent, agent_id="pi")
    assert restarted.work_once()
    assert relay.posts == [saved]
    assert restarted_agent.calls == 0
    assert restarted_store.pending() is None


def test_two_fake_agents_complete_bounded_multi_turn_exchange():
    relay = FakeRelay([message()])
    hermes_store = BridgeStore(":memory:")
    pi_store = BridgeStore(":memory:")
    hermes = BridgeWorker(relay, hermes_store, FakeAgent(), agent_id="hermes", max_auto_replies_per_thread=4)
    pi = BridgeWorker(relay, pi_store, FakeAgent(), agent_id="pi", max_auto_replies_per_thread=4)

    for _ in range(20):
        hermes.poll_once()
        pi.poll_once()
        progressed = hermes.work_once() | pi.work_once()
        if not progressed and hermes_store.cursor == max(m["seq"] for m in relay.messages) and pi_store.cursor == hermes_store.cursor:
            break

    assert len(relay.messages) == 9
    assert [item["hop"] for item in relay.messages] == list(range(9))
    assert len({item["client_msg_id"] for item in relay.messages}) == 9
    assert hermes_store.thread_reply_count(message()["thread_id"]) == 4
    assert pi_store.thread_reply_count(message()["thread_id"]) == 4


def test_explicit_completion_is_published_and_stops_the_peer_without_another_model_call():
    relay = FakeRelay([message()])
    pi_store = BridgeStore(":memory:")
    hermes_store = BridgeStore(":memory:")
    final_agent = CompletingAgent()
    hermes_agent = FakeAgent()
    pi = BridgeWorker(relay, pi_store, final_agent, agent_id="pi")
    hermes = BridgeWorker(relay, hermes_store, hermes_agent, agent_id="hermes")

    pi.poll_once()
    assert pi.work_once()
    assert relay.messages[-1]["complete"] is True
    hermes.poll_once()
    assert hermes.work_once()  # Ignore the original message addressed to Pi.
    assert hermes.work_once()  # Observe the explicit thread-completion message.
    assert hermes_agent.calls == 0
    assert hermes_store.thread_is_closed(message()["thread_id"])
    assert len(relay.posts) == 1


def test_agent_failures_exhaust_thread_budget_and_require_explicit_retry():
    relay = FakeRelay([message()])
    state = BridgeStore(":memory:")
    failing = FailingAgent()
    worker = BridgeWorker(relay, state, failing, agent_id="pi", max_auto_replies_per_thread=2)
    worker.poll_once()

    for _ in range(2):
        with pytest.raises(TimeoutError):
            worker.work_once()
    assert worker.work_once() is True
    assert failing.calls == 2
    assert state.pending() is None
    status = state.status()
    assert status["pending_count"] == 0
    assert status["failed_count"] == 1
    assert status["recent_errors"][0]["error_category"].startswith("thread_budget_exhausted:TimeoutError")
    assert state.thread_reply_count(message()["thread_id"]) == 0
    assert state.thread_attempt_count(message()["thread_id"]) == 2

    assert state.retry_failed(1)
    recovered = BridgeWorker(relay, state, FakeAgent(), agent_id="pi", max_auto_replies_per_thread=2)
    assert recovered.work_once()
    assert state.pending() is None
    assert state.thread_attempt_count(message()["thread_id"]) == 1
    assert state.thread_reply_count(message()["thread_id"]) == 1


def test_hermes_adapter_timeout_is_bounded_by_remaining_idempotency_window():
    class TimeoutObservingAgent(FailingAgent):
        idempotency_window_seconds = 240

        def run(self, **kwargs):
            self.timeout = kwargs["timeout"]
            return super().run(**kwargs)

    relay = FakeRelay([message()])
    state = BridgeStore(":memory:")
    agent = TimeoutObservingAgent()
    worker = BridgeWorker(relay, state, agent, agent_id="pi", agent_timeout=300)
    worker.poll_once()
    state.db.execute("UPDATE inbox SET agent_started_at=? WHERE seq=1", (time.time() - 239,))

    with pytest.raises(TimeoutError):
        worker.work_once()

    assert 0 < agent.timeout <= 1


def test_hermes_retry_window_expires_closed_after_bridge_restart():
    class IdempotentFailingAgent(FailingAgent):
        idempotency_window_seconds = 240

    relay = FakeRelay([message()])
    state = BridgeStore(":memory:")
    agent = IdempotentFailingAgent()
    worker = BridgeWorker(relay, state, agent, agent_id="pi", max_auto_replies_per_thread=4)
    worker.poll_once()

    with pytest.raises(TimeoutError):
        worker.work_once()
    state.db.execute("UPDATE inbox SET agent_started_at=? WHERE seq=1", (time.time() - 241,))

    assert worker.work_once()
    assert agent.calls == 1
    assert state.pending() is None
    assert state.status()["recent_errors"][0]["error_category"].startswith("idempotency_window_expired:TimeoutError")


def test_legacy_bridge_inbox_migrates_request_start_timestamp(tmp_path):
    path = tmp_path / "legacy-bridge.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE state(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
          INSERT INTO state VALUES('cursor', 1);
          CREATE TABLE inbox(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
          CREATE TABLE outbox(inbound_seq INTEGER PRIMARY KEY REFERENCES inbox(seq), payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending');
          CREATE TABLE thread_sessions(thread_id TEXT PRIMARY KEY, session_ref TEXT, reply_count INTEGER NOT NULL DEFAULT 0, agent_attempts INTEGER NOT NULL DEFAULT 0);
        """)
        db.execute("INSERT INTO inbox(seq,payload) VALUES(1,?)", (json.dumps(message()),))

    state = BridgeStore(path)
    assert state.pending()[1]["text"] == "review this"
    assert state.agent_attempt_age(1) is None
    assert state.begin_agent_attempt(1, message()["thread_id"], 4)
    assert state.agent_attempt_age(1) is not None


def test_operator_retry_preserves_successful_thread_reply_budget():
    state = BridgeStore(":memory:")
    thread_id = message()["thread_id"]
    state.mark_done(99, replied_thread=thread_id)
    state.ingest([message()], 1)
    state.mark_failed(1, "thread_budget_exhausted")

    assert state.retry_failed(1)
    assert state.thread_reply_count(thread_id) == 1
    assert state.thread_attempt_count(thread_id) == 1


def test_bridge_store_migrates_existing_thread_sessions_without_losing_data(tmp_path):
    path = tmp_path / "legacy-bridge.sqlite3"
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE thread_sessions(thread_id TEXT PRIMARY KEY, session_ref TEXT, reply_count INTEGER NOT NULL DEFAULT 0)")
        db.execute("INSERT INTO thread_sessions VALUES(?,?,?)", ("thread-1", "session-1", 3))
    migrated = BridgeStore(path)
    row = migrated.db.execute("SELECT session_ref,reply_count,agent_attempts FROM thread_sessions WHERE thread_id='thread-1'").fetchone()
    assert tuple(row) == ("session-1", 3, 0)


def test_store_recovers_cursor_and_pending_inbox_after_restart(tmp_path):
    path = tmp_path / "bridge.sqlite3"
    first = BridgeStore(path)
    first.ingest([message(7)], 7)
    first.db.close()
    reopened = BridgeStore(path)
    assert reopened.cursor == 7
    assert reopened.pending()[0:2] == (7, message(7))
