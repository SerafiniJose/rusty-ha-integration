"""Config flow for the Rusty integration.

Supports zeroconf discovery (`_rusty._tcp.local.`) and manual host entry.
The device's `/api/state` endpoint is used as the validation probe; the
device id it reports becomes the config entry's unique id.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import RustyApiError, RustyClient
from .const import DEFAULT_PORT, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class RustyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rusty."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow's discovered-device state."""
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None

    async def _async_probe(self, host: str) -> dict[str, Any]:
        """Probe the device's /api/state, returning the parsed JSON state."""
        client = RustyClient(host, async_get_clientsession(self.hass))
        return await client.async_state()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf discovery."""
        host = discovery_info.host
        device_id = discovery_info.properties.get("id")
        had_txt_id = bool(device_id)

        # Quick abort (and host update) if the TXT id already matches a
        # configured entry, without needing to reach the device first.
        if had_txt_id:
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        try:
            state = await self._async_probe(host)
        except RustyApiError:
            return self.async_abort(reason="cannot_connect")

        device = state.get("device") or {}
        device_id = device_id or device.get("id")
        device_name = device.get("name")
        if not device_id or not device_name:
            return self.async_abort(reason="cannot_connect")

        # Only re-run the unique-id dance if the id wasn't already known (and
        # already checked) from the TXT record above.
        if not had_txt_id:
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_name = device_name
        self.context["title_placeholders"] = {"name": device_name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery before creating the entry."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name,
                data={CONF_HOST: self._discovered_host, CONF_PORT: DEFAULT_PORT},
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._discovered_name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user entering a host manually."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            try:
                state = await self._async_probe(host)
            except RustyApiError:
                errors["base"] = "cannot_connect"
            else:
                device = state.get("device") or {}
                device_id = device.get("id")
                device_name = device.get("name")
                if not device_id or not device_name:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(device_id)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    return self.async_create_entry(
                        title=device_name,
                        data={CONF_HOST: host, CONF_PORT: DEFAULT_PORT},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
