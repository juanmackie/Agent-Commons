import json
import urllib.error
import urllib.parse

import pytest

from bridge.adapters.hermes_api import HermesApiAdapter, HermesApiError


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size=-1):
        return json.dumps(self.payload).encode()[:size]


class FakeOpener:
    def __init__(self, *, enabled=False, final_status="completed", lose_first_post_response=False):
        self.enabled = enabled
        self.final_status = final_status
        self.lose_first_post_response = lose_first_post_response
        self.requests = []
        self.admissions = {}

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        path = urllib.parse.urlsplit(request.full_url).path
        if path == "/v1/toolsets":
            return Response({"data": [{"name": "terminal", "enabled": self.enabled}]})
        if path == "/v1/responses":
            body = json.loads(request.data)
            key = request.get_header("Idempotency-key")
            admission = self.admissions.get(key)
            if admission is None:
                admission = (body, {
                    "status": self.final_status,
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "answer"}],
                    }],
                })
                self.admissions[key] = admission
            else:
                assert admission[0] == body
            if self.lose_first_post_response:
                self.lose_first_post_response = False
                raise urllib.error.URLError("simulated lost response")
            return Response(admission[1])
        raise AssertionError(f"unexpected Hermes request: {request.method} {path}")


def test_hermes_responses_checks_toolset_and_returns_completed_output(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr("bridge.adapters.hermes_api._opener", opener)
    adapter = HermesApiAdapter("http://127.0.0.1:8642", "test-key", toolsets_verified=True)

    result = adapter.run(
        thread_id="b3d9dcc7-137c-481f-b268-2a40c7e20c88",
        session_ref=None,
        prompt="safe task",
        timeout=2,
        request_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
    )

    assert result.text == "answer"
    assert result.session_ref.startswith("agent-room-")
    assert [urllib.parse.urlsplit(req.full_url).path for req, _ in opener.requests] == [
        "/v1/toolsets", "/v1/responses"
    ]
    request, _ = opener.requests[1]
    assert request.get_header("Idempotency-key") == "agent-room-cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13"
    body = json.loads(request.data)
    assert body["conversation"] == result.session_ref
    assert body["input"] == "safe task"
    assert body["store"] is True and body["stream"] is False
    assert all(req.get_header("Authorization") == "Bearer test-key" for req, _ in opener.requests)


def test_hermes_adapter_requires_operator_toolset_verification():
    with pytest.raises(ValueError, match="explicit local verification"):
        HermesApiAdapter("http://127.0.0.1:8642", "test-key")


def test_hermes_retry_after_lost_response_reuses_same_idempotency_key(monkeypatch):
    opener = FakeOpener(lose_first_post_response=True)
    monkeypatch.setattr("bridge.adapters.hermes_api._opener", opener)
    adapter = HermesApiAdapter("http://127.0.0.1:8642", "test-key", toolsets_verified=True)
    args = {
        "thread_id": "b3d9dcc7-137c-481f-b268-2a40c7e20c88",
        "session_ref": None,
        "prompt": "safe task",
        "timeout": 2,
        "request_id": "cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c13",
    }

    with pytest.raises(HermesApiError, match="unavailable"):
        adapter.run(**args)
    result = adapter.run(**args)

    posts = [(req, timeout) for req, timeout in opener.requests if urllib.parse.urlsplit(req.full_url).path == "/v1/responses"]
    assert len(posts) == 2
    assert posts[0][0].get_header("Idempotency-key") == posts[1][0].get_header("Idempotency-key")
    assert len(opener.admissions) == 1
    assert result.text == "answer"


def test_hermes_does_not_post_if_toolset_check_crosses_retry_deadline(monkeypatch):
    opener = FakeOpener()
    monkeypatch.setattr("bridge.adapters.hermes_api._opener", opener)
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr("bridge.adapters.hermes_api.time.monotonic", lambda: next(ticks))
    adapter = HermesApiAdapter("http://127.0.0.1:8642", "test-key", toolsets_verified=True)

    with pytest.raises(HermesApiError, match="timed out"):
        adapter.run(
            thread_id="b3d9dcc7-137c-481f-b268-2a40c7e20c88",
            session_ref=None,
            prompt="safe task",
            timeout=1,
            request_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c16",
        )

    assert [urllib.parse.urlsplit(req.full_url).path for req, _ in opener.requests] == ["/v1/toolsets"]


def test_hermes_refuses_any_enabled_tools_before_submitting_peer_text(monkeypatch):
    opener = FakeOpener(enabled=True)
    monkeypatch.setattr("bridge.adapters.hermes_api._opener", opener)
    adapter = HermesApiAdapter("http://127.0.0.1:8642", "test-key", toolsets_verified=True)

    with pytest.raises(HermesApiError, match="toolsets"):
        adapter.run(
            thread_id="b3d9dcc7-137c-481f-b268-2a40c7e20c88",
            session_ref=None,
            prompt="untrusted peer request",
            timeout=1,
            request_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c14",
        )

    assert len(opener.requests) == 1


@pytest.mark.parametrize("url", [
    "http://192.168.1.2:8642",
    "http://hermes-host:8642",
    "https://example.com",
    "file:///tmp/hermes",
])
def test_hermes_api_must_use_loopback_url(url):
    with pytest.raises(ValueError, match="loopback"):
        HermesApiAdapter(url, "test-key", toolsets_verified=True)


def test_hermes_rejects_incomplete_responses(monkeypatch):
    opener = FakeOpener(final_status="in_progress")
    monkeypatch.setattr("bridge.adapters.hermes_api._opener", opener)
    adapter = HermesApiAdapter("http://127.0.0.1:8642", "test-key", toolsets_verified=True)

    with pytest.raises(HermesApiError, match="incomplete"):
        adapter.run(
            thread_id="b3d9dcc7-137c-481f-b268-2a40c7e20c88",
            session_ref=None,
            prompt="safe task",
            timeout=2,
            request_id="cbb2bf4c-2ae5-45f4-87b1-4b7c0e0c7c15",
        )
