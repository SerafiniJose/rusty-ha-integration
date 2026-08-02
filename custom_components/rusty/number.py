"""Number platform for the Rusty integration: device volume control."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import RustyApiError, RustyVolumeFixedError
from .coordinator import RustyConfigEntry, RustyCoordinator
from .entity import RustyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RustyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Rusty volume number entity."""
    async_add_entities([RustyVolumeNumber(entry.runtime_data)])


class RustyVolumeNumber(RustyEntity, NumberEntity):
    """The Rusty device's volume, as a 0-100 slider."""

    _attr_name = "Volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: RustyCoordinator) -> None:
        """Initialize the volume number entity."""
        super().__init__(coordinator, "volume")

    @property
    def available(self) -> bool:
        """Unavailable when the coordinator has no good data, or the device
        reports its volume as fixed (not adjustable).
        """
        return super().available and not self.coordinator.data["volume"]["fixed"]

    @property
    def native_value(self) -> int:
        """Return the current volume value."""
        return self.coordinator.data["volume"]["value"]

    async def async_set_native_value(self, value: float) -> None:
        """Set the device volume."""
        try:
            result = await self.coordinator.client.async_set_volume(round(value))
        except RustyVolumeFixedError as err:
            raise HomeAssistantError(
                "Rusty's volume is fixed and cannot be changed"
            ) from err
        except RustyApiError as err:
            raise HomeAssistantError(f"Error communicating with Rusty: {err}") from err
        await self.coordinator.async_apply_command_result(result)
