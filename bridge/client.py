"""Small synchronous relay client; bearer secret is held only in memory."""

import json
import urllib.error
import urllib.parse
import urllib.request


class RelayError(Exception):
    pass


class RelayClient:
    def __init__(self, base_url: str, room_id: str, timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.room_id = room_id
        self.timeout = timeout

    def _request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.room_id,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
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
        request = urllib.request.Request(base_url.rstrip("/") + "/v1/rooms", data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            raise RelayError(f"room creation failed: {type(error).__name__}") from None
