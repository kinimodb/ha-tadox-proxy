from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import CONF_NAME, CONF_SOURCE_ENTITY_ID

# Optionen/Keys (werden später sauber nach const.py gezogen)
CONF_EXTERNAL_TEMPERATURE_ENTITY_ID = "external_temperature_entity_id"
CONF_EXTERNAL_HUMIDITY_ENTITY_ID = "external_humidity_entity_id"
CONF_WINDOW_SENSOR_ENTITY_ID = "window_sensor_entity_id"
CONF_PRESENCE_SENSOR_ENTITY_ID = "presence_sensor_entity_id"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    name = entry.data.get(CONF_NAME, entry.title)
    source_entity_id = entry.data[CONF_SOURCE_ENTITY_ID]
    async_add_entities([TadoxProxyClimate(hass, entry, name, source_entity_id)])


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
        name: str,
        source_entity_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._source_entity_id = source_entity_id
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_climate"

        self._unsub_state_listener = None

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
        if st is None or st.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        try:
            value = float(st.state)
        except (TypeError, ValueError):
            return None

        sensor_unit = st.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        hass_unit = self.hass.config.units.temperature_unit

        if sensor_unit and hass_unit and sensor_unit != hass_unit:
            try:
                value = TemperatureConverter.convert(value, sensor_unit, hass_unit)
            except Exception:
                # If conversion fails for any reason, fall back to raw numeric value.
                pass

        return value

    @property
    def available(self) -> bool:
        return self._source_state() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ext_temp_id = self._opt_entity_id(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        ext_hum_id = self._opt_entity_id(CONF_EXTERNAL_HUMIDITY_ENTITY_ID)
        win_id = self._opt_entity_id(CONF_WINDOW_SENSOR_ENTITY_ID)
        pres_id = self._opt_entity_id(CONF_PRESENCE_SENSOR_ENTITY_ID)

        return {
            "source_entity_id": self._source_entity_id,
            "external_temperature_entity_id": ext_temp_id,
            "external_temperature": self._read_temperature_in_hass_units(ext_temp_id),
            "external_humidity_entity_id": ext_hum_id,
            "external_humidity": self._read_float_state(ext_hum_id),
            "window_sensor_entity_id": win_id,
            "presence_sensor_entity_id": pres_id,
        }

    @property
    def temperature_unit(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def hvac_mode(self):
        st = self._source_state()
        if st is None:
            return None
        return st.attributes.get("hvac_mode") or st.state

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
        # Prefer external sensor if configured; fall back to source climate current_temperature
        ext_temp_id = self._opt_entity_id(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        ext_temp = self._read_temperature_in_hass_units(ext_temp_id)
        if ext_temp is not None:
            return ext_temp

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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        tracked: list[str] = [self._source_entity_id]

        ext_temp_id = self._opt_entity_id(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        ext_hum_id = self._opt_entity_id(CONF_EXTERNAL_HUMIDITY_ENTITY_ID)
        win_id = self._opt_entity_id(CONF_WINDOW_SENSOR_ENTITY_ID)
        pres_id = self._opt_entity_id(CONF_PRESENCE_SENSOR_ENTITY_ID)

        for eid in (ext_temp_id, ext_hum_id, win_id, pres_id):
            if eid:
                tracked.append(eid)

        @callback
        def _handle_state_change(event) -> None:
            self.async_write_ha_state()

        self._unsub_state_listener = async_track_state_change_event(
            self.hass, tracked, _handle_state_change
        )

        # Write once so attributes are present immediately
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_state_listener is not None:
            self._unsub_state_listener()
            self._unsub_state_listener = None
        await super().async_will_remove_from_hass()
