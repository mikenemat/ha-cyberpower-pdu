"""Config flow for the CyberPower PDU integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
import voluptuous as vol

from .const import (
    AUTH_NONE,
    AUTH_PROTOCOLS,
    CONF_AUTH_KEY,
    CONF_AUTH_PROTOCOL,
    CONF_COMMUNITY,
    CONF_HOST,
    CONF_PORT,
    CONF_PRIV_KEY,
    CONF_PRIV_PROTOCOL,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    CONF_VERSION,
    CONF_WRITE_COMMUNITY,
    DEFAULT_COMMUNITY,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERSION,
    DEFAULT_WRITE_COMMUNITY,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    OID_IDENT_MODEL,
    OID_IDENT_SERIAL,
    OID_IF_PHYS_ADDRESS,
    OID_SYS_OBJECT_ID,
    PRIV_NONE,
    PRIV_PROTOCOLS,
    SNMP_VERSIONS,
    VERSION_V3,
)
from .coordinator import CyberPowerConfigEntry, device_unique_id
from .discovery import DiscoveredPdu, async_discover_pdus
from .snmp import CyberPowerSnmp, SnmpCredentials, SnmpError, as_mac, as_str

_LOGGER = logging.getLogger(__name__)

# CyberPower Systems enterprise number; sysObjectID must live under it.
_CYBERPOWER_ENTERPRISE = "3808"
# Sentinel option for "I'll type an IP myself" in the discovery picker.
CONF_DEVICE = "device"
MANUAL = "__manual__"


class CannotConnect(Exception):
    """Cannot reach the PDU over SNMP."""


class NotCyberPower(Exception):
    """The responding device is not a CyberPower PDU."""


async def _validate(
    host: str, port: int, credentials: SnmpCredentials
) -> dict[str, Any]:
    """Probe the device and return its identifying details."""
    snmp = CyberPowerSnmp(host, port, credentials)
    try:
        result = await snmp.get(
            [
                OID_SYS_OBJECT_ID,
                OID_IDENT_MODEL,
                OID_IDENT_SERIAL,
                OID_IF_PHYS_ADDRESS,
            ]
        )
    except SnmpError as err:
        raise CannotConnect from err
    finally:
        snmp.close()

    sys_object_id = as_str(result.get(OID_SYS_OBJECT_ID)) or ""
    if _CYBERPOWER_ENTERPRISE not in sys_object_id:
        raise NotCyberPower
    return {
        "model": as_str(result.get(OID_IDENT_MODEL)) or "PDU",
        "serial": as_str(result.get(OID_IDENT_SERIAL)) or "",
        "mac": as_mac(result.get(OID_IF_PHYS_ADDRESS)),
    }


class CyberPowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CyberPower PDU."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}
        self._discovered: dict[str, DiscoveredPdu] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: CyberPowerConfigEntry,
    ) -> CyberPowerOptionsFlow:
        """Return the options flow."""
        return CyberPowerOptionsFlow()

    def _already_configured(self, pdu: DiscoveredPdu) -> bool:
        """True if a discovered PDU matches an existing entry (by serial/MAC)."""
        unique_id = device_unique_id(pdu.serial, pdu.mac)
        if unique_id is None:
            return False
        return unique_id in {entry.unique_id for entry in self._async_current_entries()}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan the network and let the user pick a PDU (or go manual)."""
        if user_input is not None:
            choice = user_input[CONF_DEVICE]
            if choice == MANUAL:
                return await self.async_step_manual()
            pdu = self._discovered[choice]
            self._data[CONF_HOST] = pdu.host
            self._data[CONF_PORT] = DEFAULT_PORT
            self._data[CONF_VERSION] = DEFAULT_VERSION
            return await self.async_step_credentials()

        try:
            discovered = await async_discover_pdus(self.hass)
        except Exception:  # discovery is best-effort; fall back to manual
            _LOGGER.debug("Network discovery failed", exc_info=True)
            discovered = []

        self._discovered = {
            pdu.host: pdu for pdu in discovered if not self._already_configured(pdu)
        }
        if not self._discovered:
            return await self.async_step_manual()

        options = {
            host: f"{pdu.model} ({host})" for host, pdu in self._discovered.items()
        }
        options[MANUAL] = "Enter IP address manually"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(options)}),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details by hand (the always-available fallback)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_credentials()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._data.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_VERSION, default=DEFAULT_VERSION): vol.In(
                    SNMP_VERSIONS
                ),
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect SNMP credentials and validate the connection."""
        errors: dict[str, str] = {}
        version = self._data[CONF_VERSION]

        if user_input is not None:
            data = {**self._data, **user_input}
            credentials = SnmpCredentials(
                version=version,
                community=data.get(CONF_COMMUNITY, DEFAULT_COMMUNITY),
                write_community=data.get(CONF_WRITE_COMMUNITY, DEFAULT_WRITE_COMMUNITY),
                username=data.get(CONF_USERNAME, ""),
                auth_protocol=data.get(CONF_AUTH_PROTOCOL, AUTH_NONE),
                auth_key=data.get(CONF_AUTH_KEY, ""),
                priv_protocol=data.get(CONF_PRIV_PROTOCOL, PRIV_NONE),
                priv_key=data.get(CONF_PRIV_KEY, ""),
            )
            try:
                info = await _validate(data[CONF_HOST], data[CONF_PORT], credentials)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except NotCyberPower:
                errors["base"] = "not_cyberpower"
            else:
                unique_id = device_unique_id(info["serial"], info["mac"])
                if unique_id is None:
                    errors["base"] = "cannot_identify"
                else:
                    await self.async_set_unique_id(unique_id, raise_on_progress=False)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: data[CONF_HOST]}
                    )
                    title = info["model"]
                    if info["serial"]:
                        title = f"{info['model']} ({info['serial']})"
                    return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=self._credentials_schema(version),
            errors=errors,
            description_placeholders={"host": self._data.get(CONF_HOST, "")},
        )

    @staticmethod
    def _credentials_schema(version: str) -> vol.Schema:
        """Return the credentials schema for the chosen SNMP version."""
        if version == VERSION_V3:
            return vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_AUTH_PROTOCOL, default=AUTH_NONE): vol.In(
                        AUTH_PROTOCOLS
                    ),
                    vol.Optional(CONF_AUTH_KEY, default=""): str,
                    vol.Required(CONF_PRIV_PROTOCOL, default=PRIV_NONE): vol.In(
                        PRIV_PROTOCOLS
                    ),
                    vol.Optional(CONF_PRIV_KEY, default=""): str,
                }
            )
        return vol.Schema(
            {
                vol.Required(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): str,
                vol.Required(
                    CONF_WRITE_COMMUNITY, default=DEFAULT_WRITE_COMMUNITY
                ): str,
            }
        )


class CyberPowerOptionsFlow(OptionsFlow):
    """Handle options for the CyberPower PDU integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the poll interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
