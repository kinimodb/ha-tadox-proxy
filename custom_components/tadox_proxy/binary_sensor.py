"""Binary sensor entities for Tado X Proxy: sensor degradation and needs heat."""
from __future__ import annotations

import time

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device import proxy_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X Proxy binary sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    degraded = TadoXProxySensorDegradedBinarySensor(coordinator, entry)
    needs_heat = TadoXProxyNeedsHeatBinarySensor(coordinator, entry)
    # The climate entity pushes its state changes to both.
    coordinator.binary_sensor_entity = degraded
    coordinator.needs_heat_entity = needs_heat
    async_add_entities([degraded, needs_heat])


class TadoXProxySensorDegradedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating external temperature sensor degradation.

    Turns on when the external room temperature sensor becomes unavailable
    and the proxy is operating on stale or missing data.
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "sensor_degraded"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor degraded binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_sensor_degraded"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity appears on the same device."""
        return proxy_device_info(self._entry)

    @property
    def is_on(self) -> bool | None:
        """Return True when the external sensor is degraded."""
        climate = getattr(self.coordinator, "climate_entity", None)
        if climate is None:
            return None
        return climate._control.degraded

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return diagnostic attributes for the sensor degradation state."""
        climate = getattr(self.coordinator, "climate_entity", None)
        if climate is None:
            return {}

        control = climate._control
        attrs: dict[str, object] = {
            "grace_period_s": control.grace_s,
            "active_control_mode": control.mode,
        }

        # The last valid room reading, only while the sensor is missing.
        missing = control.missing_reading(time.time())
        if missing is not None:
            attrs["last_valid_reading"], attrs["last_valid_age_s"] = missing

        return attrs


class TadoXProxyNeedsHeatBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is on while the room needs heat.

    The signal for a boiler that only runs on demand.  The climate entity
    decides (``NeedsHeat`` in climate_controllers: hysteresis on error_c, off
    while the heating is off or the room sensor is degraded); this entity
    only shows the decision.  No device class: ``heat`` would show "Hot"
    for a room that is cold.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "needs_heat"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the needs-heat binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_needs_heat"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity appears on the same device."""
        return proxy_device_info(self._entry)

    @property
    def is_on(self) -> bool | None:
        """Return True while the room needs heat."""
        climate = getattr(self.coordinator, "climate_entity", None)
        if climate is None:
            return None
        return climate.needs_heat

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return the gap the decision follows (None while not regulating)."""
        climate = getattr(self.coordinator, "climate_entity", None)
        return {"error_c": climate.error_c if climate is not None else None}
