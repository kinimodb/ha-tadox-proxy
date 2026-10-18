"""Regulation cycle mixin for TadoXProxyClimate."""

from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError

from .climate_controllers import decide_send

_LOGGER = logging.getLogger(__name__)


class RegulationMixin:
    """Regulation cycle methods extracted from TadoXProxyClimate."""

    # Coordinator attributes under which sub-entities register themselves so
    # the climate entity can push its state changes to them.
    _LINKED_ENTITY_ATTRS = (
        "binary_sensor_entity",
        "sensor_entity",
        "guest_mode_switch_entity",
    )

    def _write_state_with_linked_entities(self) -> None:
        """Write HA state for this entity and linked sub-entities."""
        self.async_write_ha_state()
        self._write_linked_entities()

    def _write_linked_entities(self) -> None:
        """Write HA state for linked sub-entities that are already added."""
        for attr in self._LINKED_ENTITY_ATTRS:
            entity = getattr(self.coordinator, attr, None)
            if entity is not None and entity.hass is not None:
                entity.async_write_ha_state()

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
            self._write_state_with_linked_entities()
            return

        # Guard: skip when coordinator data is stale (update method raised an exception).
        if not self.coordinator.last_update_success:
            _LOGGER.debug("Skipping regulation cycle – coordinator update failed")
            self._last_reason = "coordinator_unavailable"
            self._write_state_with_linked_entities()
            return

        # 1. Gather sensor data from coordinator; short room-sensor gaps are
        # bridged with the last valid value
        room_temp = self._room_temp_bridge.resolve(
            self.coordinator.data.get("room_temp"),
            self.coordinator.data.get("room_temp_ts"),
            now,
        )
        tado_internal = self.coordinator.data.get("tado_internal_temp")

        if room_temp is None or tado_internal is None:
            self._last_reason = "waiting_for_sensors"
            self._write_state_with_linked_entities()
            return

        # 2. Time delta
        dt = (now - self._last_regulation_ts) if self._last_regulation_ts > 0 else 0.0
        self._last_regulation_ts = now

        # 3. Effective setpoint (considers HVAC mode + preset)
        setpoint = self._effective_setpoint()

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
        # Prefer our own last-sent value (always fresh) over coordinator data
        # (which may be up to 60s stale from the polling interval).  The
        # coordinator reports None while Tado is off/unavailable – an unknown
        # baseline is handled by decide_send, never replaced by 0.0.
        baseline = (
            self._last_sent_setpoint
            if self._last_sent_setpoint is not None
            else self.coordinator.data.get("tado_setpoint")
        )
        should_send, reason = decide_send(
            target_c=result.target_for_tado_c,
            baseline_c=baseline,
            last_command_ts=self._last_command_sent_ts,
            now=now,
            min_interval_s=self._config.min_command_interval_s,
            min_change_c=self._config.min_change_threshold_c,
            urgent_decrease_c=self._behaviour.urgent_decrease_threshold_c,
            overlay_refresh_s=self._overlay_refresh_s,
        )

        # 6. Send command to Tado
        if should_send:
            # Record the attempt BEFORE awaiting the service call: the TRV's
            # state update can arrive while the call is still running, and
            # follow-tado must then see it inside its grace window – otherwise
            # the proxy's own command is mistaken for a manual change.  A
            # failed send keeps the timestamp so retries honour the rate limit.
            self._last_command_sent_ts = now
            if await self._async_send_to_tado(result.target_for_tado_c):
                self._last_reason = f"sent({reason})"
            else:
                self._last_reason = f"send_failed({reason})"
        else:
            self._last_reason = reason

        self._write_state_with_linked_entities()

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

    async def _async_send_to_tado(self, target_c: float) -> bool:
        """Send a temperature command to the source Tado entity.

        Returns ``True`` on success, ``False`` if the command failed.
        """
        source_entity = self._config_entry.data.get("source_entity_id")
        if not source_entity:
            _LOGGER.warning("No source_entity_id configured – cannot send command to Tado")
            return False

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
            return False
        return True
