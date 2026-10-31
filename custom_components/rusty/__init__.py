"""The Rusty integration: entry lifecycle (client, coordinator, device, platforms)."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RustyApiError, RustyClient
from .const import DOMAIN
from .coordinator import RustyConfigEntry, RustyCoordinator

PLATFORMS: list[Platform | str] = [
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

# Service schema: device_id is the HA device UUID (from the device selector);
# text is what to say.  Max 4000 chars matches the device cap.
_ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("text"): vol.All(cv.string, vol.Length(min=1, max=4000)),
    }
)

_ANNOUNCE_SERVICE = "announce"


def _coordinator_for_rusty_id(
    hass: HomeAssistant, rusty_id: str
) -> RustyCoordinator | None:
    """Find the loaded coordinator whose device.id matches rusty_id."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not hasattr(entry, "runtime_data"):
            continue
        coordinator: RustyCoordinator = entry.runtime_data
        if coordinator.data and coordinator.data.get("device", {}).get("id") == rusty_id:
            return coordinator
    return None


def _register_announce_service(hass: HomeAssistant) -> None:
    """Register the domain-level announce service (idempotent)."""
    if hass.services.has_service(DOMAIN, _ANNOUNCE_SERVICE):
        return

    async def _handle_announce(call: ServiceCall) -> None:
        ha_device_id: str = call.data["device_id"]
        text: str = call.data["text"]

        # Resolve HA device UUID → Rusty device.id via identifiers.
        device_registry = dr.async_get(hass)
        ha_device = device_registry.async_get(ha_device_id)
        if ha_device is None:
            raise ServiceValidationError(
                f"No device found for id {ha_device_id!r}."
            )
        rusty_id = next(
            (ident[1] for ident in ha_device.identifiers if ident[0] == DOMAIN),
            None,
        )
        if rusty_id is None:
            raise ServiceValidationError(
                f"Device {ha_device_id!r} is not a Rusty device."
            )

        coordinator = _coordinator_for_rusty_id(hass, rusty_id)
        if coordinator is None:
            raise ServiceValidationError(
                f"No loaded Rusty device with id {rusty_id!r}. "
                "Make sure the integration is set up and the device is reachable."
            )
        try:
            await coordinator.client.async_announce(text)
        except RustyApiError as err:
            raise HomeAssistantError(f"Rusty announce failed: {err}") from err

    hass.services.async_register(
        DOMAIN,
        _ANNOUNCE_SERVICE,
        _handle_announce,
        schema=_ANNOUNCE_SCHEMA,
    )


async def async_setup_entry(hass: HomeAssistant, entry: RustyConfigEntry) -> bool:
    """Set up a Rusty config entry: client, coordinator, device, platforms."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    password = entry.data.get(CONF_PASSWORD) or None

    client = RustyClient(host, async_get_clientsession(hass), port=port, password=password)
    coordinator = RustyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    device = coordinator.data["device"]
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device["id"])},
        name=device.get("name"),
        manufacturer="Rusty",
        model="Rusty",
        sw_version=device.get("version"),
        serial_number=device["id"],
        configuration_url=f"http://{host}:{port}/",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the domain-level announce service once; subsequent entries reuse it.
    _register_announce_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: RustyConfigEntry) -> bool:
    """Unload a Rusty config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Remove the service only when the last entry is gone.
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        hass.services.async_remove(DOMAIN, _ANNOUNCE_SERVICE)
    return unloaded
