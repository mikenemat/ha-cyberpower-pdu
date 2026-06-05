"""Fixtures and a configurable fake SNMP backend for CyberPower PDU tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import ClassVar
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cyberpower_pdu import const as c
from custom_components.cyberpower_pdu.snmp import SnmpError

DEFAULT_MAC = b"\x00\x0c\x15\x11\x22\x33"
DEFAULT_SERIAL = "TESTSERIAL1"


def _default_outlets() -> dict[int, tuple[str, int]]:
    """16 outlets across 2 banks; outlet 12 named LED (matches the real PDU)."""
    outlets = {i: (f"Outlet {i}", 1 if i <= 8 else 2) for i in range(1, 17)}
    outlets[12] = ("LED", 2)
    return outlets


class FakeSnmp:
    """In-memory stand-in for CyberPowerSnmp; topology is fully configurable."""

    # Per-row metering values (row -> value); rows not listed get a generic value.
    _CURRENT: ClassVar[dict[int, int]] = {1: 7, 2: 4, 3: 3}
    _POWER: ClassVar[dict[int, int]] = {1: 40, 2: 30, 3: 10}

    def __init__(
        self,
        *,
        serial: str = DEFAULT_SERIAL,
        mac: bytes = DEFAULT_MAC,
        model: str = "PDU41008",
        outlets: dict[int, tuple[str, int]] | None = None,
        load_rows: dict[int, int] | None = None,
        sys_object_id: str = "1.3.6.1.4.1.3808.1.1.3",
    ) -> None:
        self.fail = False
        self.not_cyberpower = False
        self.sets: list[tuple[str, int]] = []
        self.closed = False
        self.walk_calls = 0
        self._serial = serial
        self._mac = mac
        self._model = model
        self._sysobj = sys_object_id
        self._outlets = outlets if outlets is not None else _default_outlets()
        # bank_id -> row; default is device-total(0) + two banks.
        self._load_rows = load_rows if load_rows is not None else {0: 1, 1: 2, 2: 3}
        self._state = dict.fromkeys(self._outlets, c.OUTLET_STATE_ON)
        if 12 in self._state:
            self._state[12] = c.OUTLET_STATE_OFF
        if 0 in self._load_rows:
            self._total_row: int | None = self._load_rows[0]
        elif len(self._load_rows) == 1:
            self._total_row = next(iter(self._load_rows.values()))
        else:
            self._total_row = None

    def _scalars(self) -> dict[str, object]:
        sysobj = "1.3.6.1.4.1.9.1.1" if self.not_cyberpower else self._sysobj
        return {
            c.OID_SYS_OBJECT_ID: sysobj,
            c.OID_IDENT_MODEL: self._model,
            c.OID_IDENT_FW_REV: "1.2.4",
            c.OID_IDENT_SERIAL: self._serial,
            c.OID_IDENT_NAME: self._model,
            c.OID_SYS_NAME: self._model,
            c.OID_IF_PHYS_ADDRESS: self._mac,
            c.OID_IDENT_OUTLET_COUNT: len(self._outlets),
        }

    def _meter(self, oid: str) -> object | None:
        for row in self._load_rows.values():
            if oid == f"{c.OID_LOAD_CURRENT}.{row}":
                return self._CURRENT.get(row, 5)
            if oid == f"{c.OID_LOAD_VOLTAGE}.{row}":
                return 2426
            if oid == f"{c.OID_LOAD_POWER}.{row}":
                return self._POWER.get(row, 10)
        if self._total_row is not None:
            if oid == f"{c.OID_LOAD_APPARENT}.{self._total_row}":
                return 170
            if oid == f"{c.OID_LOAD_PF}.{self._total_row}":
                return 23
            if oid == f"{c.OID_LOAD_ENERGY}.{self._total_row}":
                return 13679
        return None

    async def get(self, oids: list[str]) -> dict[str, object]:
        if self.fail:
            raise SnmpError("boom")
        scalars = self._scalars()
        out: dict[str, object] = {}
        for oid in oids:
            if oid in scalars:
                out[oid] = scalars[oid]
            elif oid.startswith(f"{c.OID_OUTLET_STATUS_STATE}."):
                out[oid] = self._state.get(int(oid.rsplit(".", 1)[-1]))
            elif oid.startswith(f"{c.OID_OUTLET_STATUS_NAME}."):
                idx = int(oid.rsplit(".", 1)[-1])
                out[oid] = self._outlets.get(idx, (f"Outlet {idx}", 0))[0]
            else:
                out[oid] = self._meter(oid)
        return out

    async def walk(self, base_oid: str) -> dict[str, object]:
        if self.fail:
            raise SnmpError("boom")
        self.walk_calls += 1
        if base_oid == c.OID_OUTLET_STATUS_NAME:
            return {f"{base_oid}.{i}": n for i, (n, _b) in self._outlets.items()}
        if base_oid == c.OID_OUTLET_STATUS_BANK:
            return {f"{base_oid}.{i}": b for i, (_n, b) in self._outlets.items()}
        if base_oid == c.OID_LOAD_BANK_ID:
            return {f"{base_oid}.{row}": bid for bid, row in self._load_rows.items()}
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
    """Patch CyberPowerSnmp with a per-host fake.

    The default instance answers for any host; register additional devices via
    ``fake_snmp.extras[host] = FakeSnmp(...)`` to model multiple PDUs.
    """
    fake = FakeSnmp()
    extras: dict[str, FakeSnmp] = {}
    fake.extras = extras  # type: ignore[attr-defined]

    def factory(host, port=None, credentials=None):
        return extras.get(host, fake)

    with (
        patch("custom_components.cyberpower_pdu.CyberPowerSnmp", side_effect=factory),
        patch(
            "custom_components.cyberpower_pdu.config_flow.CyberPowerSnmp",
            side_effect=factory,
        ),
    ):
        yield fake


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured entry for the default fake PDU."""
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
