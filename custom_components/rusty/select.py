"""Select platform for the Rusty integration.

Two entities:
- RustyPanelSelect: navigates between top-level Rusty panels (Spotify, HA, DLNA…)
  using panel.available / panel.active from GET /api/state → POST /api/panel.
- RustyDashboardSelect: navigates between Home Assistant dashboards on the chip bar
  using panel.homeAssistant.dashboards / panel.homeAssistant.active → POST /api/home_assistant/dashboard.

Both entities only appear when the device reports the relevant section; older firmware
that omits panel data gracefully produces no entity at all.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import RustyApiError
from .coordinator import RustyConfigEntry, RustyCoordinator
from .entity import RustyEntity

# Human-readable labels for the wire values the device sends for panels.
_PANEL_LABELS: dict[str, str] = {
    "spotify": "Spotify",
    "home_assistant": "Home Assistant",
    "dlna": "DLNA",
    "camera": "Camera",
    "lockscreen": "Lockscreen",
}


def _panel(coordinator: RustyCoordinator) -> dict:
    """The `panel` section, or {} when a poll reports it missing or malformed."""
    panel = coordinator.data.get("panel")
    return panel if isinstance(panel, dict) else {}


def _dashboard_labels(dashboards: object) -> list[tuple[str, str]]:
    """(label, path) per selectable dashboard, with unique labels.

    The label is the title (the path when the title is empty); titles shared
    by several dashboards get the path appended so each stays selectable.
    """
    if not isinstance(dashboards, list):
        return []
    items = [
        (t if isinstance(t := d.get("title"), str) and t.strip() else d["path"], d["path"])
        for d in dashboards
        if isinstance(d, dict) and isinstance(d.get("path"), str) and d["path"]
    ]
    labels = [label for label, _ in items]
    return [
        (f"{label} ({path})" if labels.count(label) > 1 else label, path)
        for label, path in items
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RustyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Rusty panel and dashboard select entities."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    panel = coordinator.data.get("panel")
    if isinstance(panel, dict):
        entities.append(RustyPanelSelect(coordinator))
        ha_section = panel.get("homeAssistant") or {}
        # Dashboard select only when the HA feature is present and reports dashboards.
        if isinstance(ha_section, dict) and "dashboards" in ha_section:
            entities.append(RustyDashboardSelect(coordinator))
    if entities:
        async_add_entities(entities)


# ---------------------------------------------------------------------------
# Panel select
# ---------------------------------------------------------------------------

class RustyPanelSelect(RustyEntity, SelectEntity):
    """Active Rusty panel selector.

    Options are built from panel.available as reported by the device, so no
    hardcoded list is needed — whatever panels the device currently exposes are
    exactly the options the selector offers.
    """

    _attr_name = "Active panel"
    _attr_icon = "mdi:view-dashboard-variant"

    def __init__(self, coordinator: RustyCoordinator) -> None:
        """Initialize the panel select entity."""
        super().__init__(coordinator, "panel")

    @property
    def available(self) -> bool:
        """Unavailable when the coordinator has no good data or panel data is absent."""
        return super().available and isinstance(self.coordinator.data.get("panel"), dict)

    def _available_panels(self) -> list[str]:
        available = _panel(self.coordinator).get("available")
        return [p for p in available if isinstance(p, str)] if isinstance(available, list) else []

    @property
    def options(self) -> list[str]:
        """All panels the device currently reports as reachable."""
        return [_PANEL_LABELS.get(p, p) for p in self._available_panels()]

    @property
    def current_option(self) -> str | None:
        """The panel currently active on the device (None when no window is up)."""
        active = _panel(self.coordinator).get("active")
        if not isinstance(active, str):
            return None
        return _PANEL_LABELS.get(active, active)

    async def async_select_option(self, option: str) -> None:
        """Navigate the device to the selected panel."""
        # Reverse the label→wire mapping built in options.
        wire: str | None = next(
            (w for w in self._available_panels() if _PANEL_LABELS.get(w, w) == option),
            None,
        )
        if wire is None:
            raise HomeAssistantError(f"Unknown panel option: {option!r}")
        try:
            result = await self.coordinator.client.async_set_panel(wire)
        except RustyApiError as err:
            raise HomeAssistantError(f"Error communicating with Rusty: {err}") from err
        await self.coordinator.async_apply_command_result(result)


# ---------------------------------------------------------------------------
# Dashboard select (Home Assistant dashboards on the device's chip bar)
# ---------------------------------------------------------------------------

class RustyDashboardSelect(RustyEntity, SelectEntity):
    """Home Assistant dashboard selector.

    Options come from panel.homeAssistant.dashboards as reported by the device —
    the same list the device's chip bar shows.  Writes go to
    POST /api/home_assistant/dashboard with the dashboard's url_path.
    """

    _attr_name = "HA dashboard"
    _attr_icon = "mdi:home-assistant"

    def __init__(self, coordinator: RustyCoordinator) -> None:
        """Initialize the dashboard select entity."""
        super().__init__(coordinator, "ha_dashboard")

    def _ha_section(self) -> dict:
        ha = _panel(self.coordinator).get("homeAssistant")
        return ha if isinstance(ha, dict) else {}

    def _labels(self) -> list[tuple[str, str]]:
        return _dashboard_labels(self._ha_section().get("dashboards"))

    @property
    def available(self) -> bool:
        """Unavailable when the device doesn't expose the HA dashboard section."""
        return super().available and "dashboards" in self._ha_section()

    @property
    def options(self) -> list[str]:
        """Dashboard labels from the device's chip bar, in chip order."""
        return [label for label, _ in self._labels()]

    @property
    def current_option(self) -> str | None:
        """The dashboard url_path currently on screen, matched to its label."""
        active_path = self._ha_section().get("active")
        return next((label for label, path in self._labels() if path == active_path), None)

    async def async_select_option(self, option: str) -> None:
        """Navigate the device to the chosen HA dashboard."""
        # Map the displayed label back to the url_path the API expects.
        path: str | None = next(
            (path for label, path in self._labels() if label == option), None
        )
        if path is None:
            raise HomeAssistantError(f"Unknown HA dashboard option: {option!r}")
        try:
            result = await self.coordinator.client.async_set_dashboard(path)
        except RustyApiError as err:
            raise HomeAssistantError(f"Error communicating with Rusty: {err}") from err
        await self.coordinator.async_apply_command_result(result)
