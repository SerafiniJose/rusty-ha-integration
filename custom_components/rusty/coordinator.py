"""DataUpdateCoordinator for the Rusty integration."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RustyApiError, RustyClient
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

_REQUIRED_STATE_KEYS = ("device", "screen", "volume", "playing")

type RustyConfigEntry = ConfigEntry[RustyCoordinator]


class RustyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the Rusty device's /api/state and exposes the client for commands."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: RustyConfigEntry,
        client: RustyClient,
    ) -> None:
        """Initialize the coordinator with its device client."""
        self.client = client

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and validate the device's current state."""
        try:
            state = await self.client.async_state()
        except RustyApiError as err:
            # Covers RustyConnectionError and any other API failure (e.g. HTTP
            # error status, bad JSON) uniformly: never surface partial data.
            raise UpdateFailed(str(err)) from err

        for key in _REQUIRED_STATE_KEYS:
            if key not in state:
                raise UpdateFailed("malformed state")

        return state
