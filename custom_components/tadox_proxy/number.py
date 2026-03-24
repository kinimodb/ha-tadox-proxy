"""Number entities for Tado X Proxy preset temperatures."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AWAY_TARGET,
    CONF_BOOST_TARGET,
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_FROST_PROTECTION_TARGET,
    DOMAIN,
)
from .parameters import PresetConfig

_LOGGER = logging.getLogger(__name__)


@dataclass
class PresetNumberDescription:
    """Describes one preset temperature number entity."""

    conf_key: str
    default: float
    translation_key: str


_PRESET_NUMBERS: tuple[PresetNumberDescription, ...] = (
    PresetNumberDescription(
        conf_key=CONF_BOOST_TARGET,
        default=PresetConfig.boost_target_c,
        translation_key="boost_target",
    ),
    PresetNumberDescription(
        conf_key=CONF_COMFORT_TARGET,
        default=PresetConfig.comfort_target_c,
        translation_key="comfort_target",
    ),
    PresetNumberDescription(
        conf_key=CONF_ECO_TARGET,
        default=PresetConfig.eco_target_c,
        translation_key="eco_target",
    ),
    PresetNumberDescription(
        conf_key=CONF_AWAY_TARGET,
        default=PresetConfig.away_target_c,
        translation_key="away_target",
    ),
    PresetNumberDescription(
        conf_key=CONF_FROST_PROTECTION_TARGET,
        default=PresetConfig.frost_protection_target_c,
        translation_key="frost_protection_target",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X Proxy number entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PresetTemperatureNumber(coordinator, entry, desc)
        for desc in _PRESET_NUMBERS
    )


class PresetTemperatureNumber(CoordinatorEntity, NumberEntity):
    """A number entity representing one preset target temperature."""

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 5.0
    _attr_native_max_value = 30.0
    _attr_native_step = 0.5

    def __init__(self, coordinator, entry: ConfigEntry, desc: PresetNumberDescription) -> None:
        """Initialize the preset temperature number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._desc = desc
        self._attr_unique_id = f"{entry.entry_id}_{desc.conf_key}"
        self._attr_translation_key = desc.translation_key

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity appears on the same device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
        )

    @property
    def native_value(self) -> float:
        """Return the current preset temperature from options."""
        return self._entry.options.get(self._desc.conf_key, self._desc.default)

    async def async_set_native_value(self, value: float) -> None:
        """Persist the new preset temperature to config entry options."""
        # Reject NaN / Inf – Python's max/min do not propagate NaN correctly
        # (nan comparisons return False, so max(5.0, nan) == nan in some paths),
        # and storing a non-finite value would silently corrupt preset logic.
        if not math.isfinite(value):
            _LOGGER.warning("Ignoring non-finite preset temperature value: %s", value)
            return
        # Clamp to configured bounds (service calls bypass frontend validation)
        value = max(self._attr_native_min_value, min(self._attr_native_max_value, value))
        new_opts = {**self._entry.options, self._desc.conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self.async_write_ha_state()
