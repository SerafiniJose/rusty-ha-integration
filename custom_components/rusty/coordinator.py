"""DataUpdateCoordinator for the Rusty integration."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RustyApiError, RustyAuthError, RustyClient
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

# Every key the integration actually indexes, section by section. Entities read
# these two levels deep without re-checking, so this is where they get checked -
# once, for both the /api/state poll and command responses.
_REQUIRED_STATE_KEYS: dict[str, tuple[str, ...]] = {
    "device": ("id",),
    "screen": ("on", "brightness", "available", "writable"),
    "volume": ("value", "fixed"),
    "playing": ("spotify", "dlna"),
}

type RustyConfigEntry = ConfigEntry[RustyCoordinator]


def _validate_state(state: Any) -> dict[str, Any]:
    """Return `state` if it has every key the integration reads, else raise.

    `device` is consumed by both async_setup_entry (device registration) and
    the entities, so its `id` must additionally be non-empty; the entities read
    the other sections' leaves directly, so those must simply be present.

    Also validates that leaf values have the correct types so entities can read
    them without additional guards:
    - screen.on, screen.available, screen.writable, volume.fixed,
      playing.spotify, playing.dlna must be booleans.
    - screen.brightness must be an integer in 1..100.
    - volume.value must be an integer in 0..100.
    """
    if not isinstance(state, dict):
        raise UpdateFailed("malformed state")

    for section, leaf_keys in _REQUIRED_STATE_KEYS.items():
        value = state.get(section)
        if not isinstance(value, Mapping):
            raise UpdateFailed("malformed state")
        for leaf in leaf_keys:
            if leaf not in value:
                raise UpdateFailed("malformed state")

    if not state["device"]["id"]:
        raise UpdateFailed("malformed state")

    screen = state["screen"]
    # Type validation for boolean fields.
    for bool_key in ("on", "available", "writable"):
        if not isinstance(screen[bool_key], bool):
            raise UpdateFailed("malformed state")
    # Brightness: integer 1..100.
    brightness = screen["brightness"]
    if not isinstance(brightness, int) or isinstance(brightness, bool):
        raise UpdateFailed("malformed state")
    if not (1 <= brightness <= 100):
        raise UpdateFailed("malformed state")

    volume = state["volume"]
    if not isinstance(volume["fixed"], bool):
        raise UpdateFailed("malformed state")
    vol_value = volume["value"]
    if not isinstance(vol_value, int) or isinstance(vol_value, bool):
        raise UpdateFailed("malformed state")
    if not (0 <= vol_value <= 100):
        raise UpdateFailed("malformed state")

    playing = state["playing"]
    for bool_key in ("spotify", "dlna"):
        if not isinstance(playing[bool_key], bool):
            raise UpdateFailed("malformed state")

    return state


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
        except RustyAuthError as err:
            # 401 means the stored password is wrong or was removed from the
            # device. Raise ConfigEntryAuthFailed so HA triggers re-auth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except RustyApiError as err:
            # Covers RustyConnectionError and any other API failure (e.g. HTTP
            # error status, bad JSON) uniformly: never surface partial data.
            raise UpdateFailed(str(err)) from err

        return _validate_state(state)

    async def async_apply_command_result(self, state: Any) -> None:
        """Adopt the full state a command (POST) returned as the new data.

        Command responses go through exactly the same validation as the poll,
        so a truncated or unexpected body can never be written into
        `coordinator.data` - nor silently flip `last_update_success` to True.
        A body that doesn't validate is logged and answered with a refresh
        instead, leaving the last good data in place.
        """
        try:
            validated = _validate_state(state)
        except UpdateFailed:
            _LOGGER.warning(
                "Ignoring malformed state in a command response from %s; "
                "falling back to a refresh. Body: %s",
                self.client.host,
                state,
            )
            await self.async_request_refresh()
            return

        self.async_set_updated_data(validated)
