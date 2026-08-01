"""Thin aiohttp client for the Rusty device's local HTTP API.

Pure HTTP transport + error translation only. No Home Assistant imports.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import DEFAULT_PORT

_TIMEOUT_SECONDS = 10


class RustyApiError(Exception):
    """Base error for any failure talking to the Rusty device's API."""


class RustyConnectionError(RustyApiError):
    """Raised when the Rusty device cannot be reached (connect failure or timeout)."""


class RustyVolumeFixedError(RustyApiError):
    """Raised when a volume write is rejected (HTTP 409) because volume is fixed."""


class RustyClient:
    """Client for the Rusty device's local, unauthenticated HTTP API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._session = session
        self._port = port
        self._base_url = f"http://{host}:{port}"

    async def async_state(self) -> dict[str, Any]:
        """Fetch the device's current state via GET /api/state."""
        return await self._request("GET", "/api/state")

    async def async_set_screen(self, on: bool, brightness: int | None = None) -> dict[str, Any]:
        """Set screen power (and optionally brightness) via POST /api/screen.

        Returns the resulting full state as a dict.
        """
        payload: dict[str, Any] = {"on": on}
        if brightness is not None:
            payload["brightness"] = brightness
        return await self._request("POST", "/api/screen", json=payload)

    async def async_set_volume(self, value: int) -> dict[str, Any]:
        """Set the volume via POST /api/volume.

        Returns the resulting full state as a dict.
        Raises RustyVolumeFixedError if the device reports volume is fixed (HTTP 409).
        """
        return await self._request("POST", "/api/volume", json={"value": value})

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with self._session.request(method, url, json=json) as resp:
                    body_text = await resp.text()
                    if resp.status == 409:
                        raise RustyVolumeFixedError(body_text)
                    if resp.status >= 400:
                        raise RustyApiError(body_text)
                    try:
                        return await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError) as err:
                        raise RustyApiError(
                            f"Invalid JSON response from {url}: {body_text!r}"
                        ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise RustyConnectionError(str(err)) from err
