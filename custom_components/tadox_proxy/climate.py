"""Climate platform for tadox_proxy.

Single proxy ClimateEntity per config entry:
- reads room temperature (external sensor preferred; fallback to source climate)
- runs local PID regulation (regulation.py)
- commands the source climate via climate.set_temperature
- exposes clear diagnostics + all relevant temperatures

Defaults and tuning knobs are sourced from:
- custom_components/tadox_proxy/parameters.py
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any, Optional, Tuple

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, CONF_SOURCE_ENTITY_ID
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    FROST_PROTECT_C,
    WILL_HEAT_EPS_C,
    RegulationConfig,
)
from .regulation import PidRegulator

_LOGGER = logging.getLogger(__name__)


def _as_float(value: Any) -> Optional[float]:
    """Parse HA state/attribute value to float."""
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
        v = _as_float(attrs.get(k))
        if v is not None:
            return v
    return None


def _clamp(value: float, vmin: float, vmax: float) -> float:
    return max(vmin, min(vmax, value))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the climate entity from a config entry."""
    source_entity_id = entry.data.get(CONF_SOURCE_ENTITY_ID)
    if not source_entity_id:
        _LOGGER.error(
            "tadox_proxy: missing %s in entry.data for entry_id=%s (keys=%s)",
            CONF_SOURCE_ENTITY_ID,
            entry.entry_id,
            list(entry.data.keys()),
        )
        return

    external_temp_entity_id = (
        entry.options.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        or entry.data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
    )

    interval_s = int(_as_float(entry.options.get("control_interval_s")) or DEFAULT_CONTROL_INTERVAL_S)
    interval_s = max(30, interval_s)

    # Auto-clean stale climate entities from previous iterations (prevents "no longer provided")
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
                "tadox_proxy: removing stale entity_registry entry %s (unique_id=%s, expected=%s)",
                entity_id,
                reg_entry.unique_id,
                expected_unique_id,
            )
            ent_reg.async_remove(entity_id)

    name = entry.title or "Tado X Proxy"

    async_add_entities(
        [
            TadoxProxyClimate(
                hass=hass,
                entry=entry,
                name=name,
                source_entity_id=str(source_entity_id),
                external_temp_entity_id=str(external_temp_entity_id) if external_temp_entity_id else None,
                interval_s=interval_s,
            )
        ]
    )


class TadoxProxyClimate(ClimateEntity):
    """Proxy thermostat entity backed by a source climate."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_should_poll = False
    _attr_min_temp = 7.0
    _attr_max_temp = 35.0

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        entry: ConfigEntry,
        name: str,
        source_entity_id: str,
        external_temp_entity_id: Optional[str],
        interval_s: int,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_name = name
        self._attr_unique_id = entry.unique_id or entry.entry_id

        self._source_entity_id = source_entity_id
        self._external_temp_entity_id = external_temp_entity_id

        self._interval = timedelta(seconds=interval_s)
        self._unsub_timer = None

        # Regulation engine (defaults from parameters.py)
        self._regulator = PidRegulator(RegulationConfig())

        # Core state
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = 21.0
        self._attr_current_temperature: Optional[float] = None
        self._attr_available = True

        # Diagnostics (exposed as attributes)
        self._diag: dict[str, Any] = {}
        self._last_command_target_c: Optional[float] = None

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
            # Wiring
            "source_entity_id": self._source_entity_id,
            "external_temperature_entity_id": self._external_temp_entity_id,
            "control_interval_s": int(self._interval.total_seconds()),
            # Last command (post-mapping)
            "tado_command_target_c": self._last_command_target_c,
            # Diagnostics snapshot
            **self._diag,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Allow immediate heating on first cycle (avoid initial min_off_hold)
        try:
            now = time.monotonic()
            if self._regulator.state.heating_state_change_ts_s is None:
                self._regulator.state.heating_state_change_ts_s = now - float(self._regulator.config.min_off_s)
        except Exception:
            _LOGGER.debug("tadox_proxy: unable to prime min_off timer", exc_info=True)

        @callback
        def _tick(_now) -> None:
            self.hass.async_create_task(self._async_regulation_cycle(trigger="timer"))

        self._unsub_timer = async_track_time_interval(self.hass, _tick, self._interval)
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
        if not self._external_temp_entity_id:
            return None
        st = self.hass.states.get(self._external_temp_entity_id)
        if not st:
            return None
        return _as_float(st.state)

    def _read_room_temperature(self) -> Tuple[Optional[float], str]:
        """Return (temperature, source). Prefer external sensor; fallback to source climate."""
        ext = self._read_external_temperature()
        if ext is not None:
            return ext, "external_sensor"

        src_state = self.hass.states.get(self._source_entity_id)
        src_temp = _get_attr_float(src_state, ["current_temperature"])
        if src_temp is not None:
            return src_temp, "tado_internal"

        return None, "unavailable"

    def _apply_tadox_mapping(
        self,
        *,
        target_c: float,
        heating_request: bool,
        rate_limited: bool,
        tado_internal_temp_c: Optional[float],
    ) -> tuple[float, Optional[str]]:
        """Apply Tado X mapping on top of computed target.

        We regulate based on room temperature, but the TRV opens/closes based on its internal temp.
        Mapping enforces margins relative to tado_internal_temp_c to make "heat request" actually open.
        """
        cfg = self._regulator.config
        map_cfg = cfg.tadox_mapping

        if not map_cfg.enabled or tado_internal_temp_c is None:
            return target_c, None

        mapped = target_c
        reason: Optional[str] = None

        # Clamp basis always to regulator absolute limits
        mapped = _clamp(mapped, cfg.min_target_c, cfg.max_target_c)

        if heating_request and map_cfg.enforce_open_on_request:
            # Avoid bypassing regulator rate-limit for increases; otherwise we could spam.
            if rate_limited:
                return mapped, "mapping_skipped_rate_limited"

            min_open = float(tado_internal_temp_c) + float(map_cfg.open_margin_c)
            max_allowed = min(float(map_cfg.max_open_target_c), float(cfg.max_target_c))
            if mapped < min_open:
                mapped = _clamp(min_open, cfg.min_target_c, max_allowed)
                reason = "enforce_open_margin"
        elif (not heating_request) and map_cfg.enforce_close_on_no_request:
            # For decreases, we allow even during rate limiting (safe + helps stop heating).
            max_close = float(tado_internal_temp_c) - float(map_cfg.close_margin_c)
            if mapped > max_close:
                mapped = _clamp(max_close, cfg.min_target_c, cfg.max_target_c)
                reason = "enforce_close_margin"

        # Final safety clamp
        mapped = _clamp(mapped, cfg.min_target_c, cfg.max_target_c)
        return mapped, reason

    async def _async_regulation_cycle(self, *, trigger: str) -> None:
        src_state = self.hass.states.get(self._source_entity_id)
        if src_state is None or src_state.state in ("unavailable", "unknown"):
            self._attr_available = False
            self._diag = {
                "regulation_trigger": trigger,
                "regulation_status": "skipped",
                "regulation_reason": "source_unavailable",
            }
            self.async_write_ha_state()
            return

        self._attr_available = True

        external_temp_c = self._read_external_temperature()
        room_temp_c, room_temp_source = self._read_room_temperature()
        self._attr_current_temperature = room_temp_c  # measurement used for regulation

        tado_internal_temp_c = _get_attr_float(src_state, ["current_temperature"])
        tado_current_setpoint_c = _get_attr_float(src_state, ["temperature", "target_temperature"])

        if room_temp_c is None:
            self._diag = {
                "regulation_trigger": trigger,
                "regulation_status": "skipped",
                "regulation_reason": "no_room_temperature",
                "room_temperature_source": room_temp_source,
                "external_temperature_c": external_temp_c,
                "tado_internal_temperature_c": tado_internal_temp_c,
                "tado_current_setpoint_c": tado_current_setpoint_c,
                "proxy_setpoint_c": float(self._attr_target_temperature or 0.0),
            }
            self.async_write_ha_state()
            return

        proxy_setpoint_c = float(self._attr_target_temperature or 21.0)

        if self._attr_hvac_mode == HVACMode.OFF:
            base_target_c = FROST_PROTECT_C
            reason = "proxy_off"
            rate_limited = False
            deadband_active = False
            heating_request = False
            pid_diag: dict[str, Any] = {}
        else:
            now = time.monotonic()
            # User actions (and startup) should not feel blocked by the command rate limit.
            # Prime the rate-limit timestamp so the next command is allowed immediately.
            if trigger in ("set_temperature", "set_hvac_mode", "startup"):
                self._regulator.state.last_sent_ts_s = now - float(self._regulator.config.min_command_interval_s)
            result = self._regulator.step(
                user_setpoint_c=proxy_setpoint_c,
                measured_temp_c=float(room_temp_c),
                now_ts_s=now,
            )
            base_target_c = float(result.target_c)
            reason = str(result.reason)
            rate_limited = bool(result.rate_limited)
            deadband_active = bool(result.deadband_active)

            # Robust definition: request heat only if the room is meaningfully below setpoint.
            # This matches user expectations and avoids latch artifacts.
            heating_request = (result.error_c > self._regulator.config.deadband_c)

            pid_diag = {
                "pid_error_c": float(result.error_c),
                "pid_output_delta_c": float(result.output_delta_c),
                "pid_p_term_c": float(result.p_c),
                "pid_i_term_c": float(result.i_c),
                "pid_d_term_c": float(result.d_c),
                "pid_deadband_active": deadband_active,
                "pid_rate_limited": rate_limited,
                "pid_heating_latched_on": bool(result.heating_on),
                "temperature_trend_c_per_s": float(result.dtemp_dt_c_per_s),
            }

        # Apply Tado-X mapping AFTER regulator step (actuator strategy layer)
        mapped_target_c, mapping_reason = self._apply_tadox_mapping(
            target_c=base_target_c,
            heating_request=heating_request,
            rate_limited=rate_limited,
            tado_internal_temp_c=tado_internal_temp_c,
        )
        
        # Quantize to the source entity's supported step size (avoid repeated commands due to rounding).
        # Many TRVs only support discrete setpoints (often 0.1°C). If the source exposes a step, use it.
        temp_step = _get_attr_float(
            src_state, ["target_temp_step", "target_temperature_step", "temperature_step"]
        ) or 0.1
        try:
            temp_step = max(0.01, float(temp_step))
        except (TypeError, ValueError):
            temp_step = 0.1

        mapped_target_c = round(float(mapped_target_c) / temp_step) * temp_step
        # Keep a stable decimal representation for state attributes and comparisons.
        mapped_target_c = round(float(mapped_target_c), 2)
        
        # Derived boolean: will Tado likely open given internal temp?
        tado_will_heat = None
        if tado_internal_temp_c is not None:
            tado_will_heat = mapped_target_c > (float(tado_internal_temp_c) + WILL_HEAT_EPS_C)

        # Diagnostics
        self._diag = {
            "regulation_trigger": trigger,
            "regulation_status": "ok",
            "regulation_reason": reason,
            # Core decision flags (simple)
            "heating_request": heating_request,
            "tado_mapping_enabled": bool(self._regulator.config.tadox_mapping.enabled),
            "tado_mapping_reason": mapping_reason,
            # Temperatures (explicit)
            "proxy_setpoint_c": proxy_setpoint_c,
            "room_temperature_c": float(room_temp_c),
            "room_temperature_source": room_temp_source,
            "external_temperature_c": external_temp_c,
            "tado_internal_temperature_c": tado_internal_temp_c,
            "tado_current_setpoint_c": tado_current_setpoint_c,
            "tado_will_heat": tado_will_heat,
            # Command targets (before/after mapping)
            "tado_target_pre_mapping_c": float(base_target_c),
            "tado_target_post_mapping_c": float(mapped_target_c),
            **pid_diag,
        }

        # Apply to source thermostat (post-mapping)
        # Optimization: avoid spamming set_temperature when the source is already at the desired setpoint.
        # Compare against the source's current setpoint within half a step.
        command_eps_c = max(0.05, temp_step / 2.0)
        command_sent = True
        command_skip_reason: Optional[str] = None

        if tado_current_setpoint_c is not None and abs(float(tado_current_setpoint_c) - float(mapped_target_c)) < command_eps_c:
            command_sent = False
            command_skip_reason = "already_at_target"

        if command_sent:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": self._source_entity_id, "temperature": float(mapped_target_c)},
                blocking=True,
            )
            self._last_command_target_c = float(mapped_target_c)

        # Expose send/skip info as part of the diagnostics snapshot
        self._diag["tado_command_sent"] = command_sent
        self._diag["tado_command_skip_reason"] = command_skip_reason

        self.async_write_ha_state()
