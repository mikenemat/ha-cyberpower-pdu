"""Tests for the CyberPower PDU config flow (bulk discovery + manual fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.cyberpower_pdu import const as c
from custom_components.cyberpower_pdu.config_flow import CONF_DEVICES, CONF_SUBNET
from custom_components.cyberpower_pdu.discovery import DiscoveredPdu, parse_scan_targets

from .conftest import FakeSnmp

H1, H2, H3 = "192.0.2.50", "192.0.2.51", "192.0.2.52"
_DISCOVERED = [
    DiscoveredPdu(H1, "00:0c:15:00:00:01", "PDU41008", "SNAAA"),
    DiscoveredPdu(H2, "00:0c:15:00:00:02", "PDU41002", "SNBBB"),
    DiscoveredPdu(H3, "00:0c:15:00:00:03", "PDU41002", "SNCCC"),
]
_CREDS = {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"}
_MANUAL_CONN = {c.CONF_HOST: [H1], c.CONF_PORT: 161, c.CONF_VERSION: c.VERSION_V1}


def _register_three(fake_snmp: FakeSnmp) -> None:
    fake_snmp.extras[H1] = FakeSnmp(serial="SNAAA", mac=b"\x00\x0c\x15\x00\x00\x01")
    fake_snmp.extras[H2] = FakeSnmp(serial="SNBBB", mac=b"\x00\x0c\x15\x00\x00\x02")
    fake_snmp.extras[H3] = FakeSnmp(serial="SNCCC", mac=b"\x00\x0c\x15\x00\x00\x03")


async def _advance(hass: HomeAssistant, result: dict):
    """Drive a flow through any progress steps until it needs input."""
    while result["type"] in (
        FlowResultType.SHOW_PROGRESS,
        FlowResultType.SHOW_PROGRESS_DONE,
    ):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def _start(
    hass: HomeAssistant, discovered: list[DiscoveredPdu], scannable: bool = True
):
    """Start the flow and drive through the discovery progress step."""
    with (
        patch(
            "custom_components.cyberpower_pdu.config_flow.async_discover_pdus",
            new=AsyncMock(return_value=discovered),
        ),
        patch(
            "custom_components.cyberpower_pdu.config_flow.async_has_scannable_networks",
            new=AsyncMock(return_value=scannable),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            c.DOMAIN, context={"source": SOURCE_USER}
        )
        return await _advance(hass, result)


async def _open_manual(hass: HomeAssistant, result: dict):
    """From the fallback menu, open the manual-entry form."""
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "fallback"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"
    return result


async def test_bulk_add_all_discovered(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """Select all discovered PDUs and create an entry for each in one flow."""
    _register_three(fake_snmp)
    result = await _start(hass, _DISCOVERED)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    assert result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICES: [H1, H2, H3]}
    )
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(c.DOMAIN)
    assert {e.unique_id for e in entries} == {"SNAAA", "SNBBB", "SNCCC"}


async def test_bulk_add_subset(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """Selecting a subset only adds those PDUs."""
    _register_three(fake_snmp)
    result = await _start(hass, _DISCOVERED)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pick"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICES: [H2, H3]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(c.DOMAIN)
    assert {e.unique_id for e in entries} == {"SNBBB", "SNCCC"}


async def test_no_devices_falls_back_to_manual(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """With nothing discovered, the flow offers the fallback menu -> manual."""
    result = await _start(hass, [])
    result = await _open_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_CONN
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "TESTSERIAL1"


async def test_manual_via_menu(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """The menu's manual option opens the manual step even when devices exist."""
    _register_three(fake_snmp)
    result = await _start(hass, _DISCOVERED)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"


async def test_large_subnet_skips_to_fallback(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """A /21-or-larger subnet skips the auto-scan and offers the fallback menu."""
    result = await _start(hass, [], scannable=False)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "fallback"


async def test_manual_multiple_ips(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """The manual fallback accepts several IPs and adds one entry per PDU."""
    _register_three(fake_snmp)
    result = await _start(hass, [], scannable=False)
    result = await _open_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            # multi-value rows; one row also pasted as a separated list
            c.CONF_HOST: [H1, f"{H2}, {H3}"],
            c.CONF_PORT: 161,
            c.CONF_VERSION: c.VERSION_V1,
        },
    )
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(c.DOMAIN)
    assert {e.unique_id for e in entries} == {"SNAAA", "SNBBB", "SNCCC"}


async def test_manual_cannot_connect(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """SNMP failure during manual setup surfaces cannot_connect."""
    result = await _start(hass, [])
    result = await _open_manual(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_CONN
    )
    fake_snmp.fail = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


def _open_scan(hass: HomeAssistant, result: dict):
    """From the fallback menu, open the subnet-scan form."""
    return hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan_subnet"}
    )


async def test_scan_subnet_finds_and_adds(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """A user-specified subnet scan surfaces PDUs that auto-discovery can't see."""
    _register_three(fake_snmp)
    result = await _start(hass, [])  # fallback menu (nothing auto-discovered)
    result = await _open_scan(hass, result)
    assert result["step_id"] == "scan_subnet"
    with patch(
        "custom_components.cyberpower_pdu.config_flow.async_scan_hosts",
        new=AsyncMock(return_value=_DISCOVERED),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SUBNET: "192.0.2.0/24", c.CONF_COMMUNITY: "public", c.CONF_PORT: 161},
        )
        result = await _advance(hass, result)
    assert result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICES: [H1, H2, H3]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    await hass.async_block_till_done()
    entries = hass.config_entries.async_entries(c.DOMAIN)
    assert {e.unique_id for e in entries} == {"SNAAA", "SNBBB", "SNCCC"}


async def test_scan_subnet_invalid(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A malformed subnet is rejected with a clear error."""
    result = await _start(hass, [])
    result = await _open_scan(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SUBNET: "not-an-ip", c.CONF_COMMUNITY: "public", c.CONF_PORT: 161},
    )
    assert result["step_id"] == "scan_subnet"
    assert result["errors"] == {"base": "invalid_subnet"}


async def test_scan_subnet_too_large(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A range larger than the cap is rejected rather than swept."""
    result = await _start(hass, [])
    result = await _open_scan(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SUBNET: "10.0.0.0/20", c.CONF_COMMUNITY: "public", c.CONF_PORT: 161},
    )
    assert result["step_id"] == "scan_subnet"
    assert result["errors"] == {"base": "subnet_too_large"}


async def test_scan_subnet_no_results(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A scan that finds nothing returns to the form with a notice."""
    result = await _start(hass, [])
    result = await _open_scan(hass, result)
    with patch(
        "custom_components.cyberpower_pdu.config_flow.async_scan_hosts",
        new=AsyncMock(return_value=[]),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SUBNET: "192.0.2.0/24", c.CONF_COMMUNITY: "public", c.CONF_PORT: 161},
        )
        result = await _advance(hass, result)
    assert result["step_id"] == "scan_subnet"
    assert result["errors"] == {"base": "no_devices_found"}


def test_parse_scan_targets() -> None:
    """The target parser accepts CIDRs, ranges, shorthands, and single IPs."""
    assert parse_scan_targets("192.0.2.5") == ["192.0.2.5"]
    assert len(parse_scan_targets("192.0.2.0/24")) == 254
    assert parse_scan_targets("192.0.2.10-192.0.2.12") == [
        "192.0.2.10",
        "192.0.2.11",
        "192.0.2.12",
    ]
    assert parse_scan_targets("192.0.2.10-12") == [
        "192.0.2.10",
        "192.0.2.11",
        "192.0.2.12",
    ]
    with pytest.raises(ValueError):
        parse_scan_targets("nope")
    with pytest.raises(ValueError):
        parse_scan_targets("")
