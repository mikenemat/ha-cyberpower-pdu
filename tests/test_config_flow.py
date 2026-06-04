"""Tests for the CyberPower PDU config flow (scan picker + manual fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cyberpower_pdu import const as c
from custom_components.cyberpower_pdu.config_flow import CONF_DEVICE, MANUAL
from custom_components.cyberpower_pdu.discovery import DiscoveredPdu

from .conftest import FakeSnmp

_DISCOVERED = DiscoveredPdu(
    host="192.0.2.50", mac="00:0c:15:11:22:33", model="PDU41008", serial="TESTSERIAL1"
)
_CREDS = {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"}
_MANUAL_CONN = {
    c.CONF_HOST: "192.0.2.50",
    c.CONF_PORT: 161,
    c.CONF_VERSION: c.VERSION_V1,
}


async def _start(hass: HomeAssistant, discovered: list[DiscoveredPdu]):
    with patch(
        "custom_components.cyberpower_pdu.config_flow.async_discover_pdus",
        new=AsyncMock(return_value=discovered),
    ):
        return await hass.config_entries.flow.async_init(
            c.DOMAIN, context={"source": SOURCE_USER}
        )


async def test_discovery_pick_and_create(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """A discovered PDU is picked from the list and created."""
    result = await _start(hass, [_DISCOVERED])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # The picker field is keyed "device"; selecting the host string proceeds.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: "192.0.2.50"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][c.CONF_HOST] == "192.0.2.50"
    assert result["result"].unique_id == "00:0c:15:11:22:33"


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
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][c.CONF_HOST] == "192.0.2.50"


async def test_manual_option_from_picker(
    hass: HomeAssistant, fake_snmp: FakeSnmp
) -> None:
    """Choosing 'manual' in the picker opens the manual step."""
    result = await _start(hass, [_DISCOVERED])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE: MANUAL}
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


async def test_manual_not_cyberpower(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A non-CyberPower responder is rejected during manual setup."""
    result = await _start(hass, [])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _MANUAL_CONN
    )
    fake_snmp.not_cyberpower = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_cyberpower"}
