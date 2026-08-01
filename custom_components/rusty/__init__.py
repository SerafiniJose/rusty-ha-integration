"""The Rusty integration: entry lifecycle (client, coordinator, device, platforms)."""
from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RustyClient
from .const import DOMAIN
from .coordinator import RustyConfigEntry, RustyCoordinator

PLATFORMS: list[Platform | str] = [Platform.LIGHT, Platform.NUMBER, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: RustyConfigEntry) -> bool:
    """Set up a Rusty config entry: client, coordinator, device, platforms."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]

    client = RustyClient(host, async_get_clientsession(hass), port=port)
    coordinator = RustyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # The coordinator guarantees `device` is a mapping with a non-empty "id"
    # (see coordinator.py's _async_update_data validation); "name"/"version"
    # are optional fields we read defensively.
    device = coordinator.data["device"]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device["id"])},
        name=device.get("name"),
        manufacturer="Rusty",
        model="Rusty",
        sw_version=device.get("version"),
        configuration_url=f"http://{host}:{port}/",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: RustyConfigEntry) -> bool:
    """Unload a Rusty config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
