"""Small synchronous relay client; bearer secret is held only in memory."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request


class RelayError(Exception):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class _NoProxyHandler(urllib.request.ProxyHandler):
    def __init__(self):
        super().__init__({})


_opener = urllib.request.build_opener(_NoProxyHandler, _NoRedirectHandler)


def validate_api_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("relay API must be an HTTP(S) URL without user info, query, or fragment")
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("plain HTTP is allowed only for loopback relay endpoints")
    return base_url.rstrip("/")


class RelayClient:
    def __init__(self, base_url: str, room_id: str, timeout: float = 20):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", room_id):
            raise ValueError("room capability must be a 256-bit URL-safe token")
        self.base_url = validate_api_url(base_url)
        self.room_id = room_id
        self.timeout = timeout

    def _request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.room_id,
            "Content-Type": "application/json",
        })
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RelayError(f"relay returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise RelayError(f"relay unavailable: {type(error).__name__}") from None

    def read(self, after: int, limit: int = 100):
        query = urllib.parse.urlencode({"after": after, "limit": limit})
        return self._request("GET", "/v1/messages?" + query)

    def post(self, message: dict):
        return self._request("POST", "/v1/messages", message)

    @classmethod
    def create_room(cls, base_url: str, timeout: float = 20):
        request = urllib.request.Request(validate_api_url(base_url) + "/v1/rooms", data=b"", method="POST")
        try:
            with _opener.open(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RelayError(f"room creation failed: {type(error).__name__}") from None
