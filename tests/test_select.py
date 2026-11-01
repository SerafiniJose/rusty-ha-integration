"""Tests for the Rusty panel select entity."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyApiError, RustyAuthError, RustyConnectionError
from homeassistant.exceptions import HomeAssistantError
from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
DEVICE_ID = "abc123"
DEVICE_NAME = "Rusty"
ENTITY_ID = "select.rusty_active_panel"

STATE_BODY_WITH_PANEL = {
    "device": {"id": DEVICE_ID, "name": DEVICE_NAME, "version": "2.3.0"},
    "screen": {"on": True, "brightness": 80, "mode": "auto", "writable": True, "available": True},
    "volume": {"value": 42, "fixed": False},
    "playing": {"spotify": True, "dlna": False},
    "slideshow": {"enabled": False},
    "app": {"foreground": False, "canBringForward": True},
    "panel": {
        "active": "spotify",
        "available": ["spotify", "home_assistant", "dlna", "lockscreen"],
        "lockscreen": {"theme": "clock", "themes": ["clock"]},
        "homeAssistant": {"dashboards": [], "active": None},
    },
}

STATE_BODY_NO_PANEL = {
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


async def _setup(hass, state_body: dict = STATE_BODY_WITH_PANEL) -> MockConfigEntry:
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=state_body,
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def test_panel_select_entity_created_when_panel_in_state(hass):
    """Panel select entity is created when the device reports a panel section."""
    await _setup(hass)
    assert hass.states.get(ENTITY_ID) is not None


async def test_panel_select_entity_not_created_without_panel(hass):
    """No select entity when the device state has no panel section (old firmware)."""
    await _setup(hass, STATE_BODY_NO_PANEL)
    assert hass.states.get(ENTITY_ID) is None


async def test_panel_select_current_option_reflects_active(hass):
    """current_option maps active wire value to human label."""
    await _setup(hass)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "Spotify"


async def test_panel_select_options_from_available(hass):
    """options lists human labels for all panels in panel.available."""
    await _setup(hass)
    state = hass.states.get(ENTITY_ID)
    assert set(state.attributes["options"]) == {
        "Spotify", "Home Assistant", "DLNA", "Lockscreen"
    }


async def test_panel_select_null_active_yields_unknown(hass):
    """panel.active = null maps to state 'unknown'."""
    body = {
        **STATE_BODY_WITH_PANEL,
        "panel": {**STATE_BODY_WITH_PANEL["panel"], "active": None},
    }
    await _setup(hass, body)
    state = hass.states.get(ENTITY_ID)
    # HA SelectEntity reports "unknown" when current_option is None.
    assert state.state == "unknown"


async def test_panel_select_calls_set_panel(hass):
    """Selecting an option calls async_set_foreground(True) then async_set_panel."""
    await _setup(hass)

    result_body = {**STATE_BODY_WITH_PANEL, "panel": {**STATE_BODY_WITH_PANEL["panel"], "active": "home_assistant"}}
    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_fg,
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=result_body,
        ) as mock_set_panel,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=result_body,
        ),
    ):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": ENTITY_ID, "option": "Home Assistant"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_fg.assert_awaited_once_with(True)
    mock_set_panel.assert_awaited_once_with("home_assistant")


async def test_panel_select_api_error_raises_ha_error(hass):
    """A RustyApiError during select_option raises HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    await _setup(hass)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value={**STATE_BODY_WITH_PANEL},
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            side_effect=RustyApiError("device unreachable"),
        ),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": ENTITY_ID, "option": "Spotify"},
                blocking=True,
            )


# ---------------------------------------------------------------------------
# Dashboard select tests
# ---------------------------------------------------------------------------

DASHBOARD_ENTITY_ID = "select.rusty_ha_dashboard"

STATE_WITH_DASHBOARDS = {
    **STATE_BODY_WITH_PANEL,
    "panel": {
        **STATE_BODY_WITH_PANEL["panel"],
        "homeAssistant": {
            "dashboards": [
                {"path": "lovelace", "title": "Home"},
                {"path": "lovelace-cameras", "title": "Cameras"},
            ],
            "active": "lovelace",
        },
    },
}


async def test_dashboard_select_created_when_dashboards_present(hass):
    """Dashboard select entity appears when the device reports HA dashboards."""
    await _setup(hass, STATE_WITH_DASHBOARDS)
    assert hass.states.get(DASHBOARD_ENTITY_ID) is not None


async def test_dashboard_select_not_created_without_panel(hass):
    """No dashboard select entity for old firmware that omits the panel section."""
    await _setup(hass, STATE_BODY_NO_PANEL)
    assert hass.states.get(DASHBOARD_ENTITY_ID) is None


async def test_dashboard_select_not_created_without_ha_dashboards(hass):
    """No dashboard select entity when homeAssistant.dashboards is absent."""
    body = {
        **STATE_BODY_WITH_PANEL,
        "panel": {**STATE_BODY_WITH_PANEL["panel"], "homeAssistant": {}},
    }
    await _setup(hass, body)
    assert hass.states.get(DASHBOARD_ENTITY_ID) is None


async def test_dashboard_select_options_are_titles(hass):
    """Dashboard selector options come from dashboard titles, not paths."""
    await _setup(hass, STATE_WITH_DASHBOARDS)
    state = hass.states.get(DASHBOARD_ENTITY_ID)
    assert set(state.attributes["options"]) == {"Home", "Cameras"}


async def test_dashboard_select_current_option_from_active_path(hass):
    """current_option maps active path to its title."""
    await _setup(hass, STATE_WITH_DASHBOARDS)
    state = hass.states.get(DASHBOARD_ENTITY_ID)
    assert state.state == "Home"


async def test_dashboard_select_null_active_yields_unknown(hass):
    """active = null → state 'unknown'."""
    body = {
        **STATE_WITH_DASHBOARDS,
        "panel": {
            **STATE_WITH_DASHBOARDS["panel"],
            "homeAssistant": {
                **STATE_WITH_DASHBOARDS["panel"]["homeAssistant"],
                "active": None,
            },
        },
    }
    await _setup(hass, body)
    state = hass.states.get(DASHBOARD_ENTITY_ID)
    assert state.state == "unknown"


async def test_dashboard_select_calls_set_dashboard(hass):
    """Selecting a dashboard calls async_set_foreground, async_set_panel, then async_set_dashboard."""
    await _setup(hass, STATE_WITH_DASHBOARDS)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value=STATE_WITH_DASHBOARDS,
        ) as mock_fg,
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=STATE_WITH_DASHBOARDS,
        ) as mock_panel,
        patch(
            "custom_components.rusty.api.RustyClient.async_set_dashboard",
            new_callable=AsyncMock,
            return_value=STATE_WITH_DASHBOARDS,
        ) as mock_set_dash,
        patch(
            "custom_components.rusty.coordinator.RustyClient.async_state",
            return_value=STATE_WITH_DASHBOARDS,
        ),
    ):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": DASHBOARD_ENTITY_ID, "option": "Cameras"},
            blocking=True,
        )
        await hass.async_block_till_done()

    mock_fg.assert_awaited_once_with(True)
    mock_panel.assert_awaited_once_with("home_assistant")
    mock_set_dash.assert_awaited_once_with("lovelace-cameras")


async def test_dashboard_select_api_error_raises_ha_error(hass):
    """A RustyApiError during dashboard selection raises HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    await _setup(hass, STATE_WITH_DASHBOARDS)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value=STATE_WITH_DASHBOARDS,
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=STATE_WITH_DASHBOARDS,
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_dashboard",
            new_callable=AsyncMock,
            side_effect=RustyApiError("HA feature off"),
        ),
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": DASHBOARD_ENTITY_ID, "option": "Home"},
                blocking=True,
            )


# ---------------------------------------------------------------------------
# Regression tests for finding 8 (panel=None crash) and empty title
# ---------------------------------------------------------------------------


async def test_panel_none_does_not_crash_select_setup(hass):
    """Regression R7: panel=null in state — select platform must not crash."""
    body = {
        **STATE_BODY_NO_PANEL,
        "panel": None,  # explicitly null, not missing
    }
    entry = _make_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=body,
    ):
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert result is True
    # No select entity created for null panel — graceful skip.
    assert hass.states.get(ENTITY_ID) is None


async def test_dashboard_empty_title_uses_path_as_label(hass):
    """Regression R8: dashboard with empty title uses path as label consistently."""
    body = {
        **STATE_BODY_WITH_PANEL,
        "panel": {
            **STATE_BODY_WITH_PANEL["panel"],
            "homeAssistant": {
                "dashboards": [{"path": "lovelace-x", "title": ""}],
                "active": "lovelace-x",
            },
        },
    }
    await _setup(hass, body)
    s = hass.states.get(DASHBOARD_ENTITY_ID)
    assert s is not None
    # Option label and current state must match (both fall back to path).
    assert s.attributes["options"] == ["lovelace-x"]
    assert s.state == "lovelace-x"


# ---------------------------------------------------------------------------
# Regression tests for the final review (F1 panel=null after setup, F2 duplicate titles)
# ---------------------------------------------------------------------------


async def test_panel_null_after_setup_does_not_break_refresh(hass):
    """Regression F1: a later poll with panel=null must not raise from the listeners."""
    entry = await _setup(hass, STATE_WITH_DASHBOARDS)
    assert hass.states.get(ENTITY_ID).state == "Spotify"
    bad = {**STATE_WITH_DASHBOARDS, "panel": None, "volume": {"value": 7, "fixed": False}}
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=bad,
    ):
        await entry.runtime_data.async_refresh()  # used to raise AttributeError
        await hass.async_block_till_done()
    assert entry.runtime_data.last_update_success
    assert hass.states.get(ENTITY_ID).state == "unavailable"
    assert hass.states.get(DASHBOARD_ENTITY_ID).state == "unavailable"
    # Listeners after the select still ran: the volume entity picked up the new value.
    assert hass.states.get("number.rusty_volume").state == "7"

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_WITH_DASHBOARDS,
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == "Spotify"


async def test_duplicate_dashboard_titles_are_distinct_and_selectable(hass):
    """Regression F2: same title on two dashboards → labels carry the path; each selectable."""
    body = {
        **STATE_BODY_WITH_PANEL,
        "panel": {
            **STATE_BODY_WITH_PANEL["panel"],
            "homeAssistant": {
                "dashboards": [
                    {"path": "lovelace", "title": "Home"},
                    {"path": "dash-b", "title": "Home"},
                    {"path": "cams", "title": "  "},
                ],
                "active": "dash-b",
            },
        },
    }
    await _setup(hass, body)
    s = hass.states.get(DASHBOARD_ENTITY_ID)
    assert s.attributes["options"] == ["Home (lovelace)", "Home (dash-b)", "cams"]
    assert s.state == "Home (dash-b)"

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_dashboard",
            new_callable=AsyncMock,
            return_value=body,
        ) as mock_set_dash,
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        for option in ("Home (dash-b)", "Home (lovelace)"):
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": DASHBOARD_ENTITY_ID, "option": option},
                blocking=True,
            )
    assert [c.args[0] for c in mock_set_dash.await_args_list] == ["dash-b", "lovelace"]


async def test_panel_select_calls_foreground_even_when_already_foreground(hass):
    """When already foreground, async_set_foreground is still called (harmless, avoids stale data)."""
    body = {**STATE_BODY_WITH_PANEL, "app": {"foreground": True, "canBringForward": True}}
    await _setup(hass, body)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
            return_value=body,
        ) as mock_fg,
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": ENTITY_ID, "option": "Spotify"},
            blocking=True,
        )

    # Called even when already foreground — avoids stale coordinator data bugs
    mock_fg.assert_awaited_once_with(True)


async def test_panel_select_skips_foreground_when_not_supported(hass):
    """_ensure_foreground must not call async_set_foreground when canBringForward is False."""
    body = {**STATE_BODY_WITH_PANEL, "app": {"foreground": False, "canBringForward": False}}
    await _setup(hass, body)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            new_callable=AsyncMock,
        ) as mock_fg,
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        await hass.services.async_call(
            "select", "select_option",
            {"entity_id": ENTITY_ID, "option": "Spotify"},
            blocking=True,
        )

    mock_fg.assert_not_called()


# ---------------------------------------------------------------------------
# Regression tests — hallazgo 1: _ensure_foreground errors must propagate
# ---------------------------------------------------------------------------


async def test_ensure_foreground_connection_error_propagates_panel_not_called(hass):
    """RustyConnectionError from foreground propagates; panel is never called.

    Regression: before fix, the error was swallowed and panel was called once
    (EXP-2a in the ponytail audit).
    """
    body = {**STATE_BODY_WITH_PANEL, "app": {"foreground": False, "canBringForward": True}}
    await _setup(hass, body)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            side_effect=RustyConnectionError("network timeout"),
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            side_effect=AssertionError("panel must not be called after foreground error"),
        ),
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        with pytest.raises(HomeAssistantError, match="Error communicating with Rusty"):
            await hass.services.async_call(
                "select", "select_option",
                {"entity_id": ENTITY_ID, "option": "Spotify"},
                blocking=True,
            )


async def test_ensure_foreground_409_propagates_no_extra_panel_retry(hass):
    """409 from foreground propagates immediately; panel is not called at all.

    Regression: before fix, the 409 was swallowed, then panel was called twice
    (first attempt + _nav retry) adding 0.4 s latency (EXP-2b in the audit).
    """
    body = {**STATE_BODY_WITH_PANEL, "app": {"foreground": False, "canBringForward": True}}
    await _setup(hass, body)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            side_effect=RustyApiError("Display over other apps permission required", status=409),
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            side_effect=AssertionError("panel must not be called after foreground error"),
        ),
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        with pytest.raises(HomeAssistantError, match="Error communicating with Rusty"):
            await hass.services.async_call(
                "select", "select_option",
                {"entity_id": ENTITY_ID, "option": "Spotify"},
                blocking=True,
            )


async def test_ensure_foreground_auth_error_propagates(hass):
    """RustyAuthError from foreground raises HomeAssistantError; navigation does not proceed.

    Regression: before fix, the 401 was silently swallowed and navigation continued
    (EXP-2c in the ponytail audit).
    """
    body = {**STATE_BODY_WITH_PANEL, "app": {"foreground": False, "canBringForward": True}}
    await _setup(hass, body)

    with (
        patch(
            "custom_components.rusty.api.RustyClient.async_set_foreground",
            side_effect=RustyAuthError("password required", status=401),
        ),
        patch(
            "custom_components.rusty.api.RustyClient.async_set_panel",
            side_effect=AssertionError("panel must not be called after foreground error"),
        ),
        patch("custom_components.rusty.coordinator.RustyClient.async_state", return_value=body),
    ):
        with pytest.raises(HomeAssistantError, match="Error communicating with Rusty"):
            await hass.services.async_call(
                "select", "select_option",
                {"entity_id": ENTITY_ID, "option": "Spotify"},
                blocking=True,
            )
