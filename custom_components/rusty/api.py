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


class RustyAuthError(RustyApiError):
    """Raised when the device responds 401 Unauthorized (wrong or missing password)."""


class RustyVolumeFixedError(RustyApiError):
    """Raised when a volume write is rejected (HTTP 409) because volume is fixed."""


class RustyClient:
    """Client for the Rusty device's local HTTP API."""

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        port: int = DEFAULT_PORT,
        password: str | None = None,
    ) -> None:
        self._host = host
        self._session = session
        self._port = port
        self._password = password
        self._base_url = f"http://{host}:{port}"

    @property
    def host(self) -> str:
        """The device host this client talks to (for log messages)."""
        return self._host

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

    async def async_set_panel(self, panel_id: str) -> dict[str, Any]:
        """Navigate to a panel via POST /api/panel.

        Returns the resulting full state as a dict.
        Raises RustyApiError (status 409) when the app is not in the foreground.
        Call async_set_foreground(True) first if the app may be in background.
        """
        return await self._request("POST", "/api/panel", json={"id": panel_id})

    async def async_set_dashboard(self, path: str) -> dict[str, Any]:
        """Navigate to an HA dashboard via POST /api/home_assistant/dashboard.

        `path` is the url_path of a dashboard on the device's chip bar.
        Returns the resulting full state as a dict.
        Raises RustyApiError (status 409) when the app is not in the foreground,
        or (status 400) when the path is not on the chip bar,
        or (status 404) when the Home Assistant feature is switched off.
        """
        return await self._request(
            "POST", "/api/home_assistant/dashboard", json={"path": path}
        )

    async def async_set_foreground(self, on: bool) -> dict[str, Any]:
        """Bring the Rusty app to the foreground (or send it to background).

        POST /api/foreground {"on": true/false}.
        Returns the resulting full state as a dict.
        Raises RustyApiError on failure.
        """
        return await self._request("POST", "/api/foreground", json={"on": on})

    async def async_announce(self, text: str) -> None:
        """Speak [text] on the device via POST /api/announce/text.

        Returns when the request is accepted (202); the actual playback is async.
        Raises RustyApiError on any failure (including 503 when TTS is unavailable).
        """
        # 202 Accepted is a success status; _request only raises on >= 400, so
        # we need to handle 202 the same way as 200 — just drop the body.
        await self._request("POST", "/api/announce/text", json={"text": text}, accept_202=True)

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        accept_202: bool = False,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}
        if self._password is not None:
            headers["Authorization"] = f"Bearer {self._password}"
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with self._session.request(
                    method, url, json=json, headers=headers
                ) as resp:
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

        if status == 401:
            raise RustyAuthError(_error_message(status, data, body_text), status=status)

        if status >= 400:
            raise RustyApiError(_error_message(status, data, body_text), status=status)

        # 202 Accepted is a valid success for announce; return empty dict.
        if accept_202 and status == 202:
            return {}

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
