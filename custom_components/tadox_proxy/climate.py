from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TEMPERATURE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_NAME, CONF_SOURCE_ENTITY_ID


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the proxy climate entity."""
    name = entry.data[CONF_NAME]
    source_entity_id = entry.data[CONF_SOURCE_ENTITY_ID]
    async_add_entities([TadoxProxyClimate(hass, entry.entry_id, name, source_entity_id)])


class TadoxProxyClimate(ClimateEntity):
    """Proxy a source climate entity (forward commands and mirror state)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        name: str,
        source_entity_id: str,
    ) -> None:
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._remove_listener = None

        self._attr_name = name
        # Stabiler Unique ID: 1 Proxy pro Quell-Entity
        self._attr_unique_id = f"{source_entity_id}_proxy"
        self._attr_should_poll = False

    def _source_state(self):
        return self.hass.states.get(self._source_entity_id)

    @property
    def available(self) -> bool:
        st = self._source_state()
        return st is not None and st.state not in ("unavailable", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        st = self._source_state()
        if st is None:
            return {"source_entity_id": self._source_entity_id}
        attrs = dict(st.attributes)
        # Quelle als Attribut sichtbar machen (Debug/Transparenz)
        attrs["source_entity_id"] = self._source_entity_id
        return attrs

    @property
    def temperature_unit(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def hvac_mode(self):
        st = self._source_state()
        if st is None:
            return None
        # Bei climate-Entities ist der state typischerweise der hvac_mode ("heat", "off", ...)
        return st.state

    @property
    def hvac_modes(self):
        st = self._source_state()
        if st is None:
            return []
        return st.attributes.get("hvac_modes", [])

    @property
    def supported_features(self) -> int:
        st = self._source_state()
        if st is None:
            return 0
        return int(st.attributes.get("supported_features", 0))

    @property
    def current_temperature(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("current_temperature")

    @property
    def target_temperature(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("temperature")

    @property
    def target_temperature_low(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get(ATTR_TARGET_TEMP_LOW)

    @property
    def target_temperature_high(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get(ATTR_TARGET_TEMP_HIGH)

    @property
    def min_temp(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("min_temp")

    @property
    def max_temp(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("max_temp")

    @property
    def current_humidity(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("current_humidity")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Forward target temperature to the source entity."""
        # Unterstützt single-setpoint und (falls die Quelle es kann) low/high.
        service_data: dict[str, Any] = {"entity_id": self._source_entity_id}

        if ATTR_TEMPERATURE in kwargs:
            service_data[ATTR_TEMPERATURE] = kwargs[ATTR_TEMPERATURE]
        if ATTR_TARGET_TEMP_LOW in kwargs:
            service_data[ATTR_TARGET_TEMP_LOW] = kwargs[ATTR_TARGET_TEMP_LOW]
        if ATTR_TARGET_TEMP_HIGH in kwargs:
            service_data[ATTR_TARGET_TEMP_HIGH] = kwargs[ATTR_TARGET_TEMP_HIGH]

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            service_data,
            blocking=True,
        )

    async def async_set_hvac_mode(self, hvac_mode: str) -> None:
        """Forward HVAC mode to the source entity."""
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self._source_entity_id, ATTR_HVAC_MODE: hvac_mode},
            blocking=True,
        )

    async def async_added_to_hass(self) -> None:
        """Register listener to update when source changes."""
        @callback
        def _handle_source_change(event) -> None:
            self.async_write_ha_state()

        self._remove_listener = async_track_state_change_event(
            self.hass, [self._source_entity_id], _handle_source_change
        )
        # initial state push
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cleanup listener."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
