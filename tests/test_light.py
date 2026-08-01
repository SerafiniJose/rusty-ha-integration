"""Tests for the Rusty light platform (screen power + brightness)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
DEVICE_ID = "abc123"
DEVICE_NAME = "Rusty"
ENTITY_ID = "light.rusty_screen"

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


async def test_entity_id_is_rusty_screen(hass):
    """The screen light entity registers as light.rusty_screen."""
    await _setup(hass)

    assert hass.states.get(ENTITY_ID) is not None


async def test_state_reflects_screen_on_and_brightness(hass):
    """is_on and brightness (device 1-100 -> HA 0-255) come from screen state."""
    await _setup(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    # device brightness 80 -> round(80 * 255 / 100) = 204
    assert state.attributes["brightness"] == 204


async def test_brightness_rounding_lower_edge(hass):
    """Device brightness 1 rounds up to HA brightness 3."""
    body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "brightness": 1}}
    await _setup(hass, body)

    state = hass.states.get(ENTITY_ID)
    assert state.attributes["brightness"] == 3


async def test_brightness_rounding_upper_edge(hass):
    """Device brightness 100 maps to full HA brightness 255."""
    body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "brightness": 100}}
    await _setup(hass, body)

    state = hass.states.get(ENTITY_ID)
    assert state.attributes["brightness"] == 255


async def test_screen_off_state(hass):
    """screen.on = False maps to STATE_OFF."""
    body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "on": False}}
    await _setup(hass, body)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF


async def test_turn_on_without_brightness_calls_set_screen_true_none(hass):
    """turn_on() with no brightness posts async_set_screen(True, None) exactly once."""
    entry = await _setup(hass)

    result_body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "on": True}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_set_screen,
        # The post-command async_request_refresh() also polls; keep it
        # answering so the coordinator's refresh succeeds cleanly.
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=result_body,
        ),
    ):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
        )
        await hass.async_block_till_done()

    mock_set_screen.assert_awaited_once_with(True, None)
    assert entry.runtime_data.data == result_body


async def test_turn_on_with_brightness_converts_and_clamps(hass):
    """turn_on(brightness=128) converts to device scale in ONE call."""
    await _setup(hass)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
            new_callable=AsyncMock,
            return_value=STATE_BODY,
        ) as mock_set_screen,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ENTITY_ID, "brightness": 128},
            blocking=True,
        )
        await hass.async_block_till_done()

    # round(128 * 100 / 255) = 50
    mock_set_screen.assert_awaited_once_with(True, 50)


async def test_turn_on_with_tiny_brightness_clamps_to_one(hass):
    """A tiny non-zero HA brightness (1) maps to device brightness 1, not 0."""
    await _setup(hass)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
            new_callable=AsyncMock,
            return_value=STATE_BODY,
        ) as mock_set_screen,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ENTITY_ID, "brightness": 1},
            blocking=True,
        )
        await hass.async_block_till_done()

    # round(1 * 100 / 255) = 0, clamped up to 1
    mock_set_screen.assert_awaited_once_with(True, 1)


async def test_turn_on_with_max_brightness_clamps_to_hundred(hass):
    """HA brightness 255 maps to device brightness 100 (upper clamp is a no-op here)."""
    await _setup(hass)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
            new_callable=AsyncMock,
            return_value=STATE_BODY,
        ) as mock_set_screen,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
    ):
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ENTITY_ID, "brightness": 255},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_set_screen.assert_awaited_once_with(True, 100)


async def test_turn_off_calls_set_screen_false(hass):
    """turn_off() posts async_set_screen(False) exactly once."""
    await _setup(hass)

    result_body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "on": False}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_set_screen,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=result_body,
        ),
    ):
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
        )
        await hass.async_block_till_done()

    mock_set_screen.assert_awaited_once_with(False)


async def test_command_result_applies_as_snapshot_immediately(hass):
    """The state returned by the command becomes the entity's state immediately,
    without waiting for the next poll.
    """
    await _setup(hass)

    # Device reports a different brightness than what we asked for (device-side
    # clamping, dimming curve, etc.) - the returned snapshot is truth.
    result_body = {**STATE_BODY, "screen": {**STATE_BODY["screen"], "on": True, "brightness": 33}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_screen",
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
            "light",
            "turn_on",
            {"entity_id": ENTITY_ID, "brightness": 200},
            blocking=True,
        )
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    # round(33 * 255 / 100) = 84
    assert state.attributes["brightness"] == 84


async def test_unavailable_when_coordinator_update_fails(hass):
    """Never mark the light available on a coordinator parse/update failure."""
    entry = await _setup(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "unavailable"
