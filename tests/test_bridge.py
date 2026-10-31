import json

import pytest

from bridge.adapters.base import AgentResult
from bridge.store import BridgeStore
from bridge.worker import BridgeWorker


class FakeRelay:
    def __init__(self, messages):
        self.messages = messages
        self.posts = []
        self.fail_once = False

    def read(self, after, limit):
        messages = [m for m in self.messages if m["seq"] > after][:limit]
        return {"messages": messages, "next_after": messages[-1]["seq"] if messages else after}

    def post(self, message):
        if self.fail_once:
            self.fail_once = False
            raise OSError("response lost")
        self.posts.append(message)
        self.messages.append(message)
        return message


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


def test_store_recovers_cursor_and_pending_inbox_after_restart(tmp_path):
    path = tmp_path / "bridge.sqlite3"
    first = BridgeStore(path)
    first.ingest([message(7)], 7)
    first.db.close()
    reopened = BridgeStore(path)
    assert reopened.cursor == 7
    assert reopened.pending()[0:2] == (7, message(7))
