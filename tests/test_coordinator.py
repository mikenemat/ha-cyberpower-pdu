"""Coordinator resilience: capped exponential backoff and clean recovery."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu import const as c
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


async def test_self_heal_updates_ip_on_move(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """At the backoff ceiling, a MAC rescan relocates the PDU and updates the IP."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    with patch(
        "custom_components.cyberpower_pdu.coordinator.async_find_host_for_device",
        new=AsyncMock(return_value="192.0.2.250"),
    ) as find:
        fake_snmp.fail = True
        await coordinator.async_refresh()  # failure #1 -> 30s, no self-heal yet
        assert find.call_count == 0
        await coordinator.async_refresh()  # failure #2 -> 60s ceiling -> self-heal
        # Let the reload (triggered by the host update) settle successfully.
        fake_snmp.fail = False
        await hass.async_block_till_done()

    find.assert_awaited()
    assert config_entry.data[c.CONF_HOST] == "192.0.2.250"


async def test_topology_cached_across_reload(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Topology is walked once and reused from cache on reload (same serial)."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    walks_after_setup = fake_snmp.walk_calls
    assert walks_after_setup == 2  # outlet-bank walk + load-table walk

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    # No further walks — the structure came from the per-serial cache.
    assert fake_snmp.walk_calls == walks_after_setup
