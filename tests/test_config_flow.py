"""Tests for the CyberPower PDU config flow."""

from __future__ import annotations

from custom_components.cyberpower_pdu import const as c
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .conftest import FakeSnmp


async def _finish_user_flow(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        c.DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_HOST: "192.0.2.50", c.CONF_PORT: 161, c.CONF_VERSION: c.VERSION_V1},
    )
    assert result["step_id"] == "credentials"
    return result


async def test_user_happy_path(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A full v1 user flow creates an entry."""
    result = await _finish_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PDU41008 (TESTSERIAL1)"
    assert result["data"][c.CONF_HOST] == "192.0.2.50"
    assert result["result"].unique_id == "00:0c:15:11:22:33"


async def test_cannot_connect(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """SNMP failure surfaces a cannot_connect error."""
    result = await _finish_user_flow(hass)
    fake_snmp.fail = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_not_cyberpower(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """A non-CyberPower responder is rejected."""
    result = await _finish_user_flow(hass)
    fake_snmp.not_cyberpower = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_cyberpower"}


async def test_dhcp_discovery(hass: HomeAssistant, fake_snmp: FakeSnmp) -> None:
    """DHCP discovery pre-fills host and completes."""
    info = DhcpServiceInfo(
        ip="192.0.2.50", hostname="pdu41008", macaddress="000c15112233"
    )
    result = await hass.config_entries.flow.async_init(
        c.DOMAIN, context={"source": SOURCE_DHCP}, data=info
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_HOST: "192.0.2.50", c.CONF_PORT: 161, c.CONF_VERSION: c.VERSION_V1},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {c.CONF_COMMUNITY: "public", c.CONF_WRITE_COMMUNITY: "private"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
