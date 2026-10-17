"""Small JSON-over-HTTP helper with an optional on-disk cache."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_TIMEOUT = 20


class ApiError(RuntimeError):
    pass


class JsonHttp:
    def __init__(
        self,
        cache_dir: str | Path | None = None,
        session: requests.Session | None = None,
        user_agent: str = "ffagent/0.1",
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.cache_dir = Path(os.path.expanduser(str(cache_dir))) if cache_dir else None

    def get(
        self,
        url: str,
        params: dict | list | None = None,
        headers: dict | None = None,
        cookies: dict | None = None,
        cache_ttl: float = 0,
    ) -> Any:
        """GET a URL and decode JSON. Responses are cached on disk for `cache_ttl` seconds."""
        cache_path = self._cache_path(url, params, headers) if cache_ttl and self.cache_dir else None
        if cache_path and cache_path.exists() and time.time() - cache_path.stat().st_mtime < cache_ttl:
            return json.loads(cache_path.read_text())

        try:
            resp = self.session.get(
                url, params=params, headers=headers, cookies=cookies, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise ApiError(f"GET {url} failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ApiError(
                f"GET {url} returned {resp.status_code}; private ESPN leagues need ESPN_S2 and ESPN_SWID"
            )
        if resp.status_code >= 400:
            raise ApiError(f"GET {url} returned {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ApiError(f"GET {url} did not return JSON") from exc

        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(data))
        return data

    def _cache_path(self, url: str, params: Any, headers: dict | None) -> Path:
        key = json.dumps([url, params, headers], sort_keys=True, default=str)
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")
