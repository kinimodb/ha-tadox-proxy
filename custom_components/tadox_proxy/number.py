"""Number entities for Tado X Proxy preset temperatures and boost duration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_BOOST_TARGET,
    CONF_BOOST_DURATION,
    CONF_AWAY_TARGET,
    CONF_FROST_PROTECTION_TARGET,
)
from .parameters import PresetConfig


@dataclass
class PresetNumberDescription:
    """Describes one preset temperature number entity."""

    conf_key: str
    default: float
    translation_key: str


# Ordered: most-used first (Comfort, Eco, Away, Boost, Frost Protection)
_PRESET_NUMBERS: tuple[PresetNumberDescription, ...] = (
    PresetNumberDescription(
        conf_key=CONF_COMFORT_TARGET,
        default=20.0,
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
        conf_key=CONF_BOOST_TARGET,
        default=PresetConfig.boost_target_c,
        translation_key="boost_target",
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
    entities: list[NumberEntity] = [
        PresetTemperatureNumber(coordinator, entry, desc)
        for desc in _PRESET_NUMBERS
    ]
    entities.append(BoostDurationNumber(coordinator, entry))
    async_add_entities(entities)


class PresetTemperatureNumber(CoordinatorEntity, NumberEntity):
    """A number entity representing one preset target temperature."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
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
        new_opts = {**self._entry.options, self._desc.conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self.async_write_ha_state()


class BoostDurationNumber(CoordinatorEntity, NumberEntity):
    """Number entity for the boost timer duration in minutes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_translation_key = "boost_duration"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the boost duration number entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{CONF_BOOST_DURATION}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info so this entity appears on the same device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
        )

    @property
    def native_value(self) -> float:
        """Return the current boost duration from options."""
        return self._entry.options.get(
            CONF_BOOST_DURATION, PresetConfig.boost_duration_min
        )

    async def async_set_native_value(self, value: float) -> None:
        """Persist the new boost duration to config entry options."""
        new_opts = {**self._entry.options, CONF_BOOST_DURATION: int(value)}
        self.hass.config_entries.async_update_entry(self._entry, options=new_opts)
        self.async_write_ha_state()
