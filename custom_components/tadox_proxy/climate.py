"""
Climate Entity for Tado X Proxy.
"""
from __future__ import annotations

import datetime
import logging
import time
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_WINDOW_OPEN_ENABLED,
    CONF_WINDOW_SENSOR_ENTITY_ID,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    FROST_PROTECT_C,
    RATE_LIMIT_DECREASE_EPS_C,
    WILL_HEAT_EPS_C,
    RegulationConfig,
    PidTuning,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command hygiene (send path)
# ---------------------------------------------------------------------------

MIN_SEND_DELTA_C: float = 0.2
MAX_STEP_UP_C: float = 0.5

# Adaptive BOOST step-up: open valve quickly up to (tado_internal + margin),
# then return to conservative step-up.
BOOST_OPEN_MARGIN_C: float = 0.5
BOOST_FAST_STEP_UP_C: float = 2.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tado X Proxy climate entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity = TadoXProxyClimate(
        coordinator=coordinator,
        unique_id=f"{entry.entry_id}",
        config_entry=entry,
    )
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

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry):
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None

        self._config = RegulationConfig()

        if config_entry.options:
            opts = config_entry.options
            kp = opts.get("kp", self._config.tuning.kp)
            ki = opts.get("ki", self._config.tuning.ki)
            kd = opts.get("kd", self._config.tuning.kd)
            _LOGGER.debug("Loading custom control parameters: Kp=%s Ki=%s Kd=%s", kp, ki, kd)
            self._config.tuning = PidTuning(kp=kp, ki=ki, kd=kd)

        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._target_temp: float = 20.0

        self._hybrid_config = HybridConfig(
            min_target_c=self._config.min_target_c,
            max_target_c=self._config.max_target_c,
            coast_target_c=FROST_PROTECT_C,
            kp=self._config.tuning.kp,
            ki_small=min(self._config.tuning.ki, 0.001),
        )

        # If a trend-based window-open latch exists in HybridConfig, disable it:
        # window handling is controlled via binary_sensor + Options Flow.
        if hasattr(self._hybrid_config, "window_open_enabled"):
            try:
                setattr(self._hybrid_config, "window_open_enabled", False)
            except Exception:
                pass

        self._regulator = HybridRegulator(self._hybrid_config)
        self._hybrid_state = HybridState()

        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0

        self._last_regulation_result = None
        self._last_regulation_reason = "startup"

        self._last_current_tado_setpoint_c: float | None = None
        self._last_desired_target_c: float | None = None
        self._last_command_target_c: float | None = None
        self._last_command_step_limited: bool = False
        self._last_command_step_up_limit_c: float | None = None
        self._last_command_diff_c: float | None = None

        # Window handling (sensor-based)
        self._window_open_enabled: bool = bool(
            config_entry.options.get(CONF_WINDOW_OPEN_ENABLED, False)
        )
        self._window_sensor_entity_id: str | None = config_entry.options.get(
            CONF_WINDOW_SENSOR_ENTITY_ID
        )
        self._window_open_active: bool = False
        self._window_open_triggered: bool = False
        self._unsub_window_sensor = None

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

        last_state = await self.async_get_last_state()
        if last_state:
            self._hvac_mode = (
                last_state.state
                if last_state.state in self._attr_hvac_modes
                else HVACMode.HEAT
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
            datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
        )

        await self._async_regulation_cycle(trigger="startup")

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_window_sensor:
            try:
                self._unsub_window_sensor()
            finally:
                self._unsub_window_sensor = None
        await super().async_will_remove_from_hass()

    def _async_setup_window_sensor(self) -> None:
        if self._unsub_window_sensor:
            try:
                self._unsub_window_sensor()
            finally:
                self._unsub_window_sensor = None

        self._window_open_triggered = False
        self._window_open_active = False

        if not self._window_open_enabled or not self._window_sensor_entity_id:
            return

        st = self.hass.states.get(self._window_sensor_entity_id)
        if st is not None:
            self._window_open_active = (st.state == "on")

        @callback
        def _handle_window_change(event) -> None:
            new_state = event.data.get("new_state")
            is_open = new_state is not None and new_state.state == "on"

            if is_open and not self._window_open_active:
                self._window_open_triggered = True

            self._window_open_active = is_open
            self.hass.async_create_task(self._async_regulation_cycle(trigger="window_sensor"))
            self.async_write_ha_state()

        self._unsub_window_sensor = async_track_state_change_event(
            self.hass,
            [self._window_sensor_entity_id],
            _handle_window_change,
        )

    @callback
    def _async_regulation_timer_callback(self, now: datetime.datetime) -> None:
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

    async def _async_regulation_cycle(self, trigger: str) -> None:
        now = time.time()

        room_temp = self.coordinator.data.get("room_temp")
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        if room_temp is None or tado_internal is None:
            self._last_regulation_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        dt = 0.0
        if self._last_regulation_ts > 0:
            dt = now - self._last_regulation_ts
        self._last_regulation_ts = now

        effective_setpoint = self._target_temp
        if self._hvac_mode == HVACMode.OFF:
            effective_setpoint = FROST_PROTECT_C

        window_forced = bool(
            self._window_open_enabled and self._window_sensor_entity_id and self._window_open_active
        )

        reg_result = self._regulator.compute_target(
            setpoint_c=effective_setpoint,
            room_temp_c=room_temp,
            time_delta_s=dt,
            state=self._hybrid_state,
            heating_enabled=(self._hvac_mode != HVACMode.OFF) and (not window_forced),
        )

        self._hybrid_state = reg_result.new_state
        self._last_regulation_result = reg_result

        desired_target_c = max(
            self._config.min_target_c,
            min(self._config.max_target_c, reg_result.target_c),
        )

        if window_forced:
            desired_target_c = FROST_PROTECT_C

        desired_target_c = round(desired_target_c, 1)

        current_tado_setpoint = self.coordinator.data.get("tado_setpoint")
        command_target_c = desired_target_c
        step_limited = False
        step_up_limit_c = None

        if current_tado_setpoint is not None:
            if desired_target_c > (current_tado_setpoint + 0.05):
                max_step_up = MAX_STEP_UP_C
                if getattr(reg_result.mode, "value", None) == "boost":
                    if current_tado_setpoint < (tado_internal + BOOST_OPEN_MARGIN_C):
                        max_step_up = max(MAX_STEP_UP_C, BOOST_FAST_STEP_UP_C)

                stepped = min(desired_target_c, current_tado_setpoint + max_step_up)
                step_limited = (stepped != desired_target_c)
                command_target_c = stepped
                step_up_limit_c = max_step_up

            command_target_c = round(command_target_c, 1)

        self._last_current_tado_setpoint_c = current_tado_setpoint
        self._last_desired_target_c = desired_target_c
        self._last_command_target_c = command_target_c
        self._last_command_step_limited = step_limited
        self._last_command_step_up_limit_c = step_up_limit_c
        self._last_command_diff_c = (
            abs(command_target_c - current_tado_setpoint)
            if current_tado_setpoint is not None
            else None
        )

        should_send = False
        reason = "noop"

        if current_tado_setpoint is None:
            should_send = True
            reason = "init_unknown_current_setpoint"
        else:
            diff = abs(command_target_c - current_tado_setpoint)
            time_since_last_send = now - self._last_command_sent_ts
            is_rate_limited = time_since_last_send < self._config.min_command_interval_s

            if diff < MIN_SEND_DELTA_C:
                reason = f"min_delta_guard({MIN_SEND_DELTA_C}C)"
            elif is_rate_limited:
                is_decrease = (command_target_c < current_tado_setpoint - RATE_LIMIT_DECREASE_EPS_C)
                if is_decrease:
                    should_send = True
                    reason = "urgent_decrease"
                else:
                    reason = f"rate_limited({int(self._config.min_command_interval_s - time_since_last_send)}s)"
            else:
                should_send = True
                reason = "normal_update"

        if step_limited and step_up_limit_c is not None:
            reason = f"{reason}|step_up_limited({step_up_limit_c}C)"
        if window_forced:
            reason = f"{reason}|window_open_forced"

        if should_send:
            await self._async_send_to_tado(command_target_c)
            self._last_command_sent_ts = now
            self._last_regulation_reason = f"sent({reason})"
        else:
            self._last_regulation_reason = reason

        self.async_write_ha_state()

    async def _async_send_to_tado(self, target_c: float) -> None:
        source_entity = self.coordinator.config_entry.data.get("source_entity_id")
        if not source_entity:
            return

        _LOGGER.debug("Sending %s°C to %s", target_c, source_entity)

        try:
            await self.hass.services.async_call(
                domain="climate",
                service="set_temperature",
                service_data={
                    "entity_id": source_entity,
                    "temperature": target_c,
                    "hvac_mode": HVACMode.HEAT,
                },
                blocking=True,
            )
        except Exception as e:
            _LOGGER.error("Failed to send command to Tado: %s", e)

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

        tado_internal = self.coordinator.data.get("tado_internal_temp")
        tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if tado_setpoint > tado_internal + WILL_HEAT_EPS_C:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tuning = self._config.tuning

        attrs: dict[str, Any] = {
            "control_interval_s": DEFAULT_CONTROL_INTERVAL_S,
            "regulation_reason": self._last_regulation_reason,
            "tado_internal_temperature_c": self.coordinator.data.get("tado_internal_temp"),
            "tado_setpoint_c": self._last_current_tado_setpoint_c,
            "regulator": "hybrid",

            "window_open_enabled": self._window_open_enabled,
            "window_sensor_entity_id": self._window_sensor_entity_id,
            "window_open": self._window_open_active,

            "command_min_send_delta_c": MIN_SEND_DELTA_C,
            "command_max_step_up_c": MAX_STEP_UP_C,
            "command_boost_open_margin_c": BOOST_OPEN_MARGIN_C,
            "command_boost_fast_step_up_c": BOOST_FAST_STEP_UP_C,
            "hybrid_desired_target_c": self._last_desired_target_c,
            "hybrid_command_target_c": self._last_command_target_c,
            "hybrid_command_step_limited": self._last_command_step_limited,
            "hybrid_command_step_up_limit_c": self._last_command_step_up_limit_c,
            "hybrid_command_diff_c": self._last_command_diff_c,

            "pid_kp": tuning.kp,
            "pid_ki": tuning.ki,
            "pid_kd": tuning.kd,

            "hybrid_kp": self._hybrid_config.kp,
            "hybrid_ki_small": self._hybrid_config.ki_small,

            "hybrid_mode": self._hybrid_state.mode.value,
            "hybrid_bias_c": round(self._hybrid_state.bias_c, 3),
            "hybrid_i_small_c": round(self._hybrid_state.i_small_c, 3),
            "hybrid_dTdt_ema_c_per_min": round(self._hybrid_state.dTdt_ema_c_per_s * 60.0, 5),

            "hybrid_window_open_triggered": self._window_open_triggered,
            "hybrid_window_open_remaining_s": 0.0,
            "hybrid_window_open_active": self._window_open_active,
        }

        if self._last_regulation_result:
            res = self._last_regulation_result
            mode_reason = res.debug_info.get("mode_reason")
            if self._window_open_active and self._window_open_enabled:
                mode_reason = "window_open_active"

            attrs.update(
                {
                    "hybrid_target_c": res.target_c,
                    "hybrid_error_c": res.error_c,
                    "hybrid_p_term_c": res.p_term_c,
                    "hybrid_mode_reason": mode_reason,
                    "hybrid_predicted_temp_c": res.predicted_temp_c,
                }
            )

        return attrs
