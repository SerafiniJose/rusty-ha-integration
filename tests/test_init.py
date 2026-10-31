"""Tests for the Rusty integration's entry setup/unload and coordinator."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyAuthError, RustyConnectionError
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

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
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

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        side_effect=RustyConnectionError("cannot connect"),
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_malformed_payload_during_refresh_marks_update_failed(hass):
    """A malformed state payload during a later refresh fails the coordinator update."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
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
        # Drive a refresh directly rather than relying on time-interval
        # scheduling for the 5 s polling loop.
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    # The stale, last-good data is preserved rather than replaced with partial data.
    assert coordinator.data == STATE_BODY


async def test_unload_entry_cleans_up(hass):
    """Unloading the entry unloads its platforms, leaves it NOT_LOADED, and releases runtime_data."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
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

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=malformed_body,
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

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=malformed_body,
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize(
    ("platform", "unique_id"),
    [
        ("light", f"{DEVICE_ID}_screen"),
        ("number", f"{DEVICE_ID}_volume"),
        ("binary_sensor", f"{DEVICE_ID}_spotify_playing"),
        ("binary_sensor", f"{DEVICE_ID}_dlna_playing"),
    ],
)
async def test_entity_unique_ids_are_stable(hass, platform, unique_id):
    """Every entity keeps its device-derived unique_id.

    A silent unique_id change permanently orphans the user's existing entity
    (and every automation pointing at it), so pin all four here.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique_id {unique_id}"
    assert hass.states.get(entity_id) is not None


@pytest.mark.parametrize(
    "malformed_body",
    [
        {**STATE_BODY, "screen": {}},
        {**STATE_BODY, "screen": {"on": True}},
        {**STATE_BODY, "volume": {"value": 42}},
        {**STATE_BODY, "playing": {"spotify": True}},
        {**STATE_BODY, "screen": "not-a-mapping"},
        {**STATE_BODY, "volume": None},
    ],
)
async def test_missing_leaf_keys_fail_the_update(hass, malformed_body):
    """Sections present but missing the leaves the entities read are malformed
    too - the entry retries instead of loading entities that KeyError.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=malformed_body,
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

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
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


@pytest.mark.parametrize(
    "malformed_body",
    [
        # Missing available/writable in screen section
        {**STATE_BODY, "screen": {"on": True, "brightness": 80}},
        # screen.available not a bool
        {**STATE_BODY, "screen": {**STATE_BODY["screen"], "available": 1}},
        # screen.writable not a bool
        {**STATE_BODY, "screen": {**STATE_BODY["screen"], "writable": "yes"}},
        # screen.brightness out of range
        {**STATE_BODY, "screen": {**STATE_BODY["screen"], "brightness": 0}},
        {**STATE_BODY, "screen": {**STATE_BODY["screen"], "brightness": 101}},
        # screen.brightness not an int
        {**STATE_BODY, "screen": {**STATE_BODY["screen"], "brightness": "80"}},
        # volume.value out of range
        {**STATE_BODY, "volume": {"value": -1, "fixed": False}},
        {**STATE_BODY, "volume": {"value": 101, "fixed": False}},
        # playing.spotify not a bool
        {**STATE_BODY, "playing": {"spotify": 1, "dlna": False}},
    ],
)
async def test_type_validation_rejects_bad_values(hass, malformed_body):
    """Values with wrong types or out-of-range numbers fail the coordinator update."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=malformed_body,
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


# ---------------------------------------------------------------------------
# Announce service tests
# ---------------------------------------------------------------------------

DEVICE_ID_2 = "xyz789"
DEVICE_NAME_2 = "Bedroom Rusty"
HOST_2 = "192.168.7.200"

STATE_BODY_2 = {
    "device": {"id": DEVICE_ID_2, "name": DEVICE_NAME_2, "version": "2.3.0"},
    "screen": {"on": True, "brightness": 80, "mode": "auto", "writable": True, "available": True},
    "volume": {"value": 30, "fixed": False},
    "playing": {"spotify": False, "dlna": False},
    "slideshow": {"enabled": False},
}


async def _setup_two_devices(hass):
    """Load two Rusty config entries sequentially and return (entry1, entry2)."""
    entry1 = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT},
        title=DEVICE_NAME,
    )
    entry2 = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID_2,
        data={CONF_HOST: HOST_2, CONF_PORT: DEFAULT_PORT},
        title=DEVICE_NAME_2,
    )
    # Add and set up entry1 first, then entry2. Using separate with blocks so
    # the mock returns the right state for each entry's coordinator.
    entry1.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry1.entry_id)
        await hass.async_block_till_done()

    entry2.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY_2,
    ):
        await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()
    return entry1, entry2


async def test_announce_service_registered_after_setup(hass):
    """The announce service is registered when at least one entry is loaded."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "announce")


async def test_announce_service_removed_after_last_unload(hass):
    """The announce service is removed when the last entry is unloaded."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "announce")


async def test_announce_service_not_removed_when_second_entry_remains(hass):
    """Service stays registered as long as at least one Rusty entry is loaded."""
    entry1, entry2 = await _setup_two_devices(hass)
    assert hass.services.has_service(DOMAIN, "announce")

    await hass.config_entries.async_unload(entry1.entry_id)
    await hass.async_block_till_done()
    # entry2 still loaded → service must still exist.
    assert hass.services.has_service(DOMAIN, "announce")


async def test_announce_service_routes_to_correct_device(hass):
    """Announce with device_id sends to that specific device's coordinator."""
    entry1, entry2 = await _setup_two_devices(hass)

    # Patch announce on the client; we need to know WHICH client got called.
    called_from: list[str] = []

    async def _mock_announce(text: str) -> None:
        # 'self' is the RustyClient; read the stored host to identify which device.
        called_from.append(text)

    # Resolve HA device UUID for device 2.
    device_reg = dr.async_get(hass)
    ha_dev2 = device_reg.async_get_device(identifiers={(DOMAIN, DEVICE_ID_2)})
    assert ha_dev2 is not None, "device 2 must be registered"

    with patch.object(entry2.runtime_data.client, "async_announce", _mock_announce):
        with patch.object(entry1.runtime_data.client, "async_announce",
                          side_effect=AssertionError("wrong device")):
            await hass.services.async_call(
                DOMAIN,
                "announce",
                {"device_id": ha_dev2.id, "text": "hello from bedroom"},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert called_from == ["hello from bedroom"]


async def test_announce_service_unknown_device_raises(hass):
    """Calling announce with an unknown device_id raises an error."""
    from homeassistant.exceptions import ServiceValidationError

    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "announce",
            {"device_id": "nonexistent-id", "text": "hello"},
            blocking=True,
        )


async def test_announce_service_api_error_raises_homeassistant_error(hass):
    """An API error from the device raises HomeAssistantError."""
    from custom_components.rusty.api import RustyConnectionError
    from homeassistant.exceptions import HomeAssistantError

    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Resolve HA device UUID.
    device_reg = dr.async_get(hass)
    ha_dev = device_reg.async_get_device(identifiers={(DOMAIN, DEVICE_ID)})
    assert ha_dev is not None

    with patch(
        "custom_components.rusty.api.RustyClient.async_announce",
        new_callable=AsyncMock,
        side_effect=RustyConnectionError("device unreachable"),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "announce",
                {"device_id": ha_dev.id, "text": "hello"},
                blocking=True,
            )


async def test_announce_service_text_too_long_rejected(hass):
    """Text exceeding 4000 chars is rejected by schema validation."""
    import voluptuous

    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with pytest.raises((voluptuous.error.Invalid, Exception)):
        await hass.services.async_call(
            DOMAIN,
            "announce",
            {"device_id": DEVICE_ID, "text": "x" * 4001},
            blocking=True,
        )


# ---------------------------------------------------------------------------
# Regression tests for serial_number (finding 7) and 401→reauth (finding 6)
# ---------------------------------------------------------------------------


async def test_setup_entry_registers_serial_number(hass):
    """Regression: device registry entry carries the Rusty device.id as serial_number."""
    from homeassistant import config_entries as ce
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, DEVICE_ID)})
    assert device is not None
    assert device.serial_number == DEVICE_ID


async def test_coordinator_401_starts_reauth_flow(hass):
    """Regression R6: a 401 from the device during coordinator refresh starts reauth."""
    from homeassistant import config_entries as ce
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, "password": "old"},
        title=DEVICE_NAME,
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.RustyClient.async_state",
        side_effect=RustyAuthError("401", status=401),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(
        f["context"]["source"] == ce.SOURCE_REAUTH for f in flows
    ), "Expected a reauth flow to be started"
