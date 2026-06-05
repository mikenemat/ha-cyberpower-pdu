"""Setup/teardown tests for the CyberPower PDU integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu.const import DOMAIN

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


async def test_device_identity_from_serial(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """The device is keyed on serial (not IP) and labelled from the SNMP name."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, "TESTSERIAL1") in device.identifiers  # serial, never the IP
    assert device.serial_number == "TESTSERIAL1"
    assert device.model == "PDU41008"
    assert device.name == "PDU41008"  # from SNMP device name
