"""Data update coordinator for the CyberPower PDU integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_COMMUNITY,
    CONF_HOST,
    DEFAULT_COMMUNITY,
    DEVICE_BANK_ID,
    DOMAIN,
    MAX_BACKOFF_INTERVAL,
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
    STORAGE_VERSION,
)
from .discovery import async_find_host_for_device
from .snmp import CyberPowerSnmp, SnmpError, as_int, as_mac, as_str

_LOGGER = logging.getLogger(__name__)

type CyberPowerConfigEntry = ConfigEntry[CyberPowerCoordinator]


def device_unique_id(serial: str | None, mac: str | None) -> str | None:
    """Stable identity for a PDU: serial number first, then MAC. Never IP."""
    if serial:
        return serial
    if mac:
        return format_mac(mac)
    return None


@dataclass(slots=True)
class OutletInfo:
    """Non-mutable structure of a single outlet (index + bank). No label here."""

    index: int
    bank: int


@dataclass(slots=True)
class PduInfo:
    """Identity and fixed topology of the PDU (cached by serial)."""

    model: str
    firmware: str
    serial: str
    mac: str | None
    outlets: list[OutletInfo] = field(default_factory=list)
    # bank_id (0 = whole device on these PDUs) -> table row index.
    load_rows: dict[int, int] = field(default_factory=dict)

    @property
    def total_bank_id(self) -> int | None:
        """The metering row representing the whole PDU, if any."""
        if DEVICE_BANK_ID in self.load_rows:
            return DEVICE_BANK_ID
        if len(self.load_rows) == 1:
            return next(iter(self.load_rows))
        return None

    @property
    def bank_ids(self) -> list[int]:
        """Per-bank metering rows, excluding whichever row is the PDU total."""
        total = self.total_bank_id
        return sorted(b for b in self.load_rows if b != total)


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
    """A single poll snapshot, including mutable labels."""

    outlet_states: dict[int, bool] = field(default_factory=dict)
    outlet_names: dict[int, str] = field(default_factory=dict)
    device_name: str = ""
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
        self.device_id: str | None = None
        self.device_name = "CyberPower PDU"
        self._base_interval = timedelta(seconds=scan_interval)
        self._failures = 0
        self._healing = False
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    # --- self-heal ---------------------------------------------------------

    def _next_backoff(self) -> timedelta:
        """Exponential backoff capped at MAX_BACKOFF_INTERVAL."""
        seconds = min(
            self._base_interval.total_seconds() * (2**self._failures),
            MAX_BACKOFF_INTERVAL,
        )
        return timedelta(seconds=seconds)

    def _maybe_self_heal(self) -> None:
        """At the backoff ceiling, try to relocate a moved PDU by serial/MAC."""
        at_ceiling = self.update_interval == timedelta(seconds=MAX_BACKOFF_INTERVAL)
        if at_ceiling and (self.info.serial or self.info.mac) and not self._healing:
            self._healing = True
            self.config_entry.async_create_background_task(
                self.hass, self._async_self_heal(), "cyberpower_pdu_self_heal"
            )

    async def _async_self_heal(self) -> None:
        """Re-resolve the PDU's IP by serial/MAC and update the entry if moved."""
        try:
            community = self.config_entry.data.get(CONF_COMMUNITY, DEFAULT_COMMUNITY)
            new_host = await async_find_host_for_device(
                self.hass, self.info.serial, self.info.mac, community
            )
            if new_host and new_host != self.host:
                _LOGGER.info(
                    "CyberPower PDU %s moved from %s to %s; updating entry",
                    self.device_id,
                    self.host,
                    new_host,
                )
                # Changing the host fires the entry's update listener -> reload.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_HOST: new_host},
                )
        finally:
            self._healing = False

    # --- setup / topology --------------------------------------------------

    async def async_setup(self) -> None:
        """Read identity once; reuse cached topology when the serial matches."""
        try:
            ident = await self.snmp.get(
                [
                    OID_IDENT_MODEL,
                    OID_IDENT_FW_REV,
                    OID_IDENT_SERIAL,
                    OID_IDENT_NAME,
                    OID_SYS_NAME,
                    OID_IF_PHYS_ADDRESS,
                    OID_IDENT_OUTLET_COUNT,
                ]
            )
        except SnmpError as err:
            raise ConfigEntryNotReady(f"Error reading PDU identity: {err}") from err

        serial = as_str(ident.get(OID_IDENT_SERIAL)) or ""
        mac = as_mac(ident.get(OID_IF_PHYS_ADDRESS))
        model = as_str(ident.get(OID_IDENT_MODEL)) or "PDU"
        self.device_id = device_unique_id(serial, mac)
        self.device_name = (
            as_str(ident.get(OID_IDENT_NAME))
            or as_str(ident.get(OID_SYS_NAME))
            or model
        )

        outlets, load_rows = await self._async_topology(
            serial, as_int(ident.get(OID_IDENT_OUTLET_COUNT))
        )
        self.info = PduInfo(
            model=model,
            firmware=as_str(ident.get(OID_IDENT_FW_REV)) or "",
            serial=serial,
            mac=mac,
            outlets=outlets,
            load_rows=load_rows,
        )
        _LOGGER.debug(
            "PDU %s: model=%s outlets=%d banks=%s",
            self.device_id,
            model,
            len(outlets),
            self.info.bank_ids,
        )

    async def _async_topology(
        self, serial: str, outlet_count: int | None
    ) -> tuple[list[OutletInfo], dict[int, int]]:
        """Return (outlets, load_rows), reusing the cached structure by serial.

        The physical layout of a PDU never changes, so once we have read it for a
        given serial we reuse it on every reload. This keeps already-created
        entities stable and avoids a flaky topology walk re-deriving them.
        """
        cached = await self._store.async_load()
        if cached and serial and cached.get("serial") == serial:
            outlets = [
                OutletInfo(index=o["index"], bank=o["bank"]) for o in cached["outlets"]
            ]
            load_rows = {int(k): v for k, v in cached["load_rows"].items()}
            _LOGGER.debug("Reusing cached topology for serial %s", serial)
            return outlets, load_rows

        try:
            banks = await self.snmp.walk(OID_OUTLET_STATUS_BANK)
            load_bank_ids = await self.snmp.walk(OID_LOAD_BANK_ID)
        except SnmpError as err:
            raise ConfigEntryNotReady(f"Error reading PDU topology: {err}") from err

        outlets = [
            OutletInfo(index=_index_of(oid), bank=as_int(value) or 0)
            for oid, value in sorted(banks.items(), key=lambda kv: _index_of(kv[0]))
        ]
        load_rows: dict[int, int] = {}
        for oid, value in load_bank_ids.items():
            bank_id = as_int(value)
            if bank_id is not None:
                load_rows[bank_id] = _index_of(oid)

        # Guard against a partial walk so we never create a truncated entity set.
        if not outlets or (outlet_count and len(outlets) != outlet_count):
            raise ConfigEntryNotReady(
                f"Incomplete outlet topology: read {len(outlets)} of {outlet_count}"
            )
        if not load_rows:
            raise ConfigEntryNotReady("No metering rows discovered")

        if serial:
            await self._store.async_save(
                {
                    "serial": serial,
                    "outlets": [{"index": o.index, "bank": o.bank} for o in outlets],
                    "load_rows": {str(k): v for k, v in load_rows.items()},
                }
            )
        return outlets, load_rows

    # --- polling -----------------------------------------------------------

    async def _async_update_data(self) -> PduData:
        """Fetch outlet states, live labels, and per-bank metering in one poll."""
        total_bank_id = self.info.total_bank_id
        oids: list[str] = [OID_IDENT_NAME]
        for outlet in self.info.outlets:
            oids.append(f"{OID_OUTLET_STATUS_STATE}.{outlet.index}")
            oids.append(f"{OID_OUTLET_STATUS_NAME}.{outlet.index}")
        for bank_id, row in self.info.load_rows.items():
            oids.append(f"{OID_LOAD_CURRENT}.{row}")
            oids.append(f"{OID_LOAD_VOLTAGE}.{row}")
            oids.append(f"{OID_LOAD_POWER}.{row}")
            if bank_id == total_bank_id:
                oids.append(f"{OID_LOAD_APPARENT}.{row}")
                oids.append(f"{OID_LOAD_PF}.{row}")
                oids.append(f"{OID_LOAD_ENERGY}.{row}")

        try:
            values = await self.snmp.get(oids)
        except SnmpError as err:
            self._failures += 1
            self.update_interval = self._next_backoff()
            _LOGGER.debug(
                "PDU %s poll failed (#%d); backing off to %s",
                self.host,
                self._failures,
                self.update_interval,
            )
            self._maybe_self_heal()
            raise UpdateFailed(f"Error polling PDU: {err}") from err

        if self._failures:
            self._failures = 0
            self.update_interval = self._base_interval

        outlet_states: dict[int, bool] = {}
        outlet_names: dict[int, str] = {}
        for outlet in self.info.outlets:
            outlet_states[outlet.index] = (
                as_int(values.get(f"{OID_OUTLET_STATUS_STATE}.{outlet.index}"))
                == OUTLET_STATE_ON
            )
            outlet_names[outlet.index] = (
                as_str(values.get(f"{OID_OUTLET_STATUS_NAME}.{outlet.index}"))
                or f"Outlet {outlet.index}"
            )

        device_name = as_str(values.get(OID_IDENT_NAME)) or self.device_name
        self._async_sync_device_name(device_name)

        banks: dict[int, BankMeasurement] = {}
        for bank_id, row in self.info.load_rows.items():
            current = as_int(values.get(f"{OID_LOAD_CURRENT}.{row}"))
            voltage = as_int(values.get(f"{OID_LOAD_VOLTAGE}.{row}"))
            measurement = BankMeasurement(
                bank_id=bank_id,
                current=None if current is None else current / 10.0,
                voltage=None if voltage is None else voltage / 10.0,
                power=as_int(values.get(f"{OID_LOAD_POWER}.{row}")),
            )
            if bank_id == total_bank_id:
                pf = as_int(values.get(f"{OID_LOAD_PF}.{row}"))
                energy = as_int(values.get(f"{OID_LOAD_ENERGY}.{row}"))
                measurement.apparent_power = as_int(
                    values.get(f"{OID_LOAD_APPARENT}.{row}")
                )
                measurement.power_factor = None if pf is None else pf / 100.0
                measurement.energy = None if energy is None else energy / 10.0
            banks[bank_id] = measurement

        return PduData(
            outlet_states=outlet_states,
            outlet_names=outlet_names,
            device_name=device_name,
            banks=banks,
        )

    @callback
    def _async_sync_device_name(self, name: str) -> None:
        """Push a changed SNMP device name through to the device registry."""
        if not name or name == self.device_name:
            return
        self.device_name = name
        if not self.device_id:
            return
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get_device(identifiers={(DOMAIN, self.device_id)})
        if device and device.name != name:
            dev_reg.async_update_device(device.id, name=name)
