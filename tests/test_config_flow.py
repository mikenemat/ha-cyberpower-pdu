"""Tests for the CyberPower PDU config flow (bulk discovery + manual fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cyberpower_pdu import const as c
from custom_components.cyberpower_pdu.config_flow import CONF_DEVICES
from custom_components.cyberpower_pdu.discovery import DiscoveredPdu

from .conftest import FakeSnmp

H1, H2, H3 = "192.0.2.50", "192.0.2.51", "192.0.2.52"
_DISCOVERED = [
    DiscoveredPdu(H1, "00:0c:15:00:00:01", "PDU41008", "SNAAA"),
    DiscoveredPdu(H2, "00:0c:15:00:00:02", "PDU41002", "SNBBB"),
    DiscoveredPdu(H3, "00:0c:15:00:00:03", "PDU41002", "SNCCC"),
]
_CREDS = {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"}
_MANUAL_CONN = {c.CONF_HOST: H1, c.CONF_PORT: 161, c.CONF_VERSION: c.VERSION_V1}


def _register_three(fake_snmp: FakeSnmp) -> None:
    fake_snmp.extras[H1] = FakeSnmp(serial="SNAAA", mac=b"\x00\x0c\x15\x00\x00\x01")
    fake_snmp.extras[H2] = FakeSnmp(serial="SNBBB", mac=b"\x00\x0c\x15\x00\x00\x02")
    fake_snmp.extras[H3] = FakeSnmp(serial="SNCCC", mac=b"\x00\x0c\x15\x00\x00\x03")


async def _start(hass: HomeAssistant, discovered: list[DiscoveredPdu]):
    """Start the flow and drive through the discovery progress step."""
    with patch(
        "custom_components.cyberpower_pdu.config_flow.async_discover_pdus",
        new=AsyncMock(return_value=discovered),
    ):
        result = await hass.config_entries.flow.async_init(
            c.DOMAIN, context={"source": SOURCE_USER}
        )
        while result["type"] in (
            FlowResultType.SHOW_PROGRESS,
            FlowResultType.SHOW_PROGRESS_DONE,
        ):
            await hass.async_block_till_done()
            result = await hass.config_entries.flow.async_configure(result["flow_id"])
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
    """With nothing discovered, the flow drops straight to manual entry."""
    result = await _start(hass, [])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
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


async def test_manual_cannot_connect(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """SNMP failure during manual setup surfaces cannot_connect."""
    result = await _start(hass, [])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_CONN
    )
    fake_snmp.fail = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
