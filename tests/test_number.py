"""Tests for the Rusty number platform (volume control)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyConnectionError, RustyVolumeFixedError
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
        # Guard: a command must not trigger any GET /api/state of its own.
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_state,
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_ID, "value": 70},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_set_volume.assert_awaited_once_with(70)
    assert mock_state.await_count == 0


async def test_set_value_result_applies_as_snapshot_immediately(hass):
    """The state returned by the command becomes the entity's state immediately,
    and survives - no follow-up poll overwrites it with the pre-command value.
    """
    await _setup(hass)

    result_body = {**STATE_BODY, "volume": {"value": 55, "fixed": False}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_volume",
            new_callable=AsyncMock,
            return_value=result_body,
        ),
        # If the command path polled again, it would read the pre-command
        # value (42) straight back over the snapshot.
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            new_callable=AsyncMock,
            return_value=STATE_BODY,
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
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": ENTITY_ID, "value": 70},
                blocking=True,
            )

    assert "fixed" in str(excinfo.value)


async def test_connection_error_during_command_raises_homeassistant_error(hass):
    """A non-volume-fixed API failure also surfaces as HomeAssistantError, so the
    UI shows a real message instead of "Unknown error" plus a traceback.
    """
    await _setup(hass)

    with patch(
        "custom_components.rusty.api.RustyClient.async_set_volume",
        new_callable=AsyncMock,
        side_effect=RustyConnectionError("cannot connect"),
    ):
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": ENTITY_ID, "value": 70},
                blocking=True,
            )

    assert "cannot connect" in str(excinfo.value)


async def test_malformed_command_response_does_not_poison_the_coordinator(hass):
    """A volume command answering with a partial body leaves the last good data
    (and last_update_success) alone rather than writing an unparseable payload.
    """
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_volume",
            new_callable=AsyncMock,
            return_value={"device": {"id": DEVICE_ID}, "volume": {"value": 70}},
        ),
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            new_callable=AsyncMock,
            return_value=STATE_BODY,
        ),
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_ID, "value": 70},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert coordinator.data == STATE_BODY
    assert coordinator.last_update_success is True
    assert hass.states.get(ENTITY_ID).state == "42"


async def test_malformed_command_response_falls_back_to_a_refresh(hass):
    """Rather than adopting an unusable body, the coordinator asks for a poll."""
    entry = await _setup(hass)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_volume",
            new_callable=AsyncMock,
            return_value={"nope": True},
        ),
        patch(
            "custom_components.rusty.coordinator.RustyCoordinator.async_request_refresh",
            new_callable=AsyncMock,
        ) as mock_refresh,
    ):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_ID, "value": 70},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_refresh.assert_awaited_once()
    assert entry.runtime_data.data == STATE_BODY
