"""Coordinator resilience: capped exponential backoff and clean recovery."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu.const import DEFAULT_SCAN_INTERVAL

from .conftest import FakeSnmp


async def test_backoff_and_recovery(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """On repeated failures the poll interval doubles to a 60s cap, then resets."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    base = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
    assert coordinator.update_interval == base

    fake_snmp.fail = True

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.update_interval == timedelta(seconds=30)  # 15 * 2

    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=60)  # 15 * 4

    await coordinator.async_refresh()
    assert coordinator.update_interval == timedelta(seconds=60)  # capped at 60

    # Network restored: next successful poll snaps straight back to the base.
    fake_snmp.fail = False
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.update_interval == base
