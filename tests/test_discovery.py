"""Active discovery, multi-PDU, and dynamic-topology tests."""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu import const as c, discovery as d
from custom_components.cyberpower_pdu.discovery import DiscoveredPdu

from .conftest import FakeSnmp


def _entry(host: str, unique_id: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=c.DOMAIN,
        unique_id=unique_id,
        data={
            c.CONF_HOST: host,
            c.CONF_PORT: 161,
            c.CONF_VERSION: c.VERSION_V1,
            c.CONF_COMMUNITY: "public",
            c.CONF_WRITE_COMMUNITY: "private",
        },
    )


def _uids(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    reg = er.async_get(hass)
    return [e.unique_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)]


# --- discovery engine -------------------------------------------------------


async def test_discovery_probes_arp_live_hosts(hass: HomeAssistant) -> None:
    """Only ARP-live hosts are probed; non-PDU responders are filtered out."""
    net = ipaddress.ip_network("192.0.2.0/24")

    async def fake_probe(host, community):
        if host == "192.0.2.50":
            return DiscoveredPdu(host, "00:0c:15:11:22:33", "PDU41008", "SN1")
        return None

    with (
        patch.object(d, "_local_networks", AsyncMock(return_value=[net])),
        patch.object(
            d,
            "_read_arp_table",
            return_value={
                "192.0.2.50": "00:0c:15:11:22:33",
                "192.0.2.99": "de:ad:be:ef:00:01",
            },
        ),
        patch.object(d, "_probe_host", side_effect=fake_probe) as probe,
    ):
        found = await d.async_discover_pdus(hass)

    assert [p.host for p in found] == ["192.0.2.50"]
    probed = {call.args[0] for call in probe.call_args_list}
    assert probed == {"192.0.2.50", "192.0.2.99"}  # only ARP-live hosts


async def test_discovery_sweeps_when_arp_cold(hass: HomeAssistant) -> None:
    """An empty ARP cache falls back to a bounded subnet sweep."""
    net = ipaddress.ip_network("192.0.2.0/29")  # .1-.6

    async def fake_probe(host, community):
        if host == "192.0.2.3":
            return DiscoveredPdu(host, "00:0c:15:00:00:03", "PDU", "S")
        return None

    with (
        patch.object(d, "_local_networks", AsyncMock(return_value=[net])),
        patch.object(d, "_read_arp_table", return_value={}),
        patch.object(d, "_probe_host", side_effect=fake_probe) as probe,
    ):
        found = await d.async_discover_pdus(hass)

    assert [p.host for p in found] == ["192.0.2.3"]
    assert len(probe.call_args_list) == 6  # full sweep of the /29 host range


async def test_find_host_for_mac(hass: HomeAssistant) -> None:
    """MAC lookup returns the current IP regardless of case."""
    with patch.object(
        d,
        "async_discover_pdus",
        AsyncMock(
            return_value=[DiscoveredPdu("192.0.2.80", "00:0c:15:ab:cd:ef", "PDU", "S")]
        ),
    ):
        assert (
            await d.async_find_host_for_mac(hass, "00:0C:15:AB:CD:EF") == "192.0.2.80"
        )
        assert await d.async_find_host_for_mac(hass, "00:00:00:00:00:00") is None


# --- multi-PDU and dynamic topology ----------------------------------------


async def test_two_pdus_coexist(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Two distinct PDUs install as independent devices/entries."""
    fake_snmp.extras["192.0.2.51"] = FakeSnmp(  # type: ignore[attr-defined]
        serial="SN2", mac=b"\x00\x0c\x15\xaa\xbb\xcc", model="PDU41008B"
    )
    entry2 = _entry("192.0.2.51", "00:0c:15:aa:bb:cc")

    config_entry.add_to_hass(hass)
    entry2.add_to_hass(hass)
    for entry in (config_entry, entry2):
        if entry.state is ConfigEntryState.NOT_LOADED:
            await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert entry2.state is ConfigEntryState.LOADED

    dev_reg = dr.async_get(hass)
    assert len(dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id)) == 1
    assert len(dr.async_entries_for_config_entry(dev_reg, entry2.entry_id)) == 1

    assert sum(1 for u in _uids(hass, config_entry) if "_outlet_" in u) == 16
    assert sum(1 for u in _uids(hass, entry2) if "_outlet_" in u) == 16
    assert hass.states.async_entity_ids_count("switch") == 32


async def test_dynamic_outlet_count(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """An 8-outlet PDU yields exactly 8 switches (count is discovered)."""
    fake_snmp.extras["192.0.2.60"] = FakeSnmp(  # type: ignore[attr-defined]
        serial="SN8",
        mac=b"\x00\x0c\x15\x00\x00\x08",
        outlets={i: (f"O{i}", 1) for i in range(1, 9)},
        load_rows={0: 1},
    )
    entry = _entry("192.0.2.60", "00:0c:15:00:00:08")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids_count("switch") == 8


async def test_single_bank_total_only(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A single-bank PDU exposes totals and no per-bank sensors."""
    fake_snmp.extras["192.0.2.61"] = FakeSnmp(  # type: ignore[attr-defined]
        serial="SN1BANK",
        mac=b"\x00\x0c\x15\x00\x00\x01",
        load_rows={0: 1, 1: 2},  # device total + one bank
    )
    entry = _entry("192.0.2.61", "00:0c:15:00:00:01")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    uids = _uids(hass, entry)
    assert any(u.endswith("_total_power") for u in uids)
    assert not any("_bank_power" in u for u in uids)
    assert not any("_bank_current" in u for u in uids)


async def test_remove_pdu(
    hass: HomeAssistant, fake_snmp: FakeSnmp, config_entry: MockConfigEntry
) -> None:
    """Removing a PDU tears down its entities and closes the SNMP session."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids_count("switch") == 16

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_entity_ids_count("switch") == 0
    assert fake_snmp.closed is True
