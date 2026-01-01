"""
Climate Entity for Tado X Proxy.
"""
from __future__ import annotations

import logging
import time
import datetime
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    FROST_PROTECT_C,
    RATE_LIMIT_DECREASE_EPS_C,
    WILL_HEAT_EPS_C,
    RegulationConfig,
    PidTuning,
)
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState

_LOGGER = logging.getLogger(__name__)


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
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_translation_key = "tadox_proxy"

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry):
        """Initialize the proxy thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None  # Use translation key from HA

        # Configuration & Parameters
        self._config = RegulationConfig()

        # Apply Tuning from Options Flow (if configured)
        if config_entry.options:
            opts = config_entry.options
            kp = opts.get("kp", self._config.tuning.kp)
            ki = opts.get("ki", self._config.tuning.ki)
            kd = opts.get("kd", self._config.tuning.kd)

            _LOGGER.debug(f"Loading custom control parameters: Kp={kp}, Ki={ki}, Kd={kd}")
            self._config.tuning = PidTuning(kp=kp, ki=ki, kd=kd)

        # Internal State
        self._hvac_mode = HVACMode.HEAT
        self._target_temp = 20.0

        # Hybrid Regulator & State Memory (new default in this branch)
        # Map existing Options Flow values (kp/ki/kd) onto hybrid config.
        # Note: kd is currently unused by the hybrid strategy.
        self._hybrid_config = HybridConfig(
            min_target_c=self._config.min_target_c,
            max_target_c=self._config.max_target_c,
            kp=self._config.tuning.kp,
            ki_small=self._config.tuning.ki,
            coast_target_c=FROST_PROTECT_C,
        )
        self._regulator = HybridRegulator(self._hybrid_config)
        self._hybrid_state = HybridState()

        # Operational Timestamps
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0

        # Diagnostics buffer
        self._last_regulation_result = None
        self._last_regulation_reason = "startup"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=self._config_entry.title,
            manufacturer="Tado X Proxy",
            model="Hybrid Regulator",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore state
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

        # Start the regulation timer
        async_track_time_interval(
            self.hass,
            self._async_regulation_timer_callback,
            datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
        )

        # Run one cycle immediately
        await self._async_regulation_cycle(trigger="startup")

    @callback
    def _async_regulation_timer_callback(self, now: datetime.datetime) -> None:
        """Timer callback to trigger regulation."""
        self.hass.async_create_task(self._async_regulation_cycle(trigger="timer"))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode (heat/off)."""
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._target_temp = float(temp)
        self.async_write_ha_state()  # Update UI immediately
        await self._async_regulation_cycle(trigger="set_temperature")

    # -----------------------------------------------------------------------
    # Core Regulation Logic
    # -----------------------------------------------------------------------

    async def _async_regulation_cycle(self, trigger: str) -> None:
        """Execute one control loop cycle."""
        now = time.time()

        # 1. Gather Inputs
        room_temp = self.coordinator.data.get("room_temp")
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        if room_temp is None or tado_internal is None:
            self._last_regulation_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        # Time delta calculation
        dt = 0.0
        if self._last_regulation_ts > 0:
            dt = now - self._last_regulation_ts
        self._last_regulation_ts = now

        # 2. Determine Effective Target
        effective_setpoint = self._target_temp
        if self._hvac_mode == HVACMode.OFF:
            effective_setpoint = FROST_PROTECT_C

        # 3. Hybrid Computation (absolute target)
        reg_result = self._regulator.compute_target(
            setpoint_c=effective_setpoint,
            room_temp_c=room_temp,
            time_delta_s=dt,
            state=self._hybrid_state,
            heating_enabled=(self._hvac_mode != HVACMode.OFF),
        )

        self._hybrid_state = reg_result.new_state
        self._last_regulation_result = reg_result

        # 4. Calculate Command for Tado (absolute target from regulator)
        final_command_target = max(
            self._config.min_target_c,
            min(self._config.max_target_c, reg_result.target_c),
        )

        final_command_target = round(final_command_target, 1)

        # 5. Rate Limiting
        should_send = False
        reason = "noop"

        current_tado_setpoint = self.coordinator.data.get("tado_setpoint", 0.0)

        diff = abs(final_command_target - current_tado_setpoint)
        time_since_last_send = now - self._last_command_sent_ts
        is_rate_limited = time_since_last_send < self._config.min_command_interval_s

        if diff < 0.1:
            reason = "already_at_target"
        elif is_rate_limited:
            is_decrease = (final_command_target < current_tado_setpoint - RATE_LIMIT_DECREASE_EPS_C)
            if is_decrease:
                should_send = True
                reason = "urgent_decrease"
            else:
                should_send = False
                reason = f"rate_limited({int(self._config.min_command_interval_s - time_since_last_send)}s)"
        else:
            should_send = True
            reason = "normal_update"

        # 6. Execute Command
        if should_send:
            await self._async_send_to_tado(final_command_target)
            self._last_command_sent_ts = now
            self._last_regulation_reason = f"sent({reason})"
        else:
            self._last_regulation_reason = reason

        self.async_write_ha_state()

    async def _async_send_to_tado(self, target_c: float) -> None:
        """Send command to the source entity."""
        source_entity = self.coordinator.config_entry.data.get("source_entity_id")
        if not source_entity:
            return

        _LOGGER.debug(f"Sending {target_c}°C to {source_entity}")

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
            _LOGGER.error(f"Failed to send command to Tado: {e}")

    # -----------------------------------------------------------------------
    # Properties for UI
    # -----------------------------------------------------------------------
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

        if tado_internal and tado_setpoint:
            if tado_setpoint > tado_internal + WILL_HEAT_EPS_C:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes."""
        tuning = self._config.tuning

        attrs = {
            "control_interval_s": DEFAULT_CONTROL_INTERVAL_S,
            "regulation_reason": self._last_regulation_reason,
            "regulator": "hybrid",
            "tado_internal_temperature_c": self.coordinator.data.get("tado_internal_temp"),

            # Legacy tuning keys (Options Flow currently uses kp/ki/kd)
            "pid_kp": tuning.kp,
            "pid_ki": tuning.ki,
            "pid_kd": tuning.kd,

            # Hybrid diagnostics
            "hybrid_kp": self._hybrid_config.kp,
            "hybrid_ki_small": self._hybrid_config.ki_small,
            "hybrid_bias_tau_s": self._hybrid_config.bias_tau_s,
            "hybrid_mode": self._hybrid_state.mode.value,
            "hybrid_bias_c": round(self._hybrid_state.bias_c, 3),
            "hybrid_i_small_c": round(self._hybrid_state.i_small_c, 3),
            "hybrid_dTdt_ema_c_per_min": round(self._hybrid_state.dTdt_ema_c_per_s * 60.0, 5),
        }

        if self._last_regulation_result:
            res = self._last_regulation_result
            attrs.update({
                "hybrid_target_c": res.target_c,
                "hybrid_mode": res.mode.value,
                "hybrid_mode_reason": res.debug_info.get("mode_reason"),
                "hybrid_error_c": res.error_c,
                "hybrid_p_term_c": res.p_term_c,
                "hybrid_i_small_c": res.i_small_c,
                "hybrid_bias_c": res.bias_c,
                "hybrid_dTdt_ema_c_per_min": round(res.dTdt_ema_c_per_s * 60.0, 5),
                "hybrid_predicted_temp_c": res.predicted_temp_c,
            })

        return attrs
