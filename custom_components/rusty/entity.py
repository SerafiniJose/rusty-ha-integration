"""Shared base entity for the Rusty integration's platforms.

Holds the one thing every platform needs and must not duplicate: the device
this entity belongs to (via `device_info`, tying back to the device
registered in `__init__.py`) and a stable, device-derived `unique_id`.
"""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RustyCoordinator


class RustyEntity(CoordinatorEntity[RustyCoordinator]):
    """Base class for all Rusty entities.

    Availability follows the coordinator's success state by default
    (`CoordinatorEntity.available` reads `coordinator.last_update_success`),
    so a parse/update failure never gets papered over here.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: RustyCoordinator, unique_id_suffix: str) -> None:
        """Initialize with the coordinator and this entity's unique_id suffix."""
        super().__init__(coordinator)
        device_id = coordinator.data["device"]["id"]
        self._attr_unique_id = f"{device_id}_{unique_id_suffix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})
