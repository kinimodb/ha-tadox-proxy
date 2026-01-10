"""Climate entity for Tado X Proxy (Hybrid control).

Key design goals:
- Stable room temperature control via Tado setpoint (actuator black box).
- Sensor offset handling via HybridRegulator.
- Window-open behavior: deterministic, sensor-based, and testable.
- Command hygiene: reduce flapping while allowing bounded “fast recovery”.

This file intentionally contains extensive telemetry to make the control behavior
explainable during tuning and debugging.

"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import timedelta
from typing import Any, cast

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_COMMAND_REASON,
    ATTR_HYBRID_BIAS,
    ATTR_HYBRID_CMD,
    ATTR_HYBRID_STATE,
    ATTR_LAST_SENT_CONTEXT_ID,
    ATTR_LAST_SENT_REASON,
    ATTR_LAST_SENT_SETPOINT,
    ATTR_LAST_SENT_TS,
    ATTR_ROOM_TEMPERATURE,
    ATTR_TADO_SETPOINT,
    ATTR_TADO_TEMPERATURE,
    CONF_ROOM_SENSOR_ENTITY_ID,
    CONF_TADO_CLIMATE_ENTITY_ID,
    CONF_TADO_TEMP_ENTITY_ID,
    CONF_WINDOW_CLOSE_DELAY_MIN,
    CONF_WINDOW_OPEN_DELAY_MIN,
    CONF_WINDOW_OPEN_ENABLED,
    CONF_WINDOW_SENSOR_ENTITY_ID,
    DOMAIN,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState, WindowMode
from .parameters import PidTuning
from .regulation import CommandPolicy, RegulationMode

_LOGGER = logging.getLogger(__name__)

DEFAULT_MIN_TEMP_C = 5.0
DEFAULT_MAX_TEMP_C = 25.0
DEFAULT_TARGET_TEMP_C = 21.0

# Command hygiene defaults (fallback)
DEFAULT_MIN_COMMAND_INTERVAL_S = 60.0
DEFAULT_MIN_SETPOINT_DELTA_C = 0.5
DEFAULT_STEP_UP_LIMIT_C = 2.0

# Update intervals
CONTROL_LOOP_INTERVAL = timedelta(seconds=30)

# Fast recovery defaults (if enabled in policy)
FAST_RECOVERY_MAX_C = 3.0
FAST_RECOVERY_DURATION_S = 20 * 60


# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up tadox_proxy climate entity from a config entry."""
    # __init__.py stores the DataUpdateCoordinator directly at hass.data[DOMAIN][entry.entry_id]
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TadoxProxyThermostat(hass, entry, coordinator)], update_before_add=True)


# -----------------------------------------------------------------------------
# Entity
# -----------------------------------------------------------------------------

class TadoxProxyThermostat(ClimateEntity, RestoreEntity):
    """Proxy thermostat controlling a Tado climate entity via target temperature."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = DEFAULT_MIN_TEMP_C
    _attr_max_temp = DEFAULT_MAX_TEMP_C

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
        # Options > Data (align with __init__.py coordinator update source)
        self._room_sensor_entity_id: str | None = cast(
            str | None,
            options.get(CONF_ROOM_SENSOR_ENTITY_ID, data.get(CONF_ROOM_SENSOR_ENTITY_ID)),
        )
        self._tado_temp_entity_id: str | None = cast(str | None, data.get(CONF_TADO_TEMP_ENTITY_ID))

        # Window options
        self._window_open_enabled: bool = bool(options.get(CONF_WINDOW_OPEN_ENABLED, False))
        self._window_sensor_entity_id: str | None = cast(str | None, options.get(CONF_WINDOW_SENSOR_ENTITY_ID))
        self._window_open_delay_min: int = int(options.get(CONF_WINDOW_OPEN_DELAY_MIN, 0) or 0)
        self._window_close_delay_min: int = int(options.get(CONF_WINDOW_CLOSE_DELAY_MIN, 0) or 0)

        # Target / mode state
        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._hvac_action: HVACAction = HVACAction.IDLE
        self._target_temperature: float | None = DEFAULT_TARGET_TEMP_C

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

        # Expose external room temp via Climate.current_temperature
        self._current_temperature: float | None = None

        # Loop
        self._unsub_control_loop: Any = None
        self._unsub_window_sensor: Any = None

        self._restore_state_done: bool = False

        # Regulation tuning -> hybrid config (P + small I)
        defaults = PidTuning()
        tuning = PidTuning(
            kp=float(options.get("pid_kp", defaults.kp)),
            ki=float(options.get("pid_ki", defaults.ki)),
        )

        self._hybrid_cfg = HybridConfig(
            kp=tuning.kp,
            ki_small=tuning.ki,
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
            fast_recovery_duration_s=float(options.get("fast_recovery_duration_s", FAST_RECOVERY_DURATION_S)),
            debug=bool(options.get("debug", True)),
        )

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
        return dict(self._telemetry)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._async_restore_state()
        self._setup_window_subscription()
        self._start_control_loop()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_control_loop is not None:
            self._unsub_control_loop()
            self._unsub_control_loop = None
        if self._unsub_window_sensor is not None:
            self._unsub_window_sensor()
            self._unsub_window_sensor = None
        await super().async_will_remove_from_hass()

    async def _async_restore_state(self) -> None:
        last_state = await self.async_get_last_state()
        if last_state is None:
            self._restore_state_done = True
            return

        # Restore target temp and hvac mode
        try:
            if "temperature" in last_state.attributes:
                self._target_temperature = float(last_state.attributes["temperature"])
        except (TypeError, ValueError):
            pass

        try:
            if last_state.state == STATE_OFF:
                self._hvac_mode = HVACMode.OFF
            else:
                self._hvac_mode = HVACMode.HEAT
        except ValueError:
            self._hvac_mode = HVACMode.HEAT

        self._restore_state_done = True

    def _start_control_loop(self) -> None:
        @callback
        def _tick(_: Any) -> None:
            self.hass.async_create_task(self._async_control_tick())

        self._unsub_control_loop = async_track_time_interval(self.hass, _tick, CONTROL_LOOP_INTERVAL)
        self.hass.async_create_task(self._async_control_tick())

    # -------------------------------------------------------------------------
    # Window sensor handling
    # -------------------------------------------------------------------------

    def _setup_window_subscription(self) -> None:
        if not self._window_open_enabled or not self._window_sensor_entity_id:
            self._window_mode = WindowMode.CLOSED
            self._telemetry["window_mode"] = self._window_mode.value
            return

        @callback
        def _on_window_change(event: Any) -> None:
            self._handle_window_state_change(event)

        self._unsub_window_sensor = async_track_state_change_event(
            self.hass,
            [self._window_sensor_entity_id],
            _on_window_change,
        )

    def _handle_window_state_change(self, event: Any) -> None:
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_open = new_state.state in ("on", "open", "true")

        if is_open:
            if self._window_close_timer_cancel is not None:
                self._window_close_timer_cancel()
                self._window_close_timer_cancel = None

            delay_s = self._window_open_delay_min * 60
            if delay_s > 0:
                if self._window_open_timer_cancel is not None:
                    self._window_open_timer_cancel()
                self._window_open_timer_cancel = async_call_later(
                    self.hass, delay_s, self._apply_window_open
                )
                self._telemetry["window_pending"] = "open_delay"
            else:
                self._apply_window_open(None)
        else:
            if self._window_open_timer_cancel is not None:
                self._window_open_timer_cancel()
                self._window_open_timer_cancel = None

            delay_s = self._window_close_delay_min * 60
            if delay_s > 0:
                if self._window_close_timer_cancel is not None:
                    self._window_close_timer_cancel()
                self._window_close_timer_cancel = async_call_later(
                    self.hass, delay_s, self._apply_window_close
                )
                self._telemetry["window_pending"] = "close_hold"
            else:
                self._apply_window_close(None)

    @callback
    def _apply_window_open(self, _: Any) -> None:
        self._window_mode = WindowMode.OPEN
        self._telemetry["window_mode"] = "open"
        self._telemetry["window_pending"] = None
        self.hass.async_create_task(self._async_control_tick())

    @callback
    def _apply_window_close(self, _: Any) -> None:
        self._window_mode = WindowMode.CLOSED
        self._telemetry["window_mode"] = "closed"
        self._telemetry["window_pending"] = None
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

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

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

        self._current_temperature = room_temp

        self._telemetry[ATTR_ROOM_TEMPERATURE] = room_temp
        self._telemetry[ATTR_TADO_TEMPERATURE] = tado_temp
        self._telemetry[ATTR_TADO_SETPOINT] = tado_setpoint

        if room_temp is None:
            self._hvac_action = HVACAction.IDLE
            self._telemetry["status"] = "no_room_temp"
            self.async_write_ha_state()
            return

        # Window override: frost protection (implemented by the regulator via window_mode)
        window_mode = self._window_mode
        self._telemetry["window_mode"] = window_mode.value

        # Hybrid regulation
        now = time.time()
        desired = self._target_temperature

        result = self._regulator.step(
            state=self._hybrid_state,
            room_temp_c=room_temp,
            target_temp_c=desired,
            now_ts=now,
            window_mode=window_mode,
        )
        self._hybrid_state = result.new_state

        self._telemetry[ATTR_HYBRID_STATE] = result.mode.value
        self._telemetry[ATTR_HYBRID_CMD] = result.command_temp_c
        self._telemetry[ATTR_HYBRID_BIAS] = result.new_state.bias_c

        # Command policy
        decided = self._policy.apply(
            desired_setpoint=result.command_temp_c,
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
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return None

    def _read_tado_internal_temperature(self) -> float | None:
        """Read the temperature reported by the Tado thermostat itself (optional)."""
        if not self._tado_temp_entity_id:
            return None
        st = self.hass.states.get(self._tado_temp_entity_id)
        if st is None:
            return None
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
        if ATTR_TEMPERATURE in st.attributes:
            try:
                return float(st.attributes[ATTR_TEMPERATURE])
            except (ValueError, TypeError):
                return None
        return None

    # -------------------------------------------------------------------------
    # Commands / writes
    # -------------------------------------------------------------------------

    async def _async_send_setpoint(self, setpoint_c: float, reason: str) -> None:
        """Send setpoint to the underlying Tado climate entity."""
        if not self._tado_entity_id:
            self._telemetry["send_error"] = "no_tado_entity_id"
            return

        setpoint_c = max(DEFAULT_MIN_TEMP_C, min(DEFAULT_MAX_TEMP_C, float(setpoint_c)))

        # Use HA context (to correlate service calls)
        ctx = self.hass.context
        self._last_sent_context_id = getattr(ctx, "id", None) if ctx else None
        self._last_sent_reason = reason

        service_data = {
            ATTR_ENTITY_ID: self._tado_entity_id,
            ATTR_TEMPERATURE: setpoint_c,
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

        # Telemetry mirrors
        self._telemetry[ATTR_LAST_SENT_SETPOINT] = self._last_sent_setpoint
        self._telemetry[ATTR_LAST_SENT_TS] = self._last_sent_ts
        self._telemetry[ATTR_LAST_SENT_CONTEXT_ID] = self._last_sent_context_id
        self._telemetry[ATTR_LAST_SENT_REASON] = self._last_sent_reason
        self._telemetry[ATTR_COMMAND_REASON] = reason
