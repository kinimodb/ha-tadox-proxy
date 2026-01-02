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
from datetime import timedelta
from typing import Any, cast

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature
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
from homeassistant.core import Event, HomeAssistant, callback
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
from .hybrid_regulation import HybridConfig, HybridRegulator, HybridState, WindowMode
from .parameters import PidTuning
from .regulation import CommandPolicy, RegulationMode
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
# Defaults / constants
# -----------------------------------------------------------------------------

DEFAULT_MIN_TEMP_C = 5.0
DEFAULT_MAX_TEMP_C = 25.0
DEFAULT_TARGET_TEMP_C = 21.0

# Command hygiene defaults (fallback)
DEFAULT_MIN_COMMAND_INTERVAL_S = 60.0
DEFAULT_MIN_SETPOINT_DELTA_C = 0.5
DEFAULT_STEP_UP_LIMIT_C = 2.0

# Window frost protection
WINDOW_FROST_TEMP_C = 5.0

# Telemetry keys used in attributes
ATTR_TELEMETRY = "tadox_telemetry"

# Update intervals
COORDINATOR_UPDATE_INTERVAL = timedelta(seconds=30)
CONTROL_LOOP_INTERVAL = timedelta(seconds=30)

# Fast recovery defaults (if enabled in policy)
FAST_RECOVERY_MAX_C = 3.0
FAST_RECOVERY_DURATION_S = 20 * 60

# Boost-like behavior thresholds
BOOST_DELTA_TRIGGER_C = 1.5
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
    """Proxy thermostat that controls a Tado X climate entity via setpoint writes.

    The entity uses an external room temperature sensor as the controlled variable and
    writes a computed target setpoint to the underlying Tado thermostat entity.
    """

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]

    def __init__(self, coordinator: TadoxProxyCoordinator, entry: Any) -> None:
        super().__init__(coordinator)

        self._hass: HomeAssistant = coordinator.hass
        self._entry = entry

        # Configuration derived from config entry
        self._reg_cfg: RegulationConfig = RegulationConfig.from_entry(entry)
        self._name: str = self._reg_cfg.name

        # Hybrid regulator setup
        # Map PidTuning -> HybridConfig (kp, ki_small)
        tuning: PidTuning = self._reg_cfg.tuning
        self._hybrid_cfg = HybridConfig(
            kp=tuning.kp,
            ki_small=tuning.ki,
        )
        self._regulator = HybridRegulator(self._hybrid_cfg)

        # Command policy
        self._policy = CommandPolicy(
            min_command_interval_s=self._reg_cfg.min_command_interval_s,
            min_setpoint_delta_c=DEFAULT_MIN_SETPOINT_DELTA_C,
            step_up_limit_c=DEFAULT_STEP_UP_LIMIT_C,
            fast_recovery_max_c=FAST_RECOVERY_MAX_C,
            fast_recovery_duration_s=FAST_RECOVERY_DURATION_S,
        )

        # State
        self._hvac_mode: HVACMode = HVACMode.HEAT
        self._hvac_action: HVACAction = HVACAction.IDLE

        self._target_temperature: float = DEFAULT_TARGET_TEMP_C
        self._last_command_ts: float = 0.0
        self._last_sent_setpoint: float | None = None
        self._last_sent_ts: float | None = None

        # Window handling
        self._window_open: bool = False
        self._window_mode: WindowMode = WindowMode.CLOSED
        self._window_open_timer_cancel: Any = None
        self._window_close_timer_cancel: Any = None

        # Telemetry
        self._telemetry: dict[str, Any] = {}
        self._telemetry[ATTR_TELEMETRY] = {}

        # Control loop
        self._unsub_control_loop: Any = None
        self._unsub_window_sensor: Any = None

        # Underlying target (Tado climate entity id)
        # NOTE: In this branch, the coordinator is expected to provide the write target via data
        # or via entry data; adjust as needed in your architecture.
        self._tado_entity_id: str | None = None
        if isinstance(entry.data, dict):
            self._tado_entity_id = cast(str | None, entry.data.get("tado_entity_id"))

        # External room temperature sensor (entity id)
        self._room_sensor_entity_id: str | None = None
        if isinstance(entry.data, dict):
            self._room_sensor_entity_id = cast(str | None, entry.data.get("room_sensor_entity_id"))

        # Optional internal temperature sensor (tado’s own)
        self._tado_temp_entity_id: str | None = None
        if isinstance(entry.data, dict):
            self._tado_temp_entity_id = cast(str | None, entry.data.get("tado_temp_entity_id"))

        # Internal: remember last external temperature and last room temp timestamp
        self._last_room_temp: float | None = None
        self._last_room_temp_ts: float | None = None

        # Regulation mode
        self._regulation_mode: RegulationMode = RegulationMode.AUTO

        # Diagnostics / debugging helpers
        self._restore_state_done: bool = False

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
    def target_temperature(self) -> float | None:
        return self._target_temperature

    @property
    def min_temp(self) -> float:
        return self._reg_cfg.min_target_c if hasattr(self._reg_cfg, "min_target_c") else DEFAULT_MIN_TEMP_C

    @property
    def max_temp(self) -> float:
        return self._reg_cfg.max_target_c if hasattr(self._reg_cfg, "max_target_c") else DEFAULT_MAX_TEMP_C

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._telemetry.get(ATTR_TELEMETRY, {})

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore state
        await self._async_restore_state()

        # Subscribe to window sensor changes if configured
        self._setup_window_subscription()

        # Start periodic control loop
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
        if self._restore_state_done:
            return

        last_state = await self.async_get_last_state()
        if last_state is None:
            self._restore_state_done = True
            return

        # Restore target temp and hvac mode
        restored_mode = get_climate_hvac_mode(last_state, HVACMode.HEAT)
        self._hvac_mode = restored_mode

        restored_target = get_climate_attr_float(last_state, "temperature", DEFAULT_TARGET_TEMP_C)
        if restored_target is not None:
            self._target_temperature = float(restored_target)

        # Restore underlying entity id if present in attributes
        restored_tado = get_climate_attr_str(last_state, "tado_entity_id", None)
        if restored_tado:
            self._tado_entity_id = restored_tado

        self._restore_state_done = True

    # -------------------------------------------------------------------------
    # Window handling
    # -------------------------------------------------------------------------

    def _setup_window_subscription(self) -> None:
        if not self._reg_cfg.window_open_enabled:
            _LOGGER.debug("Window handling disabled by config")
            return

        entity_id = self._reg_cfg.window_sensor_entity_id
        if not entity_id:
            _LOGGER.warning("Window handling enabled but no window sensor entity configured")
            return

        # Initial state
        self._window_open = is_binary_sensor_on(self._hass, entity_id)
        self._window_mode = WindowMode.OPEN if self._window_open else WindowMode.CLOSED

        @callback
        def _on_window_event(event: Event) -> None:
            self._handle_window_event(event)

        self._unsub_window_sensor = async_track_state_change_event(
            self._hass,
            [entity_id],
            _on_window_event,
        )

    @callback
    def _handle_window_event(self, event: Event) -> None:
        if not self._reg_cfg.window_open_enabled:
            return

        entity_id = self._reg_cfg.window_sensor_entity_id
        if not entity_id:
            return

        new_state = event.data.get("new_state")
        if new_state is None:
            return

        is_open = new_state.state == "on"
        if is_open == self._window_open:
            return

        self._window_open = is_open

        if is_open:
            # Cancel close timer
            if self._window_close_timer_cancel is not None:
                self._window_close_timer_cancel()
                self._window_close_timer_cancel = None

            delay = max(0, int(self._reg_cfg.window_open_delay_min)) * 60
            if delay > 0:
                if self._window_open_timer_cancel is not None:
                    self._window_open_timer_cancel()
                self._window_open_timer_cancel = async_call_later(
                    self._hass, delay, self._apply_window_open
                )
                self._set_telemetry("window_pending", "open_delay")
            else:
                self._apply_window_open(None)
        else:
            # Cancel open timer
            if self._window_open_timer_cancel is not None:
                self._window_open_timer_cancel()
                self._window_open_timer_cancel = None

            delay = max(0, int(self._reg_cfg.window_close_delay_min)) * 60
            if delay > 0:
                if self._window_close_timer_cancel is not None:
                    self._window_close_timer_cancel()
                self._window_close_timer_cancel = async_call_later(
                    self._hass, delay, self._apply_window_close
                )
                self._set_telemetry("window_pending", "close_hold")
            else:
                self._apply_window_close(None)

        self.async_write_ha_state()

    @callback
    def _apply_window_open(self, _: Any) -> None:
        self._window_mode = WindowMode.OPEN
        self._set_telemetry("window_mode", "open")
        self._set_telemetry("window_pending", None)
        self._window_open_timer_cancel = None

        # Apply frost protection immediately (do not destroy regulator state)
        # We do not change target_temperature state; we only override the outgoing command.
        self._schedule_control_tick()
        self.async_write_ha_state()

    @callback
    def _apply_window_close(self, _: Any) -> None:
        self._window_mode = WindowMode.CLOSED
        self._set_telemetry("window_mode", "closed")
        self._set_telemetry("window_pending", None)
        self._window_close_timer_cancel = None

        # Resume regulation after hold
        self._schedule_control_tick()
        self.async_write_ha_state()

    # -------------------------------------------------------------------------
    # Control loop
    # -------------------------------------------------------------------------

    def _start_control_loop(self) -> None:
        @callback
        def _tick(_: Any) -> None:
            self._schedule_control_tick()

        self._unsub_control_loop = async_track_time_interval(self._hass, _tick, CONTROL_LOOP_INTERVAL)

        # immediate first tick
        self._schedule_control_tick()

    @callback
    def _schedule_control_tick(self) -> None:
        self._hass.async_create_task(self._async_control_tick())

    async def _async_control_tick(self) -> None:
        # If OFF, ensure underlying is set to off or frost? Here: do nothing for now.
        if self._hvac_mode == HVACMode.OFF:
            self._hvac_action = HVACAction.OFF
            self._set_telemetry("mode", "off")
            self.async_write_ha_state()
            return

        room_temp = self._read_room_temperature()
        tado_temp = self._read_tado_internal_temperature()
        tado_setpoint = self._read_tado_setpoint()

        self._set_telemetry("room_temp", room_temp)
        self._set_telemetry("tado_temp", tado_temp)
        self._set_telemetry("tado_setpoint", tado_setpoint)

        if room_temp is None:
            self._hvac_action = HVACAction.IDLE
            self._set_telemetry("status", "no_room_temp")
            self.async_write_ha_state()
            return

        # Window override: frost protection
        if self._window_mode == WindowMode.OPEN:
            target = WINDOW_FROST_TEMP_C
            reason = "window_open_frost"
            await self._async_send_setpoint(target, reason=reason)
            self._hvac_action = HVACAction.HEATING if (tado_setpoint or 0) > 0 else HVACAction.IDLE
            self.async_write_ha_state()
            return

        # Regulation
        now = time.time()
        desired = self._target_temperature

        # Hybrid regulator decides "commanded setpoint"
        cmd, state = self._regulator.compute(
            room_temp=room_temp,
            target_temp=desired,
            now_ts=now,
        )
        self._set_telemetry("hybrid_state", state.value)
        self._set_telemetry("hybrid_cmd", cmd)

        # Policy/hygiene
        decided = self._policy.apply(
            desired_setpoint=cmd,
            last_sent_setpoint=self._last_sent_setpoint,
            last_sent_ts=self._last_sent_ts,
            now_ts=now,
        )
        self._set_telemetry("policy_decision", decided.reason)
        self._set_telemetry("policy_send", decided.send)

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
        """Read external room temperature sensor."""
        if not self._room_sensor_entity_id:
            # fallback: coordinator may provide room temp
            val = self.coordinator.data.get("room_temp") if self.coordinator.data else None
            try:
                return float(val) if val is not None else None
            except (ValueError, TypeError):
                return None

        st = self._hass.states.get(self._room_sensor_entity_id)
        if st is None:
            return None
        try:
            temp = float(st.state)
        except (ValueError, TypeError):
            return None

        self._last_room_temp = temp
        self._last_room_temp_ts = time.time()
        return temp

    def _read_tado_internal_temperature(self) -> float | None:
        if self._tado_temp_entity_id:
            st = self._hass.states.get(self._tado_temp_entity_id)
            if st is None:
                return None
            try:
                return float(st.state)
            except (ValueError, TypeError):
                return None

        val = self.coordinator.data.get("tado_internal_temp") if self.coordinator.data else None
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def _read_tado_setpoint(self) -> float | None:
        val = self.coordinator.data.get("tado_setpoint") if self.coordinator.data else None
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    # -------------------------------------------------------------------------
    # Commands / writes
    # -------------------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Handle user-set target temperature on the proxy entity."""
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        try:
            self._target_temperature = float(temp)
        except (ValueError, TypeError):
            return

        self._set_telemetry("user_target", self._target_temperature)
        self.async_write_ha_state()

        # Trigger immediate tick
        await self._async_control_tick()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._hvac_mode = hvac_mode
        self._set_telemetry("user_hvac_mode", hvac_mode.value)
        self.async_write_ha_state()
        await self._async_control_tick()

    async def _async_send_setpoint(self, setpoint_c: float, reason: str) -> None:
        """Send setpoint to underlying Tado climate entity via service call."""
        if not self._tado_entity_id:
            self._set_telemetry("send_error", "no_tado_entity_id")
            return

        setpoint_c = clamp(setpoint_c, self.min_temp, self.max_temp)

        # Track outgoing
        self._last_sent_setpoint = setpoint_c
        self._last_sent_ts = time.time()

        self._set_telemetry("tado_last_sent_setpoint", setpoint_c)
        self._set_telemetry("tado_last_sent_reason", reason)
        self._set_telemetry("tado_last_sent_ts", now_utc().isoformat())

        service_data = {
            ATTR_ENTITY_ID: self._tado_entity_id,
            ATTR_TEMPERATURE: setpoint_c,
        }

        await self._hass.services.async_call(
            "climate",
            "set_temperature",
            service_data,
            blocking=True,
        )

    # -------------------------------------------------------------------------
    # Telemetry helpers
    # -------------------------------------------------------------------------

    def _set_telemetry(self, key: str, value: Any) -> None:
        tel = self._telemetry.setdefault(ATTR_TELEMETRY, {})
        tel[key] = value

    # -------------------------------------------------------------------------
    # Coordinator updates
    # -------------------------------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        self.async_write_ha_state()
