"""Small unauthenticated relay client for the private-network API."""

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
_PROJECT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LABEL = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_api_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "relay API must be an HTTP(S) URL without user info, query, or fragment"
        )
    if parsed.scheme == "http" and parsed.hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("plain HTTP is allowed only for loopback relay endpoints")
    return base_url.rstrip("/")


class RelayClient:
    def __init__(self, base_url: str, project: str, agent_id: str, timeout: float = 20):
        self.base_url = validate_api_url(base_url)
        if not isinstance(project, str) or not _PROJECT.fullmatch(project):
            raise ValueError("invalid project name")
        if not isinstance(agent_id, str) or not _LABEL.fullmatch(agent_id):
            raise ValueError("invalid agent name")
        self.project = project
        self.agent_id = agent_id
        self.timeout = timeout

    def _request(self, method: str, path: str, payload=None):
        data = (
            None
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RelayError(f"relay returned HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise RelayError(f"relay unavailable: {type(error).__name__}") from None

    def join(self):
        return self._request(
            "POST", "/v1/join", {"project": self.project, "agent": self.agent_id}
        )

    def projects(self):
        return self._request("GET", "/v1/projects")

    def participants(self):
        query = urllib.parse.urlencode({"project": self.project})
        return self._request("GET", "/v1/participants?" + query)

    def read(self, after: int, limit: int = 100):
        query = urllib.parse.urlencode(
            {"project": self.project, "after": after, "limit": limit}
        )
        return self._request("GET", "/v1/messages?" + query)

    def post(self, message: dict):
        query = urllib.parse.urlencode({"project": self.project})
        return self._request("POST", "/v1/messages?" + query, message)
