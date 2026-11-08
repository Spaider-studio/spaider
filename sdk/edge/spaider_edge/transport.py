"""HTTP for sync, using only the standard library."""
from __future__ import annotations

import json
import urllib.request
from typing import Optional


class HttpTransport:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 10.0) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be http or https")
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    def _request(self, method: str, path: str, body: Optional[dict]) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 — scheme checked in __init__
            self._base + path,
            data=data,
            method=method,
            headers={"X-Api-Key": self._key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def get(self, path: str) -> dict:
        return self._request("GET", path, None)
