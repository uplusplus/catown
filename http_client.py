from __future__ import annotations

import asyncio
from typing import Any

import httpx


class SyncASGITestClient:
    def __init__(
        self,
        app: Any,
        *,
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
        headers: dict[str, str] | None = None,
    ):
        self.app = app
        self.base_url = base_url
        self.raise_server_exceptions = raise_server_exceptions
        self.headers = {"X-Catown-Client": "test", **(headers or {})}

    def __enter__(self) -> "SyncASGITestClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return asyncio.run(self._request(method, url, **kwargs))

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = {**self.headers, **(kwargs.pop("headers", {}) or {})}
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url=self.base_url,
            follow_redirects=True,
        ) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)
