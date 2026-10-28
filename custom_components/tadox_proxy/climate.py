"""Climate Entity for Tado X Proxy."""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import time
from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
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
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .climate_controllers import (
    CHANGE_APPLY,
    CONTROL_BACKUP_SENSOR,
    DEFAULT_SENSOR_LOSS_ACTION,
    BoostController,
    ControlModeResolver,
    FollowPhysicalController,
    ManualReturnController,
    PresenceAutomationController,
    SavedState,
    WindowAutomationController,
    preset_after_restart,
    route_change,
)
from .climate_presets import PresetMixin
from .climate_regulation import RegulationMixin
from .const import (
    ATTR_GUEST_MODE,
    ATTR_MANUAL_RETURN_AT,
    ATTR_MANUAL_RETURN_PRESET,
    ATTR_MANUAL_RETURN_TARGET,
    ATTR_MANUAL_TARGET,
    ATTR_WINDOW_RESTORE_PRESET,
    ATTR_WINDOW_RESTORE_TARGET,
    ATTR_WINDOW_TRV_OFF,
    CONF_AWAY_TARGET,
    CONF_BOOST_DURATION,
    CONF_BOOST_TARGET,
    CONF_CORRECTION_KI,
    CONF_CORRECTION_KP,
    CONF_ECO_TARGET,
    CONF_FOLLOW_GRACE_S,
    CONF_FOLLOW_TADO_INPUT,
    CONF_FOLLOW_THRESHOLD_C,
    CONF_FROST_PROTECTION_TARGET,
    CONF_GAIN_FINE_MULTIPLIER,
    CONF_GAIN_FINE_THRESHOLD_C,
    CONF_GAIN_SCHEDULING,
    CONF_GAIN_STARTUP_MULTIPLIER,
    CONF_GAIN_STARTUP_THRESHOLD_C,
    CONF_INTEGRAL_DEADBAND_C,
    CONF_MANUAL_MODE_ACTION,
    CONF_MIN_CHANGE_THRESHOLD_C,
    CONF_MIN_COMMAND_INTERVAL_S,
    CONF_OVERLAY_REFRESH_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_SENSOR_GRACE_S,
    CONF_SENSOR_LOSS_ACTION,
    CONF_URGENT_DECREASE_THRESHOLD_C,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
    DEFAULT_MANUAL_MODE_ACTION,
    DOMAIN,
    MANUAL_MODE_TIMER,
    PRESET_FROST_PROTECTION,
    PRESET_LIST,
    safe_float,
)
from .device import proxy_device_info
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_OVERLAY_REFRESH_S,
    DEFAULT_SENSOR_GRACE_S,
    DEFAULT_WINDOW_DELAY_S,
    BehaviourConfig,
    CorrectionTuning,
    PresetConfig,
    RegulationConfig,
)
from .regulation import FeedforwardPiRegulator, RegulationState

_LOGGER = logging.getLogger(__name__)


def _source_state_available(state: State | None) -> bool:
    """Return whether the source thermostat can currently be reached."""
    return state is not None and state.state not in ("unavailable", "unknown")


class _ProxyRestoreData(ExtraStoredData):
    """Keep the desired climate state when HA hides unavailable attributes."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def as_dict(self) -> dict[str, Any]:
        return self._values


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
    coordinator.climate_entity = entity
    async_add_entities([entity])


class TadoXProxyClimate(RegulationMixin, PresetMixin, CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Proxy climate entity that controls a Tado X TRV via feedforward + PI."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = PRESET_LIST
    _attr_translation_key = "tadox_proxy"
    # Class-level defaults so HA's CachedProperties metaclass sees 5/30
    # BEFORE super().__init__() runs (prevents fallback to HA's 7/35).
    _attr_min_temp: float = 5.0
    _attr_max_temp: float = 30.0

    def __init__(self, coordinator, unique_id: str, config_entry: ConfigEntry):
        """Initialize the proxy thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._config_entry = config_entry
        self._attr_name = None  # uses translation key

        # Build regulation + behaviour config from defaults + options
        self._config = self._build_config(config_entry)
        self._attr_min_temp = self._config.min_target_c
        self._attr_max_temp = self._config.max_target_c
        # Invalidate HA's CachedProperties cache for min/max_temp so the
        # instance-level values from _config take effect immediately.
        self.__dict__.pop("min_temp", None)
        self.__dict__.pop("max_temp", None)
        self._behaviour = self._build_behaviour(config_entry)
        self._regulator = FeedforwardPiRegulator(self._config)
        self._reg_state = RegulationState()

        # UI state
        self._hvac_mode = HVACMode.HEAT
        # Base target for COMFORT (= comfort target) and PRESET_NONE (= manual)
        self._target_temp: float = self._comfort_target()
        self._preset_mode: str = PRESET_COMFORT

        # Timing
        self._regulation_lock = asyncio.Lock()
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0
        self._last_sent_setpoint: float | None = None

        # Sensor resilience: bridges short room-sensor gaps and applies the
        # sensor-loss action once the grace period has expired
        self._control = ControlModeResolver(
            config_entry.options.get(CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S),
            self._sensor_loss_action(config_entry),
        )

        # Overlay refresh: optional periodic resend for cloud-API integrations
        # with timer-based overlays (e.g. exabird ha-tado-x uses 30 min TIMER).
        # Value of 0 means disabled (default, correct for Matter/Thread).
        self._overlay_refresh_s: int = config_entry.options.get(
            CONF_OVERLAY_REFRESH_S, DEFAULT_OVERLAY_REFRESH_S
        )

        # State-machine controllers (hold their own timer + saved-state)
        self._window_ctrl = WindowAutomationController()
        self._presence_ctrl = PresenceAutomationController()
        self._boost = BoostController()
        self._manual_return = ManualReturnController()

        # Diagnostics
        self._last_result = None
        self._last_reason = "startup"

    # ------------------------------------------------------------------
    # Config builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_config(entry: ConfigEntry) -> RegulationConfig:
        """Build regulation config, applying options over defaults."""
        config = RegulationConfig()
        opts = entry.options
        if opts:
            kp = opts.get(CONF_CORRECTION_KP, config.tuning.kp)
            ki = opts.get(CONF_CORRECTION_KI, config.tuning.ki)
            config.tuning = CorrectionTuning(kp=kp, ki=ki)
            config.presets = PresetConfig(
                eco_target_c=opts.get(CONF_ECO_TARGET, config.presets.eco_target_c),
                boost_target_c=opts.get(CONF_BOOST_TARGET, config.presets.boost_target_c),
                boost_duration_min=opts.get(CONF_BOOST_DURATION, config.presets.boost_duration_min),
                away_target_c=opts.get(CONF_AWAY_TARGET, config.presets.away_target_c),
                frost_protection_target_c=opts.get(CONF_FROST_PROTECTION_TARGET, config.presets.frost_protection_target_c),
            )
            # Ensure max_target_c is at least as high as boost_target_c
            config.max_target_c = max(config.max_target_c, config.presets.boost_target_c)
            # Adaptive gain scheduling
            config.gain_scheduling_enabled = opts.get(
                CONF_GAIN_SCHEDULING, config.gain_scheduling_enabled
            )
            config.gain_fine_multiplier = opts.get(
                CONF_GAIN_FINE_MULTIPLIER, config.gain_fine_multiplier
            )
            config.gain_startup_multiplier = opts.get(
                CONF_GAIN_STARTUP_MULTIPLIER, config.gain_startup_multiplier
            )
            config.gain_startup_threshold_c = opts.get(
                CONF_GAIN_STARTUP_THRESHOLD_C, config.gain_startup_threshold_c
            )
            config.gain_fine_threshold_c = opts.get(
                CONF_GAIN_FINE_THRESHOLD_C, config.gain_fine_threshold_c
            )
            config.min_command_interval_s = opts.get(
                CONF_MIN_COMMAND_INTERVAL_S, config.min_command_interval_s
            )
            config.min_change_threshold_c = opts.get(
                CONF_MIN_CHANGE_THRESHOLD_C, config.min_change_threshold_c
            )
            config.integral_deadband_c = opts.get(
                CONF_INTEGRAL_DEADBAND_C, config.integral_deadband_c
            )
        return config

    @staticmethod
    def _build_behaviour(entry: ConfigEntry) -> BehaviourConfig:
        """Build behaviour config, applying options over defaults."""
        defaults = BehaviourConfig()
        opts = entry.options
        if not opts:
            return defaults
        return BehaviourConfig(
            follow_threshold_c=opts.get(CONF_FOLLOW_THRESHOLD_C, defaults.follow_threshold_c),
            follow_grace_s=opts.get(CONF_FOLLOW_GRACE_S, defaults.follow_grace_s),
            urgent_decrease_threshold_c=opts.get(
                CONF_URGENT_DECREASE_THRESHOLD_C, defaults.urgent_decrease_threshold_c
            ),
        )

    @staticmethod
    def _sensor_loss_action(entry: ConfigEntry) -> str:
        """Return the configured sensor-loss action (options > data > default).

        New entries store it in data at setup; older entries have none and
        keep holding the last command.
        """
        return entry.options.get(
            CONF_SENSOR_LOSS_ACTION,
            entry.data.get(CONF_SENSOR_LOSS_ACTION, DEFAULT_SENSOR_LOSS_ACTION),
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the proxy."""
        return proxy_device_info(self._config_entry)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to HA."""
        await super().async_added_to_hass()

        # Window sensor state now: None while it has not reported yet (e.g. a
        # Zigbee/MQTT sensor that comes up after this integration).
        window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
        window_state = self.hass.states.get(window_sensor) if window_sensor else None
        window_open: bool | None = False
        if window_sensor:
            if window_state is None or window_state.state in ("unavailable", "unknown"):
                window_open = None
            else:
                window_open = window_state.state == "on"
        resume_window = False

        # Restore previous state
        last_state = await self.async_get_last_state()
        if last_state:
            attrs = dict(last_state.attributes)
            if extra_data := await self.async_get_last_extra_data():
                attrs.update(extra_data.as_dict())
            restored_hvac_mode = attrs.get("desired_hvac_mode", last_state.state)
            if restored_hvac_mode in (HVACMode.HEAT, HVACMode.OFF):
                self._hvac_mode = HVACMode(restored_hvac_mode)
            # Only the Manual preset carries its own temperature.  The
            # ATTR_TEMPERATURE attribute is the *effective* setpoint (5°C when
            # OFF, the eco/boost/frost target otherwise) and must not become
            # the base target – it is only a fallback for states written
            # before manual_target_c existed, and only while heating.
            manual = safe_float(attrs.get(ATTR_MANUAL_TARGET))
            if manual is None and last_state.state == HVACMode.HEAT:
                manual = safe_float(attrs.get(ATTR_TEMPERATURE))
            # Boost and frost protection are not restored as such; an open
            # window's frost protection gives way to the preset it saved.
            restart = preset_after_restart(
                attrs.get("preset_mode"),
                manual,
                SavedState(
                    attrs.get(ATTR_WINDOW_RESTORE_PRESET),
                    safe_float(attrs.get(ATTR_WINDOW_RESTORE_TARGET)),
                ),
                window_open=window_open,
                trv_off=attrs.get(ATTR_WINDOW_TRV_OFF) is True,
            )
            self._preset_mode = restart.preset
            if restart.manual_c is not None:
                self._target_temp = restart.manual_c
            resume_window = restart.resume_window
            if restart.switch_trv_on:
                # Still off from the window: the first cycle switches it on.
                self._window_ctrl.mark_trv_off()
            if attrs.get(ATTR_GUEST_MODE) is True:
                self._presence_ctrl.set_guest_mode(True)
            previous = attrs.get(ATTR_MANUAL_RETURN_PRESET)
            deadline = safe_float(attrs.get(ATTR_MANUAL_RETURN_AT))
            manual_target = safe_float(attrs.get(ATTR_MANUAL_RETURN_TARGET))
            if (
                self._config_entry.options.get(CONF_MANUAL_MODE_ACTION, DEFAULT_MANUAL_MODE_ACTION)
                == MANUAL_MODE_TIMER
                and previous in PRESET_LIST
                and deadline is not None
                and deadline > 0
                and manual_target is not None
            ):
                self._manual_return.restore(
                    self.hass, previous, manual_target, deadline,
                    self._async_manual_return_expired,
                )

        # For every named preset the comfort target from options is the
        # authoritative base (may have changed via the number entity while
        # HA was down).  Only PRESET_NONE (manual) keeps its own temperature.
        if self._preset_mode != PRESET_NONE:
            self._target_temp = self._comfort_target()

        # Initialize baseline for follow-tado from current tado setpoint so
        # the feature works immediately without waiting for the first regulation.
        tado_sp = self.coordinator.data.get("tado_setpoint")
        if tado_sp is not None and self._last_sent_setpoint is None:
            self._last_sent_setpoint = tado_sp

        # Config entry update listener (from number/switch entities)
        self.async_on_remove(
            self._config_entry.add_update_listener(self._async_config_entry_updated)
        )

        # State change listener on source Tado entity (follow physical thermostat)
        source_entity = self._config_entry.data.get("source_entity_id")
        if source_entity:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [source_entity],
                    self._async_tado_state_changed,
                )
            )

        # Presence sensor listener.  Evaluated before the window: it works on
        # the preset an open window will restore.
        presence_sensor = self._config_entry.options.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [presence_sensor],
                    self._async_presence_changed,
                )
            )
            # Evaluate current state after restart
            presence_state = self.hass.states.get(presence_sensor)
            presence = presence_state.state if presence_state else None
            if self._presence_ctrl.guest_mode:
                _LOGGER.info("Startup: guest mode active, presence automation suspended")
            elif presence in (None, "off", "unavailable", "unknown"):
                if self._preset_mode == PRESET_AWAY:
                    # Preset AWAY was restored from state but the controller's
                    # is_active flag is not persisted.  Restore a pending
                    # timed Manual choice, otherwise use COMFORT, so that
                    # coming home restores a useful preset instead of
                    # AWAY → AWAY (no-op).  This
                    # also covers a sensor that is not available yet at boot –
                    # otherwise its later "on" would find an inactive
                    # controller and AWAY would stay forever.
                    if self._manual_return.is_active:
                        self._presence_ctrl.activate(
                            PRESET_NONE, self._manual_return.manual_target_c
                        )
                    else:
                        self._presence_ctrl.activate(
                            PRESET_COMFORT, self._comfort_target()
                        )
                    _LOGGER.info(
                        "Startup: preset AWAY restored, controller pre-activated "
                        "with %s as saved state",
                        self._presence_ctrl.get_saved().preset,
                    )
                elif presence == "off":
                    self._schedule_presence_away()
            elif self._preset_mode == PRESET_AWAY:
                # Presence shows home but preset was restored as AWAY.
                # This happens when the user returned while HA was down.
                # A timed Manual change saved while Away must take effect
                # until its original deadline, not be replaced by Comfort.
                if self._manual_return.is_active:
                    self._preset_mode = PRESET_NONE
                    self._target_temp = self._manual_return.manual_target_c
                else:
                    self._preset_mode = PRESET_COMFORT
                    self._target_temp = self._comfort_target()
                _LOGGER.info(
                    "Startup: presence is home but preset was AWAY, switching to %s",
                    self._preset_mode,
                )

        # Window sensor listener
        if window_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [window_sensor],
                    self._async_window_changed,
                )
            )
            # Evaluate current state after restart
            if resume_window:
                # Open before the restart and still open: frost protection
                # goes on without a new open delay.
                self._window_ctrl.activate(self._preset_mode, self._target_temp)
                self._preset_mode = PRESET_FROST_PROTECTION
                _LOGGER.info("Startup: window still open, frost protection resumed")
            elif window_open:
                delay = self._config_entry.options.get(
                    CONF_WINDOW_DELAY_S, DEFAULT_WINDOW_DELAY_S
                )
                self._window_ctrl.handle_window_opened(
                    self.hass, delay, self._async_window_action
                )
                _LOGGER.info("Startup: window sensor is open, action in %ds", delay)

        # Start periodic regulation
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_regulation_cycle_timer,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
            )
        )

        # Sub-entities (e.g. the guest-mode switch) may have been added before
        # this restore ran – refresh them with the restored state.
        self._write_linked_entities()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel all timers when the entity is being removed."""
        self._window_ctrl.cancel_all()
        self._presence_ctrl.cancel_timer()
        self._boost.cancel()
        self._manual_return.cancel()
        await super().async_will_remove_from_hass()

    async def _async_config_entry_updated(self, hass, entry) -> None:
        """Called when config entry options change (e.g. from number entities)."""
        self._config_entry = entry
        self._config = self._build_config(entry)
        self._attr_min_temp = self._config.min_target_c
        self._attr_max_temp = self._config.max_target_c
        self.__dict__.pop("min_temp", None)
        self.__dict__.pop("max_temp", None)
        self._behaviour = self._build_behaviour(entry)
        self._regulator.cfg = self._config
        self._control.grace_s = entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        self._control.loss_action = self._sensor_loss_action(entry)
        self._overlay_refresh_s = entry.options.get(
            CONF_OVERLAY_REFRESH_S, DEFAULT_OVERLAY_REFRESH_S
        )
        # Only sync comfort target when COMFORT preset is active; PRESET_NONE
        # (manual) keeps its independently set temperature.
        if self._preset_mode == PRESET_COMFORT:
            self._target_temp = self._comfort_target()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Follow physical thermostat
    # ------------------------------------------------------------------

    @callback
    def _async_tado_state_changed(self, event) -> None:
        """Track source availability and follow physical changes if enabled."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if _source_state_available(new_state) != _source_state_available(old_state):
            self.async_write_ha_state()
            self.hass.async_create_task(self._async_source_availability_changed())

        if not self._config_entry.options.get(CONF_FOLLOW_TADO_INPUT, False):
            return
        if new_state is None or new_state.state in ("unavailable", "unknown"):
            return

        new_temp_attr = new_state.attributes.get("temperature")
        old_temp_attr = old_state.attributes.get("temperature") if old_state else None
        if new_temp_attr is None or new_temp_attr == old_temp_attr:
            return

        tado_setpoint = safe_float(new_temp_attr)
        if tado_setpoint is None:
            return

        if not FollowPhysicalController.should_follow(
            tado_setpoint=tado_setpoint,
            last_sent=self._last_sent_setpoint,
            last_sent_ts=self._last_command_sent_ts,
            threshold_c=self._behaviour.follow_threshold_c,
            grace_s=self._behaviour.follow_grace_s,
        ):
            return

        # Don't override window frost protection or presence-away automation.
        if self._window_ctrl.is_active:
            _LOGGER.info("Follow-tado ignored: window automation active (frost protection)")
            return
        if self._presence_ctrl.is_active:
            _LOGGER.info("Follow-tado ignored: presence automation active (away)")
            return

        _LOGGER.info(
            "Physical Tado change detected: %.1f°C → following (last sent: %.1f°C)",
            tado_setpoint,
            self._last_sent_setpoint,
        )
        self._target_temp = tado_setpoint
        self._begin_manual_return(tado_setpoint)
        self._preset_mode = PRESET_NONE
        self._boost.cancel()
        self.async_write_ha_state()
        # Trigger immediate regulation so the new target takes effect fast.
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="follow_tado")
        )

    async def _async_source_availability_changed(self) -> None:
        """Refresh source readings and resume regulation after reconnection."""
        await self.coordinator.async_refresh()
        await self._async_regulation_cycle(trigger="source_availability")

    # ------------------------------------------------------------------
    # Standard climate controls
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        # Hold the regulation lock for the whole transition: a cycle whose
        # setpoint command (sent with hvac_mode=heat) is still in flight must
        # not interleave with turning the TRV off.  The follow-up cycle runs
        # the lock-free body – asyncio.Lock is not re-entrant.
        async with self._regulation_lock:
            previous_mode = self._hvac_mode
            previous_result = self._last_result
            self._hvac_mode = hvac_mode
            if hvac_mode == HVACMode.OFF:
                # No regulation while OFF: error_c and the corrections must not
                # linger as if still current – also not while the TRV command
                # below is on its way.
                self._last_result = None
            self.async_write_ha_state()

            # Forward HVAC mode to the source TRV entity
            if hvac_mode == HVACMode.OFF:
                try:
                    await self._async_send_hvac_mode_to_tado(HVACMode.OFF)
                except (TimeoutError, HomeAssistantError):
                    # Command failed – revert local state so the proxy stays in sync
                    # with the TRV (which may still be heating).  An open window
                    # keeps its frost protection.
                    self._hvac_mode = previous_mode
                    self._last_result = previous_result
                    self._last_reason = "hvac_off_failed"
                    self.async_write_ha_state()
                    return
                # Turning the heating OFF ends the window detection for an open
                # window so the user's intention is respected – only now that
                # the TRV is off, so a failed command leaves the window in
                # charge.  The saved preset comes back; otherwise the entity
                # would stay in frost protection with no automation left to end
                # it.  Only for OFF: a HEAT request (e.g. climate.turn_on while
                # already heating) must not end frost protection.
                self._apply_window_restore(self._window_ctrl.end())
                self._last_reason = "sent(hvac_off)"
                self.async_write_ha_state()
            elif hvac_mode == HVACMode.HEAT and previous_mode == HVACMode.OFF:
                # Returning from OFF → reset timestamp so the first HEAT cycle uses
                # dt=0 and avoids an integral spike from the long OFF period.
                self._last_regulation_ts = 0
                # Re-evaluate window sensor: if the window is still open after
                # OFF→HEAT, restart frost protection so we don't heat into the void.
                window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
                if window_sensor:
                    ws = self.hass.states.get(window_sensor)
                    if ws and ws.state == "on":
                        delay = self._config_entry.options.get(
                            CONF_WINDOW_DELAY_S, DEFAULT_WINDOW_DELAY_S
                        )
                        self._window_ctrl.handle_window_opened(
                            self.hass, delay, self._async_window_action
                        )
                        _LOGGER.info(
                            "HVAC OFF→HEAT: window still open, frost action in %ds",
                            delay,
                        )
                # Reactivate the TRV, then run regulation to send the correct setpoint.
                # If set_hvac_mode fails, still run the cycle: every setpoint
                # command carries hvac_mode=heat, so it retries the activation
                # (rate limited) instead of aborting with an unhandled error.
                try:
                    await self._async_send_hvac_mode_to_tado(HVACMode.HEAT)
                except (TimeoutError, HomeAssistantError):
                    _LOGGER.warning(
                        "HVAC OFF→HEAT: activating the TRV failed, retrying via setpoint command"
                    )
                await self._async_regulation_cycle_locked(trigger="hvac_mode_change")
            else:
                await self._async_regulation_cycle_locked(trigger="hvac_mode_change")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature; enters manual (PRESET_NONE) mode.

        Moving the slider is treated as a temporary manual override. It does
        NOT change the stored comfort target – use the Comfort number entity
        or the options flow for that.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        try:
            temp_f = float(temp)
        except (ValueError, TypeError):
            return
        if not math.isfinite(temp_f):
            _LOGGER.warning("Ignoring non-finite temperature: %s", temp)
            return
        # Clamp to safe range
        temp_f = max(self._config.min_target_c, min(self._config.max_target_c, temp_f))

        # A manual change during the window close delay ends the delay right
        # away (same as a preset change): the window's saved preset comes back
        # first.  Must run before the routing below – the controller stays
        # active until the delay expires.
        close_delay_ended = self._end_window_close_delay()

        # While presence away or an open window holds the room, the
        # temperature is saved for when that automation ends.
        route = route_change(
            PRESET_NONE,
            presence_away=self._presence_ctrl.is_active,
            window_open=self._window_ctrl.is_active,
        )
        self._begin_manual_return(temp_f)
        if route != CHANGE_APPLY:
            await self._async_save_change(
                route, PRESET_NONE, temp_f, close_delay_ended=close_delay_ended
            )
            return

        self._target_temp = temp_f

        # Any direct temperature change activates manual mode and cancels
        # any running boost timer.
        if self._preset_mode != PRESET_NONE:
            self._boost.cancel()
            self._preset_mode = PRESET_NONE

        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="set_temperature")

    # ------------------------------------------------------------------
    # Properties for HA UI
    # ------------------------------------------------------------------

    @property
    def _source_available(self) -> bool:
        source_entity = self._config_entry.data.get("source_entity_id")
        source_state = self.hass.states.get(source_entity) if source_entity else None
        return _source_state_available(source_state)

    @property
    def available(self) -> bool:
        """The proxy cannot control an unavailable source thermostat."""
        return super().available and self._source_available

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Persist the requested mode and preset even while unavailable."""
        values: dict[str, Any] = {
            "desired_hvac_mode": self._hvac_mode,
            "preset_mode": self._preset_mode,
            ATTR_GUEST_MODE: self._presence_ctrl.guest_mode,
        }
        if self._preset_mode == PRESET_NONE:
            values[ATTR_MANUAL_TARGET] = self._target_temp
        if self._window_ctrl.is_active:
            saved = self._window_ctrl.get_saved()
            values[ATTR_WINDOW_RESTORE_PRESET] = saved.preset
            values[ATTR_WINDOW_RESTORE_TARGET] = saved.temp
        if self._window_ctrl.trv_off:
            values[ATTR_WINDOW_TRV_OFF] = True
        if self._manual_return.is_active:
            values[ATTR_MANUAL_RETURN_PRESET] = self._manual_return.previous_preset
            values[ATTR_MANUAL_RETURN_AT] = self._manual_return.end_ts
            values[ATTR_MANUAL_RETURN_TARGET] = self._manual_return.manual_target_c
        return _ProxyRestoreData(values)

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature the proxy regulates on.

        That is the backup sensor while it stands in for the external one.
        Under direct TRV control nothing is shown: the TRV's own reading is
        not a room temperature.
        """
        if self._control.mode == CONTROL_BACKUP_SENSOR:
            return self.coordinator.data.get("backup_room_temp")
        return self.coordinator.data.get("room_temp")

    @property
    def target_temperature(self) -> float | None:
        """Return the effective setpoint so HA always shows the active target."""
        return self._effective_setpoint()

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction | None:
        """Infer heating/idle from the Tado entity state."""
        if not self._source_available:
            return None
        if self._hvac_mode == HVACMode.OFF or self._window_ctrl.trv_off:
            return HVACAction.OFF

        tado_internal = self.coordinator.data.get("tado_internal_temp")
        tado_setpoint = self.coordinator.data.get("tado_setpoint")

        if tado_internal is not None and tado_setpoint is not None:
            if tado_setpoint > tado_internal + 0.05:
                return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def icon(self) -> str:
        """Return preset-specific icon so the primary entity icon reflects the active preset."""
        if self._hvac_mode == HVACMode.OFF:
            return "mdi:power"
        return {
            PRESET_COMFORT: "mdi:sofa",
            PRESET_ECO: "mdi:leaf",
            PRESET_BOOST: "mdi:rocket-launch",
            PRESET_AWAY: "mdi:home-export-outline",
            PRESET_FROST_PROTECTION: "mdi:snowflake",
            PRESET_NONE: "mdi:hand-back-right",
        }.get(self._preset_mode, "mdi:thermostat")

    @property
    def preset_mode(self) -> str:
        """Return the current preset mode."""
        return self._preset_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes visible in HA Developer Tools."""
        attrs: dict[str, Any] = {
            "regulation_reason": self._last_reason,
            "tado_internal_temp_c": self.coordinator.data.get("tado_internal_temp"),
            "correction_kp": self._config.tuning.kp,
            "correction_ki": self._config.tuning.ki,
            "effective_setpoint_c": self._effective_setpoint(),
            "window_open_active": self._window_ctrl.is_active,
            "window_close_delay_active": self._window_ctrl.close_delay_active,
            "presence_away_active": self._presence_ctrl.is_active,
            ATTR_GUEST_MODE: self._presence_ctrl.guest_mode,
            "sensor_degraded": self._control.degraded,
            "active_control_mode": self._control.mode,
            "overlay_refresh_s": self._overlay_refresh_s,
        }
        # Persisted so a restart restores the manual temperature instead of
        # the effective setpoint (which is 5°C while HVAC is OFF).
        if self._preset_mode == PRESET_NONE:
            attrs[ATTR_MANUAL_TARGET] = self._target_temp
        if self._manual_return.is_active:
            attrs[ATTR_MANUAL_RETURN_PRESET] = self._manual_return.previous_preset
            attrs[ATTR_MANUAL_RETURN_AT] = self._manual_return.end_ts
            attrs[ATTR_MANUAL_RETURN_TARGET] = self._manual_return.manual_target_c
        # Persisted so a restart brings back what the open window saved.
        if self._window_ctrl.is_active:
            saved = self._window_ctrl.get_saved()
            attrs[ATTR_WINDOW_RESTORE_PRESET] = saved.preset
            attrs[ATTR_WINDOW_RESTORE_TARGET] = saved.temp
        if self._window_ctrl.trv_off:
            attrs[ATTR_WINDOW_TRV_OFF] = True

        # Sensor resilience diagnostics
        control = self._control
        if control.last_valid is not None:
            attrs["room_temp_last_valid_c"] = control.last_valid
            if control.last_valid_ts > 0:
                age = int(time.time() - control.last_valid_ts)
                attrs["room_temp_last_valid_age_s"] = age

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
