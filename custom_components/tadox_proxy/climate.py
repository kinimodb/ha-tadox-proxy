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
)
from .regulation import PidRegulator, RegulationState

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Tado X Proxy climate entity."""
    # Retrieve the coordinator created in __init__.py
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Create the entity
    # We pass the full config entry to access ID and Title for Device Info
    entity = TadoXProxyClimate(
        coordinator=coordinator,
        unique_id=f"{entry.entry_id}",
        config_entry=entry,
    )
    
    async_add_entities([entity])


class TadoXProxyClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Proxy Climate Entity that controls a Tado X TRV via PID."""

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
        self._attr_name = None # Use translation key from HA
        
        # Configuration & Parameters
        self._config = RegulationConfig()
        
        # Internal State
        self._hvac_mode = HVACMode.HEAT
        self._target_temp = 20.0
        
        # PID Regulator & State Memory
        self._regulator = PidRegulator(self._config)
        self._pid_state = RegulationState() 
        
        # Operational Timestamps
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0

        # Diagnostics buffer
        self._last_regulation_result = None
        self._last_regulation_reason = "startup"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        # This connects the entity to the device in the registry
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=self._config_entry.title,
            manufacturer="Tado X Proxy",
            model="PID Regulator",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore state
        last_state = await self.async_get_last_state()
        if last_state:
            self._hvac_mode = last_state.state if last_state.state in self._attr_hvac_modes else HVACMode.HEAT
            if last_state.attributes.get(ATTR_TEMPERATURE):
                try:
                    self._target_temp = float(last_state.attributes[ATTR_TEMPERATURE])
                except (ValueError, TypeError):
                    self._target_temp = 20.0

        # Start the regulation timer
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_timer_tick,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S)
            )
        )

    @callback
    async def _async_timer_tick(self, _now) -> None:
        """Periodic timer trigger."""
        await self._async_regulation_cycle(trigger="timer")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        self._hvac_mode = hvac_mode
        self.async_write_ha_state() # Update UI immediately
        await self._async_regulation_cycle(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._target_temp = float(temp)
        self.async_write_ha_state() # Update UI immediately
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

        # 3. PID Computation
        reg_result = self._regulator.compute(
            setpoint_c=effective_setpoint,
            current_temp_c=room_temp,
            time_delta_s=dt,
            state=self._pid_state
        )
        
        self._pid_state = reg_result.new_state
        self._last_regulation_result = reg_result

        # 4. Calculate Command for Tado
        raw_command_target = effective_setpoint + reg_result.output_delta_c
        
        final_command_target = max(
            self._config.min_target_c, 
            min(self._config.max_target_c, raw_command_target)
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
            # FIX: Correct async_call syntax. domain and service must be lowercase arguments.
            await self.hass.services.async_call(
                domain="climate",
                service="set_temperature",
                service_data={
                    "entity_id": source_entity,
                    "temperature": target_c,
                    "hvac_mode": HVACMode.HEAT,
                },
                blocking=True
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
        attrs = {
            "control_interval_s": DEFAULT_CONTROL_INTERVAL_S,
            "regulation_reason": self._last_regulation_reason,
            "tado_internal_temperature_c": self.coordinator.data.get("tado_internal_temp"),
            "pid_p_term_c": 0,
            "pid_i_term_c": 0,
            "pid_d_term_c": 0,
            "pid_output_delta_c": 0,
        }
        
        if self._last_regulation_result:
            res = self._last_regulation_result
            attrs.update({
                "pid_output_delta_c": res.output_delta_c,
                "pid_p_term_c": res.p_term_c,
                "pid_i_term_c": res.i_term_c,
                "pid_d_term_c": res.d_term_c,
                "pid_error_c": res.error_c,
            })
            
        return attrs
