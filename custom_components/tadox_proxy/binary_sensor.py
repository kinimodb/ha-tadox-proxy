"""Binary sensor entity for Tado X Proxy sensor degradation."""
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X Proxy binary sensor entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity = TadoXProxySensorDegradedBinarySensor(coordinator, entry)
    coordinator.binary_sensor_entity = entity
    async_add_entities([entity])


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
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when the external sensor is degraded."""
        climate = getattr(self.coordinator, "climate_entity", None)
        if climate is None:
            return None
        return climate._sensor_degraded

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return diagnostic attributes for the sensor degradation state."""
        climate = getattr(self.coordinator, "climate_entity", None)
        if climate is None:
            return {}

        attrs: dict[str, object] = {
            "grace_period_s": climate._sensor_grace_s,
        }

        if climate._last_valid_room_temp is not None:
            attrs["last_valid_reading"] = climate._last_valid_room_temp
            if climate._last_valid_room_temp_ts > 0:
                attrs["last_valid_age_s"] = int(
                    time.time() - climate._last_valid_room_temp_ts
                )

        return attrs
