"""Regulation cycle mixin for TadoXProxyClimate."""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SENSOR_LOSS_ACTION,
    PRESET_FROST_PROTECTION,
    SENSOR_LOSS_FALLBACK,
    SENSOR_LOSS_HOLD,
)
from .regulation import RegulationState

_LOGGER = logging.getLogger(__name__)


class RegulationMixin:
    """Regulation cycle methods extracted from TadoXProxyClimate."""

    def _write_state_with_binary_sensor(self) -> None:
        """Write HA state for this entity and linked sub-entities."""
        self.async_write_ha_state()
        bs = getattr(self.coordinator, "binary_sensor_entity", None)
        if bs is not None:
            bs.async_write_ha_state()
        se = getattr(self.coordinator, "sensor_entity", None)
        if se is not None:
            se.async_write_ha_state()

    async def _async_regulation_cycle_timer(self, _now) -> None:
        """Periodic timer callback."""
        await self._async_regulation_cycle(trigger="timer")

    async def _async_regulation_cycle(self, trigger: str) -> None:
        """Execute one control cycle."""
        async with self._regulation_lock:
            await self._async_regulation_cycle_locked(trigger)

    async def _async_regulation_cycle_locked(self, trigger: str) -> None:
        """Inner regulation cycle body, protected by _regulation_lock."""
        now = time.time()

        # Guard: skip when HVAC is OFF – the TRV has been turned off directly,
        # no regulation needed.
        if self._hvac_mode == HVACMode.OFF:
            self._last_reason = "hvac_off"
            self._write_state_with_binary_sensor()
            return

        # Guard: skip when coordinator data is stale (update method raised an exception).
        if not self.coordinator.last_update_success:
            _LOGGER.debug("Skipping regulation cycle – coordinator update failed")
            self._last_reason = "coordinator_unavailable"
            self._write_state_with_binary_sensor()
            return

        primary_temp = self.coordinator.data.get("room_temp")
        room_temp = primary_temp
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        if primary_temp is not None:
            self._last_valid_room_temp = primary_temp
            self._last_valid_room_temp_ts = self.coordinator.data.get("room_temp_ts") or now
            self._sensor_degraded = False
            control_mode = "primary"
        elif (
            self._last_valid_room_temp is not None
            and (now - self._last_valid_room_temp_ts) <= self._sensor_grace_s
        ):
            room_temp = self._last_valid_room_temp
            self._sensor_degraded = True
            control_mode = "grace"
            age = int(now - self._last_valid_room_temp_ts)
            _LOGGER.debug(
                "Room temp unavailable – using last valid %.1f°C (age %ds, grace %ds)",
                room_temp, age, self._sensor_grace_s,
            )
        else:
            self._sensor_degraded = True
            if self._last_valid_room_temp is not None:
                _LOGGER.warning(
                    "Room sensor unavailable and grace period expired "
                    "(last valid: %.1f°C, %ds ago, grace: %ds)",
                    self._last_valid_room_temp,
                    int(now - self._last_valid_room_temp_ts),
                    self._sensor_grace_s,
                )
            loss_action = self._config_entry.options.get(
                CONF_SENSOR_LOSS_ACTION,
                self._config_entry.data.get(CONF_SENSOR_LOSS_ACTION, SENSOR_LOSS_HOLD),
            )
            fallback_temp = self.coordinator.data.get("fallback_room_temp")
            if (
                loss_action == SENSOR_LOSS_FALLBACK
                and fallback_temp is not None
                and tado_internal is not None
            ):
                room_temp = fallback_temp
                control_mode = "fallback_sensor"
            elif loss_action == SENSOR_LOSS_HOLD and not self._safety_native_until_regulated:
                control_mode = "hold"
            else:
                control_mode = "native_trv"

        if self._preset_mode == PRESET_FROST_PROTECTION:
            control_mode = "frost"
            self._safety_native_until_regulated = True
        elif self._safety_native_until_regulated:
            if room_temp is not None and tado_internal is not None:
                self._safety_native_until_regulated = False
            else:
                control_mode = "native_trv"

        mode_changed = control_mode != self._active_control_mode
        if mode_changed:
            self._reg_state = RegulationState()
            self._last_regulation_ts = 0.0
            self._pending_mode_transition = control_mode != "hold"
        self._active_control_mode = control_mode

        if control_mode == "hold" or (
            control_mode in ("primary", "grace", "fallback_sensor")
            and tado_internal is None
        ):
            self._last_result = None
            self._last_reason = "waiting_for_sensors"
            self._write_state_with_binary_sensor()
            return

        dt = (now - self._last_regulation_ts) if self._last_regulation_ts > 0 else 0.0
        self._last_regulation_ts = now

        setpoint = self._effective_setpoint()

        if control_mode in ("native_trv", "frost"):
            target_c = max(self._config.min_target_c, min(self._config.max_target_c, setpoint))
            self._last_result = None
        else:
            result = self._regulator.compute(
                setpoint_c=setpoint,
                room_temp_c=room_temp,
                tado_internal_c=tado_internal,
                time_delta_s=dt,
                state=self._reg_state,
            )
            self._reg_state = result.new_state
            self._last_result = result
            target_c = result.target_for_tado_c

        # 5. Rate limiting & send decision
        # Prefer our own last-sent value (always fresh) over coordinator data
        # (which may be up to 60s stale from the polling interval).
        # NOTE: do NOT fall back to 0.0 – an unknown baseline makes the
        # urgent-decrease check (target < baseline - threshold) always False
        # for realistic temperatures, so a needed fast cooldown would be
        # suppressed.  Instead, skip rate limiting entirely when no baseline
        # is known (e.g. after a quick HVAC OFF → HEAT cycle).
        current_tado_setpoint = (
            self._last_sent_setpoint
            if self._last_sent_setpoint is not None
            else self.coordinator.data.get("tado_setpoint")  # None if Tado is off/unavailable
        )
        time_since_last = now - self._last_command_sent_ts
        is_rate_limited = time_since_last < self._config.min_command_interval_s

        should_send = False
        reason = "noop"

        if current_tado_setpoint is None:
            # No known Tado baseline – send to establish one.
            # On the very first attempt (_last_command_sent_ts == 0) we send
            # immediately.  On subsequent retries (e.g. after a failed send)
            # we honour the rate limiter so a transient TRV/backend outage
            # does not cause command spam every regulation cycle.
            if (
                is_rate_limited
                and self._last_command_sent_ts > 0
                and not self._pending_mode_transition
            ):
                remaining = int(self._config.min_command_interval_s - time_since_last)
                reason = f"rate_limited({remaining}s)"
            else:
                should_send = True
                reason = "no_baseline"
        else:
            diff = abs(target_c - current_tado_setpoint)

            # Overlay refresh: resend the same setpoint if overlay_refresh_s has
            # elapsed, keeping timer-based overlays alive (cloud-API integrations).
            overlay_refresh_due = (
                self._overlay_refresh_s > 0
                and time_since_last >= self._overlay_refresh_s
            )

            if diff < self._config.min_change_threshold_c and not overlay_refresh_due:
                reason = "already_at_target"
                self._pending_mode_transition = False
            elif diff < self._config.min_change_threshold_c and overlay_refresh_due:
                should_send = True
                reason = "overlay_refresh"
            elif self._pending_mode_transition:
                should_send = True
                reason = "mode_changed"
            elif is_rate_limited:
                is_urgent_decrease = (
                    target_c
                    < current_tado_setpoint - self._behaviour.urgent_decrease_threshold_c
                )
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
            await self._async_send_to_tado(target_c)
            self._last_command_sent_ts = now
            self._pending_mode_transition = False
            self._last_reason = f"sent({reason})"
        else:
            self._last_reason = reason

        self._write_state_with_binary_sensor()

    async def _async_send_hvac_mode_to_tado(self, mode: HVACMode) -> None:
        """Forward an HVAC mode change to the source Tado entity."""
        source_entity = self._config_entry.data.get("source_entity_id")
        if not source_entity:
            _LOGGER.warning("No source_entity_id configured – cannot send HVAC mode to Tado")
            return

        _LOGGER.debug("Sending HVAC mode %s to %s", mode, source_entity)

        try:
            async with asyncio.timeout(10):
                await self.hass.services.async_call(
                    domain="climate",
                    service="set_hvac_mode",
                    service_data={
                        "entity_id": source_entity,
                        "hvac_mode": mode,
                    },
                    blocking=True,
                )
            if mode == HVACMode.OFF:
                self._last_sent_setpoint = None
        except (TimeoutError, HomeAssistantError):
            _LOGGER.exception("Failed to send HVAC mode to Tado")
            raise

    async def _async_send_to_tado(self, target_c: float) -> None:
        """Send a temperature command to the source Tado entity."""
        source_entity = self._config_entry.data.get("source_entity_id")
        if not source_entity:
            _LOGGER.warning("No source_entity_id configured – cannot send command to Tado")
            return

        _LOGGER.debug("Sending %.1f°C to %s", target_c, source_entity)

        try:
            async with asyncio.timeout(10):
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
            self._last_sent_setpoint = target_c
        except (TimeoutError, HomeAssistantError):
            _LOGGER.exception("Failed to send command to Tado")
