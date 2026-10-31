"""Tests for the Rusty config flow (zeroconf discovery + manual host entry)."""
from __future__ import annotations

from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rusty.api import RustyApiError, RustyAuthError, RustyConnectionError
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


def _zeroconf_discovery_info(
    host: str = HOST, properties: dict | None = None, port: int | None = DEFAULT_PORT
) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=port,
        hostname="rusty.local.",
        type="_rusty._tcp.local.",
        name="rusty._rusty._tcp.local.",
        properties=properties if properties is not None else {"id": DEVICE_ID, "api": "1"},
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
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == DEVICE_ID
    assert entry.data[CONF_HOST] == HOST


async def test_zeroconf_duplicate_shows_rediscovery_confirm_then_updates(hass):
    """Rediscovering a known device at a new IP shows confirm form, then updates host."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    discovery_info = _zeroconf_discovery_info(HOST)

    # New flow probes the host WITHOUT stored password to verify identity.
    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    # Must show a confirmation form — not silently update.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "rediscovery_confirm"

    # User confirms → entry updated, reload triggered, flow aborts.
    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    updated_entry = hass.config_entries.async_get_entry(existing_entry.entry_id)
    assert updated_entry.data[CONF_HOST] == HOST


async def test_zeroconf_rediscovery_probe_failure_aborts_silently(hass):
    """A re-discovery whose probe fails does NOT update the stored entry."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    discovery_info = _zeroconf_discovery_info(HOST)

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyConnectionError("cannot connect"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"

    # Stored entry is unchanged.
    unchanged = hass.config_entries.async_get_entry(existing_entry.entry_id)
    assert unchanged.data[CONF_HOST] == EXISTING_HOST


async def test_zeroconf_same_address_aborts_without_form(hass):
    """Re-discovering the same device at the same address aborts immediately."""
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    discovery_info = _zeroconf_discovery_info(HOST)

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_zeroconf_uses_the_advertised_port(hass):
    """A device advertising a non-default port is probed and stored on that port."""
    discovery_info = _zeroconf_discovery_info(port=9876)

    with patch(
        "custom_components.rusty.config_flow.RustyConfigFlow._async_probe",
        new_callable=AsyncMock,
        return_value=STATE_BODY,
    ) as mock_probe:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 9876, CONF_PASSWORD: ""}
    # The probe went to the advertised port, without a password (security: don't send
    # stored credentials to an unverified host).
    mock_probe.assert_awaited_once_with(HOST, 9876)


async def test_zeroconf_without_a_port_falls_back_to_the_default(hass):
    """A record that omits the port still gets DEFAULT_PORT."""
    discovery_info = _zeroconf_discovery_info(port=None)

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""}


async def test_zeroconf_probe_failure_aborts(hass):
    """A discovered device that can't be probed aborts with cannot_connect."""
    discovery_info = _zeroconf_discovery_info(properties={"api": "1"})

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyConnectionError("cannot connect"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_zeroconf_malformed_state_aborts(hass):
    """A discovered device whose /api/state has no usable device id/name aborts."""
    discovery_info = _zeroconf_discovery_info(properties={"api": "1"})

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=discovery_info,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert hass.config_entries.async_entries(DOMAIN) == []


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
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""}

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


async def test_zeroconf_txt_id_absent_falls_back_to_probe(hass):
    """When TXT properties carry no `id`, the flow probes /api/state for it."""
    discovery_info = _zeroconf_discovery_info(properties={"api": "1"})

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
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == DEVICE_ID


async def test_manual_malformed_state_shows_cannot_connect(hass):
    """A response missing the expected device id/name is treated as cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value={"unexpected": "shape"},
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: HOST}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_manual_non_connection_api_error_shows_cannot_connect(hass):
    """A non-connection RustyApiError (e.g. HTTP 4xx/5xx or bad JSON) also maps to cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyApiError("HTTP 500"),
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


async def test_user_step_invalid_auth_shows_error(hass):
    """A RustyAuthError while probing shows invalid_auth instead of cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyAuthError("password required"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: HOST, CONF_PASSWORD: "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_step_with_password_stores_it(hass):
    """A password entered in the user step is persisted in the config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_HOST: HOST, CONF_PASSWORD: "s3cr3t"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == "s3cr3t"


async def test_zeroconf_duplicate_also_updates_port(hass):
    """Rediscovering a known device at new IP+port shows confirm, then updates both."""
    NEW_PORT = 9999
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""},
        title=DEVICE_NAME,
    )
    existing_entry.add_to_hass(hass)

    from ipaddress import ip_address as _ip
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo as _ZSI

    discovery_info = _ZSI(
        ip_address=_ip(HOST),
        ip_addresses=[_ip(HOST)],
        port=NEW_PORT,
        hostname="rusty.local.",
        type="_rusty._tcp.local.",
        name="rusty._rusty._tcp.local.",
        properties={"id": DEVICE_ID, "api": "1"},
    )

    # Probe the new host to confirm identity.
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
    assert result["step_id"] == "rediscovery_confirm"

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    updated = hass.config_entries.async_get_entry(existing_entry.entry_id)
    assert updated.data[CONF_HOST] == HOST
    assert updated.data[CONF_PORT] == NEW_PORT


async def test_reauth_confirm_updates_password_and_reloads(hass):
    """Re-auth confirm updates the stored password and reloads the entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "old"},
        title=DEVICE_NAME,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.rusty.config_flow.RustyConfigFlow._async_probe",
        new_callable=AsyncMock,
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "new_pass"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new_pass"


async def test_reauth_confirm_wrong_password_shows_error(hass):
    """Re-auth confirm with a wrong password shows invalid_auth."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "old"},
        title=DEVICE_NAME,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )

    with patch(
        "custom_components.rusty.config_flow.RustyConfigFlow._async_probe",
        new_callable=AsyncMock,
        side_effect=RustyAuthError("wrong password"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "wrong"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


# ---------------------------------------------------------------------------
# Regression tests for findings 1-4 and minor security issues
# ---------------------------------------------------------------------------


async def test_zeroconf_probe_returns_no_device_id_aborts(hass):
    """Regression R1: probe succeeds but returns no device.id — abort, no TXT fallback."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "s3cret"},
    ).add_to_hass(hass)
    # Probe returns state without a device.id; TXT carries the id — must NOT be trusted.
    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value={
            "device": {"name": "Rusty"},  # no "id"
            "screen": {"on": False, "brightness": 50, "mode": "window",
                        "writable": True, "available": True},
            "volume": {"value": 10, "fixed": False},
            "playing": {"spotify": False, "dlna": False},
        },
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_discovery_info(HOST, properties={"id": DEVICE_ID}),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_zeroconf_password_entry_401_shows_credential_form(hass):
    """Regression R2: device with password moves IP; probe returns 401 → show form."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "s3cret"},
        title=DEVICE_NAME,
    ).add_to_hass(hass)
    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        side_effect=RustyAuthError("401"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_discovery_info(HOST, properties={"id": DEVICE_ID}),
        )
    # Fixed: should show the credential form, not abort.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "rediscovery_confirm"
    assert CONF_PASSWORD in result["data_schema"].schema


async def test_zeroconf_password_entry_confirm_reloads_entry(hass):
    """Regression R3: confirming a host change triggers a reload of the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: ""},
        title=DEVICE_NAME,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_discovery_info(HOST),
        )
    assert result["step_id"] == "rediscovery_confirm"

    with patch(
        "custom_components.rusty.coordinator.RustyClient.async_state",
        return_value=STATE_BODY,
    ) as state_mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated.data[CONF_HOST] == HOST
    # Reload happened: coordinator fetched state from the new host.
    assert state_mock.call_count >= 1


async def test_zeroconf_ignored_entry_aborts(hass):
    """Regression R4: a device the user ignored does not show the rediscovery form."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        source=config_entries.SOURCE_IGNORE,
        data={},
    ).add_to_hass(hass)
    with patch(
        "custom_components.rusty.config_flow.RustyClient.async_state",
        return_value=STATE_BODY,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=_zeroconf_discovery_info(HOST),
        )
    assert result["type"] is FlowResultType.ABORT


async def test_reauth_confirm_identity_mismatch_rejected(hass):
    """Minor: reauth probe returns a different device.id → invalid_auth, not reload."""
    DIFFERENT_STATE = {
        **STATE_BODY,
        "device": {"id": "different-device-id", "name": "Other", "version": "1"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "old"},
        title=DEVICE_NAME,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    with patch(
        "custom_components.rusty.config_flow.RustyConfigFlow._async_probe",
        new_callable=AsyncMock,
        return_value=DIFFERENT_STATE,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_PASSWORD: "newpass"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_zeroconf_401_rediscovery_dedupes_flows_without_sending_password(hass):
    """Regression F4: repeated 401 announcements yield ONE flow keyed by the device id,
    and the stored password is never sent to the newly announced host."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=DEVICE_ID,
        data={CONF_HOST: EXISTING_HOST, CONF_PORT: DEFAULT_PORT, CONF_PASSWORD: "s3cret"},
        title=DEVICE_NAME,
    ).add_to_hass(hass)
    sent_passwords = []

    async def _state(client):
        sent_passwords.append(client._password)
        raise RustyAuthError("401")

    with patch("custom_components.rusty.config_flow.RustyClient.async_state", _state):
        results = [
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_ZEROCONF},
                data=_zeroconf_discovery_info(HOST, properties={"id": DEVICE_ID}),
            )
            for _ in range(3)
        ]
    assert results[0]["step_id"] == "rediscovery_confirm"
    assert [r["reason"] for r in results[1:]] == ["already_in_progress"] * 2
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["unique_id"] for f in flows] == [DEVICE_ID]
    assert sent_passwords and all(pw is None for pw in sent_passwords)
