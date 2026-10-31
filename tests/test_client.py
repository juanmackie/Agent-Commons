import urllib.request

import pytest

from app.client import RelayClient, _NoProxyHandler, _NoRedirectHandler, _opener


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
