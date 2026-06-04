"""Setup/teardown tests for the CyberPower PDU integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import FakeSnmp


async def test_setup_and_unload(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """The entry loads, creates entities, and unloads cleanly."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    switches = hass.states.async_entity_ids("switch")
    assert len(switches) == 16

    # device-total + per-bank sensors exist
    sensors = hass.states.async_entity_ids("sensor")
    assert len(sensors) >= 6

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert fake_snmp.closed is True


async def test_setup_retries_on_snmp_failure(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """A device that is unreachable at setup raises ConfigEntryNotReady."""
    fake_snmp.fail = True
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
