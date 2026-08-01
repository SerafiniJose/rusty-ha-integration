"""Tests for the Rusty config flow (zeroconf discovery + manual host entry)."""
from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyConnectionError
from custom_components.rusty.const import DEFAULT_PORT, DOMAIN

HOST = "192.168.7.116"
EXISTING_HOST = "192.168.7.10"
DEVICE_ID = "abc"
DEVICE_NAME = "Living Room Rusty"

STATE_BODY = {
    "device": {"id": DEVICE_ID, "name": DEVICE_NAME, "version": "2.3.0"},
    "screen": {"on": True, "brightness": 80, "mode": "auto", "writable": True, "available": True},
    "volume": {"value": 42, "fixed": False},
    "playing": {"spotify": True, "dlna": False},
    "slideshow": {"enabled": False},
}


def _zeroconf_discovery_info(host: str = HOST) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=DEFAULT_PORT,
        hostname="rusty.local.",
        type="_rusty._tcp.local.",
        name="rusty._rusty._tcp.local.",
        properties={"id": DEVICE_ID, "api": "1"},
    )


async def test_zeroconf_happy_path(hass):
    """Zeroconf discovery with an id in TXT properties confirms then creates the entry."""
    discovery_info = _zeroconf_discovery_info()

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "zeroconf_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEVICE_NAME
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == DEVICE_ID
    assert entry.data[CONF_HOST] == HOST


async def test_zeroconf_duplicate_updates_host_and_aborts(hass):
    """Rediscovering a known device at a new IP updates the stored host and aborts."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    discovery_info = _zeroconf_discovery_info(HOST)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    updated_entry = hass.config_entries.async_get_entry(existing_entry.entry_id)
    assert updated_entry.data[CONF_HOST] == HOST


async def test_manual_happy_path(hass):
    """The manual user step probes the device and creates an entry from its response."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: HOST}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == DEVICE_NAME
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == DEVICE_ID


async def test_manual_cannot_connect(hass):
    """A RustyConnectionError while probing the manual host shows a form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyConnectionError("cannot connect"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: HOST}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_duplicate_aborts(hass):
    """Manually entering a host for an already-configured device id aborts."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: HOST}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
