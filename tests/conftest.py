"""Fixtures and a fake SNMP backend for CyberPower PDU tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu import const as c
from custom_components.cyberpower_pdu.snmp import SnmpError

# 16-outlet, 2-bank PDU modelled on the real PDU41008. Outlet 12 = "LED", off.
_NAMES = {i: f"Outlet {i}" for i in range(1, 17)}
_NAMES[12] = "LED"


class FakeSnmp:
    """In-memory stand-in for CyberPowerSnmp used by tests."""

    def __init__(self, *args, **kwargs) -> None:
        self.fail = False
        self.not_cyberpower = False
        self.sets: list[tuple[str, int]] = []
        self.closed = False
        self._state = dict.fromkeys(range(1, 17), c.OUTLET_STATE_ON)
        self._state[12] = c.OUTLET_STATE_OFF

    # --- helpers -----------------------------------------------------------
    def _scalars(self) -> dict[str, object]:
        sysobj = "1.3.6.1.4.1.3808.1.1.3"
        if self.not_cyberpower:
            sysobj = "1.3.6.1.4.1.9.1.1"  # some other vendor
        return {
            c.OID_SYS_OBJECT_ID: sysobj,
            c.OID_IDENT_MODEL: "PDU41008",
            c.OID_IDENT_FW_REV: "1.2.4",
            c.OID_IDENT_SERIAL: "TESTSERIAL1",
            c.OID_IDENT_NAME: "PDU41008",
            c.OID_IDENT_OUTLET_COUNT: 16,
            c.OID_SYS_NAME: "PDU41008",
            c.OID_IF_PHYS_ADDRESS: b"\x00\x0c\x15\x11\x22\x33",
        }

    def _meter(self, oid: str) -> object | None:
        # rows: 1=device(bank0), 2=bank1, 3=bank2
        current = {1: 7, 2: 4, 3: 3}
        power = {1: 40, 2: 30, 3: 10}
        for row in (1, 2, 3):
            if oid == f"{c.OID_LOAD_CURRENT}.{row}":
                return current[row]
            if oid == f"{c.OID_LOAD_VOLTAGE}.{row}":
                return 2426
            if oid == f"{c.OID_LOAD_POWER}.{row}":
                return power[row]
        if oid == f"{c.OID_LOAD_APPARENT}.1":
            return 170
        if oid == f"{c.OID_LOAD_PF}.1":
            return 23
        if oid == f"{c.OID_LOAD_ENERGY}.1":
            return 13679
        return None

    # --- API ---------------------------------------------------------------
    async def get(self, oids: list[str]) -> dict[str, object]:
        if self.fail:
            raise SnmpError("boom")
        scalars = self._scalars()
        out: dict[str, object] = {}
        for oid in oids:
            if oid in scalars:
                out[oid] = scalars[oid]
            elif oid.startswith(f"{c.OID_OUTLET_STATUS_STATE}."):
                out[oid] = self._state[int(oid.rsplit(".", 1)[-1])]
            else:
                out[oid] = self._meter(oid)
        return out

    async def walk(self, base_oid: str) -> dict[str, object]:
        if self.fail:
            raise SnmpError("boom")
        if base_oid == c.OID_OUTLET_STATUS_NAME:
            return {f"{base_oid}.{i}": _NAMES[i] for i in range(1, 17)}
        if base_oid == c.OID_OUTLET_STATUS_BANK:
            return {f"{base_oid}.{i}": (1 if i <= 8 else 2) for i in range(1, 17)}
        if base_oid == c.OID_LOAD_BANK_ID:
            return {f"{base_oid}.1": 0, f"{base_oid}.2": 1, f"{base_oid}.3": 2}
        return {}

    async def set_int(self, oid: str, value: int) -> None:
        if self.fail:
            raise SnmpError("boom")
        self.sets.append((oid, value))
        if oid.startswith(f"{c.OID_OUTLET_CMD}."):
            idx = int(oid.rsplit(".", 1)[-1])
            self._state[idx] = (
                c.OUTLET_STATE_ON if value == c.OUTLET_CMD_ON else c.OUTLET_STATE_OFF
            )

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading the custom integration in tests."""
    yield


@pytest.fixture
def fake_snmp() -> Generator[FakeSnmp]:
    """Patch the SNMP client with a shared fake instance everywhere it is built."""
    fake = FakeSnmp()
    with (
        patch("custom_components.cyberpower_pdu.CyberPowerSnmp", return_value=fake),
        patch(
            "custom_components.cyberpower_pdu.config_flow.CyberPowerSnmp",
            return_value=fake,
        ),
    ):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry for the fake PDU."""
    return MockConfigEntry(
        domain=c.DOMAIN,
        title="PDU41008 (TESTSERIAL1)",
        unique_id="00:0c:15:11:22:33",
        data={
            c.CONF_HOST: "192.0.2.50",
            c.CONF_PORT: 161,
            c.CONF_VERSION: c.VERSION_V1,
            c.CONF_COMMUNITY: "public",
            c.CONF_WRITE_COMMUNITY: "private",
        },
    )
