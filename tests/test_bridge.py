import json

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
        return AgentResult("answer")


def message(seq=1, sender="hermes", recipient="pi", hop=0):
    return {"seq": seq, "client_msg_id": str(seq), "sender": sender, "recipient": recipient,
            "thread_id":"b3d9dcc7-137c-481f-b268-2a40c7e20c88", "reply_to":None,
            "hop":hop, "text":"review this", "created_at":"2026-10-31T00:00:00Z"}


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


def test_store_recovers_cursor_and_pending_inbox_after_restart(tmp_path):
    path = tmp_path / "bridge.sqlite3"
    first = BridgeStore(path)
    first.ingest([message(7)], 7)
    first.db.close()
    reopened = BridgeStore(path)
    assert reopened.cursor == 7
    assert reopened.pending()[0:2] == (7, message(7))
