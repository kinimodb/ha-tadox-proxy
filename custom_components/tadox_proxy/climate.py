"""Climate platform for tadox_proxy.

Fixes: platform must expose async_setup_entry for config-entry based setup.
Without it, Home Assistant cannot create entities and will mark existing ones as
"no longer provided".
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .regulation import PidRegulator, RegulationConfig

_LOGGER = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300  # 5 minutes


def _pick_first(d: dict[str, Any], keys: list[str]) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _as_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up tadox_proxy climate platform from a config entry."""

    # Be tolerant to key naming while we iterate on config flow.
    source_entity_id = _pick_first(
        entry.data,
        [
            "source_entity_id",
            "source",
            "source_climate",
            "source_climate_entity_id",
            "tado_entity_id",
            "entity_id",
        ],
    )

    if not source_entity_id:
        _LOGGER.error(
            "tadox_proxy entry %s has no source_entity_id in entry.data keys=%s",
            entry.entry_id,
            list(entry.data.keys()),
        )
        return

    # Options / data: external temperature sensor and control interval
    sensor_entity_id = _pick_first(
        entry.options,
        ["temperature_sensor", "temp_sensor", "sensor_entity_id", "external_sensor"],
    ) or _pick_first(
        entry.data,
        ["temperature_sensor", "temp_sensor", "sensor_entity_id", "external_sensor"],
    )

    interval_s = _pick_first(entry.options, ["control_interval_s", "interval_s", "interval"]) or DEFAULT_INTERVAL_S
    interval_s = int(_as_float(interval_s) or DEFAULT_INTERVAL_S)

    name = entry.title or "TadoX Proxy Thermostat"

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
        # Use a stable unique_id tied to the entry, to avoid entity orphaning on code changes.
        self._attr_unique_id = entry.unique_id or entry.entry_id

        self._source_entity_id = source_entity_id
        self._sensor_entity_id = sensor_entity_id
        self._interval = timedelta(seconds=max(30, interval_s))

        self._regulator = PidRegulator(RegulationConfig())

        # State
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = 21.0
        self._attr_current_temperature = None
        self._attr_available = True

        self._unsub_timer = None
        self._last_decision: dict[str, Any] = {}

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
        return {
            "source_entity_id": self._source_entity_id,
            "sensor_entity_id": self._sensor_entity_id,
            "control_interval_s": int(self._interval.total_seconds()),
            **self._last_decision,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Start periodic regulation loop
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

    def _read_current_temperature(self) -> Optional[float]:
        """Prefer external sensor if configured; fallback to source climate attribute."""
        # External sensor
        if self._sensor_entity_id:
            st = self.hass.states.get(self._sensor_entity_id)
            if st:
                val = _as_float(st.state)
                if val is not None:
                    return val

        # Fallback: read source climate current_temperature attribute
        src = self.hass.states.get(self._source_entity_id)
        if src:
            val = _as_float(src.attributes.get("current_temperature"))
            if val is not None:
                return val

        return None

    async def _async_regulation_cycle(self, *, trigger: str) -> None:
        """Compute and apply new target to source thermostat."""
        src_state = self.hass.states.get(self._source_entity_id)
        if src_state is None or src_state.state in ("unavailable", "unknown"):
            self._attr_available = False
            self._last_decision = {"last_trigger": trigger, "reason": "source_unavailable"}
            _LOGGER.warning("Source climate %s unavailable; proxy not regulating", self._source_entity_id)
            self.async_write_ha_state()
            return

        self._attr_available = True
        measured = self._read_current_temperature()
        self._attr_current_temperature = measured

        if measured is None:
            self._last_decision = {"last_trigger": trigger, "reason": "no_measurement"}
            _LOGGER.warning(
                "No usable temperature measurement (sensor=%s, source=%s); skipping regulation",
                self._sensor_entity_id,
                self._source_entity_id,
            )
            self.async_write_ha_state()
            return

        # Decide target
        if self._attr_hvac_mode == HVACMode.OFF:
            target = 5.0  # frost-safe target
            decision = {
                "last_trigger": trigger,
                "reason": "proxy_hvac_off",
                "target_c": target,
            }
        else:
            now = time.monotonic()
            result = self._regulator.step(
                user_setpoint_c=float(self._attr_target_temperature or 21.0),
                measured_temp_c=float(measured),
                now_ts_s=now,
            )
            target = float(result.target_c)
            decision = {
                "last_trigger": trigger,
                "reason": result.reason,
                "target_c": target,
                "output_delta_c": float(result.output_delta_c),
                "error_c": float(result.error_c),
                "p_c": float(result.p_c),
                "i_c": float(result.i_c),
                "d_c": float(result.d_c),
                "rate_limited": bool(result.rate_limited),
                "deadband_active": bool(result.deadband_active),
                "heating_on": bool(result.heating_on),
                "dtemp_dt_c_per_s": float(result.dtemp_dt_c_per_s),
            }

        self._last_decision = decision

        # Apply to source thermostat
        _LOGGER.debug(
            "tadox_proxy cycle (%s): measured=%.2f setpoint=%.2f -> target=%.2f (%s)",
            trigger,
            measured,
            float(self._attr_target_temperature or 0.0),
            target,
            decision.get("reason"),
        )

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": self._source_entity_id, "temperature": target},
            blocking=True,
        )

        self.async_write_ha_state()
