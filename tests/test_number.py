"""Tests for the Rusty number platform (volume control)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyVolumeFixedError
from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
DEVICE_ID = "abc123"
DEVICE_NAME = "Rusty"
ENTITY_ID = "number.rusty_volume"

STATE_BODY = {
    "device": {"id": DEVICE_ID, "name": DEVICE_NAME, "version": "2.3.0"},
    "screen": {"on": True, "brightness": 80, "mode": "auto", "writable": True, "available": True},
    "volume": {"value": 42, "fixed": False},
    "playing": {"spotify": True, "dlna": False},
    "slideshow": {"enabled": False},
}


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT},
        title=DEVICE_NAME,
    )


async def _setup(hass, state_body: dict = STATE_BODY) -> MockConfigEntry:
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=state_body,
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_entity_id_is_rusty_volume(hass):
    """The volume number entity registers as number.rusty_volume."""
    await _setup(hass)

    assert hass.states.get(ENTITY_ID) is not None


async def test_state_reflects_volume_value(hass):
    """native value comes from volume.value."""
    await _setup(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "42"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 100
    assert state.attributes["step"] == 1
    assert state.attributes["mode"] == "slider"
    assert state.attributes["icon"] == "mdi:volume-high"


async def test_set_value_calls_set_volume(hass):
    """Setting the number calls async_set_volume with the requested value."""
    await _setup(hass)

    result_body = {**STATE_BODY, "volume": {"value": 70, "fixed": False}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_volume",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_set_volume,
        # The post-command async_request_refresh() also polls; keep it
        # answering so the coordinator's refresh succeeds cleanly.
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=result_body,
        ),
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_ID, "value": 70},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_set_volume.assert_awaited_once_with(70)


async def test_set_value_result_applies_as_snapshot_immediately(hass):
    """The state returned by the command becomes the entity's state immediately."""
    await _setup(hass)

    result_body = {**STATE_BODY, "volume": {"value": 55, "fixed": False}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_volume",
            new_callable=AsyncMock,
            return_value=result_body,
        ),
        # No-op the post-command refresh so this test isolates the
        # immediate snapshot-apply from any subsequent poll.
        patch(
            "custom_components.rusty.coordinator.RustyCoordinator.async_request_refresh",
            new_callable=AsyncMock,
        ),
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_ID, "value": 70},
            blocking=True,
        )
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "55"


async def test_unavailable_when_volume_fixed(hass):
    """The number entity is unavailable when the device reports volume.fixed."""
    body = {**STATE_BODY, "volume": {"value": 42, "fixed": True}}
    await _setup(hass, body)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "unavailable"


async def test_unavailable_when_coordinator_update_fails(hass):
    """Never mark the number available on a coordinator parse/update failure
    (availability must come from the coordinator's success state, not just
    `volume.fixed`)."""
    entry = await _setup(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "unavailable"


async def test_volume_fixed_error_raises_homeassistant_error(hass):
    """RustyVolumeFixedError from a set_volume call surfaces as HomeAssistantError."""
    await _setup(hass)

    with patch(
        "custom_components.rusty.api.RustyClient.async_set_volume",
        new_callable=AsyncMock,
        side_effect=RustyVolumeFixedError("volume is fixed"),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": ENTITY_ID, "value": 70},
                blocking=True,
            )
