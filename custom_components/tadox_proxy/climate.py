from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import (
    DOMAIN,
    CONF_SOURCE_ENTITY_ID,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_EXTERNAL_HUMIDITY_ENTITY_ID,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    CONF_PRESENCE_SENSOR_ENTITY_ID,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    source_entity_id = entry.data[CONF_SOURCE_ENTITY_ID]
    async_add_entities([TadoxProxyClimate(hass, entry, source_entity_id)])


class TadoxProxyClimate(ClimateEntity):
    """Proxy climate entity that mirrors another climate entity and forwards service calls.

    Additionally, it can use an external temperature sensor as the current temperature source.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False  # event-driven updates

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        source_entity_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id

        # Device name comes from device_info.name (entry.title)
        # Entity name becomes "<Device Name> Thermostat" in UI
        self._attr_name = "Thermostat"

        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._unsub_state_listener = None

    @property
    def device_info(self) -> DeviceInfo:
        """Expose a dedicated device for this proxy config entry."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="tado°",
            model="Tado X Proxy Thermostat",
        )

    def _source_state(self):
        return self.hass.states.get(self._source_entity_id)

    def _opt_entity_id(self, key: str) -> str | None:
        """Return the effective entity_id for an option/config key."""
        val = self._entry.options.get(key)
        if val:
            return val
        return self._entry.data.get(key)

    def _read_float_state(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        if st.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        try:
            return float(st.state)
        except (TypeError, ValueError):
            return None

    def _read_temperature_in_hass_units(self, entity_id: str | None) -> float | None:
        """Read a temperature sensor and best-effort convert to HA's configured temperature unit."""
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in (STATE_UNKNOWN, STATE_UN
