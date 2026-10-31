import urllib.request

import pytest

from bridge.client import RelayClient, _NoProxyHandler, _NoRedirectHandler, _opener


def test_client_does_not_follow_redirects_or_ambient_proxies_with_bearer_capability(monkeypatch):
    assert _NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://other.example") is None
    assert not any(isinstance(handler, urllib.request.ProxyHandler) for handler in _opener.handlers)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://proxy.invalid"})
    constructed = urllib.request.build_opener(_NoProxyHandler, _NoRedirectHandler)
    assert not any(isinstance(handler, urllib.request.ProxyHandler) for handler in constructed.handlers)


def test_client_rejects_cleartext_remote_capability_transport():
    capability = "A" * 43
    with pytest.raises(ValueError, match="loopback"):
        RelayClient("http://relay.tailnet", capability)
    with pytest.raises(ValueError, match="URL-safe token"):
        RelayClient("https://relay.tailnet", "not-a-capability")
    assert RelayClient("http://127.0.0.1:8080", capability).base_url == "http://127.0.0.1:8080"
