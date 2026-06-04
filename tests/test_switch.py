"""Tests for CyberPower PDU outlet switches."""

from __future__ import annotations

from datetime import timedelta

from custom_components.cyberpower_pdu import const as c
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from .conftest import FakeSnmp


async def _entity_for_outlet(
    hass: HomeAssistant, entry: MockConfigEntry, index: int
) -> str:
    reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.unique_id.endswith(f"_outlet_{index}"):
            return e.entity_id
    raise AssertionError(f"no entity for outlet {index}")


async def test_outlet_toggle(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Outlet 12 (LED) starts off, turns on, and turns off again."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    led = await _entity_for_outlet(hass, config_entry, 12)
    assert hass.states.get(led).state == STATE_OFF
    assert hass.states.get(led).attributes["bank"] == 2

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": led}, blocking=True
    )
    await hass.async_block_till_done()
    # optimistic update
    assert hass.states.get(led).state == STATE_ON
    assert (f"{c.OID_OUTLET_CMD}.12", c.OUTLET_CMD_ON) in fake_snmp.sets

    # flush the delayed reconcile poll; device now reports on
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()
    assert hass.states.get(led).state == STATE_ON

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": led}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(led).state == STATE_OFF
    assert (f"{c.OID_OUTLET_CMD}.12", c.OUTLET_CMD_OFF) in fake_snmp.sets


async def test_other_outlets_on(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Outlet 1 reports on; bank attribute reflects bank 1."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    outlet1 = await _entity_for_outlet(hass, config_entry, 1)
    state = hass.states.get(outlet1)
    assert state.state == STATE_ON
    assert state.attributes["bank"] == 1
