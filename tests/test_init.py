"""Tests for the Rusty integration's entry setup/unload and coordinator."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyConnectionError
from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
DEVICE_ID = "abc123"
DEVICE_NAME = "Living Room Rusty"

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


async def test_setup_entry_success_creates_device(hass):
    """Setup succeeds with a mocked state and registers the device with configuration_url."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.LOADED

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_ID)})
    assert device is not None
    assert device.name == DEVICE_NAME
    assert device.sw_version == "2.3.0"
    assert device.configuration_url == f"http://{HOST}:{DEFAULT_PORT}/"

    assert entry.runtime_data.data == STATE_BODY
    assert entry.runtime_data.client is not None


async def test_setup_entry_device_offline_raises_not_ready(hass):
    """A device unreachable at setup leaves the entry in SETUP_RETRY (ConfigEntryNotReady)."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            side_effect=RustyConnectionError("cannot connect"),
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_malformed_payload_during_refresh_marks_update_failed(hass):
    """A malformed state payload during a later refresh fails the coordinator update."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data
    assert coordinator.last_update_success is True

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        # Drive a refresh directly: no platforms are loaded in this test (Task
        # 5 entities are what would normally register as coordinator
        # listeners and trigger the 5 s polling loop), so we exercise the
        # coordinator's own public refresh entrypoint instead of relying on
        # time-interval scheduling.
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    # The stale, last-good data is preserved rather than replaced with partial data.
    assert coordinator.data == STATE_BODY


async def test_unload_entry_cleans_up(hass):
    """Unloading the entry unloads its platforms, leaves it NOT_LOADED, and releases runtime_data."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED

        result = await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Per-entry state (the coordinator) is released, not just left dangling.
    assert not hasattr(entry, "runtime_data")


async def test_device_missing_id_fails_refresh_and_setup_retry(hass):
    """A `device` present but without an `id` fails the coordinator update and, at
    setup time, leaves the entry in SETUP_RETRY rather than crashing with a raw
    KeyError/TypeError.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    malformed_body = {**STATE_BODY, "device": {"name": DEVICE_NAME, "version": "2.3.0"}}

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=malformed_body,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_not_a_mapping_fails_refresh_and_setup_retry(hass):
    """A `device` value that isn't a mapping (e.g. a string) is handled the same
    way as a missing id: no raw TypeError, entry lands in SETUP_RETRY.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    malformed_body = {**STATE_BODY, "device": "not-a-mapping"}

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=malformed_body,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_device_missing_id_during_refresh_marks_update_failed(hass):
    """A `device` without an id arriving on a later refresh (not just at setup)
    also fails the coordinator update, without crashing.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_BODY,
        ),
        patch("custom_components.rusty.PLATFORMS", []),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.last_update_success is True

    malformed_body = {**STATE_BODY, "device": {"name": DEVICE_NAME}}
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=malformed_body,
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert coordinator.data == STATE_BODY
