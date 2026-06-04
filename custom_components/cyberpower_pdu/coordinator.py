"""Data update coordinator for the CyberPower PDU integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEVICE_BANK_ID,
    DOMAIN,
    OID_IDENT_FW_REV,
    OID_IDENT_MODEL,
    OID_IDENT_NAME,
    OID_IDENT_OUTLET_COUNT,
    OID_IDENT_SERIAL,
    OID_IF_PHYS_ADDRESS,
    OID_LOAD_APPARENT,
    OID_LOAD_BANK_ID,
    OID_LOAD_CURRENT,
    OID_LOAD_ENERGY,
    OID_LOAD_PF,
    OID_LOAD_POWER,
    OID_LOAD_VOLTAGE,
    OID_OUTLET_STATUS_BANK,
    OID_OUTLET_STATUS_NAME,
    OID_OUTLET_STATUS_STATE,
    OID_SYS_NAME,
    OUTLET_STATE_ON,
)
from .snmp import CyberPowerSnmp, SnmpError, as_int, as_mac, as_str

_LOGGER = logging.getLogger(__name__)

type CyberPowerConfigEntry = ConfigEntry[CyberPowerCoordinator]


@dataclass(slots=True)
class OutletInfo:
    """Static description of a single outlet."""

    index: int
    name: str
    bank: int


@dataclass(slots=True)
class PduInfo:
    """Static description of the PDU, read once at setup."""

    model: str
    firmware: str
    serial: str
    name: str
    mac: str | None
    outlets: list[OutletInfo] = field(default_factory=list)
    # bank_id (0 = whole device) -> table row index in the load table
    load_rows: dict[int, int] = field(default_factory=dict)

    @property
    def bank_ids(self) -> list[int]:
        """Metering bank ids excluding the device-total row."""
        return sorted(b for b in self.load_rows if b != DEVICE_BANK_ID)


@dataclass(slots=True)
class BankMeasurement:
    """Live measurement for one metering row (device total or a bank)."""

    bank_id: int
    current: float | None = None
    voltage: float | None = None
    power: int | None = None
    apparent_power: int | None = None
    power_factor: float | None = None
    energy: float | None = None


@dataclass(slots=True)
class PduData:
    """A single poll snapshot."""

    outlet_states: dict[int, bool] = field(default_factory=dict)
    banks: dict[int, BankMeasurement] = field(default_factory=dict)


def _index_of(oid: str) -> int:
    """Return the trailing numeric index of an OID string."""
    return int(oid.rsplit(".", 1)[-1])


class CyberPowerCoordinator(DataUpdateCoordinator[PduData]):
    """Polls a CyberPower PDU over SNMP."""

    info: PduInfo

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CyberPowerConfigEntry,
        snmp: CyberPowerSnmp,
        host: str,
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.snmp = snmp
        self.host = host

    async def async_setup(self) -> None:
        """Read the static identity and topology of the PDU once."""
        try:
            ident = await self.snmp.get(
                [
                    OID_IDENT_MODEL,
                    OID_IDENT_FW_REV,
                    OID_IDENT_SERIAL,
                    OID_IDENT_NAME,
                    OID_IDENT_OUTLET_COUNT,
                    OID_SYS_NAME,
                    OID_IF_PHYS_ADDRESS,
                ]
            )
            names = await self.snmp.walk(OID_OUTLET_STATUS_NAME)
            banks = await self.snmp.walk(OID_OUTLET_STATUS_BANK)
            load_bank_ids = await self.snmp.walk(OID_LOAD_BANK_ID)
        except SnmpError as err:
            raise ConfigEntryNotReady(f"Error reading PDU identity: {err}") from err

        outlets: list[OutletInfo] = []
        for oid, value in sorted(names.items(), key=lambda kv: _index_of(kv[0])):
            index = _index_of(oid)
            bank_oid = f"{OID_OUTLET_STATUS_BANK}.{index}"
            outlets.append(
                OutletInfo(
                    index=index,
                    name=as_str(value) or f"Outlet {index}",
                    bank=as_int(banks.get(bank_oid)) or 0,
                )
            )

        load_rows: dict[int, int] = {}
        for oid, value in load_bank_ids.items():
            bank_id = as_int(value)
            if bank_id is not None:
                load_rows[bank_id] = _index_of(oid)

        self.info = PduInfo(
            model=as_str(ident.get(OID_IDENT_MODEL)) or "PDU",
            firmware=as_str(ident.get(OID_IDENT_FW_REV)) or "",
            serial=as_str(ident.get(OID_IDENT_SERIAL)) or "",
            name=as_str(ident.get(OID_IDENT_NAME))
            or as_str(ident.get(OID_SYS_NAME))
            or "CyberPower PDU",
            mac=as_mac(ident.get(OID_IF_PHYS_ADDRESS)),
            outlets=outlets,
            load_rows=load_rows,
        )
        _LOGGER.debug(
            "PDU %s: model=%s fw=%s outlets=%d banks=%s",
            self.host,
            self.info.model,
            self.info.firmware,
            len(outlets),
            self.info.bank_ids,
        )

    async def _async_update_data(self) -> PduData:
        """Fetch outlet states and per-bank metering in one batched poll."""
        oids: list[str] = [
            f"{OID_OUTLET_STATUS_STATE}.{o.index}" for o in self.info.outlets
        ]
        for bank_id, row in self.info.load_rows.items():
            oids.append(f"{OID_LOAD_CURRENT}.{row}")
            oids.append(f"{OID_LOAD_VOLTAGE}.{row}")
            oids.append(f"{OID_LOAD_POWER}.{row}")
            if bank_id == DEVICE_BANK_ID:
                oids.append(f"{OID_LOAD_APPARENT}.{row}")
                oids.append(f"{OID_LOAD_PF}.{row}")
                oids.append(f"{OID_LOAD_ENERGY}.{row}")

        try:
            values = await self.snmp.get(oids)
        except SnmpError as err:
            raise UpdateFailed(f"Error polling PDU: {err}") from err

        outlet_states = {
            o.index: as_int(values.get(f"{OID_OUTLET_STATUS_STATE}.{o.index}"))
            == OUTLET_STATE_ON
            for o in self.info.outlets
        }

        banks: dict[int, BankMeasurement] = {}
        for bank_id, row in self.info.load_rows.items():
            current = as_int(values.get(f"{OID_LOAD_CURRENT}.{row}"))
            voltage = as_int(values.get(f"{OID_LOAD_VOLTAGE}.{row}"))
            power = as_int(values.get(f"{OID_LOAD_POWER}.{row}"))
            measurement = BankMeasurement(
                bank_id=bank_id,
                current=None if current is None else current / 10.0,
                voltage=None if voltage is None else voltage / 10.0,
                power=power,
            )
            if bank_id == DEVICE_BANK_ID:
                pf = as_int(values.get(f"{OID_LOAD_PF}.{row}"))
                energy = as_int(values.get(f"{OID_LOAD_ENERGY}.{row}"))
                measurement.apparent_power = as_int(
                    values.get(f"{OID_LOAD_APPARENT}.{row}")
                )
                measurement.power_factor = None if pf is None else pf / 100.0
                measurement.energy = None if energy is None else energy / 10.0
            banks[bank_id] = measurement

        return PduData(outlet_states=outlet_states, banks=banks)
