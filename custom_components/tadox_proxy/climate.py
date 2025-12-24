"""Climate platform for tadox_proxy.

Goals (current milestone):
- Provide exactly one proxy ClimateEntity per config entry.
- Use external temperature sensor as primary measurement (fallback to source climate).
- Send computed target temperature to the source climate via climate.set_temperature.
- Expose all relevant temperatures as readable attributes.
- Auto-clean stale entity_registry entries from earlier iterations (prevents "entity no longer provided").
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Optional, Tuple

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_SOURCE_ENTITY_ID,
)
from .regulation import PidRegulator, RegulationConfig

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300  # 5 minutes


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_attr_float(state_obj, keys: list[str]) -> Optional[float]:
    """Try multiple attribute names and return the first numeric value."""
    if state_obj is None:
        return None
    attrs = state_obj.attributes or {}
    for k in keys:
        if k in attrs:
            v = _as_float(attrs.get(k))
            if v is not None:
                return v
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up tadox_proxy climate platform from a config entry."""

    source_entity_id = entry.data.get(CONF_SOURCE_ENTITY_ID)
    if not source_entity_id:
        _LOGGER.error(
            "tadox_proxy entry %s missing %s in entry.data (keys=%s)",
            entry.entry_id,
            CONF_SOURCE_ENTITY_ID,
            list(entry.data.keys()),
        )
        return

    # External temperature sensor: prefer options; fallback to entry.data
    sensor_entity_id = entry.options.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID) or entry.data.get(
        CONF_EXTERNAL_TEMPERATURE_ENTITY_ID
    )

    interval_s = int(_as_float(entry.options.get("control_interval_s")) or DEFAULT_INTERVAL_S)
    interval_s = max(30, interval_s)

    # ---- Auto cleanup: remove stale entity_registry entries for this config entry ----
    # This prevents "Thermostat not available / entity no longer provided" leftovers.
    expected_unique_id = entry.unique_id or entry.entry_id
    ent_reg = er.async_get(hass)
    for entity_id, reg_entry in list(ent_reg.entities.items()):
        if (
            reg_entry.config_entry_id == entry.entry_id
            and reg_entry.domain == "climate"
            and reg_entry.platform == DOMAIN
            and reg_entry.unique_id != expected_unique_id
        ):
            _LOGGER.info(
                "Removing stale entity_registry entry %s (old unique_id=%s, expected=%s)",
                entity_id,
                reg_entry.unique_id,
                expected_unique_id,
            )
            ent_reg.async_remove(entity_id)

    name = entry.title or "Tado X Proxy"

    entity = TadoxProxyClimate(
        hass=hass,
        entry=entry,
        name=name,
        source_entity_id=str(source_entity_id),
        sensor_entity_id=str(sensor_entity_id) if sensor_entity_id else None,
        interval_s=interval_s,
    )
    async_add_entities([entity])


class TadoxProxyClimate(ClimateEntity):
    """Proxy thermostat entity backed by a source climate."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_should_poll = False  # we self-schedule

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry: ConfigEntry,
        name: str,
        source_entity_id: str,
        sensor_entity_id: Optional[str],
        interval_s: int,
    ) -> None:
        self.hass = hass
        self._entry = entry

        self._attr_name = name
        # Keep stable: ConfigFlow sets entry.unique_id = source_entity_id (duplicate protection)
        self._attr_unique_id = entry.unique_id or entry.entry_id

        self._source_entity_id = source_entity_id
        self._sensor_entity_id = sensor_entity_id
        self._interval = timedelta(seconds=interval_s)

        self._regulator = PidRegulator(RegulationConfig())

        # Core state
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = 21.0
        self._attr_current_temperature = None
        self._attr_available = True

        self._unsub_timer = None

        # Diagnostics snapshot (rendered as attributes)
        self._diag: dict[str, Any] = {}

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._attr_name,
            manufacturer="kinimodb",
            model="Tado X Proxy Thermostat",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Keep it readable and include all temperatures the user cares about.
        return {
            # Wiring
            "source_entity_id": self._source_entity_id,
            "external_temperature_entity_id": self._sensor_entity_id,
            "control_interval_s": int(self._interval.total_seconds()),
            # Diagnostics
            **self._diag,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _tick(_now) -> None:
            self.hass.async_create_task(self._async_regulation_cycle(trigger="timer"))

        self._unsub_timer = async_track_time_interval(self.hass, _tick, self._interval)

        # Run once immediately to initialize state
        self.hass.async_create_task(self._async_regulation_cycle(trigger="startup"))

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await super().async_will_remove_from_hass()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._attr_target_temperature = float(temp)
            await self._async_regulation_cycle(trigger="set_temperature")
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._attr_hvac_mode = hvac_mode
        await self._async_regulation_cycle(trigger="set_hvac_mode")
        self.async_write_ha_state()

    def _read_external_temperature(self) -> Optional[float]:
        """Read external temperature sensor state (if configured)."""
        if not self._sensor_entity_id:
            return None
        st = self.hass.states.get(self._sensor_entity_id)
        if not st:
            return None
        return _as_float(st.state)

    def _read_room_temperature(self) -> Tuple[Optional[float], str]:
        """Return (temperature, source). Prefer external sensor; fallback to source climate current_temperature."""
        ext = self._read_external_temperature()
        if ext is not None:
            return ext, "external_sensor"

        src = self.hass.states.get(self._source_entity_id)
        src_temp = _get_attr_float(src, ["current_temperature"])
        if src_temp is not None:
            return src_temp, "source_climate"

        return None, "unavailable"

    async def _async_regulation_cycle(self, *, trigger: str) -> None:
        """Compute and apply new target temperature to the source thermostat."""
        src_state = self.hass.states.get(self._source_entity_id)

        # Source availability
        if src_state is None or src_state.state in ("unavailable", "unknown"):
            self._attr_available = False
            self._diag = {
                "regula
