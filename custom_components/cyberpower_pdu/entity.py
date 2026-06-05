"""Base entity for the CyberPower PDU integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import CyberPowerCoordinator


class CyberPowerEntity(CoordinatorEntity[CyberPowerCoordinator]):
    """Common base wiring every entity to the PDU device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CyberPowerCoordinator) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        info = coordinator.info
        # Identity is the serial number (preferred) or MAC — never the IP.
        self._device_id = coordinator.device_id or info.serial or info.mac
        connections = {(CONNECTION_NETWORK_MAC, info.mac)} if info.mac else set()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=info.model,
            name=coordinator.device_name,
            sw_version=info.firmware or None,
            serial_number=info.serial or None,
            configuration_url=f"http://{coordinator.host}",
        )
