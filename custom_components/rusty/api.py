"""Thin aiohttp client for the Rusty device's local HTTP API.

Pure HTTP transport + error translation only. No Home Assistant imports.
"""
from __future__ import annotations

import asyncio
from json import loads as json_loads
from typing import Any

import aiohttp

from .const import DEFAULT_PORT

_TIMEOUT_SECONDS = 10

# Sentinel for "the body was not valid JSON at all", so that a literal `null`
# body (which decodes to None perfectly well) is not confused with it.
_NOT_JSON = object()


class RustyApiError(Exception):
    """Base error for any failure talking to the Rusty device's API.

    `status` carries the HTTP status when the failure came from a response
    (None for transport-level failures), so callers that care about a specific
    status can translate it into a domain-specific error themselves.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialize with a human-readable message and optional HTTP status."""
        super().__init__(message)
        self.status = status


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
        try:
            return await self._request("POST", "/api/volume", json={"value": value})
        except RustyApiError as err:
            # 409 means "volume is fixed" *on this endpoint only* - the generic
            # transport deliberately stays status-agnostic.
            if err.status == 409:
                raise RustyVolumeFixedError(str(err), status=err.status) from err
            raise

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
                    status = resp.status
                    body_text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise RustyConnectionError(str(err)) from err

        # The device always sends JSON, but never trust it: decode once, by
        # hand, so a wrong/absent Content-Type header can't change the outcome.
        try:
            data: Any = json_loads(body_text)
        except ValueError:
            data = _NOT_JSON

        if status >= 400:
            raise RustyApiError(_error_message(status, data, body_text), status=status)

        if data is _NOT_JSON:
            raise RustyApiError(f"Invalid JSON response from {url}: {body_text!r}")
        if not isinstance(data, dict):
            raise RustyApiError(
                f"Expected a JSON object from {url}, got {type(data).__name__}: "
                f"{body_text!r}"
            )
        return data


def _error_message(status: int, data: Any, body_text: str) -> str:
    """Build the message for an HTTP error, unwrapping `{"error": "..."}` bodies."""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, str) and error:
            return error
    if body_text.strip():
        return body_text
    return f"HTTP {status}"
