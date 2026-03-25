"""Climate Entity for Tado X Proxy."""
from __future__ import annotations

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
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .climate_controllers import (
    FollowPhysicalController,
    PresenceAutomationController,
    WindowAutomationController,
)
from .climate_presets import PresetMixin
from .climate_regulation import RegulationMixin
from .const import (
    CONF_AWAY_TARGET,
    CONF_BOOST_DURATION,
    CONF_BOOST_TARGET,
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_FOLLOW_GRACE_S,
    CONF_FOLLOW_TADO_INPUT,
    CONF_FOLLOW_THRESHOLD_C,
    CONF_FROST_PROTECTION_TARGET,
    CONF_GAIN_SCHEDULING,
    CONF_OVERLAY_REFRESH_S,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_SENSOR_GRACE_S,
    CONF_URGENT_DECREASE_THRESHOLD_C,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
    DOMAIN,
    PRESET_FROST_PROTECTION,
    PRESET_LIST,
    safe_float,
)
from .parameters import (
    DEFAULT_CONTROL_INTERVAL_S,
    DEFAULT_SENSOR_GRACE_S,
    BehaviourConfig,
    CorrectionTuning,
    PresetConfig,
    RegulationConfig,
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
        self._target_temp: float = config_entry.options.get(CONF_COMFORT_TARGET, PresetConfig.comfort_target_c)
        self._preset_mode: str = PRESET_COMFORT

        # Boost timer
        self._boost_cancel: CALLBACK_TYPE | None = None
        self._boost_saved_preset: str = PRESET_COMFORT
        self._boost_saved_temp: float | None = None
        self._boost_end_ts: float = 0.0

        # Timing
        self._last_regulation_ts = 0.0
        self._last_command_sent_ts = 0.0
        self._last_sent_setpoint: float | None = None

        # Sensor resilience: last-valid values for grace-period bridging
        self._last_valid_room_temp: float | None = None
        self._last_valid_room_temp_ts: float = 0.0
        self._sensor_grace_s: int = config_entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        self._sensor_degraded: bool = False

        # Overlay refresh: optional periodic resend for cloud-API integrations
        # with timer-based overlays (e.g. exabird ha-tado-x uses 30 min TIMER).
        # Value of 0 means disabled (default, correct for Matter/Thread).
        self._overlay_refresh_s: int = config_entry.options.get(
            CONF_OVERLAY_REFRESH_S, 0
        )

        # State-machine controllers (hold their own timer + saved-state)
        self._window_ctrl = WindowAutomationController()
        self._presence_ctrl = PresenceAutomationController()

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
            kp = opts.get("correction_kp", config.tuning.kp)
            ki = opts.get("correction_ki", config.tuning.ki)
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
            temp = safe_float(last_state.attributes.get(ATTR_TEMPERATURE))
            if temp is not None:
                self._target_temp = temp
            # Restore preset (default to comfort if missing or invalid)
            restored_preset = last_state.attributes.get("preset_mode")
            if restored_preset in PRESET_LIST or restored_preset == PRESET_NONE:
                self._preset_mode = restored_preset
                # Don't restore boost – it's time-limited and the timer is gone
                if self._preset_mode == PRESET_BOOST:
                    self._preset_mode = PRESET_COMFORT
                # Don't restore frost protection – it's window-driven and the
                # controller state is not persisted across restarts
                elif self._preset_mode == PRESET_FROST_PROTECTION:
                    self._preset_mode = PRESET_COMFORT

        # If the active preset is COMFORT, the comfort_target in options is
        # authoritative (may have changed via the number entity while HA was down).
        # For PRESET_NONE (manual), the restored slider temperature wins.
        if self._preset_mode == PRESET_COMFORT:
            opts_comfort = safe_float(self._config_entry.options.get(CONF_COMFORT_TARGET))
            if opts_comfort is not None:
                self._target_temp = opts_comfort

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

        # Window sensor listener
        window_sensor = self._config_entry.options.get(CONF_WINDOW_SENSOR_ID)
        if window_sensor:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [window_sensor],
                    self._async_window_changed,
                )
            )
            # Evaluate current state after restart
            window_state = self.hass.states.get(window_sensor)
            if window_state and window_state.state == "on":
                delay = self._config_entry.options.get(CONF_WINDOW_DELAY_S, 30)
                self._window_ctrl.handle_window_opened(
                    self.hass, delay, self._async_window_action
                )
                _LOGGER.info("Startup: window sensor is open, action in %ds", delay)

        # Presence sensor listener
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
            if presence_state and presence_state.state == "off":
                if self._preset_mode == PRESET_AWAY:
                    # Preset AWAY was restored from state but the controller's
                    # is_active flag is not persisted.  Pre-activate with
                    # COMFORT as the saved state so that coming home restores
                    # a useful preset instead of AWAY → AWAY (no-op).
                    comfort = safe_float(
                        self._config_entry.options.get(CONF_COMFORT_TARGET)
                    )
                    self._presence_ctrl.activate(
                        PRESET_COMFORT,
                        comfort if comfort is not None else self._config.presets.comfort_target_c,
                    )
                    _LOGGER.info(
                        "Startup: preset AWAY restored, controller pre-activated "
                        "with COMFORT as saved state"
                    )
                else:
                    delay = self._config_entry.options.get(CONF_PRESENCE_AWAY_DELAY_S, 600)
                    self._presence_ctrl.handle_presence_away(
                        self.hass, delay, self._async_presence_away_action
                    )
                    _LOGGER.info("Startup: presence sensor is away, action in %ds", delay)
            elif presence_state and presence_state.state not in ("unavailable", "unknown"):
                # Presence shows home but preset was restored as AWAY.
                # This happens when the user returned while HA was down.
                if self._preset_mode == PRESET_AWAY:
                    self._preset_mode = PRESET_COMFORT
                    opts_comfort = safe_float(
                        self._config_entry.options.get(CONF_COMFORT_TARGET)
                    )
                    if opts_comfort is not None:
                        self._target_temp = opts_comfort
                    _LOGGER.info(
                        "Startup: presence is home but preset was AWAY, "
                        "switching to COMFORT"
                    )

        # Start periodic regulation
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_regulation_cycle_timer,
                datetime.timedelta(seconds=DEFAULT_CONTROL_INTERVAL_S),
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel all timers when the entity is being removed."""
        self._window_ctrl.cancel_all()
        self._presence_ctrl.cancel_timer()
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None
            self._boost_end_ts = 0.0
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
        self._regulator = FeedforwardPiRegulator(self._config)
        self._sensor_grace_s = entry.options.get(
            CONF_SENSOR_GRACE_S, DEFAULT_SENSOR_GRACE_S
        )
        self._overlay_refresh_s = entry.options.get(CONF_OVERLAY_REFRESH_S, 0)
        # Only sync comfort target when COMFORT preset is active; PRESET_NONE
        # (manual) keeps its independently set temperature.
        if self._preset_mode == PRESET_COMFORT:
            comfort = safe_float(entry.options.get(CONF_COMFORT_TARGET))
            if comfort is not None:
                self._target_temp = comfort
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Follow physical thermostat
    # ------------------------------------------------------------------

    @callback
    def _async_tado_state_changed(self, event) -> None:
        """Detect physical thermostat changes and follow them if enabled."""
        if not self._config_entry.options.get(CONF_FOLLOW_TADO_INPUT, False):
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
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
        self._preset_mode = PRESET_NONE
        if self._boost_cancel is not None:
            self._boost_cancel()
            self._boost_cancel = None
            self._boost_end_ts = 0.0
        self.async_write_ha_state()
        # Trigger immediate regulation so the new target takes effect fast.
        self.hass.async_create_task(
            self._async_regulation_cycle(trigger="follow_tado")
        )

    # ------------------------------------------------------------------
    # Standard climate controls
    # ------------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode not in self._attr_hvac_modes:
            return
        # Manual HVAC change clears any active window-open state so the user's
        # intention is respected.
        if self._window_ctrl.is_active:
            self._window_ctrl.cancel_all()

        previous_mode = self._hvac_mode
        self._hvac_mode = hvac_mode
        self.async_write_ha_state()

        # Forward HVAC mode to the source TRV entity
        if hvac_mode == HVACMode.OFF:
            try:
                await self._async_send_hvac_mode_to_tado(HVACMode.OFF)
            except (TimeoutError, HomeAssistantError):
                # Command failed – revert local state so the proxy stays in sync
                # with the TRV (which may still be heating).
                self._hvac_mode = previous_mode
                self._last_reason = "hvac_off_failed"
                self.async_write_ha_state()
                return
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
                    delay = self._config_entry.options.get(CONF_WINDOW_DELAY_S, 30)
                    self._window_ctrl.handle_window_opened(
                        self.hass, delay, self._async_window_action
                    )
                    _LOGGER.info(
                        "HVAC OFF→HEAT: window still open, frost action in %ds",
                        delay,
                    )
            # Reactivate the TRV, then run regulation to send the correct setpoint.
            await self._async_send_hvac_mode_to_tado(HVACMode.HEAT)
            await self._async_regulation_cycle(trigger="hvac_mode_change")
        else:
            await self._async_regulation_cycle(trigger="hvac_mode_change")

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

        # If window or presence automation is active, save the temperature
        # for later restoration instead of overriding the active automation.
        if self._window_ctrl.is_active:
            self._window_ctrl.update_saved(PRESET_NONE, temp_f)
            _LOGGER.info(
                "Window open: temperature %.1f°C saved for restore", temp_f
            )
            self.async_write_ha_state()
            return
        if self._presence_ctrl.is_active:
            self._presence_ctrl.update_saved(PRESET_NONE, temp_f)
            _LOGGER.info(
                "Presence away: temperature %.1f°C saved for restore", temp_f
            )
            self.async_write_ha_state()
            return

        self._target_temp = temp_f

        # Cancel window close delay if user manually changes temperature
        if self._window_ctrl.close_delay_active:
            self._window_ctrl.cancel_all()

        # Any direct temperature change activates manual mode and cancels
        # any running boost timer.
        if self._preset_mode != PRESET_NONE:
            if self._boost_cancel is not None:
                self._boost_cancel()
                self._boost_cancel = None
                self._boost_end_ts = 0.0
            self._preset_mode = PRESET_NONE

        self.async_write_ha_state()
        await self._async_regulation_cycle(trigger="set_temperature")

    # ------------------------------------------------------------------
    # Properties for HA UI
    # ------------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Return the room temperature from the external sensor."""
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
            "sensor_degraded": self._sensor_degraded,
            "overlay_refresh_s": self._overlay_refresh_s,
        }

        # Sensor resilience diagnostics
        if self._last_valid_room_temp is not None:
            attrs["room_temp_last_valid_c"] = self._last_valid_room_temp
            if self._last_valid_room_temp_ts > 0:
                age = int(time.time() - self._last_valid_room_temp_ts)
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
