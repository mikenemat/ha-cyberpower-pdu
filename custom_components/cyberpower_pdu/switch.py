"""Outlet switches for the CyberPower PDU integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import OID_OUTLET_CMD, OUTLET_CMD_OFF, OUTLET_CMD_ON
from .coordinator import (
    CyberPowerConfigEntry,
    CyberPowerCoordinator,
    OutletInfo,
)
from .entity import CyberPowerEntity
from .snmp import SnmpError

# Delay before the confirming poll: the PDU needs ~1-2s to settle a relay.
_RECONCILE_DELAY = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CyberPowerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up outlet switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        CyberPowerOutletSwitch(coordinator, outlet)
        for outlet in coordinator.info.outlets
    )


class CyberPowerOutletSwitch(CyberPowerEntity, SwitchEntity):
    """A single switchable outlet."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: CyberPowerCoordinator, outlet: OutletInfo) -> None:
        """Initialize the outlet switch."""
        super().__init__(coordinator)
        self._outlet = outlet
        self._attr_name = outlet.name
        self._attr_unique_id = f"{self._device_id}_outlet_{outlet.index}"
        self._attr_extra_state_attributes = {
            "outlet_number": outlet.index,
            "bank": outlet.bank,
        }

    @property
    def is_on(self) -> bool | None:
        """Return True if the outlet is energized."""
        return self.coordinator.data.outlet_states.get(self._outlet.index)

    @property
    def available(self) -> bool:
        """Return availability for this specific outlet."""
        return (
            super().available
            and self._outlet.index in self.coordinator.data.outlet_states
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the outlet on."""
        await self._async_command(OUTLET_CMD_ON, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the outlet off."""
        await self._async_command(OUTLET_CMD_OFF, False)

    async def _async_command(self, command: int, optimistic: bool) -> None:
        oid = f"{OID_OUTLET_CMD}.{self._outlet.index}"
        try:
            await self.coordinator.snmp.set_int(oid, command)
        except SnmpError as err:
            raise HomeAssistantError(
                f"Failed to switch outlet {self._outlet.index}: {err}"
            ) from err

        # Optimistically reflect the change, then schedule a confirming poll
        # once the relay has had time to settle.
        self.coordinator.data.outlet_states[self._outlet.index] = optimistic
        self.async_write_ha_state()

        @callback
        def _reconcile(_now: Any) -> None:
            self.hass.async_create_task(self.coordinator.async_request_refresh())

        async_call_later(self.hass, _RECONCILE_DELAY, _reconcile)
