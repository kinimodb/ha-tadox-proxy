from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.const import ATTR_TEMPERATURE
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
    async_add_entities([TadoxProxyClimate(hass, name, source_entity_id)])


class TadoxProxyClimate(ClimateEntity):
    """Proxy a source climate entity (forward commands and mirror state)."""

    def __init__(self, hass: HomeAssistant, name: str, source_entity_id: str) -> None:
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._remove_listener = None

        self._attr_name = name
        self._attr_unique_id = f"{source_entity_id}_proxy"
        self._attr_should_poll = False

        # WICHTIG: In deiner HA-Version erwartet ClimateEntity dieses Attribut
        self._attr_temperature_unit = hass.config.units.temperature_unit

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
        attrs["source_entity_id"] = self._source_entity_id
        return attrs

    @property
    def hvac_mode(self):
        st = self._source_state()
        return None if st is None else st.state

    @property
    def hvac_modes(self):
        st = self._source_state()
        return [] if st is None else st.attributes.get("hvac_modes", [])

    @property
    def supported_features(self) -> int:
        st = self._source_state()
        return 0 if st is None else int(st.attributes.get("supported_features", 0))

    @property
    def current_temperature(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("current_temperature")

    @property
    def target_temperature(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("temperature")

    @property
    def target_temperature_low(self):
        st = self._source_state()
        return None if st is None else st.attributes.get(ATTR_TARGET_TEMP_LOW)

    @property
    def target_temperature_high(self):
        st = self._source_state()
        return None if st is None else st.attributes.get(ATTR_TARGET_TEMP_HIGH)

    @property
    def min_temp(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("min_temp")

    @property
    def max_temp(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("max_temp")

    @property
    def current_humidity(self):
        st = self._source_state()
        return None if st is None else st.attributes.get("current_humidity")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Forward target temperature to the source entity."""
        service_data: dict[str, Any] = {"entity_id": self._source_entity_id}
