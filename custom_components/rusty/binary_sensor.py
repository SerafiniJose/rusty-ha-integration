"""Binary sensor platform for the Rusty integration: what's currently playing."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import RustyConfigEntry, RustyCoordinator
from .entity import RustyEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RustyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Rusty playing-state binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            RustyPlayingBinarySensor(coordinator, "spotify", "Spotify playing", "mdi:spotify"),
            RustyPlayingBinarySensor(coordinator, "dlna", "DLNA playing", "mdi:cast-audio"),
        ]
    )


class RustyPlayingBinarySensor(RustyEntity, BinarySensorEntity):
    """Reflects one of the device's `playing` sources (spotify or dlna)."""

    def __init__(
        self, coordinator: RustyCoordinator, playing_key: str, name: str, icon: str
    ) -> None:
        """Initialize the binary sensor for a single playing source."""
        super().__init__(coordinator, f"{playing_key}_playing")
        self._playing_key = playing_key
        self._attr_name = name
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        """Return whether this playing source is currently active."""
        return self.coordinator.data["playing"][self._playing_key]
