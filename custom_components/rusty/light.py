"""Light platform for the Rusty integration: the device's screen, as a
brightness-only dimmable light.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import RustyApiError
from .coordinator import RustyConfigEntry, RustyCoordinator
from .entity import RustyEntity

_MIN_DEVICE_BRIGHTNESS = 1
_MAX_DEVICE_BRIGHTNESS = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RustyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Rusty screen light entity."""
    async_add_entities([RustyScreenLight(entry.runtime_data)])


def _ha_brightness_to_device(ha_brightness: int) -> int:
    """Convert an HA brightness (0-255) to the device's 1-100 scale, clamped."""
    device_brightness = round(ha_brightness * 100 / 255)
    return max(_MIN_DEVICE_BRIGHTNESS, min(_MAX_DEVICE_BRIGHTNESS, device_brightness))


class RustyScreenLight(RustyEntity, LightEntity):
    """The Rusty device's screen, exposed as a dimmable light."""

    _attr_name = "Screen"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: RustyCoordinator) -> None:
        """Initialize the screen light entity."""
        super().__init__(coordinator, "screen")

    @property
    def is_on(self) -> bool:
        """Return whether the screen is on."""
        return self.coordinator.data["screen"]["on"]

    @property
    def brightness(self) -> int:
        """Return the screen brightness converted from the device's 1-100 scale."""
        return round(self.coordinator.data["screen"]["brightness"] * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the screen on, optionally setting brightness, in one API call."""
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS)
        device_brightness = (
            _ha_brightness_to_device(ha_brightness) if ha_brightness else None
        )
        try:
            result = await self.coordinator.client.async_set_screen(
                True, device_brightness
            )
        except RustyApiError as err:
            raise HomeAssistantError(f"Error communicating with Rusty: {err}") from err
        await self.coordinator.async_apply_command_result(result)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the screen off."""
        try:
            result = await self.coordinator.client.async_set_screen(False)
        except RustyApiError as err:
            raise HomeAssistantError(f"Error communicating with Rusty: {err}") from err
        await self.coordinator.async_apply_command_result(result)
