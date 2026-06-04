"""Tests for CyberPower PDU metering sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeSnmp


def _state_by_uid_suffix(hass: HomeAssistant, entry, suffix: str):
    reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.unique_id.endswith(suffix):
            return hass.states.get(e.entity_id)
    raise AssertionError(f"no entity with unique_id suffix {suffix}")


async def test_metering_values(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Device-total and per-bank metering values are scaled correctly."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # device totals (row 1): current 7->0.7A, voltage 2426->242.6V,
    # power 40W, energy 13679->1367.9 kWh, pf 23->0.23
    assert _state_by_uid_suffix(hass, config_entry, "_bank0_total_power").state == "40"
    assert (
        _state_by_uid_suffix(hass, config_entry, "_bank0_total_voltage").state
        == "242.6"
    )
    assert (
        _state_by_uid_suffix(hass, config_entry, "_bank0_total_current").state == "0.7"
    )
    assert (
        _state_by_uid_suffix(hass, config_entry, "_bank0_total_energy").state
        == "1367.9"
    )

    # per-bank power: bank1 30W, bank2 10W
    assert _state_by_uid_suffix(hass, config_entry, "_bank1_bank_power").state == "30"
    assert _state_by_uid_suffix(hass, config_entry, "_bank2_bank_power").state == "10"

    # power factor / apparent power are disabled-by-default; verify scaling on the
    # coordinator snapshot instead (23 -> 0.23, V*A apparent = 170 VA).
    coordinator = config_entry.runtime_data
    assert coordinator.data.banks[0].power_factor == 0.23
    assert coordinator.data.banks[0].apparent_power == 170
