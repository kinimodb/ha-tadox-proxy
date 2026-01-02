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

import datetime as dt
import logging
import time
from dataclasses import asdict
from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_WINDOW_CLOSE_DELAY_MIN,
    CONF_WINDOW_OPEN_DELAY_MIN,
    CONF_WINDOW_OPEN_ENABLED,
    CONF_WINDOW_SENSOR_ENTITY_ID,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridResult, HybridState
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    FROST_PROTECT_C,
    RATE_LIMIT_DECREASE_EPS_C,
    WILL_HEAT_EPS_C,
    RegulationConfig,
    PidTuning,
)

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Command hygiene (base defaults)
# -----------------------------------------------------------------------------
MIN_SEND_DELTA_C: float = 0.2
MAX_STEP_UP_C: float = 0.5

# “Fast recovery” parameters (bounded)
FAST_RECOVERY_MIN_INTERVAL_S: float = 20.0
FAST_RECOVERY_MAX_STEP_UP_C: float = 2.0

# Triggers for fast recovery
FAST_RECOVERY_ERROR_C: float = 1.5  # setpoint - room_temp
FAST_RECOVERY_TARGET_GAP_C: float = 3.0  # desired_target - current_tado_setpoint

# Special case: leaving frost protection after window. One-time “jump to desired”.
FROSTISH_C: float = 7.5
FROST_EXIT_JUMP_GAP_C: float = 8.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tado X Proxy climate entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity = TadoXProxyClimate(coordinator=coordinator, unique_id=entry.entry_id, config_entry=entry)
    async_add_entities([entity])


class TadoXProxyClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Proxy thermostat entity that regulates a Tado X thermostat."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_translation_key = "tadox_proxy"

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None

        self._config = RegulationConfig()

        # Apply tuning from Options Flow (legacy keys mapped to hybrid)
        if config_entry.options:
            opts = config_entry.options
            kp = opts.get("kp", self._config.tuning.kp)
            ki = opts.get("ki", self._config.tuning.ki)
            kd = opts.get("kd", self._config.tuning.kd)
            self._config.tuning = PidTuning(kp=kp, ki=ki, kd=kd)

        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._target_temp: float = 20.0

        # Hybrid regulator
        self._hybrid_config = HybridConfig(
            min_target_c=self._config.min_target_c,
            max_target_c=self._config.max_target_c,
            coast_target_c=FROST_PROTECT_C,
            kp=self._config.tuning.kp,
            ki_small=min(self._config.tuning.ki, 0.001),
        )

        # Trend-based window-open must be disabled (we use binary_sensor only).
        if hasattr(self._hybrid_config, "window_open_enabled"):
            try:
                setattr(self._hybrid_config, "window_open_enabled", False)
            except Exception:
                pass

        self._regulator = HybridRegulator(self._hybrid_config)
        self._hybrid_state = HybridState()

        # timestamps
        self._last_regulation_ts: float = 0.0
        self._last_command_sent_ts: float = 0.0

        # last results
        self._last_regulation_result: HybridResult | None = None
        self._last_regulation_reason: str = "startup"

        # command telemetry
        self._last_current_tado_setpoint_c: float | None = None
        self._last_desired_target_c: float | None = None
        self._last_command_target_c: float | None = None
        self._last_command_step_limited: bool = False
        self._last_command_step_up_limit_c: float | None = None
        self._last_command_diff_c: float | None = None

        # extra “ground truth” telemetry
        self._last_sent_target_c: float | None = None
        self._last_sent_reason: str | None = None
        self._last_sent_mono: float | None = None

        self._effective_min_interval_s: float = self._config.min_command_interval_s
        self._effective_step_up_c: float = MAX_STEP_UP_C
        self._fast_recovery_active: bool = False
        self._fast_recovery_reason: str | None = None

        # window options
        opts = config_entry.options or {}
        self._window_open_enabled: bool = bool(opts.get(CONF_WINDOW_OPEN_ENABLED, False))
        self._window_sensor_entity_id: str | None = opts.get(CONF_WINDOW_SENSOR_ENTITY_ID)
        self._window_open_delay_s: float = float(opts.get(CONF_WINDOW_OPEN_DELAY_MIN, 0) or 0) * 60.0
        self._window_close_delay_s: float = float(opts.get(CONF_WINDOW_CLOSE_DELAY_MIN, 0) or 0) * 60.0

        # window runtime state
        self._window_open_active: bool = False  # raw sensor
        self._window_forced: bool = False  # output override active
        self._window_open_pending: bool = False
        self._window_open_delay_remaining_s: float = 0.0
        self._window_close_hold_remaining_s: float = 0.0
        self._window_forced_reason: str | None = None

        # deadlines and transitions
        self._window_open_deadline_mono: float | None = None
        self._window_close_deadline_mono: float | None = None
        self._last_window_forced: bool = False

        # unsub handles
        self._unsub_window_sensor = None
        self._unsub_window_open_timer = None
        self._unsub_window_close_timer = None
        self._unsub_window_tick = None
        self._unsub_fast_recovery_timer = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=self._config_entry.title,
            manufacturer="Tado X Proxy",
            model="Hybrid Regulator",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # restore state
        last_state = await self.async_get_last_state()
        if last_state:
            self._hvac_mode = (
                last_state.state if last_state.state in self._attr_hvac_modes else HVACMode.HEAT
            )
            if last_state.attributes.get(ATTR_TEMPERATURE):
                try:
                    self._target_temp = float(last_state.attributes[ATTR_TEMPERATURE])
                except (ValueError, TypeError):
                    self._target_temp = 20.0

        self._async_setup_window_sensor()

        async_track_time_interval(
            self.hass,
            self._async_regulation_timer_callback,
            dt.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
        )

        await self._async_regulation_cycle(trigger="startup")

    async def async_will_remove_from_hass(self) -> None:
        for unsub in (
            self._unsub_window_sensor,
            self._unsub_window_open_timer,
            self._unsub_window_close_timer,
            self._unsub_window_tick,
            self._unsub_fast_recovery_timer,
        ):
            if unsub:
                try:
                    unsub()
                except Exception:
                    pass
        await super().async_will_remove_from_hass()

    # -----------------------------------------------------------------------------
    # Window sensor handling
    # -----------------------------------------------------------------------------
    def _async_setup_window_sensor(self) -> None:
        if self._unsub_window_sensor:
            try:
                self._unsub_window_sensor()
            finally:
                self._unsub_window_sensor = None

        self._cancel_window_timers()
        self._stop_window_tick()

        self._window_open_deadline_mono = None
        self._window_close_deadline_mono = None
        self._window_open_active = False

        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return

        st = self.hass.states.get(self._window_sensor_entity_id)
        if st is not None:
            self._window_open_active = (st.state == "on")

        now_m = time.monotonic()
        if self._window_open_active:
            self._window_open_deadline_mono = now_m + max(0.0, self._window_open_delay_s)
            self._window_close_deadline_mono = None
            self._schedule_window_open_timer()
            self._start_window_tick()

        @callback
        def _handle_window_change(event) -> None:
            new_state = event.data.get("new_state")
            is_open = new_state is not None and new_state.state == "on"
            now_m2 = time.monotonic()

            self._window_open_active = is_open
            self._cancel_window_timers()

            if is_open:
                self._window_open_deadline_mono = now_m2 + max(0.0, self._window_open_delay_s)
                self._window_close_deadline_mono = None
                self._schedule_window_open_timer()
                self._start_window_tick()
            else:
                self._window_close_deadline_mono = now_m2 + max(0.0, self._window_close_delay_s)
                self._window_open_deadline_mono = None
                self._schedule_window_close_timer()
                self._start_window_tick()

            # immediate cycle so pending/hold becomes visible instantly
            self.hass.async_create_task(self._async_regulation_cycle(trigger="window_sensor"))
            self.async_write_ha_state()

        self._unsub_window_sensor = async_track_state_change_event(
            self.hass, [self._window_sensor_entity_id], _handle_window_change
        )

    def _cancel_window_timers(self) -> None:
        if self._unsub_window_open_timer:
            try:
                self._unsub_window_open_timer()
            finally:
                self._unsub_window_open_timer = None
        if self._unsub_window_close_timer:
            try:
                self._unsub_window_close_timer()
            finally:
                self._unsub_window_close_timer = None

    def _schedule_window_open_timer(self) -> None:
        if self._unsub_window_open_timer:
            try:
                self._unsub_window_open_timer()
            finally:
                self._unsub_window_open_timer = None

        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return
        if not self._window_open_active or self._window_open_deadline_mono is None:
            return

        delay = max(0.0, self._window_open_deadline_mono - time.monotonic())

        @callback
        def _fire(_now) -> None:
            self._unsub_window_open_timer = None
            self.hass.async_create_task(self._async_regulation_cycle(trigger="window_open_delay_expired"))
            self.async_write_ha_state()

        self._unsub_window_open_timer = async_call_later(self.hass, delay, _fire)

    def _schedule_window_close_timer(self) -> None:
        if self._unsub_window_close_timer:
            try:
                self._unsub_window_close_timer()
            finally:
                self._unsub_window_close_timer = None

        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return
        if self._window_open_active or self._window_close_deadline_mono is None:
            return

        delay = max(0.0, self._window_close_deadline_mono - time.monotonic())

        @callback
        def _fire(_now) -> None:
            self._unsub_window_close_timer = None
            self.hass.async_create_task(self._async_regulation_cycle(trigger="window_close_hold_expired"))
            self.async_write_ha_state()

        self._unsub_window_close_timer = async_call_later(self.hass, delay, _fire)

    def _start_window_tick(self) -> None:
        if self._unsub_window_tick:
            return

        @callback
        def _tick(_now_dt: dt.datetime) -> None:
            self._update_window_diagnostics()
            self.async_write_ha_state()

            if self._window_open_delay_remaining_s <= 0.0 and self._window_close_hold_remaining_s <= 0.0:
                self._stop_window_tick()

        self._unsub_window_tick = async_track_time_interval(self.hass, _tick, dt.timedelta(seconds=1))

    def _stop_window_tick(self) -> None:
        if self._unsub_window_tick:
            try:
                self._unsub_window_tick()
            finally:
                self._unsub_window_tick = None

    def _compute_window_state(self) -> tuple[bool, str | None, bool, float, float]:
        """Return (forced, reason, pending, open_remaining_s, close_remaining_s)."""
        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return False, None, False, 0.0, 0.0

        now_m = time.monotonic()

        # OPEN: delay -> forced frost
        if self._window_open_active:
            delay_s = max(0.0, self._window_open_delay_s)
            if delay_s <= 0.0:
                return True, "window_open_forced", False, 0.0, 0.0

            if self._window_open_deadline_mono is None:
                self._window_open_deadline_mono = now_m + delay_s
                self._schedule_window_open_timer()

            rem = max(0.0, self._window_open_deadline_mono - now_m)
            if rem <= 0.0:
                return True, "window_open_forced", False, 0.0, 0.0
            return False, "window_open_pending", True, rem, 0.0

        # CLOSED: hold forced for N seconds
        hold_s = max(0.0, self._window_close_delay_s)
        if hold_s <= 0.0 or self._window_close_deadline_mono is None:
            return False, None, False, 0.0, 0.0

        rem = max(0.0, self._window_close_deadline_mono - now_m)
        if rem <= 0.0:
            return False, None, False, 0.0, 0.0
        return True, "window_close_hold", False, 0.0, rem

    def _update_window_diagnostics(self) -> None:
        forced, reason, pending, open_rem, close_rem = self._compute_window_state()
        self._window_forced = forced
        self._window_forced_reason = reason
        self._window_open_pending = pending
        self._window_open_delay_remaining_s = open_rem
        self._window_close_hold_remaining_s = close_rem

    # -----------------------------------------------------------------------------
    # Base HA callbacks
    # -----------------------------------------------------------------------------
    @callback
    def _async_regulation_timer_callback(self, now: dt.datetime) -> None:
        self.hass.async_create_task(self._async_regulation_cycle(trigger="timer"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self._attr_hvac_modes:
            return
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._target_temp = float(temp)
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="set_temperature")

    # -----------------------------------------------------------------------------
    # Helpers: read source entity state as “ground truth” (not coordinator-stale)
    # -----------------------------------------------------------------------------
    def _get_source_entity_id(self) -> str | None:
        return self.coordinator.config_entry.data.get("source_entity_id")

    def _read_source_setpoint_c(self) -> float | None:
        src = self._get_source_entity_id()
        if not src:
            return None
        st = self.hass.states.get(src)
        if st is None:
            return None
        try:
            return float(st.attributes.get("temperature"))
        except (TypeError, ValueError):
            return None

    def _read_source_internal_temp_c(self) -> float | None:
        src = self._get_source_entity_id()
        if not src:
            return None
        st = self.hass.states.get(src)
        if st is None:
            return None
        try:
            return float(st.attributes.get("current_temperature"))
        except (TypeError, ValueError):
            return None

    # -----------------------------------------------------------------------------
    # Fast recovery timer
    # -----------------------------------------------------------------------------
    def _schedule_fast_recovery_tick(self) -> None:
        """Schedule a short tick while fast recovery is active to avoid waiting for control_interval."""
        if self._unsub_fast_recovery_timer:
            return
        if not self._fast_recovery_active:
            return

        delay = max(1.0, min(FAST_RECOVERY_MIN_INTERVAL_S, self._effective_min_interval_s))

        @callback
        def _fire(_now) -> None:
            self._unsub_fast_recovery_timer = None
            self.hass.async_create_task(self._async_regulation_cycle(trigger="fast_recovery_tick"))

        self._unsub_fast_recovery_timer = async_call_later(self.hass, delay, _fire)

    def _cancel_fast_recovery_tick(self) -> None:
        if self._unsub_fast_recovery_timer:
            try:
                self._unsub_fast_recovery_timer()
            finally:
                self._unsub_fast_recovery_timer = None

    # -----------------------------------------------------------------------------
    # Regulation loop
    # -----------------------------------------------------------------------------
    async def _async_regulation_cycle(self, trigger: str) -> None:
        now_wall = time.time()

        # Sensors used for regulation
        room_temp = self.coordinator.data.get("room_temp")
        if room_temp is None:
            self._last_regulation_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        # “Ground truth” for Tado telemetry
        tado_internal = self._read_source_internal_temp_c()
        tado_setpoint = self._read_source_setpoint_c()

        if tado_internal is None:
            # fallback to coordinator if needed
            tado_internal = self.coordinator.data.get("tado_internal_temp")
        if tado_setpoint is None:
            tado_setpoint = self.coordinator.data.get("tado_setpoint")

        # compute dt for hybrid
        dt_s = 0.0
        if self._last_regulation_ts > 0:
            dt_s = now_wall - self._last_regulation_ts
        self._last_regulation_ts = now_wall

        # effective user setpoint
        effective_setpoint = self._target_temp
        if self._hvac_mode == HVACMode.OFF:
            effective_setpoint = FROST_PROTECT_C

        # update window diagnostics each cycle
        self._update_window_diagnostics()
        window_forced = self._window_forced
        window_reason = self._window_forced_reason
        resume_from_window = self._last_window_forced and (not window_forced)

        # IMPORTANT: Window-forced is an output override and should not mutate hybrid state.
        # So we compute hybrid only when not forced; otherwise we synthesize a result for telemetry.
        if window_forced:
            desired_target_c = FROST_PROTECT_C
            reg_result = HybridResult(
                target_c=desired_target_c,
                error_c=0.0,
                p_term_c=0.0,
                predicted_temp_c=room_temp,
                mode=self._hybrid_state.mode,
                new_state=self._hybrid_state,
                debug_info={"mode_reason": "window_forced"},
            )
            self._last_regulation_result = reg_result
        else:
            reg_result = self._regulator.compute_target(
                setpoint_c=effective_setpoint,
                room_temp_c=room_temp,
                time_delta_s=dt_s,
                state=self._hybrid_state,
                heating_enabled=(self._hvac_mode != HVACMode.OFF),
            )
            self._hybrid_state = reg_result.new_state
            self._last_regulation_result = reg_result

            desired_target_c = max(
                self._config.min_target_c,
                min(self._config.max_target_c, reg_result.target_c),
            )

        desired_target_c = round(float(desired_target_c), 1)

        # -------------------------
        # Command policy selection
        # -------------------------
        self._fast_recovery_active = False
        self._fast_recovery_reason = None
        self._effective_min_interval_s = float(self._config.min_command_interval_s)
        self._effective_step_up_c = float(MAX_STEP_UP_C)

        # Calculate room error (setpoint - room_temp) for urgency.
        room_error_c = float(effective_setpoint - room_temp)

        if tado_setpoint is not None:
            target_gap_c = float(desired_target_c - tado_setpoint)
        else:
            target_gap_c = 0.0

        # Conditions for fast recovery:
        # - Hybrid in BOOST, or
        # - Room error large (cold), or
        # - Desired target far from current Tado setpoint
        try:
            is_boost = reg_result.mode.value == "boost"
        except Exception:
            is_boost = False

        if (not window_forced) and (self._hvac_mode != HVACMode.OFF):
            if is_boost:
                self._fast_recovery_active = True
                self._fast_recovery_reason = "boost"
            elif room_error_c >= FAST_RECOVERY_ERROR_C:
                self._fast_recovery_active = True
                self._fast_recovery_reason = f"room_error({room_error_c:.2f})"
            elif target_gap_c >= FAST_RECOVERY_TARGET_GAP_C:
                self._fast_recovery_active = True
                self._fast_recovery_reason = f"target_gap({target_gap_c:.2f})"

        if self._fast_recovery_active:
            self._effective_min_interval_s = min(self._effective_min_interval_s, FAST_RECOVERY_MIN_INTERVAL_S)
            self._effective_step_up_c = max(self._effective_step_up_c, FAST_RECOVERY_MAX_STEP_UP_C)

        # Resume from window: allow ONE immediate “jump” to desired, bounded.
        # This is explicitly to avoid slow 0.5°C ramp from frost.
        resume_jump_allowed = False
        if resume_from_window and tado_setpoint is not None:
            gap = desired_target_c - float(tado_setpoint)
            if float(tado_setpoint) <= FROSTISH_C and gap >= FROST_EXIT_JUMP_GAP_C:
                resume_jump_allowed = True

        # -------------------------
        # Build command target
        # -------------------------
        command_target_c = desired_target_c
        step_limited = False
        step_up_limit_c = None

        if tado_setpoint is not None:
            current_sp = float(tado_setpoint)

            # Upwards moves are stepped unless:
            # - resume_jump_allowed (one-time), or
            # - still allow full jump if within min interval but big gap (fast recovery handled separately)
            if command_target_c > current_sp + 0.05:
                if resume_jump_allowed:
                    # One-time direct jump to desired.
                    step_limited = False
                    step_up_limit_c = None
                    command_target_c = desired_target_c
                else:
                    max_step = self._effective_step_up_c
                    stepped = min(desired_target_c, current_sp + max_step)
                    step_limited = (stepped != desired_target_c)
                    step_up_limit_c = max_step
                    command_target_c = round(float(stepped), 1)

        command_target_c = round(float(command_target_c), 1)

        # Diagnostics snapshot
        self._last_current_tado_setpoint_c = tado_setpoint
        self._last_desired_target_c = desired_target_c
        self._last_command_target_c = command_target_c
        self._last_command_step_limited = step_limited
        self._last_command_step_up_limit_c = step_up_limit_c
        self._last_command_diff_c = (
            abs(command_target_c - float(tado_setpoint)) if tado_setpoint is not None else None
        )

        # -------------------------
        # Decide whether to send
        # -------------------------
        should_send = False
        reason = "noop"

        if tado_setpoint is None:
            should_send = True
            reason = "init_unknown_current_setpoint"
        else:
            current_sp = float(tado_setpoint)
            diff = abs(command_target_c - current_sp)

            time_since_last_send = now_wall - self._last_command_sent_ts
            is_rate_limited = time_since_last_send < self._effective_min_interval_s

            if diff < MIN_SEND_DELTA_C:
                reason = f"min_delta_guard({MIN_SEND_DELTA_C}C)"
            elif is_rate_limited:
                is_decrease = command_target_c < (current_sp - RATE_LIMIT_DECREASE_EPS_C)
                if is_decrease:
                    should_send = True
                    reason = "urgent_decrease"
                else:
                    remaining = int(max(0.0, self._effective_min_interval_s - time_since_last_send))
                    reason = f"rate_limited({remaining}s)"
            else:
                should_send = True
                reason = "normal_update"

        # Window forced must always be sent promptly (close valve).
        # Even if rate-limited, this should be treated as urgent decrease.
        if window_forced and tado_setpoint is not None:
            current_sp = float(tado_setpoint)
            if command_target_c < (current_sp - RATE_LIMIT_DECREASE_EPS_C):
                should_send = True
                reason = "window_forced_urgent_decrease"

        # Resume “jump” should not be blocked by rate limit.
        if resume_jump_allowed and tado_setpoint is not None:
            current_sp = float(tado_setpoint)
            if command_target_c > current_sp + 0.05:
                should_send = True
                reason = "window_resume_jump"

        # decorate reason
        if step_limited and step_up_limit_c is not None:
            reason = f"{reason}|step_up_limited({step_up_limit_c}C)"
        if window_reason:
            reason = f"{reason}|{window_reason}"
        if self._fast_recovery_active:
            reason = f"{reason}|fast_recovery({self._fast_recovery_reason})"
        if resume_from_window:
            reason = f"{reason}|resume_from_window"

        # send command
        if should_send:
            await self._async_send_to_tado(command_target_c)
            self._last_command_sent_ts = now_wall
            self._last_regulation_reason = f"sent({reason})"

            self._last_sent_target_c = command_target_c
            self._last_sent_reason = reason
            self._last_sent_mono = time.monotonic()
        else:
            self._last_regulation_reason = reason

        # schedule fast recovery tick if needed
        self._cancel_fast_recovery_tick()
        if self._fast_recovery_active and (not window_forced):
            # Only if we still have a meaningful gap to close
            if tado_setpoint is not None and (desired_target_c - float(tado_setpoint)) > 0.2:
                self._schedule_fast_recovery_tick()

        self._last_window_forced = window_forced
        self.async_write_ha_state()

    async def _async_send_to_tado(self, target_c: float) -> None:
        source_entity = self._get_source_entity_id()
        if not source_entity:
            return

        try:
            await self.hass.services.async_call(
                domain="climate",
                service="set_temperature",
                service_data={
                    "entity_id": source_entity,
                    "temperature": float(target_c),
                    "hvac_mode": HVACMode.HEAT,
                },
                blocking=True,
            )
        except Exception as err:
            _LOGGER.error("Failed to send command to Tado: %s", err)

    # -----------------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------------
    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.data.get("room_temp")

    @property
    def target_temperature(self) -> float | None:
        return self._target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        tado_internal = self._read_source_internal_temp_c()
        tado_setpoint = self._read_source_setpoint_c()

        if tado_internal is None:
            tado_internal = self.coordinator.data.get("tado_internal_temp")
        if tado_setpoint is None:
            tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if float(tado_setpoint) > float(tado_internal) + WILL_HEAT_EPS_C:
                return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tuning = self._config.tuning

        # ground truth reads for display
        tado_internal = self._read_source_internal_temp_c()
        tado_setpoint = self._read_source_setpoint_c()
        if tado_internal is None:
            tado_internal = self.coordinator.data.get("tado_internal_temp")
        if tado_setpoint is None:
            tado_setpoint = self.coordinator.data.get("tado_setpoint")

        sent_age_s: float | None = None
        if self._last_sent_mono is not None:
            sent_age_s = round(float(time.monotonic() - self._last_sent_mono), 1)

        remaining_any_s = max(self._window_open_delay_remaining_s, self._window_close_hold_remaining_s)

        attrs: dict[str, Any] = {
            "control_interval_s": DEFAULT_CONTROL_INTERVAL_S,
            "regulation_reason": self._last_regulation_reason,
            "regulator": "hybrid",

            # source telemetry (ground truth)
            "tado_internal_temperature_c": tado_internal,
            "tado_setpoint_c": tado_setpoint,

            # last sent telemetry (ours)
            "tado_last_sent_target_c": self._last_sent_target_c,
            "tado_last_sent_reason": self._last_sent_reason,
            "tado_last_sent_age_s": sent_age_s,

            # window config/state
            "window_open_enabled": self._window_open_enabled,
            "window_sensor_entity_id": self._window_sensor_entity_id,
            "window_open": self._window_open_active,
            "window_open_delay_min": round(self._window_open_delay_s / 60.0, 3),
            "window_close_delay_min": round(self._window_close_delay_s / 60.0, 3),
            "window_forced": self._window_forced,
            "window_open_pending": self._window_open_pending,
            "window_open_delay_remaining_s": round(self._window_open_delay_remaining_s, 1),
            "window_close_hold_remaining_s": round(self._window_close_hold_remaining_s, 1),

            # command policy effective values
            "command_min_send_delta_c": MIN_SEND_DELTA_C,
            "command_base_max_step_up_c": MAX_STEP_UP_C,
            "command_effective_min_interval_s": round(self._effective_min_interval_s, 1),
            "command_effective_max_step_up_c": round(self._effective_step_up_c, 2),
            "command_fast_recovery_active": self._fast_recovery_active,
            "command_fast_recovery_reason": self._fast_recovery_reason,

            # command targets
            "hybrid_desired_target_c": self._last_desired_target_c,
            "hybrid_command_target_c": self._last_command_target_c,
            "hybrid_command_step_limited": self._last_command_step_limited,
            "hybrid_command_step_up_limit_c": self._last_command_step_up_limit_c,
            "hybrid_command_diff_c": self._last_command_diff_c,

            # legacy tuning keys (UI compatibility)
            "pid_kp": tuning.kp,
            "pid_ki": tuning.ki,
            "pid_kd": tuning.kd,

            # hybrid tuning
            "hybrid_kp": self._hybrid_config.kp,
            "hybrid_ki_small": self._hybrid_config.ki_small,

            # hybrid internal state
            "hybrid_mode": self._hybrid_state.mode.value,
            "hybrid_bias_c": round(self._hybrid_state.bias_c, 3),
            "hybrid_i_small_c": round(self._hybrid_state.i_small_c, 3),
            "hybrid_dTdt_ema_c_per_min": round(self._hybrid_state.dTdt_ema_c_per_s * 60.0, 5),

            # compatibility keys (keep names for dashboards)
            "hybrid_window_open_remaining_s": round(remaining_any_s, 1),
            "hybrid_window_open_active": self._window_forced,
        }

        if self._last_regulation_result:
            res = self._last_regulation_result
            attrs.update(
                {
                    "hybrid_target_c": res.target_c,
                    "hybrid_error_c": res.error_c,
                    "hybrid_p_term_c": res.p_term_c,
                    "hybrid_mode_reason": res.debug_info.get("mode_reason") if res.debug_info else None,
                    "hybrid_predicted_temp_c": res.predicted_temp_c,
                }
            )

        return attrs


# Commit: fix: make window forcing + command hygiene transparent and bounded