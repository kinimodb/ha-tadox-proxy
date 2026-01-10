"""Climate entity for Tado X Proxy (Hybrid control).

Key design goals:
- Use external room temperature sensor as the controlled variable.
- Use Tado thermostat target temperature as the manipulated variable (valve is a black box).
- Provide robust, explainable hybrid regulation strategy (BOOST / HOLD / COAST + Bias).
- Apply command policies (min delta, rate limiting, step-up limit, urgent decrease) to avoid thrashing.

Notes:
- This is a proxy climate entity. It does not implement PID.
- It is inspired by patterns from versatile_thermostat but specialized for Tado X integration patterns.
- All I/O is async and should not block the event loop.
- Uses a DataUpdateCoordinator for update timing and a separate control loop for regulation ticks.

Telemetry:
- Extra state attributes include regulator state, command policy decisions, last send metadata.
- A "context" is generated for each send and stored so we can correlate call_service events.

Window handling:
- Supports an external binary sensor for window open detection (optional).
- When window is open, set a frost protection setpoint (override).

Author: ha-tadox-proxy
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any, cast

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.service import async_call_from_config
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_HYBRID_BIAS,
    ATTR_HYBRID_CMD,
    ATTR_HYBRID_STATE,
    ATTR_ROOM_TEMPERATURE,
    ATTR_TADO_SETPOINT,
    ATTR_TADO_TEMPERATURE,
    CONF_ROOM_SENSOR_ENTITY_ID,
    CONF_TADO_CLIMATE_ENTITY_ID,
    CONF_TADO_TEMP_ENTITY_ID,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_MAX_TEMP_C,
    DEFAULT_MIN_COMMAND_INTERVAL_S,
    DEFAULT_MIN_SETPOINT_DELTA_C,
    DEFAULT_MIN_TEMP_C,
    DEFAULT_STEP_UP_LIMIT_C,
    DOMAIN,
    FAST_RECOVERY_MAX_C,
    WINDOW_FROST_TEMP_C,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState, WindowMode
from .regulation import CommandPolicy

_LOGGER = logging.getLogger(__name__)


class TadoXProxyClimate(ClimateEntity, RestoreEntity):
    """Proxy climate entity controlling a Tado thermostat based on external room temperature."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = DEFAULT_MIN_TEMP_C
    _attr_max_temp = DEFAULT_MAX_TEMP_C
    _attr_temperature_unit = "°C"

    def __init__(self, hass: HomeAssistant, entry: Any, coordinator: Any) -> None:
        """Initialize the proxy thermostat."""
        self.hass = hass
        self._entry = entry
        self._coordinator = coordinator

        data = dict(entry.data or {})
        options = dict(entry.options or {})

        self._name: str = data.get(CONF_NAME) or entry.title or "Tado X Proxy"

        # Entities
        self._tado_entity_id: str | None = cast(str | None, data.get(CONF_TADO_CLIMATE_ENTITY_ID))
        self._room_sensor_entity_id: str | None = cast(str | None, data.get(CONF_ROOM_SENSOR_ENTITY_ID))
        self._tado_temp_entity_id: str | None = cast(str | None, data.get(CONF_TADO_TEMP_ENTITY_ID))
        self._window_sensor_entity_id: str | None = cast(str | None, data.get(CONF_WINDOW_SENSOR_ENTITY_ID))

        # Control interval
        self._control_interval_s: float = float(options.get("control_interval_s", DEFAULT_CONTROL_INTERVAL_S))

        # Target / mode state
        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._hvac_action: HVACAction = HVACAction.IDLE
        self._target_temperature: float | None = None

        # Window mode
        self._window_mode: WindowMode = WindowMode.CLOSED
        self._window_open_timer_cancel: Any = None
        self._window_close_timer_cancel: Any = None

        # Last sent
        self._last_sent_setpoint: float | None = None
        self._last_sent_ts: float | None = None
        self._last_sent_context_id: str | None = None
        self._last_sent_reason: str | None = None

        # Telemetry
        self._telemetry: dict[str, Any] = {}

        self._current_temperature: float | None = None
        # Loop
        self._unsub_control_loop: Any = None
        self._unsub_window_sensor: Any = None

        # Hybrid regulator
        tuning = options.get("tuning") or {}
        self._hybrid_cfg = HybridConfig(
            boost_delta_c=float(options.get("boost_delta_c", 1.0)),
            hold_band_c=float(options.get("hold_band_c", 0.2)),
            coast_band_c=float(options.get("coast_band_c", 0.2)),
            coast_min_setpoint_c=float(options.get("coast_min_setpoint_c", 5.0)),
            coast_max_setpoint_c=float(options.get("coast_max_setpoint_c", 25.0)),
            max_setpoint_drop_below_target_c=float(options.get("max_setpoint_drop_below_target_c", 0.5)),
            kp=tuning.get("kp", 1.0),
            ki_small=tuning.get("ki", 0.02),
        )
        self._regulator = HybridRegulator(self._hybrid_cfg)

        # Persistent regulator state (required by HybridRegulator.step API)
        self._hybrid_state = HybridState()

        # Command policy
        self._policy = CommandPolicy(
            min_command_interval_s=float(options.get("min_command_interval_s", DEFAULT_MIN_COMMAND_INTERVAL_S)),
            min_setpoint_delta_c=float(options.get("min_setpoint_delta_c", DEFAULT_MIN_SETPOINT_DELTA_C)),
            step_up_limit_c=float(options.get("step_up_limit_c", DEFAULT_STEP_UP_LIMIT_C)),
            fast_recovery_max_c=float(options.get("fast_recovery_max_c", FAST_RECOVERY_MAX_C)),
            debug=bool(options.get("debug", True)),
        )

        # HA entity attributes
        self._attr_unique_id = f"{entry.entry_id}_climate"

    # -------------------------------------------------------------------------
    # HA entity basics
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_climate"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._name,
            manufacturer="tadox_proxy",
            model="Proxy Thermostat (Hybrid)",
        )

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        return self._hvac_action

    @property
    def current_temperature(self) -> float | None:
        """Return the external room temperature used for regulation."""
        if self._current_temperature is not None:
            return self._current_temperature
        return self._read_room_temperature()

    @property
    def target_temperature(self) -> float | None:
        return self._target_temperature

    @property
    def min_temp(self) -> float:
        return DEFAULT_MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        return DEFAULT_MAX_TEMP_C

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        attrs.update(self._telemetry)

        # Some explicit mirrors for easier UI filtering
        attrs["tadox_last_sent_setpoint"] = self._last_sent_setpoint
        attrs["tadox_last_sent_ts"] = self._last_sent_ts
        attrs["tadox_last_sent_context_id"] = self._last_sent_context_id
        attrs["tadox_last_sent_reason"] = self._last_sent_reason

        attrs["user_target"] = self._target_temperature
        attrs["tadox_command_reason"] = self._telemetry.get("policy_reason")

        return attrs

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                if last_state.state in ("off", "heat"):
                    self._hvac_mode = HVACMode(last_state.state)
            except ValueError:
                _LOGGER.debug("Invalid restored hvac_mode state: %s", last_state.state)

            # Restore target temperature if available
            if "temperature" in last_state.attributes:
                try:
                    self._target_temperature = float(last_state.attributes["temperature"])
                except (ValueError, TypeError):
                    self._target_temperature = None

        # Start control loop
        self._unsub_control_loop = async_track_time_interval(
            self.hass,
            self._async_control_loop_cb,
            dt_util.timedelta(seconds=self._control_interval_s),
        )

        # Listen to window sensor
        if self._window_sensor_entity_id:
            self._unsub_window_sensor = self.hass.helpers.event.async_track_state_change_event(
                [self._window_sensor_entity_id],
                self._async_window_sensor_changed,
            )

        # Initial tick
        await self._async_control_tick()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_control_loop:
            self._unsub_control_loop()
            self._unsub_control_loop = None
        if self._unsub_window_sensor:
            self._unsub_window_sensor()
            self._unsub_window_sensor = None
        await super().async_will_remove_from_hass()

    @callback
    def _async_control_loop_cb(self, _now: Any) -> None:
        """Periodic control callback."""
        self.hass.async_create_task(self._async_control_tick())

    # -------------------------------------------------------------------------
    # Window sensor handling
    # -------------------------------------------------------------------------

    @callback
    def _async_window_sensor_changed(self, event: Any) -> None:
        """Track window sensor changes, set window mode."""
        if not self._window_sensor_entity_id:
            return

        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_open = new_state.state in ("on", "open", "true")
        self._window_mode = WindowMode.OPEN if is_open else WindowMode.CLOSED
        self._telemetry["window_mode"] = self._window_mode.value
        _LOGGER.debug("Window sensor -> %s", self._window_mode.value)

        # Trigger immediate tick
        self.hass.async_create_task(self._async_control_tick())

    # -------------------------------------------------------------------------
    # Climate commands
    # -------------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        self._target_temperature = float(temperature)
        self._telemetry["user_target"] = self._target_temperature
        self.async_write_ha_state()
        await self._async_control_tick()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._hvac_mode = hvac_mode
        self._telemetry["user_hvac_mode"] = hvac_mode.value
        self.async_write_ha_state()
        await self._async_control_tick()

    # -------------------------------------------------------------------------
    # Control tick
    # -------------------------------------------------------------------------

    async def _async_control_tick(self) -> None:
        # Reset transient status each tick to avoid stale telemetry
        self._telemetry.pop("status", None)

        if self._hvac_mode == HVACMode.OFF:
            self._hvac_action = HVACAction.OFF
            self._telemetry["mode"] = "off"
            self.async_write_ha_state()
            return

        room_temp = self._read_room_temperature()
        tado_temp = self._read_tado_internal_temperature()
        tado_setpoint = self._read_tado_setpoint()

        # Expose external room temperature via Climate.current_temperature
        self._current_temperature = room_temp

        self._telemetry[ATTR_ROOM_TEMPERATURE] = room_temp
        self._telemetry[ATTR_TADO_TEMPERATURE] = tado_temp
        self._telemetry[ATTR_TADO_SETPOINT] = tado_setpoint

        if room_temp is None:
            self._hvac_action = HVACAction.IDLE
            self._telemetry["status"] = "no_room_temp"
            self.async_write_ha_state()
            return

        # Window override: frost protection
        if self._window_mode == WindowMode.OPEN:
            await self._async_send_setpoint(WINDOW_FROST_TEMP_C, reason="window_open_frost")
            self._hvac_action = HVACAction.HEATING
            self.async_write_ha_state()
            return

        # Hybrid regulation
        now = time.time()
        desired = self._target_temperature

        result = self._regulator.step(
            state=self._hybrid_state,
            room_temp_c=room_temp,
            target_temp_c=desired,
            now_ts=now,
        )
        # HybridRegulator.step mutates and returns state; keep a reference for clarity
        self._hybrid_state = result.new_state

        cmd = result.target_c
        bias = result.new_state.bias_c

        self._telemetry[ATTR_HYBRID_STATE] = result.mode.value
        self._telemetry[ATTR_HYBRID_CMD] = cmd
        self._telemetry[ATTR_HYBRID_BIAS] = bias

        decided = self._policy.apply(
            desired_setpoint=cmd,
            last_sent_setpoint=self._last_sent_setpoint,
            last_sent_ts=self._last_sent_ts,
            now_ts=now,
        )
        self._telemetry["policy_send"] = decided.send
        self._telemetry["policy_reason"] = decided.reason

        if decided.send:
            await self._async_send_setpoint(decided.setpoint, reason=decided.reason)

        # HVAC action heuristic
        if desired - room_temp > 0.2:
            self._hvac_action = HVACAction.HEATING
        else:
            self._hvac_action = HVACAction.IDLE

        self.async_write_ha_state()

    # -------------------------------------------------------------------------
    # Temperature reads
    # -------------------------------------------------------------------------

    def _read_room_temperature(self) -> float | None:
        """Read the external room sensor temperature."""
        if not self._room_sensor_entity_id:
            return None
        st = self.hass.states.get(self._room_sensor_entity_id)
        if st is None:
            return None
        # Try attribute "temperature" first, then state
        if "temperature" in st.attributes:
            try:
                return float(st.attributes["temperature"])
            except (ValueError, TypeError):
                return None
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _read_tado_internal_temperature(self) -> float | None:
        """Read the temperature reported by the Tado thermostat itself (optional)."""
        entity_id = self._tado_temp_entity_id or self._tado_entity_id
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        # Tado climate entity often has current_temperature attribute
        for key in ("current_temperature", "temperature"):
            if key in st.attributes:
                try:
                    return float(st.attributes[key])
                except (ValueError, TypeError):
                    continue
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _read_tado_setpoint(self) -> float | None:
        """Read the target temperature currently set on the Tado thermostat."""
        if not self._tado_entity_id:
            return None
        st = self.hass.states.get(self._tado_entity_id)
        if st is None:
            return None
        # Try common attribute names for target temperature
        for key in ("temperature", "target_temperature", "setpoint", ATTR_TEMPERATURE):
            if key in st.attributes:
                try:
                    return float(st.attributes[key])
                except (ValueError, TypeError):
                    continue
        return None

    # -------------------------------------------------------------------------
    # Commands / writes
    # ------------------------------------------------------------------------

    async def _async_send_setpoint(self, setpoint_c: float, reason: str) -> None:
        if not self._tado_entity_id:
            self._telemetry["send_error"] = "no_tado_entity_id"
            return

        setpoint_c = max(DEFAULT_MIN_TEMP_C, min(DEFAULT_MAX_TEMP_C, float(setpoint_c)))

        # Create an explicit context so we can correlate call_service events
        ctx = self.hass.context
        if ctx is None:
            # Fallback to a new context if hass doesn't provide one
            ctx = self.hass.helpers.event.Context()  # type: ignore[attr-defined]

        self._last_sent_context_id = getattr(ctx, "id", None)
        self._last_sent_reason = reason

        service_data = {
            "entity_id": self._tado_entity_id,
            "temperature": setpoint_c,
        }

        _LOGGER.debug(
            "Sending setpoint=%.2f to %s (reason=%s, context_id=%s)",
            setpoint_c,
            self._tado_entity_id,
            reason,
            self._last_sent_context_id,
        )

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            service_data,
            blocking=True,
            context=ctx,
        )

        self._last_sent_setpoint = setpoint_c
        self._last_sent_ts = time.time()
        self._telemetry["tadox_last_sent_setpoint"] = self._last_sent_setpoint
        self._telemetry["tadox_last_sent_ts"] = self._last_sent_ts
        self._telemetry["tadox_last_sent_context_id"] = self._last_sent_context_id
        self._telemetry["tadox_last_sent_reason"] = self._last_sent_reason


async def async_setup_entry(hass: HomeAssistant, entry: Any, async_add_entities: Any) -> None:
    """Set up the Tado X Proxy climate entity from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([TadoXProxyClimate(hass, entry, coordinator)], update_before_add=True)
