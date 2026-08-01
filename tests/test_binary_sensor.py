"""Tests for the Rusty binary_sensor platform (spotify/dlna playing state)."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
DEVICE_ID = "abc123"
DEVICE_NAME = "Rusty"
SPOTIFY_ENTITY_ID = "binary_sensor.rusty_spotify_playing"
DLNA_ENTITY_ID = "binary_sensor.rusty_dlna_playing"

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


async def test_entity_ids(hass):
    """Both binary sensors register with the expected entity_ids."""
    await _setup(hass)

    assert hass.states.get(SPOTIFY_ENTITY_ID) is not None
    assert hass.states.get(DLNA_ENTITY_ID) is not None


async def test_initial_state_from_playing(hass):
    """Initial on/off state reflects playing.spotify / playing.dlna."""
    await _setup(hass)

    assert hass.states.get(SPOTIFY_ENTITY_ID).state == STATE_ON
    assert hass.states.get(DLNA_ENTITY_ID).state == STATE_OFF


async def test_icons_and_no_device_class(hass):
    """Icons are mdi:spotify / mdi:cast-audio and no device_class attribute is set."""
    await _setup(hass)

    spotify_state = hass.states.get(SPOTIFY_ENTITY_ID)
    dlna_state = hass.states.get(DLNA_ENTITY_ID)

    assert spotify_state.attributes["icon"] == "mdi:spotify"
    assert dlna_state.attributes["icon"] == "mdi:cast-audio"
    assert "device_class" not in spotify_state.attributes
    assert "device_class" not in dlna_state.attributes


async def test_sensors_flip_on_coordinator_update(hass):
    """A subsequent coordinator refresh with different playing values flips both sensors."""
    entry = await _setup(hass)

    new_body = {**STATE_BODY, "playing": {"spotify": False, "dlna": True}}
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=new_body,
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(SPOTIFY_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(DLNA_ENTITY_ID).state == STATE_ON


async def test_unavailable_when_coordinator_update_fails(hass):
    """Never mark binary sensors available on a coordinator parse/update failure."""
    entry = await _setup(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(SPOTIFY_ENTITY_ID).state == "unavailable"
    assert hass.states.get(DLNA_ENTITY_ID).state == "unavailable"
