"""Config flow for the Rusty integration.

Supports zeroconf discovery (`_rusty._tcp.local.`) and manual host entry.
The device's `/api/state` endpoint is used as the validation probe; the
device id it reports becomes the config entry's unique id.

Security model for zeroconf rediscovery
---------------------------------------
A mDNS TXT record is not authenticated: any LAN host can announce a service
with an arbitrary `id` property.  Blindly updating a configured entry's host
from such a record — and then polling the new host with the stored password —
would let an attacker harvest the credential by spoofing the record.

Rules implemented here:
1. The TXT `id` is used only to look up an existing entry when the probe
   returns HTTP 401.  It is NOT used as a fallback device identity when the
   probe succeeds: if the probe returns no device.id, the flow aborts.
2. When a TXT id matches a configured entry with a password, and the probe
   returns 401, the user is asked to re-enter the credential explicitly.
   The credential is verified against the new host (device.id must match)
   before the entry is updated.
3. When the probe succeeds but the entry has a stored password, the flow
   aborts (a device answering without auth is suspicious).
4. Only after explicit user confirmation is the stored endpoint updated.
5. SOURCE_IGNORE entries are respected: they never trigger the rediscovery
   form.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IGNORE, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import RustyApiError, RustyAuthError, RustyClient
from .const import DEFAULT_PORT, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


class RustyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rusty."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow's discovered-device state."""
        self._discovered_host: str | None = None
        self._discovered_port: int = DEFAULT_PORT
        self._discovered_name: str | None = None
        self._rediscovered_device_id: str | None = None
        # For rediscovery_confirm description.
        self._existing_host: str | None = None
        # True when the existing entry has a password and we need the user to enter it.
        self._needs_credential: bool = False

    async def _async_probe(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Probe the device's /api/state, returning the parsed JSON state."""
        client = RustyClient(
            host, async_get_clientsession(self.hass), port=port, password=password
        )
        return await client.async_state()

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a flow initiated by zeroconf discovery.

        Security: probes the new host WITHOUT the stored password.  A 401
        from a known (password-protected) device triggers a credential form
        rather than a silent abort, so a legitimate IP change isn't blocked.
        A 200 from a device whose existing entry has a password is treated as
        suspicious and aborts.
        """
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        txt_device_id: str | None = discovery_info.properties.get("id") or None

        # --- Phase 1: probe WITHOUT any stored credential. -------------------
        try:
            state = await self._async_probe(host, port)
        except RustyAuthError:
            # Device requires auth.  If TXT id matches an entry with a
            # password, ask the user to re-enter the credential explicitly.
            if txt_device_id:
                existing_entry = next(
                    (
                        e
                        for e in self._async_current_entries(include_ignore=False)
                        if e.unique_id == txt_device_id
                    ),
                    None,
                )
                if (
                    existing_entry is not None
                    and existing_entry.data.get(CONF_PASSWORD)
                    and existing_entry.source != SOURCE_IGNORE
                ):
                    old_host = existing_entry.data.get(CONF_HOST)
                    old_port = existing_entry.data.get(CONF_PORT, DEFAULT_PORT)
                    if old_host == host and old_port == port:
                        return self.async_abort(reason="already_configured")
                    self._discovered_host = host
                    self._discovered_port = port
                    self._discovered_name = existing_entry.title
                    self._rediscovered_device_id = txt_device_id
                    self._existing_host = old_host
                    self._needs_credential = True
                    # TXT id only dedupes repeat announcements (already_in_progress);
                    # identity is still proven by the authenticated probe on confirm.
                    await self.async_set_unique_id(txt_device_id)
                    self.context["title_placeholders"] = {
                        "name": existing_entry.title
                    }
                    return await self.async_step_rediscovery_confirm()
            return self.async_abort(reason="cannot_connect")
        except RustyApiError:
            return self.async_abort(reason="cannot_connect")

        device = state.get("device") or {}
        # No TXT fallback: only the id reported by the device itself is trusted.
        probed_id: str | None = device.get("id") or None
        device_name: str | None = device.get("name") or None

        if not probed_id or not device_name:
            return self.async_abort(reason="cannot_connect")

        # --- Phase 2: does this id match a configured entry? ----------------
        await self.async_set_unique_id(probed_id)
        existing = self._async_current_entries(include_ignore=True)
        existing_entry = next(
            (e for e in existing if e.unique_id == probed_id), None
        )

        if existing_entry is None:
            # Brand-new device: normal discovery → confirm → create.
            self._discovered_host = host
            self._discovered_port = port
            self._discovered_name = device_name
            self.context["title_placeholders"] = {"name": device_name}
            return await self.async_step_zeroconf_confirm()

        # Respect SOURCE_IGNORE: user explicitly ignored this device.
        if existing_entry.source == SOURCE_IGNORE:
            return self.async_abort(reason="already_configured")

        # Entry has a password but device answered without auth → suspicious.
        if existing_entry.data.get(CONF_PASSWORD):
            return self.async_abort(reason="cannot_connect")

        old_host = existing_entry.data.get(CONF_HOST)
        old_port = existing_entry.data.get(CONF_PORT, DEFAULT_PORT)

        if old_host == host and old_port == port:
            return self.async_abort(reason="already_configured")

        # Different address, no credential needed.
        self._discovered_host = host
        self._discovered_port = port
        self._discovered_name = device_name
        self._rediscovered_device_id = probed_id
        self._existing_host = old_host
        self._needs_credential = False
        self.context["title_placeholders"] = {"name": device_name}
        return await self.async_step_rediscovery_confirm()

    async def async_step_rediscovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm an endpoint change for a known device.

        When the device requires authentication, the user must re-enter the
        credential.  The credential is verified against the new host before
        the entry is updated.  The entry is reloaded after updating so the
        client immediately uses the new endpoint.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if self._needs_credential:
                password = user_input.get(CONF_PASSWORD) or None
                try:
                    state = await self._async_probe(
                        self._discovered_host,
                        self._discovered_port,
                        password=password,
                    )
                except RustyAuthError:
                    errors["base"] = "invalid_auth"
                except RustyApiError:
                    errors["base"] = "cannot_connect"
                else:
                    probed_id = (state.get("device") or {}).get("id")
                    if probed_id != self._rediscovered_device_id:
                        # Device at new address isn't the expected device.
                        errors["base"] = "invalid_auth"

            if not errors:
                existing = next(
                    (
                        e
                        for e in self._async_current_entries(include_ignore=True)
                        if e.unique_id == self._rediscovered_device_id
                    ),
                    None,
                )
                if existing is None:
                    return self.async_abort(reason="already_configured")
                updates: dict[str, Any] = {
                    CONF_HOST: self._discovered_host,
                    CONF_PORT: self._discovered_port,
                }
                if self._needs_credential:
                    updates[CONF_PASSWORD] = user_input.get(CONF_PASSWORD) or ""
                return self.async_update_reload_and_abort(
                    existing,
                    data_updates=updates,
                    reason="already_configured",
                )

        schema = (
            {vol.Required(CONF_PASSWORD): str} if self._needs_credential else {}
        )
        return self.async_show_form(
            step_id="rediscovery_confirm",
            data_schema=vol.Schema(schema) if schema else None,
            description_placeholders={
                "name": self._discovered_name,
                "host": self._discovered_host,
                "old_host": self._existing_host or "unknown",
            },
            errors=errors,
        )

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery before creating a new entry."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name,
                data={
                    CONF_HOST: self._discovered_host,
                    CONF_PORT: self._discovered_port,
                    CONF_PASSWORD: "",
                },
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
            password = user_input.get(CONF_PASSWORD) or None
            try:
                state = await self._async_probe(host, password=password)
            except RustyAuthError:
                errors["base"] = "invalid_auth"
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
                    self._abort_if_unique_id_configured(
                        updates={
                            CONF_HOST: host,
                            CONF_PORT: DEFAULT_PORT,
                            CONF_PASSWORD: password or "",
                        }
                    )
                    return self.async_create_entry(
                        title=device_name,
                        data={
                            CONF_HOST: host,
                            CONF_PORT: DEFAULT_PORT,
                            CONF_PASSWORD: password or "",
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when a 401 is received."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt the user for a new password."""
        errors: dict[str, str] = {}

        reauth_entry = self._get_reauth_entry()
        host = reauth_entry.data[CONF_HOST]
        port = reauth_entry.data.get(CONF_PORT, DEFAULT_PORT)

        if user_input is not None:
            password = user_input.get(CONF_PASSWORD) or None
            try:
                state = await self._async_probe(host, port, password=password)
            except RustyAuthError:
                errors["base"] = "invalid_auth"
            except RustyApiError:
                errors["base"] = "cannot_connect"
            else:
                # Reject if the device at this address isn't the expected one.
                probed_id = (state.get("device") or {}).get("id")
                if probed_id != reauth_entry.unique_id:
                    errors["base"] = "invalid_auth"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates={CONF_PASSWORD: password or ""},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"host": host},
        )
