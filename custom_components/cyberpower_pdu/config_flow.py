"""Config flow for the CyberPower PDU integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig
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
    MAX_DISCOVERY_HOSTS,
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
from .discovery import (
    DiscoveredPdu,
    async_discover_pdus,
    async_has_scannable_networks,
    async_scan_hosts,
    is_epdu,
    parse_scan_targets,
)
from .snmp import CyberPowerSnmp, SnmpCredentials, SnmpError, as_mac, as_str

_LOGGER = logging.getLogger(__name__)

CONF_DEVICES = "devices"
CONF_SUBNET = "subnet"


def _parse_hosts(raw: str) -> list[str]:
    """Split a free-text field into a de-duplicated, ordered list of hosts."""
    seen: dict[str, None] = {}
    for token in re.split(r"[\s,;]+", raw.strip()):
        if token:
            seen.setdefault(token, None)
    return list(seen)


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
        head = await snmp.get([OID_SYS_OBJECT_ID])
        if not is_epdu(as_str(head.get(OID_SYS_OBJECT_ID)) or ""):
            raise NotCyberPower
        details = await snmp.get(
            [OID_IDENT_MODEL, OID_IDENT_SERIAL, OID_IF_PHYS_ADDRESS]
        )
    except SnmpError as err:
        raise CannotConnect from err
    finally:
        snmp.close()

    return {
        "model": as_str(details.get(OID_IDENT_MODEL)) or "PDU",
        "serial": as_str(details.get(OID_IDENT_SERIAL)) or "",
        "mac": as_mac(details.get(OID_IF_PHYS_ADDRESS)),
    }


def _title(info: dict[str, Any]) -> str:
    """Entry title from model (+ serial when available)."""
    if info["serial"]:
        return f"{info['model']} ({info['serial']})"
    return info["model"]


class CyberPowerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CyberPower PDU."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._conn: dict[str, Any] = {}  # shared port + version
        self._hosts: list[str] = []  # hosts to configure with the next creds
        self._discovered: dict[str, DiscoveredPdu] = {}
        self._discovery_task: asyncio.Task[list[DiscoveredPdu]] | None = None
        self._last_progress = 0.0
        self._scan_hosts: list[str] = []  # expanded targets for a manual subnet scan
        self._scan_community = DEFAULT_COMMUNITY
        self._scan_port = DEFAULT_PORT
        self._scan_no_results = False

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
        """Scan the network behind a progress bar, then offer the results."""
        # No scannable subnet -> skip the auto-scan and offer the fallbacks
        # (scan a subnet you specify, or enter IPs by hand) rather than flashing
        # a scan. This is the common case for HA in a bridged Docker container,
        # which can only see its own network, not the LAN.
        if self._discovery_task is None and not await async_has_scannable_networks(
            self.hass
        ):
            return await self.async_step_fallback()
        if self._discovery_task is None:
            self._discovery_task = self.hass.async_create_task(self._async_discover())
        if not self._discovery_task.done():
            return self.async_show_progress(
                step_id="user",
                progress_action="discovering",
                progress_task=self._discovery_task,
            )

        try:
            discovered = self._discovery_task.result()
        except Exception:  # discovery is best-effort; fall back to manual
            _LOGGER.debug("Network discovery failed", exc_info=True)
            discovered = []
        finally:
            self._discovery_task = None

        self._discovered = {
            pdu.host: pdu for pdu in discovered if not self._already_configured(pdu)
        }
        next_step = "discovered" if self._discovered else "fallback"
        return self.async_show_progress_done(next_step_id=next_step)

    async def _async_discover(self) -> list[DiscoveredPdu]:
        """Run the scan, pushing throttled progress to the bar."""

        def _progress(done: int, total: int) -> None:
            if not total:
                return
            fraction = done / total
            if fraction - self._last_progress >= 0.02 or done == total:
                self._last_progress = fraction
                self.async_update_progress(fraction)

        return await async_discover_pdus(self.hass, progress_cb=_progress)

    async def async_step_discovered(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the discovered PDUs (bulk), a subnet scan, or manual entry."""
        return self.async_show_menu(
            step_id="discovered", menu_options=["pick", "scan_subnet", "manual"]
        )

    async def async_step_fallback(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the fallbacks when nothing could be auto-discovered.

        Reached when the auto-scan found nothing or could not run at all (the
        usual case for bridged Docker, where HA can't see the LAN subnet).
        """
        return self.async_show_menu(
            step_id="fallback", menu_options=["scan_subnet", "manual"]
        )

    async def async_step_scan_subnet(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a subnet/range to scan, then run the scan behind a progress bar.

        Works even when HA can't enumerate the LAN itself (bridged Docker), since
        the scan sends unicast SNMP to each address, which routes out normally.
        """
        errors: dict[str, str] = {}
        if self._scan_no_results:
            self._scan_no_results = False
            errors["base"] = "no_devices_found"
        if user_input is not None:
            try:
                hosts = parse_scan_targets(user_input[CONF_SUBNET])
            except ValueError:
                errors["base"] = "invalid_subnet"
            else:
                if len(hosts) > MAX_DISCOVERY_HOSTS:
                    errors["base"] = "subnet_too_large"
                else:
                    self._scan_hosts = hosts
                    self._scan_community = user_input[CONF_COMMUNITY]
                    self._scan_port = user_input[CONF_PORT]
                    self._conn = {
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_VERSION: DEFAULT_VERSION,
                    }
                    # Kick off the scan and show progress. We must return the
                    # progress result directly (not chain into scan_run): HA's
                    # eager task factory can finish the task synchronously, and a
                    # progress_done returned from this form-submit handler would
                    # carry this form's user_input into the next step. Letting HA
                    # re-enter scan_run via its done-callback avoids that.
                    self._discovery_task = self.hass.async_create_task(
                        self._async_scan()
                    )
                    return self.async_show_progress(
                        step_id="scan_run",
                        progress_action="discovering",
                        progress_task=self._discovery_task,
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_SUBNET): str,
                vol.Required(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(
            step_id="scan_subnet", data_schema=schema, errors=errors
        )

    async def async_step_scan_run(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Resolve the subnet scan once its progress task finishes.

        Entered only via the progress task's done-callback (with no user_input),
        so routing onward never leaks the scan form's input into the next step.
        """
        task = self._discovery_task
        if task is not None and not task.done():
            return self.async_show_progress(
                step_id="scan_run",
                progress_action="discovering",
                progress_task=task,
            )

        try:
            discovered = task.result() if task is not None else []
        except Exception:  # best-effort; fall back to the form
            _LOGGER.debug("Subnet scan failed", exc_info=True)
            discovered = []
        finally:
            self._discovery_task = None
            self._last_progress = 0.0

        self._discovered = {
            pdu.host: pdu for pdu in discovered if not self._already_configured(pdu)
        }
        if self._discovered:
            return self.async_show_progress_done(next_step_id="pick")
        self._scan_no_results = True
        return self.async_show_progress_done(next_step_id="scan_subnet")

    async def _async_scan(self) -> list[DiscoveredPdu]:
        """Run the subnet scan, pushing throttled progress to the bar."""

        def _progress(done: int, total: int) -> None:
            if not total:
                return
            fraction = done / total
            if fraction - self._last_progress >= 0.02 or done == total:
                self._last_progress = fraction
                self.async_update_progress(fraction)

        return await async_scan_hosts(
            self._scan_hosts,
            community=self._scan_community,
            port=self._scan_port,
            progress_cb=_progress,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one or more discovered PDUs to add at once."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._hosts = user_input[CONF_DEVICES]
            if self._hosts:
                # Auto-discovery uses defaults; a subnet scan may have set a
                # custom port/community, so keep self._conn if already populated.
                self._conn = self._conn or {
                    CONF_PORT: DEFAULT_PORT,
                    CONF_VERSION: DEFAULT_VERSION,
                }
                return await self.async_step_credentials()
            errors["base"] = "no_devices_selected"

        options = {
            host: f"{pdu.model} ({host})" for host, pdu in self._discovered.items()
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICES, default=list(options)): cv.multi_select(
                    options
                )
            }
        )
        return self.async_show_form(step_id="pick", data_schema=schema, errors=errors)

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect one or more PDUs by hand (the always-available fallback)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # The host field is a multi-value selector (one IP per row); still
            # tolerate someone pasting a separated list into a single row.
            hosts: list[str] = []
            for item in user_input.get(CONF_HOST) or []:
                hosts.extend(_parse_hosts(item))
            hosts = list(dict.fromkeys(hosts))
            if hosts:
                self._hosts = hosts
                self._conn = {
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_VERSION: user_input[CONF_VERSION],
                }
                return await self.async_step_credentials()
            errors["base"] = "no_hosts"

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): TextSelector(
                    TextSelectorConfig(multiple=True)
                ),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(CONF_VERSION, default=DEFAULT_VERSION): vol.In(
                    SNMP_VERSIONS
                ),
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect shared SNMP credentials and create an entry per reachable PDU."""
        errors: dict[str, str] = {}
        version = self._conn[CONF_VERSION]

        if user_input is not None:
            credentials = SnmpCredentials(
                version=version,
                community=user_input.get(CONF_COMMUNITY, DEFAULT_COMMUNITY),
                write_community=user_input.get(
                    CONF_WRITE_COMMUNITY, DEFAULT_WRITE_COMMUNITY
                ),
                username=user_input.get(CONF_USERNAME, ""),
                auth_protocol=user_input.get(CONF_AUTH_PROTOCOL, AUTH_NONE),
                auth_key=user_input.get(CONF_AUTH_KEY, ""),
                priv_protocol=user_input.get(CONF_PRIV_PROTOCOL, PRIV_NONE),
                priv_key=user_input.get(CONF_PRIV_KEY, ""),
            )
            entry_common = {**self._conn, **user_input}
            result = await self._async_create_for_hosts(credentials, entry_common)
            if result is not None:
                return result
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="credentials",
            data_schema=self._credentials_schema(version),
            errors=errors,
            description_placeholders={"count": str(len(self._hosts))},
        )

    async def _async_create_for_hosts(
        self, credentials: SnmpCredentials, entry_common: dict[str, Any]
    ) -> ConfigFlowResult | None:
        """Validate every host, then create one entry each. None => all failed."""
        port = self._conn[CONF_PORT]
        infos = await asyncio.gather(
            *(self._validate_or_none(host, port, credentials) for host in self._hosts)
        )
        valid = [
            (host, info, device_unique_id(info["serial"], info["mac"]))
            for host, info in zip(self._hosts, infos, strict=True)
            if info is not None
        ]
        valid = [(h, info, uid) for (h, info, uid) in valid if uid is not None]
        if not valid:
            return None

        # Fan out the extra PDUs as headless import flows (one entry each).
        for host, info, uid in valid[1:]:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_IMPORT},
                    data={
                        "unique_id": uid,
                        "title": _title(info),
                        "data": {CONF_HOST: host, **entry_common},
                    },
                )
            )

        host0, info0, uid0 = valid[0]
        await self.async_set_unique_id(uid0, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host0})
        return self.async_create_entry(
            title=_title(info0), data={CONF_HOST: host0, **entry_common}
        )

    async def _validate_or_none(
        self, host: str, port: int, credentials: SnmpCredentials
    ) -> dict[str, Any] | None:
        try:
            return await _validate(host, port, credentials)
        except (CannotConnect, NotCyberPower):
            _LOGGER.debug("Validation failed for %s", host, exc_info=True)
            return None

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Headless entry creation for additional bulk-added PDUs."""
        await self.async_set_unique_id(
            import_data["unique_id"], raise_on_progress=False
        )
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: import_data["data"][CONF_HOST]}
        )
        return self.async_create_entry(
            title=import_data["title"], data=import_data["data"]
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
