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
import dataclasses
import logging
import time
from datetime import timedelta
from typing import Any, cast

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    CONF_NAME,
    STATE_OFF,
    UnitOfTemperature,
)
from homeassistant.core import Context, HomeAssistant, callback
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
from .coordinator import TadoxProxyCoordinator
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState
from .regulation_config import RegulationConfig
from .util import (
    clamp,
    get_climate_attr_float,
    get_climate_attr_str,
    get_climate_hvac_mode,
    is_binary_sensor_on,
    now_utc,
)

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

DEFAULT_CONTROL_INTERVAL_S = 60

# Frost protect setpoint when window open forced.
FROST_PROTECT_C = 5.0

# Tado set_temperature resolution in HA is typically 0.1. We'll round.
ROUND_STEP_C = 0.1

# Command hygiene
MIN_SEND_DELTA_C = 0.2
MAX_STEP_UP_C = 0.5

# Urgent decreases (close valve) should bypass rate limit if significantly lower than current setpoint.
RATE_LIMIT_DECREASE_EPS_C = 0.05

# Will-heat epsilon: if setpoint > internal temp + eps => likely heating
WILL_HEAT_EPS_C = 0.3

# Resume behavior after window forced:
# - allow a one-time "jump" upwards without step limit if gap is large, to avoid minutes of crawling.
RESUME_JUMP_GAP_C = 2.0
RESUME_JUMP_ALLOWED_ONCE = True

# Fast recovery (bounded)
FAST_RECOVERY_MIN_INTERVAL_S = 20
FAST_RECOVERY_MAX_STEP_UP_C = 2.0

# Trigger thresholds (heuristics)
FAST_RECOVERY_ERROR_C = 1.2
FAST_RECOVERY_TARGET_GAP_C = 3.0

# Boost open margin: if Tado internal temp is close to setpoint, sometimes valve won't open; raise target slightly.
BOOST_OPEN_MARGIN_C = 0.5

# When in boost and we detect we still won't open, allow a fast step-up limit.
BOOST_FAST_STEP_UP_C = 2.0


# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: Any,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TadoxProxyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TadoxProxyThermostat(coordinator, entry)], update_before_add=True)


# -----------------------------------------------------------------------------
# Entity
# -----------------------------------------------------------------------------

class TadoxProxyThermostat(CoordinatorEntity[TadoxProxyCoordinator], ClimateEntity, RestoreEntity):
    """Proxy thermostat entity (Hybrid control)."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    def __init__(self, coordinator: TadoxProxyCoordinator, entry: Any) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._config = RegulationConfig.from_entry(entry)

        self._attr_name = self._config.name
        self._attr_unique_id = f"{entry.entry_id}_proxy"

        # Hybrid config is derived from tuning keys to preserve UI/Options compatibility.
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

        # State
        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._target_temperature: float = self._config.default_target_c

        # Last regulation result & reason
        self._last_regulation_result = None
        self._last_regulation_reason: str | None = None

        # Last command telemetry
        self._last_command_sent_ts: float | None = None
        self._last_command_step_limited: bool = False
        self._last_command_step_up_limit_c: float | None = None
        self._last_command_diff_c: float | None = None
        self._last_desired_target_c: float | None = None
        self._last_command_target_c: float | None = None

        self._last_sent_target_c: float | None = None
        self._last_sent_context_id: str | None = None
        self._last_sent_reason: str | None = None
        self._last_sent_mono: float | None = None

        # Window handling config (sensor-based)
        self._window_open_enabled: bool = bool(self._config.window_open_enabled)
        self._window_sensor_entity_id: str | None = self._config.window_sensor_entity_id
        self._window_open_delay_s: float = float(self._config.window_open_delay_min) * 60.0
        self._window_close_delay_s: float = float(self._config.window_close_delay_min) * 60.0

        # Window runtime state
        self._window_open: bool = False
        self._window_forced: bool = False
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

        # Effective command policy (normal vs fast recovery)
        self._effective_min_interval_s: float = float(self._config.min_command_interval_s)
        self._effective_step_up_c: float = MAX_STEP_UP_C
        self._fast_recovery_active: bool = False
        self._fast_recovery_reason: str | None = None
        self._unsub_fast_recovery_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # restore last state
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._hvac_mode = get_climate_hvac_mode(last_state, default=HVACMode.HEAT)
            except Exception:
                self._hvac_mode = HVACMode.HEAT

            try:
                t = get_climate_attr_float(last_state, "temperature")
                if t is not None:
                    self._target_temperature = float(t)
            except Exception:
                pass

        # window sensor subscription
        self._setup_window_sensor_subscription()

        # periodic regulation cycle
        async_track_time_interval(
            self.hass, self._async_regulation_cycle, timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S)
        )

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=self._attr_name,
            manufacturer="Tado",
            model="Tado X Proxy Thermostat",
        )

    @property
    def hvac_mode(self) -> HVACMode:
        return self._hvac_mode

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

    @property
    def temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def target_temperature(self) -> float:
        return self._target_temperature

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._target_temperature = float(temp)
            self.async_write_ha_state()
            # run regulation soon (do not wait full interval)
            await self._async_regulation_cycle(now_utc())

    @property
    def current_temperature(self) -> float | None:
        return self.coordinator.data.get("room_temp")

    @property
    def min_temp(self) -> float:
        return float(self._config.min_target_c)

    @property
    def max_temp(self) -> float:
        return float(self._config.max_target_c)

    # -----------------------------------------------------------------------------
    # Window handling (sensor-based)
    # -----------------------------------------------------------------------------

    def _setup_window_sensor_subscription(self) -> None:
        if self._unsub_window_sensor:
            self._unsub_window_sensor()
            self._unsub_window_sensor = None

        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return

        @callback
        def _window_sensor_changed(event: Any) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:
                return
            is_open = is_binary_sensor_on(new_state)
            self._handle_window_sensor_update(is_open)

        self._unsub_window_sensor = async_track_state_change_event(
            self.hass, [self._window_sensor_entity_id], _window_sensor_changed
        )

        # seed initial state
        st = self.hass.states.get(self._window_sensor_entity_id)
        if st is not None:
            self._handle_window_sensor_update(is_binary_sensor_on(st))

    def _cancel_window_open_timer(self) -> None:
        if self._unsub_window_open_timer:
            self._unsub_window_open_timer()
            self._unsub_window_open_timer = None

    def _cancel_window_close_timer(self) -> None:
        if self._unsub_window_close_timer:
            self._unsub_window_close_timer()
            self._unsub_window_close_timer = None

    def _cancel_window_tick(self) -> None:
        if self._unsub_window_tick:
            self._unsub_window_tick()
            self._unsub_window_tick = None

    def _start_window_tick(self) -> None:
        if self._unsub_window_tick:
            return

        def _tick(_: Any) -> None:
            self._update_window_timers()

        self._unsub_window_tick = async_track_time_interval(
            self.hass, lambda _: _tick(_), timedelta(seconds=1)
        )

    def _update_window_timers(self) -> None:
        now = time.monotonic()

        # open delay countdown
        if self._window_open_deadline_mono is not None:
            self._window_open_delay_remaining_s = max(0.0, self._window_open_deadline_mono - now)
            if self._window_open_delay_remaining_s <= 0.0:
                self._window_open_deadline_mono = None
                self._window_open_pending = False
                self._window_forced = True
                self._window_forced_reason = "window_open_forced"
        else:
            self._window_open_delay_remaining_s = 0.0

        # close hold countdown
        if self._window_close_deadline_mono is not None:
            self._window_close_hold_remaining_s = max(0.0, self._window_close_deadline_mono - now)
            if self._window_close_hold_remaining_s <= 0.0:
                self._window_close_deadline_mono = None
                self._window_close_hold_remaining_s = 0.0
                self._window_forced = False
                self._window_forced_reason = None
        else:
            if not self._window_forced:
                self._window_close_hold_remaining_s = 0.0

        # stop tick if no timers active
        if (self._window_open_deadline_mono is None) and (self._window_close_deadline_mono is None):
            self._cancel_window_tick()

        # Update state
        self.async_write_ha_state()

    def _handle_window_sensor_update(self, is_open: bool) -> None:
        self._window_open = bool(is_open)
        now = time.monotonic()

        if is_open:
            # cancel close hold; start open delay if not already forced
            self._cancel_window_close_timer()
            self._window_close_deadline_mono = None
            self._window_close_hold_remaining_s = 0.0

            if not self._window_forced:
                if self._window_open_delay_s <= 0.0:
                    self._window_forced = True
                    self._window_open_pending = False
                    self._window_open_deadline_mono = None
                    self._window_forced_reason = "window_open_forced"
                else:
                    self._window_open_pending = True
                    self._window_open_deadline_mono = now + self._window_open_delay_s
                    self._window_forced_reason = "window_open_pending"
                    self._start_window_tick()
        else:
            # window closed
            self._window_open_pending = False
            self._window_open_deadline_mono = None
            self._window_open_delay_remaining_s = 0.0

            if self._window_forced:
                # start close hold timer
                if self._window_close_delay_s <= 0.0:
                    self._window_forced = False
                    self._window_forced_reason = None
                    self._window_close_deadline_mono = None
                    self._window_close_hold_remaining_s = 0.0
                else:
                    self._window_close_deadline_mono = now + self._window_close_delay_s
                    self._window_forced_reason = "window_close_hold"
                    self._start_window_tick()

        self.async_write_ha_state()

        # run regulation soon
        asyncio.create_task(self._async_regulation_cycle(now_utc()))

    # -----------------------------------------------------------------------------
    # Regulation cycle
    # -----------------------------------------------------------------------------

    async def _async_regulation_cycle(self, _: Any) -> None:
        if self.hass is None:
            return

        # room temperature input
        room_temp = self.current_temperature
        if room_temp is None:
            self._last_regulation_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        setpoint_c = float(self._target_temperature)
        room_temp_c = float(room_temp)

        # determine heating enabled
        heating_enabled = self._hvac_mode != HVACMode.OFF

        # Hybrid compute
        dt_s = DEFAULT_CONTROL_INTERVAL_S
        res = self._regulator.compute_target(
            setpoint_c=setpoint_c,
            room_temp_c=room_temp_c,
            time_delta_s=dt_s,
            state=self._hybrid_state,
            heating_enabled=heating_enabled,
        )
        self._hybrid_state = res.new_state
        self._last_regulation_result = res

        # desired target from regulator
        desired_target_c = float(res.target_c)

        # apply window override
        window_forced = self._window_open_enabled and self._window_forced
        window_reason = self._window_forced_reason
        if window_forced:
            desired_target_c = float(FROST_PROTECT_C)

        # clamp desired target to config bounds (and round)
        desired_target_c = float(clamp(desired_target_c, self._config.min_target_c, self._config.max_target_c))
        desired_target_c = round(desired_target_c / ROUND_STEP_C) * ROUND_STEP_C

        # --- command policy & hygiene ---
        tado_setpoint = self._read_source_setpoint_c()
        if tado_setpoint is None:
            tado_setpoint = self.coordinator.data.get("tado_setpoint")

        # Decide fast recovery
        self._fast_recovery_active = False
        self._fast_recovery_reason = None
        self._effective_min_interval_s = float(self._config.min_command_interval_s)
        self._effective_step_up_c = MAX_STEP_UP_C

        gap = None
        if tado_setpoint is not None:
            gap = float(desired_target_c) - float(tado_setpoint)

        if not window_forced:
            if res.mode.value == "boost":
                self._fast_recovery_active = True
                self._fast_recovery_reason = "boost"
            elif abs(res.error_c) >= FAST_RECOVERY_ERROR_C:
                self._fast_recovery_active = True
                self._fast_recovery_reason = "room_error"
            elif gap is not None and gap >= FAST_RECOVERY_TARGET_GAP_C:
                self._fast_recovery_active = True
                self._fast_recovery_reason = "target_gap"

        if self._fast_recovery_active:
            self._effective_min_interval_s = float(FAST_RECOVERY_MIN_INTERVAL_S)
            self._effective_step_up_c = float(FAST_RECOVERY_MAX_STEP_UP_C)

        # Boost open guard: if Tado internal temp close to setpoint, raise desired a bit (helps valve open)
        tado_internal = self._read_source_internal_temp_c()
        if tado_internal is None:
            tado_internal = self.coordinator.data.get("tado_internal_temp")

        if not window_forced and tado_internal is not None:
            if res.mode.value == "boost":
                if float(desired_target_c) <= float(tado_internal) + BOOST_OPEN_MARGIN_C:
                    desired_target_c = float(tado_internal) + BOOST_OPEN_MARGIN_C + 0.1
                    desired_target_c = float(clamp(desired_target_c, self._config.min_target_c, self._config.max_target_c))

        # Decide command target (step limiting)
        command_target_c = float(desired_target_c)
        step_limited = False
        step_up_limit_c: float | None = None

        if tado_setpoint is not None:
            current_sp = float(tado_setpoint)

            if command_target_c > current_sp + 0.001:
                # step up
                step_up_limit_c = self._effective_step_up_c
                if step_up_limit_c is not None and (command_target_c - current_sp) > step_up_limit_c:
                    command_target_c = current_sp + step_up_limit_c
                    step_limited = True
            elif command_target_c < current_sp - 0.001:
                # decreases are urgent; no step limit by default
                pass

        # round command target
        command_target_c = round(command_target_c / ROUND_STEP_C) * ROUND_STEP_C

        # bookkeeping for telemetry
        self._last_desired_target_c = desired_target_c
        self._last_command_target_c = command_target_c
        self._last_command_step_limited = step_limited
        self._last_command_step_up_limit_c = step_up_limit_c if step_limited else None
        if tado_setpoint is not None:
            self._last_command_diff_c = round(float(command_target_c) - float(tado_setpoint), 3)
        else:
            self._last_command_diff_c = None

        # Decide if we should send
        should_send = False
        reason = None
        now_wall = time.time()

        # Min delta guard
        if tado_setpoint is not None:
            if abs(float(command_target_c) - float(tado_setpoint)) < MIN_SEND_DELTA_C:
                should_send = False
                reason = f"min_delta_guard({MIN_SEND_DELTA_C}C)"
            else:
                should_send = True
                reason = "normal_update"
        else:
            should_send = True
            reason = "normal_update(no_source_state)"

        # rate limit
        if should_send and self._last_command_sent_ts is not None:
            time_since_last_send = now_wall - float(self._last_command_sent_ts)
            if time_since_last_send < self._effective_min_interval_s:
                # Allow urgent decreases even within rate limit
                if tado_setpoint is not None:
                    current_sp = float(tado_setpoint)
                else:
                    current_sp = float(command_target_c)

                is_decrease = command_target_c < (current_sp - RATE_LIMIT_DECREASE_EPS_C)
                if is_decrease:
                    should_send = True
                    reason = "urgent_decrease"
                else:
                    remaining = int(max(0.0, self._effective_min_interval_s - time_since_last_send))
                    reason = f"rate_limited({remaining}s)"
                    should_send = False

        # Window forced must always be sent promptly (close valve).
        # Even if rate-limited, this should be treated as urgent decrease.
        if window_forced and tado_setpoint is not None:
            current_sp = float(tado_setpoint)
            if command_target_c < (current_sp - RATE_LIMIT_DECREASE_EPS_C):
                should_send = True
                reason = "window_open_forced"
            else:
                # do not spam; the valve is already closed enough
                should_send = False
                reason = "window_open_forced(no_change)"

        # Resume from window: allow a one-time jump to avoid minutes of crawl
        resume_from_window = self._last_window_forced and (not window_forced)
        resume_jump_allowed = False
        if resume_from_window and RESUME_JUMP_ALLOWED_ONCE:
            resume_jump_allowed = True

        if resume_jump_allowed and tado_setpoint is not None:
            current_sp = float(tado_setpoint)
            if (desired_target_c - current_sp) >= RESUME_JUMP_GAP_C:
                # ignore step limiting once
                command_target_c = float(desired_target_c)
                command_target_c = round(command_target_c / ROUND_STEP_C) * ROUND_STEP_C
                step_limited = False
                step_up_limit_c = None

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
            send_ctx = Context()
            await self._async_send_to_tado(command_target_c, context=send_ctx)
            self._last_command_sent_ts = now_wall
            self._last_regulation_reason = f"sent({reason})"

            self._last_sent_target_c = command_target_c
            self._last_sent_reason = reason
            self._last_sent_mono = time.monotonic()
            self._last_sent_context_id = send_ctx.id
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

    async def _async_send_to_tado(self, target_c: float, *, context: Context | None = None) -> None:
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
                context=context,
                blocking=True,
            )
        except Exception as err:
            _LOGGER.error("Failed to send command to Tado: %s", err)

    # -----------------------------------------------------------------------------
    # Properties
    # -----------------------------------------------------------------------------

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
            "tado_last_sent_context_id": self._last_sent_context_id,

            # window config/runtime
            "window_open_enabled": self._window_open_enabled,
            "window_sensor_entity_id": self._window_sensor_entity_id,
            "window_open": self._window_open,
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


# Commit: fix: add last-sent context id for service-call correlation
