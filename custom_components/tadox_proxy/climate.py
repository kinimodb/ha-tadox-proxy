from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, CONF_NAME, CONF_SOURCE_ENTITY_ID


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    name = entry.data.get(CONF_NAME, entry.title)
    source_entity_id = entry.data[CONF_SOURCE_ENTITY_ID]
    async_add_entities([TadoxProxyClimate(hass, entry.entry_id, name, source_entity_id)])


class TadoxProxyClimate(ClimateEntity):
    """Proxy climate entity that mirrors another climate entity and forwards service calls."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry_id: str, name: str, source_entity_id: str) -> None:
        self.hass = hass
        self._entry_id = entry_id
        self._source_entity_id = source_entity_id
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_climate"

    @property
    def available(self) -> bool:
        return self.hass.states.get(self._source_entity_id) is not None

    def _source_state(self):
        return self.hass.states.get(self._source_entity_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source_entity_id": self._source_entity_id}

    @property
    def temperature_unit(self) -> str:
        return self.hass.config.units.temperature_unit


    @property
    def hvac_mode(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("hvac_mode")

    @property
    def hvac_modes(self):
        st = self._source_state()
        return [] if st is None else st.attributes.get("hvac_modes", [])

    @property
    def supported_features(self) -> ClimateEntityFeature:
        st = self._source_state()
        raw = 0 if st is None else st.attributes.get("supported_features", 0)
        try:
            return ClimateEntityFeature(int(raw))
        except (TypeError, ValueError):
            return ClimateEntityFeature(0)

    @property
    def current_temperature(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("current_temperature")

    @property
    def target_temperature(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("temperature")

    @property
    def target_temperature_high(self):
        st = self._source_state()
        return None if st is None else st.attributes.get(ATTR_TARGET_TEMP_HIGH)

    @property
    def target_temperature_low(self):
        st = self._source_state()
        return None if st is None else st.attributes.get(ATTR_TARGET_TEMP_LOW)

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {
                "entity_id": self._source_entity_id,
                ATTR_HVAC_MODE: hvac_mode,
            },
            blocking=True,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        service_data: dict[str, Any] = {"entity_id": self._source_entity_id}
        if ATTR_TEMPERATURE in kwargs:
            service_data[ATTR_TEMPERATURE] = kwargs[ATTR_TEMPERATURE]
        if ATTR_TARGET_TEMP_HIGH in kwargs:
            service_data[ATTR_TARGET_TEMP_HIGH] = kwargs[ATTR_TARGET_TEMP_HIGH]
        if ATTR_TARGET_TEMP_LOW in kwargs:
            service_data[ATTR_TARGET_TEMP_LOW] = kwargs[ATTR_TARGET_TEMP_LOW]

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            service_data,
            blocking=True,
        )
