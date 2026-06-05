"""Diagnostics support for the CyberPower PDU integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUTH_KEY,
    CONF_COMMUNITY,
    CONF_PRIV_KEY,
    CONF_WRITE_COMMUNITY,
)
from .coordinator import CyberPowerConfigEntry

TO_REDACT = {CONF_COMMUNITY, CONF_WRITE_COMMUNITY, CONF_AUTH_KEY, CONF_PRIV_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CyberPowerConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "info": {
            "device_id": coordinator.device_id,
            "device_name": coordinator.device_name,
            "model": coordinator.info.model,
            "firmware": coordinator.info.firmware,
            "serial": coordinator.info.serial,
            "mac": coordinator.info.mac,
            "outlet_count": len(coordinator.info.outlets),
            "total_bank_id": coordinator.info.total_bank_id,
            "bank_ids": coordinator.info.bank_ids,
            "load_rows": coordinator.info.load_rows,
        },
        "data": {
            "outlet_states": data.outlet_states,
            "outlet_names": data.outlet_names,
            "banks": {bank: asdict(m) for bank, m in data.banks.items()},
        },
    }
