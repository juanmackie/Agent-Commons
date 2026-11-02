import urllib.request

import pytest

from app.client import (
    RelayClient,
    RelayError,
    _NoProxyHandler,
    _NoRedirectHandler,
    _opener,
)


def test_client_does_not_follow_redirects_or_ambient_proxies():
    assert (
        _NoRedirectHandler().redirect_request(
            None, None, 302, "Found", {}, "https://other.example"
        )
        is None
    )
    assert not any(
        isinstance(handler, urllib.request.ProxyHandler) for handler in _opener.handlers
    )
    constructed = urllib.request.build_opener(_NoProxyHandler, _NoRedirectHandler)
    assert not any(
        isinstance(handler, urllib.request.ProxyHandler)
        for handler in constructed.handlers
    )


def test_client_requires_https_for_remote_relay_and_valid_project_address():
    with pytest.raises(ValueError, match="loopback"):
        RelayClient("http://relay.tailnet", "demo", "pi")
    with pytest.raises(ValueError, match="project"):
        RelayClient("https://relay.tailnet", "Bad Project", "pi")
    assert (
        RelayClient("http://127.0.0.1:8080", "demo", "pi").base_url
        == "http://127.0.0.1:8080"
    )


def test_read_before_uses_exact_sequence_boundary_and_limit():
    client = RelayClient("https://relay.tailnet", "demo", "pi")
    calls = []
    client._request = lambda method, path: calls.append((method, path)) or {"messages": []}
    assert client.read_before(13, limit=1) == {"messages": []}
    assert calls == [("GET", "/v1/messages?project=demo&before=13&limit=1")]


def test_read_can_mark_an_agent_as_a_listener_without_changing_manual_reads():
    client = RelayClient("https://relay.tailnet", "demo", "pi")
    calls = []
    client._request = lambda method, path: calls.append((method, path)) or {"messages": []}
    assert client.read(after=5, listener="pi") == {"messages": []}
    assert client.read(after=5) == {"messages": []}
    assert calls == [
        ("GET", "/v1/messages?project=demo&after=5&limit=100&listener=pi"),
        ("GET", "/v1/messages?project=demo&after=5&limit=100"),
    ]


def test_setup_client_cannot_message_without_a_project():
    client = RelayClient("https://relay.tailnet", None, "pi")
    with pytest.raises(RelayError, match="create or join a project first"):
        client.read(after=0)
