"""Tests for the Rusty aiohttp API client."""
from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.rusty.api import (
    RustyApiError,
    RustyAuthError,
    RustyClient,
    RustyConnectionError,
    RustyVolumeFixedError,
)
from custom_components.rusty.const import DEFAULT_PORT

HOST = "192.168.1.50"
BASE_URL = f"http://{HOST}:{DEFAULT_PORT}"

STATE_BODY = {
    "device": {"id": "abc123", "name": "Living Room Rusty", "version": "2.3.0"},
    "screen": {"on": True, "brightness": 80, "mode": "auto", "writable": True, "available": True},
    "volume": {"value": 42, "fixed": False},
    "playing": {"spotify": True, "dlna": False},
    "slideshow": {"enabled": False},
}

# ---------------------------------------------------------------------------
# Minimal aiohttp mock (replaces aioresponses for aiohttp 3.14+ compatibility)
# ---------------------------------------------------------------------------

class _MockRequest:
    """Recorded request for post-call inspection."""
    def __init__(self, method: str, url: str, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self._method = method
        self._url = url


class _FakeResponse:
    """Minimal async-context-manager response compatible with api.py's usage."""
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False


class MockAiohttp:
    """Lightweight aioresponses replacement; responses registered per (METHOD, url)."""

    def __init__(self) -> None:
        self._responses: dict[tuple[str, str], list] = {}
        self.requests: dict[tuple[str, str], list[_MockRequest]] = {}
        self._patcher = None

    def _register(
        self,
        method: str,
        url: str,
        *,
        payload: dict | None = None,
        body: str | None = None,
        status: int = 200,
        content_type: str = "application/json",
        exception: BaseException | None = None,
    ) -> None:
        key = (method.upper(), url)
        if exception is not None:
            entry = ("exc", exception)
        else:
            raw = _json.dumps(payload) if payload is not None else (body or "")
            entry = ("resp", _FakeResponse(status, raw))
        self._responses.setdefault(key, []).append(entry)

    def get(self, url: str, **kw: Any) -> None:
        self._register("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> None:
        self._register("POST", url, **kw)

    def __enter__(self) -> "MockAiohttp":
        def _fake_request(method: str, url: Any, **kwargs: Any) -> Any:
            url_str = str(url)
            key = (method.upper(), url_str)
            rec = _MockRequest(method, url_str, **kwargs)
            self.requests.setdefault(key, []).append(rec)

            entries = self._responses.get(key, [])
            if not entries:
                raise RuntimeError(f"No mock response for {method} {url_str}")
            kind, value = entries.pop(0)
            if not entries:  # keep last response in place (aioresponses behaviour)
                self._responses[key] = [(kind, value)]

            if kind == "exc":
                raise value  # type: ignore[misc]
            return value

        self._patcher = patch.object(
            aiohttp.ClientSession, "request", side_effect=_fake_request
        )
        self._patcher.start()
        return self

    def __exit__(self, *_: Any) -> None:
        if self._patcher:
            self._patcher.stop()


@pytest.fixture
def mock_aioresponse():
    with MockAiohttp() as m:
        yield m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_async_state_round_trip(mock_aioresponse):
    """GET /api/state returns the parsed JSON body as a dict."""
    mock_aioresponse.get(f"{BASE_URL}/api/state", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        result = await client.async_state()

    assert result == STATE_BODY


async def test_async_set_screen_on_posts_on_and_brightness(mock_aioresponse):
    """set_screen(True, 55) posts {"on": true, "brightness": 55} and returns parsed body."""
    mock_aioresponse.post(f"{BASE_URL}/api/screen", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        result = await client.async_set_screen(True, 55)

    assert result == STATE_BODY
    key = ("POST", f"{BASE_URL}/api/screen")
    request = mock_aioresponse.requests[key][-1]
    assert request.kwargs["json"] == {"on": True, "brightness": 55}


async def test_async_set_screen_off_omits_brightness(mock_aioresponse):
    """set_screen(False) omits the brightness key entirely."""
    mock_aioresponse.post(f"{BASE_URL}/api/screen", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        result = await client.async_set_screen(False)

    assert result == STATE_BODY
    key = ("POST", f"{BASE_URL}/api/screen")
    request = mock_aioresponse.requests[key][-1]
    assert request.kwargs["json"] == {"on": False}
    assert "brightness" not in request.kwargs["json"]


async def test_async_set_volume_409_raises_volume_fixed_error(mock_aioresponse):
    """A 409 response from /api/volume raises RustyVolumeFixedError."""
    mock_aioresponse.post(
        f"{BASE_URL}/api/volume",
        payload={"error": "volume is fixed"},
        status=409,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyVolumeFixedError) as excinfo:
            await client.async_set_volume(50)

    assert str(excinfo.value) == "volume is fixed"


async def test_screen_409_is_not_a_volume_fixed_error(mock_aioresponse):
    """A 409 from /api/screen is a plain RustyApiError - never "volume is fixed"."""
    mock_aioresponse.post(
        f"{BASE_URL}/api/screen",
        payload={"error": "screen is busy"},
        status=409,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError) as excinfo:
            await client.async_set_screen(True)

    assert not isinstance(excinfo.value, RustyVolumeFixedError)
    assert excinfo.value.status == 409
    assert str(excinfo.value) == "screen is busy"


async def test_state_409_is_not_a_volume_fixed_error(mock_aioresponse):
    """A 409 from the GET /api/state poll is a plain RustyApiError too."""
    mock_aioresponse.get(f"{BASE_URL}/api/state", body="conflict", status=409)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError) as excinfo:
            await client.async_state()

    assert not isinstance(excinfo.value, RustyVolumeFixedError)
    assert excinfo.value.status == 409


async def test_error_envelope_is_unwrapped(mock_aioresponse):
    """An `{"error": "..."}` body surfaces as just its message, not the raw JSON."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state", payload={"error": "screen unavailable"}, status=500
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError) as excinfo:
            await client.async_state()

    assert str(excinfo.value) == "screen unavailable"
    assert excinfo.value.status == 500


async def test_error_without_envelope_falls_back_to_body_text(mock_aioresponse):
    """A non-JSON error body is surfaced verbatim."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state", body="Internal Server Error", status=500
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError) as excinfo:
            await client.async_state()

    assert "Internal Server Error" in str(excinfo.value)


@pytest.mark.parametrize("body", ["[]", '"ok"', "null", "3"])
async def test_valid_json_that_is_not_an_object_raises_api_error(mock_aioresponse, body):
    """A 200 body that is valid JSON but not an object never reaches callers."""
    mock_aioresponse.get(f"{BASE_URL}/api/state", body=body, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError):
            await client.async_state()


async def test_json_body_with_wrong_content_type_still_parses(mock_aioresponse):
    """The body is decoded by hand, so a text/plain Content-Type is no obstacle."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state",
        body='{"device": {"id": "abc123"}}',
        status=200,
        content_type="text/plain",
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        result = await client.async_state()

    assert result == {"device": {"id": "abc123"}}


async def test_async_state_connect_error_raises_connection_error(mock_aioresponse):
    """A connection error while reaching the device raises RustyConnectionError."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state",
        exception=aiohttp.ClientConnectionError("cannot connect"),
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyConnectionError):
            await client.async_state()


async def test_async_state_timeout_raises_connection_error(mock_aioresponse):
    """A timeout while reaching the device raises RustyConnectionError."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state",
        exception=TimeoutError("timed out"),
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyConnectionError):
            await client.async_state()


async def test_async_state_non_json_body_raises_api_error(mock_aioresponse):
    """A malformed/non-JSON response body raises RustyApiError, never a partial dict."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state",
        body="not json at all",
        status=200,
        content_type="text/plain",
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError):
            await client.async_state()


async def test_auth_error_on_401(mock_aioresponse):
    """A 401 response raises RustyAuthError (a subclass of RustyApiError)."""
    mock_aioresponse.get(
        f"{BASE_URL}/api/state",
        payload={"error": "password required"},
        status=401,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyAuthError) as excinfo:
            await client.async_state()

    assert excinfo.value.status == 401
    assert str(excinfo.value) == "password required"


async def test_auth_error_is_api_error_subclass(mock_aioresponse):
    """RustyAuthError is a subclass of RustyApiError so callers can catch both."""
    mock_aioresponse.get(f"{BASE_URL}/api/state", body="", status=401)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError):
            await client.async_state()


async def test_bearer_token_sent_when_password_set(mock_aioresponse):
    """When a password is configured, every request carries Authorization: Bearer ***"""
    mock_aioresponse.get(f"{BASE_URL}/api/state", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session, password="s3cr3t")
        await client.async_state()

    key = ("GET", f"{BASE_URL}/api/state")
    req = mock_aioresponse.requests[key][-1]
    assert req.kwargs.get("headers", {}).get("Authorization") == "Bearer s3cr3t"


async def test_no_auth_header_when_no_password(mock_aioresponse):
    """Without a password, no Authorization header is sent."""
    mock_aioresponse.get(f"{BASE_URL}/api/state", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        await client.async_state()

    key = ("GET", f"{BASE_URL}/api/state")
    req = mock_aioresponse.requests[key][-1]
    assert "Authorization" not in (req.kwargs.get("headers") or {})


async def test_announce_accepts_202(mock_aioresponse):
    """async_announce succeeds on a 202 Accepted response (does not raise)."""
    mock_aioresponse.post(
        f"{BASE_URL}/api/announce/text",
        body='{"status":"playing"}',
        status=202,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        await client.async_announce("hello")  # should not raise


async def test_announce_posts_text(mock_aioresponse):
    """async_announce posts the text field in the JSON body."""
    mock_aioresponse.post(
        f"{BASE_URL}/api/announce/text",
        body='{"status":"playing"}',
        status=202,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        await client.async_announce("say this")

    key = ("POST", f"{BASE_URL}/api/announce/text")
    req = mock_aioresponse.requests[key][-1]
    assert req.kwargs["json"] == {"text": "say this"}


async def test_announce_raises_on_503(mock_aioresponse):
    """A 503 from /api/announce/text (TTS unavailable) raises RustyApiError."""
    mock_aioresponse.post(
        f"{BASE_URL}/api/announce/text",
        payload={"error": "text-to-speech is not available on the device"},
        status=503,
    )

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        with pytest.raises(RustyApiError) as excinfo:
            await client.async_announce("hello")

    assert excinfo.value.status == 503
