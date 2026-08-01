"""Tests for the Rusty aiohttp API client."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.rusty.api import (
    RustyApiError,
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


@pytest.fixture
def mock_aioresponse():
    with aioresponses() as m:
        yield m


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
    key = ("POST", aiohttp.client.URL(f"{BASE_URL}/api/screen"))
    request = mock_aioresponse.requests[key][-1]
    assert request.kwargs["json"] == {"on": True, "brightness": 55}


async def test_async_set_screen_off_omits_brightness(mock_aioresponse):
    """set_screen(False) omits the brightness key entirely."""
    mock_aioresponse.post(f"{BASE_URL}/api/screen", payload=STATE_BODY, status=200)

    async with aiohttp.ClientSession() as session:
        client = RustyClient(HOST, session)
        result = await client.async_set_screen(False)

    assert result == STATE_BODY
    key = ("POST", aiohttp.client.URL(f"{BASE_URL}/api/screen"))
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
        with pytest.raises(RustyVolumeFixedError):
            await client.async_set_volume(50)


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
