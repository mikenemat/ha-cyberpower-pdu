"""The CyberPower PDU integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
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
)
from .coordinator import CyberPowerConfigEntry, CyberPowerCoordinator
from .snmp import CyberPowerSnmp, SnmpCredentials

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


def _credentials_from_entry(entry: CyberPowerConfigEntry) -> SnmpCredentials:
    """Build SNMP credentials from a config entry."""
    data = entry.data
    return SnmpCredentials(
        version=data.get(CONF_VERSION, DEFAULT_VERSION),
        community=data.get(CONF_COMMUNITY, DEFAULT_COMMUNITY),
        write_community=data.get(CONF_WRITE_COMMUNITY, DEFAULT_WRITE_COMMUNITY),
        username=data.get(CONF_USERNAME, ""),
        auth_protocol=data.get(CONF_AUTH_PROTOCOL, "none"),
        auth_key=data.get(CONF_AUTH_KEY, ""),
        priv_protocol=data.get(CONF_PRIV_PROTOCOL, "none"),
        priv_key=data.get(CONF_PRIV_KEY, ""),
    )


async def async_setup_entry(hass: HomeAssistant, entry: CyberPowerConfigEntry) -> bool:
    """Set up CyberPower PDU from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    snmp = CyberPowerSnmp(host, port, _credentials_from_entry(entry))
    coordinator = CyberPowerCoordinator(hass, entry, snmp, host, scan_interval)

    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CyberPowerConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.snmp.close()
    return unload_ok


async def _async_reload_entry(
    hass: HomeAssistant, entry: CyberPowerConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
