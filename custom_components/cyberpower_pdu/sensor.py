"""Metering sensors for the CyberPower PDU integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    BankMeasurement,
    CyberPowerConfigEntry,
    CyberPowerCoordinator,
)
from .entity import CyberPowerEntity


@dataclass(frozen=True, kw_only=True)
class CyberPowerSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor over a metering row."""

    value_fn: Callable[[BankMeasurement], float | int | None]


# Sensors derived from the device-total metering row (bank id 0).
DEVICE_SENSORS: tuple[CyberPowerSensorDescription, ...] = (
    CyberPowerSensorDescription(
        key="total_power",
        translation_key="total_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.power,
    ),
    CyberPowerSensorDescription(
        key="total_current",
        translation_key="total_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.current,
    ),
    CyberPowerSensorDescription(
        key="total_voltage",
        translation_key="total_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.voltage,
    ),
    CyberPowerSensorDescription(
        key="total_apparent_power",
        translation_key="total_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.apparent_power,
    ),
    CyberPowerSensorDescription(
        key="total_power_factor",
        translation_key="total_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.power_factor,
    ),
    CyberPowerSensorDescription(
        key="total_energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda m: m.energy,
    ),
)

# Sensors created per metering bank (bank id >= 1).
BANK_SENSORS: tuple[CyberPowerSensorDescription, ...] = (
    CyberPowerSensorDescription(
        key="bank_power",
        translation_key="bank_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.power,
    ),
    CyberPowerSensorDescription(
        key="bank_current",
        translation_key="bank_current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.current,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CyberPowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up metering sensors."""
    coordinator = entry.runtime_data
    info = coordinator.info
    entities: list[CyberPowerSensor] = []

    # Whole-PDU totals come from the discovered total row (device row if present,
    # otherwise the sole metering row on a single-bank unit).
    if info.total_bank_id is not None:
        entities.extend(
            CyberPowerSensor(coordinator, description, info.total_bank_id)
            for description in DEVICE_SENSORS
        )

    # Per-bank sensors only when the PDU actually has more than one bank.
    if len(info.bank_ids) > 1:
        for bank_id in info.bank_ids:
            entities.extend(
                CyberPowerSensor(coordinator, description, bank_id)
                for description in BANK_SENSORS
            )

    async_add_entities(entities)


class CyberPowerSensor(CyberPowerEntity, SensorEntity):
    """A single metering value for the device total or one bank."""

    entity_description: CyberPowerSensorDescription

    def __init__(
        self,
        coordinator: CyberPowerCoordinator,
        description: CyberPowerSensorDescription,
        bank_id: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._bank_id = bank_id
        self._attr_unique_id = f"{self._device_id}_bank{bank_id}_{description.key}"
        if description.translation_key and description.translation_key.startswith(
            "bank_"
        ):
            self._attr_translation_placeholders = {"bank": str(bank_id)}

    @property
    def native_value(self) -> float | int | None:
        """Return the current metering value."""
        measurement = self.coordinator.data.banks.get(self._bank_id)
        if measurement is None:
            return None
        return self.entity_description.value_fn(measurement)

    @property
    def available(self) -> bool:
        """Return availability for this metering row."""
        return super().available and self._bank_id in self.coordinator.data.banks
