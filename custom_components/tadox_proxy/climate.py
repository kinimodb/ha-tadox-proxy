"""Climate Entity for Tado X Proxy."""
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    FROST_PROTECT_C,
    RegulationConfig,
    CorrectionTuning,
)
from .regulation import FeedforwardPiRegulator, RegulationState

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
        unique_id=entry.entry_id,
        config_entry=entry,
    )
    async_add_entities([entity])


class TadoXProxyClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Proxy climate entity that controls a Tado X TRV via feedforward + PI."""

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
        self._attr_name = None  # uses translation key

        # Build regulation config from defaults + options
        self._config = self._build_config(config_entry)
        self._regulator = FeedforwardPiRegulator(self._config)
        self._reg_state = RegulationState()

        # UI state
        self._hvac_mode = HVACMode.HEAT
        self._target_temp = 20.0

        # Timing
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0

        # Diagnostics
        self._last_result = None
        self._last_reason = "startup"

    @staticmethod
    def _build_config(entry: ConfigEntry) -> RegulationConfig:
        """Build regulation config, applying options over defaults."""
        config = RegulationConfig()
        opts = entry.options
        if opts:
            kp = opts.get("correction_kp", config.tuning.kp)
            ki = opts.get("correction_ki", config.tuning.ki)
            config.tuning = CorrectionTuning(kp=kp, ki=ki)
        return config

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._config_entry.entry_id)},
            name=self._config_entry.title,
            manufacturer="Tado X Proxy",
            model="Feedforward + PI Regulator",
        )

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to HA."""
        await super().async_added_to_hass()

        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state:
            if last_state.state in (HVACMode.HEAT, HVACMode.OFF):
                self._hvac_mode = HVACMode(last_state.state)
            temp = last_state.attributes.get(ATTR_TEMPERATURE)
            if temp is not None:
                try:
                    self._target_temp = float(temp)
                except (ValueError, TypeError):
                    pass

        # Start periodic regulation
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_regulation_cycle_timer,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
            )
        )

    async def _async_regulation_cycle_timer(self, _now) -> None:
        """Periodic timer callback."""
        await self._async_regulation_cycle(trigger="timer")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._target_temp = float(temp)
        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="set_temperature")

    # ------------------------------------------------------------------
    # Core regulation
    # ------------------------------------------------------------------

    async def _async_regulation_cycle(self, trigger: str) -> None:
        """Execute one control cycle."""
        now = time.time()

        # 1. Gather sensor data from coordinator
        room_temp = self.coordinator.data.get("room_temp")
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        if room_temp is None or tado_internal is None:
            self._last_reason = "waiting_for_sensors"
            self.async_write_ha_state()
            return

        # 2. Time delta
        dt = (now - self._last_regulation_ts) if self._last_regulation_ts > 0 else 0.0
        self._last_regulation_ts = now

        # 3. Effective setpoint (frost protection when OFF)
        setpoint = FROST_PROTECT_C if self._hvac_mode == HVACMode.OFF else self._target_temp

        # 4. Compute regulation
        result = self._regulator.compute(
            setpoint_c=setpoint,
            room_temp_c=room_temp,
            tado_internal_c=tado_internal,
            time_delta_s=dt,
            state=self._reg_state,
        )
        self._reg_state = result.new_state
        self._last_result = result

        # 5. Rate limiting & send decision
        current_tado_setpoint = self.coordinator.data.get("tado_setpoint", 0.0)
        diff = abs(result.target_for_tado_c - current_tado_setpoint)
        time_since_last = now - self._last_command_sent_ts
        is_rate_limited = time_since_last < self._config.min_command_interval_s

        should_send = False
        reason = "noop"

        if diff < self._config.min_change_threshold_c:
            reason = "already_at_target"
        elif is_rate_limited:
            # Allow immediate decrease (e.g. user lowered setpoint significantly)
            is_urgent_decrease = result.target_for_tado_c < current_tado_setpoint - 1.0
            if is_urgent_decrease:
                should_send = True
                reason = "urgent_decrease"
            else:
                remaining = int(self._config.min_command_interval_s - time_since_last)
                reason = f"rate_limited({remaining}s)"
        else:
            should_send = True
            reason = "normal_update"

        # 6. Send command to Tado
        if should_send:
            await self._async_send_to_tado(result.target_for_tado_c)
            self._last_command_sent_ts = now
            self._last_reason = f"sent({reason})"
        else:
            self._last_reason = reason

        self.async_write_ha_state()

    async def _async_send_to_tado(self, target_c: float) -> None:
        """Send a temperature command to the source Tado entity."""
        source_entity = self._config_entry.data.get("source_entity_id")
        if not source_entity:
            return

        _LOGGER.debug("Sending %.1f°C to %s", target_c, source_entity)

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
        except Exception:
            _LOGGER.exception("Failed to send command to Tado")

    # ------------------------------------------------------------------
    # Properties for HA UI
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature from the external sensor."""
        return self.coordinator.data.get("room_temp")

    @property
    def target_temperature(self) -> float | None:
        """Return the user-set target temperature."""
        return self._target_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Infer heating/idle from the Tado entity state."""
        if self._hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        tado_internal = self.coordinator.data.get("tado_internal_temp")
        tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if tado_setpoint > tado_internal + 0.05:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes visible in HA Developer Tools."""
        attrs: dict[str, Any] = {
            "regulation_reason": self._last_reason,
            "tado_internal_temp_c": self.coordinator.data.get("tado_internal_temp"),
            "correction_kp": self._config.tuning.kp,
            "correction_ki": self._config.tuning.ki,
        }

        if self._last_result:
            r = self._last_result
            attrs.update({
                "feedforward_offset_c": r.feedforward_offset_c,
                "p_correction_c": r.p_correction_c,
                "i_correction_c": r.i_correction_c,
                "error_c": r.error_c,
                "target_for_tado_c": r.target_for_tado_c,
                "is_saturated": r.is_saturated,
            })

        return attrs
